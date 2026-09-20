"""
dosl.formats.struct_dsl -- declarative binary structures.

Subclass :class:`BinaryStruct`, annotate the fields, get ``pack`` and
``unpack`` for free::

    class Header(BinaryStruct, endian="<"):
        magic: Magic(b"SDWX")
        version: U16 = 1
        flags: U16 = 0
        stamp: U64
        title: Utf8(32)

The metaclass reads the annotations at class-creation time, builds one
``struct`` format string, precompiles it, and rejects anything ambiguous
then and there rather than at 3am inside a file parser.

The field types are descriptors, so ``header.version = 70000`` raises
immediately instead of producing a file nobody can read.
"""

from __future__ import annotations

import struct
from typing import Any, ClassVar, Iterator


class StructError(ValueError):
    """Raised for a malformed declaration, or a value that will not pack."""


# --------------------------------------------------------------------------
# Field descriptors
# --------------------------------------------------------------------------

class Field:
    """Base descriptor: validates on assignment, stores in the instance dict."""

    code: str = ""
    size: int = 0

    def __init__(self, default: Any = None) -> None:
        self.default = default
        self.name = "<unbound>"

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name, self.default)

    def __set__(self, obj, value) -> None:
        obj.__dict__[self.name] = self.validate(value)

    def validate(self, value: Any) -> Any:
        return value

    def to_struct(self, value: Any) -> Any:
        return value

    def from_struct(self, value: Any) -> Any:
        return value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


class _Int(Field):
    bits: int = 0
    signed: bool = False

    def __init__(self, default: int = 0) -> None:
        super().__init__(default)

    @property
    def low(self) -> int:
        return -(1 << (self.bits - 1)) if self.signed else 0

    @property
    def high(self) -> int:
        return (1 << (self.bits - 1)) - 1 if self.signed else (1 << self.bits) - 1

    def validate(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise StructError(f"{self.name}: expected an integer, got {value!r}")
        if not self.low <= value <= self.high:
            raise StructError(
                f"{self.name}: {value} does not fit in "
                f"{'i' if self.signed else 'u'}{self.bits} "
                f"[{self.low}, {self.high}]")
        return value


class U8(_Int):
    code, size, bits = "B", 1, 8


class U16(_Int):
    code, size, bits = "H", 2, 16


class U32(_Int):
    code, size, bits = "I", 4, 32


class U64(_Int):
    code, size, bits = "Q", 8, 64


class I32(_Int):
    code, size, bits, signed = "i", 4, 32, True


class I64(_Int):
    code, size, bits, signed = "q", 8, 64, True


class F64(Field):
    code, size = "d", 8

    def __init__(self, default: float = 0.0) -> None:
        super().__init__(default)

    def validate(self, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StructError(f"{self.name}: expected a number, got {value!r}")
        return float(value)


class Bytes(Field):
    """A fixed-width byte field, zero-padded on write, kept whole on read."""

    def __init__(self, length: int, default: bytes = b"") -> None:
        if length <= 0:
            raise StructError("Bytes(length) must be positive")
        self.size = length
        self.code = f"{length}s"
        super().__init__(default.ljust(length, b"\x00"))

    def validate(self, value: Any) -> bytes:
        if isinstance(value, str):
            value = value.encode("utf-8")
        if not isinstance(value, (bytes, bytearray)):
            raise StructError(f"{self.name}: expected bytes, got {type(value).__name__}")
        if len(value) > self.size:
            raise StructError(
                f"{self.name}: {len(value)} bytes will not fit in {self.size}")
        return bytes(value).ljust(self.size, b"\x00")


class Magic(Bytes):
    """A constant signature. Refuses to be set to anything else."""

    def __init__(self, signature: bytes) -> None:
        super().__init__(len(signature), signature)
        self.signature = signature

    def validate(self, value: Any) -> bytes:
        value = super().validate(value)
        if value != self.signature:
            raise StructError(
                f"{self.name}: bad signature {value!r}, expected {self.signature!r}")
        return value


class Utf8(Bytes):
    """Fixed-width UTF-8 text, NUL-padded on disk and a ``str`` in memory.

    Truncation is an error rather than a shrug: a field that silently drops
    the tail of a name produces files that disagree with themselves.
    """

    def __init__(self, length: int, default: str = "") -> None:
        super().__init__(length, default.encode("utf-8"))
        self.default = default  # keep the in-memory form as text

    def validate(self, value: Any) -> str:
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).rstrip(b"\x00").decode("utf-8", "replace")
        if not isinstance(value, str):
            raise StructError(f"{self.name}: expected text, got {type(value).__name__}")
        encoded = value.encode("utf-8")
        if len(encoded) > self.size:
            raise StructError(
                f"{self.name}: {value!r} is {len(encoded)} bytes, "
                f"the field holds {self.size}")
        return value

    def from_struct(self, value: Any) -> str:
        return bytes(value).rstrip(b"\x00").decode("utf-8", "replace")

    def to_struct(self, value: Any) -> bytes:
        return self.validate(value).encode("utf-8").ljust(self.size, b"\x00")


# --------------------------------------------------------------------------
# Metaclass
# --------------------------------------------------------------------------

class StructMeta(type):
    """Collects :class:`Field` annotations into a compiled struct format."""

    def __new__(mcls, name: str, bases: tuple, namespace: dict,
                endian: str = "<", **kwargs):
        fields: dict[str, Field] = {}

        # Inherit base fields first so subclasses extend rather than replace.
        for base in bases:
            fields.update(getattr(base, "_fields", {}))

        for attr, declared in list(namespace.get("__annotations__", {}).items()):
            if attr.startswith("_"):
                continue
            if isinstance(declared, str):
                # The defining module used `from __future__ import annotations`,
                # so every annotation is a string and none of this can work.
                raise StructError(
                    f"{name}.{attr}: annotations are strings here. A module "
                    f"defining BinaryStructs must not use "
                    f"`from __future__ import annotations`.")
            if isinstance(declared, type) and issubclass(declared, Field):
                declared = declared()  # `x: U16` is shorthand for `x: U16()`
            if not isinstance(declared, Field):
                continue  # an ordinary type hint on a non-field attribute
            if attr in namespace and not isinstance(namespace[attr], Field):
                declared.default = declared.validate(namespace[attr])
            namespace[attr] = declared
            fields[attr] = declared

        namespace["_fields"] = fields
        namespace["_endian"] = endian
        if fields:
            fmt = endian + "".join(f.code for f in fields.values())
            namespace["_format"] = fmt
            namespace["_struct"] = struct.Struct(fmt)
            expected = sum(f.size for f in fields.values())
            if struct.calcsize(fmt) != expected:
                raise StructError(
                    f"{name}: struct alignment surprise -- "
                    f"{struct.calcsize(fmt)} packed vs {expected} declared")

        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        for attr, declared in fields.items():
            declared.__set_name__(cls, attr)
        return cls

    def __init__(cls, name, bases, namespace, endian="<", **kwargs):
        super().__init__(name, bases, namespace, **kwargs)


class BinaryStruct(metaclass=StructMeta):
    """A fixed-layout record that knows how to pack and unpack itself."""

    _fields: ClassVar[dict[str, Field]] = {}
    _format: ClassVar[str] = ""
    _struct: ClassVar[struct.Struct] = struct.Struct("")
    _endian: ClassVar[str] = "<"

    def __init__(self, **kwargs: Any) -> None:
        unknown = set(kwargs) - set(self._fields)
        if unknown:
            raise StructError(
                f"{type(self).__name__} has no field(s): {', '.join(sorted(unknown))}")
        for name, field in self._fields.items():
            value = kwargs.get(name, field.default)
            if value is None:
                raise StructError(f"{type(self).__name__}.{name} has no value")
            setattr(self, name, value)

    # ------------------------------------------------------------ geometry

    @classmethod
    def size(cls) -> int:
        return cls._struct.size

    @classmethod
    def offset_of(cls, name: str) -> int:
        """Byte offset of a field, for patching a header in place."""
        offset = 0
        for attr, field in cls._fields.items():
            if attr == name:
                return offset
            offset += field.size
        raise StructError(f"{cls.__name__} has no field {name!r}")

    # -------------------------------------------------------------- codecs

    def pack(self) -> bytes:
        values = [f.to_struct(getattr(self, n)) for n, f in self._fields.items()]
        try:
            return self._struct.pack(*values)
        except struct.error as exc:
            raise StructError(f"{type(self).__name__}: {exc}") from exc

    @classmethod
    def unpack(cls, data: bytes, offset: int = 0) -> "BinaryStruct":
        if len(data) - offset < cls.size():
            raise StructError(
                f"{cls.__name__} needs {cls.size()} bytes, "
                f"only {max(0, len(data) - offset)} available at offset {offset}")
        raw = cls._struct.unpack_from(data, offset)
        return cls(**{name: field.from_struct(value)
                      for (name, field), value in zip(cls._fields.items(), raw)})

    @classmethod
    def unpack_many(cls, data: bytes, count: int, offset: int = 0
                    ) -> Iterator["BinaryStruct"]:
        for index in range(count):
            yield cls.unpack(data, offset + index * cls.size())

    # ------------------------------------------------------------ niceties

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self._fields}

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other.as_dict() == self.as_dict()

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self.as_dict().items())
        return f"{type(self).__name__}({inner})"
