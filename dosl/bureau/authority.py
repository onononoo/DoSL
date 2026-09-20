"""
dosl.bureau.authority -- the single front door.

Everything below this module is a component; this is the thing that uses
them in the right order:

    form -> tribunal -> adjudication -> permanent record -> .sdwx dossier

Both user interfaces (CLI and GUI) go through :class:`Authority` and
nothing else, so the two cannot drift apart.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ..formats import sdwx
from ..kernel.importer import compile_file
from ..native import bridge
from . import certificate
from .departments import Adjudication, Department, Tribunal
from .forms import Form27B6
from .ledger import Ledger, LedgerEntry, default_path


class Authority:
    """The Department, as a single object with a short list of verbs."""

    def __init__(self, ledger_path: str | Path | None = None, *,
                 prefer_native: bool = True, trace: bool = False) -> None:
        self.report = bridge.load_engine(prefer_native=prefer_native)
        self.engine = self.report.engine
        self.tribunal = Tribunal(self.engine, trace=trace)
        self.ledger = Ledger(Path(ledger_path) if ledger_path else default_path())

    # ------------------------------------------------------------ verbs

    def adjudicate(self, form: Form27B6, *, record: bool = True,
                   when: _dt.datetime | None = None
                   ) -> tuple[Adjudication, LedgerEntry | None]:
        """Run the tribunal and, unless told otherwise, seal the result."""
        adjudication = self.tribunal.convene(form, when=when)
        entry = self.ledger.append(adjudication) if record else None
        return adjudication, entry

    def dossier(self, adjudication: Adjudication,
                entry: LedgerEntry | None = None) -> sdwx.Dossier:
        """Package everything about one adjudication into a ``.sdwx`` file."""
        flags = sdwx.Flags.SEALED
        if not adjudication.verdict.permitted:
            flags |= sdwx.Flags.UNDER_APPEAL
        if "mayonnaise" in adjudication.form.get("condiments", []):
            flags |= sdwx.Flags.CONTAINS_MAYONNAISE

        dossier = sdwx.Dossier(flags=flags)
        dossier.add_json(sdwx.Section.MANIFEST, "manifest", {
            "format": "Sandwich Dossier Exchange",
            "reference": adjudication.reference,
            "issued": adjudication.issued,
            "verdict": adjudication.verdict.name,
            "score": adjudication.score,
            "seal": adjudication.seal,
            "engine": adjudication.engine,
            "departments": [d.slug for d in Department.all()],
        })
        dossier.add_json(sdwx.Section.FORM, "form-27b6", adjudication.form)
        dossier.add_json(sdwx.Section.OUTCOME, "adjudication", adjudication.as_dict())
        dossier.add_text(sdwx.Section.ATTACHMENT, "certificate.txt",
                         certificate.full_report(adjudication))

        for department in Department.all():
            try:
                code = department.policy()
            except Exception:  # a department that could not sit ships no policy
                continue
            dossier.add(sdwx.Section.POLICY_CODE, department.slug,
                        sdwx.marshal_policy(code))

        if entry is not None:
            dossier.add_json(sdwx.Section.LEDGER, "ledger-entry", {
                "index": entry.index,
                "entry_hash": entry.entry_hash,
                "previous_hash": entry.previous_hash,
            })
        dossier.add(sdwx.Section.SEAL, "seal",
                    adjudication.seal.replace("-", "").encode("ascii"))
        return dossier

    def save_dossier(self, adjudication: Adjudication, path: str | Path,
                     entry: LedgerEntry | None = None) -> int:
        return self.dossier(adjudication, entry).write(Path(path))

    # ------------------------------------------------------- introspection

    def policy_sources(self) -> dict[str, Path]:
        """Where each department's regulations actually live on disk."""
        import dosl.policies as policies

        found: dict[str, Path] = {}
        for path in policies.discover():
            found[path.stem] = path
        return found

    def recompile(self, slug: str) -> Any:
        """Force a department's policy to be re-read from source."""
        source = self.policy_sources().get(slug)
        if source is None:
            raise KeyError(f"no policy file for {slug!r}")
        code, _ = compile_file(source, use_cache=False)
        Department._cache[slug] = code
        return code

    def diagnostics(self) -> list[str]:
        """The answer to 'is any of this actually working'."""
        lines = [
            f"engine          {self.engine.backend}: {self.engine.detail}",
            f"dll             {self.report.dll_path or '(none built)'}",
            f"bridge error    {self.report.error or 'none'}",
            f"ledger          {self.ledger.path}",
        ]
        if self.report.pe_info:
            info = self.report.pe_info
            lines.append(
                f"pe image        {info['size_on_disk']} bytes on disk, "
                f"{info['size_of_image']:#x} mapped, "
                f"{len(info['exports'])} exports")
            lines.append(
                "sections        "
                + ", ".join(f"{s['name']}@{s['rva']:#x}" for s in info["sections"]))
        ok, complaints = self.ledger.verify()
        lines.append(f"record          {len(self.ledger)} entries, "
                     f"{'intact' if ok else str(len(complaints)) + ' PROBLEM(S)'}")
        for department in Department.all():
            try:
                code = department.policy()
                lines.append(
                    f"  {department.slug:<14} v{code.version} "
                    f"{code.size:>5}B  {code.source_digest[:12]}  {department.title}")
            except Exception as exc:
                lines.append(f"  {department.slug:<14} FAILED: {exc}")
        return lines
