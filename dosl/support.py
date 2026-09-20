"""
dosl.support -- the donation and source details, in one place.

The gui tab, the ``dosl support`` command and the readme all render the same
constants, so the address can never drift between them. If the address below
is ever changed, it changes everywhere at once.
"""

from __future__ import annotations

#: the bitcoin address donations go to. treat as load-bearing: it is money.
BTC_ADDRESS = "bc1qs4z04ltddh6vaqd4stu3p4vekv253ht4cwqma4"

BLURB = ("this and all of my other projects are open source, "
         "so please donate to keep everything up!")

SOURCE_URL = "https://github.com/onononoo/DoSL"
PROJECTS_URL = "https://github.com/onononoo"

HEADING = "support + source"

#: shown in the gui and by ``dosl support``. the readme drops the first url,
#: because the readme is already in that repository.
LINKS_LINE = f"source code and my other projects: {SOURCE_URL} :: {PROJECTS_URL}"


def as_text(width: int = 74, include_source: bool = True) -> str:
    """the whole block as plain text, for the console and for files."""
    import textwrap

    rule = "=" * width
    address = f"  btc: {BTC_ADDRESS}"
    links = (LINKS_LINE if include_source
             else f"my other projects: {PROJECTS_URL}")
    lines = [
        rule,
        HEADING.center(width).rstrip(),
        rule,
        "",
        *textwrap.wrap(BLURB, width=width - 4, initial_indent="  ",
                       subsequent_indent="  "),
        "",
        "  +" + "-" * (len(address) + 1) + "+",
        "  |" + address + " |",
        "  +" + "-" * (len(address) + 1) + "+",
        "",
        *textwrap.wrap(links, width=width - 4, initial_indent="  ",
                       subsequent_indent="    "),
        rule,
    ]
    return "\n".join(lines)
