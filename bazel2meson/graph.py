"""Configuration (select()) resolution and dependency traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .starlark import Label, Recorder, Select, SelectBranch, Stub, Target, Workspace

DEFAULT = "//conditions:default"


@dataclass
class Config:
    """The subset of a Bazel build configuration that select() can observe."""

    # `--define k=v`
    defines: dict[str, str] = field(default_factory=dict)
    # Native options matched by config_setting(values = ...), e.g. compilation_mode.
    values: dict[str, str] = field(default_factory=dict)
    # Starlark build settings (bool_flag/string_flag/label_flag) set on the command line.
    flags: dict[str, str] = field(default_factory=dict)
    # Platform constraint_values that hold, e.g. @platforms//os:linux.
    constraints: set[str] = field(default_factory=set)
    # Conditions living in repositories we have no sources for.
    conditions: dict[str, bool] = field(default_factory=dict)


def _norm(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return text.lower() if text.lower() in ("true", "false") else text


class Resolver:
    def __init__(self, ws: Workspace, config: Config) -> None:
        self.ws = ws
        self.config = config
        self._cache: dict[Label, bool] = {}
        self.unknown_conditions: set[str] = set()
        self.unknown_values: set[str] = set()

    # -- conditions ---------------------------------------------------------

    def flag_value(self, label: Label) -> str | None:
        if str(label) in self.config.flags:
            return _norm(self.config.flags[str(label)])
        target = self.ws.get(label)
        if target is None:
            return None
        if target.kind == "alias":
            return self.flag_value(self.ws.label(self.value(target.attrs["actual"]), label.repo, label.pkg))
        default = target.attrs.get("build_setting_default")
        return None if default is None else _norm(self.value(default))

    def holds(self, label: Label) -> bool:
        if label in self._cache:
            return self._cache[label]
        result = self._holds(label)
        self._cache[label] = result
        return result

    def _holds(self, label: Label) -> bool:
        text = str(label)
        if text in self.config.conditions:
            return self.config.conditions[text]
        if text in self.config.constraints:
            return True
        target = self.ws.get(label)
        if target is None:
            self.unknown_conditions.add(text)
            return False
        attrs = target.attrs

        def sub(item: str) -> bool:
            return self.holds(self.ws.label(item, label.repo, label.pkg))

        if target.kind == "alias":
            return sub(self.value(attrs["actual"]))
        if target.kind == "config_setting_group":
            if attrs["match_any"]:
                return any(sub(i) for i in attrs["match_any"])
            return all(sub(i) for i in attrs["match_all"])
        if target.kind == "constraint_value":
            return False
        if target.kind != "config_setting":
            self.unknown_conditions.add(f"{text} ({target.kind})")
            return False

        for key, want in self.value(attrs.get("values", {})).items():
            if key == "define":
                name, _, expected = want.partition("=")
                if self.config.defines.get(name) != expected:
                    return False
            elif self.config.values.get(key) != want:
                return False
        for name, expected in self.value(attrs.get("define_values", {})).items():
            if self.config.defines.get(name) != expected:
                return False
        for flag, expected in self.value(attrs.get("flag_values", {})).items():
            if self.flag_value(self.ws.label(flag, label.repo, label.pkg)) != _norm(expected):
                return False
        return all(sub(c) for c in self.value(attrs.get("constraint_values", [])))

    # -- select -------------------------------------------------------------

    def _branch(self, branch: SelectBranch) -> Any:
        matches = []
        for key, value in branch.conditions.items():
            if key == DEFAULT:
                continue
            if self.holds(self.ws.label(key, branch.repo, branch.pkg)):
                matches.append((key, value))
        if matches:
            # Bazel picks the most specific match; ours are few enough that the
            # first one agrees with it unless the values genuinely differ.
            return matches[0][1]
        if DEFAULT in branch.conditions:
            return branch.conditions[DEFAULT]
        return None

    def value(self, value: Any) -> Any:
        """Collapse select() expressions into a plain value."""
        if not isinstance(value, Select):
            return value
        result: Any = None
        for part in value.parts:
            item = self._branch(part) if isinstance(part, SelectBranch) else part
            item = self.value(item)
            if item is None:
                continue
            if result is None:
                result = dict(item) if isinstance(item, dict) else list(item) if isinstance(item, list) else item
            elif isinstance(result, dict):
                result.update(item)
            else:
                result = result + item
        return [] if result is None else result

    def attr(self, target: Target, name: str, default: Any = None) -> Any:
        value = target.attrs.get(name)
        if value is not None:
            value = self.value(value)
        if isinstance(value, (Stub, Recorder)):
            # A constant loaded from a .bzl file of a repository we have no sources for.
            self.unknown_values.add(f"{target.label} {name}")
            value = None
        if value is None:
            return [] if default is None else default
        return value

    # -- traversal ----------------------------------------------------------

    def labels(self, target: Target, name: str) -> list[Label]:
        value = self.attr(target, name)
        if isinstance(value, (str, Label)):
            value = [value]
        if isinstance(value, dict):
            value = list(value)
        return [self.ws.label(item, target.label.repo, target.label.pkg) for item in value if item]

    def closure(self, roots: list[Label], edges: tuple[str, ...]) -> tuple[dict[Label, Target], set[Label]]:
        """All targets reachable from roots, and the labels we have no target for."""
        seen: dict[Label, Target] = {}
        missing: set[Label] = set()
        stack = list(roots)
        while stack:
            label = stack.pop()
            if label in seen or label in missing:
                continue
            target = self.ws.get(label)
            if target is None:
                missing.add(label)
                continue
            seen[label] = target
            for edge in edges:
                stack.extend(self.labels(target, edge))
        return seen, missing
