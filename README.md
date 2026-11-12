# envoy-meson

Build [Envoy](https://github.com/envoyproxy/envoy) with Meson instead of Bazel,
taking dependencies from the system (nixpkgs) wherever a compatible version is
packaged.

**Status: work in progress.** The translator can evaluate Envoy's `BUILD` files
and reproduce Bazel's dependency closure of `//source/exe:envoy-static`. It
does not emit a Meson build yet, so nothing compiles.

## How it works

Nothing in here is a hand-written copy of Envoy's build. `bazel2meson` reads
Envoy's own `BUILD` and `.bzl` files and generates the Meson build from them, so
following a new Envoy release means bumping the source pin and re-running it.

- `bazel2meson/starlark.py` executes `BUILD`/`.bzl` files (Starlark is nearly
  Python). Envoy's macros run for real; rules from repositories we do not
  have on disk are recorded as plain targets.
- `bazel2meson/graph.py` resolves `select()` for one configuration
  (`config_setting`, flags, platform constraints) and walks the dependency
  graph.

Bazel is not needed to build. During development it is useful as an oracle to
compare the translator's view of the graph against (`bazel cquery`).
