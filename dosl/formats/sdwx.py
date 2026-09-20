"""
dosl.formats.sdwx -- the Sandwich Dossier Exchange container (``.sdwx``).

A dossier is a sectioned archive with a fixed header, a directory at the
tail, per-section CRC-32, a whole-file SHA-256 over the payload region, and
a trailer word so truncation is detected rather than guessed at.

Layout on disk::

    +--------------------------------------+ 0
    | FileHeader        (128 bytes, fixed) |
    +--------------------------------------+ 128
    | section payloads, back to back       |
    | (each zlib- or lzma-compressed when  |
    |  that actually makes it smaller)     |
    +--------------------------------------+ directory_offset
    | SectionEntry * section_count         |
    +--------------------------------------+
    | trailer  b"XWDS"                     |
    +--------------------------------------+ total_size

The directory lives at the end so the writer can stream payloads without
knowing their compressed sizes up front, and the header is patched in place
afterwards -- which is what :meth:`BinaryStruct.offset_of` is for.

NOTE: this module must not use ``from __future__ import annotations``.
The struct DSL reads real annotation objects, not strings.
"""

import enum
import hashlib
import json
import lzma
import os
import struct
import time
import zlib
from typing import Any, Iterator

from .struct_dsl import (
    Bytes, Magic, StructError, U8, U16, U32, U64, Utf8, BinaryStruct,
)

MAGIC = b"SDWX\x1a\n"
TRAILER = b"XWDS"
FORMAT_MAJOR = 1
FORMAT_MINOR = 2
GENERATOR = "DoSL/1.0 forge"


class SdwxError(ValueError):
    """The bytes handed to us are not a dossier we can work with."""


class Section(enum.IntEnum):
    """What a section holds. Unknown kinds are preserved, not discarded."""

    MANIFEST = 1
    FORM = 2
    POLICY_CODE = 3
    POLICY_SOURCE = 4
    OUTCOME = 5
    LEDGER = 6
    SEAL = 7
    ATTACHMENT = 8


class Encoding(enum.IntEnum):
    RAW = 0
    ZLIB = 1
    LZMA = 2


class Flags(enum.IntFlag):
    NONE = 0
    SEALED = 1 << 0
    PROVISIONAL = 1 << 1
    UNDER_APPEAL = 1 << 2
    CONTAINS_MAYONNAISE = 1 << 3  # a warning to the reader


# --------------------------------------------------------------------------
# On-disk records
# --------------------------------------------------------------------------

class FileHeader(BinaryStruct, endian="<"):
    """128 bytes, fixed. Everything after byte 6 is patched after writing."""

    magic: Magic(MAGIC)                      # 6
    version_major: U8 = FORMAT_MAJOR         # 7
    version_minor: U8 = FORMAT_MINOR         # 8
    flags: U32 = 0                           # 12
    section_count: U16 = 0                   # 14
    reserved0: U16 = 0                       # 16
    created: U64 = 0                         # 24
    dossier_id: Bytes(16, b"\x00" * 16)      # 40
    generator: Utf8(24, GENERATOR)           # 64
    directory_offset: U32 = 0                # 68
    total_size: U32 = 0                      # 72
    content_hash: Bytes(32, b"\x00" * 32)    # 104
    header_crc: U32 = 0                      # 108
    reserved1: Bytes(20, b"\x00" * 20)       # 128


class SectionEntry(BinaryStruct, endian="<"):
    """One directory record: 48 bytes."""

    kind: U16 = 0                            # 2
    encoding: U8 = 0                         # 3
    reserved: U8 = 0                         # 4
    name: Utf8(28)                           # 32
    offset: U32 = 0                          # 36
    stored_size: U32 = 0                     # 40
    original_size: U32 = 0                   # 44
    crc32: U32 = 0                           # 48


assert FileHeader.size() == 128, f"header drifted to {FileHeader.size()} bytes"
assert SectionEntry.size() == 48, f"entry drifted to {SectionEntry.size()} bytes"


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------

#: Below this, compression headers cost more than they save.
COMPRESSION_FLOOR = 96


def _compress(payload: bytes, allow_lzma: bool = True) -> tuple[Encoding, bytes]:
    """Try each codec and keep whichever actually won."""
    if len(payload) < COMPRESSION_FLOOR:
        return Encoding.RAW, payload
    best_encoding, best = Encoding.RAW, payload
    candidates = [(Encoding.ZLIB, zlib.compress(payload, 9))]
    if allow_lzma:
        candidates.append((Encoding.LZMA, lzma.compress(payload, preset=6)))
    for encoding, blob in candidates:
        if len(blob) < len(best):
            best_encoding, best = encoding, blob
    return best_encoding, best


def _decompress(encoding: Encoding, blob: bytes, expected: int) -> bytes:
    try:
        if encoding is Encoding.RAW:
            payload = blob
        elif encoding is Encoding.ZLIB:
            payload = zlib.decompress(blob)
        elif encoding is Encoding.LZMA:
            payload = lzma.decompress(blob)
        else:
            raise SdwxError(f"unknown section encoding {encoding!r}")
    except (zlib.error, lzma.LZMAError) as exc:
        raise SdwxError(f"section will not decompress: {exc}") from exc
    if len(payload) != expected:
        raise SdwxError(
            f"section decompressed to {len(payload)} bytes, directory says {expected}")
    return payload


# --------------------------------------------------------------------------
# Dossier
# --------------------------------------------------------------------------

class Dossier:
    """A read/write ``.sdwx`` container."""

    def __init__(self, dossier_id: bytes = b"", flags: Flags = Flags.NONE,
                 created: int = 0) -> None:
        self.dossier_id = (dossier_id or os.urandom(16))[:16].ljust(16, b"\x00")
        self.flags = Flags(flags)
        self.created = created or int(time.time())
        self.generator = GENERATOR
        self._sections: list[tuple[Section | int, str, bytes]] = []

    # ------------------------------------------------------------- writing

    def add(self, kind: "Section | int", name: str, payload: bytes) -> "Dossier":
        if len(name.encode("utf-8")) > 28:
            raise SdwxError(f"section name {name!r} exceeds 28 bytes")
        if len(self._sections) >= 0xFFFF:
            raise SdwxError("a dossier holds at most 65535 sections")
        self._sections.append((kind, name, bytes(payload)))
        return self

    def add_json(self, kind: "Section | int", name: str, obj: Any) -> "Dossier":
        blob = json.dumps(obj, indent=None, separators=(",", ":"),
                          sort_keys=True, default=_json_default).encode("utf-8")
        return self.add(kind, name, blob)

    def add_text(self, kind: "Section | int", name: str, text: str) -> "Dossier":
        return self.add(kind, name, text.encode("utf-8"))

    def to_bytes(self) -> bytes:
        out = bytearray(b"\x00" * FileHeader.size())
        entries: list[SectionEntry] = []
        hasher = hashlib.sha256()

        for kind, name, payload in self._sections:
            encoding, blob = _compress(payload)
            entries.append(SectionEntry(
                kind=int(kind), encoding=int(encoding), name=name,
                offset=len(out), stored_size=len(blob),
                original_size=len(payload), crc32=zlib.crc32(payload) & 0xFFFFFFFF))
            out.extend(blob)
            hasher.update(payload)

        directory_offset = len(out)
        for entry in entries:
            out.extend(entry.pack())
        out.extend(TRAILER)

        header = FileHeader(
            flags=int(self.flags), section_count=len(entries),
            created=self.created, dossier_id=self.dossier_id,
            generator=self.generator, directory_offset=directory_offset,
            total_size=len(out), content_hash=hasher.digest())
        packed = bytearray(header.pack())
        # The CRC covers the header with its own CRC field zeroed, which is
        # the only way a self-describing checksum can work.
        crc_at = FileHeader.offset_of("header_crc")
        header.header_crc = zlib.crc32(bytes(packed)) & 0xFFFFFFFF
        packed[crc_at:crc_at + 4] = struct.pack("<I", header.header_crc)
        out[:FileHeader.size()] = packed
        return bytes(out)

    def write(self, path) -> int:
        blob = self.to_bytes()
        with open(path, "wb") as handle:
            handle.write(blob)
        return len(blob)

    # ------------------------------------------------------------- reading

    @classmethod
    def from_bytes(cls, data: bytes, *, strict: bool = True) -> "Dossier":
        if len(data) < FileHeader.size() + len(TRAILER):
            raise SdwxError(f"file is {len(data)} bytes; too short to be a dossier")
        if data[:len(MAGIC)] != MAGIC:
            raise SdwxError(f"bad magic {data[:len(MAGIC)]!r}, expected {MAGIC!r}")
        try:
            header = FileHeader.unpack(data)
        except StructError as exc:
            raise SdwxError(f"unreadable header: {exc}") from exc

        if header.version_major != FORMAT_MAJOR:
            raise SdwxError(
                f"dossier format v{header.version_major}.{header.version_minor} "
                f"is not readable by this build (v{FORMAT_MAJOR}.{FORMAT_MINOR})")

        probe = bytearray(data[:FileHeader.size()])
        crc_at = FileHeader.offset_of("header_crc")
        probe[crc_at:crc_at + 4] = b"\x00\x00\x00\x00"
        if strict and zlib.crc32(bytes(probe)) & 0xFFFFFFFF != header.header_crc:
            raise SdwxError("header CRC mismatch; the file has been edited or damaged")

        if strict and header.total_size != len(data):
            raise SdwxError(
                f"header claims {header.total_size} bytes, file holds {len(data)}")
        if data[-len(TRAILER):] != TRAILER:
            raise SdwxError("missing trailer; the file is truncated")

        dossier = cls(dossier_id=header.dossier_id, flags=Flags(header.flags),
                      created=header.created)
        dossier.generator = header.generator

        directory_end = header.directory_offset + header.section_count * SectionEntry.size()
        if directory_end + len(TRAILER) > len(data):
            raise SdwxError("section directory runs past the end of the file")

        hasher = hashlib.sha256()
        for entry in SectionEntry.unpack_many(
            data, header.section_count, header.directory_offset
        ):
            end = entry.offset + entry.stored_size
            if not FileHeader.size() <= entry.offset <= end <= header.directory_offset:
                raise SdwxError(
                    f"section {entry.name!r} spans {entry.offset}..{end}, "
                    f"which is outside the payload region")
            payload = _decompress(Encoding(entry.encoding),
                                  data[entry.offset:end], entry.original_size)
            actual = zlib.crc32(payload) & 0xFFFFFFFF
            if actual != entry.crc32:
                raise SdwxError(
                    f"section {entry.name!r} CRC {actual:#010x} != "
                    f"recorded {entry.crc32:#010x}")
            hasher.update(payload)
            kind = Section(entry.kind) if entry.kind in Section._value2member_map_ \
                else entry.kind
            dossier._sections.append((kind, entry.name, payload))

        if strict and hasher.digest() != header.content_hash:
            raise SdwxError("content hash mismatch; sections do not match the header")
        return dossier

    @classmethod
    def read(cls, path, *, strict: bool = True) -> "Dossier":
        with open(path, "rb") as handle:
            return cls.from_bytes(handle.read(), strict=strict)

    # ------------------------------------------------------------- access

    def __len__(self) -> int:
        return len(self._sections)

    def __iter__(self) -> Iterator[tuple]:
        return iter(self._sections)

    def find(self, kind: "Section | int" = None, name: str = None) -> list[bytes]:
        return [payload for k, n, payload in self._sections
                if (kind is None or int(k) == int(kind)) and (name is None or n == name)]

    def one(self, kind: "Section | int" = None, name: str = None) -> bytes:
        matches = self.find(kind, name)
        if len(matches) != 1:
            raise SdwxError(
                f"expected exactly one section matching "
                f"kind={kind!r} name={name!r}, found {len(matches)}")
        return matches[0]

    def json(self, kind: "Section | int" = None, name: str = None) -> Any:
        return json.loads(self.one(kind, name).decode("utf-8"))

    def describe(self) -> list[str]:
        """A directory listing, for ``dosl inspect``."""
        blob = self.to_bytes()
        lines = [
            f"dossier   {self.dossier_id.hex()}",
            f"format    v{FORMAT_MAJOR}.{FORMAT_MINOR}   generator {self.generator}",
            f"created   {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created))}",
            f"flags     {self.flags!r}",
            f"size      {len(blob)} bytes in {len(self._sections)} section(s)",
            "",
            f"{'kind':<14} {'name':<24} {'enc':<5} {'stored':>8} {'raw':>8} {'crc32':>10}",
            f"{'-' * 14} {'-' * 24} {'-' * 5} {'-' * 8} {'-' * 8} {'-' * 10}",
        ]
        header = FileHeader.unpack(blob)
        for entry in SectionEntry.unpack_many(blob, header.section_count,
                                              header.directory_offset):
            kind_name = (Section(entry.kind).name
                         if entry.kind in Section._value2member_map_
                         else f"?{entry.kind}")
            lines.append(
                f"{kind_name:<14} {entry.name:<24} "
                f"{Encoding(entry.encoding).name:<5} {entry.stored_size:>8} "
                f"{entry.original_size:>8} {entry.crc32:>#10x}")
        return lines


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, enum.Enum):
        return obj.name
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    raise TypeError(f"{type(obj).__name__} is not JSON-serialisable")


# --------------------------------------------------------------------------
# PolicyCode marshalling
#
# Compiled policies go into the dossier as a compact tagged blob rather than
# as JSON, partly for size and mostly because a policy that round-trips
# through JSON quietly turns its integers into floats.
# --------------------------------------------------------------------------

PCOD_MAGIC = b"PCOD"
PCOD_VERSION = 1

_TAG_NONE, _TAG_TRUE, _TAG_FALSE = 0, 1, 2
_TAG_INT, _TAG_FLOAT, _TAG_STR, _TAG_LIST = 3, 4, 5, 6


def _put_str(out: bytearray, text: str) -> None:
    blob = text.encode("utf-8")
    if len(blob) > 0xFFFF:
        raise SdwxError(f"string of {len(blob)} bytes exceeds the 65535 limit")
    out.extend(struct.pack("<H", len(blob)))
    out.extend(blob)


def _get_str(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<H", data, pos)
    pos += 2
    return data[pos:pos + length].decode("utf-8"), pos + length


def _put_value(out: bytearray, value: Any) -> None:
    if value is None:
        out.append(_TAG_NONE)
    elif value is True:
        out.append(_TAG_TRUE)
    elif value is False:
        out.append(_TAG_FALSE)
    elif isinstance(value, int):
        out.append(_TAG_INT)
        out.extend(struct.pack("<q", value))
    elif isinstance(value, float):
        out.append(_TAG_FLOAT)
        out.extend(struct.pack("<d", value))
    elif isinstance(value, str):
        out.append(_TAG_STR)
        _put_str(out, value)
    elif isinstance(value, (list, tuple)):
        out.append(_TAG_LIST)
        out.extend(struct.pack("<H", len(value)))
        for item in value:
            _put_value(out, item)
    else:
        raise SdwxError(f"cannot marshal {type(value).__name__} into a policy constant")


def _get_value(data: bytes, pos: int) -> tuple[Any, int]:
    tag, pos = data[pos], pos + 1
    if tag == _TAG_NONE:
        return None, pos
    if tag == _TAG_TRUE:
        return True, pos
    if tag == _TAG_FALSE:
        return False, pos
    if tag == _TAG_INT:
        return struct.unpack_from("<q", data, pos)[0], pos + 8
    if tag == _TAG_FLOAT:
        return struct.unpack_from("<d", data, pos)[0], pos + 8
    if tag == _TAG_STR:
        return _get_str(data, pos)
    if tag == _TAG_LIST:
        (count,) = struct.unpack_from("<H", data, pos)
        pos += 2
        items = []
        for _ in range(count):
            item, pos = _get_value(data, pos)
            items.append(item)
        return items, pos
    raise SdwxError(f"unknown constant tag {tag}")


def marshal_policy(code) -> bytes:
    """Serialise a :class:`~dosl.kernel.compiler.PolicyCode`."""
    out = bytearray(PCOD_MAGIC)
    out.append(PCOD_VERSION)
    _put_str(out, code.name)
    out.extend(struct.pack("<H", code.version))
    _put_str(out, code.regulation)
    _put_str(out, code.origin)
    _put_str(out, code.source_digest)

    for table in (code.names, code.intrinsics):
        out.extend(struct.pack("<H", len(table)))
        for item in table:
            _put_str(out, item)

    out.extend(struct.pack("<H", len(code.constants)))
    for constant in code.constants:
        _put_value(out, constant)

    out.extend(struct.pack("<I", len(code.code)))
    out.extend(code.code)

    out.extend(struct.pack("<H", len(code.line_table)))
    for offset, line in sorted(code.line_table.items()):
        out.extend(struct.pack("<HH", offset, min(line, 0xFFFF)))
    return bytes(out)


def unmarshal_policy(blob: bytes):
    """Rebuild a :class:`~dosl.kernel.compiler.PolicyCode` from its bytes."""
    from ..kernel.compiler import PolicyCode  # deferred: kernel imports formats

    if blob[:4] != PCOD_MAGIC:
        raise SdwxError(f"not a compiled policy: {blob[:4]!r}")
    if blob[4] != PCOD_VERSION:
        raise SdwxError(f"compiled policy version {blob[4]} is not supported")
    pos = 5
    name, pos = _get_str(blob, pos)
    (version,) = struct.unpack_from("<H", blob, pos)
    pos += 2
    regulation, pos = _get_str(blob, pos)
    origin, pos = _get_str(blob, pos)
    digest, pos = _get_str(blob, pos)

    tables = []
    for _ in range(2):
        (count,) = struct.unpack_from("<H", blob, pos)
        pos += 2
        table = []
        for _ in range(count):
            item, pos = _get_str(blob, pos)
            table.append(item)
        tables.append(table)
    names, intrinsics = tables

    (count,) = struct.unpack_from("<H", blob, pos)
    pos += 2
    constants = []
    for _ in range(count):
        constant, pos = _get_value(blob, pos)
        constants.append(constant)

    (code_len,) = struct.unpack_from("<I", blob, pos)
    pos += 4
    code = blob[pos:pos + code_len]
    pos += code_len

    (line_count,) = struct.unpack_from("<H", blob, pos)
    pos += 2
    line_table = {}
    for _ in range(line_count):
        offset, line = struct.unpack_from("<HH", blob, pos)
        pos += 4
        line_table[offset] = line

    return PolicyCode(
        name=name, version=version, regulation=regulation, origin=origin,
        code=code, constants=constants, names=names, intrinsics=intrinsics,
        source_digest=digest, line_table=line_table)
