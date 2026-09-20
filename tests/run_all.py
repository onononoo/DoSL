"""
Runs every self-test module and prints one summary.

    python tests/run_all.py            everything
    python tests/run_all.py forge      just tests/test_forge.py
    python tests/run_all.py -v         print each test as it runs

Each module also runs standalone, and the whole suite works under pytest.
Having a plain runner as well means ``build.ps1`` can gate the freeze on the
tests without adding a dependency to do it.
"""

from __future__ import annotations

import importlib
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

MODULES = ("test_forge", "test_language", "test_formats", "test_bureau")


def run_module(name: str, verbose: bool) -> tuple[int, int, list[str]]:
    module = importlib.import_module(name)
    tests = [v for k, v in sorted(vars(module).items())
             if k.startswith("test_") and callable(v)]
    failures: list[str] = []

    for test in tests:
        started = time.perf_counter()
        try:
            test()
        except Exception:
            failures.append(f"{name}.{test.__name__}\n"
                            + traceback.format_exc(limit=6).rstrip())
            status, colour = "FAIL", "!"
        else:
            status, colour = "ok", " "
        if verbose:
            elapsed = (time.perf_counter() - started) * 1000
            print(f" {colour} {status:<4} {test.__name__:<52} {elapsed:7.1f} ms")

    return len(tests), len(failures), failures


def main(argv: list[str]) -> int:
    verbose = "-v" in argv or "--verbose" in argv
    wanted = [a for a in argv if not a.startswith("-")]
    modules = [m for m in MODULES
               if not wanted or any(w in m for w in wanted)]
    if not modules:
        print(f"no test module matches {wanted}; known: {', '.join(MODULES)}")
        return 2

    total = total_failed = 0
    all_failures: list[str] = []
    started = time.perf_counter()

    for name in modules:
        print(f"\n{name}")
        print("-" * 68)
        count, failed, failures = run_module(name, verbose)
        total += count
        total_failed += failed
        all_failures.extend(failures)
        if not verbose:
            print(f"  {count - failed}/{count} passed")

    elapsed = time.perf_counter() - started
    print("\n" + "=" * 68)
    if all_failures:
        for failure in all_failures:
            print(f"\n{failure}")
        print("\n" + "=" * 68)
    print(f"{total - total_failed}/{total} passed across {len(modules)} module(s) "
          f"in {elapsed:.2f}s")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
