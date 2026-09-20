"""
dosl.kernel.compiler -- lowers a ``.bureau`` AST into policy bytecode.

Single pass, with backpatching: every jump is emitted with a placeholder
operand and a note of where the placeholder lives, then fixed up once the
target offset is known. Constants are pooled and deduplicated; names and
intrinsics get their own tables so the bytecode never contains a string.

The output is a :class:`PolicyCode`, which is what gets written into the
``.sdwx`` container and what the VM executes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import syntax
from .intrinsics import TABLE as INTRINSIC_TABLE
from .opcodes import BINARY_OPS, Kind, Op, decode, disassemble, encode

#: Placeholder written into a jump operand until the target is known. It is
#: deliberately the maximum 16-bit value: if backpatching is ever skipped the
#: VM jumps off the end of the program and says so, rather than to offset 0
#: and looping silently forever.
UNPATCHED = 0xFFFF

_FINDING_KIND = {
    "DENY": Kind.DENIAL,
    "WARN": Kind.OBJECTION,
    "COMMEND": Kind.COMMENDATION,
}


class PolicyCompileError(SyntaxError):
    """Raised for problems a parser cannot see, like a misspelled intrinsic."""


@dataclass(slots=True)
class PolicyCode:
    """A compiled policy: everything the VM needs and nothing it does not."""

    name: str
    version: int
    regulation: str
    origin: str
    code: bytes
    constants: list
    names: list[str]
    intrinsics: list[str]
    source_digest: str
    line_table: dict[int, int] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.code)

    def line_for(self, offset: int) -> int:
        """The source line responsible for the instruction at ``offset``.

        The line table is sparse -- one entry per statement -- so this is the
        nearest preceding entry, which is the usual bytecode-to-source trick.
        """
        best_start, best_line = -1, 0
        for start, line in self.line_table.items():
            if best_start <= start <= offset:
                best_start, best_line = start, line
        return best_line

    def listing(self) -> list[str]:
        """Human-readable disassembly, used by ``dosl audit``."""
        header = [
            f"; policy   {self.name} v{self.version}",
            f"; origin   {self.origin}",
            f"; sha256   {self.source_digest}",
            f"; reg.     {self.regulation or '(none cited)'}",
            f"; {len(self.code)} bytes, {sum(1 for _ in decode(self.code))} instructions",
            "",
        ]
        return header + disassemble(self.code, self.constants, self.names,
                                    self.intrinsics)


class Compiler:
    """Walks the AST once, emitting bytecode as it goes."""

    def __init__(self, policy: syntax.Policy, source: str = "") -> None:
        self.policy = policy
        self.source = source
        self.code = bytearray()
        self.constants: list = []
        self._const_index: dict[tuple, int] = {}
        self.names: list[str] = []
        self._name_index: dict[str, int] = {}
        self.intrinsics: list[str] = []
        self._intrinsic_index: dict[str, int] = {}
        self.line_table: dict[int, int] = {}

    # ------------------------------------------------------------ emission

    def emit(self, op: Op, *args: int) -> int:
        offset = len(self.code)
        self.code.extend(encode(op, *args))
        return offset

    def emit_jump(self, op: Op) -> int:
        """Emit a jump to nowhere; returns the operand's byte offset."""
        self.emit(op, UNPATCHED)
        return len(self.code) - 2

    def patch(self, operand_offset: int, target: int | None = None) -> None:
        if target is None:
            target = len(self.code)
        if target > 0xFFFF:
            raise PolicyCompileError(
                f"policy '{self.policy.name}' exceeds the 65535-byte bytecode limit"
            )
        self.code[operand_offset:operand_offset + 2] = target.to_bytes(2, "little")

    # -------------------------------------------------------------- tables

    def const(self, value: object) -> int:
        # Key on the type too: in Python ``1 == True`` and ``1 == 1.0``, but a
        # policy that says TRUE should not disassemble as 1.
        key = (type(value).__name__, value if not isinstance(value, list)
               else tuple(map(repr, value)))
        if key not in self._const_index:
            self._const_index[key] = len(self.constants)
            self.constants.append(value)
        return self._const_index[key]

    def name(self, ident: str) -> int:
        if ident not in self._name_index:
            self._name_index[ident] = len(self.names)
            self.names.append(ident)
        return self._name_index[ident]

    def intrinsic(self, ident: str, argc: int, line: int) -> int:
        spec = INTRINSIC_TABLE.get(ident)
        if spec is None:
            close = [n for n in INTRINSIC_TABLE if n.startswith(ident[:2])]
            hint = f" Did you mean {' or '.join(sorted(close))}?" if close else ""
            raise PolicyCompileError(
                f"{self.policy.origin}:{line}: no such function {ident!r}.{hint}")
        problem = spec.check_arity(argc)
        if problem:
            raise PolicyCompileError(f"{self.policy.origin}:{line}: {problem}")
        if ident not in self._intrinsic_index:
            self._intrinsic_index[ident] = len(self.intrinsics)
            self.intrinsics.append(ident)
        return self._intrinsic_index[ident]

    # --------------------------------------------------------------- drive

    def compile(self) -> PolicyCode:
        if self.policy.regulation:
            self.emit(Op.CONST, self.const(self.policy.regulation))
            self.emit(Op.CITE)
        for statement in self.policy.body:
            self.statement(statement)
        self.emit(Op.HALT)
        return PolicyCode(
            name=self.policy.name,
            version=self.policy.version,
            regulation=self.policy.regulation,
            origin=self.policy.origin,
            code=bytes(self.code),
            constants=self.constants,
            names=self.names,
            intrinsics=self.intrinsics,
            source_digest=hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
            line_table=self.line_table,
        )

    # ---------------------------------------------------------- statements

    def statement(self, node: syntax.Node) -> None:
        self.line_table[len(self.code)] = node.line
        handler = getattr(self, f"_s_{type(node).__name__.lower()}", None)
        if handler is None:
            raise PolicyCompileError(f"cannot compile {type(node).__name__}")
        handler(node)

    def _s_require(self, node: syntax.Require) -> None:
        self.expression(node.condition)
        skip = self.emit_jump(Op.JUMP_IF_TRUE)
        self.emit(Op.CONST, self.const(node.message))
        self.emit(Op.FINDING, node.severity, int(_FINDING_KIND[node.kind]))
        self.patch(skip)

    def _s_when(self, node: syntax.When) -> None:
        self.expression(node.condition)
        to_else = self.emit_jump(Op.JUMP_IF_FALSE)
        for statement in node.then:
            self.statement(statement)
        if node.otherwise:
            to_end = self.emit_jump(Op.JUMP)
            self.patch(to_else)
            for statement in node.otherwise:
                self.statement(statement)
            self.patch(to_end)
        else:
            self.patch(to_else)

    def _s_adjust(self, node: syntax.Adjust) -> None:
        self.expression(node.amount)
        self.emit(Op.CONST, self.const(node.message))
        self.emit(Op.PENALIZE if node.direction == "PENALIZE" else Op.AWARD)

    def _s_assess(self, node: syntax.Assess) -> None:
        self.expression(node.expr)
        self.emit(Op.ASSESS)

    def _s_let(self, node: syntax.Let) -> None:
        self.expression(node.expr)
        self.emit(Op.STORE, self.name(node.target))

    def _s_note(self, node: syntax.Note) -> None:
        self.emit(Op.CONST, self.const(node.message))
        self.emit(Op.NOTE)

    def _s_cite(self, node: syntax.Cite) -> None:
        self.emit(Op.CONST, self.const(node.regulation))
        self.emit(Op.CITE)

    # --------------------------------------------------------- expressions

    def expression(self, node: syntax.Node) -> None:
        handler = getattr(self, f"_e_{type(node).__name__.lower()}", None)
        if handler is None:
            raise PolicyCompileError(f"cannot compile expression {type(node).__name__}")
        handler(node)

    def _e_literal(self, node: syntax.Literal) -> None:
        self.emit(Op.CONST, self.const(node.value))

    def _e_name(self, node: syntax.Name) -> None:
        self.emit(Op.LOAD, self.name(node.ident))

    def _e_listlit(self, node: syntax.ListLit) -> None:
        for item in node.items:
            self.expression(item)
        self.emit(Op.BUILD_LIST, len(node.items))

    def _e_unary(self, node: syntax.Unary) -> None:
        self.expression(node.operand)
        self.emit(Op.NEG if node.op == "-" else Op.NOT)

    def _e_binary(self, node: syntax.Binary) -> None:
        self.expression(node.left)
        self.expression(node.right)
        self.emit(BINARY_OPS[node.op])

    def _e_logical(self, node: syntax.Logical) -> None:
        """Short-circuit AND/OR.

        The left operand is left on the stack and *peeked* at: if it already
        decides the result we jump to the end and it becomes the value of the
        whole expression, exactly like Python's ``and``/``or``. Otherwise it
        is discarded and the right operand's value stands.
        """
        self.expression(node.left)
        bail = self.emit_jump(Op.PEEK_FALSE if node.op == "AND" else Op.PEEK_TRUE)
        self.emit(Op.POP)
        self.expression(node.right)
        self.patch(bail)

    def _e_call(self, node: syntax.Call) -> None:
        for argument in node.args:
            self.expression(argument)
        index = self.intrinsic(node.func, len(node.args), node.line)
        self.emit(Op.CALL, index, len(node.args))


def compile_source(source: str, origin: str = "<policy>") -> PolicyCode:
    """Parse and compile ``source`` in one step."""
    return Compiler(syntax.parse(source, origin), source).compile()
