"""Self-tests for the struct DSL and the .sdwx container.

NOTE: no ``from __future__ import annotations`` here. This module declares
BinaryStructs, and the DSL reads real annotation objects rather than strings
-- see :func:`test_string_annotations_are_refused`, which asserts that the
DSL says so out loud rather than silently producing an empty layout.
"""

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dosl.formats import sdwx  # noqa: E402
from dosl.formats.sdwx import (  # noqa: E402
    Dossier, FileHeader, Flags, Section, SectionEntry, SdwxError,
)
from dosl.formats.struct_dsl import (  # noqa: E402
    BinaryStruct, Bytes, Magic, StructError, U8, U16, U32, U64, Utf8,
)
from dosl.kernel import compile_source  # noqa: E402


# ---------------------------------------------------------------- struct DSL

class Probe(BinaryStruct, endian="<"):
    magic: Magic(b"PRB")
    small: U8 = 7
    medium: U16 = 0
    large: U32 = 0
    huge: U64 = 0
    name: Utf8(12)
    blob: Bytes(4, b"\x00" * 4)


def test_layout_is_computed_from_the_declaration() -> None:
    assert Probe.size() == 3 + 1 + 2 + 4 + 8 + 12 + 4 == 34
    assert Probe._format == "<3sBHIQ12s4s"
    assert Probe.offset_of("huge") == 10
    assert Probe.offset_of("name") == 18


def test_roundtrip_preserves_everything() -> None:
    probe = Probe(medium=513, large=70000, huge=2 ** 63, name="departure",
                  blob=b"\x01\x02")
    restored = Probe.unpack(probe.pack())
    assert restored == probe
    assert restored.name == "departure", "Utf8 must come back as text"
    assert restored.blob == b"\x01\x02\x00\x00", "Bytes keeps its padding"
    assert restored.small == 7, "defaults must survive"


def test_descriptors_reject_bad_values_at_assignment() -> None:
    probe = Probe()
    cases = [
        ("small", 256, "u8"), ("small", -1, "u8"), ("medium", 65536, "u16"),
        ("large", 2 ** 32, "u32"), ("huge", 2 ** 64, "u64"),
        ("small", "seven", "integer"), ("small", 1.5, "integer"),
        ("name", "far too long to fit here", "bytes"),
        ("blob", b"12345", "fit"), ("magic", b"NOP", "signature"),
    ]
    for attribute, value, expected in cases:
        try:
            setattr(probe, attribute, value)
        except StructError as exc:
            assert expected in str(exc), f"{attribute}={value!r} -> {exc}"
        else:
            raise AssertionError(f"accepted {attribute}={value!r}")


def test_booleans_are_not_integers_here() -> None:
    try:
        Probe(small=True)
    except StructError:
        return
    raise AssertionError("True was accepted as a u8")


def test_unpack_checks_length() -> None:
    try:
        Probe.unpack(b"PRB" + b"\x00" * 4)
    except StructError as exc:
        assert "34 bytes" in str(exc)
        return
    raise AssertionError("unpacked a truncated record")


def test_inheritance_extends_the_layout() -> None:
    class Extended(Probe, endian="<"):
        extra: U16 = 0

    assert Extended.size() == Probe.size() + 2
    assert list(Extended._fields)[-1] == "extra"
    assert Extended(extra=5).pack()[:3] == b"PRB"


def test_string_annotations_are_refused() -> None:
    """A module with `from __future__ import annotations` cannot declare these."""
    try:
        type("Broken", (BinaryStruct,), {"__annotations__": {"x": "U16"}})
    except StructError as exc:
        assert "annotations are strings" in str(exc)
        return
    raise AssertionError("string annotations were accepted")


# -------------------------------------------------------------------- sdwx

def test_header_and_entry_are_the_documented_sizes() -> None:
    assert FileHeader.size() == 128
    assert SectionEntry.size() == 48


def _sample() -> Dossier:
    dossier = Dossier(flags=Flags.SEALED | Flags.CONTAINS_MAYONNAISE)
    dossier.add_json(Section.FORM, "form-27b6", {"bread": "rye", "layers": 2})
    dossier.add_text(Section.POLICY_SOURCE, "big.bureau", "POLICY p\n" * 200)
    dossier.add(Section.SEAL, "seal", b"\x00\xff" * 8)
    dossier.add(Section.ATTACHMENT, "empty", b"")
    return dossier


def test_container_roundtrip() -> None:
    original = _sample()
    restored = Dossier.from_bytes(original.to_bytes())

    assert len(restored) == len(original) == 4
    assert restored.json(Section.FORM) == {"bread": "rye", "layers": 2}
    assert restored.one(name="seal") == b"\x00\xff" * 8
    assert restored.one(name="empty") == b""
    assert restored.flags is original.flags
    assert restored.dossier_id == original.dossier_id
    assert restored.created == original.created


def test_compression_is_only_used_when_it_helps() -> None:
    blob = Dossier.to_bytes(_sample())
    header = FileHeader.unpack(blob)
    entries = list(SectionEntry.unpack_many(blob, header.section_count,
                                            header.directory_offset))
    by_name = {e.name: e for e in entries}
    assert by_name["big.bureau"].encoding != sdwx.Encoding.RAW
    assert by_name["big.bureau"].stored_size < by_name["big.bureau"].original_size
    assert by_name["seal"].encoding == sdwx.Encoding.RAW, "16 bytes is below the floor"
    assert by_name["empty"].stored_size == 0


def test_corruption_is_always_detected() -> None:
    blob = _sample().to_bytes()
    header = FileHeader.unpack(blob)

    payload_offset = FileHeader.size() + 4
    mutations = {
        "flipped payload byte":
            blob[:payload_offset] + bytes([blob[payload_offset] ^ 0x5A])
            + blob[payload_offset + 1:],
        "truncated file": blob[:-4],
        "wrong magic": b"NOPE!!" + blob[6:],
        "edited header": blob[:14] + b"\x09\x00" + blob[16:],
        "removed trailer": blob[:-4] + b"____",
        "directory past EOF":
            blob[:64] + (len(blob) + 500).to_bytes(4, "little") + blob[68:],
    }
    for label, mutated in mutations.items():
        try:
            Dossier.from_bytes(mutated)
        except SdwxError:
            continue
        raise AssertionError(f"{label} went undetected")

    assert header.total_size == len(blob)


def test_crc_and_hash_cover_different_failures() -> None:
    """A CRC that still matches must not let a content-hash change through."""
    dossier = Dossier()
    dossier.add(Section.ATTACHMENT, "a", b"payload one")
    blob = bytearray(dossier.to_bytes())

    # Recompute a valid CRC over corrupted data: only the content hash can
    # catch this.
    start = FileHeader.size()
    blob[start:start + 11] = b"payload two"
    header = FileHeader.unpack(bytes(blob))
    entry_offset = header.directory_offset
    crc_at = entry_offset + SectionEntry.offset_of("crc32")
    blob[crc_at:crc_at + 4] = (zlib.crc32(b"payload two") & 0xFFFFFFFF).to_bytes(4, "little")

    try:
        Dossier.from_bytes(bytes(blob))
    except SdwxError as exc:
        assert "content hash" in str(exc), str(exc)
        return
    raise AssertionError("a re-CRC'd edit passed verification")


def test_section_name_limit() -> None:
    try:
        Dossier().add(Section.FORM, "x" * 40, b"")
    except SdwxError as exc:
        assert "28 bytes" in str(exc)
        return
    raise AssertionError("an over-long section name was accepted")


def test_write_and_read_a_file(tmp: Path = None) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "case.sdwx"
        size = _sample().write(path)
        assert size == path.stat().st_size
        assert Dossier.read(path).json(Section.FORM)["bread"] == "rye"


# --------------------------------------------------------- policy marshalling

def test_policy_marshalling_is_lossless() -> None:
    source = (Path(__file__).resolve().parents[1]
              / "dosl" / "policies" / "temporal.bureau").read_text(encoding="utf-8")
    code = compile_source(source, "temporal.bureau")
    restored = sdwx.unmarshal_policy(sdwx.marshal_policy(code))

    for attribute in ("name", "version", "regulation", "origin", "code",
                      "constants", "names", "intrinsics", "source_digest",
                      "line_table"):
        assert getattr(restored, attribute) == getattr(code, attribute), attribute


def test_marshalling_preserves_integer_types() -> None:
    """JSON would turn these into floats; the tagged encoder must not."""
    code = compile_source('POLICY x\nWHEN TRUE THEN AWARD 1 "a" END\nASSESS 2.5')
    restored = sdwx.unmarshal_policy(sdwx.marshal_policy(code))
    types = [type(c) for c in restored.constants]
    assert bool in types and int in types and float in types
    for original, round_tripped in zip(code.constants, restored.constants):
        assert type(original) is type(round_tripped), f"{original!r}"


def test_marshalling_rejects_the_unmarshallable() -> None:
    code = compile_source("POLICY x\nASSESS 1")
    code.constants.append(object())
    try:
        sdwx.marshal_policy(code)
    except SdwxError as exc:
        assert "cannot marshal" in str(exc)
        return
    raise AssertionError("an arbitrary object was marshalled")


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
