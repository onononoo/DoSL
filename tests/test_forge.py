"""Self-tests for the forge: encoder, linker, and the DLL they produce.

Run directly (``python tests/test_forge.py``) or under pytest.

The decisive test is :func:`test_native_matches_reference`: the hand-encoded
machine code and the Python reference implementations are handed the same
inputs and must produce identical bits. If a ModR/M byte is wrong, this is
where it surfaces.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dosl.forge import kernels, pe  # noqa: E402
from dosl.forge.x64 import RAX, RCX, RDX, R8, Assembler, Cond, EncodingError  # noqa: E402
from dosl.native import bridge  # noqa: E402


# --------------------------------------------------------------------- x64

def test_encoder_known_bytes() -> None:
    """Spot-check encodings against bytes verified by hand."""
    cases = [
        (lambda a: a.mov(RAX, RCX), "48 89 c8"),
        (lambda a: a.movabs(RDX, 0x9E3779B97F4A7C15), "48 ba 15 7c 4a 7f b9 79 37 9e"),
        (lambda a: a.add(RAX, RDX), "48 01 d0"),
        (lambda a: a.xor(RAX, RDX), "48 31 d0"),
        (lambda a: a.shr(RDX, 30), "48 c1 ea 1e"),
        (lambda a: a.imul(RAX, RDX), "48 0f af c2"),
        (lambda a: a.mov_imm32(RAX, 0x811C9DC5), "b8 c5 9d 1c 81"),
        (lambda a: a.test(RDX, RDX), "48 85 d2"),
        (lambda a: a.movzx_byte(R8, RCX), "44 0f b6 01"),
        (lambda a: a.xor(RAX, R8, w=False), "44 31 c0"),
        (lambda a: a.imul_imm32(RAX, RAX, 0x01000193), "69 c0 93 01 00 01"),
        (lambda a: a.inc(RCX), "48 ff c1"),
        (lambda a: a.dec(RDX), "48 ff ca"),
        (lambda a: a.mov(R8, RAX), "49 89 c0"),
        (lambda a: a.shr(R8, 32), "49 c1 e8 20"),
        (lambda a: a.add(RAX, RDX, w=False), "01 d0"),
        (lambda a: a.ret(), "c3"),
    ]
    for build, expected in cases:
        asm = Assembler()
        build(asm)
        got = " ".join(f"{b:02x}" for b in asm.assemble())
        assert got == expected, f"expected [{expected}] got [{got}]"


def test_encoder_labels_and_fixups() -> None:
    """A backward rel32 branch must land exactly on its label."""
    asm = Assembler()
    asm.label("top")
    asm.inc(RCX)          # 3 bytes
    asm.jcc(Cond.NE, "top")  # 6 bytes, so displacement must be -9
    code = asm.assemble()
    assert code[:3] == b"\x48\xff\xc1"
    assert code[3:5] == b"\x0f\x85"
    assert int.from_bytes(code[5:9], "little", signed=True) == -9


def test_encoder_rejects_nonsense() -> None:
    for build in (
        lambda: Assembler().shr(RAX, 99),
        lambda: Assembler().jmp("nowhere").assemble(),
        lambda: Assembler().label("x").label("x"),
    ):
        try:
            build()
        except EncodingError:
            continue
        raise AssertionError("expected EncodingError")


# ---------------------------------------------------------------------- pe

def test_linker_roundtrip() -> None:
    """Everything the writer emitted must survive being read back."""
    image = kernels.build_dll()
    info = pe.inspect(image)

    assert image[:2] == b"MZ"
    assert info["machine"] == pe.IMAGE_FILE_MACHINE_AMD64
    assert info["magic"] == pe.IMAGE_NT_OPTIONAL_HDR64_MAGIC
    assert info["characteristics"] & pe.IMAGE_FILE_DLL
    assert info["entry_rva"] == pe.SECTION_ALIGNMENT
    assert len(image) % pe.FILE_ALIGNMENT == 0
    assert {s["name"] for s in info["sections"]} == {".text", ".rdata", ".reloc"}

    names = [n for n, _ in info["exports"]]
    assert names == sorted(names), "GetProcAddress binary searches; sort is mandatory"
    assert set(names) == {name for name, *_ in kernels.KERNELS}

    for _, rva in info["exports"]:
        assert pe.SECTION_ALIGNMENT <= rva < pe.SECTION_ALIGNMENT + len(
            [s for s in info["sections"] if s["name"] == ".text"][0]["name"]
        ) + info["size_of_image"]


def test_linker_is_deterministic() -> None:
    assert kernels.build_dll() == kernels.build_dll()


def test_linker_rejects_duplicates() -> None:
    image = pe.DllImage("x.dll")
    image.set_entry(kernels.dllmain())
    image.add_export(pe.Export("a", b"\xc3"))
    try:
        image.add_export(pe.Export("a", b"\xc3"))
    except pe.LinkError:
        return
    raise AssertionError("expected LinkError on duplicate export")


# ------------------------------------------------------------------ bridge

def test_native_matches_reference() -> None:
    """The whole point. Machine code and reference must agree, bit for bit."""
    report = bridge.load_engine(force_rebuild=True)
    if report.engine.backend != "native":
        if os.name == "nt":
            raise AssertionError(f"native engine unavailable: {report.error}")
        print(f"  (skipped: not Windows -- {report.error})")
        return

    native, ref = report.engine, bridge.PythonEngine()
    rng = random.Random(0x5A4E_4457)

    assert native.abi_version() == ref.abi_version() == kernels.ABI_VERSION

    seeds = [0, 1, 2, kernels.U64, 0x8000_0000_0000_0000]
    seeds += [rng.getrandbits(64) for _ in range(400)]
    for seed in seeds:
        assert native.splitmix64(seed) == ref.splitmix64(seed), f"splitmix64({seed:#x})"

    blobs = [b"", b"\x00", b"mustard", bytes(range(256))]
    blobs += [bytes(rng.getrandbits(8) for _ in range(rng.randrange(1, 64)))
              for _ in range(200)]
    for blob in blobs:
        assert native.fnv1a(blob) == ref.fnv1a(blob), f"fnv1a({blob!r})"

    for _ in range(400):
        e, c = rng.getrandbits(64), rng.getrandbits(32)
        assert native.verdict(e, c) == ref.verdict(e, c), f"verdict({e:#x},{c:#x})"
        a, b = rng.getrandbits(64), rng.getrandbits(64)
        assert native.mix2(a, b) == ref.mix2(a, b), f"mix2({a:#x},{b:#x})"

    for parts in ([b"rye", 3, "mustard"], [], ["x" * 500]):
        assert native.digest(*parts) == ref.digest(*parts)


def test_known_answer_vectors() -> None:
    """Pin the algorithms down so a 'harmless' refactor cannot drift them."""
    ref = bridge.PythonEngine()
    assert ref.splitmix64(0) == 0xE220A8397B1DCDAF
    assert ref.splitmix64(1) == 0x910A2DEC89025CC1
    assert ref.fnv1a(b"") == 0x811C9DC5
    assert ref.fnv1a(b"a") == 0xE40C292C
    assert ref.fnv1a(b"foobar") == 0xBF9CF968


# ---------------------------------------------------------------------- run

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 -- this *is* the reporter
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
