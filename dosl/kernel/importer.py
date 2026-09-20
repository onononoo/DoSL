"""
dosl.kernel.importer -- teaches Python's import system about ``.bureau`` files.

Once :func:`install` has run::

    from dosl.policies import condiment
    condiment.POLICY          # a compiled PolicyCode
    condiment.SOURCE          # the original text
    condiment.__file__        # .../dosl/policies/condiment.bureau

This is a ``sys.meta_path`` finder plus a loader, which is the supported way
to do this and, more importantly, means a policy file participates in
``importlib.reload``, tracebacks, and ``python -X importtime`` like anything
else.

Compiled output is cached beside the source in ``__bureaucache__`` as an
``.sdwx`` dossier, keyed by a digest of the source. The parallel with
``__pycache__`` is entirely deliberate.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from ..formats import sdwx
from .compiler import Compiler, PolicyCode
from .syntax import parse

SUFFIX = ".bureau"
CACHE_DIR = "__bureaucache__"

#: Bumped when the compiler's output changes shape, to invalidate every cache
#: on disk at once. The source digest alone would not catch a compiler change.
CACHE_TAG = "pc1"


class PolicyImportError(ImportError):
    """A ``.bureau`` file exists but could not be turned into a module."""


def cache_path_for(source_path: Path, digest: str) -> Path:
    return source_path.parent / CACHE_DIR / f"{source_path.stem}.{CACHE_TAG}.{digest[:16]}.sdwx"


def compile_file(path: Path, *, use_cache: bool = True) -> tuple[PolicyCode, str]:
    """Compile a ``.bureau`` file, consulting and refreshing the cache."""
    source = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    cache = cache_path_for(path, digest)

    if use_cache and cache.is_file():
        try:
            dossier = sdwx.Dossier.read(cache)
            code = sdwx.unmarshal_policy(dossier.one(sdwx.Section.POLICY_CODE))
            if code.source_digest == digest:
                return code, source
        except (sdwx.SdwxError, OSError, KeyError):
            pass  # a bad cache entry is not a reason to fail the import

    code = Compiler(parse(source, path.name), source).compile()

    if use_cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            dossier = sdwx.Dossier()
            dossier.add(sdwx.Section.POLICY_CODE, code.name, sdwx.marshal_policy(code))
            dossier.add_text(sdwx.Section.POLICY_SOURCE, path.name, source)
            dossier.write(cache)
            _sweep_stale(path, keep=cache)
        except OSError:
            pass  # read-only install, frozen bundle, someone else's directory
    return code, source


def _sweep_stale(source_path: Path, keep: Path) -> None:
    """Delete cache entries for older versions of this same policy file."""
    directory = source_path.parent / CACHE_DIR
    if not directory.is_dir():
        return
    for stale in directory.glob(f"{source_path.stem}.*.sdwx"):
        if stale != keep:
            try:
                stale.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Loader / finder
# --------------------------------------------------------------------------

class BureauLoader(importlib.abc.Loader):
    """Turns one ``.bureau`` file into a module object."""

    def __init__(self, fullname: str, path: Path) -> None:
        self.fullname = fullname
        self.path = path

    def create_module(self, spec) -> ModuleType | None:
        return None  # default module creation is fine

    def exec_module(self, module: ModuleType) -> None:
        try:
            code, source = compile_file(self.path)
        except (SyntaxError, OSError) as exc:
            raise PolicyImportError(
                f"cannot compile {self.path}: {exc}", name=self.fullname,
                path=str(self.path)) from exc

        module.POLICY = code
        module.SOURCE = source
        module.NAME = code.name
        module.REGULATION = code.regulation
        module.VERSION = code.version
        module.__doc__ = (
            f"Policy {code.name} v{code.version}."
            + (f" {code.regulation}" if code.regulation else "")
        )
        module.__all__ = ["POLICY", "SOURCE", "NAME", "REGULATION", "VERSION"]

    def get_source(self, fullname: str) -> str:
        return self.path.read_text(encoding="utf-8")

    def is_package(self, fullname: str) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<BureauLoader for {self.path}>"


class BureauFinder(importlib.abc.MetaPathFinder):
    """Looks for ``<name>.bureau`` on the import path, and nowhere else."""

    def find_spec(self, fullname: str, path=None, target=None):
        leaf = fullname.rpartition(".")[2]
        roots = [Path(entry) for entry in (path if path is not None else sys.path)
                 if isinstance(entry, (str, Path))]
        for root in roots:
            candidate = root / f"{leaf}{SUFFIX}"
            try:
                exists = candidate.is_file()
            except OSError:
                continue
            if exists:
                spec = importlib.util.spec_from_loader(
                    fullname, BureauLoader(fullname, candidate),
                    origin=str(candidate))
                if spec is not None:
                    spec.has_location = True
                    spec.origin = str(candidate)
                return spec
        return None

    def __repr__(self) -> str:
        return "<BureauFinder>"


_installed: BureauFinder | None = None


def install() -> BureauFinder:
    """Add the finder to ``sys.meta_path``. Safe to call repeatedly.

    It goes at the *end*: a ``.bureau`` file must never win against a real
    Python module of the same name, and the standard finders decline quickly
    when nothing matches.
    """
    global _installed
    if _installed is None:
        _installed = BureauFinder()
        sys.meta_path.append(_installed)
    return _installed


def uninstall() -> None:
    global _installed
    if _installed is not None and _installed in sys.meta_path:
        sys.meta_path.remove(_installed)
    _installed = None


def discover(package: str = "dosl.policies") -> list[Path]:
    """List every ``.bureau`` file shipped inside ``package``."""
    try:
        module = __import__(package, fromlist=["__path__"])
    except ImportError:
        return []
    found: list[Path] = []
    for entry in getattr(module, "__path__", []):
        found.extend(sorted(Path(entry).glob(f"*{SUFFIX}")))
    return found
