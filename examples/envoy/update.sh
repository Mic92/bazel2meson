#!/usr/bin/env nix-shell
#!nix-shell -i bash -p nix-update git python3
# shellcheck shell=bash
# Bump the Envoy example: update.sh [VERSION]
# Without VERSION the latest upstream release is used.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root"

nix-update --flake envoy ${1:+--version "$1"}
version=$(nix eval --raw .#envoy.version)

# nix-update does not know about the commit the binary reports. An annotated
# tag lists the commit it points to as "<tag>^{}".
revision=$(git ls-remote https://github.com/envoyproxy/envoy "refs/tags/v$version" "refs/tags/v$version^{}" |
  tail -n 1 | cut -f 1)
sed -i -E "s|^(  revision = )\"[^\"]*\";|\1\"$revision\";|" "$here/package.nix"

# Envoy's lockfile decides the versions of the modules built from source.
source=$(nix build --no-link --print-out-paths .#envoy.src)
PYTHONPATH=$root python3 -m bazel2meson.lock \
  --config "$here/envoy.toml" --source "$source" --output "$here/sources.json"

echo "envoy $version ($revision)"
echo "next: nix build .#envoy, and adjust envoy.toml if the generator reports new problems"
