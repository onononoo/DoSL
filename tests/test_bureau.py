"""Self-tests for the bureaucracy: forms, departments, tribunal, ledger, GUI."""

from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dosl.bureau import Authority, Form27B6, Tribunal, ValidationError, Verdict  # noqa: E402
from dosl.bureau import certificate as cert  # noqa: E402
from dosl.bureau.departments import Department, DepartmentMeta  # noqa: E402
from dosl.bureau.ledger import GENESIS, Ledger  # noqa: E402
from dosl.formats import sdwx  # noqa: E402

FIXED_TIME = _dt.datetime(2031, 4, 1, 13, 5, 0)


def _authority(directory: str) -> Authority:
    return Authority(Path(directory) / "record.jsonl")


def _good_form(**overrides) -> Form27B6:
    values = dict(
        applicant="r. milquetoast", bread="rye", fillings=["pastrami", "pickle"],
        condiments=["mustard"], cheese="swiss", cut="diagonal", layers=2,
        height_mm=42, toasted=True, crusts_removed=False,
        consumed_at="kitchen table", hour=13, urgency=1,
        accompaniment="crisps", declared_purpose="lunch", clearance=4)
    values.update(overrides)
    return Form27B6(**values)


# -------------------------------------------------------------------- forms

def test_fields_are_collected_in_declaration_order() -> None:
    names = list(Form27B6.fields)
    assert names[0] == "applicant"
    assert "filling_count" not in names, "derived values are not form fields"
    assert len(names) == 16, f"the form has {len(names)} fields"


def test_coercion() -> None:
    form = Form27B6(fillings="Ham, , Cheese;Tomato", toasted="yes",
                    height_mm="47.8", cut="DIAGONAL", crusts_removed="no")
    assert form.fillings == ["ham", "cheese", "tomato"], form.fillings
    assert form.toasted is True and form.crusts_removed is False
    assert form.height_mm == 47
    assert form.cut == "diagonal", "choices should match case-insensitively"


def test_validation_reports_every_problem_at_once() -> None:
    problems = Form27B6(applicant="", bread="", fillings=[], layers=99,
                        hour=40, cut="spiral").problems()
    labels = {p.field for p in problems}
    assert {"name of applicant", "vessel", "fillings", "bread layers",
            "hour of consumption", "cut geometry"} <= labels, labels
    assert all(label == label.lower() for label in labels), labels


def test_cross_field_checks() -> None:
    thin = Form27B6(layers=1, height_mm=80)
    assert any("40mm" in p.message for p in thin.problems())

    crusty = _good_form(bread="baguette", crusts_removed=True)
    assert any("crust" in p.message for p in crusty.problems())

    overlap = _good_form(fillings=["ham", "mustard"], condiments=["mustard"])
    assert any("both filling and condiment" in p.message for p in overlap.problems())

    assert _good_form().problems() == []


def test_require_valid_raises_with_all_the_detail() -> None:
    try:
        Form27B6(applicant="", fillings=[]).require_valid()
    except ValidationError as exc:
        assert "name of applicant" in str(exc) and "fillings" in str(exc)
        return
    raise AssertionError("an invalid form was accepted")


def test_environment_exposes_exactly_what_policies_use() -> None:
    environment = _good_form().environment(when=FIXED_TIME)
    assert environment["filling_count"] == 2
    assert environment["condiment_count"] == 1
    assert environment["total_items"] == 3
    assert environment["weekday"] == "tuesday"
    assert set(Form27B6.fields) <= set(environment)


def test_form_json_roundtrip() -> None:
    form = _good_form()
    assert Form27B6.from_dict(form.as_dict()).as_dict() == form.as_dict()


# -------------------------------------------------------------- departments

def test_every_department_is_registered_and_distinct() -> None:
    departments = Department.all()
    assert len(departments) == 5
    assert len({d.slug for d in departments}) == 5
    assert [d.slug for d in departments] == [
        "nomenclature", "structural", "condiment", "temporal", "ethics"]
    for department in departments:
        assert department.title and department.motto
        assert department.policy().name == department.slug
        assert department.title == department.title.lower(), department.title


def test_duplicate_slug_is_refused() -> None:
    from dosl.bureau.departments import DepartmentError

    try:
        DepartmentMeta("Clash", (Department,), {"slug": "ethics"})
    except DepartmentError as exc:
        assert "already taken" in str(exc)
        return
    raise AssertionError("two departments claimed the same slug")


# ----------------------------------------------------------------- tribunal

def test_a_good_sandwich_is_approved() -> None:
    adjudication = Tribunal().convene(_good_form(), when=FIXED_TIME)
    assert adjudication.verdict.permitted, adjudication.verdict
    assert adjudication.score > 80
    assert all(r.outcome is not None for r in adjudication.results)
    assert len(adjudication.citations) == 5, "each department cites its regulation"


def test_a_wrap_is_denied_and_halts_the_sitting() -> None:
    adjudication = Tribunal().convene(
        _good_form(bread="tortilla wrap"), when=FIXED_TIME)
    assert adjudication.verdict is Verdict.DENIED
    convened = [r for r in adjudication.results if r.outcome is not None]
    assert len(convened) == 1, "nomenclature halts; nobody else should sit"
    assert "jurisdiction declined" in adjudication.results[1].error


def test_a_structural_veto_denies_but_does_not_halt() -> None:
    adjudication = Tribunal().convene(_good_form(layers=1, height_mm=20),
                                      when=FIXED_TIME)
    assert adjudication.verdict is Verdict.DENIED
    assert all(r.outcome is not None for r in adjudication.results), \
        "structural vetoes without halting, so everyone still sits"


def test_a_severe_objection_caps_an_otherwise_perfect_score() -> None:
    """Pineapple + ham + toasted is a severity-8 denial from ethics."""
    adjudication = Tribunal().convene(
        _good_form(fillings=["ham", "pineapple"], toasted=True), when=FIXED_TIME)
    assert adjudication.verdict is Verdict.REFERRED
    assert any(f.severity >= 8 for f in adjudication.findings)


def test_adjudication_is_deterministic() -> None:
    first = Tribunal().convene(_good_form(), when=FIXED_TIME)
    second = Tribunal().convene(_good_form(), when=FIXED_TIME)
    assert first.reference == second.reference
    assert first.seal == second.seal
    assert first.score == second.score


def test_the_seal_changes_when_anything_material_changes() -> None:
    base = Tribunal().convene(_good_form(), when=FIXED_TIME)
    other = Tribunal().convene(_good_form(applicant="someone else"), when=FIXED_TIME)
    assert base.seal != other.seal
    assert base.reference != other.reference


def test_a_broken_policy_does_not_break_the_tribunal() -> None:
    class Broken(Department):
        slug = "" or None  # unregistered on purpose
        title = "bureau of things that do not work"
        weight = 1.0

        @classmethod
        def policy(cls):
            raise RuntimeError("the regulations are in the other building")

    Broken.slug = "broken"
    adjudication = Tribunal(departments=[*Department.all(), Broken]).convene(
        _good_form(), when=FIXED_TIME)
    broken = adjudication.results[-1]
    assert broken.error and "other building" in broken.error
    assert adjudication.verdict.permitted, "one failed department must not deny"


# ------------------------------------------------------------------- ledger

def test_ledger_chains_entries() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        entries = [authority.adjudicate(_good_form(applicant=f"person {i}"))[1]
                   for i in range(5)]

        assert [e.index for e in entries] == [0, 1, 2, 3, 4]
        assert entries[0].previous_hash == GENESIS
        for previous, current in zip(entries, entries[1:]):
            assert current.previous_hash == previous.entry_hash

        ok, complaints = authority.ledger.verify()
        assert ok and not complaints
        assert len(authority.ledger) == 5


def test_ledger_detects_an_edited_entry() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        for i in range(3):
            authority.adjudicate(_good_form(applicant=f"person {i}"))

        lines = authority.ledger.path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"person 1"', '"person x"')
        authority.ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, complaints = authority.ledger.verify()
        assert not ok
        assert any("edited" in c for c in complaints), complaints
        assert any("does not match" in c for c in complaints), complaints


def test_ledger_detects_a_deleted_entry() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        for i in range(4):
            authority.adjudicate(_good_form(applicant=f"person {i}"))

        lines = authority.ledger.path.read_text(encoding="utf-8").splitlines()
        del lines[2]
        authority.ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, complaints = authority.ledger.verify()
        assert not ok
        assert any("index should be" in c for c in complaints), complaints


def test_ledger_statistics() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        assert authority.ledger.statistics()["count"] == 0
        authority.adjudicate(_good_form())
        authority.adjudicate(_good_form(applicant="other", bread="a bowl"))

        stats = authority.ledger.statistics()
        assert stats["count"] == 2 and stats["applicants"] == 2
        assert stats["best"].score >= stats["worst"].score
        assert sum(stats["verdicts"].values()) == 2


def test_empty_ledger_is_valid() -> None:
    with tempfile.TemporaryDirectory() as directory:
        ledger = Ledger(Path(directory) / "nothing.jsonl")
        assert ledger.verify() == (True, [])
        assert ledger.tail() == [] and ledger.last() is None


# ---------------------------------------------------------------- authority

def test_dossier_contains_everything_needed_to_re_audit() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        adjudication, entry = authority.adjudicate(
            _good_form(condiments=["mustard", "mayonnaise"]))
        path = Path(directory) / "case.sdwx"
        authority.save_dossier(adjudication, path, entry)

        dossier = sdwx.Dossier.read(path)
        assert dossier.flags & sdwx.Flags.CONTAINS_MAYONNAISE
        assert dossier.json(sdwx.Section.MANIFEST)["reference"] == adjudication.reference
        assert dossier.json(sdwx.Section.FORM)["bread"] == "rye"
        assert dossier.json(sdwx.Section.LEDGER)["entry_hash"] == entry.entry_hash

        # Every department's compiled policy travels with the verdict, so the
        # decision can be reproduced later even if the rules have changed.
        for department in Department.all():
            code = sdwx.unmarshal_policy(
                dossier.one(sdwx.Section.POLICY_CODE, department.slug))
            assert code.name == department.slug
            assert code.code == department.policy().code


def test_diagnostics_mention_every_part() -> None:
    with tempfile.TemporaryDirectory() as directory:
        text = "\n".join(_authority(directory).diagnostics())
        for expected in ("engine", "ledger", "record", "nomenclature", "ethics"):
            assert expected in text, f"{expected!r} missing from diagnostics"
        if os.name == "nt":
            assert "native" in text, "the DLL should be in use on Windows"
            assert ".text" in text and ".reloc" in text


def test_recompile_picks_up_source() -> None:
    with tempfile.TemporaryDirectory() as directory:
        authority = _authority(directory)
        before = Department.all()[0].policy()
        after = authority.recompile(before.name)
        assert after.code == before.code
        assert after.source_digest == before.source_digest


# ------------------------------------------------------------- certificates

def test_certificate_is_pure_ascii_and_fits_the_page() -> None:
    adjudication = Tribunal().convene(
        _good_form(fillings=["ham", "pineapple"], cut="chaotic", height_mm=99),
        when=FIXED_TIME)
    for text in (cert.render(adjudication), cert.full_report(adjudication)):
        text.encode("ascii")  # raises if anything non-ASCII crept in
        longest = max(len(line) for line in text.splitlines())
        assert longest <= cert.WIDTH, f"a line is {longest} characters"
    # the score row must fit without _fit having to break it: a wrapped
    # progress bar looks like a bug, because it is one.
    row = next(line for line in cert.render(adjudication).splitlines()
               if "composite legitimacy" in line)
    assert row.rstrip().endswith(f"{adjudication.score:6.2f}".strip()), row
    assert len(f"  composite legitimacy score   {cert._bar(100.0)}") <= cert.WIDTH


def _prose_only(text: str) -> str:
    """strip the things the house style does not govern.

    urls are addresses, not prose: ``https://github.com/onononoo/DoSL`` has
    to keep its spelling or the link stops being the link.
    """
    import re

    return re.sub(r"https?://\S+", "", text)


def test_everything_the_department_prints_is_lowercase() -> None:
    """the house style. urls and user-supplied text are exempt; nothing else."""
    adjudication = Tribunal().convene(
        _good_form(fillings=["ham", "pineapple"], cut="chaotic"), when=FIXED_TIME)

    for name, text in (("certificate", cert.render(adjudication)),
                       ("full report", cert.full_report(adjudication))):
        shouting = sorted({c for c in _prose_only(text) if c.isupper()})
        assert not shouting, f"{name} contains upper case: {shouting}"

    assert adjudication.reference == adjudication.reference.lower()
    assert adjudication.seal == adjudication.seal.lower()
    assert adjudication.verdict.label == adjudication.verdict.label.lower()
    assert adjudication.engine == adjudication.engine.lower()

    with tempfile.TemporaryDirectory() as directory:
        diagnostics = "\n".join(_authority(directory).diagnostics())
    # paths are the operating system's business, so judge only the prose.
    prose = "\n".join(line.split("  ")[0] for line in diagnostics.splitlines())
    assert not [c for c in prose if c.isupper()], prose


def test_support_block_is_present_and_correct() -> None:
    from dosl import support

    assert support.BTC_ADDRESS == "bc1qs4z04ltddh6vaqd4stu3p4vekv253ht4cwqma4"
    assert support.BTC_ADDRESS.startswith("bc1q") and len(support.BTC_ADDRESS) == 42

    block = support.as_text()
    # the blurb is wrapped on the way in, so compare with whitespace folded.
    assert " ".join(support.BLURB.split()) in " ".join(block.split())
    assert support.BTC_ADDRESS in block, "the address must never be wrapped"
    assert support.SOURCE_URL in block and support.PROJECTS_URL in block
    assert max(len(line) for line in block.splitlines()) <= cert.WIDTH
    block.encode("ascii")

    # the readme variant drops the repository link, since the readme is in it.
    without = support.as_text(include_source=False)
    assert support.SOURCE_URL not in without
    assert support.PROJECTS_URL in without

    # every long report carries the block, so a filed dossier carries it too.
    adjudication = Tribunal().convene(_good_form(), when=FIXED_TIME)
    report = cert.full_report(adjudication)
    assert support.BTC_ADDRESS in report and support.PROJECTS_URL in report
    assert adjudication.reference in cert.render(adjudication)
    assert "schedule c" in cert.full_report(adjudication)
    assert cert.one_line(adjudication).startswith(adjudication.reference)


def test_every_verdict_has_a_stamp() -> None:
    for verdict in Verdict:
        assert verdict in cert._STAMP
        assert verdict.label and verdict.advice


# ---------------------------------------------------------------------- gui

def test_gui_builds_and_submits() -> None:
    try:
        import tkinter
        tkinter.Tk().destroy()
    except Exception as exc:  # no display, no tkinter, no problem
        print(f"  (skipped: {exc})")
        return

    from dosl.ui.gui import Counter

    with tempfile.TemporaryDirectory() as directory:
        app = Counter(_authority(directory))
        try:
            assert len(app.widgets) == len(Form27B6.fields)
            app.randomise()
            app.submit()
            assert app.adjudication is not None and app.entry is not None

            app.widgets["applicant"].set("")
            app.submit()
            assert "returned" in app.status.get().casefold()

            app.reset()
            app.submit()
            assert app.adjudication.applicant == "a. citizen"

            for index in range(len(app.tabs.tabs())):
                app.tabs.select(index)
            for department in Department.all():
                app.policy_choice.set(department.slug)
                app._show_disassembly()
            app.show_source.set(True)
            app._show_disassembly()
            app.recompile_policies()
            app._refresh_record()
            app._refresh_diagnostics()
            app.update()
        finally:
            app.destroy()


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
