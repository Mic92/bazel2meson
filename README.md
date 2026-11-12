# envoy-meson

Build [Envoy](https://github.com/envoyproxy/envoy) with Meson, taking
dependencies from the system (nixpkgs) wherever a compatible version is
packaged.

**Status: work in progress.** The generator evaluates Envoy's `BUILD` files,
reproduces Bazel's dependency closure of `//source/exe:envoy-static` and
writes `meson.build` files for it. The result does not compile yet.

## How it works

`bazel2meson` generates the Meson build from Envoy's own `BUILD` and `.bzl`
files. Following a new Envoy release means bumping the source pin and
re-running it.

- `bazel2meson/starlark.py` executes `BUILD`/`.bzl` files (Starlark is nearly
  Python). Envoy's macros run for real; rules from repositories that are
  absent on disk are recorded as plain targets.
- `bazel2meson/graph.py` resolves `select()` for one configuration
  (`config_setting`, flags, platform constraints) and walks the dependency
  graph.
- `bazel2meson/meson.py` writes the `meson.build` files: one `custom_target`
  per `.proto` and `genrule`, one static library per source repository, and
  the `envoy` executable.
- `bazel2meson/lock.py` pins the Bazel modules that are built from source in
  `sources.json`, using the versions and hashes from Envoy's
  `MODULE.bazel.lock`.

`envoy.toml` says which Bazel repositories map to installed libraries and
which are built from source.

The build runs on Meson, Ninja and the libraries listed in `package.nix`.
Bazel is useful during development as an oracle: `bazel cquery` shows the
graph the generator should arrive at.

## Usage

    nix build .#envoy-meson

Updating Envoy:

    nix flake update envoy
    python -m bazel2meson.lock --config envoy.toml --source <envoy checkout> --output sources.json
