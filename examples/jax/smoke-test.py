"""Import every extension module of jaxlib, then compute something with JAX."""

import importlib
import pathlib
import sys

import jax  # isort: skip
import jax.numpy as jnp
import jaxlib

# Imports "jax.jaxlib._jax", a path that only exists inside Google.
BROKEN_UPSTREAM = {"jaxlib._sdy_mpmd"}

# jax is imported before this loop: MLIR loads a dialect's Python package
# while the dialect's extension module initialises, and that package would
# copy the names of a half-initialised module.
root = pathlib.Path(jaxlib.__file__).parent
failed = False
for path in sorted(root.rglob("*.so")):
    if path.name.startswith("lib"):
        continue
    name = ".".join(["jaxlib", *path.relative_to(root).parts[:-1], path.name.split(".")[0]])
    if name in BROKEN_UPSTREAM:
        continue
    try:
        importlib.import_module(name)
    except Exception as error:  # noqa: BLE001
        failed = True
        print(f"FAIL {name}: {error!r} caused by {error.__cause__!r}")
if failed:
    sys.exit(1)


def loss(w):
    return jnp.sum(jnp.tanh(w) ** 2)


# Small values, where float32 keeps 1 - tanh(w)**2 accurate.
w = jnp.linspace(-1.0, 1.0, 6).reshape(2, 3)
grad = jax.jit(jax.grad(loss))(w)
expected = 2 * jnp.tanh(w) * (1 - jnp.tanh(w) ** 2)
assert jnp.allclose(grad, expected), (grad, expected)
assert jnp.allclose(jnp.linalg.inv(jnp.eye(3) * 2.0), jnp.eye(3) / 2.0)
print("jax", jax.__version__, "on jaxlib", jaxlib.__version__, "ok")
