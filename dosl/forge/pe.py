"""
dosl.forge.pe -- a minimal PE32+ linker that emits real, loadable Windows DLLs.

There is no C compiler on this machine. There is, however, ``struct``.

This module lays out a complete Portable Executable image by hand:
MS-DOS header, real 16-bit DOS stub, PE signature, COFF file header,
64-bit optional header with all sixteen data directories, a section table,
an export directory with a sorted name table (``GetProcAddress`` binary
searches it, so the sort is not decorative), and a base relocation block
containing two ``IMAGE_REL_BASED_ABSOLUTE`` no-ops so the loader is willing
to rebase an image that needs no rebasing.

The result is a file that ``LoadLibraryW`` accepts and ``ctypes`` can call.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Constants, lifted from winnt.h and then slightly resented.
# --------------------------------------------------------------------------

IMAGE_DOS_SIGNATURE = 0x5A4D  # 'MZ'
IMAGE_NT_SIGNATURE = 0x00004550  # 'PE\0\0'
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_NT_OPTIONAL_HDR64_MAGIC = 0x20B

IMAGE_FILE_EXECUTABLE_IMAGE = 0x0002
IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020
IMAGE_FILE_DLL = 0x2000

IMAGE_SUBSYSTEM_WINDOWS_CUI = 3
IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE = 0x0040
IMAGE_DLLCHARACTERISTICS_NX_COMPAT = 0x0100

IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_CNT_INITIALIZED_DATA = 0x00000040
IMAGE_SCN_MEM_DISCARDABLE = 0x02000000
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000

DIRECTORY_EXPORT = 0
DIRECTORY_BASERELOC = 5

FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000
SIZE_OF_HEADERS = 0x400

#: A timestamp is required. A *meaningful* timestamp is not, and varying it
#: would make builds irreproducible, so the Department has standardised on
#: the moment the sandwich was invented (allegedly, 1762, rendered in epoch
#: seconds as a negative number, which the field cannot hold, so: this).
TIME_DATE_STAMP = 0x17620000


class LinkError(RuntimeError):
    """Raised when an image cannot be laid out."""


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


# --------------------------------------------------------------------------
# DOS stub
# --------------------------------------------------------------------------

_DOS_MESSAGE = b"This DLL requires a 64-bit bureaucracy.\r\n$"

#: Genuine 16-bit real-mode code: print ``$``-terminated string via INT 21h/09h,
#: then exit with code 1 via INT 21h/4Ch. Fourteen bytes, then the message.
_DOS_CODE = bytes([
    0x0E,              # push cs
    0x1F,              # pop  ds
    0xBA, 0x0E, 0x00,  # mov  dx, 0x000E   ; message offset within the segment
    0xB4, 0x09,        # mov  ah, 0x09     ; DOS "print string"
    0xCD, 0x21,        # int  0x21
    0xB8, 0x01, 0x4C,  # mov  ax, 0x4C01   ; DOS "terminate, exit code 1"
    0xCD, 0x21,        # int  0x21
])
assert len(_DOS_CODE) == 0x0E, "the mov dx above hardcodes the message offset"


def _dos_header(e_lfanew: int) -> bytes:
    """The 64-byte IMAGE_DOS_HEADER, as thirty words and one signed long."""
    words = [0] * 30
    words[0] = IMAGE_DOS_SIGNATURE  # e_magic
    words[1] = 0x0090               # e_cblp    bytes on last page
    words[2] = 0x0003               # e_cp      pages in file
    words[4] = 0x0004               # e_cparhdr header size in paragraphs (64B)
    words[6] = 0xFFFF               # e_maxalloc
    words[8] = 0x00B8               # e_sp
    words[12] = 0x0040              # e_lfarlc  relocation table offset
    return struct.pack("<30Hi", *words, e_lfanew)


def _dos_stub() -> bytes:
    body = _DOS_CODE + _DOS_MESSAGE
    header = _dos_header(align_up(64 + len(body), 8))
    padding = b"\x00" * (align_up(64 + len(body), 8) - 64 - len(body))
    return header + body + padding


# --------------------------------------------------------------------------
# Image description
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Export:
    """One exported symbol: a name, a blob of machine code, and a docstring."""

    name: str
    code: bytes
    signature: str = ""
    doc: str = ""
    rva: int = 0  # filled in during layout

    def __post_init__(self) -> None:
        if not self.name.isascii():
            raise LinkError(f"export name {self.name!r} must be ASCII")
        if not self.code:
            raise LinkError(f"export {self.name!r} has no code")


@dataclass(slots=True)
class Section:
    name: bytes
    characteristics: int
    data: bytes
    rva: int = 0
    raw_ptr: int = 0

    @property
    def raw_size(self) -> int:
        return align_up(len(self.data), FILE_ALIGNMENT)

    @property
    def virtual_size(self) -> int:
        return len(self.data)

    def header(self) -> bytes:
        return struct.pack(
            "<8sIIIIIIHHI",
            self.name.ljust(8, b"\x00"),
            self.virtual_size,
            self.rva,
            self.raw_size,
            self.raw_ptr,
            0, 0, 0, 0,
            self.characteristics,
        )


class DllImage:
    """Accumulates exports, then lays the whole PE file out in one pass."""

    def __init__(self, name: str, *, image_base: int = 0x0000_0001_8000_0000) -> None:
        self.name = name
        self.image_base = image_base
        self.entry_code = b""
        self.exports: list[Export] = []

    # ------------------------------------------------------------- building

    def set_entry(self, code: bytes) -> None:
        """Install ``DllMain``. Must return non-zero or the load is rejected."""
        self.entry_code = code

    def add_export(self, export: Export) -> Export:
        if any(e.name == export.name for e in self.exports):
            raise LinkError(f"duplicate export {export.name!r}")
        self.exports.append(export)
        return export

    # --------------------------------------------------------------- layout

    def _build_text(self) -> bytes:
        """Concatenate DllMain and every export, 16-byte aligned, INT3 padded."""
        if not self.entry_code:
            raise LinkError("no DllMain installed")
        blob = bytearray(self.entry_code)
        for export in self.exports:
            while len(blob) % 16:
                blob.append(0xCC)
            export.rva = SECTION_ALIGNMENT + len(blob)
            blob.extend(export.code)
        while len(blob) % 16:
            blob.append(0xCC)
        return bytes(blob)

    def _build_edata(self, rva: int) -> bytes:
        """The export directory. Names *must* end up sorted for the loader."""
        ordered = sorted(self.exports, key=lambda e: e.name.encode("ascii"))
        count = len(ordered)

        dir_size = 40
        addrs_off = dir_size
        names_off = addrs_off + 4 * count
        ords_off = names_off + 4 * count
        strings_off = ords_off + 2 * count

        strings = bytearray()
        dll_name_rva = rva + strings_off + len(strings)
        strings.extend(self.name.encode("ascii") + b"\x00")

        name_rvas: list[int] = []
        for export in ordered:
            name_rvas.append(rva + strings_off + len(strings))
            strings.extend(export.name.encode("ascii") + b"\x00")

        blob = bytearray()
        blob.extend(struct.pack(
            "<IIHHIIIIIII",
            0,                    # Characteristics (reserved, must be zero)
            TIME_DATE_STAMP,
            0, 0,                 # Major/MinorVersion
            dll_name_rva,
            1,                    # Base -- first ordinal
            count,                # NumberOfFunctions
            count,                # NumberOfNames
            rva + addrs_off,
            rva + names_off,
            rva + ords_off,
        ))
        # The address table is indexed by (ordinal - Base); we hand out
        # ordinals in sorted-name order so the two tables stay parallel.
        for export in ordered:
            blob.extend(struct.pack("<I", export.rva))
        for name_rva in name_rvas:
            blob.extend(struct.pack("<I", name_rva))
        for index in range(count):
            blob.extend(struct.pack("<H", index))
        blob.extend(strings)
        assert len(blob) == strings_off + len(strings)
        return bytes(blob)

    @staticmethod
    def _build_reloc(page_rva: int) -> bytes:
        """One block, two ABSOLUTE entries.

        ``IMAGE_REL_BASED_ABSOLUTE`` (type 0) means "skip me"; it exists
        precisely so blocks can be padded to a 4-byte boundary. Two of them
        make a well-formed, completely inert relocation table, which is what
        lets us set ``DYNAMIC_BASE`` on position-independent code.
        """
        return struct.pack("<IIHH", page_rva, 12, 0, 0)

    def build(self) -> bytes:
        text = self._build_text()
        text_rva = SECTION_ALIGNMENT

        edata_rva = align_up(text_rva + len(text), SECTION_ALIGNMENT)
        edata = self._build_edata(edata_rva)

        reloc_rva = align_up(edata_rva + len(edata), SECTION_ALIGNMENT)
        reloc = self._build_reloc(text_rva)

        sections = [
            Section(b".text", IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE
                    | IMAGE_SCN_MEM_READ, text, text_rva),
            Section(b".rdata", IMAGE_SCN_CNT_INITIALIZED_DATA
                    | IMAGE_SCN_MEM_READ, edata, edata_rva),
            Section(b".reloc", IMAGE_SCN_CNT_INITIALIZED_DATA
                    | IMAGE_SCN_MEM_DISCARDABLE | IMAGE_SCN_MEM_READ,
                    reloc, reloc_rva),
        ]

        cursor = SIZE_OF_HEADERS
        for section in sections:
            section.raw_ptr = cursor
            cursor += section.raw_size

        size_of_image = align_up(reloc_rva + len(reloc), SECTION_ALIGNMENT)

        directories = [(0, 0)] * 16
        directories[DIRECTORY_EXPORT] = (edata_rva, len(edata))
        directories[DIRECTORY_BASERELOC] = (reloc_rva, len(reloc))

        file_header = struct.pack(
            "<HHIIIHH",
            IMAGE_FILE_MACHINE_AMD64,
            len(sections),
            TIME_DATE_STAMP,
            0, 0,
            240,  # SizeOfOptionalHeader
            IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_LARGE_ADDRESS_AWARE
            | IMAGE_FILE_DLL,
        )

        optional_header = struct.pack(
            "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
            IMAGE_NT_OPTIONAL_HDR64_MAGIC,
            14, 0,                        # linker version (a polite fiction)
            len(text),                    # SizeOfCode
            len(edata) + len(reloc),      # SizeOfInitializedData
            0,                            # SizeOfUninitializedData
            text_rva,                     # AddressOfEntryPoint -> DllMain
            text_rva,                     # BaseOfCode
            self.image_base,
            SECTION_ALIGNMENT,
            FILE_ALIGNMENT,
            6, 0,                         # Major/MinorOperatingSystemVersion
            0, 0,                         # Major/MinorImageVersion
            6, 0,                         # Major/MinorSubsystemVersion
            0,                            # Win32VersionValue
            size_of_image,
            SIZE_OF_HEADERS,
            0,                            # CheckSum (only kernel drivers care)
            IMAGE_SUBSYSTEM_WINDOWS_CUI,
            IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE
            | IMAGE_DLLCHARACTERISTICS_NX_COMPAT,
            0x100000, 0x1000,             # stack reserve / commit
            0x100000, 0x1000,             # heap reserve / commit
            0,                            # LoaderFlags
            16,                           # NumberOfRvaAndSizes
        )
        optional_header += b"".join(struct.pack("<II", a, s) for a, s in directories)
        if len(optional_header) != 240:
            raise LinkError(f"optional header is {len(optional_header)} bytes, not 240")

        image = bytearray()
        image.extend(_dos_stub())
        image.extend(struct.pack("<I", IMAGE_NT_SIGNATURE))
        image.extend(file_header)
        image.extend(optional_header)
        for section in sections:
            image.extend(section.header())
        if len(image) > SIZE_OF_HEADERS:
            raise LinkError("headers overflowed SizeOfHeaders")
        image.extend(b"\x00" * (SIZE_OF_HEADERS - len(image)))

        for section in sections:
            image.extend(section.data)
            image.extend(b"\x00" * (section.raw_size - len(section.data)))

        return bytes(image)


# --------------------------------------------------------------------------
# A small reader, so the linker can be checked against something that is not
# also the linker.
# --------------------------------------------------------------------------

def inspect(image: bytes) -> dict:
    """Parse a PE32+ image back into a dictionary. Used by the self-test."""
    if struct.unpack_from("<H", image, 0)[0] != IMAGE_DOS_SIGNATURE:
        raise LinkError("not an MZ image")
    e_lfanew = struct.unpack_from("<i", image, 0x3C)[0]
    if struct.unpack_from("<I", image, e_lfanew)[0] != IMAGE_NT_SIGNATURE:
        raise LinkError("not a PE image")

    machine, nsections, _, _, _, opt_size, chars = struct.unpack_from(
        "<HHIIIHH", image, e_lfanew + 4)
    opt_off = e_lfanew + 24
    magic = struct.unpack_from("<H", image, opt_off)[0]
    entry = struct.unpack_from("<I", image, opt_off + 16)[0]
    image_base = struct.unpack_from("<Q", image, opt_off + 24)[0]
    size_of_image = struct.unpack_from("<I", image, opt_off + 56)[0]
    ndirs = struct.unpack_from("<I", image, opt_off + 108)[0]
    dirs = [struct.unpack_from("<II", image, opt_off + 112 + 8 * i)
            for i in range(ndirs)]

    sect_off = opt_off + opt_size
    sections = []
    for i in range(nsections):
        name, vsize, rva, rsize, rptr, *_rest = struct.unpack_from(
            "<8sIIIIIIHHI", image, sect_off + 40 * i)
        sections.append({
            "name": name.rstrip(b"\x00").decode(),
            "rva": rva, "virtual_size": vsize,
            "raw_ptr": rptr, "raw_size": rsize,
        })

    def rva_to_off(rva: int) -> int:
        for s in sections:
            if s["rva"] <= rva < s["rva"] + max(s["virtual_size"], s["raw_size"]):
                return s["raw_ptr"] + rva - s["rva"]
        raise LinkError(f"rva {rva:#x} is not inside any section")

    exports: list[tuple[str, int]] = []
    edir_rva, edir_size = dirs[DIRECTORY_EXPORT]
    if edir_rva:
        off = rva_to_off(edir_rva)
        (_c, _t, _mj, _mn, _name_rva, base, nfuncs, nnames,
         funcs_rva, names_rva, ords_rva) = struct.unpack_from("<IIHHIIIIIII", image, off)
        for i in range(nnames):
            nrva = struct.unpack_from("<I", image, rva_to_off(names_rva) + 4 * i)[0]
            noff = rva_to_off(nrva)
            end = image.index(b"\x00", noff)
            ordinal = struct.unpack_from("<H", image, rva_to_off(ords_rva) + 2 * i)[0]
            faddr = struct.unpack_from("<I", image, rva_to_off(funcs_rva) + 4 * ordinal)[0]
            exports.append((image[noff:end].decode(), faddr))

    return {
        "machine": machine, "magic": magic, "characteristics": chars,
        "entry_rva": entry, "image_base": image_base,
        "size_of_image": size_of_image, "size_on_disk": len(image),
        "sections": sections, "exports": exports,
        "export_dir_size": edir_size,
    }
