"""
dosl.bureau.certificate -- renders an adjudication as something you can print.

strictly 7-bit ascii. the windows console's default code page is not utf-8,
and a certificate that raises unicodeencodeerror on the last line is worse
than no certificate at all. it also, conveniently, looks exactly like
something printed by a government in 1974.
"""

from __future__ import annotations

import textwrap

from .. import support
from .departments import Adjudication, Verdict

WIDTH = 74
RULE = "=" * WIDTH
THIN = "-" * WIDTH

_STAMP = {
    Verdict.APPROVED: "  * * *   a p p r o v e d   * * *  ",
    Verdict.CONDITIONAL: " a p p r o v e d  ( c o n d . ) ",
    Verdict.PROVISIONAL: "  p r o v i s i o n a l  ",
    Verdict.REFERRED: "  r e f e r r e d  ",
    Verdict.DENIED: "  d e n i e d  ",
}


def _wrap(text: str, indent: str = "  ", width: int = WIDTH) -> list[str]:
    """wrap to the certificate width, hanging bullets and tags by two spaces."""
    hang = indent + ("  " if text[:1] in "-[" else "")
    return textwrap.wrap(text, width=width - len(indent), initial_indent=indent,
                         subsequent_indent=hang) or [indent.rstrip()]


def _centre(text: str) -> str:
    return text.center(WIDTH).rstrip()


def _row(left: str, right: str) -> str:
    gap = WIDTH - len(left) - len(right)
    return (left + " " * max(1, gap) + right).rstrip()


def _fit(lines: list[str]) -> str:
    """final guarantee that nothing exceeds the page.

    most lines are wrapped correctly on the way in, but a 40-character
    applicant name or a very long filling list can still overflow a row that
    was built with an f-string. rather than audit every format string, the
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


def _show(value: object) -> str:
    """render one submitted answer for schedule c.

    booleans get spelled out as yes/no rather than left as python's ``True``
    and ``False``, which are the only two words on the whole certificate that
    would otherwise arrive capitalised.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "(none)"
    text = str(value)
    return text if text else "(blank)"


def _bar(score: float, width: int = 32) -> str:
    """a score bar sized so the whole row fits in WIDTH without wrapping.

    label (31) + bracket + width + bracket + space + "100.00" must stay
    under 74, which caps the bar at 33.
    """
    filled = int(round(score / 100 * width))
    return "[" + "#" * filled + "." * (width - filled) + f"] {score:6.2f}"


def render(adjudication: Adjudication) -> str:
    """the short form: the bit you would pin to a noticeboard."""
    lines = [
        RULE,
        _centre("department of sandwich legitimacy"),
        _centre("office of adjudication and standing"),
        _centre("nihil sine forma"),
        RULE,
        "",
        _row(f"  reference : {adjudication.reference}",
             f"issued : {adjudication.issued}  "),
        _row(f"  applicant : {adjudication.applicant}",
             f"seal : {adjudication.seal}  "),
        "",
        THIN,
        _centre(_STAMP[adjudication.verdict]),
        _centre(adjudication.verdict.label),
        THIN,
        "",
        f"  composite legitimacy score   {_bar(adjudication.score)}",
        "",
    ]

    lines.append("  departmental findings")
    for result in adjudication.results:
        if result.error:
            lines.append(f"    {result.title:<36} {'--':>6}  {result.error}")
            continue
        flag = "veto" if result.veto else "    "
        lines.append(
            f"    {result.title:<36} {result.score:6.2f}  "
            f"(w{result.weight:.1f}) {flag}")
    lines.append("")

    findings = adjudication.findings
    if findings:
        lines.append("  objections and observations")
        for finding in findings:
            tag = f"[{finding.kind.name.lower()[:4]} {finding.severity}]"
            lines.extend(_wrap(f"{tag} {finding.message}", indent="    "))
        lines.append("")

    if adjudication.notes:
        lines.append("  marginalia")
        for note in adjudication.notes:
            lines.extend(_wrap(f"- {note}", indent="    "))
        lines.append("")

    lines.extend([
        THIN,
        *_wrap(adjudication.verdict.advice, indent="  "),
        THIN,
        *_wrap("this certificate is valid for the duration of the sandwich. "
               "it confers no rights, transfers no title, and may be revoked "
               "retroactively.", indent="  "),
        RULE,
    ])
    return _fit(lines)


def full_report(adjudication: Adjudication) -> str:
    """the long form: every adjustment, every citation, every department."""
    lines = [render(adjudication), "", RULE,
             _centre("schedule a -- detailed departmental record"), RULE]

    for result in adjudication.results:
        lines.append("")
        lines.append(f"{result.title}   [{result.slug}]")
        lines.append(THIN)
        if result.error:
            lines.extend(_wrap(f"did not sit: {result.error}"))
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
                f"{finding.kind.name.lower():<12} sev {finding.severity}  "
                f"{finding.message}", indent="    "))
        for note in outcome.notes:
            lines.extend(_wrap(f"note: {note}", indent="    "))

    if adjudication.citations:
        lines.extend(["", RULE, _centre("schedule b -- regulations cited"),
                      RULE, ""])
        for citation in adjudication.citations:
            lines.extend(_wrap(f"- {citation}"))

    lines.extend(["", RULE, _centre("schedule c -- submitted particulars"),
                  RULE, ""])
    for key, value in adjudication.form.items():
        lines.extend(_wrap(f"{key:<20} {_show(value)}", indent="  "))

    lines.extend(["", THIN, *_wrap(f"engine: {adjudication.engine}"), THIN])
    return _fit(lines) + "\n\n" + support.as_text(WIDTH)


def one_line(adjudication: Adjudication) -> str:
    """for the ledger view and anywhere else that is short of room."""
    return (f"{adjudication.reference}  {adjudication.verdict.label:<24} "
            f"{adjudication.score:6.2f}  {adjudication.applicant}")
