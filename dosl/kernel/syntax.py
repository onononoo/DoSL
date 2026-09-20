"""
dosl.kernel.syntax -- lexer, AST and parser for the ``.bureau`` policy language.

``.bureau`` is the Department's regulatory description language. It looks like
this::

    POLICY condiment
    VERSION 3
    REGULATION "DoSL-7.4(a) -- On the Application of Spreadable Matter"

    LET wet = count(condiments)

    REQUIRE wet > 0
        ELSE DENY "A dry sandwich is a cracker with ambitions." SEVERITY 7

    WHEN "ketchup" IN condiments AND bread MATCHES "brioche" THEN
        PENALIZE 25 "Ketchup on brioche is a cry for help."
    OTHERWISE
        AWARD 5 "Condiment selection shows restraint."
    END

    ASSESS 100 - 4 * max(0, wet - 3)

Whitespace and newlines are insignificant; blocks close with ``end``.
Comments run from ``#`` to end of line. Keywords are reserved, and matched
case-insensitively: ``require``, ``REQUIRE`` and ``Require`` are one word.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Sequence


class PolicySyntaxError(SyntaxError):
    """A parse or scan failure, with enough context to point at the problem."""

    def __init__(self, message: str, source: str, line: int, col: int,
                 origin: str = "<policy>") -> None:
        lines = source.splitlines() or [""]
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        caret = " " * (col - 1) + "^"
        super().__init__(
            f"{origin}:{line}:{col}: {message}\n    {text}\n    {caret}"
        )
        self.origin, self.lineno, self.col = origin, line, col


class T(enum.Enum):
    """Token kinds."""

    IDENT = "identifier"
    NUMBER = "number"
    STRING = "string"
    KEYWORD = "keyword"
    OP = "operator"
    EOF = "end of input"


KEYWORDS = frozenset("""
POLICY VERSION REGULATION REQUIRE ELSE DENY WARN COMMEND SEVERITY
WHEN THEN OTHERWISE END PENALIZE AWARD ASSESS LET NOTE CITE
AND OR NOT IN CONTAINS MATCHES TRUE FALSE NIL
""".split())

#: Ordered longest-first so ``<=`` is never scanned as ``<`` then ``=``.
OPERATORS = ("==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/", "%",
             "(", ")", "[", "]", ",", "=")

_SCANNER = re.compile(
    r"""
      (?P<space>[ \t\r\n]+)
    | (?P<comment>\#[^\n]*)
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<op>==|!=|<=|>=|[<>+\-*/%()\[\],=])
    """,
    re.VERBOSE,
)

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}


@dataclass(frozen=True, slots=True)
class Token:
    kind: T
    value: object
    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.kind.value} {self.value!r}"


def tokenize(source: str, origin: str = "<policy>") -> list[Token]:
    """Scan ``source`` into a token list terminated by a single EOF token."""
    tokens: list[Token] = []
    pos, line, line_start = 0, 1, 0
    while pos < len(source):
        match = _SCANNER.match(source, pos)
        if match is None:
            raise PolicySyntaxError(
                f"unexpected character {source[pos]!r}", source,
                line, pos - line_start + 1, origin)
        kind = match.lastgroup
        text = match.group()
        col = pos - line_start + 1

        if kind in ("space", "comment"):
            newlines = text.count("\n")
            if newlines:
                line += newlines
                line_start = pos + text.rfind("\n") + 1
        elif kind == "number":
            value = float(text) if "." in text else int(text)
            tokens.append(Token(T.NUMBER, value, line, col))
        elif kind == "string":
            tokens.append(Token(T.STRING, _unescape(text[1:-1]), line, col))
        elif kind == "word":
            # keywords are recognised case-insensitively and normalised to
            # upper case, so the parser only ever compares one spelling while
            # policy authors may write REQUIRE, require or Require.
            folded = text.upper()
            if folded in KEYWORDS:
                tokens.append(Token(T.KEYWORD, folded, line, col))
            else:
                tokens.append(Token(T.IDENT, text, line, col))
        else:
            tokens.append(Token(T.OP, text, line, col))
        pos = match.end()

    tokens.append(Token(T.EOF, None, line, pos - line_start + 1))
    return tokens


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(_ESCAPES.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Node:
    line: int = field(default=0, kw_only=True)


# -- expressions -----------------------------------------------------------

@dataclass(slots=True)
class Literal(Node):
    value: object


@dataclass(slots=True)
class Name(Node):
    ident: str


@dataclass(slots=True)
class ListLit(Node):
    items: list[Node]


@dataclass(slots=True)
class Unary(Node):
    op: str
    operand: Node


@dataclass(slots=True)
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass(slots=True)
class Logical(Node):
    """``AND``/``OR``. Separate from Binary because it short-circuits."""

    op: str
    left: Node
    right: Node


@dataclass(slots=True)
class Call(Node):
    func: str
    args: list[Node]


# -- statements ------------------------------------------------------------

@dataclass(slots=True)
class Require(Node):
    condition: Node
    kind: str  # DENY | WARN | COMMEND
    message: str
    severity: int


@dataclass(slots=True)
class When(Node):
    condition: Node
    then: list[Node]
    otherwise: list[Node]


@dataclass(slots=True)
class Adjust(Node):
    """``PENALIZE``/``AWARD``: a signed score change with a justification."""

    direction: str
    amount: Node
    message: str


@dataclass(slots=True)
class Assess(Node):
    expr: Node


@dataclass(slots=True)
class Let(Node):
    target: str
    expr: Node


@dataclass(slots=True)
class Note(Node):
    message: str


@dataclass(slots=True)
class Cite(Node):
    regulation: str


@dataclass(slots=True)
class Policy(Node):
    name: str
    version: int
    regulation: str
    body: list[Node]
    origin: str = "<policy>"


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

#: Binding power for each binary operator. Higher binds tighter.
_PRECEDENCE: dict[str, int] = {
    "OR": 1, "AND": 2,
    # 3 is reserved for prefix NOT -- see _NOT_BP
    "==": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
    "IN": 4, "CONTAINS": 4, "MATCHES": 4,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
}

#: ``NOT`` sits below the comparisons and above ``AND``, exactly as Python's
#: ``not`` does, so ``NOT bread MATCHES "bowl"`` negates the comparison rather
#: than negating ``bread`` and then comparing a boolean to a string.
_NOT_BP = 3

_SEVERITY_DEFAULT = {"DENY": 8, "WARN": 3, "COMMEND": 0}


class Parser:
    """Recursive descent for statements, precedence climbing for expressions."""

    def __init__(self, source: str, origin: str = "<policy>") -> None:
        self.source, self.origin = source, origin
        self.tokens = tokenize(source, origin)
        self.pos = 0

    # ------------------------------------------------------------ plumbing

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.tokens[self.pos]
        if token.kind is not T.EOF:
            self.pos += 1
        return token

    def check(self, kind: T, value: object = None) -> bool:
        token = self.current
        return token.kind is kind and (value is None or token.value == value)

    def accept(self, kind: T, value: object = None) -> Token | None:
        return self.advance() if self.check(kind, value) else None

    def expect(self, kind: T, value: object = None) -> Token:
        if not self.check(kind, value):
            wanted = f"{kind.value} {value!r}" if value is not None else kind.value
            self.fail(f"expected {wanted}, found {self.current}")
        return self.advance()

    def fail(self, message: str) -> None:
        token = self.current
        raise PolicySyntaxError(message, self.source, token.line, token.col,
                                self.origin)

    # ------------------------------------------------------------- program

    def parse(self) -> Policy:
        start = self.current.line
        self.expect(T.KEYWORD, "POLICY")
        name = str(self.expect(T.IDENT).value)

        version = 1
        regulation = ""
        if self.accept(T.KEYWORD, "VERSION"):
            token = self.expect(T.NUMBER)
            if not isinstance(token.value, int):
                self.fail("VERSION must be a whole number")
            version = int(token.value)
        if self.accept(T.KEYWORD, "REGULATION"):
            regulation = str(self.expect(T.STRING).value)

        body = self.statements(terminators=())
        if not self.check(T.EOF):
            self.fail(f"unexpected {self.current} after end of policy")
        return Policy(name=name, version=version, regulation=regulation,
                      body=body, origin=self.origin, line=start)

    def statements(self, terminators: Sequence[str]) -> list[Node]:
        out: list[Node] = []
        while not self.check(T.EOF) and not (
            self.current.kind is T.KEYWORD and self.current.value in terminators
        ):
            out.append(self.statement())
        return out

    def statement(self) -> Node:
        token = self.current
        if token.kind is not T.KEYWORD:
            self.fail(f"a statement must start with a keyword, found {token}")
        handler = getattr(self, f"_stmt_{token.value.lower()}", None)
        if handler is None:
            self.fail(f"{token.value!r} cannot start a statement")
        self.advance()
        return handler(token)

    # ---------------------------------------------------------- statements

    def _stmt_require(self, token: Token) -> Node:
        condition = self.expression()
        kind, message, severity = "DENY", "Requirement not met.", None
        if self.accept(T.KEYWORD, "ELSE"):
            kind, message, severity = self.verdict()
        if severity is None:
            severity = _SEVERITY_DEFAULT[kind]
        return Require(condition=condition, kind=kind, message=message,
                       severity=severity, line=token.line)

    def verdict(self) -> tuple[str, str, int | None]:
        word = self.current
        if not (word.kind is T.KEYWORD and word.value in _SEVERITY_DEFAULT):
            self.fail("expected DENY, WARN or COMMEND")
        self.advance()
        message = str(self.expect(T.STRING).value)
        severity = None
        if self.accept(T.KEYWORD, "SEVERITY"):
            number = self.expect(T.NUMBER)
            if not isinstance(number.value, int) or not 0 <= number.value <= 9:
                self.fail("SEVERITY must be a whole number from 0 to 9")
            severity = int(number.value)
        return str(word.value), message, severity

    def _stmt_when(self, token: Token) -> Node:
        condition = self.expression()
        self.expect(T.KEYWORD, "THEN")
        then = self.statements(("OTHERWISE", "END"))
        otherwise: list[Node] = []
        if self.accept(T.KEYWORD, "OTHERWISE"):
            otherwise = self.statements(("END",))
        self.expect(T.KEYWORD, "END")
        return When(condition=condition, then=then, otherwise=otherwise,
                    line=token.line)

    def _stmt_penalize(self, token: Token) -> Node:
        return self._adjust("PENALIZE", token)

    def _stmt_award(self, token: Token) -> Node:
        return self._adjust("AWARD", token)

    def _adjust(self, direction: str, token: Token) -> Node:
        amount = self.expression()
        message = str(self.expect(T.STRING).value)
        return Adjust(direction=direction, amount=amount, message=message,
                      line=token.line)

    def _stmt_assess(self, token: Token) -> Node:
        return Assess(expr=self.expression(), line=token.line)

    def _stmt_let(self, token: Token) -> Node:
        target = str(self.expect(T.IDENT).value)
        self.expect(T.OP, "=")
        return Let(target=target, expr=self.expression(), line=token.line)

    def _stmt_note(self, token: Token) -> Node:
        return Note(message=str(self.expect(T.STRING).value), line=token.line)

    def _stmt_cite(self, token: Token) -> Node:
        return Cite(regulation=str(self.expect(T.STRING).value), line=token.line)

    # --------------------------------------------------------- expressions

    def expression(self, min_bp: int = 0) -> Node:
        token = self.current
        if token.kind is T.KEYWORD and token.value == "NOT" and min_bp <= _NOT_BP:
            self.advance()
            left = Unary(op="NOT", operand=self.expression(_NOT_BP),
                         line=token.line)
        else:
            left = self.unary()
        while True:
            token = self.current
            op = token.value if token.kind in (T.OP, T.KEYWORD) else None
            if not isinstance(op, str) or op not in _PRECEDENCE:
                break
            bp = _PRECEDENCE[op]
            if bp < min_bp:
                break
            self.advance()
            right = self.expression(bp + 1)  # all operators are left-associative
            node_type = Logical if op in ("AND", "OR") else Binary
            left = node_type(op=op, left=left, right=right, line=token.line)
        return left

    def unary(self) -> Node:
        """Prefix minus only. ``NOT`` is handled in :meth:`expression`, where
        it can be given a binding power instead of maximal tightness."""
        token = self.current
        if self.accept(T.OP, "-"):
            return Unary(op="-", operand=self.unary(), line=token.line)
        return self.primary()

    def primary(self) -> Node:
        token = self.current

        if token.kind is T.NUMBER or token.kind is T.STRING:
            self.advance()
            return Literal(value=token.value, line=token.line)

        if token.kind is T.KEYWORD and token.value in ("TRUE", "FALSE", "NIL"):
            self.advance()
            return Literal(value={"TRUE": True, "FALSE": False,
                                  "NIL": None}[str(token.value)], line=token.line)

        if token.kind is T.IDENT:
            self.advance()
            if self.accept(T.OP, "("):
                args: list[Node] = []
                if not self.check(T.OP, ")"):
                    args.append(self.expression())
                    while self.accept(T.OP, ","):
                        args.append(self.expression())
                self.expect(T.OP, ")")
                return Call(func=str(token.value), args=args, line=token.line)
            return Name(ident=str(token.value), line=token.line)

        if self.accept(T.OP, "("):
            inner = self.expression()
            self.expect(T.OP, ")")
            return inner

        if self.accept(T.OP, "["):
            items: list[Node] = []
            if not self.check(T.OP, "]"):
                items.append(self.expression())
                while self.accept(T.OP, ","):
                    if self.check(T.OP, "]"):
                        break  # tolerate a trailing comma
                    items.append(self.expression())
            self.expect(T.OP, "]")
            return ListLit(items=items, line=token.line)

        self.fail(f"expected a value, found {token}")
        raise AssertionError("unreachable")  # pragma: no cover


def parse(source: str, origin: str = "<policy>") -> Policy:
    """Parse ``source`` into a :class:`Policy` AST."""
    return Parser(source, origin).parse()
