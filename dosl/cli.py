"""
dosl.cli -- the Department's command line.

The ``submit`` subcommand's flags are generated from :class:`Form27B6`, so
the form, the GUI and the CLI cannot disagree about what the questions are.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .bureau import Authority, Form27B6
from .bureau import certificate as cert  # the module, not the function it exports
from .bureau.departments import Department
from .bureau.forms import ValidationError, field_help
from .bureau.ledger import Ledger, default_path
from .formats import sdwx
from .kernel import intrinsics
from .kernel.importer import compile_file

BANNER = r"""
+------------------------------------------------------------------------+
|  D E P A R T M E N T   O F   S A N D W I C H   L E G I T I M A C Y     |
|  Office of Adjudication and Standing            "NIHIL SINE FORMA"     |
+------------------------------------------------------------------------+
"""


def _stdout_utf8() -> None:
    """Best-effort UTF-8 on a console that may be running code page 437."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def add_form_arguments(parser: argparse.ArgumentParser) -> None:
    """Derive one ``--flag`` per form field, with the field's own help text."""
    group = parser.add_argument_group("Form 27-B/6")
    for name, field in Form27B6.fields.items():
        flag = "--" + name.replace("_", "-")
        options = field.choices()
        help_text = field.help or field.label
        if options:
            help_text += f"  (e.g. {', '.join(options[:4])})"
        if field.kind == "bool":
            group.add_argument(flag, dest=name, action="store_true", default=None,
                               help=help_text)
            group.add_argument("--no-" + name.replace("_", "-"), dest=name,
                               action="store_false", default=None,
                               help=argparse.SUPPRESS)
        else:
            group.add_argument(flag, dest=name, default=None, metavar=field.kind.upper(),
                               help=help_text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dosl",
        description="Department of Sandwich Legitimacy -- adjudication service.",
        epilog="Run with no arguments to open the counter (graphical interface).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"DoSL {__version__}")
    parser.add_argument("--ledger", metavar="PATH", default=None,
                        help=f"the Permanent Record (default: {default_path()})")
    parser.add_argument("--no-native", action="store_true",
                        help="ignore bureau_entropy.dll and use the Python kernels")

    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    submit = subs.add_parser("submit", help="submit Form 27-B/6 for adjudication")
    submit.add_argument("--full", action="store_true", help="print the long report")
    submit.add_argument("--json", action="store_true", help="print JSON instead")
    submit.add_argument("--dossier", metavar="PATH", help="also write a .sdwx dossier")
    submit.add_argument("--no-record", action="store_true",
                        help="do not write to the Permanent Record")
    submit.add_argument("--trace", action="store_true",
                        help="record a VM instruction trace (slow, noisy)")
    submit.add_argument("--explain", action="store_true",
                        help="describe every field and exit")
    add_form_arguments(submit)

    subs.add_parser("gui", help="open the counter (graphical interface)")

    audit = subs.add_parser("audit", help="disassemble a department's policy")
    audit.add_argument("policy", nargs="?", help="slug, or a path to a .bureau file")
    audit.add_argument("--source", action="store_true", help="print the source too")

    record = subs.add_parser("record", help="read the Permanent Record")
    record.add_argument("--tail", type=int, default=12, metavar="N")
    record.add_argument("--verify", action="store_true", help="check the hash chain")
    record.add_argument("--stats", action="store_true")

    inspect = subs.add_parser("inspect", help="describe a .sdwx dossier")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--extract", metavar="NAME",
                         help="print one section's contents")

    forge = subs.add_parser("forge", help="build bureau_entropy.dll and describe it")
    forge.add_argument("--out", type=Path, help="write the DLL here instead")
    forge.add_argument("--disassemble", action="store_true",
                       help="hex dump each exported kernel")

    subs.add_parser("policies", help="list policies, fields and intrinsics")
    subs.add_parser("doctor", help="report on every moving part")
    return parser


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_submit(args, authority: Authority) -> int:
    if args.explain:
        print(f"Form 27-B/6 -- {Form27B6.title}")
        print("\n".join(field_help()))
        return 0

    supplied = {name: getattr(args, name) for name in Form27B6.fields
                if getattr(args, name, None) is not None}
    try:
        form = Form27B6(**supplied)
        form.require_valid()
    except ValidationError as exc:
        print(f"RETURNED FOR CORRECTION\n{exc}", file=sys.stderr)
        return 2

    adjudication, entry = authority.adjudicate(form, record=not args.no_record)

    if args.json:
        payload = adjudication.as_dict()
        if entry:
            payload["ledger"] = {"index": entry.index, "hash": entry.entry_hash}
        print(json.dumps(payload, indent=2))
    else:
        print(cert.full_report(adjudication) if args.full
              else cert.render(adjudication))
        if entry:
            print(f"\n  Permanent Record entry {entry.index}, hash {entry.short}...")

    if args.dossier:
        size = authority.save_dossier(adjudication, args.dossier, entry)
        print(f"  Dossier written: {args.dossier} ({size} bytes)")

    return 0 if adjudication.verdict.permitted else 1


def cmd_audit(args, authority: Authority) -> int:
    target = args.policy
    if not target:
        print("Departments with policies:")
        for department in Department.all():
            print(f"  {department.slug:<14} {department.title}")
        return 0

    path = Path(target)
    if path.suffix == ".bureau" and path.is_file():
        code, source = compile_file(path, use_cache=False)
    else:
        sources = authority.policy_sources()
        if target not in sources:
            print(f"no policy named {target!r}; try one of: "
                  f"{', '.join(sorted(sources))}", file=sys.stderr)
            return 2
        code, source = compile_file(sources[target], use_cache=False)

    if args.source:
        for number, line in enumerate(source.splitlines(), start=1):
            print(f"{number:4d} | {line}")
        print()
    print("\n".join(code.listing()))
    return 0


def cmd_record(args, authority: Authority) -> int:
    ledger: Ledger = authority.ledger
    print(f"Permanent Record: {ledger.path}")

    if args.verify:
        ok, complaints = ledger.verify()
        print(f"chain: {'INTACT' if ok else 'BROKEN'} over {len(ledger)} entries")
        for complaint in complaints:
            print(f"  ! {complaint}")
        return 0 if ok else 1

    if args.stats:
        stats = ledger.statistics()
        if not stats["count"]:
            print("  (empty)")
            return 0
        print(f"  entries        {stats['count']}")
        print(f"  applicants     {stats['applicants']}")
        print(f"  mean score     {stats['mean_score']}")
        for verdict, count in stats["verdicts"].items():
            print(f"  {verdict:<14} {count}")
        print(f"  highest        {stats['best'].score:6.2f}  "
              f"{stats['best'].applicant}")
        print(f"  lowest         {stats['worst'].score:6.2f}  "
              f"{stats['worst'].applicant}")
        return 0

    entries = ledger.tail(args.tail)
    if not entries:
        print("  (empty -- no sandwich has yet been adjudicated)")
        return 0
    print(f"  {'#':>4}  {'reference':<26} {'verdict':<12} {'score':>6}  applicant")
    for entry in entries:
        print(f"  {entry.index:>4}  {entry.reference:<26} {entry.verdict:<12} "
              f"{entry.score:>6.2f}  {entry.applicant}")
    return 0


def cmd_inspect(args, authority: Authority) -> int:
    try:
        dossier = sdwx.Dossier.read(args.path)
    except (sdwx.SdwxError, OSError) as exc:
        print(f"cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    if args.extract:
        try:
            payload = dossier.one(name=args.extract)
        except sdwx.SdwxError as exc:
            print(exc, file=sys.stderr)
            return 2
        sys.stdout.write(payload.decode("utf-8", "replace"))
        return 0

    print("\n".join(dossier.describe()))
    return 0


def cmd_forge(args, authority: Authority) -> int:
    from .forge import kernels, pe
    from .native import bridge

    image = kernels.build_dll()
    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(image)
    else:
        # Not force=True: the Authority built at startup has already loaded
        # the canonical DLL, and Windows will not let us overwrite a mapped
        # image. materialise_dll notices the bytes already match and hands
        # back the canonical path instead of writing a hashed sibling.
        destination = bridge.materialise_dll()

    info = pe.inspect(image)
    print(f"{destination}  ({len(image)} bytes)")
    print(f"  machine        {info['machine']:#06x} (x86-64)")
    print(f"  image base     {info['image_base']:#018x}")
    print(f"  size of image  {info['size_of_image']:#x}")
    print(f"  entry (DllMain) rva {info['entry_rva']:#x}")
    print("  sections")
    for section in info["sections"]:
        print(f"    {section['name']:<8} rva {section['rva']:#07x}  "
              f"virtual {section['virtual_size']:>5}  raw {section['raw_size']:>5}")
    print("  exports")
    for symbol, rva in info["exports"]:
        spec = next((k for k in kernels.KERNELS if k[0] == symbol), None)
        print(f"    {symbol:<22} rva {rva:#07x}  {spec[2] if spec else ''}")
        if spec:
            print(f"    {'':<22} {spec[3]}")

    if args.disassemble:
        from .forge.x64 import Assembler
        print("\n  machine code")
        for symbol, emit, signature, _doc in kernels.KERNELS:
            asm = Assembler()
            asm.buf.extend(emit())
            print(f"\n  {signature}")
            for line in asm.hexdump():
                print(f"    {line}")
    return 0


def cmd_policies(args, authority: Authority) -> int:
    print("DEPARTMENTS")
    for department in Department.all():
        code = department.policy()
        print(f"  {department.slug:<14} v{code.version}  w{department.weight:.1f}"
              f"{'  VETO' if department.veto else '      '}"
              f"{'  HALTS' if department.halts else ''}")
        print(f"  {'':<14} {department.title} -- \"{department.motto}\"")
        print(f"  {'':<14} {code.regulation}")

    print("\nFORM 27-B/6 FIELDS VISIBLE TO POLICIES")
    sample = Form27B6().environment()
    for key in sorted(sample):
        value = sample[key]
        shown = f"[{', '.join(value)}]" if isinstance(value, list) else repr(value)
        print(f"  {key:<20} {type(value).__name__:<6} e.g. {shown}")

    print("\nINTRINSIC FUNCTIONS")
    for line in intrinsics.describe():
        print(f"  {line}")
    return 0


def cmd_doctor(args, authority: Authority) -> int:
    print(BANNER)
    print("\n".join(authority.diagnostics()))
    return 0


def cmd_gui(args, authority: Authority) -> int:
    try:
        from .ui.gui import run
    except ImportError as exc:
        print(f"the graphical counter needs tkinter, which is unavailable: {exc}",
              file=sys.stderr)
        print("use `dosl submit --help` instead.", file=sys.stderr)
        return 3
    return run(authority)


COMMANDS = {
    "submit": cmd_submit, "gui": cmd_gui, "audit": cmd_audit,
    "record": cmd_record, "inspect": cmd_inspect, "forge": cmd_forge,
    "policies": cmd_policies, "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # No arguments at all means somebody double-clicked the .exe.
    command = args.command or "gui"

    authority = Authority(args.ledger, prefer_native=not args.no_native,
                          trace=getattr(args, "trace", False))
    try:
        return COMMANDS[command](args, authority)
    except KeyboardInterrupt:
        print("\ninterrupted; the Department has stopped mid-sentence.",
              file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 -- the CLI is the last line of defence
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
