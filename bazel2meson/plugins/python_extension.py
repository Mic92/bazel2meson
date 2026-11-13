"""Python extension modules built with XLA's pywrap rules (`pybind_extension`).

Each module is a small stub; the code behind it lives in the library all
modules of the project share, installed next to them in the package.
"""

from __future__ import annotations

from ..meson import Emitter, template
from ..starlark import Label
from ..syntax import Expr, Value, call


def register(em: Emitter) -> None:
    em.templates.setdefault("python", template("python.meson"))
    package = em.settings.root.pkg

    def module(label: Label, args: dict[str, Value]) -> Expr:
        return Expr(
            "py."
            + call(
                "extension_module",
                label.name,
                **args,
                subdir=label.pkg,
                install=True,
                install_rpath="$ORIGIN",
            ).code
        )

    em.cc_kinds.add("pybind_extension")
    em.module_kinds["pybind_extension"] = module
    # The interpreter loading the modules provides the Python symbols.
    em.default_options.append("b_lundef=false")
    em.common_library_args["install_dir"] = Expr("py.get_install_dir()") / package
