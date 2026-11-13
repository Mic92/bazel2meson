"""Translations for rules of specific Bazel projects.

A plugin is a module with a ``register(emitter)`` function that extends
``Emitter.handlers``, ``Emitter.passive_kinds`` or ``Emitter.protoc_plugins``.
The ``plugins`` list of the configuration names modules of this package, or
paths of Python files relative to the configuration file.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..meson import Emitter


def load(names: list[str], base: Path, emitter: Emitter) -> None:
    for name in names:
        if name.endswith(".py"):
            spec = importlib.util.spec_from_file_location(Path(name).stem, base / name)
            if spec is None or spec.loader is None:
                msg = f"cannot load plugin {name}"
                raise ImportError(msg)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(f"{__name__}.{name}")
        module.register(emitter)
