"""
dosl.kernel.vm -- the Regulatory Evaluation Engine.

A stack machine that executes compiled ``.bureau`` bytecode against a
sandwich and reports what it thinks. It has no loops, no user-defined
functions and no way to reach the host process, which is the only reason
the Department is permitted to run policy files at all.

It does carry a step limit, a stack depth limit and an optional instruction
trace, because "it has no loops" is a statement about the *compiler*, and
the VM is required to survive bytecode the compiler did not write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .compiler import PolicyCode
from .intrinsics import TABLE as INTRINSIC_TABLE, IntrinsicError
from .opcodes import Instruction, Kind, Op, decode

STEP_LIMIT = 100_000
STACK_LIMIT = 256
INITIAL_SCORE = 100.0


class PolicyRuntimeError(RuntimeError):
    """Raised when a policy does something the VM will not do for it."""

    def __init__(self, message: str, policy: str = "", line: int = 0,
                 offset: int = 0) -> None:
        where = f"{policy}:{line}" if policy else "<vm>"
        super().__init__(f"{where} (pc={offset}): {message}")
        self.policy, self.line, self.offset = policy, line, offset


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Finding:
    """One thing a policy objected to, approved of, or merely mentioned."""

    severity: int
    kind: Kind
    message: str
    policy: str
    line: int

    @property
    def fatal(self) -> bool:
        return self.kind is Kind.DENIAL

    def __str__(self) -> str:
        return f"[{self.kind.name[:4]} {self.severity}] {self.message}"


@dataclass(frozen=True, slots=True)
class Adjustment:
    """A signed change to the score, with the reason it was applied."""

    delta: float
    reason: str
    policy: str
    line: int

    def __str__(self) -> str:
        return f"{self.delta:+g}  {self.reason}"


@dataclass(slots=True)
class Outcome:
    """Everything one policy had to say.

    ``ASSESS`` sets :attr:`baseline`; ``PENALIZE`` and ``AWARD`` accumulate
    into :attr:`adjustments`. The final :attr:`score` is the sum, clamped.
    Keeping them separate means a policy's ASSESS line can appear anywhere
    without silently erasing the adjustments above it.
    """

    policy: str
    version: int
    baseline: float = INITIAL_SCORE
    findings: list[Finding] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    steps: int = 0
    trace: list[str] = field(default_factory=list)

    @property
    def raw_score(self) -> float:
        return self.baseline + sum(a.delta for a in self.adjustments)

    @property
    def score(self) -> float:
        return round(max(0.0, min(100.0, self.raw_score)), 2)

    @property
    def denied(self) -> bool:
        return any(f.fatal for f in self.findings)

    @property
    def worst(self) -> int:
        return max((f.severity for f in self.findings), default=0)


# --------------------------------------------------------------------------
# Machine
# --------------------------------------------------------------------------

class Machine:
    """Executes one :class:`PolicyCode` against one environment."""

    def __init__(self, engine, *, trace: bool = False,
                 step_limit: int = STEP_LIMIT) -> None:
        self.engine = engine
        self.tracing = trace
        self.step_limit = step_limit

    # ----------------------------------------------------------------- run

    def run(self, program: PolicyCode, environment: Mapping[str, Any]) -> Outcome:
        outcome = Outcome(policy=program.name, version=program.version)
        stack: list[Any] = []
        local: dict[str, Any] = {}

        # Decoding up front turns every jump into a dictionary lookup and
        # means malformed bytecode fails before any of it runs.
        listing: dict[int, Instruction] = {ins.offset: ins for ins in decode(program.code)}
        end = len(program.code)
        pc = 0

        def die(message: str) -> "PolicyRuntimeError":
            return PolicyRuntimeError(message, program.origin,
                                      program.line_for(pc), pc)

        while pc < end:
            outcome.steps += 1
            if outcome.steps > self.step_limit:
                raise die(f"step limit of {self.step_limit} exceeded")
            instruction = listing.get(pc)
            if instruction is None:
                raise die("jumped into the middle of an instruction")
            if self.tracing:
                outcome.trace.append(
                    f"{pc:04d} {instruction.op.name:<14} "
                    f"{' '.join(str(a) for a in instruction.args):<8} "
                    f"| {_render_stack(stack)}")

            op, args = instruction.op, instruction.args
            next_pc = pc + instruction.size

            # -- stack ----------------------------------------------------
            if op is Op.CONST:
                stack.append(program.constants[args[0]])
            elif op is Op.LOAD:
                key = program.names[args[0]]
                if key in local:
                    stack.append(local[key])
                elif key in environment:
                    stack.append(environment[key])
                else:
                    known = ", ".join(sorted(set(local) | set(environment))) or "nothing"
                    raise die(f"unknown field {key!r}; this sandwich has {known}")
            elif op is Op.STORE:
                local[program.names[args[0]]] = _pop(stack, die)
            elif op is Op.POP:
                _pop(stack, die)
            elif op is Op.DUP:
                stack.append(_peek(stack, die))
            elif op is Op.SWAP:
                b, a = _pop(stack, die), _pop(stack, die)
                stack.extend((b, a))
            elif op is Op.BUILD_LIST:
                count = args[0]
                if count > len(stack):
                    raise die("stack underflow building a list")
                items = stack[len(stack) - count:]
                del stack[len(stack) - count:]
                stack.append(items)
            elif op is Op.NOP:
                pass

            # -- unary ----------------------------------------------------
            elif op is Op.NEG:
                value = _pop(stack, die)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise die(f"cannot negate {_name_of(value)}")
                stack.append(-value)
            elif op is Op.NOT:
                stack.append(not _truthy(_pop(stack, die)))

            # -- binary ---------------------------------------------------
            elif Op.ADD <= op <= Op.MATCHES:
                right, left = _pop(stack, die), _pop(stack, die)
                stack.append(_binary(op, left, right, die))

            # -- control --------------------------------------------------
            elif op is Op.JUMP:
                next_pc = args[0]
            elif op is Op.JUMP_IF_FALSE:
                next_pc = args[0] if not _truthy(_pop(stack, die)) else next_pc
            elif op is Op.JUMP_IF_TRUE:
                next_pc = args[0] if _truthy(_pop(stack, die)) else next_pc
            elif op is Op.PEEK_FALSE:
                if not _truthy(_peek(stack, die)):
                    next_pc = args[0]
            elif op is Op.PEEK_TRUE:
                if _truthy(_peek(stack, die)):
                    next_pc = args[0]

            # -- calls ----------------------------------------------------
            elif op is Op.CALL:
                name, argc = program.intrinsics[args[0]], args[1]
                if argc > len(stack):
                    raise die(f"stack underflow calling {name}()")
                call_args = stack[len(stack) - argc:]
                del stack[len(stack) - argc:]
                spec = INTRINSIC_TABLE.get(name)
                if spec is None:
                    raise die(f"no such function {name!r}")
                try:
                    stack.append(spec.fn(self, *call_args))
                except IntrinsicError as exc:
                    raise die(f"{name}(): {exc}") from None
                except (TypeError, ValueError, ZeroDivisionError) as exc:
                    raise die(f"{name}() failed: {exc}") from None

            # -- adjudication ---------------------------------------------
            elif op is Op.FINDING:
                message = _pop(stack, die)
                outcome.findings.append(Finding(
                    severity=args[0], kind=Kind(args[1]), message=str(message),
                    policy=program.name, line=program.line_for(pc)))
            elif op in (Op.PENALIZE, Op.AWARD):
                reason = str(_pop(stack, die))
                amount = _pop(stack, die)
                if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                    raise die(f"{op.name} needs a number, got {_name_of(amount)}")
                delta = float(-abs(amount) if op is Op.PENALIZE else abs(amount))
                outcome.adjustments.append(Adjustment(
                    delta=delta, reason=reason, policy=program.name,
                    line=program.line_for(pc)))
            elif op is Op.ASSESS:
                value = _pop(stack, die)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise die(f"ASSESS needs a number, got {_name_of(value)}")
                outcome.baseline = float(value)
            elif op is Op.NOTE:
                outcome.notes.append(str(_pop(stack, die)))
            elif op is Op.CITE:
                outcome.citations.append(str(_pop(stack, die)))

            elif op is Op.HALT:
                break
            else:  # pragma: no cover -- Op is exhaustive above
                raise die(f"unimplemented opcode {op.name}")

            if len(stack) > STACK_LIMIT:
                raise die(f"stack exceeded {STACK_LIMIT} entries")
            if next_pc > end:
                raise die(f"jump to {next_pc} is past the end of the policy")
            pc = next_pc

        return outcome


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------

def _pop(stack: list, die) -> Any:
    if not stack:
        raise die("stack underflow")
    return stack.pop()


def _peek(stack: list, die) -> Any:
    if not stack:
        raise die("stack underflow")
    return stack[-1]


def _truthy(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (list, tuple, str, dict)):
        return len(value) > 0
    return bool(value)


def _name_of(value: Any) -> str:
    return {bool: "TRUE/FALSE", int: "a number", float: "a number",
            str: "text", list: "a list", type(None): "NIL"}.get(
                type(value), type(value).__name__)


def _render_stack(stack: list) -> str:
    parts = [repr(v) if not isinstance(v, str) else f'"{v[:14]}"' for v in stack[-4:]]
    prefix = "... " if len(stack) > 4 else ""
    return prefix + " ".join(parts)


def _comparable(left: Any, right: Any) -> bool:
    numeric = (int, float)
    if isinstance(left, numeric) and isinstance(right, numeric):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _binary(op: Op, left: Any, right: Any, die) -> Any:
    """Apply one binary opcode, with error messages a policy author can act on."""
    if op is Op.ADD:
        if isinstance(left, str) or isinstance(right, str):
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            raise die(f"cannot add {_name_of(left)} to {_name_of(right)}")
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        return _arith(op, left, right, die)
    if op in (Op.SUB, Op.MUL, Op.DIV, Op.MOD):
        return _arith(op, left, right, die)

    if op is Op.EQ:
        return _equal(left, right)
    if op is Op.NE:
        return not _equal(left, right)
    if op in (Op.LT, Op.LE, Op.GT, Op.GE):
        if not _comparable(left, right):
            raise die(f"cannot compare {_name_of(left)} with {_name_of(right)}")
        return {Op.LT: left < right, Op.LE: left <= right,
                Op.GT: left > right, Op.GE: left >= right}[op]

    if op is Op.IN:
        return _member(left, right)
    if op is Op.CONTAINS:
        return _member(right, left)
    if op is Op.MATCHES:
        return str(_fold(right)) in str(_fold(left))

    raise die(f"unimplemented binary opcode {op.name}")  # pragma: no cover


def _arith(op: Op, left: Any, right: Any, die) -> Any:
    for value in (left, right):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise die(f"arithmetic needs numbers, got {_name_of(value)}")
    if op in (Op.DIV, Op.MOD) and right == 0:
        raise die("division by zero; the Department does not permit this")
    result = {Op.ADD: lambda: left + right, Op.SUB: lambda: left - right,
              Op.MUL: lambda: left * right, Op.DIV: lambda: left / right,
              Op.MOD: lambda: left % right}[op]()
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def _fold(value: Any) -> Any:
    """Case-fold text so policy comparisons are not a spelling test."""
    return value.casefold() if isinstance(value, str) else value


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right))
    return _fold(left) == _fold(right)


def _member(needle: Any, haystack: Any) -> bool:
    if isinstance(haystack, str):
        return str(_fold(needle)) in _fold(haystack)
    if isinstance(haystack, (list, tuple)):
        return any(_equal(needle, item) for item in haystack)
    return False
