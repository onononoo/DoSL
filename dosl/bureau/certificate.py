"""
dosl.bureau.certificate -- renders an adjudication as something you can print.

Strictly 7-bit ASCII. The Windows console's default code page is not UTF-8,
and a certificate that raises UnicodeEncodeError on the last line is worse
than no certificate at all. It also, conveniently, looks exactly like
something printed by a government in 1974.
"""

from __future__ import annotations

import textwrap

from .departments import Adjudication, Verdict

WIDTH = 74
RULE = "=" * WIDTH
THIN = "-" * WIDTH

_STAMP = {
    Verdict.APPROVED: "  * * *   A P P R O V E D   * * *  ",
    Verdict.CONDITIONAL: " A P P R O V E D  ( C O N D . ) ",
    Verdict.PROVISIONAL: "  P R O V I S I O N A L  ",
    Verdict.REFERRED: "  R E F E R R E D  ",
    Verdict.DENIED: "  D E N I E D  ",
}


def _wrap(text: str, indent: str = "  ", width: int = WIDTH) -> list[str]:
    """Wrap to the certificate width, hanging bullets and tags by two spaces."""
    hang = indent + ("  " if text[:1] in "-[" else "")
    return textwrap.wrap(text, width=width - len(indent), initial_indent=indent,
                         subsequent_indent=hang) or [indent.rstrip()]


def _centre(text: str) -> str:
    return text.center(WIDTH).rstrip()


def _row(left: str, right: str) -> str:
    gap = WIDTH - len(left) - len(right)
    return (left + " " * max(1, gap) + right).rstrip()


def _fit(lines: list[str]) -> str:
    """Final guarantee that nothing exceeds the page.

    Most lines are wrapped correctly on the way in, but a 40-character
    applicant name or a very long filling list can still overflow a row that
    was built with an f-string. Rather than audit every format string, the
    renderers pass their output through here on the way out.
    """
    out: list[str] = []
    for line in lines:
        line = line.rstrip()
        while len(line) > WIDTH:
            cut = line.rfind(" ", 20, WIDTH)
            if cut <= 0:
                cut = WIDTH
            out.append(line[:cut].rstrip())
            line = "      " + line[cut:].lstrip()
        out.append(line)
    return "\n".join(out)


def _bar(score: float, width: int = 32) -> str:
    """A score bar sized so the whole row fits in WIDTH without wrapping.

    label (31) + bracket + width + bracket + space + "100.00" must stay
    under 74, which caps the bar at 33.
    """
    filled = int(round(score / 100 * width))
    return "[" + "#" * filled + "." * (width - filled) + f"] {score:6.2f}"


def render(adjudication: Adjudication) -> str:
    """The short form: the bit you would pin to a noticeboard."""
    lines = [
        RULE,
        _centre("DEPARTMENT OF SANDWICH LEGITIMACY"),
        _centre("Office of Adjudication and Standing"),
        _centre("NIHIL SINE FORMA"),
        RULE,
        "",
        _row(f"  Reference : {adjudication.reference}",
             f"Issued : {adjudication.issued}  "),
        _row(f"  Applicant : {adjudication.applicant}",
             f"Seal : {adjudication.seal}  "),
        "",
        THIN,
        _centre(_STAMP[adjudication.verdict]),
        _centre(adjudication.verdict.label),
        THIN,
        "",
        f"  Composite legitimacy score   {_bar(adjudication.score)}",
        "",
    ]

    lines.append("  DEPARTMENTAL FINDINGS")
    for result in adjudication.results:
        if result.error:
            lines.append(f"    {result.title:<36} {'--':>6}  {result.error}")
            continue
        flag = "VETO" if result.veto else "    "
        lines.append(
            f"    {result.title:<36} {result.score:6.2f}  "
            f"(w{result.weight:.1f}) {flag}")
    lines.append("")

    findings = adjudication.findings
    if findings:
        lines.append("  OBJECTIONS AND OBSERVATIONS")
        for finding in findings:
            tag = f"[{finding.kind.name[:4]} {finding.severity}]"
            lines.extend(_wrap(f"{tag} {finding.message}", indent="    "))
        lines.append("")

    if adjudication.notes:
        lines.append("  MARGINALIA")
        for note in adjudication.notes:
            lines.extend(_wrap(f"- {note}", indent="    "))
        lines.append("")

    lines.extend([
        THIN,
        *_wrap(adjudication.verdict.advice, indent="  "),
        THIN,
        *_wrap("This certificate is valid for the duration of the sandwich. "
               "It confers no rights, transfers no title, and may be revoked "
               "retroactively.", indent="  "),
        RULE,
    ])
    return _fit(lines)


def full_report(adjudication: Adjudication) -> str:
    """The long form: every adjustment, every citation, every department."""
    lines = [render(adjudication), "", RULE,
             _centre("SCHEDULE A -- DETAILED DEPARTMENTAL RECORD"), RULE]

    for result in adjudication.results:
        lines.append("")
        lines.append(f"{result.title}   [{result.slug}]")
        lines.append(THIN)
        if result.error:
            lines.extend(_wrap(f"Did not sit: {result.error}"))
            continue
        outcome = result.outcome
        lines.append(_row(f"  baseline {outcome.baseline:.2f}",
                          f"final {outcome.score:.2f}  "))
        lines.append(f"  {outcome.steps} instructions executed, "
                     f"policy v{outcome.version}")
        for adjustment in outcome.adjustments:
            lines.extend(_wrap(f"{adjustment.delta:+7.1f}  {adjustment.reason}",
                               indent="    "))
        for finding in outcome.findings:
            lines.extend(_wrap(
                f"{finding.kind.name:<12} sev {finding.severity}  {finding.message}",
                indent="    "))
        for note in outcome.notes:
            lines.extend(_wrap(f"note: {note}", indent="    "))

    if adjudication.citations:
        lines.extend(["", RULE, _centre("SCHEDULE B -- REGULATIONS CITED"), RULE, ""])
        for citation in adjudication.citations:
            lines.extend(_wrap(f"- {citation}"))

    lines.extend(["", RULE, _centre("SCHEDULE C -- SUBMITTED PARTICULARS"), RULE, ""])
    for key, value in adjudication.form.items():
        shown = ", ".join(value) if isinstance(value, list) else str(value)
        lines.extend(_wrap(f"{key:<20} {shown or '(blank)'}", indent="  "))

    lines.extend(["", THIN, *_wrap(f"engine: {adjudication.engine}"), THIN])
    return _fit(lines)


def one_line(adjudication: Adjudication) -> str:
    """For the ledger view and anywhere else that is short of room."""
    return (f"{adjudication.reference}  {adjudication.verdict.label:<24} "
            f"{adjudication.score:6.2f}  {adjudication.applicant}")
