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
import functools
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

    def _absorb(self, other: Any) -> Stub:
        return self

    def _unordered(self, other: Any) -> bool:
        return False

    __lt__ = __le__ = __gt__ = __ge__ = _unordered

    # Values computed from something unknown stay unknown.
    __sub__ = __rsub__ = __mul__ = __rmul__ = _absorb
    __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = _absorb


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


class _Depset(list):
    def to_list(self) -> list[Any]:
        return list(self)


def _depset(direct: Any = (), transitive: Any = (), **_: Any) -> _Depset:
    out = _Depset()
    for item in [*(direct or ()), *(i for t in transitive or () for i in t)]:
        if item not in out:
            out.append(item)
    return out


# Python keywords that are plain identifiers in Starlark.
_KEYWORD_IDENT = re.compile(
    r"\b(async|await|nonlocal|global|class|try|except|finally|raise|with|yield|is)\b(?=\s*[=,)])"
)


_DICT_VIEW = re.compile(r"\.(keys|values|items)\(\)")


class _DictViews(ast.NodeTransformer):
    """Starlark's dict.keys()/values()/items() return lists, not views."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("keys", "values", "items") and not node.args:
            name = ast.copy_location(ast.Name(id="list", ctx=ast.Load()), node)
            return ast.copy_location(ast.Call(func=name, args=[node], keywords=[]), node)
        return node


@dataclass
class Repository:
    """A repository declared by a module extension."""

    kind: str
    attrs: dict[str, Any]
    # Repository the declaring extension lives in; its labels are relative to it.
    origin: str


class ModuleExtension:
    def __init__(self, implementation: Any = None, **_: Any) -> None:
        self.implementation = implementation


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

    # repo -> {name used inside that repo: canonical repo name}
    mappings: dict[str, dict[str, str]] = field(default_factory=dict)
    # Labels that stand for another label, e.g. targets of Bazel's generated
    # "*_deps" repositories that a module's extension points back at the root.
    redirects: dict[str, str] = field(default_factory=dict)

    targets: dict[Label, Target] = field(default_factory=dict)
    # Filled by run_extension().
    repositories: dict[str, Repository] = field(default_factory=dict)
    repository_origin: str | None = None
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

    def canonical_repo(self, name: str, context: str) -> str:
        name = self.mappings.get(context, {}).get(name) or self.mappings.get("*", {}).get(name, name)
        repo = self.repos.get(name)
        return name if repo is None else repo.name

    def label(self, text: str | Label, repo: str | None = None, pkg: str | None = None) -> Label:
        if isinstance(text, Label):
            return text
        if repo is None or pkg is None:
            repo, pkg = self._ctx[-1]
        if text.startswith("@@"):
            text = text[1:]
        if text.startswith("@"):
            head, sep, rest = text[1:].partition("//")
            target_repo = self.canonical_repo(head, repo)
            if not sep:
                return self._redirect(Label(target_repo, "", head))
            text = "//" + rest
        else:
            target_repo = repo
        if text.startswith("//"):
            path, sep, name = text[2:].partition(":")
            if not sep:
                name = path.rsplit("/", 1)[-1]
            return self._redirect(Label(target_repo, path, name))
        return Label(target_repo, pkg, text.removeprefix(":"))

    def _redirect(self, label: Label) -> Label:
        target = self.redirects.get(str(label))
        return label if target is None else self.label(target, "", "")

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

        @functools.cache
        def is_package(directory: Path) -> bool:
            return (directory / "BUILD").is_file() or (directory / "BUILD.bazel").is_file()

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
                while parent != base and not is_package(parent):
                    parent = parent.parent
                if parent == base:
                    result.append(item)
            return result

        def module_field(field: str) -> str | None:
            """A field of module() in the MODULE.bazel of the current repository."""
            root = ws.repo_root(ws._ctx[-1][0])
            if root is None or not (root / "MODULE.bazel").is_file():
                return None
            found = re.search(rf"\bmodule\([^)]*?\b{field}\s*=\s*\"([^\"]*)\"", (root / "MODULE.bazel").read_text())
            return found.group(1) if found else None

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
            "module_version": lambda: module_field("version"),
            "fail": fail,
            "struct": Struct,
            "type": _starlark_type,
            "Label": make_label,
            "json": Struct(decode=_json.loads, encode=_json.dumps, encode_indent=_json.dumps),
            "depset": _depset,
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
        env["repository_rule"] = lambda *a, **k: ws.recorder("custom_rule")
        env["module_extension"] = ModuleExtension
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
            # Walking the tree costs more than compiling; few files need it.
            tree = _DictViews().visit(ast.parse(source, str(path))) if _DICT_VIEW.search(source) else source
            code = compile(tree, str(path), "exec")
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
            # `foo = rule(...)`: the rule is known by the name it is bound to.
            for name, value in symbols.items():
                if isinstance(value, Recorder) and value._kind == "custom_rule":
                    value._kind = name.removeprefix("_")
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

    def run_extension(self, bzl: str, name: str) -> bool:
        """Evaluate a module extension, collecting the repositories it declares."""
        label = self.label(bzl, "", "")
        extension = self.load_bzl(label).get(name)
        if not isinstance(extension, ModuleExtension) or extension.implementation is None:
            return False
        self._ctx.append((label.repo, label.pkg))
        self.repository_origin = label.repo
        try:
            extension.implementation(Stub("module_ctx"))
        finally:
            self.repository_origin = None
            self._ctx.pop()
        return True

    def recorder(self, kind: str) -> Recorder:
        return Recorder(self, kind)


class Recorder:
    """A rule: calling it declares a target in the package being evaluated."""

    def __init__(self, ws: Workspace, kind: str, guessed: bool = False) -> None:
        self._ws = ws
        self._kind = kind
        # From a .bzl file we cannot read; may be a macro that returns something.
        self._guessed = guessed
        self.__name__ = kind

    def __call__(self, *args: Any, **attrs: Any) -> Any:
        ws = self._ws
        name = attrs.get("name")
        if ws.repository_origin is not None:
            # Called from a module extension: this declares a repository.
            if isinstance(name, str):
                ws.repositories.setdefault(name, Repository(self._kind, attrs, ws.repository_origin))
            return None
        # Symbols loaded from files we cannot read may be helper functions
        # or structs instead of rules; those are not called like a rule.
        if args or not isinstance(name, str) or not ws._ctx:
            return Stub(self._kind)
        repo, pkg = ws._ctx[-1]
        ws.targets[Label(repo, pkg, name)] = Target(Label(repo, pkg, name), self._kind, attrs)
        return Stub(self._kind) if self._guessed else None

    def __getattr__(self, item: str) -> Stub:
        if item.startswith("__"):
            raise AttributeError(item)
        return Stub(f"{self._kind}.{item}")


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
        if name in ("glob", "module_version"):
            return ws._globals(*ws._ctx[-1], is_build=False)[name]
        return ws.recorder(name)


class AutoStubModule(dict):
    """A .bzl file we cannot read: every symbol becomes a rule recorder."""

    def __init__(self, ws: Workspace, known: dict[str, Any]) -> None:
        super().__init__(known)
        self._ws = ws

    def __missing__(self, key: str) -> Any:
        self._ws.unknown_rules[key] = self._ws.unknown_rules.get(key, 0) + 1
        value = Recorder(self._ws, key, guessed=True)
        self[key] = value
        return value
