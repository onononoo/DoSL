"""
dosl.bureau.forms -- Form 27-B/6 and the machinery that validates it.

Fields are descriptors collected by a metaclass, so the schema is declared
once and everything else is derived from it: the command line's ``--flags``,
the GUI's widgets, JSON round-tripping, and the environment handed to the
policy VM. Adding a question to the form is one line in one place.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, ClassVar, Iterable


class ValidationError(ValueError):
    """One field is wrong. The Department will tell you which."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field, self.message = field, message


# --------------------------------------------------------------------------
# Field descriptors
# --------------------------------------------------------------------------

class FormField:
    """A question on a form, and the rules for answering it badly."""

    kind: ClassVar[str] = "text"

    def __init__(self, label: str, default: Any = None, *, help: str = "",
                 required: bool = True, section: str = "General") -> None:
        self.label = label
        self.default = default
        self.help = help
        self.required = required
        self.section = section
        self.name = "<unbound>"
        self.order = next(_counter)

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj._values.get(self.name, self.default)

    def __set__(self, obj, value) -> None:
        obj._values[self.name] = self.coerce(value)

    def coerce(self, value: Any) -> Any:
        """Convert whatever we were handed into the field's canonical type."""
        return value

    def validate(self, value: Any) -> None:
        """Raise :class:`ValidationError` if ``value`` will not do."""

    def choices(self) -> list[str]:
        return []

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


def _ints():
    n = 0
    while True:
        yield n
        n += 1


_counter = _ints()


class TextField(FormField):
    kind = "text"

    def __init__(self, label: str, default: str = "", *, max_length: int = 120,
                 pattern: str = "", suggestions: Iterable[str] = (), **kwargs) -> None:
        super().__init__(label, default, **kwargs)
        self.max_length = max_length
        self.pattern = re.compile(pattern) if pattern else None
        self.suggestions = list(suggestions)

    def coerce(self, value: Any) -> str:
        return "" if value is None else str(value).strip()

    def validate(self, value: Any) -> None:
        if self.required and not value:
            raise ValidationError(self.label, "must not be blank")
        if len(value) > self.max_length:
            raise ValidationError(
                self.label, f"is {len(value)} characters; the box holds {self.max_length}")
        if value and self.pattern and not self.pattern.fullmatch(value):
            raise ValidationError(self.label, f"does not match {self.pattern.pattern}")

    def choices(self) -> list[str]:
        return list(self.suggestions)


class ChoiceField(FormField):
    kind = "choice"

    def __init__(self, label: str, options: Iterable[str], default: str = "",
                 *, strict: bool = True, **kwargs) -> None:
        self.options = list(options)
        super().__init__(label, default or self.options[0], **kwargs)
        self.strict = strict

    def coerce(self, value: Any) -> str:
        text = "" if value is None else str(value).strip()
        for option in self.options:
            if option.casefold() == text.casefold():
                return option
        return text

    def validate(self, value: Any) -> None:
        if self.strict and value not in self.options:
            raise ValidationError(
                self.label,
                f"{value!r} is not one of: {', '.join(self.options)}")

    def choices(self) -> list[str]:
        return list(self.options)


class IntField(FormField):
    kind = "number"

    def __init__(self, label: str, default: int = 0, *, low: int = 0,
                 high: int = 100, unit: str = "", **kwargs) -> None:
        super().__init__(label, default, **kwargs)
        self.low, self.high, self.unit = low, high, unit

    def coerce(self, value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return self.default
        try:
            return int(float(text))
        except ValueError:
            raise ValidationError(self.label, f"{value!r} is not a number") from None

    def validate(self, value: Any) -> None:
        if not self.low <= value <= self.high:
            raise ValidationError(
                self.label, f"{value}{self.unit} is outside {self.low}-{self.high}{self.unit}")


class BoolField(FormField):
    kind = "bool"

    TRUTHY = frozenset({"1", "true", "yes", "y", "on", "affirmative"})
    FALSEY = frozenset({"0", "false", "no", "n", "off", "negative", ""})

    def __init__(self, label: str, default: bool = False, **kwargs) -> None:
        super().__init__(label, default, **kwargs)

    def coerce(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().casefold()
        if text in self.TRUTHY:
            return True
        if text in self.FALSEY:
            return False
        raise ValidationError(self.label, f"{value!r} is neither yes nor no")


class ListField(FormField):
    """A comma-separated list of short strings."""

    kind = "list"

    def __init__(self, label: str, default: Iterable[str] = (), *,
                 max_items: int = 12, suggestions: Iterable[str] = (), **kwargs) -> None:
        super().__init__(label, list(default), **kwargs)
        self.max_items = max_items
        self.suggestions = list(suggestions)

    def coerce(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [part for part in re.split(r"[,;\n]", value)]
        return [str(item).strip().casefold() for item in value if str(item).strip()]

    def validate(self, value: Any) -> None:
        if self.required and not value:
            raise ValidationError(self.label, "must list at least one item")
        if len(value) > self.max_items:
            raise ValidationError(
                self.label, f"lists {len(value)} items; the form allows {self.max_items}")
        for item in value:
            if len(item) > 32:
                raise ValidationError(self.label, f"{item!r} is too long for the box")

    def choices(self) -> list[str]:
        return list(self.suggestions)


# --------------------------------------------------------------------------
# Form
# --------------------------------------------------------------------------

class FormMeta(type):
    """Collects :class:`FormField` attributes, in declaration order."""

    def __new__(mcls, name, bases, namespace, **kwargs):
        fields: dict[str, FormField] = {}
        for base in bases:
            fields.update(getattr(base, "fields", {}))
        own = {k: v for k, v in namespace.items() if isinstance(v, FormField)}
        fields.update(dict(sorted(own.items(), key=lambda kv: kv[1].order)))
        namespace["fields"] = fields
        return super().__new__(mcls, name, bases, namespace, **kwargs)


class Form(metaclass=FormMeta):
    """Base form: construction, validation, JSON, and the VM environment."""

    fields: ClassVar[dict[str, FormField]] = {}
    title: ClassVar[str] = "Form"
    code: ClassVar[str] = "00-A/0"

    def __init__(self, **kwargs: Any) -> None:
        self._values: dict[str, Any] = {}
        unknown = set(kwargs) - set(self.fields)
        if unknown:
            raise ValidationError(
                "form", f"no such field(s): {', '.join(sorted(unknown))}")
        for name, field in self.fields.items():
            setattr(self, name, kwargs.get(name, field.default))

    # ---------------------------------------------------------- validation

    def problems(self) -> list[ValidationError]:
        """Every complaint at once, rather than one per resubmission."""
        found: list[ValidationError] = []
        for name, field in self.fields.items():
            try:
                field.validate(getattr(self, name))
            except ValidationError as exc:
                found.append(exc)
        found.extend(self.cross_check())
        return found

    def cross_check(self) -> list[ValidationError]:
        """Overridden for rules that span more than one field."""
        return []

    def require_valid(self) -> None:
        problems = self.problems()
        if problems:
            raise ValidationError(
                "Form 27-B/6",
                "returned for correction --\n  "
                + "\n  ".join(str(p) for p in problems))

    # -------------------------------------------------------------- codecs

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.fields}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Form":
        return cls(**{k: v for k, v in data.items() if k in cls.fields})

    def sections(self) -> dict[str, list[FormField]]:
        grouped: dict[str, list[FormField]] = {}
        for field in self.fields.values():
            grouped.setdefault(field.section, []).append(field)
        return grouped

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.as_dict()}>"


# --------------------------------------------------------------------------
# The form itself
# --------------------------------------------------------------------------

BREADS = ["rye", "sourdough", "wholemeal", "white sliced", "baguette", "brioche",
          "focaccia", "bagel", "ciabatta", "tortilla wrap", "a bowl"]
CHEESES = ["none", "cheddar", "swiss", "brie", "american", "halloumi", "gouda",
           "something blue"]
CUTS = ["diagonal", "rectangular", "uncut", "chaotic"]
VENUES = ["kitchen table", "desk", "standing", "car", "in transit", "park bench"]
ACCOMPANIMENTS = ["none", "crisps", "chips", "pickle", "soup", "an apple"]
COMMON_FILLINGS = ["pastrami", "ham", "turkey", "tuna", "egg", "cucumber",
                   "tomato", "lettuce", "pineapple", "bacon", "falafel", "avocado"]
COMMON_CONDIMENTS = ["mustard", "mayonnaise", "ketchup", "butter", "jam",
                     "relish", "aioli", "pickle brine", "gravy", "hummus"]


class Form27B6(Form):
    """The Sandwich Legitimacy Application. There is no Form 27-B/5."""

    title = "Sandwich Legitimacy Application"
    code = "27-B/6"

    applicant = TextField(
        "Name of applicant", "A. Citizen", max_length=48, section="Applicant",
        help="As it appears on at least one document.")
    declared_purpose = TextField(
        "Declared purpose", "lunch", max_length=90, required=False,
        section="Applicant",
        help="The Directorate of Nomenclature reads this. Keep it under twelve words.")
    clearance = IntField(
        "Clearance level", 3, low=0, high=9, section="Applicant",
        help="Self-assessed. The Department does not audit this. The Department knows.")

    bread = TextField(
        "Vessel", "rye", max_length=32, suggestions=BREADS, section="Construction",
        help="Bread, or the closest thing you are prepared to call bread.")
    layers = IntField(
        "Bread layers", 2, low=1, high=6, section="Construction")
    height_mm = IntField(
        "Assembled height", 38, low=5, high=250, unit="mm", section="Construction")
    cut = ChoiceField(
        "Cut geometry", CUTS, "diagonal", section="Construction")
    crusts_removed = BoolField(
        "Crusts removed", False, section="Construction",
        help="Answer honestly. It will be cross-referenced.")
    toasted = BoolField("Toasted", False, section="Construction")

    fillings = ListField(
        "Fillings", ["pastrami"], max_items=12, suggestions=COMMON_FILLINGS,
        section="Contents", help="Comma-separated.")
    condiments = ListField(
        "Condiments", ["mustard"], max_items=8, suggestions=COMMON_CONDIMENTS,
        section="Contents", required=False, help="Comma-separated.")
    cheese = ChoiceField("Cheese", CHEESES, "none", section="Contents")

    consumed_at = ChoiceField(
        "Consumption venue", VENUES, "kitchen table", section="Circumstances")
    hour = IntField(
        "Hour of consumption", 13, low=0, high=23, unit="h", section="Circumstances")
    urgency = IntField(
        "Declared urgency", 2, low=0, high=9, section="Circumstances")
    accompaniment = ChoiceField(
        "Accompaniment", ACCOMPANIMENTS, "crisps", section="Circumstances")

    def cross_check(self) -> list[ValidationError]:
        problems: list[ValidationError] = []
        if self.layers == 1 and self.height_mm > 40:
            problems.append(ValidationError(
                "Assembled height",
                "a single slice cannot be 40mm tall; check one of these two numbers"))
        if self.crusts_removed and self.bread.casefold() in ("baguette", "bagel"):
            problems.append(ValidationError(
                "Crusts removed",
                f"a {self.bread} is substantially crust; the claim is not credible"))
        overlap = set(self.fillings) & set(self.condiments)
        if overlap:
            problems.append(ValidationError(
                "Contents",
                f"{', '.join(sorted(overlap))} declared as both filling and "
                f"condiment; the Department requires you to choose"))
        return problems

    # ------------------------------------------------------- VM environment

    def environment(self, *, when: _dt.datetime | None = None) -> dict[str, Any]:
        """The exact set of names a ``.bureau`` policy may reference.

        Policies see this dictionary and nothing else -- no builtins, no
        module globals, no way back into the host. The derived counts are
        computed here so five policies cannot each derive them differently.
        """
        now = when or _dt.datetime.now()
        base = self.as_dict()
        base.update({
            "filling_count": len(self.fillings),
            "condiment_count": len(self.condiments),
            "total_items": len(self.fillings) + len(self.condiments),
            "weekday": now.strftime("%A").casefold(),
            "submitted_hour": now.hour,
        })
        return base


def field_help() -> list[str]:
    """One line per field, for ``dosl form --explain``."""
    lines = []
    for section, fields in Form27B6().sections().items():
        lines.append(f"\n{section}")
        for field in fields:
            options = field.choices()
            hint = f"  ({', '.join(options[:6])}{'...' if len(options) > 6 else ''})" \
                if options else ""
            lines.append(f"  --{field.name.replace('_', '-'):<18} {field.label}{hint}")
            if field.help:
                lines.append(f"  {'':<20} {field.help}")
    return lines
