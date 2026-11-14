"""JAX's `symlink_files` rule, which gives files a place in a Python package.

Needs the python_library plugin, which installs them there.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from ..meson import Emitter
from ..starlark import Label, Target
from .python_library import files_of


def register(em: Emitter) -> None:
    def placed(label: Label, target: Target) -> list[tuple[Label, str]]:
        directory = PurePosixPath(label.pkg) / em.rs.attr(target, "dst", ".")
        strip = em.rs.attr(target, "strip_prefix", "")
        found = []
        for src in em.rs.labels(target, "srcs"):
            for file, natural in files_of(em, src):
                if em.rs.attr(target, "flatten", False):
                    rel = PurePosixPath(natural).name
                elif natural.startswith(strip):
                    rel = natural[len(strip) :].lstrip("/")
                else:
                    em.problems["symlink_files: file outside of strip_prefix"].add(f"{label}: {file}")
                    continue
                found.append((file, str(directory / rel)))
        return found

    em.passive_kinds.add("symlink_files")
    em.file_placers["symlink_files"] = placed
