"""
dosl.bureau.departments -- the five departments, and the tribunal that convenes them.

Each department is a class. Defining the class registers it; there is no
list of departments maintained anywhere, which is the one respect in which
this system is less bureaucratic than it could be.

A department owns exactly one ``.bureau`` policy, a weight, and a note on
whether its objections are fatal. The :class:`Tribunal` runs them in order,
stops early when a department with veto power denies, and folds the rest
into a single :class:`Adjudication`.
"""

from __future__ import annotations

import abc
import datetime as _dt
import importlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Iterable

from ..kernel.compiler import PolicyCode
from ..kernel.vm import Finding, Machine, Outcome
from ..native import bridge
from .forms import Form27B6


class Verdict(Enum):
    """the five things the department is prepared to say about a sandwich."""

    APPROVED = ("approved", "proceed. enjoy is a strong word.")
    CONDITIONAL = ("approved with conditions", "proceed, but the file stays open.")
    PROVISIONAL = ("provisionally tolerated", "consume quickly and say nothing.")
    REFERRED = ("referred for review", "a committee will consider this. eventually.")
    DENIED = ("denied", "you may not have this sandwich. you may have soup.")

    def __init__(self, label: str, advice: str) -> None:
        self.label, self.advice = label, advice

    @property
    def permitted(self) -> bool:
        return self in (Verdict.APPROVED, Verdict.CONDITIONAL, Verdict.PROVISIONAL)


#: Weighted score thresholds, highest first. The first one met wins.
THRESHOLDS: tuple[tuple[float, Verdict], ...] = (
    (85.0, Verdict.APPROVED),
    (70.0, Verdict.CONDITIONAL),
    (50.0, Verdict.PROVISIONAL),
    (0.0, Verdict.REFERRED),
)


class DepartmentError(RuntimeError):
    """A department could not sit today."""


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

class DepartmentMeta(abc.ABCMeta):
    """Registers every concrete department as it is defined."""

    registry: ClassVar[dict[str, type["Department"]]] = {}

    def __new__(mcls, name, bases, namespace, **kwargs):
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        slug = namespace.get("slug")
        if slug:
            if slug in mcls.registry:
                raise DepartmentError(
                    f"department slug {slug!r} is already taken by "
                    f"{mcls.registry[slug].__name__}")
            mcls.registry[slug] = cls
        return cls


class Department(abc.ABC, metaclass=DepartmentMeta):
    """One policy, one opinion, one weight in the final average."""

    slug: ClassVar[str] = ""
    title: ClassVar[str] = ""
    motto: ClassVar[str] = ""
    weight: ClassVar[float] = 1.0
    #: A denial from this department forces the overall verdict to DENIED.
    veto: ClassVar[bool] = False
    #: A denial from this department also ends the sitting, so nobody below
    #: it is convened. Reserved for jurisdictional questions: if the thing is
    #: not a sandwich, the other departments have nothing to be asked about.
    halts: ClassVar[bool] = False
    order: ClassVar[int] = 50

    _cache: ClassVar[dict[str, PolicyCode]] = {}

    @classmethod
    def policy(cls) -> PolicyCode:
        """Import (and compile, and cache) this department's ``.bureau`` file."""
        if cls.slug not in cls._cache:
            try:
                module = importlib.import_module(f"dosl.policies.{cls.slug}")
            except ImportError as exc:
                raise DepartmentError(
                    f"{cls.title} has lost its regulations: {exc}") from exc
            code = getattr(module, "POLICY", None)
            if not isinstance(code, PolicyCode):
                raise DepartmentError(
                    f"dosl.policies.{cls.slug} is a python module, not a policy")
            cls._cache[cls.slug] = code
        return cls._cache[cls.slug]

    @classmethod
    def evaluate(cls, machine: Machine, environment: dict[str, Any]) -> Outcome:
        return machine.run(cls.policy(), environment)

    @classmethod
    def all(cls) -> list[type["Department"]]:
        """Every registered department, in convening order."""
        return sorted(DepartmentMeta.registry.values(), key=lambda d: (d.order, d.slug))


# --------------------------------------------------------------------------
# The five departments
# --------------------------------------------------------------------------

class DirectorateOfNomenclature(Department):
    slug = "nomenclature"
    title = "directorate of nomenclature"
    motto = "precedent is not appetite."
    weight = 1.4
    veto = True
    halts = True  # if it is not a sandwich, nothing else is relevant
    order = 10


class BureauOfStructuralIntegrity(Department):
    slug = "structural"
    title = "bureau of structural integrity"
    motto = "every sandwich is a bridge."
    weight = 1.2
    veto = True
    order = 20


class OfficeOfCondimentAffairs(Department):
    slug = "condiment"
    title = "office of condiment affairs"
    motto = "a suspension has a failure point."
    weight = 1.0
    veto = False
    order = 30


class TemporalComplianceDivision(Department):
    slug = "temporal"
    title = "temporal compliance division"
    motto = "when, where, and how apologetically."
    weight = 0.8
    veto = False
    order = 40


class CommitteeOnCulinaryEthics(Department):
    slug = "ethics"
    title = "committee on culinary ethics"
    motto = "binding, despite everything."
    weight = 1.1
    veto = False
    order = 50


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(slots=True)
class DepartmentResult:
    """What one department produced, or why it produced nothing."""

    slug: str
    title: str
    weight: float
    veto: bool
    outcome: Outcome | None = None
    error: str | None = None

    @property
    def score(self) -> float:
        return self.outcome.score if self.outcome else 0.0

    @property
    def denied(self) -> bool:
        return bool(self.outcome and self.outcome.denied)

    @property
    def findings(self) -> list[Finding]:
        return list(self.outcome.findings) if self.outcome else []

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug, "title": self.title, "weight": self.weight,
            "veto": self.veto, "error": self.error,
            "score": self.score, "denied": self.denied,
            "baseline": self.outcome.baseline if self.outcome else None,
            "steps": self.outcome.steps if self.outcome else 0,
            "findings": [
                {"severity": f.severity, "kind": f.kind.name,
                 "message": f.message, "line": f.line}
                for f in self.findings
            ],
            "adjustments": [
                {"delta": a.delta, "reason": a.reason, "line": a.line}
                for a in (self.outcome.adjustments if self.outcome else [])
            ],
            "notes": list(self.outcome.notes) if self.outcome else [],
            "citations": list(self.outcome.citations) if self.outcome else [],
        }


@dataclass(slots=True)
class Adjudication:
    """The complete record of one sandwich's day in front of the tribunal."""

    reference: str
    applicant: str
    verdict: Verdict
    score: float
    results: list[DepartmentResult] = field(default_factory=list)
    form: dict[str, Any] = field(default_factory=dict)
    issued: str = ""
    engine: str = ""
    seal: str = ""

    @property
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for result in self.results:
            out.extend(result.findings)
        return sorted(out, key=lambda f: (-f.severity, f.policy))

    @property
    def notes(self) -> list[str]:
        return [n for r in self.results if r.outcome for n in r.outcome.notes]

    @property
    def citations(self) -> list[str]:
        seen: list[str] = []
        for result in self.results:
            for citation in (result.outcome.citations if result.outcome else []):
                if citation not in seen:
                    seen.append(citation)
        return seen

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "applicant": self.applicant,
            "verdict": self.verdict.name,
            "verdict_label": self.verdict.label,
            "advice": self.verdict.advice,
            "score": self.score,
            "issued": self.issued,
            "engine": self.engine,
            "seal": self.seal,
            "form": self.form,
            "departments": [r.as_dict() for r in self.results],
        }


# --------------------------------------------------------------------------
# Tribunal
# --------------------------------------------------------------------------

class Tribunal:
    """Convenes the departments and reconciles whatever they come back with."""

    def __init__(self, engine=None, *, departments: Iterable[type[Department]] = None,
                 trace: bool = False) -> None:
        self.engine = engine or bridge.engine()
        self.departments = list(departments) if departments else Department.all()
        self.machine = Machine(self.engine, trace=trace)

    def convene(self, form: Form27B6, *, when: _dt.datetime | None = None,
                validate: bool = True) -> Adjudication:
        if validate:
            form.require_valid()
        now = when or _dt.datetime.now()
        environment = form.environment(when=now)

        results: list[DepartmentResult] = []
        vetoed = halted = False
        for department in self.departments:
            result = DepartmentResult(
                slug=department.slug, title=department.title,
                weight=department.weight, veto=department.veto)
            if halted:
                # the remaining departments are recorded as not having been
                # asked, which is different from having had no objection.
                result.error = "not convened: jurisdiction declined above"
                results.append(result)
                continue
            try:
                result.outcome = department.evaluate(self.machine, environment)
            except Exception as exc:  # a broken policy must not break the app
                result.error = f"{type(exc).__name__}: {exc}"
            else:
                if result.outcome.denied and department.veto:
                    vetoed = True
                    halted = halted or department.halts
            results.append(result)

        score = self._weighted_score(results)
        verdict = self._decide(results, score, vetoed)
        reference = self._reference(form, now)

        adjudication = Adjudication(
            reference=reference,
            applicant=form.applicant,
            verdict=verdict,
            score=round(score, 2),
            results=results,
            form=form.as_dict(),
            # a space separator rather than the default "T": still valid
            # iso 8601, still round-trips through datetime.fromisoformat, and
            # it does not shout on a certificate that is otherwise lowercase.
            issued=now.replace(microsecond=0).isoformat(sep=" "),
            engine=f"{self.engine.backend}: {self.engine.detail}",
        )
        adjudication.seal = self.seal(adjudication)
        return adjudication

    # ------------------------------------------------------------ scoring

    @staticmethod
    def _weighted_score(results: list[DepartmentResult]) -> float:
        usable = [r for r in results if r.outcome is not None]
        if not usable:
            return 0.0
        total_weight = sum(r.weight for r in usable)
        return sum(r.score * r.weight for r in usable) / total_weight

    @staticmethod
    def _decide(results: list[DepartmentResult], score: float, vetoed: bool) -> Verdict:
        if vetoed:
            return Verdict.DENIED
        if any(r.denied for r in results):
            return Verdict.REFERRED
        worst = max((f.severity for r in results for f in r.findings), default=0)
        for threshold, verdict in THRESHOLDS:
            if score >= threshold:
                # A severe objection caps the outcome even when the numbers
                # are comfortable. Departments are allowed to be upset.
                if worst >= 6 and verdict is Verdict.APPROVED:
                    return Verdict.CONDITIONAL
                return verdict
        return Verdict.REFERRED  # pragma: no cover -- THRESHOLDS ends at 0.0

    def _reference(self, form: Form27B6, now: _dt.datetime) -> str:
        digest = self.engine.digest(
            form.applicant, "|".join(sorted(form.fillings)),
            "|".join(sorted(form.condiments)), form.bread,
            now.strftime("%Y%m%d"))
        return (f"dosl-{now.strftime('%Y%m%d')}-"
                f"{(digest >> 40) & 0xFFFF:04x}-{digest & 0xFFFFFF:06x}")

    def seal(self, adjudication: Adjudication) -> str:
        """A tamper-evident signature over the parts that matter."""
        payload = "".join([
            adjudication.reference, adjudication.applicant,
            adjudication.verdict.name, f"{adjudication.score:.2f}",
            adjudication.issued,
            *(f"{r.slug}={r.score:.2f}" for r in adjudication.results),
        ])
        digest = self.engine.digest(payload)
        return "-".join(f"{(digest >> shift) & 0xFFFF:04x}"
                        for shift in (48, 32, 16, 0))
