"""Python extension modules built with XLA's pywrap rules (`pybind_extension`).

Each module is a small stub; the code behind it lives in the library all
modules of the project share, installed next to them in the package.
"""

from __future__ import annotations

import os

from ..meson import Emitter, template
from ..starlark import Label
from ..syntax import Expr, Value, call


def register(em: Emitter) -> None:
    em.templates.setdefault("python", template("python.meson"))
    package = em.settings.root.pkg

    def module(label: Label, args: dict[str, Value]) -> Expr:
        # Modules of other repositories join the project's package.
        subdir = label.pkg if label.repo == em.settings.root.repo else package
        # The common library is at the top of the package.
        up = os.path.relpath(package, subdir)
        return Expr(
            "py."
            + call(
                "extension_module",
                label.name.removesuffix(".so"),
                **args,
                subdir=subdir,
                install=True,
                install_rpath="$ORIGIN" if up == "." else f"$ORIGIN/{up}",
            ).code
        )

    em.cc_kinds.add("pybind_extension")
    # Names the modules sharing the common library, which here is all of them.
    em.passive_kinds.add("pywrap_library")
    em.module_kinds["pybind_extension"] = module
    # A shared object that a py_library carries as data.
    em.module_kinds["cc_binary"] = module

    def wrapper(label: Label) -> tuple[str, str]:
        name = label.name.removesuffix(".so")
        stub = (
            "#include <Python.h>\n\n"
            f"PyObject *Wrapped_PyInit_{name}(void);\n\n"
            f"PyMODINIT_FUNC PyInit_{name}(void) {{ return Wrapped_PyInit_{name}(); }}\n"
        )
        return f"-DPyInit_{name}=Wrapped_PyInit_{name}", stub

    em.module_wrappers["cc_binary"] = wrapper
    # The interpreter loading the modules provides the Python symbols.
    em.default_options.append("b_lundef=false")
    em.common_library_args["install_dir"] = Expr("py.get_install_dir()") / package
