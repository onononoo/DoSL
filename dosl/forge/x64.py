"""
dosl.forge.x64 -- a hand-rolled x86-64 machine code encoder.

The Department of Sandwich Legitimacy requires a native entropy engine.
The Department was not issued a C compiler. The Department has therefore
elected to encode x86-64 instructions by hand, which is fine, and normal.

Only the instruction subset actually needed by the entropy kernels is
implemented. Everything else raises :class:`EncodingError`, loudly.

Calling convention throughout is the Microsoft x64 ABI:

    argument 1 -> RCX      argument 3 -> R8
    argument 2 -> RDX      argument 4 -> R9
    return     -> RAX      volatile   -> RAX RCX RDX R8 R9 R10 R11

All emitted code is position-independent: no absolute addresses are ever
materialised, so the image needs no meaningful relocations.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass, field
from typing import Iterator


class EncodingError(ValueError):
    """Raised when an instruction cannot be encoded as requested."""


class Reg(enum.IntEnum):
    """The sixteen general-purpose 64-bit registers, in encoding order."""

    RAX = 0
    RCX = 1
    RDX = 2
    RBX = 3
    RSP = 4
    RBP = 5
    RSI = 6
    RDI = 7
    R8 = 8
    R9 = 9
    R10 = 10
    R11 = 11
    R12 = 12
    R13 = 13
    R14 = 14
    R15 = 15

    @property
    def low3(self) -> int:
        """The low three bits that land in ModR/M."""
        return self.value & 0b111

    @property
    def ext(self) -> int:
        """The high bit that must travel in a REX prefix."""
        return (self.value >> 3) & 1


# Re-export the registers as module-level names so kernel source reads like asm.
RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = (Reg(i) for i in range(8))
R8, R9, R10, R11, R12, R13, R14, R15 = (Reg(i) for i in range(8, 16))


class Cond(enum.Enum):
    """Condition codes for ``Jcc``, keyed by their opcode nibble."""

    O = 0x0
    NO = 0x1
    B = 0x2
    AE = 0x3
    E = 0x4
    NE = 0x5
    BE = 0x6
    A = 0x7
    S = 0x8
    NS = 0x9
    P = 0xA
    NP = 0xB
    L = 0xC
    GE = 0xD
    LE = 0xE
    G = 0xF


#: ALU operations that share the classic ``op r/m, r`` encoding shape.
#: value is the base opcode for the ``/r`` (register-direction) form.
_ALU_RM_R = {
    "add": 0x01,
    "or": 0x09,
    "and": 0x21,
    "sub": 0x29,
    "xor": 0x31,
    "cmp": 0x39,
    "test": 0x85,
    "mov": 0x89,
}

#: Shift/rotate group-2 operations, keyed by their ``/digit`` ModR/M extension.
_SHIFT_EXT = {"rol": 0, "ror": 1, "shl": 4, "shr": 5, "sar": 7}

#: Group-3/5 unary operations, keyed by ``(opcode, /digit)``.
_UNARY = {"not": (0xF7, 2), "neg": (0xF7, 3), "inc": (0xFF, 0), "dec": (0xFF, 1)}

U64 = (1 << 64) - 1
U32 = (1 << 32) - 1


@dataclass(slots=True)
class _Fixup:
    """A rel32 displacement that cannot be computed until labels are known."""

    at: int  # file offset of the 4-byte displacement field
    end: int  # offset of the first byte after the instruction
    label: str


@dataclass(slots=True)
class Assembler:
    """Accumulates encoded bytes plus a label/fixup table.

    Jump targets are always encoded as ``rel32``. Short forms would be
    smaller, but branch relaxation is a whole optimisation pass and the
    Department's budget for optimisation passes was reallocated to signage.
    """

    buf: bytearray = field(default_factory=bytearray)
    labels: dict[str, int] = field(default_factory=dict)
    fixups: list[_Fixup] = field(default_factory=list)

    # ---------------------------------------------------------------- utils

    def __len__(self) -> int:
        return len(self.buf)

    def _emit(self, *chunks: bytes | int) -> None:
        for chunk in chunks:
            if isinstance(chunk, int):
                self.buf.append(chunk & 0xFF)
            else:
                self.buf.extend(chunk)

    @staticmethod
    def _rex(w: bool, reg: Reg | int, rm: Reg | int, *, force: bool = False) -> bytes:
        """Build a REX prefix, omitting it entirely when it encodes nothing."""
        r = reg.ext if isinstance(reg, Reg) else (reg >> 3) & 1
        b = rm.ext if isinstance(rm, Reg) else (rm >> 3) & 1
        value = 0x40 | (0x08 if w else 0) | (r << 2) | b
        return bytes([value]) if (force or value != 0x40) else b""

    @staticmethod
    def _modrm(mod: int, reg: Reg | int, rm: Reg | int) -> int:
        r = reg.low3 if isinstance(reg, Reg) else reg & 0b111
        m = rm.low3 if isinstance(rm, Reg) else rm & 0b111
        return (mod & 0b11) << 6 | r << 3 | m

    @staticmethod
    def _check_imm(value: int, bits: int, signed: bool = False) -> int:
        lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed
                  else (0, (1 << bits) - 1))
        if not lo <= value <= hi:
            raise EncodingError(f"immediate {value:#x} does not fit in {bits} bits")
        return value

    # --------------------------------------------------------------- labels

    def label(self, name: str) -> "Assembler":
        """Bind ``name`` to the current offset."""
        if name in self.labels:
            raise EncodingError(f"duplicate label {name!r}")
        self.labels[name] = len(self.buf)
        return self

    def _rel32(self, label: str) -> None:
        self.fixups.append(_Fixup(at=len(self.buf), end=len(self.buf) + 4, label=label))
        self._emit(b"\x00\x00\x00\x00")

    # ------------------------------------------------------------- mov/imm

    def mov(self, dst: Reg, src: Reg, *, w: bool = True) -> "Assembler":
        """``mov dst, src`` (register to register)."""
        self._emit(self._rex(w, src, dst), 0x89, self._modrm(0b11, src, dst))
        return self

    def movabs(self, dst: Reg, imm: int) -> "Assembler":
        """``movabs dst, imm64`` -- the only 64-bit immediate form."""
        self._emit(self._rex(True, 0, dst, force=True), 0xB8 + dst.low3)
        self._emit(struct.pack("<Q", imm & U64))
        return self

    def mov_imm32(self, dst: Reg, imm: int) -> "Assembler":
        """``mov dst32, imm32`` -- implicitly zero-extends to 64 bits."""
        self._emit(self._rex(False, 0, dst), 0xB8 + dst.low3)
        self._emit(struct.pack("<I", imm & U32))
        return self

    def movzx_byte(self, dst: Reg, base: Reg) -> "Assembler":
        """``movzx dst32, byte ptr [base]``."""
        if base.low3 in (0b100, 0b101):
            raise EncodingError(
                f"[{base.name}] needs a SIB byte or displacement; unsupported"
            )
        self._emit(self._rex(False, dst, base), 0x0F, 0xB6,
                   self._modrm(0b00, dst, base))
        return self

    # ------------------------------------------------------------------ alu

    def alu(self, op: str, dst: Reg, src: Reg, *, w: bool = True) -> "Assembler":
        """Any of add/or/and/sub/xor/cmp/test/mov in ``op dst, src`` form."""
        try:
            opcode = _ALU_RM_R[op]
        except KeyError:
            raise EncodingError(f"unknown ALU op {op!r}") from None
        self._emit(self._rex(w, src, dst), opcode, self._modrm(0b11, src, dst))
        return self

    def __getattr__(self, name: str):
        """Expose ``a.add(...)``, ``a.xor(...)``, ``a.shr(...)`` and friends.

        Dispatching through ``__getattr__`` keeps the instruction table as
        *data* rather than two dozen near-identical method bodies. Python only
        calls this for attributes that are not found normally, so the
        hand-written methods above always win.
        """
        if name in _ALU_RM_R:
            return lambda dst, src, w=True: self.alu(name, dst, src, w=w)
        if name in _SHIFT_EXT:
            return lambda dst, imm, w=True: self._shift(name, dst, imm, w)
        if name in _UNARY:
            return lambda dst, w=True: self._unary(name, dst, w)
        raise AttributeError(name)

    def _shift(self, op: str, dst: Reg, imm: int, w: bool) -> "Assembler":
        self._check_imm(imm, 6)
        self._emit(self._rex(w, 0, dst), 0xC1,
                   self._modrm(0b11, _SHIFT_EXT[op], dst), imm)
        return self

    def _unary(self, op: str, dst: Reg, w: bool) -> "Assembler":
        opcode, ext = _UNARY[op]
        self._emit(self._rex(w, 0, dst), opcode, self._modrm(0b11, ext, dst))
        return self

    def add_imm32(self, dst: Reg, imm: int, *, w: bool = True) -> "Assembler":
        """``add dst, imm32`` (sign-extended when ``w``)."""
        self._check_imm(imm, 32, signed=True)
        self._emit(self._rex(w, 0, dst), 0x81, self._modrm(0b11, 0, dst))
        self._emit(struct.pack("<i", imm))
        return self

    def and_imm32(self, dst: Reg, imm: int, *, w: bool = True) -> "Assembler":
        """``and dst, imm32``."""
        self._emit(self._rex(w, 0, dst), 0x81, self._modrm(0b11, 4, dst))
        self._emit(struct.pack("<i", struct.unpack("<i", struct.pack("<I", imm & U32))[0]))
        return self

    def cmp_imm32(self, dst: Reg, imm: int, *, w: bool = True) -> "Assembler":
        """``cmp dst, imm32``."""
        self._emit(self._rex(w, 0, dst), 0x81, self._modrm(0b11, 7, dst))
        self._emit(struct.pack("<i", struct.unpack("<i", struct.pack("<I", imm & U32))[0]))
        return self

    def imul(self, dst: Reg, src: Reg, *, w: bool = True) -> "Assembler":
        """``imul dst, src`` -- two-operand form, low half only."""
        self._emit(self._rex(w, dst, src), 0x0F, 0xAF, self._modrm(0b11, dst, src))
        return self

    def imul_imm32(self, dst: Reg, src: Reg, imm: int, *, w: bool = False) -> "Assembler":
        """``imul dst, src, imm32`` -- three-operand form."""
        self._emit(self._rex(w, dst, src), 0x69, self._modrm(0b11, dst, src))
        self._emit(struct.pack("<I", imm & U32))
        return self

    # -------------------------------------------------------------- control

    def jmp(self, label: str) -> "Assembler":
        self._emit(0xE9)
        self._rel32(label)
        return self

    def jcc(self, cond: Cond, label: str) -> "Assembler":
        self._emit(0x0F, 0x80 | cond.value)
        self._rel32(label)
        return self

    def ret(self) -> "Assembler":
        self._emit(0xC3)
        return self

    def push(self, reg: Reg) -> "Assembler":
        self._emit(self._rex(False, 0, reg), 0x50 + reg.low3)
        return self

    def pop(self, reg: Reg) -> "Assembler":
        self._emit(self._rex(False, 0, reg), 0x58 + reg.low3)
        return self

    def int3(self, count: int = 1) -> "Assembler":
        """Padding that faults loudly rather than sliding into the next function."""
        self._emit(b"\xcc" * count)
        return self

    # --------------------------------------------------------------- finish

    def assemble(self) -> bytes:
        """Resolve every fixup and return the finished byte string."""
        out = bytearray(self.buf)
        for fx in self.fixups:
            if fx.label not in self.labels:
                raise EncodingError(f"unresolved label {fx.label!r}")
            disp = self.labels[fx.label] - fx.end
            self._check_imm(disp, 32, signed=True)
            out[fx.at:fx.at + 4] = struct.pack("<i", disp)
        return bytes(out)

    def hexdump(self, width: int = 16) -> Iterator[str]:
        """Yield annotated hex lines, for when things have gone wrong."""
        code = self.assemble()
        for off in range(0, len(code), width):
            row = code[off:off + width]
            hexa = " ".join(f"{b:02x}" for b in row).ljust(width * 3 - 1)
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            yield f"{off:08x}  {hexa}  |{text}|"
