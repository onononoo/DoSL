"""
dosl.kernel.opcodes -- the instruction set of the Regulatory Evaluation Engine.

A compiled ``.bureau`` policy is a flat byte string. Every instruction is one
opcode byte followed by zero, one or two unsigned 16-bit little-endian
operands, per :data:`ARITY`. Jump targets are absolute byte offsets, because
relative ones would require the compiler to think about branch distance and
the compiler has enough going on.
"""

from __future__ import annotations

import enum
import struct
from typing import Iterator, NamedTuple


class Op(enum.IntEnum):
    """Every opcode the Regulatory Evaluation Engine understands."""

    NOP = 0x00

    # -- stack ------------------------------------------------------------
    CONST = 0x01          # (const_idx)  push constants[idx]
    LOAD = 0x02           # (name_idx)   push environment lookup
    STORE = 0x03          # (name_idx)   pop into the environment
    POP = 0x04
    DUP = 0x05
    SWAP = 0x06
    BUILD_LIST = 0x07     # (count)      pop N, push list

    # -- unary ------------------------------------------------------------
    NEG = 0x10
    NOT = 0x11

    # -- arithmetic -------------------------------------------------------
    ADD = 0x20
    SUB = 0x21
    MUL = 0x22
    DIV = 0x23
    MOD = 0x24

    # -- comparison -------------------------------------------------------
    EQ = 0x30
    NE = 0x31
    LT = 0x32
    LE = 0x33
    GT = 0x34
    GE = 0x35
    IN = 0x36             # a IN b
    CONTAINS = 0x37       # a CONTAINS b
    MATCHES = 0x38        # a MATCHES b  (case-insensitive substring)

    # -- control ----------------------------------------------------------
    JUMP = 0x40           # (target)
    JUMP_IF_FALSE = 0x41  # (target)   pops
    JUMP_IF_TRUE = 0x42   # (target)   pops
    PEEK_FALSE = 0x43     # (target)   jumps if falsey, WITHOUT popping
    PEEK_TRUE = 0x44      # (target)   jumps if truthy, WITHOUT popping

    # -- intrinsics -------------------------------------------------------
    CALL = 0x50           # (intrinsic_idx, argc)

    # -- adjudication -----------------------------------------------------
    FINDING = 0x60        # (severity, kind)  pops message
    PENALIZE = 0x61       # pops message, pops amount
    AWARD = 0x62          # pops message, pops amount
    ASSESS = 0x63         # pops number, sets the baseline score
    NOTE = 0x64           # pops message
    CITE = 0x65           # pops regulation string

    HALT = 0x7F


#: Number of 16-bit operands each opcode carries.
ARITY: dict[Op, int] = {
    Op.CONST: 1, Op.LOAD: 1, Op.STORE: 1, Op.BUILD_LIST: 1,
    Op.JUMP: 1, Op.JUMP_IF_FALSE: 1, Op.JUMP_IF_TRUE: 1,
    Op.PEEK_FALSE: 1, Op.PEEK_TRUE: 1,
    Op.CALL: 2, Op.FINDING: 2,
}


class Kind(enum.IntEnum):
    """What a FINDING means. Severity is separate and runs 0..9."""

    ADVISORY = 0
    OBJECTION = 1
    DENIAL = 2
    COMMENDATION = 3


#: Binary operators, mapped to the opcode that implements them. Used by the
#: compiler and by the disassembler's pretty printer.
BINARY_OPS: dict[str, Op] = {
    "+": Op.ADD, "-": Op.SUB, "*": Op.MUL, "/": Op.DIV, "%": Op.MOD,
    "==": Op.EQ, "!=": Op.NE, "<": Op.LT, "<=": Op.LE, ">": Op.GT, ">=": Op.GE,
    "IN": Op.IN, "CONTAINS": Op.CONTAINS, "MATCHES": Op.MATCHES,
}


class Instruction(NamedTuple):
    """One decoded instruction, plus where it lived."""

    offset: int
    op: Op
    args: tuple[int, ...]

    @property
    def size(self) -> int:
        return 1 + 2 * len(self.args)


class DecodeError(ValueError):
    """Raised when a byte string is not valid policy bytecode."""


def encode(op: Op, *args: int) -> bytes:
    """Encode one instruction, checking its arity and operand range."""
    expected = ARITY.get(op, 0)
    if len(args) != expected:
        raise DecodeError(f"{op.name} takes {expected} operand(s), got {len(args)}")
    for arg in args:
        if not 0 <= arg <= 0xFFFF:
            raise DecodeError(f"{op.name} operand {arg} out of 16-bit range")
    return bytes([op]) + b"".join(struct.pack("<H", a) for a in args)


def decode(code: bytes) -> Iterator[Instruction]:
    """Walk a bytecode string, yielding :class:`Instruction` tuples."""
    pc = 0
    while pc < len(code):
        try:
            op = Op(code[pc])
        except ValueError:
            raise DecodeError(f"illegal opcode {code[pc]:#04x} at {pc}") from None
        count = ARITY.get(op, 0)
        end = pc + 1 + 2 * count
        if end > len(code):
            raise DecodeError(f"truncated {op.name} operand at {pc}")
        args = struct.unpack_from(f"<{count}H", code, pc + 1) if count else ()
        yield Instruction(pc, op, args)
        pc = end


def disassemble(code: bytes, constants: list, names: list[str],
                intrinsics: list[str]) -> list[str]:
    """Render bytecode as annotated assembly, for the audit trail view."""
    targets = {
        ins.args[0] for ins in decode(code)
        if ins.op in (Op.JUMP, Op.JUMP_IF_FALSE, Op.JUMP_IF_TRUE,
                      Op.PEEK_FALSE, Op.PEEK_TRUE)
    }
    lines: list[str] = []
    for ins in decode(code):
        marker = ">" if ins.offset in targets else " "
        raw = " ".join(f"{b:02x}" for b in code[ins.offset:ins.offset + ins.size])
        comment = ""
        if ins.op in (Op.CONST,):
            comment = f"; {constants[ins.args[0]]!r}"
        elif ins.op in (Op.LOAD, Op.STORE):
            comment = f"; {names[ins.args[0]]}"
        elif ins.op is Op.CALL:
            comment = f"; {intrinsics[ins.args[0]]}/{ins.args[1]}"
        elif ins.op is Op.FINDING:
            comment = f"; severity {ins.args[0]}, {Kind(ins.args[1]).name}"
        elif ins.args:
            comment = f"; -> {ins.args[0]:04d}"
        body = f"{ins.op.name} {' '.join(str(a) for a in ins.args)}".rstrip()
        lines.append(f"{marker}{ins.offset:04d}  {raw:<15} {body:<24}{comment}")
    return lines
