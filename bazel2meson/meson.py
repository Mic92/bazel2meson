"""Turn the resolved Bazel graph into meson.build files."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath

from .graph import Resolver
from .pch import PchSpec, Scanner
from .pch import header as pch_header
from .starlark import Label, Target, Workspace
from .syntax import (
    BUILD_ROOT,
    CURRENT_SOURCE_DIR,
    SOURCE_ROOT,
    Expr,
    Value,
    assign,
    call,
    extend,
    render,
    sanitize,
    statement,
    var,
)

CC_KINDS = {"cc_library", "cc_binary"}
# Rule kinds that need no output of their own: their inputs are reached
# through the dependency edges and handled there.
PASSIVE_KINDS = {
    "proto_library",
    "cc_proto_library",
    "cc_grpc_library",
    "alias",
    "label_flag",
    "filegroup",
    "py_binary",
    "py_library",
    "bool_flag",
    "copy_file",
    "expand_template",
}
SOURCE_SUFFIXES = {".cc", ".c", ".cpp", ".S"}
# Directory for headers that Bazel exposes under a different path than they have on disk.
VIRTUAL_INCLUDES = "bazel2meson-includes"
SCRIPTS = "bazel2meson-scripts"
PCH = "bazel2meson-pch"
BIN = "bazel2meson-bin"
EDGES = (
    "deps",
    "actual",
    "srcs",
    "hdrs",
    "malloc",
    "implementation_deps",
    "textual_hdrs",
    "cc_deps",
    "tools",
    "linkstamp",
)


@dataclass
class ProtocPlugin:
    """An extra protoc code generator, applied to the protos certain rules depend on."""

    # protoc-gen-NAME, also the prefix of the --NAME_out option.
    name: str
    # Rule kinds whose deps are the proto_library targets to run on.
    kinds: set[str]
    # Suffixes of the files generated per .proto, sources before headers.
    suffixes: tuple[str, str]
    # Parameters in front of the output directory in --NAME_out.
    options: str = ""


@dataclass
class SystemRepo:
    """An external repository satisfied by an installed library."""

    # dependency() names used for every target of the repository. Prefixes:
    #   lib:NAME            plain library without pkg-config/CMake file
    #   includes:NAME       only the include directories of dependency(NAME)
    #   cmake:PKG:TARGET    CMake package PKG, imported target TARGET
    # ...
    dependencies: list[str] = field(default_factory=list)
    # ... unless the target is listed here ...
    targets: dict[str, list[str]] = field(default_factory=dict)
    # ... or a per-target name can be derived, e.g. "absl_{name}".
    template: str | None = None
    # Template results that do not exist are skipped instead of failing.
    optional: bool = False
    # Preprocessor defines the Bazel build of the library exports to its users.
    defines: list[str] = field(default_factory=list)

    def lookup(self, label: Label) -> list[str]:
        for key in (f"{label.pkg}:{label.name}", label.name):
            if key in self.targets:
                return self.targets[key]
        if self.template is not None:
            name = self.template.format(name=label.name, pkg=label.pkg.replace("/", "_"))
            return [*self.dependencies, ("?" if self.optional else "") + name]
        return self.dependencies


@dataclass
class Settings:
    # Targets to build; the first one names the project's own repository.
    roots: list[Label]
    # repo name -> directory relative to the Meson project root
    repo_dirs: dict[str, str]
    system: dict[str, SystemRepo]
    # Repositories whose targets are dropped from the build.
    ignore: set[str] = field(default_factory=set)
    # Individual targets that are dropped from the build.
    exclude: set[str] = field(default_factory=set)
    # Protos compiled without a Bazel target describing them: (import root,
    # file relative to it, whether to also generate gRPC service stubs).
    extra_protos: list[tuple[str, str, bool]] = field(default_factory=list)
    # Include path used by the sources -> header of a system library.
    forward_headers: dict[str, str] = field(default_factory=dict)
    # Library (repository name) -> how to choose its precompiled header.
    pch: dict[str, PchSpec] = field(default_factory=dict)
    # Source revision reported by the binary.
    revision: str = "0" * 40
    project_name: str = "project"
    version: str = "0"

    @property
    def root(self) -> Label:
        return self.roots[0]


class Emitter:
    def __init__(self, ws: Workspace, resolver: Resolver, settings: Settings) -> None:
        self.ws = ws
        self.rs = resolver
        self.settings = settings
        self.problems: dict[str, set[str]] = defaultdict(set)
        # Meson statements that have to live in a sub-directory so its outputs land
        # at the path C++ includes them from.
        self.subdirs: dict[str, list[str]] = defaultdict(list)
        # Extension points for plugins: translations for further rule kinds,
        # kinds to skip, and protoc code generators.
        self.handlers: dict[str, Callable[[Label, Target], None]] = {"genrule": self.emit_genrule}
        self.passive_kinds: set[str] = set(PASSIVE_KINDS)
        # Rule kind -> names of the files a target of that kind generates,
        # for rules whose outputs other targets refer to by file name.
        self.outputs: dict[str, Callable[[Target], list[str]]] = {
            "genrule": lambda target: list(self.rs.attr(target, "outs")),
        }
        self.protoc_plugins: list[ProtocPlugin] = []
        # Rule kinds compiled like cc_library.
        self.cc_kinds: set[str] = set(CC_KINDS)
        # Rule kinds that, as a root, become a loadable module. Such a module
        # only holds its own sources; everything it depends on goes into one
        # shared library next to it, as modules of one project share state.
        # The value renders the module from its name and arguments.
        self.module_kinds: dict[str, Callable[[Label, dict[str, Value]], Expr]] = {}
        # Extra arguments for that shared library, e.g. where to install it.
        self.common_library_args: dict[str, Value] = {}
        # Executables built to run during the build: label -> Meson variable.
        self.tools: dict[Label, str] = {}
        self.tool_statements: list[str] = []
        # Include directories relative to the source root; Meson adds the
        # same path in the build tree for generated headers.
        self.include_dirs: set[str] = {".", VIRTUAL_INCLUDES}
        # Hand-written Meson code for the top-level file, by template name.
        self.templates: dict[str, str] = {}
        # Generated .cc files, by owning repository.
        self.generated_sources: dict[str, list[Expr]] = defaultdict(list)
        # C/C++ sources written by genrules, compiled only where srcs names them.
        self.genrule_sources: dict[Label, Expr] = {}
        self.emitted_genrules: set[Label] = set()

    # -- paths --------------------------------------------------------------

    def repo_dir(self, repo: str) -> str | None:
        return self.settings.repo_dirs.get(repo)

    def path(self, label: Label) -> str | None:
        """Project-relative path of a source file label."""
        base = self.repo_dir(label.repo)
        root = self.ws.repo_root(label.repo)
        if base is None or root is None:
            return None
        rel = PurePosixPath(label.pkg) / label.name
        path = str(PurePosixPath(base) / rel)
        # Files written when generating count as sources too.
        if not (root / rel).is_file() and path not in self.extra_files:
            return None
        return path

    def directory(self, label: Label) -> str:
        return str(PurePosixPath(self.settings.repo_dirs[label.repo]) / label.pkg)

    # -- traversal ----------------------------------------------------------

    def collect(self) -> None:
        self.targets: dict[Label, Target] = {}
        self.files: set[Label] = set()
        self.system_deps: set[str] = set()
        self.system_defines: set[str] = set()
        # Generated files other than meson.build, relative to the source root.
        self.extra_files: dict[str, str] = {}
        stack = list(self.settings.roots)
        while stack:
            label = stack.pop()
            if label in self.targets or label in self.files:
                continue
            if label.repo in self.settings.ignore or str(label) in self.settings.exclude:
                continue
            system = self.settings.system.get(label.repo)
            if system is not None:
                self.system_deps.update(system.lookup(label))
                self.system_defines.update(system.defines)
                continue
            if self.repo_dir(label.repo) is None:
                self.problems["repository without sources"].add(label.repo)
                continue
            target = self.ws.get(label)
            if target is None:
                if self.path(label) is None and not self.copied(label):
                    producer = self.generated(label)
                    if producer is None:
                        self.problems["label is neither a target nor a file"].add(str(label))
                    else:
                        # Files may be named without naming the rule making them.
                        stack.append(producer)
                self.files.add(label)
                continue
            self.targets[label] = target
            for edge in EDGES:
                stack.extend(self.rs.labels(target, edge))

    def linkopt(self, label: Label, opt: str) -> Value | None:
        """A linker option, None to drop it."""
        if opt.startswith("-fuse-ld"):
            return None
        match = re.search(r"\$\(location ([^)]+)\)", opt)
        if match is None:
            return opt
        path = self.path(self.ws.label(match.group(1), label.repo, label.pkg))
        if path is None:
            # Produced by a rule reading Bazel's workspace status (build id).
            return None
        located = opt[: match.start()] + SOURCE_ROOT / path
        return located + opt[match.end() :] if opt[match.end() :] else located

    def generated(self, label: Label) -> Label | None:
        """The rule in the package that declares the file as output."""
        for other, target in self.ws.targets.items():
            outputs = self.outputs.get(target.kind)
            if outputs and (other.repo, other.pkg) == (label.repo, label.pkg) and label.name in outputs(target):
                return other
        return None

    def copied(self, label: Label) -> bool:
        """Whether the file is written when generating: the output of a
        copy_file or expand_template rule in the package."""
        for other, target in self.ws.targets.items():
            if (other.repo, other.pkg) != (label.repo, label.pkg) or target.kind not in (
                "copy_file",
                "expand_template",
            ):
                continue
            if self.rs.attr(target, "out", "") != label.name:
                continue
            (src,) = self.rs.labels(target, "src" if target.kind == "copy_file" else "template")
            root = self.ws.repo_root(src.repo)
            assert root is not None
            text = (root / src.pkg / src.name).read_text()
            if target.kind == "expand_template":
                for old, new in self.rs.attr(target, "substitutions").items():
                    text = text.replace(old, str(new))
            self.extra_files[f"{self.directory(label)}/{label.name}"] = text
            return True
        return False

    def tool(self, label: Label) -> Expr | None:
        """An executable of the tree that a build step runs."""
        if label in self.tools:
            return var(self.tools[label])
        target = self.ws.get(label)
        if target is None or target.kind != "cc_binary":
            self.problems["build tool is not a cc_binary"].add(str(label))
            return None
        srcs = [self.path(src) for src in self.rs.labels(target, "srcs")]
        for dep in self.rs.labels(target, "deps"):
            system = self.settings.system.get(dep.repo)
            if system is None:
                # Would need the libraries before the code they generate.
                self.problems["build tool depends on libraries of the tree"].add(f"{label}: {dep}")
                return None
            self.system_deps.update(system.lookup(dep))
        name = "tool_" + sanitize(f"{label.pkg}_{label.name}")
        self.tools[label] = name
        self.tool_statements.append(
            assign(
                name,
                call(
                    "executable",
                    name,
                    call("files", sorted(s for s in srcs if s)),
                    include_directories=call("include_directories", var("include_dirs")),
                    dependencies=var("deps"),
                    native=True,
                ),
            )
        )
        return var(name)

    # -- protobuf -----------------------------------------------------------

    def protoc_target(self, root: str, path: str, generators: list[tuple[str, str, tuple[str, str]]]) -> None:
        """Run protoc on one file.

        root is the directory imports are relative to; generated code mirrors
        it in the build tree. generators are (name, options, suffixes) of the
        code generators to run besides C++.
        """
        file = PurePosixPath(path)
        stem = file.name.removesuffix(".proto")
        outputs = [f"{stem}.pb.cc", f"{stem}.pb.h"]
        args: list[Value] = ["--cpp_out=" + BUILD_ROOT / root]
        for name, options, suffixes in generators:
            outputs += [stem + suffix for suffix in suffixes]
            args.append(
                f"--{name}_out={options}:" + BUILD_ROOT / root if options else f"--{name}_out=" + BUILD_ROOT / root
            )
        target = var("proto_" + sanitize(path.removesuffix(".proto")))
        # A package may hold protos in sub-directories without BUILD files.
        self.subdirs[str(file.parent)] += [
            assign(
                target.code,
                call(
                    "custom_target",
                    input=file.name,
                    output=outputs,
                    # protoc wants the input spelled the way the -I roots are: absolute.
                    command=[var("protoc"), var("proto_args"), *args, CURRENT_SOURCE_DIR / file.name],
                    depend_files=var("proto_inputs"),
                ),
            ),
            extend("proto_sources", target),
            extend("proto_headers", [target[i] for i in range(1, len(outputs), 2)]),
            "",
        ]
        self.proto_count += 1

    def emit_protos(self) -> list[str]:
        selected: dict[str, set[Label]] = defaultdict(set)
        for target in self.targets.values():
            for plugin in self.protoc_plugins:
                if target.kind in plugin.kinds:
                    selected[plugin.name].update(self.rs.labels(target, "deps"))

        roots: set[str] = set()
        self.proto_count = 0
        for label, target in sorted(self.targets.items()):
            if target.kind != "proto_library":
                continue
            if target.attrs.get("import_prefix"):
                self.problems["proto_library.import_prefix is not handled"].add(str(label))
            root = self.settings.repo_dirs[label.repo]
            strip = self.rs.attr(target, "strip_import_prefix", "")
            if strip.startswith("/"):
                root = str(PurePosixPath(root) / strip.lstrip("/"))
            elif strip:
                root = str(PurePosixPath(self.directory(label)) / strip)
            roots.add(root)
            generators = [(p.name, p.options, p.suffixes) for p in self.protoc_plugins if label in selected[p.name]]
            for src in self.rs.labels(target, "srcs"):
                path = self.path(src)
                if path is None:
                    self.problems["proto source is not a file"].add(str(src))
                    continue
                self.protoc_target(root, path, generators)
        for root, file, grpc in self.settings.extra_protos:
            roots.add(root)
            self.protoc_target(root, f"{root}/{file}", [("grpc", "", (".grpc.pb.cc", ".grpc.pb.h"))] if grpc else [])
        # protoc names a file after the first root containing it, so nested
        # roots have to come before the directories they live in.
        self.proto_roots = sorted(roots, key=lambda r: (-len(PurePosixPath(r).parts), r))
        return self.proto_roots

    # -- genrule ------------------------------------------------------------

    def emit_genrule(self, label: Label, target: Target) -> None:
        if label in self.emitted_genrules:
            return
        self.emitted_genrules.add(label)
        srcs = self.rs.labels(target, "srcs")
        tools = self.rs.labels(target, "tools")
        outs = list(self.rs.attr(target, "outs"))
        cmd = self.rs.attr(target, "cmd", "")
        out_dir = self.build_path(self.directory(label))

        depends: list[Expr] = []
        programs: list[Expr] = []

        def produced(item: Label) -> str | None:
            """Shell word for a file some genrule writes into the build tree."""
            if item.pkg == label.pkg and item.repo == label.repo and item.name in outs:
                return f"{out_dir}/{shlex.quote(item.name)}"
            producer = self.generated(item)
            if producer is None or self.targets[producer].kind != "genrule":
                return None
            # Meson wants a target defined before it is referred to.
            self.emit_genrule(producer, self.targets[producer])
            depends.append(var(self.genrule_name(producer)))
            return self.build_path(f"{self.directory(item)}/{item.name}")

        def word(item: Label) -> str | None:
            path = self.path(item)
            if path is not None:
                return shlex.quote(path)
            return produced(item)

        def location(match: re.Match[str]) -> str:
            item = self.ws.label(match.group(1), label.repo, label.pkg)
            found = word(item)
            if found is not None:
                return found
            tool = self.ws.get(item)
            if tool is not None and tool.kind == "py_binary":
                main = self.rs.attr(tool, "main") or self.rs.attr(tool, "srcs")[0]
                main = main if isinstance(main, str) else main[0]
                # Bazel puts the repository root on the import path of py_binary.
                root = self.settings.repo_dirs[item.repo]
                script = f"{self.directory(item)}/{main.removeprefix(':')}"
                return f"PYTHONPATH={shlex.quote(root)} python3 {shlex.quote(script)}"
            if tool is not None and tool.kind == "cc_binary":
                program = self.tool(item)
                if program is not None:
                    # Passed as an argument; Meson knows where it was built.
                    programs.append(program)
                    return f'"${len(programs)}"'
            self.problems["genrule: cannot locate"].add(f"{label}: {match.group(1)}")
            return match.group(0)

        cmd = re.sub(r"\./\$\(location ([^)]+)\)", r"$(location \1)", cmd)
        cmd = re.sub(r"\$\((?:location|execpath|rootpath) ([^)]+)\)", location, cmd)
        src_words = [w for w in (word(s) for s in srcs) if w]
        out_words = [f"{out_dir}/{shlex.quote(o)}" for o in outs]
        cmd = cmd.replace("$<", src_words[0] if src_words else "")
        cmd = cmd.replace("$(SRCS)", " ".join(src_words))
        cmd = cmd.replace("$(OUTS)", " ".join(out_words))
        cmd = cmd.replace("$@", out_words[0] if outs else "")
        cmd = cmd.replace("$(@D)", out_dir).replace("$(RULEDIR)", out_dir)
        if re.search(r"(?<!\$)\$\([A-Za-z]", cmd):
            self.problems["genrule: unhandled make variable"].add(str(label))
        cmd = cmd.replace("$$", "$")
        inputs = [p for p in (self.path(i) for i in srcs + tools) if p]
        if self.rs.attr(target, "stamp", 0) == 1:
            # The workspace status, which a build outside of Bazel has none of.
            self.extra_files.setdefault("bazel-out/volatile-status.txt", "")
            self.extra_files.setdefault("bazel-out/stable-status.txt", "")
        name = self.genrule_name(label)
        args = self.script(name, cmd.strip())
        args["command"] = [*args["command"], *programs]
        if depends:
            args["depends"] = depends
        self.subdirs[self.directory(label)] += [
            assign(name, call("custom_target", output=outs, depend_files=[SOURCE_ROOT / p for p in inputs], **args)),
        ]
        compilable = {i for i, o in enumerate(outs) if PurePosixPath(o).suffix in SOURCE_SUFFIXES}
        for i in compilable:
            self.genrule_sources[Label(label.repo, label.pkg, outs[i])] = var(name)[i]
        # Meson would compile sources among the outputs wherever they are listed.
        headers = [var(name)[i] for i in range(len(outs)) if i not in compilable] if compilable else var(name)
        self.subdirs[self.directory(label)] += [extend("generated_headers", headers), ""]

    def genrule_name(self, label: Label) -> str:
        return "gen_" + sanitize(f"{self.directory(label)}/{label.name}")

    @staticmethod
    def build_path(path: str) -> str:
        """Shell word for a path in the build tree, for use in :meth:`script`."""
        return f"${{MESON_BUILD_ROOT}}/{shlex.quote(path)}"

    @staticmethod
    def source_path(path: str) -> str:
        """Shell word for the absolute path of a source file, for use in :meth:`script`."""
        return f"${{MESON_SOURCE_ROOT}}/{shlex.quote(path)}"

    def script(self, name: str, text: str) -> dict[str, Value]:
        """custom_target() arguments that run shell text.

        It runs in the source root, like a Bazel action runs in the exec
        root, with MESON_SOURCE_ROOT, MESON_BUILD_ROOT and PROTO_ARGS set.
        The text goes into a file: Meson turns backslashes in custom_target
        commands into slashes, and a file needs no further quoting.
        """
        self.extra_files[f"{SCRIPTS}/{name}.sh"] = f'set -e\ncd "$MESON_SOURCE_ROOT"\n{text}\n'
        return {"command": ["sh", SOURCE_ROOT / f"{SCRIPTS}/{name}.sh"], "env": var("script_env")}

    # -- C/C++ --------------------------------------------------------------

    def emit(self) -> dict[str, str]:
        self.collect()
        self.emit_protos()
        # Compiled together: a repository, or a root that is a module.
        sources: dict[str | Label, list[str]] = defaultdict(list)
        copts: dict[str | Label, list[str]] = defaultdict(list)
        modules = [
            root
            for root in self.settings.roots
            if root in self.targets and self.targets[root].kind in self.module_kinds
        ]
        defines: list[str] = []
        include_dirs = self.include_dirs
        include_dirs.update(self.proto_roots)
        linkopts: list[Value] = []
        virtual = self.extra_files
        for visible, actual in self.settings.forward_headers.items():
            virtual[f"{VIRTUAL_INCLUDES}/{visible}"] = f"#include <{actual}>\n"

        def add_unique(dest: list[str], items: list[str]) -> None:
            for item in items:
                if item not in dest:
                    dest.append(item)

        generated_srcs: list[tuple[str, Label]] = []
        for label, target in sorted(self.targets.items()):
            if target.kind in self.handlers:
                self.handlers[target.kind](label, target)
                continue
            if target.kind not in self.cc_kinds:
                if target.kind not in self.passive_kinds and not any(
                    target.kind in plugin.kinds for plugin in self.protoc_plugins
                ):
                    self.problems[f"unhandled rule kind {target.kind}"].add(str(label))
                continue
            if target.kind == "cc_binary" and label not in self.settings.roots:
                # A tool the build runs, see tool(); not part of the result.
                continue
            repo_dir = self.settings.repo_dirs[label.repo]
            include_dirs.add(repo_dir)
            unit: str | Label = label if label in modules else label.repo
            for src in self.rs.labels(target, "srcs"):
                if PurePosixPath(src.name).suffix not in SOURCE_SUFFIXES:
                    continue
                path = self.path(src)
                if path is not None:
                    sources[unit].append(path)
                elif isinstance(unit, str):
                    generated_srcs.append((unit, src))
            for stamp in self.rs.labels(target, "linkstamp"):
                # Bazel compiles linkstamps at link time with the workspace
                # status as defines; here the status is fixed when generating.
                path = self.path(stamp)
                wrapper = f"{SCRIPTS}/{sanitize(path or str(stamp))}.cc"
                virtual[wrapper] = (
                    f"#define BUILD_SCM_REVISION {c_string(self.settings.revision)}\n"
                    '#define BUILD_SCM_STATUS "Distribution"\n'
                    f"#include {c_string(path or '')}\n"
                )
                sources[unit].append(wrapper)
            # Bazel splits each copt like a shell would.
            add_unique(copts[unit], [t for c in self.rs.attr(target, "copts") for t in shlex.split(str(c))])
            add_unique(defines, [str(d) for d in self.rs.attr(target, "defines")])
            add_unique(copts[unit], [f"-D{d}" for d in self.rs.attr(target, "local_defines")])
            for opt in self.rs.attr(target, "linkopts"):
                expr = self.linkopt(label, str(opt))
                if expr is not None and render(expr) not in map(render, linkopts):
                    linkopts.append(expr)
            for inc in self.rs.attr(target, "includes"):
                include_dirs.add(str(PurePosixPath(self.directory(label)) / inc))
            strip = self.rs.attr(target, "strip_include_prefix", "")
            prefix = self.rs.attr(target, "include_prefix", "")
            if prefix:
                # Headers are included under a path that does not exist on
                # disk; Bazel symlinks them, we write forwarding headers.
                stripped = PurePosixPath(self.directory(label)) / strip
                for hdr in self.rs.labels(target, "hdrs"):
                    path = self.path(hdr)
                    if path is None or not PurePosixPath(path).is_relative_to(stripped):
                        continue
                    visible = PurePosixPath(prefix) / PurePosixPath(path).relative_to(stripped)
                    virtual[f"{VIRTUAL_INCLUDES}/{visible}"] = f"#include {c_string(path)}\n"
            elif strip:
                base = repo_dir if strip.startswith("/") else self.directory(label)
                include_dirs.add(str(PurePosixPath(base) / strip.lstrip("/")))

        for repo, src in generated_srcs:
            if src in self.genrule_sources:
                self.generated_sources[repo].append(self.genrule_sources[src])
        if not any(path.startswith(VIRTUAL_INCLUDES + "/") for path in virtual):
            include_dirs.discard(VIRTUAL_INCLUDES)
        out: dict[str, str] = dict(virtual)
        for directory, lines in sorted(self.subdirs.items()):
            out[f"{directory}/meson.build"] = "\n".join(lines)

        project = self.settings.project_name
        protoc_generators = [p.name for p in self.protoc_plugins]
        root: list[str] = [
            "# Generated by bazel2meson. Do not edit.",
            statement(
                call(
                    "project",
                    project,
                    "cpp",
                    "c",
                    version=self.settings.version,
                    meson_version=">=1.3",
                    default_options=[
                        "cpp_std=c++20",
                        "c_std=gnu11",
                        "warning_level=0",
                        "buildtype=release",
                        "b_staticpic=true",
                        "default_library=static",
                    ],
                )
            ),
            "",
            assign("proto_roots", self.proto_roots),
            assign("protoc_generators", protoc_generators),
            assign("grpc_protos", any(grpc for _, _, grpc in self.settings.extra_protos)),
            assign("system_deps", sorted(self.system_deps)),
            assign("include_dirs", sorted(include_dirs)),
            template("setup.meson"),
            *self.templates.values(),
            # Meson wants these before the first target, tools included.
            statement(
                call(
                    "add_project_arguments",
                    ["-D" + d for d in [*defines, *sorted(self.system_defines)]],
                    language=["c", "cpp"],
                )
            ),
            *self.tool_statements,
            *(statement(call("subdir", d)) for d in sorted(self.subdirs)),
            "",
            assign("protos_name", f"{sanitize(project)}_protos"),
            template("libraries.meson"),
        ]

        scanner = Scanner(self.ws.repo_root(self.settings.root.repo), sorted(include_dirs))
        main = self.settings.root.repo
        # The project's own code goes by the project name.
        specs = {main if name == project else name: spec for name, spec in self.settings.pch.items()}
        for name in sorted(set(specs) - {unit for unit in sources if isinstance(unit, str)}):
            self.problems["[pch] names a repository without sources"].add(name)

        def compiled(unit: str | Label) -> dict[str, Value]:
            args = cxx_args(copts[unit])
            pch: dict[str, Value] = {}
            spec = specs.get(unit) if isinstance(unit, str) else None
            text = pch_header(spec, scanner, sources[unit]) if spec else None
            if text:
                # Meson wants each precompiled header in a directory of its own.
                path = f"{PCH}/{sanitize(str(unit) or project)}/pch.hh"
                out[path] = text
                pch["cpp_pch"] = path
            return {
                **pch,
                "sources": [
                    call("files", sorted(set(sources[unit]))),
                    *(self.generated_sources[unit] if isinstance(unit, str) else []),
                    var("generated_headers"),
                ],
                "cpp_args": args,
                "c_args": c_args(args),
                "include_directories": var("inc"),
                "dependencies": [var("deps"), var("proto_dep")],
            }

        repos = {unit for unit in sources if isinstance(unit, str)} | set(self.generated_sources)
        if modules:
            # Everything the modules share, in one library next to them.
            for repo in sorted(repos):
                root.append(extend("libs", call("static_library", sanitize(repo or project), **compiled(repo))))
            root.append(
                assign(
                    "common",
                    call(
                        "shared_library",
                        f"{sanitize(project)}_common",
                        link_whole=var("libs"),
                        dependencies=var("deps"),
                        link_args=linkopts,
                        install=True,
                        **self.common_library_args,
                    ),
                )
            )
            for label in modules:
                args = {**compiled(label), "link_with": var("common")}
                args["dependencies"] = var("deps")
                root.append(statement(self.module_kinds[self.targets[label].kind](label, args)))
        else:
            for repo in sorted(repos - {main}):
                root.append(extend("libs", call("static_library", sanitize(repo), **compiled(repo))))
            args: dict[str, Value] = {
                **compiled(main),
                "link_args": linkopts,
                "link_whole": var("libs"),
                "install": True,
            }
            program = self.settings.root.name
            if any(d.split("/")[0] == program for d in self.subdirs):
                # The build tree has a directory with the executable's name
                # for generated files, so the executable moves into one of
                # its own.
                if "cpp_pch" in args:
                    args["cpp_pch"] = f"../{args['cpp_pch']}"
                root += [assign("executable_args", args), statement(call("subdir", BIN))]
                out[f"{BIN}/meson.build"] = statement(call("executable", program, kwargs=var("executable_args")))
            else:
                root.append(statement(call("executable", program, **args)))
        root.append("")
        out["meson.build"] = "\n".join(root)
        return out


def template(name: str) -> str:
    """Hand-written Meson code; its inputs are variables assigned before it."""
    return resources.files(__package__).joinpath("templates", name).read_text()


def c_string(text: str) -> str:
    """A C string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cxx_args(copts: list[str]) -> list[str]:
    # The language standard is a project option; warnings stay warnings
    # because system libraries differ from the versions upstream tests with.
    return [c for c in copts if not c.startswith("-std=") and not c.startswith("-Werror")]


def c_args(copts: list[str]) -> list[str]:
    # Upstream's warning flags are chosen for C++ and partly invalid for C.
    return [c for c in copts if not c.startswith("-W")]


def write(files: dict[str, str], dest: Path) -> None:
    for rel, content in files.items():
        path = dest / rel
        # Rewriting an unchanged header would make ninja rebuild everything
        # that includes it.
        if path.is_file() and path.read_text() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def format_problems(problems: dict[str, set[str]], limit: int = 25) -> str:
    lines = []
    for kind, items in sorted(problems.items()):
        lines.append(f"{kind} ({len(items)}):")
        lines += [f"  {shlex.quote(i)}" for i in sorted(items)[:limit]]
        if len(items) > limit:
            lines.append(f"  ... and {len(items) - limit} more")
    return "\n".join(lines)
