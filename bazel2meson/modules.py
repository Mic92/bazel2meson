"""Read MODULE.bazel files: who depends on whom, and under which name."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Module:
    name: str = ""
    # Name a repository is referred to inside this module -> module name.
    mapping: dict[str, str] = field(default_factory=dict)
    # use_extension() variable name -> tag name -> keyword arguments of each call.
    tags: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)


class _Extension:
    def __init__(self, calls: dict[str, list[dict[str, Any]]]) -> None:
        self._calls = calls

    def __getattr__(self, tag: str) -> Any:
        def call(**kwargs: Any) -> None:
            self._calls.setdefault(tag, []).append(kwargs)

        return call


def parse(path: Path) -> Module:
    module = Module()

    def declare(name: str = "", repo_name: str = "", **_: Any) -> None:
        module.name = name
        module.mapping[repo_name or name] = name

    def bazel_dep(name: str, repo_name: str | None = "", **_: Any) -> None:
        if repo_name is not None:
            module.mapping[repo_name or name] = name

    def use_extension(bzl: str, name: str, **_: Any) -> _Extension:
        return _Extension(module.tags.setdefault(name, {}))

    def ignore(*_: Any, **__: Any) -> None:
        return None

    env: dict[str, Any] = {
        "module": declare,
        "bazel_dep": bazel_dep,
        "use_extension": use_extension,
        "True": True,
        "False": False,
        "None": None,
    }
    for name in (
        "use_repo", "register_toolchains", "register_execution_platforms", "single_version_override",
        "multiple_version_override", "local_path_override", "git_override", "archive_override", "inject_repo",
        "override_repo", "use_repo_rule", "include", "flag_alias",
    ):  # fmt: skip
        env[name] = ignore
    env["use_repo_rule"] = lambda *_, **__: ignore
    exec(compile(path.read_text(), str(path), "exec"), env)  # noqa: S102
    return module
