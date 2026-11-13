"""XLA's cc_ir_header: C++ compiled to LLVM bitcode and embedded in a header.

The macro builds a cc_library with -emit-llvm, picks its object file out of
the library and embeds it with a tool of the tree. Here one build step
compiles the source to bitcode with the C++ compiler, which has to be Clang,
and bitcode_embed.py writes the header.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ..meson import SCRIPTS, Emitter
from ..starlark import Label, Target
from ..syntax import SOURCE_ROOT, Expr, assign, call, extend, sanitize, var

SUFFIX = "_gen_object_and_header"


def embed(em: Emitter, label: Label, target: Target) -> None:
    (extract,) = em.rs.labels(target, "srcs")
    extracted = em.ws.get(extract)
    header = next(str(o) for o in em.rs.attr(target, "outs") if str(o).endswith(".h"))
    # "TOOL $< OBJECT HEADER VARIABLE NAMESPACE [--fail_if_no_bitcode]"
    words = [word for word in shlex.split(em.rs.attr(target, "cmd")) if not word.startswith("--")]
    variable, namespace = words[-2:]
    library = em.ws.get(em.ws.label(em.rs.attr(extracted, "dep"), label.repo, label.pkg))
    source = em.path(em.ws.label(em.rs.attr(extracted, "src"), label.repo, label.pkg))
    if library is None or source is None:
        em.problems["cc_ir_header: library or source not found"].add(str(label))
        return
    flags = [str(flag) for flag in em.rs.attr(library, "copts")]
    name = "llvm_ir_" + sanitize(f"{em.directory(label)}/{label.name.removesuffix(SUFFIX)}")
    em.extra_files[f"{SCRIPTS}/bitcode_embed.py"] = Path(__file__).with_name("bitcode_embed.py").read_text()
    bitcode = em.build_path(f"{em.directory(label)}/{label.name.removesuffix(SUFFIX)}.bc")
    # Any repository of the tree may be included from; installed libraries
    # are expected on the compiler's default search path.
    includes = [f"-I{directory}" for directory in sorted(set(em.settings.repo_dirs.values()))]
    # The compiler arrives as the script's arguments.
    script = em.script(
        name,
        f'"$@" -c {shlex.join(flags + includes)} {shlex.quote(source)} -o {bitcode}\n'
        f"python3 {SCRIPTS}/bitcode_embed.py {bitcode} "
        f"{em.build_path(em.directory(label))}/{shlex.quote(header)} {shlex.quote(variable)} {shlex.quote(namespace)}",
    )
    compiler = Expr("meson.get_compiler('cpp').cmd_array()")
    em.subdirs[em.directory(label)] += [
        assign(
            name,
            call(
                "custom_target",
                output=[header],
                depend_files=[SOURCE_ROOT / source],
                **{**script, "command": [*script["command"], compiler]},
            ),
        ),
        extend("generated_headers", var(name)),
        "",
    ]


def register(em: Emitter) -> None:
    genrule = em.handlers["genrule"]

    def handler(label: Label, target: Target) -> None:
        if label.name.endswith(SUFFIX):
            embed(em, label, target)
        else:
            genrule(label, target)

    em.handlers["genrule"] = handler
    # Handled together with the genrule reading its output.
    em.passive_kinds.add("extract_object_files")
