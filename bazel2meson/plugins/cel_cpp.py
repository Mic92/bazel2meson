"""Rules defined by cel-cpp (https://github.com/google/cel-cpp)."""

from __future__ import annotations

from functools import partial
from pathlib import Path, PurePosixPath

from ..meson import SCRIPTS, Emitter
from ..starlark import Label, Target
from ..syntax import assign, call, extend, sanitize, var


def antlr_library(em: Emitter, label: Label, target: Target) -> None:
    """ANTLR C++ parser; cel-cpp includes the result as <package dir>/<Grammar>Parser.h."""
    (src,) = em.rs.labels(target, "src")
    grammar = PurePosixPath(src.name).stem
    parts = ("Lexer", "Parser", "BaseVisitor", "Visitor")
    generated = var("gen_" + sanitize(f"{em.directory(label)}/{label.name}"))
    em.subdirs[em.directory(label)] += [
        assign(
            generated.code,
            call(
                "custom_target",
                input=src.name,
                output=[f"{grammar}{part}.h" for part in parts] + [f"{grammar}{part}.cpp" for part in parts],
                command=[
                    call("find_program", "antlr4", "antlr"),
                    "-Dlanguage=Cpp",
                    "-no-listener",
                    "-visitor",
                    "-Xexact-output-dir",
                    "-o",
                    "@OUTDIR@",
                    "-package",
                    em.rs.attr(target, "package", ""),
                    "@INPUT@",
                ],
            ),
        ),
        extend("generated_headers", [generated[i] for i in range(len(parts))]),
        "",
    ]
    em.generated_sources[label.repo] += [generated[len(parts) + i] for i in range(len(parts))]


def cel_cc_embed(em: Emitter, label: Label, target: Target) -> None:
    """A serialized FileDescriptorSet as a C array initializer, in <name>.inc."""
    (src,) = em.rs.labels(target, "src")
    descriptor_set = em.ws.get(src)
    protos: list[str] = []
    for dep in em.rs.labels(descriptor_set, "deps") if descriptor_set else []:
        if dep.repo == "protobuf":
            # Well-known types ship with protoc: //:any_proto is google/protobuf/any.proto.
            protos.append(f"google/protobuf/{dep.name.removesuffix('_proto')}.proto")
            continue
        proto = em.ws.get(dep)
        for item in em.rs.labels(proto, "srcs") if proto else []:
            path = em.path(item)
            if path is not None:
                protos.append(em.source_path(path))
    if not protos:
        em.problems["cel_cc_embed without protos"].add(str(label))
    out = em.build_path(f"{em.directory(label)}/{label.name}")
    name = "gen_" + sanitize(f"{em.directory(label)}/{label.name}")
    script = (
        f"protoc $PROTO_ARGS --include_imports --descriptor_set_out={out}.binarypb {' '.join(protos)}\n"
        f"python3 {SCRIPTS}/cel_embed.py {out}.binarypb {out}.inc"
    )
    em.extra_files[f"{SCRIPTS}/cel_embed.py"] = Path(__file__).with_name("cel_embed.py").read_text()
    em.subdirs[em.directory(label)] += [
        assign(name, call("custom_target", output=f"{label.name}.inc", **em.script(name, script))),
        extend("generated_headers", var(name)),
        "",
    ]


def register(em: Emitter) -> None:
    em.handlers["antlr_library"] = partial(antlr_library, em)
    em.handlers["cel_cc_embed"] = partial(cel_cc_embed, em)
    # Only consumed through cel_cc_embed.
    em.passive_kinds.add("cel_proto_transitive_descriptor_set")
