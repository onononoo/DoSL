"""
dosl.kernel.intrinsics -- the standard library available to ``.bureau`` policies.

Every intrinsic receives the running :class:`~dosl.kernel.vm.Machine` as its
first argument, whether it wants it or not, so that ``entropy`` and ``sign``
can reach the native engine without a second calling convention existing.

The table is the single source of truth: the compiler validates call arity
against it and the VM dispatches through it, so adding a function here is the
only step required to add a function to the language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class IntrinsicError(RuntimeError):
    """An intrinsic was called with something it could not work with."""


@dataclass(frozen=True, slots=True)
class Intrinsic:
    name: str
    fn: Callable[..., object]
    min_args: int
    max_args: int | None  # None means variadic
    doc: str

    def check_arity(self, argc: int) -> str | None:
        """Return an error message if ``argc`` is wrong, else ``None``."""
        if argc < self.min_args:
            return (f"{self.name}() takes at least {self.min_args} argument(s), "
                    f"got {argc}")
        if self.max_args is not None and argc > self.max_args:
            return (f"{self.name}() takes at most {self.max_args} argument(s), "
                    f"got {argc}")
        return None


def _sized(value: object) -> "list | str | tuple":
    if isinstance(value, (list, tuple, str)):
        return value
    if value is None:
        return []
    raise IntrinsicError(f"expected a list or text, got {type(value).__name__}")


def _number(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raise IntrinsicError(f"expected a number, got {type(value).__name__}")


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value)
    return str(value)


def _blob(value: object) -> bytes:
    """Flatten anything into bytes so it can be hashed."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, list):
        return b"\x1f".join(_blob(v) for v in value)
    return _text(value).encode("utf-8")


def _tidy(value: float) -> float | int:
    """Collapse 3.0 back to 3, because ``4.0 mustard sachets`` reads badly."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


#: Populated by :func:`intrinsic`, consumed by the compiler and the VM.
TABLE: dict[str, Intrinsic] = {}


def intrinsic(name: str, min_args: int, max_args: int | None, doc: str):
    def register(fn):
        TABLE[name] = Intrinsic(name, fn, min_args, max_args, doc)
        return fn
    return register


# -- collections -----------------------------------------------------------

@intrinsic("count", 1, 1, "Number of items in a list, or characters in text.")
def _count(vm, value):
    return len(_sized(value))


@intrinsic("len", 1, 1, "Alias for count(), for people with other habits.")
def _len(vm, value):
    return len(_sized(value))


@intrinsic("has", 2, 2, "TRUE when a list or text contains the second value.")
def _has(vm, haystack, needle):
    container = _sized(haystack)
    if isinstance(container, str):
        return _text(needle).casefold() in container.casefold()
    return any(_text(item).casefold() == _text(needle).casefold()
               for item in container)


@intrinsic("distinct", 1, 1, "The list with duplicates removed, order kept.")
def _distinct(vm, value):
    seen, out = set(), []
    for item in _sized(value):
        key = _text(item).casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


@intrinsic("words", 1, 1, "Split text into a list of words.")
def _words(vm, value):
    return _text(value).split()


@intrinsic("any", 1, 1, "TRUE when at least one item in the list is truthy.")
def _any(vm, value):
    return any(bool(item) for item in _sized(value))


@intrinsic("all", 1, 1, "TRUE when every item in the list is truthy.")
def _all(vm, value):
    return all(bool(item) for item in _sized(value))


# -- arithmetic ------------------------------------------------------------

@intrinsic("abs", 1, 1, "Absolute value.")
def _abs(vm, value):
    return _tidy(abs(_number(value)))


@intrinsic("min", 1, None, "Smallest of the arguments, or of a single list.")
def _min(vm, *values):
    pool = _sized(values[0]) if len(values) == 1 else values
    if not pool:
        raise IntrinsicError("min() of an empty list")
    return _tidy(min(_number(v) for v in pool))


@intrinsic("max", 1, None, "Largest of the arguments, or of a single list.")
def _max(vm, *values):
    pool = _sized(values[0]) if len(values) == 1 else values
    if not pool:
        raise IntrinsicError("max() of an empty list")
    return _tidy(max(_number(v) for v in pool))


@intrinsic("sum", 1, 1, "Total of a list of numbers.")
def _sum(vm, value):
    return _tidy(sum(_number(v) for v in _sized(value)))


@intrinsic("clamp", 3, 3, "Constrain a number to a low..high range.")
def _clamp(vm, value, low, high):
    lo, hi = _number(low), _number(high)
    if lo > hi:
        lo, hi = hi, lo
    return _tidy(max(lo, min(hi, _number(value))))


@intrinsic("round", 1, 2, "Round to the given number of decimal places.")
def _round(vm, value, places=0):
    return _tidy(round(_number(value), int(_number(places))))


# -- text ------------------------------------------------------------------

@intrinsic("lower", 1, 1, "Text, folded to lower case.")
def _lower(vm, value):
    return _text(value).casefold()


@intrinsic("upper", 1, 1, "Text, raised to upper case.")
def _upper(vm, value):
    return _text(value).upper()


@intrinsic("text", 1, 1, "Convert anything to text.")
def _to_text(vm, value):
    return _text(value)


@intrinsic("number", 1, 1, "Convert text to a number; NIL when it will not go.")
def _to_number(vm, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _tidy(float(value))
    try:
        return _tidy(float(_text(value)))
    except ValueError:
        return None


# -- the interesting ones --------------------------------------------------

@intrinsic("entropy", 1, 1,
           "A stable pseudo-random 0..999 derived from the value, via the "
           "native entropy engine.")
def _entropy(vm, value):
    return vm.engine.digest(_blob(value)) % 1000


@intrinsic("sign", 1, 1,
           "A stable eight-hex-digit signature of the value. Appears on "
           "certificates and impresses nobody.")
def _sign(vm, value):
    return f"{vm.engine.fnv1a(_blob(value)):08X}"


@intrinsic("verdict", 2, 2,
           "The native adjudication kernel: folds entropy against a clearance "
           "level into a score from 0 to 255.")
def _verdict(vm, value, clearance):
    return vm.engine.verdict(vm.engine.digest(_blob(value)),
                             int(_number(clearance)) & 0xFFFFFFFF)


def describe() -> list[str]:
    """One line per intrinsic, for the help screen."""
    out = []
    for name in sorted(TABLE):
        spec = TABLE[name]
        arity = (f"{spec.min_args}+" if spec.max_args is None
                 else str(spec.min_args) if spec.min_args == spec.max_args
                 else f"{spec.min_args}-{spec.max_args}")
        out.append(f"{name}({arity})  {spec.doc}")
    return out
