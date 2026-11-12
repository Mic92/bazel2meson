"""A small Bazel loading-phase emulator.

BUILD and .bzl files are Starlark, which is close enough to Python that we can
`exec` them with a hand-written set of builtins.  Upstream's own macros
(`envoy_cc_library`, `api_proto_package`, ...) are executed for real, so the
recorded targets follow upstream when the macros change; only rules coming from
repositories we do not have on disk are replaced by recorders.
"""

from __future__ import annotations

import ast
import builtins
import glob as _glob
import json as _json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class StarlarkError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Label:
    repo: str
    pkg: str
    name: str

    def __str__(self) -> str:
        prefix = f"@{self.repo}" if self.repo else ""
        return f"{prefix}//{self.pkg}:{self.name}"

    # Starlark's Label() API, used by a few macros.
    @property
    def package(self) -> str:
        return self.pkg

    @property
    def workspace_name(self) -> str:
        return self.repo

    @property
    def repo_name(self) -> str:
        return self.repo


class Select:
    """`select()` plus the list/dict concatenations applied to it."""

    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts

    def __add__(self, other: Any) -> Select:
        return Select(self.parts + (other.parts if isinstance(other, Select) else [other]))

    def __radd__(self, other: Any) -> Select:
        return Select([other] + self.parts)

    def __or__(self, other: Any) -> Select:
        return self.__add__(other)

    def __ror__(self, other: Any) -> Select:
        return self.__radd__(other)


@dataclass
class SelectBranch:
    conditions: dict[str, Any]
    no_match_error: str = ""
    # Package the select() was written in; condition labels are relative to it.
    repo: str = ""
    pkg: str = ""


@dataclass
class Target:
    label: Label
    kind: str
    attrs: dict[str, Any]


class Struct:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def to_json(self) -> str:
        return _json.dumps(self.__dict__)


class Stub:
    """Stands in for anything we do not model (providers, attr schemas, ...)."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Stub:
        return Stub(self._name)

    def __getattr__(self, item: str) -> Stub:
        if item.startswith("__"):
            raise AttributeError(item)
        return Stub(f"{self._name}.{item}")

    def __getitem__(self, item: Any) -> Stub:
        return self

    def __iter__(self):
        return iter(())

    def __add__(self, other: Any) -> Any:
        return other

    def __radd__(self, other: Any) -> Any:
        return other


_TYPE_NAMES = {
    str: "string",
    int: "int",
    bool: "bool",
    list: "list",
    tuple: "tuple",
    dict: "dict",
    type(None): "NoneType",
    Select: "select",
    Struct: "struct",
    Label: "Label",
}


def _starlark_type(value: Any) -> str:
    for py, name in _TYPE_NAMES.items():
        if type(value) is py:
            return name
    return "function" if callable(value) else "unknown"


# Python keywords that are plain identifiers in Starlark.
_KEYWORD_IDENT = re.compile(
    r"\b(async|await|nonlocal|global|class|try|except|finally|raise|with|yield|is)\b(?=\s*[=,)])"
)


class _DictViews(ast.NodeTransformer):
    """Starlark's dict.keys()/values()/items() return lists, not views."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("keys", "values", "items") and not node.args:
            return ast.Call(func=ast.Name(id="list", ctx=ast.Load()), args=[node], keywords=[])
        return node


@dataclass
class Repo:
    name: str
    root: Path | None
    # Sub-directory prefixes of other repos nested in this one (e.g. envoy's api/).
    aliases: tuple[str, ...] = ()


@dataclass
class Workspace:
    repos: dict[str, Repo]
    # .bzl files replaced wholesale by our own definitions: label -> symbols
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    targets: dict[Label, Target] = field(default_factory=dict)
    _modules: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    _packages: set[tuple[str, str]] = field(default_factory=set)
    _ctx: list[tuple[str, str]] = field(default_factory=list)
    unknown_rules: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        import posixpath

        def with_or_dict(conditions: dict[Any, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in conditions.items():
                for item in key if isinstance(key, tuple) else (key,):
                    out[item] = value
            return out

        def with_or(conditions: dict[Any, Any], no_match_error: str = "") -> Select:
            repo, pkg = self._ctx[-1]
            return Select([SelectBranch(with_or_dict(conditions), no_match_error, repo, pkg)])

        def config_setting_group(name: str, match_any: list[str] = (), match_all: list[str] = (), **_: Any) -> None:
            self.recorder("config_setting_group")(name=name, match_any=list(match_any), match_all=list(match_all))

        skylib = {
            "lib:selects.bzl": {
                "selects": Struct(with_or=with_or, with_or_dict=with_or_dict, config_setting_group=config_setting_group)
            },
            "lib:paths.bzl": {
                "paths": Struct(
                    join=posixpath.join,
                    basename=posixpath.basename,
                    dirname=posixpath.dirname,
                    normalize=posixpath.normpath,
                    split_extension=posixpath.splitext,
                    replace_extension=lambda path, ext: posixpath.splitext(path)[0] + ext,
                    is_absolute=posixpath.isabs,
                    relativize=lambda path, start: posixpath.relpath(path, start),
                )
            },
            "lib:dicts.bzl": {
                "dicts": Struct(
                    add=lambda *ds, **kw: {k: v for d in (*ds, kw) for k, v in d.items()},
                    omit=lambda d, keys: {k: v for k, v in d.items() if k not in keys},
                    pick=lambda d, keys: {k: v for k, v in d.items() if k in keys},
                )
            },
        }
        for path, symbols in skylib.items():
            self.overrides.setdefault(f"@bazel_skylib//{path}", symbols)

    # -- labels -----------------------------------------------------------

    def canonical_repo(self, name: str) -> str:
        repo = self.repos.get(name)
        if repo is None:
            return name
        return repo.name

    def label(self, text: str | Label, repo: str | None = None, pkg: str | None = None) -> Label:
        if isinstance(text, Label):
            return text
        if repo is None or pkg is None:
            repo, pkg = self._ctx[-1]
        if text.startswith("@@"):
            text = text[1:]
        if text.startswith("@"):
            head, sep, rest = text[1:].partition("//")
            target_repo = self.canonical_repo(head)
            if not sep:
                return Label(target_repo, "", head)
            text = "//" + rest
        else:
            target_repo = repo
        if text.startswith("//"):
            path, sep, name = text[2:].partition(":")
            if not sep:
                name = path.rsplit("/", 1)[-1]
            return Label(target_repo, path, name)
        return Label(target_repo, pkg, text.removeprefix(":"))

    # -- file access ------------------------------------------------------

    def repo_root(self, repo: str) -> Path | None:
        entry = self.repos.get(repo)
        return entry.root if entry else None

    def package_dir(self, repo: str, pkg: str) -> Path | None:
        root = self.repo_root(repo)
        return None if root is None else root / pkg

    def build_file(self, repo: str, pkg: str) -> Path | None:
        directory = self.package_dir(repo, pkg)
        if directory is None:
            return None
        for name in ("BUILD.bazel", "BUILD"):
            if (directory / name).is_file():
                return directory / name
        return None

    # -- evaluation -------------------------------------------------------

    def _globals(self, repo: str, pkg: str, is_build: bool) -> dict[str, Any]:
        ws = self

        def load(module: str, *names: str, **aliases: str) -> None:
            symbols = ws.load_bzl(ws.label(module, repo, pkg))
            frame = env
            for local, remote in [(n, n) for n in names] + list(aliases.items()):
                frame[local] = symbols[remote]

        def select(conditions: dict[str, Any], no_match_error: str = "") -> Select:
            cur_repo, cur_pkg = ws._ctx[-1]
            return Select([SelectBranch(conditions, no_match_error, cur_repo, cur_pkg)])

        def glob(include: list[str] = (), exclude: list[str] = (), allow_empty: bool = True, **_: Any) -> list[str]:
            cur_repo, cur_pkg = ws._ctx[-1]
            base = ws.package_dir(cur_repo, cur_pkg)
            if base is None:
                return []
            found: set[str] = set()
            for pattern in include:
                found.update(_glob.glob(pattern, root_dir=base, recursive=True))
            for pattern in exclude:
                found.difference_update(_glob.glob(pattern, root_dir=base, recursive=True))
            # Bazel globs do not cross into sub-packages.
            result = []
            for item in sorted(found):
                path = base / item
                if path.is_dir():
                    continue
                parent = path.parent
                crossed = False
                while parent != base:
                    if (parent / "BUILD").is_file() or (parent / "BUILD.bazel").is_file():
                        crossed = True
                        break
                    parent = parent.parent
                if not crossed:
                    result.append(item)
            return result

        def fail(*args: Any, **_: Any) -> None:
            raise StarlarkError(" ".join(str(a) for a in args))

        def make_label(text: str) -> Label:
            return ws.label(text, repo, pkg)

        env: dict[str, Any] = {
            "__builtins__": {
                name: getattr(builtins, name)
                for name in (
                    "len",
                    "str",
                    "int",
                    "bool",
                    "list",
                    "dict",
                    "tuple",
                    "range",
                    "sorted",
                    "reversed",
                    "enumerate",
                    "zip",
                    "any",
                    "all",
                    "min",
                    "max",
                    "hasattr",
                    "getattr",
                    "repr",
                    "print",
                    "isinstance",
                    "set",
                    "abs",
                    "float",
                    "hash",
                )
            },
            "True": True,
            "False": False,
            "None": None,
            "load": load,
            "select": select,
            "glob": glob,
            "fail": fail,
            "struct": Struct,
            "type": _starlark_type,
            "Label": make_label,
            "json": Struct(decode=_json.loads, encode=_json.dumps, encode_indent=_json.dumps),
            "depset": lambda *a, **k: [],
            "native": NativeModule(ws),
            "package_name": lambda: ws._ctx[-1][1],
            "repository_name": lambda: "@" + ws._ctx[-1][0],
            "repo_name": lambda: ws._ctx[-1][0],
            "module_name": lambda: ws._ctx[-1][0],
        }
        for name in (
            "rule",
            "provider",
            "aspect",
            "attr",
            "repository_rule",
            "module_extension",
            "tag_class",
            "config_common",
            "transition",
            "config",
            "CcInfo",
            "DefaultInfo",
            "OutputGroupInfo",
            "platform_common",
            "cc_common",
            "apple_common",
            "coverage_common",
            "testing",
            "analysis_test_transition",
            "exec_group",
            "PyInfo",
            "ProtoInfo",
            "JavaInfo",
            "RunEnvironmentInfo",
            "InstrumentedFilesInfo",
            "macro",
            "subrule",
            "configuration_field",
            "CcToolchainConfigInfo",
            "DebugPackageInfo",
            "java_common",
            "visibility",
        ):
            env[name] = Stub(name)
        # `rule()` results are called from BUILD files/macros: record them.
        env["rule"] = lambda *a, **k: ws.recorder("custom_rule")
        if is_build:
            native = env["native"]
            for name in NATIVE_RULES:
                env[name] = getattr(native, name)
            env["package"] = lambda **_: None
            env["licenses"] = lambda *_: None
            env["exports_files"] = lambda *a, **k: None
            env["package_group"] = lambda **_: None
        return env

    def _exec(self, path: Path, env: dict[str, Any]) -> None:
        source = path.read_text()
        source = _KEYWORD_IDENT.sub(lambda m: m.group(1) + "_", source)
        try:
            tree = _DictViews().visit(ast.parse(source, str(path)))
            code = compile(ast.fix_missing_locations(tree), str(path), "exec")
        except SyntaxError as err:
            raise StarlarkError(f"{path}: {err}") from err
        exec(code, env)  # noqa: S102

    def load_bzl(self, label: Label) -> dict[str, Any]:
        key = (label.repo, f"{label.pkg}/{label.name}" if label.pkg else label.name)
        if key in self._modules:
            return self._modules[key]
        override = self.overrides.get(str(label))
        root = self.repo_root(label.repo)
        path = None if root is None else root / key[1]
        symbols: dict[str, Any]
        if override is not None:
            symbols = AutoStubModule(self, override)
        elif path is None or not path.is_file():
            symbols = AutoStubModule(self, {})
        else:
            symbols = self._globals(label.repo, label.pkg, is_build=False)
            self._modules[key] = symbols
            self._exec(path, symbols)
        self._modules[key] = symbols
        return symbols

    def load_package(self, repo: str, pkg: str) -> bool:
        if (repo, pkg) in self._packages:
            return True
        path = self.build_file(repo, pkg)
        if path is None:
            return False
        self._packages.add((repo, pkg))
        self._ctx.append((repo, pkg))
        try:
            self._exec(path, self._globals(repo, pkg, is_build=True))
        finally:
            self._ctx.pop()
        return True

    def get(self, label: Label) -> Target | None:
        self.load_package(label.repo, label.pkg)
        return self.targets.get(label)

    # -- rule recording ---------------------------------------------------

    def recorder(self, kind: str):
        def record(*args: Any, **attrs: Any) -> Any:
            name = attrs.get("name")
            # Symbols loaded from files we cannot read may be helper functions
            # rather than rules; those are not called like a rule.
            if args or not isinstance(name, str) or not self._ctx:
                return Stub(kind)
            repo, pkg = self._ctx[-1]
            self.targets[Label(repo, pkg, name)] = Target(Label(repo, pkg, name), kind, attrs)
            return None

        record.__name__ = kind
        return record


NATIVE_RULES = (
    "cc_library",
    "cc_binary",
    "cc_test",
    "cc_import",
    "cc_proto_library",
    "proto_library",
    "alias",
    "filegroup",
    "genrule",
    "config_setting",
    "constraint_setting",
    "constraint_value",
    "platform",
    "label_flag",
    "label_setting",
    "sh_binary",
    "sh_test",
    "sh_library",
    "py_binary",
    "py_library",
    "py_test",
    "test_suite",
    "java_library",
    "java_binary",
    "java_proto_library",
    "toolchain",
    "toolchain_type",
    "objc_library",
    "cc_shared_library",
    "cc_static_library",
    "java_lite_proto_library",
    "py_proto_library",
    "java_import",
    "java_plugin",
    "exports_files_rule",
    "cc_toolchain",
    "cc_toolchain_suite",
    "java_test",
    "starlark_doc_extract",
)


class NativeModule:
    def __init__(self, ws: Workspace) -> None:
        self._ws = ws

    def __getattr__(self, name: str) -> Any:
        ws = self._ws
        if name == "package_name":
            return lambda: ws._ctx[-1][1]
        if name in ("repository_name", "repo_name", "module_name"):
            prefix = "@" if name == "repository_name" else ""
            return lambda: prefix + ws._ctx[-1][0]
        if name == "existing_rule":
            return lambda n: ws.targets.get(Label(*ws._ctx[-1], n))
        if name == "existing_rules":
            return dict
        if name in ("package", "licenses", "exports_files", "package_group", "register_toolchains"):
            return lambda *a, **k: None
        if name == "glob":
            return ws._globals(*ws._ctx[-1], is_build=False)["glob"]
        if name == "module_version":
            return lambda: ""
        return ws.recorder(name)


class AutoStubModule(dict):
    """A .bzl file we cannot read: every symbol becomes a rule recorder."""

    def __init__(self, ws: Workspace, known: dict[str, Any]) -> None:
        super().__init__(known)
        self._ws = ws

    def __missing__(self, key: str) -> Any:
        self._ws.unknown_rules[key] = self._ws.unknown_rules.get(key, 0) + 1
        value = self._ws.recorder(key)
        self[key] = value
        return value
