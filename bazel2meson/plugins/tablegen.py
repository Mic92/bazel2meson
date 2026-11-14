"""LLVM TableGen rules: gentbl_cc_library, gentbl_filegroup and td_library.

The TableGen tools and LLVM's own .td files come from the installed LLVM;
targets of the LLVM repository are expected to be mapped to system libraries.
"""

from __future__ import annotations

from functools import partial
from pathlib import PurePosixPath

from ..meson import Emitter, template
from ..starlark import Label, Target
from ..syntax import SOURCE_ROOT, Value, assign, call, extend, sanitize, var


def include_dir(em: Emitter, label: Label, inc: str) -> str:
    """LLVM's rules take a leading slash as relative to the repository."""
    base = em.settings.repo_dirs[label.repo] if inc.startswith("/") else em.directory(label)
    return str(PurePosixPath(base) / inc.lstrip("/"))


def td_includes(em: Emitter, label: Label, seen: set[Label]) -> list[str]:
    """Include directories of a td_library and the ones it depends on."""
    target = em.targets.get(label)
    if target is None or label in seen:
        return []
    seen.add(label)
    dirs = [include_dir(em, label, inc) for inc in em.rs.attr(target, "includes")]
    for dep in em.rs.labels(target, "deps"):
        dirs += td_includes(em, dep, seen)
    return dirs


def tbl_outs(em: Emitter, target: Target) -> list[tuple[str, list[str]]]:
    """(output, options) pairs."""
    outs = em.rs.attr(target, "tbl_outs")
    # Either {output: options} or the older [(options, output)].
    return list(outs.items()) if isinstance(outs, dict) else [(out, opts) for opts, out in outs]


def gentbl(em: Emitter, headers: bool, label: Label, target: Target) -> None:
    """headers: the outputs are included by C++ sources, which then wait for them."""
    em.templates.setdefault("tablegen", template("tablegen.meson"))
    package = PurePosixPath(em.directory(label))
    pairs = tbl_outs(em, target)
    (tool,) = em.rs.labels(target, "tblgen")
    # LLVM's own generators are installed; a project's are built first.
    program = var("tblgen")[tool.name] if tool.repo in em.settings.system else em.tool(tool)
    if program is None:
        return
    (td_file,) = em.rs.labels(target, "td_file")
    td_path = em.path(td_file)
    if td_path is None:
        em.problems["gentbl: td_file is not a file"].add(str(label))
        return
    dirs = [em.settings.repo_dirs[label.repo], str(package), str(PurePosixPath(td_path).parent)]
    dirs += [include_dir(em, label, inc) for inc in em.rs.attr(target, "includes")]
    for dep in em.rs.labels(target, "deps"):
        dirs += td_includes(em, dep, set())
    includes: list[Value] = ["-I" + SOURCE_ROOT / d for d in dict.fromkeys(dirs)]
    if headers:
        em.include_dirs.add(str(package / em.rs.attr(target, "strip_include_prefix", "")))
    for out, opts in pairs:
        path = package / out
        name = "tblgen_" + sanitize(str(path))
        em.subdirs[str(path.parent)] += [
            assign(
                name,
                call(
                    "custom_target",
                    output=path.name,
                    depfile=path.name + ".d",
                    command=[
                        program,
                        *opts,
                        *includes,
                        var("tblgen_includes"),
                        SOURCE_ROOT / td_path,
                        "--write-if-changed",
                        "-d",
                        "@DEPFILE@",
                        "-o",
                        "@OUTPUT@",
                    ],
                ),
            ),
            *([extend("generated_headers", var(name))] if headers else []),
            "",
        ]
        em.generated_files[Label(label.repo, label.pkg, out)] = var(name)


def register(em: Emitter) -> None:
    em.handlers["gentbl_cc_library"] = partial(gentbl, em, True)
    # Files of other kinds, e.g. Python bindings of a dialect.
    em.handlers["gentbl_filegroup"] = partial(gentbl, em, False)
    for kind in ("gentbl_cc_library", "gentbl_filegroup"):
        em.outputs[kind] = lambda target: [out for out, _ in tbl_outs(em, target)]
    # Only its include directories matter, which gentbl_cc_library collects.
    em.passive_kinds.add("td_library")
