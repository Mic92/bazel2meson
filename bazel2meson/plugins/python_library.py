"""Python sources: `py_library` targets are installed as they lie in the tree.

A file goes where its label says, relative to the project's repository, which
is where Bazel's runfiles tree has it. Rules that put files elsewhere
register in `Emitter.file_placers`.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from ..meson import Emitter, template
from ..starlark import Label, Target
from ..syntax import Expr, Value, call, statement

# Build directory for the copies of generated files that get installed.
STAGING = "bazel2meson-python"


def files_of(em: Emitter, label: Label) -> list[tuple[Label, str]]:
    """The files a label stands for, each with its place in the package."""
    target = em.targets.get(label)
    if target is None:
        return [(label, str(PurePosixPath(label.pkg) / label.name))]
    if target.kind in em.file_placers:
        return em.file_placers[target.kind](label, target)
    if target.kind == "filegroup":
        return [found for src in em.rs.labels(target, "srcs") for found in files_of(em, src)]
    if em.is_module(target):
        # Installed by the rule building it.
        return []
    if target.kind in em.outputs:
        outs = [Label(label.repo, label.pkg, out) for out in em.outputs[target.kind](target)]
        return [(out, str(PurePosixPath(out.pkg) / out.name)) for out in outs]
    return []


def register(em: Emitter) -> None:
    em.templates.setdefault("python", template("python.meson"))
    main = em.settings.root.repo
    placed: set[str] = set()
    # Installation directory -> source files.
    sources: dict[str, list[str]] = defaultdict(list)

    def install_dir(directory: str) -> Expr:
        return Expr("py.get_install_dir()") / directory

    def library(label: Label, target: Target) -> None:
        if label.repo != main:
            # Only the project's own repository is laid out like its package.
            return
        for src in [*em.rs.labels(target, "srcs"), *em.rs.labels(target, "data")]:
            for file, dest in files_of(em, src):
                if dest in placed:
                    continue
                placed.add(dest)
                directory, name = str(PurePosixPath(dest).parent), PurePosixPath(dest).name
                path = em.path(file)
                generated = None if path is not None else em.generated_file(file)
                if path is not None and PurePosixPath(path).name == name:
                    sources[directory].append(path)
                elif path is not None:
                    args: dict[str, Value] = {"install_dir": install_dir(directory), "rename": name}
                    em.install_statements.append(statement(call("install_data", path, **args)))
                elif generated is not None:
                    # Meson installs generated files from the target writing
                    # them only; a copy keeps this apart from the rule.
                    em.late_subdirs[f"{STAGING}/{directory}"].append(
                        statement(
                            call(
                                "custom_target",
                                input=generated,
                                output=name,
                                command=["cp", "@INPUT@", "@OUTPUT@"],
                                install=True,
                                install_dir=install_dir(directory),
                                install_tag="python-runtime",
                            )
                        )
                    )
                else:
                    em.problems["python_library: no such file"].add(f"{label}: {file}")

    def finish() -> None:
        for directory, files in sorted(sources.items()):
            em.install_statements.append(
                statement(Expr("py." + call("install_sources", sorted(files), subdir=directory).code))
            )

    em.handlers["py_library"] = library
    # Extension modules and files that are no Python sources.
    em.extra_edges["py_library"] = ("data",)
    em.finalizers.append(finish)
