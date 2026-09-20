"""Self-tests for the .bureau language: lexer, parser, compiler, VM."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dosl.kernel import compile_source  # noqa: E402
from dosl.kernel.compiler import PolicyCompileError  # noqa: E402
from dosl.kernel.opcodes import Kind, Op, decode  # noqa: E402
from dosl.kernel.syntax import PolicySyntaxError, T, parse, tokenize  # noqa: E402
from dosl.kernel.vm import Machine, PolicyRuntimeError  # noqa: E402
from dosl.native.bridge import PythonEngine  # noqa: E402

ENGINE = PythonEngine()


def run(source: str, **environment):
    """Compile and execute a fragment, with POLICY prepended if missing."""
    if not source.lstrip().startswith("POLICY"):
        source = "POLICY t\n" + source
    return Machine(ENGINE).run(compile_source(source, "t.bureau"), environment)


def value_of(expression: str, **environment) -> float:
    """Evaluate one expression by ASSESSing it and reading the baseline back."""
    return run(f"ASSESS {expression}", **environment).baseline


# ------------------------------------------------------------------- lexer

def test_tokenizer() -> None:
    tokens = tokenize('POLICY x  # a comment\nASSESS 1 + 2.5 * count(a) <= "hi"')
    kinds = [t.kind for t in tokens]
    assert kinds[0] is T.KEYWORD and tokens[0].value == "POLICY"
    assert kinds[1] is T.IDENT
    assert kinds[-1] is T.EOF
    assert [t.value for t in tokens if t.kind is T.NUMBER] == [1, 2.5]
    assert [t.value for t in tokens if t.kind is T.STRING] == ["hi"]
    assert "#" not in [t.value for t in tokens]


def test_line_numbers_survive_comments_and_blanks() -> None:
    tokens = tokenize("POLICY x\n\n# nothing\n\nNOTE \"here\"")
    note = next(t for t in tokens if t.value == "NOTE")
    assert note.line == 5, f"NOTE reported line {note.line}"


def test_string_escapes() -> None:
    (token,) = [t for t in tokenize(r'POLICY x NOTE "a\nb\"c\\d"') if t.kind is T.STRING]
    assert token.value == 'a\nb"c\\d'


def test_keywords_are_case_insensitive() -> None:
    """the shipped policies are lowercase; older uppercase sources still work."""
    upper = run('ASSESS 1\nWHEN TRUE AND NOT FALSE THEN AWARD 5 "y" END')
    lower = run('assess 1\nwhen true and not false then award 5 "y" end')
    mixed = run('Assess 1\nWhen True And Not False Then Award 5 "y" End')
    assert upper.score == lower.score == mixed.score == 6
    # identifiers stay case-sensitive; only keywords are folded.
    assert value_of("Bread", Bread=7) == 7


def test_scanner_rejects_junk() -> None:
    for bad in ["POLICY x\nASSESS 1 & 2", "POLICY x\nNOTE 'single quotes'"]:
        try:
            tokenize(bad)
        except PolicySyntaxError:
            continue
        raise AssertionError(f"accepted {bad!r}")


# ------------------------------------------------------------------ parser

def test_precedence() -> None:
    assert value_of("2 + 3 * 4") == 14
    assert value_of("(2 + 3) * 4") == 20
    assert value_of("10 - 2 - 3") == 5, "subtraction must be left-associative"
    assert value_of("100 / 5 / 2") == 10
    assert value_of("7 % 4 + 1") == 4


def test_not_binds_like_python_not() -> None:
    """NOT must sit below comparisons, or `NOT a == b` means `(NOT a) == b`."""
    assert run('ASSESS 1\nREQUIRE NOT bread MATCHES "bowl" ELSE DENY "x"',
               bread="a bowl").denied
    assert not run('ASSESS 1\nREQUIRE NOT bread MATCHES "bowl" ELSE DENY "x"',
                   bread="rye").denied
    # ...and above AND, so `NOT a AND b` is `(NOT a) AND b`.
    assert value_of("1") == 1
    assert run('ASSESS 1\nWHEN NOT a AND b THEN AWARD 5 "y" END',
               a=False, b=True).adjustments
    assert not run('ASSESS 1\nWHEN NOT a AND b THEN AWARD 5 "y" END',
                   a=True, b=True).adjustments


def test_short_circuit_leaves_one_value() -> None:
    """AND/OR must not leak stack entries, whichever branch is taken."""
    for a, b, expected in [(0, 5, 0), (3, 5, 5)]:
        outcome = run("ASSESS (a AND b) + 100", a=a, b=b)
        assert outcome.baseline == expected + 100, f"a={a} b={b}"
    for a, b, expected in [(0, 5, 5), (3, 5, 3)]:
        assert run("ASSESS (a OR b) + 100", a=a, b=b).baseline == expected + 100


def test_or_does_not_evaluate_the_right_side() -> None:
    """If OR evaluated eagerly, the unknown name below would raise."""
    outcome = run('ASSESS 1\nWHEN TRUE OR nonexistent THEN NOTE "ok" END')
    assert outcome.notes == ["ok"]


def test_lists_and_membership() -> None:
    assert value_of("count([1, 2, 3])") == 3
    assert value_of("count([1, 2, 3, ])") == 3, "trailing comma should be tolerated"
    assert run('ASSESS 1\nREQUIRE "a" IN ["A", "b"] ELSE DENY "x"').findings == []
    assert run('ASSESS 1\nREQUIRE ["a"] CONTAINS "A" ELSE DENY "x"').findings == []


def test_parse_errors_point_at_the_problem() -> None:
    cases = [
        ("POLICY x\nWHEN 1 THEN\nAWARD 1 \"a\"", "END"),
        ("POLICY x\nASSESS ", "value"),
        ("ASSESS 1", "POLICY"),
        ("POLICY x\nLET 5 = 1", "identifier"),
        ("POLICY x\nREQUIRE 1 ELSE MAYBE \"a\"", "DENY"),
        ("POLICY x\nREQUIRE 1 ELSE DENY \"a\" SEVERITY 40", "0 to 9"),
    ]
    for source, expected in cases:
        try:
            parse(source, "t.bureau")
        except PolicySyntaxError as exc:
            assert expected in str(exc), f"{source!r} -> {exc}"
        else:
            raise AssertionError(f"accepted {source!r}")


# ---------------------------------------------------------------- compiler

def test_constants_are_pooled_but_typed() -> None:
    code = compile_source('POLICY x\nNOTE "a"\nNOTE "a"\nASSESS 1')
    assert code.constants.count("a") == 1, "identical strings should pool"
    code = compile_source("POLICY x\nASSESS 1 + 1")
    assert code.constants == [1], "identical numbers should pool"
    # 1 == True in Python; the pool must not conflate them.
    code = compile_source("POLICY x\nWHEN TRUE THEN AWARD 1 \"a\" END\nASSESS 1")
    assert True in code.constants and 1 in code.constants
    assert len([c for c in code.constants if c is True]) == 1


def test_every_jump_is_patched() -> None:
    source = Path(__file__).resolve().parents[1] / "dosl" / "policies" / "ethics.bureau"
    code = compile_source(source.read_text(encoding="utf-8"), "ethics.bureau")
    jumps = (Op.JUMP, Op.JUMP_IF_FALSE, Op.JUMP_IF_TRUE, Op.PEEK_FALSE, Op.PEEK_TRUE)
    seen = 0
    for instruction in decode(code.code):
        if instruction.op in jumps:
            seen += 1
            assert instruction.args[0] != 0xFFFF, \
                f"unpatched jump at {instruction.offset}"
            assert instruction.args[0] <= len(code.code), \
                f"jump past the end at {instruction.offset}"
    assert seen > 5, "this policy should contain plenty of branches"


def test_unknown_intrinsic_is_a_compile_error() -> None:
    for source, expected in [
        ("POLICY x\nASSESS nosuch(1)", "no such function"),
        ("POLICY x\nASSESS count()", "at least 1"),
        ("POLICY x\nASSESS count(1, 2)", "at most 1"),
    ]:
        try:
            compile_source(source, "t.bureau")
        except PolicyCompileError as exc:
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError(f"accepted {source!r}")


def test_line_table_maps_back_to_source() -> None:
    code = compile_source('POLICY x\nNOTE "one"\nNOTE "two"\nASSESS 5', "t.bureau")
    lines = {code.line_for(i.offset) for i in decode(code.code)}
    assert {2, 3, 4} <= lines, lines


def test_listing_is_readable() -> None:
    code = compile_source('POLICY x\nREGULATION "R"\nLET a = 1\nASSESS a', "t.bureau")
    text = "\n".join(code.listing())
    for expected in ("CONST", "STORE", "ASSESS", "HALT", "; 'R'", "; a"):
        assert expected in text, f"{expected!r} missing from listing"


# ---------------------------------------------------------------------- vm

def test_scoring_model() -> None:
    outcome = run('ASSESS 50\nAWARD 10 "a"\nPENALIZE 5 "b"')
    assert outcome.baseline == 50 and outcome.score == 55
    # ASSESS anywhere must not erase adjustments made before it.
    outcome = run('AWARD 10 "a"\nASSESS 50\nPENALIZE 5 "b"')
    assert outcome.score == 55, "ASSESS sets a baseline, it does not overwrite"
    # Score is clamped to 0..100.
    assert run('ASSESS 10\nPENALIZE 999 "a"').score == 0.0
    assert run('ASSESS 90\nAWARD 999 "a"').score == 100.0
    # PENALIZE always subtracts, even when handed a negative number.
    assert run('ASSESS 50\nPENALIZE -10 "a"').score == 40


def test_findings_and_severity() -> None:
    outcome = run('ASSESS 1\n'
                  'REQUIRE FALSE ELSE DENY "no" SEVERITY 9\n'
                  'REQUIRE FALSE ELSE WARN "hmm"\n'
                  'REQUIRE TRUE ELSE DENY "unreached"')
    assert len(outcome.findings) == 2
    assert outcome.denied and outcome.worst == 9
    assert outcome.findings[0].kind is Kind.DENIAL
    assert outcome.findings[1].kind is Kind.OBJECTION
    assert outcome.findings[1].severity == 3, "WARN defaults to severity 3"


def test_case_insensitive_comparison() -> None:
    assert run('ASSESS 1\nREQUIRE bread == "RYE" ELSE DENY "x"', bread="rye").findings == []
    assert run('ASSESS 1\nREQUIRE "MUSTARD" IN c ELSE DENY "x"',
               c=["mustard"]).findings == []


def test_runtime_errors_are_informative() -> None:
    cases = [
        ('ASSESS missing', "unknown field"),
        ('ASSESS 1 / 0', "division by zero"),
        ('ASSESS "text" + 1', "cannot add"),
        ('ASSESS -"text"', "cannot negate"),
        ('ASSESS 1 < "text"', "cannot compare"),
        ('ASSESS "text"', "ASSESS needs a number"),
        ('ASSESS 1\nPENALIZE "text" "why"', "needs a number"),
    ]
    for source, expected in cases:
        try:
            run(source)
        except PolicyRuntimeError as exc:
            assert expected in str(exc), f"{source!r} -> {exc}"
            assert "t.bureau:" in str(exc), "errors must name the source line"
        else:
            raise AssertionError(f"{source!r} did not raise")


def test_intrinsics() -> None:
    checks = {
        "count([1,2,3])": 3,
        "len(\"abcd\")": 4,
        "min(3, 1, 2)": 1,
        "max([3, 9, 2])": 9,
        "sum([1, 2, 3.5])": 6.5,
        "abs(-4)": 4,
        "clamp(50, 0, 10)": 10,
        "clamp(-5, 0, 10)": 0,
        "round(3.14159, 2)": 3.14,
        "count(words(\"a b  c\"))": 3,
        "count(distinct([\"a\", \"A\", \"b\"]))": 2,
        "number(\"12\")": 12,
    }
    for expression, expected in checks.items():
        got = value_of(expression)
        assert got == expected, f"{expression} -> {got}, expected {expected}"

    # The native-backed ones only have to be in range and be stable.
    first = value_of('entropy("rye")')
    assert 0 <= first < 1000 and first == value_of('entropy("rye")')
    assert value_of('entropy("rye")') != value_of('entropy("sourdough")')


def test_step_limit_stops_runaway_bytecode() -> None:
    """Hand-built bytecode with a loop the compiler would never emit."""
    from dosl.kernel.opcodes import encode

    code = compile_source("POLICY x\nASSESS 1", "t.bureau")
    code.code = encode(Op.JUMP, 0)  # jump to itself, forever
    try:
        Machine(ENGINE, step_limit=500).run(code, {})
    except PolicyRuntimeError as exc:
        assert "step limit" in str(exc)
        return
    raise AssertionError("the VM looped without complaint")


def test_bad_jump_target_is_caught() -> None:
    from dosl.kernel.opcodes import encode

    code = compile_source("POLICY x\nASSESS 1", "t.bureau")
    code.code = encode(Op.JUMP, 2) + encode(Op.HALT)  # lands mid-instruction
    try:
        Machine(ENGINE).run(code, {})
    except PolicyRuntimeError as exc:
        assert "middle of an instruction" in str(exc) or "past the end" in str(exc)
        return
    raise AssertionError("the VM executed a misaligned jump")


def test_trace_is_produced_on_request() -> None:
    code = compile_source('POLICY x\nLET a = 2\nASSESS a * 3', "t.bureau")
    outcome = Machine(ENGINE, trace=True).run(code, {})
    assert outcome.baseline == 6
    assert len(outcome.trace) == outcome.steps
    assert any("STORE" in line for line in outcome.trace)


# --------------------------------------------------------- shipped policies

def test_every_shipped_policy_compiles_and_runs() -> None:
    import dosl.policies as policies
    from dosl.bureau.forms import Form27B6

    environment = Form27B6().environment()
    files = policies.discover()
    assert len(files) == len(policies.SHIPPED), \
        f"{len(files)} .bureau files but SHIPPED lists {len(policies.SHIPPED)}"

    for path in files:
        code = compile_source(path.read_text(encoding="utf-8"), path.name)
        assert code.name == path.stem, f"{path.name} declares POLICY {code.name}"
        assert code.regulation, f"{path.name} cites no regulation"
        outcome = Machine(ENGINE).run(code, environment)
        assert 0 <= outcome.score <= 100, f"{path.name} scored {outcome.score}"


def test_policies_import_as_modules() -> None:
    from dosl.policies import condiment, ethics

    assert condiment.POLICY.name == "condiment"
    assert condiment.VERSION >= 1
    assert "policy condiment" in condiment.SOURCE
    assert condiment.__file__.endswith("condiment.bureau")
    assert ethics.POLICY is not condiment.POLICY


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
