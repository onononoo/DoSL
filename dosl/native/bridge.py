"""
dosl.native.bridge -- builds, loads and talks to bureau_entropy.dll.

On first use this module materialises the DLL out of thin air (see
:mod:`dosl.forge`), drops it in ``build/``, hands it to ``LoadLibraryW``
via ctypes, and verifies the ABI version the DLL reports against the one
the Python side was compiled against.

If any part of that goes wrong -- not Windows, not x86-64, DLL blocked by
policy, a ModR/M byte off by one -- the module quietly falls back to the
pure-Python reference kernels. The Department believes in continuity of
service.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..forge import kernels, pe

DLL_NAME = "bureau_entropy.dll"

#: Every digest chain starts here, so identical inputs hash identically
#: across processes, machines, and engine backends.
DIGEST_SEED = 0x0D05_1_0000


class BridgeError(RuntimeError):
    """Raised when the native engine cannot be used."""


def _build_root() -> Path:
    """Prefer ``<project>/build``; fall back to temp when frozen or read-only."""
    if getattr(sys, "frozen", False):
        return Path(tempfile.gettempdir()) / "dosl-build"
    candidate = Path(__file__).resolve().parents[2] / "build"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".writable"
        probe.write_bytes(b"")
        probe.unlink()
        return candidate
    except OSError:
        return Path(tempfile.gettempdir()) / "dosl-build"


def materialise_dll(*, force: bool = False) -> Path:
    """Write ``bureau_entropy.dll`` to disk and return its path.

    The canonical filename is reused when its contents already match. When
    it exists but differs and cannot be replaced -- which on Windows means
    some process has it mapped -- a content-addressed sibling is written
    instead, because arguing with the loader never works.
    """
    root = _build_root()
    root.mkdir(parents=True, exist_ok=True)
    image = kernels.build_dll(DLL_NAME)
    digest = hashlib.sha256(image).hexdigest()[:12]

    canonical = root / DLL_NAME
    if canonical.exists() and not force:
        try:
            if canonical.read_bytes() == image:
                return canonical
        except OSError:
            pass
    try:
        canonical.write_bytes(image)
        return canonical
    except OSError:
        pass

    sibling = root / f"bureau_entropy-{digest}.dll"
    if not sibling.exists():
        sibling.write_bytes(image)
    return sibling


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------

class _Engine:
    """Interface shared by the native and pure-Python entropy engines."""

    backend: str = "abstract"
    detail: str = ""

    def abi_version(self) -> int:
        raise NotImplementedError

    def splitmix64(self, seed: int) -> int:
        raise NotImplementedError

    def fnv1a(self, data: bytes) -> int:
        raise NotImplementedError

    def verdict(self, entropy: int, clearance: int) -> int:
        raise NotImplementedError

    def mix2(self, a: int, b: int) -> int:
        raise NotImplementedError

    def digest(self, *parts: bytes | str | int) -> int:
        """Fold an arbitrary pile of values into one 64-bit number.

        Implemented once, here, on top of the four primitives, so the native
        and Python engines cannot possibly disagree about this part.
        """
        acc = self.splitmix64(DIGEST_SEED)
        for part in parts:
            if isinstance(part, str):
                blob = part.encode("utf-8")
            elif isinstance(part, int):
                blob = part.to_bytes(8, "little", signed=part < 0)
            else:
                blob = bytes(part)
            acc = self.mix2(acc, self.splitmix64(self.fnv1a(blob)))
        return acc


class PythonEngine(_Engine):
    """The reference implementation. Slow, portable, definitionally correct."""

    backend = "python"
    detail = "pure-python reference kernels"

    def abi_version(self) -> int:
        return kernels.ABI_VERSION

    def splitmix64(self, seed: int) -> int:
        return kernels.splitmix64_ref(seed & kernels.U64)

    def fnv1a(self, data: bytes) -> int:
        return kernels.fnv1a_ref(data)

    def verdict(self, entropy: int, clearance: int) -> int:
        return kernels.verdict_ref(entropy & kernels.U64, clearance & kernels.U32)

    def mix2(self, a: int, b: int) -> int:
        return kernels.mix2_ref(a & kernels.U64, b & kernels.U64)


class NativeEngine(_Engine):
    """Calls the hand-assembled machine code in bureau_entropy.dll."""

    backend = "native"

    def __init__(self, path: Path) -> None:
        if platform.machine().lower() not in ("amd64", "x86_64"):
            raise BridgeError(f"kernels are x86-64 only, this is {platform.machine()}")
        if os.name != "nt":
            raise BridgeError("PE images only load on Windows")

        self.path = path
        try:
            self.lib = ctypes.CDLL(str(path))
        except OSError as exc:
            raise BridgeError(f"LoadLibrary failed for {path}: {exc}") from exc

        u32, u64 = ctypes.c_uint32, ctypes.c_uint64
        self._bind("bureau_abi_version", [], u32)
        self._bind("bureau_splitmix64", [u64], u64)
        self._bind("bureau_fnv1a", [ctypes.c_void_p, ctypes.c_size_t], u32)
        self._bind("bureau_verdict", [u64, u32], u32)
        self._bind("bureau_mix2", [u64, u64], u64)

        reported = self.lib.bureau_abi_version()
        if reported != kernels.ABI_VERSION:
            raise BridgeError(
                f"abi mismatch: dll reports {reported:#010x}, "
                f"expected {kernels.ABI_VERSION:#010x}"
            )
        self.detail = f"{path.name} @ abi {reported:#010x}"

    def _bind(self, symbol: str, argtypes: list, restype) -> None:
        try:
            fn = getattr(self.lib, symbol)
        except AttributeError as exc:
            raise BridgeError(f"DLL is missing export {symbol!r}") from exc
        fn.argtypes = argtypes
        fn.restype = restype

    def abi_version(self) -> int:
        return int(self.lib.bureau_abi_version())

    def splitmix64(self, seed: int) -> int:
        return int(self.lib.bureau_splitmix64(seed & kernels.U64))

    def fnv1a(self, data: bytes) -> int:
        blob = bytes(data)
        buf = ctypes.create_string_buffer(blob, len(blob))
        return int(self.lib.bureau_fnv1a(ctypes.cast(buf, ctypes.c_void_p), len(blob)))

    def verdict(self, entropy: int, clearance: int) -> int:
        return int(self.lib.bureau_verdict(entropy & kernels.U64,
                                           clearance & kernels.U32))

    def mix2(self, a: int, b: int) -> int:
        return int(self.lib.bureau_mix2(a & kernels.U64, b & kernels.U64))


@dataclass(slots=True)
class BridgeReport:
    """What happened when we tried to go native, for the diagnostics screen."""

    engine: _Engine
    dll_path: Path | None
    error: str | None
    pe_info: dict | None

    @property
    def ok(self) -> bool:
        return self.error is None


_CACHE: BridgeReport | None = None


def load_engine(*, prefer_native: bool = True, force_rebuild: bool = False) -> BridgeReport:
    """Return a cached :class:`BridgeReport` describing the live engine."""
    global _CACHE
    if _CACHE is not None and not force_rebuild:
        return _CACHE

    if not prefer_native:
        _CACHE = BridgeReport(PythonEngine(), None, None, None)
        return _CACHE

    path: Path | None = None
    info: dict | None = None
    try:
        path = materialise_dll(force=force_rebuild)
        info = pe.inspect(path.read_bytes())
        _CACHE = BridgeReport(NativeEngine(path), path, None, info)
    except (BridgeError, OSError, pe.LinkError) as exc:
        _CACHE = BridgeReport(PythonEngine(), path, f"{type(exc).__name__}: {exc}", info)
    return _CACHE


def engine() -> _Engine:
    """Shorthand for ``load_engine().engine``."""
    return load_engine().engine
