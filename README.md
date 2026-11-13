# bazel2meson

Generate a [Meson](https://mesonbuild.com) build from a project's Bazel build
files, taking dependencies from the system wherever a compatible version is
installed.

It is meant for packaging C/C++ projects that only ship a Bazel build: the
result builds with Meson and Ninja, each dependency is either a system
library or a small, individually pinned source archive, and following a new
upstream release means bumping the pin and re-running the generator.

**Status: work in progress.** [Envoy](examples/envoy) is the first project
and the test bed; its build compiles but has not linked yet.

## How it works

- `bazel2meson/starlark.py` executes `BUILD` and `.bzl` files (Starlark is
  nearly Python). The project's own macros run for real; rules from
  repositories that are absent on disk are recorded as plain targets.
- `bazel2meson/modules.py` reads `MODULE.bazel` for repository names.
- `bazel2meson/graph.py` resolves `select()` for one configuration
  (`config_setting`, flags, platform constraints) and walks the dependency
  graph from the root target.
- `bazel2meson/meson.py` writes the `meson.build` files: one `custom_target`
  per `.proto` and `genrule`, one static library per source repository, and
  the executable. `bazel2meson/syntax.py` renders Python values as Meson
  source, and `bazel2meson/templates/` holds the hand-written Meson code.
- `bazel2meson/lock.py` pins the Bazel modules that are built from source in
  a `sources.json`, using the versions and hashes from the project's
  `MODULE.bazel.lock` and the registry.

Only Bazel's loading phase is emulated, so rule implementations do not run.
`cc_library`, `cc_binary`, `proto_library`, `genrule`, `copy_file` and
linkstamps are built in. Other rule kinds need a plugin.

## Configuration

A TOML file describes one project; see
[examples/envoy/envoy.toml](examples/envoy/envoy.toml).

- `root`: the target to build.
- `[config]`: flags and defines that `select()` is resolved against.
- `[system.<repo>]`: Bazel repositories provided by installed libraries,
  looked up with Meson's `dependency()`.
- `[source]`: Bazel modules built from source.
- `plugins`: translations for further rule kinds.
- `exclude`, `forward_headers`, `extra_protos`: adjustments for the places
  where the installed libraries differ from Bazel's view.

## Plugins

A plugin is a Python module with a `register(emitter)` function. It can add
handlers for rule kinds, mark kinds as needing no output, or add protoc code
generators. `plugins` in the configuration lists modules from
`bazel2meson/plugins/` by name, or `.py` files next to the configuration.

Included: `protoc_gen_validate` and `cel_cpp` (ANTLR parser, embedded
descriptor sets).

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
