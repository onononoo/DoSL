"""
dosl.bureau.ledger -- the Permanent Record.

An append-only JSON-lines file in which every entry carries the hash of the
one before it. Editing an old line, deleting one, or reordering them breaks
the chain at exactly that point, and :meth:`Ledger.verify` says where.

This is not a blockchain. Nobody is mining anything. It is a hash chain,
which is the part of a blockchain that was always the useful bit.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

GENESIS = hashlib.sha256(
    b"DEPARTMENT OF SANDWICH LEGITIMACY :: PERMANENT RECORD :: BOOK I"
).hexdigest()

LEDGER_VERSION = 1


class LedgerError(RuntimeError):
    """The Permanent Record is not in a state the Department can accept."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable line. ``entry_hash`` covers every field above it."""

    index: int
    issued: str
    reference: str
    applicant: str
    verdict: str
    score: float
    seal: str
    departments: str  # compact "slug:score" summary, for eyeballing
    previous_hash: str
    entry_hash: str = ""

    def payload(self) -> str:
        """The canonical bytes the hash is taken over."""
        body = {k: v for k, v in asdict(self).items() if k != "entry_hash"}
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        return hashlib.sha256(self.payload().encode("utf-8")).hexdigest()

    def sealed(self) -> "LedgerEntry":
        return LedgerEntry(**{**asdict(self), "entry_hash": self.compute_hash()})

    @property
    def short(self) -> str:
        return self.entry_hash[:12]


class Ledger:
    """A hash-chained, append-only record on disk."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------ reading

    def __len__(self) -> int:
        return sum(1 for _ in self.entries())

    def entries(self) -> Iterator[LedgerEntry]:
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerError(
                        f"{self.path}:{lineno} is not valid JSON: {exc}") from exc
                if record.pop("_v", LEDGER_VERSION) != LEDGER_VERSION:
                    raise LedgerError(f"{self.path}:{lineno} uses an unknown format")
                try:
                    yield LedgerEntry(**record)
                except TypeError as exc:
                    raise LedgerError(
                        f"{self.path}:{lineno} has the wrong fields: {exc}") from exc

    def tail(self, count: int = 10) -> list[LedgerEntry]:
        collected = list(self.entries())
        return collected[-count:]

    def last(self) -> LedgerEntry | None:
        latest = None
        for entry in self.entries():
            latest = entry
        return latest

    # ------------------------------------------------------------ writing

    def append(self, adjudication) -> LedgerEntry:
        """Seal one adjudication into the record and return the entry."""
        previous = self.last()
        entry = LedgerEntry(
            index=(previous.index + 1) if previous else 0,
            issued=adjudication.issued,
            reference=adjudication.reference,
            applicant=adjudication.applicant,
            verdict=adjudication.verdict.name,
            score=round(float(adjudication.score), 2),
            seal=adjudication.seal,
            departments=" ".join(
                f"{r.slug}:{r.score:.0f}" for r in adjudication.results),
            previous_hash=previous.entry_hash if previous else GENESIS,
        ).sealed()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"_v": LEDGER_VERSION, **asdict(entry)}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True,
                                    separators=(",", ":")) + "\n")
        return entry

    # --------------------------------------------------------- verification

    def verify(self) -> tuple[bool, list[str]]:
        """Walk the chain. Returns ``(ok, complaints)``."""
        complaints: list[str] = []
        expected_previous = GENESIS
        expected_index = 0

        for entry in self.entries():
            where = f"entry {entry.index} ({entry.reference})"
            if entry.index != expected_index:
                complaints.append(
                    f"{where}: index should be {expected_index}; "
                    f"a line has been inserted or removed")
            if entry.previous_hash != expected_previous:
                complaints.append(
                    f"{where}: previous_hash {entry.previous_hash[:12]}... does not "
                    f"match the entry above ({expected_previous[:12]}...)")
            recomputed = entry.compute_hash()
            if entry.entry_hash != recomputed:
                complaints.append(
                    f"{where}: contents have been edited "
                    f"(hash {entry.entry_hash[:12]}... != {recomputed[:12]}...)")
            # Carry the RECOMPUTED hash forward, not the stored one. An edit
            # must invalidate every entry after it as well as its own line --
            # otherwise a forger who leaves entry_hash alone breaks one link
            # instead of the whole chain below it.
            expected_previous = recomputed
            expected_index = entry.index + 1

        return (not complaints), complaints

    # --------------------------------------------------------------- stats

    def statistics(self) -> dict:
        """Aggregate the record, for the front desk's morale."""
        entries = list(self.entries())
        if not entries:
            return {"count": 0, "verdicts": {}, "mean_score": 0.0,
                    "best": None, "worst": None, "applicants": 0}
        verdicts: dict[str, int] = {}
        for entry in entries:
            verdicts[entry.verdict] = verdicts.get(entry.verdict, 0) + 1
        ranked = sorted(entries, key=lambda e: e.score)
        return {
            "count": len(entries),
            "verdicts": dict(sorted(verdicts.items(), key=lambda kv: -kv[1])),
            "mean_score": round(sum(e.score for e in entries) / len(entries), 2),
            "best": ranked[-1],
            "worst": ranked[0],
            "applicants": len({e.applicant.casefold() for e in entries}),
        }


def default_path() -> Path:
    """Where the Permanent Record lives when nobody specifies."""
    root = os.environ.get("DOSL_HOME")
    if root:
        return Path(root) / "permanent-record.jsonl"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DoSL" / "permanent-record.jsonl"
