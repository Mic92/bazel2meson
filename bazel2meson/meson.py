"""Turn the resolved Bazel graph into meson.build files."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .graph import Resolver
from .starlark import Label, Target, Workspace

CC_KINDS = {"cc_library", "cc_binary"}
PGV_KINDS = {"pgv_cc_proto_library", "cc_proto_gen_validate"}
PROTO_CC_KINDS = {"cc_proto_library", "cc_grpc_library"} | PGV_KINDS
SOURCE_SUFFIXES = {".cc", ".c", ".cpp", ".S"}
EDGES = ("deps", "actual", "srcs", "hdrs", "malloc", "implementation_deps", "textual_hdrs", "cc_deps", "tools")


@dataclass
class SystemRepo:
    """An external repository satisfied by an installed library."""

    # dependency() names used for every target of the repository; a "lib:"
    # prefix looks for a plain library that ships no pkg-config/CMake file ...
    dependencies: list[str] = field(default_factory=list)
    # ... unless the target is listed here ...
    targets: dict[str, list[str]] = field(default_factory=dict)
    # ... or a per-target name can be derived, e.g. "absl_{name}".
    template: str | None = None
    # Template results that do not exist are skipped instead of failing.
    optional: bool = False

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
    root: Label
    # repo name -> directory relative to the Meson project root
    repo_dirs: dict[str, str]
    system: dict[str, SystemRepo]
    # Repositories whose targets are dropped from the build.
    ignore: set[str] = field(default_factory=set)
    project_name: str = "envoy"
    version: str = "0"


def quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def array(items: list[str], indent: str = "  ") -> str:
    if not items:
        return "[]"
    return "[\n" + "".join(f"{indent}{quote(i)},\n" for i in items) + indent[:-2] + "]"


def ident(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


class Emitter:
    def __init__(self, ws: Workspace, resolver: Resolver, settings: Settings) -> None:
        self.ws = ws
        self.rs = resolver
        self.settings = settings
        self.problems: dict[str, set[str]] = defaultdict(set)
        # Meson code that has to live in a sub-directory so its outputs land
        # at the path C++ includes them from.
        self.subdirs: dict[str, list[str]] = defaultdict(list)

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
        if not (root / rel).is_file():
            return None
        return str(PurePosixPath(base) / rel)

    def directory(self, label: Label) -> str:
        return str(PurePosixPath(self.settings.repo_dirs[label.repo]) / label.pkg)

    # -- traversal ----------------------------------------------------------

    def collect(self) -> None:
        self.targets: dict[Label, Target] = {}
        self.files: set[Label] = set()
        self.system_deps: set[str] = set()
        stack = [self.settings.root]
        while stack:
            label = stack.pop()
            if label in self.targets or label in self.files:
                continue
            if label.repo in self.settings.ignore:
                continue
            system = self.settings.system.get(label.repo)
            if system is not None:
                self.system_deps.update(system.lookup(label))
                continue
            if self.repo_dir(label.repo) is None:
                self.problems["repository without sources"].add(label.repo)
                continue
            target = self.ws.get(label)
            if target is None:
                if self.path(label) is None:
                    self.problems["label is neither a target nor a file"].add(str(label))
                self.files.add(label)
                continue
            self.targets[label] = target
            for edge in EDGES:
                stack.extend(self.rs.labels(target, edge))

    # -- protobuf -----------------------------------------------------------

    def emit_protos(self) -> list[str]:
        validated: set[Label] = set()
        for target in self.targets.values():
            if target.kind in PGV_KINDS:
                validated.update(self.rs.labels(target, "deps"))

        roots: set[str] = set()
        count = 0
        for label, target in sorted(self.targets.items()):
            if target.kind != "proto_library":
                continue
            if target.attrs.get("import_prefix"):
                self.problems["proto_library.import_prefix is not handled"].add(str(label))
            # Directory the .proto files are imported relative to; generated
            # code mirrors it in the build tree.
            repo_dir = self.settings.repo_dirs[label.repo]
            strip = self.rs.attr(target, "strip_import_prefix", "")
            if strip.startswith("/"):
                repo_dir = str(PurePosixPath(repo_dir) / strip.lstrip("/"))
            elif strip:
                repo_dir = str(PurePosixPath(self.directory(label)) / strip)
            roots.add(repo_dir)
            for src in self.rs.labels(target, "srcs"):
                path = self.path(src)
                if path is None:
                    self.problems["proto source is not a file"].add(str(src))
                    continue
                # A package may hold protos in sub-directories without BUILD files.
                lines = self.subdirs[str(PurePosixPath(path).parent)]
                stem = PurePosixPath(path).name.removesuffix(".proto")
                outputs = [f"{stem}.pb.cc", f"{stem}.pb.h"]
                args = ["proto_args", f"'--cpp_out=' + meson.project_build_root() / {quote(repo_dir)}"]
                if label in validated:
                    outputs += [f"{stem}.pb.validate.cc", f"{stem}.pb.validate.h"]
                    args.append(f"'--validate_out=lang=cc:' + meson.project_build_root() / {quote(repo_dir)}")
                var = "proto_" + ident(path.removesuffix(".proto"))
                lines.append(
                    f"{var} = custom_target(\n"
                    f"  input: {quote(PurePosixPath(path).name)},\n"
                    f"  output: {array(outputs, '    ')},\n"
                    f"  command: [protoc, {', '.join(args)}, '@INPUT@'],\n"
                    f"  depend_files: proto_inputs,\n"
                    f")\n"
                    f"proto_sources += {var}\n"
                    f"proto_headers += [{', '.join(f'{var}[{i}]' for i in range(1, len(outputs), 2))}]\n"
                )
                count += 1
        self.proto_roots = sorted(roots)
        self.proto_count = count
        return self.proto_roots

    # -- genrule ------------------------------------------------------------

    def emit_genrule(self, label: Label, target: Target) -> None:
        srcs = self.rs.labels(target, "srcs")
        tools = self.rs.labels(target, "tools")
        outs = self.rs.attr(target, "outs")
        cmd = self.rs.attr(target, "cmd", "")
        out_dir = f"@BUILD_ROOT@/{self.directory(label)}"

        def location(match: re.Match[str]) -> str:
            item = self.ws.label(match.group(1), label.repo, label.pkg)
            path = self.path(item)
            if path is not None:
                return f"@SOURCE_ROOT@/{path}"
            tool = self.ws.get(item)
            if tool is not None and tool.kind == "py_binary":
                main = self.rs.attr(tool, "main") or self.rs.attr(tool, "srcs")[0]
                main = main if isinstance(main, str) else main[0]
                return f"python3 @SOURCE_ROOT@/{self.directory(item)}/{main.removeprefix(':')}"
            self.problems["genrule: cannot locate"].add(f"{label}: {match.group(1)}")
            return match.group(0)

        cmd = re.sub(r"\./\$\(location ([^)]+)\)", r"$(location \1)", cmd)
        cmd = re.sub(r"\$\((?:location|execpath|rootpath) ([^)]+)\)", location, cmd)
        src_paths = [p for p in (self.path(s) for s in srcs) if p]
        cmd = cmd.replace("$<", f"@SOURCE_ROOT@/{src_paths[0]}" if src_paths else "")
        cmd = cmd.replace("$(SRCS)", " ".join(f"@SOURCE_ROOT@/{p}" for p in src_paths))
        cmd = cmd.replace("$@", f"{out_dir}/{outs[0]}" if outs else "")
        cmd = cmd.replace("$(@D)", out_dir).replace("$(RULEDIR)", out_dir)
        if re.search(r"(?<!\$)\$\([A-Za-z]", cmd):
            self.problems["genrule: unhandled make variable"].add(str(label))
        cmd = cmd.replace("$$", "$")
        inputs = src_paths + [p for p in (self.path(t) for t in tools) if p]
        var = "gen_" + ident(f"{self.directory(label)}/{label.name}")
        self.subdirs[self.directory(label)].append(
            f"{var} = custom_target(\n"
            f"  output: {array(list(outs), '    ')},\n"
            f"  command: ['sh', '-c', {quote('cd @SOURCE_ROOT@ && ' + cmd.strip())}],\n"
            f"  depend_files: files({array(['/'.join(['..'] * len(PurePosixPath(self.directory(label)).parts)) + '/' + p for p in inputs], '    ')}),\n"
            f")\n"
            f"generated_sources += {var}\n"
        )

    # -- C/C++ --------------------------------------------------------------

    def emit(self) -> dict[str, str]:
        self.collect()
        self.emit_protos()

        sources: dict[str, list[str]] = defaultdict(list)
        copts: dict[str, list[str]] = defaultdict(list)
        defines: list[str] = []
        include_dirs: set[str] = {"."}
        linkopts: list[str] = []

        def add_unique(dest: list[str], items: list[str]) -> None:
            for item in items:
                if item not in dest:
                    dest.append(item)

        for label, target in sorted(self.targets.items()):
            if target.kind == "genrule":
                self.emit_genrule(label, target)
                continue
            if target.kind not in CC_KINDS:
                if target.kind not in PROTO_CC_KINDS | {
                    "proto_library",
                    "alias",
                    "label_flag",
                    "filegroup",
                    "py_binary",
                    "bool_flag",
                }:
                    self.problems[f"unhandled rule kind {target.kind}"].add(str(label))
                continue
            repo_dir = self.settings.repo_dirs[label.repo]
            include_dirs.add(repo_dir)
            for src in self.rs.labels(target, "srcs"):
                if PurePosixPath(src.name).suffix not in SOURCE_SUFFIXES:
                    continue
                path = self.path(src)
                if path is not None:
                    sources[label.repo].append(path)
            add_unique(copts[label.repo], [str(c) for c in self.rs.attr(target, "copts")])
            add_unique(defines, [str(d) for d in self.rs.attr(target, "defines")])
            add_unique(copts[label.repo], [f"-D{d}" for d in self.rs.attr(target, "local_defines")])
            add_unique(linkopts, [str(o) for o in self.rs.attr(target, "linkopts")])
            for inc in self.rs.attr(target, "includes"):
                include_dirs.add(str(PurePosixPath(self.directory(label)) / inc))
            strip = self.rs.attr(target, "strip_include_prefix", "")
            if strip:
                base = repo_dir if strip.startswith("/") else self.directory(label)
                include_dirs.add(str(PurePosixPath(base) / strip.lstrip("/")))

        out: dict[str, str] = {}
        for directory, lines in sorted(self.subdirs.items()):
            out[f"{directory}/meson.build"] = "\n".join(lines)

        root: list[str] = [
            "# Generated by bazel2meson. Do not edit.",
            f"project({quote(self.settings.project_name)}, 'cpp', 'c',",
            f"  version: {quote(self.settings.version)},",
            "  meson_version: '>=1.3',",
            "  default_options: ['cpp_std=c++20', 'c_std=gnu11', 'warning_level=0', 'buildtype=release',",
            "                    'b_staticpic=true', 'default_library=static'],",
            ")",
            "",
            "protoc = find_program('protoc')",
            "protoc_gen_validate = find_program('protoc-gen-validate')",
            f"proto_roots = {array(self.proto_roots)}",
            "proto_args = ['--plugin=protoc-gen-validate=' + protoc_gen_validate.full_path()]",
            "foreach root : proto_roots",
            "  proto_args += '-I' + meson.project_source_root() / root",
            "endforeach",
            "proto_inputs = []",
            "proto_sources = []",
            "proto_headers = []",
            "generated_sources = []",
            "",
            f"system_deps = {array(sorted(self.system_deps))}",
            "deps = [dependency('threads')]",
            "foreach name : system_deps",
            "  if name.startswith('lib:')",
            "    deps += meson.get_compiler('cpp').find_library(name.substring(4))",
            "  elif name.startswith('?')",
            "    deps += dependency(name.substring(1), required: false)",
            "  else",
            "    deps += dependency(name)",
            "  endif",
            "endforeach",
            "",
        ]
        root += [f"subdir({quote(d)})" for d in sorted(self.subdirs)]
        root += [
            "",
            f"inc = include_directories({array(sorted(include_dirs))})",
            f"add_project_arguments({array(['-D' + d for d in defines])}, language: ['c', 'cpp'])",
            "",
            "protos = static_library('envoy_protos', proto_sources,",
            "  include_directories: inc, dependencies: deps)",
            "proto_dep = declare_dependency(link_whole: protos, sources: proto_headers)",
            "",
            "libs = []",
        ]
        for repo in sorted(sources):
            if repo == self.settings.root.repo:
                continue
            name = ident(repo)
            args = [c for c in copts[repo] if not c.startswith("-std=")]
            root += [
                f"libs += static_library({quote(name)},",
                f"  files({array(sorted(set(sources[repo])), '    ')}),",
                f"  cpp_args: {array(args, '    ')},",
                f"  c_args: {array(args, '    ')},",
                "  include_directories: inc, dependencies: deps + [proto_dep])",
                "",
            ]
        main = self.settings.root.repo
        args = [c for c in copts[main] if not c.startswith("-std=")]
        root += [
            f"executable({quote(self.settings.project_name)},",
            f"  files({array(sorted(set(sources[main])), '    ')}),",
            "  generated_sources,",
            f"  cpp_args: {array(args, '    ')},",
            f"  c_args: {array(args, '    ')},",
            f"  link_args: {array([o for o in linkopts if not o.startswith('-fuse-ld')], '    ')},",
            "  include_directories: inc,",
            "  dependencies: deps + [proto_dep],",
            "  link_whole: libs,",
            "  install: true,",
            ")",
            "",
        ]
        out["meson.build"] = "\n".join(root)
        return out


def write(files: dict[str, str], dest: Path) -> None:
    for rel, content in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def format_problems(problems: dict[str, set[str]]) -> str:
    lines = []
    for kind, items in sorted(problems.items()):
        lines.append(f"{kind} ({len(items)}):")
        lines += [f"  {shlex.quote(i)}" for i in sorted(items)[:25]]
        if len(items) > 25:
            lines.append(f"  ... and {len(items) - 25} more")
    return "\n".join(lines)
