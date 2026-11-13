"""XLA's cc_embed_data: files embedded in a C++ source.

Upstream runs a tool of its own tree that needs half of XLA to build;
file_embed.py writes the same interface.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path, PurePosixPath

from ..meson import SCRIPTS, Emitter
from ..starlark import Label, Target
from ..syntax import SOURCE_ROOT, assign, call, extend, sanitize, var


def outs(em: Emitter, target: Target) -> list[str]:
    return [str(out) for out in em.rs.attr(target, "outs")]


def cc_embed_data(em: Emitter, label: Label, target: Target) -> None:
    options = [str(opt) for opt in em.rs.attr(target, "embedopts")]
    namespace = next((o.removeprefix("--namespace=") for o in options if o.startswith("--namespace=")), "")
    if not em.rs.attr(target, "flatten", False) or len(options) > bool(namespace):
        em.problems["cc_embed_data: only flatten and --namespace are supported"].add(str(label))
    source = next(o for o in outs(em, target) if o.endswith((".cc", ".cpp")))
    header = next(o for o in outs(em, target) if o.endswith(".h"))
    files = [em.path(src) for src in em.rs.labels(target, "srcs")]
    name = "embed_" + sanitize(f"{em.directory(label)}/{label.name}")
    em.extra_files[f"{SCRIPTS}/file_embed.py"] = Path(__file__).with_name("file_embed.py").read_text()
    em.subdirs[em.directory(label)] += [
        assign(
            name,
            call(
                "custom_target",
                output=[source, header],
                command=[
                    call("find_program", "python3"),
                    SOURCE_ROOT / f"{SCRIPTS}/file_embed.py",
                    PurePosixPath(header).stem,
                    label.pkg,
                    namespace,
                    "@OUTPUT0@",
                    "@OUTPUT1@",
                    *[SOURCE_ROOT / f for f in files if f],
                ],
            ),
        ),
        extend("generated_headers", var(name)[1]),
        "",
    ]
    em.generated_sources[label.repo].append(var(name)[0])


def register(em: Emitter) -> None:
    em.handlers["cc_embed_data"] = partial(cc_embed_data, em)
    em.outputs["cc_embed_data"] = partial(outs, em)
