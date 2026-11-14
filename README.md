# bazel2meson

Generate a [Meson](https://mesonbuild.com) build from a project's Bazel build files.

Bazel projects tend to vendor large amount of third-party dependencies,
which makes them hostile for packaging in downstream distribution,
that have the desire to provide security updates accross the ecosystem.

This project allows to take dependencies from the system, wherever a compatible version is installed.

It is meant for packaging C/C++ projects that only ship a Bazel build: the
result builds with Meson and Ninja, each dependency is either a system
library or a small, individually pinned source archive, and following a new
upstream release means bumping the pin and re-running the generator.

**Status: work in progress.** The examples:

- [bant](examples/bant), [protoc-gen-js](examples/protoc-gen-js),
  [perf_data_converter](examples/perf_data_converter) and
  [verible](examples/verible) (its formatter): build and run; every
  dependency is an installed library. `nix build .#bant`
- [Envoy](examples/envoy): builds and serves requests. Two extensions
  are left out so far, see `envoy.toml`.
- [jaxlib](examples/jax): the CPU package builds with all 30 extension
  modules, and JAX computes on it. GPU parts (CUDA, ROCm, Triton) are left out.

## Configuration

A TOML file describes one project; see
[examples/bant/bant.toml](examples/bant/bant.toml) for a small one and
[examples/envoy/envoy.toml](examples/envoy/envoy.toml) for a large one.

- `root`: the target to build.
- `[config]`: flags and defines that `select()` is resolved against.
- `[system.<repo>]`: Bazel repositories provided by installed libraries,
  looked up with Meson's `dependency()`.
- `[source]`: Bazel modules built from source.
- `plugins`: translations for further rule kinds.
- `[pch.<repo>]`: precompile the headers most sources of a library include
  (`min_share`, `headers`, `exclude`). `-Db_pch=false` builds without it.
- `exclude`, `forward_headers`, `extra_protos`: adjustments for the places
  where the installed libraries differ from Bazel's view.

## Plugins

Only Bazel's loading phase is emulated, so rule implementations do not run.
`cc_library`, `cc_binary`, `proto_library`, `genrule`, `copy_file` and
linkstamps are built in. Other rule kinds need a plugin.

A plugin is a Python module with a `register(emitter)` function. It can add
handlers for rule kinds, mark kinds as needing no output, or add protoc code
generators. `plugins` in the configuration lists modules from
`bazel2meson/plugins/` by name, or `.py` files next to the configuration.

Included: `protoc_gen_validate`, `cel_cpp` (ANTLR parser, embedded
descriptor sets) and `tablegen` (LLVM TableGen).

## Usage

    bazel2meson --config project.toml --source <checkout> --repo NAME=DIR ...
    meson setup build <checkout>

`--repo` says where the source of a Bazel module was unpacked, relative to
the checkout. `--output DIR` writes the generated files elsewhere.

    bazel2meson-lock --config project.toml --source <checkout> --output sources.json

## Envoy example

    nix build .#envoy

Updating Envoy: set `version`, `revision` and the source hash in
[examples/envoy/package.nix](examples/envoy/package.nix), then refresh the
module pins:

    bazel2meson-lock --config examples/envoy/envoy.toml --source "$(nix build --no-link --print-out-paths .#envoy.src)" --output examples/envoy/sources.json

Bazel is useful during development as an oracle: `bazel cquery` shows the
graph the generator should arrive at.
