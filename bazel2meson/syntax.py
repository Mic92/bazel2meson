"""Build Meson source from Python values.

Every Python value passes through :func:`render` on its way into a
meson.build file, which is the only place that knows how Meson quotes
things. Code is represented by :class:`Expr`; plain ``str`` is always data
and comes out as a string literal.
"""

from __future__ import annotations

import re
from pathlib import PurePath

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LINE_WIDTH = 100

# Binding strength of the outermost operator of an expression.
_SUM, _PATH, _ATOM = 0, 1, 2

type Value = Expr | str | bool | int | PurePath | list[Value] | tuple[Value, ...] | dict[str, Value]


class Expr:
    """A piece of Meson code. Combine with ``+``, ``/``, indexing and :meth:`method`."""

    __slots__ = ("code", "strength")

    def __init__(self, code: str, strength: int = _ATOM) -> None:
        self.code = code
        self.strength = strength

    def _operand(self, strength: int) -> str:
        return self.code if self.strength >= strength else f"({self.code})"

    def __add__(self, other: Value) -> Expr:
        return Expr(f"{self.code} + {_operand(other, _SUM)}", _SUM)

    def __radd__(self, other: Value) -> Expr:
        return Expr(f"{_operand(other, _SUM)} + {self.code}", _SUM)

    def __truediv__(self, other: Value) -> Expr:
        return Expr(f"{self._operand(_PATH)} / {_operand(other, _ATOM)}", _PATH)

    def __rtruediv__(self, other: Value) -> Expr:
        return Expr(f"{_operand(other, _PATH)} / {self._operand(_ATOM)}", _PATH)

    def __getitem__(self, key: int | str) -> Expr:
        return Expr(f"{self._operand(_ATOM)}[{render(key)}]")

    def method(self, name: str, *args: Value, **kwargs: Value) -> Expr:
        return Expr(f"{self._operand(_ATOM)}.{_invoke(name, args, kwargs)}")

    def __repr__(self) -> str:
        return f"Expr({self.code!r})"


def _operand(value: Value, strength: int) -> str:
    return value._operand(strength) if isinstance(value, Expr) else render(value)


def identifier(name: str) -> str:
    if not IDENTIFIER.match(name):
        msg = f"not a Meson identifier: {name!r}"
        raise ValueError(msg)
    return name


def sanitize(text: str) -> str:
    """An identifier derived from arbitrary text such as a path."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def var(name: str) -> Expr:
    return Expr(identifier(name))


def call(function: str, *args: Value, **kwargs: Value) -> Expr:
    return Expr(_invoke(function, args, kwargs))


def string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"


def _sequence(opening: str, items: list[str], closing: str) -> str:
    flat = f"{opening}{', '.join(items)}{closing}"
    if len(flat) <= LINE_WIDTH and "\n" not in flat:
        return flat
    # String literals never contain a raw newline, so re-indenting is safe.
    body = "".join(f"  {item.replace('\n', '\n  ')},\n" for item in items)
    return f"{opening}\n{body}{closing}"


def _invoke(function: str, args: tuple[Value, ...], kwargs: dict[str, Value]) -> str:
    items = [render(arg) for arg in args]
    items += [f"{identifier(key)}: {render(value)}" for key, value in kwargs.items()]
    return _sequence(f"{identifier(function)}(", items, ")")


def render(value: Value) -> str:
    """Meson source for a Python value."""
    if isinstance(value, Expr):
        return value.code
    if isinstance(value, str):
        return string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, PurePath):
        return string(str(value))
    if isinstance(value, list | tuple):
        return _sequence("[", [render(item) for item in value], "]")
    if isinstance(value, dict):
        return _sequence("{", [f"{string(key)}: {render(item)}" for key, item in value.items()], "}")
    msg = f"cannot express {type(value).__name__} in Meson"
    raise TypeError(msg)


def assign(name: str, value: Value) -> str:
    return f"{identifier(name)} = {render(value)}"


def extend(name: str, value: Value) -> str:
    return f"{identifier(name)} += {render(value)}"


def statement(expr: Expr) -> str:
    return expr.code


SOURCE_ROOT = var("meson").method("project_source_root")
BUILD_ROOT = var("meson").method("project_build_root")
CURRENT_SOURCE_DIR = var("meson").method("current_source_dir")
