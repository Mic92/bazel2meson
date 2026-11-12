"""Pin the sources of Bazel modules that have to be built from source.

Envoy's MODULE.bazel.lock names the registry and version of every module in
its dependency graph. For the modules listed under `[source]` in the config we
copy the registry's `source.json` (URL, integrity hashes, patches, overlay
files) into one JSON file that a package manager can fetch from without Bazel.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

_SOURCE_JSON = re.compile(r"(?P<registry>.+)/modules/(?P<module>[^/]+)/(?P<version>[^/]+)/source\.json$")


def locked_modules(hashes: dict[str, str]) -> dict[str, tuple[str, str]]:
    """module name -> (registry URL, version)"""
    modules = {}
    for url in hashes:
        match = _SOURCE_JSON.match(url)
        if match:
            modules[match["module"]] = (match["registry"], match["version"])
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(prog="bazel2meson.lock", description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Envoy source tree")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    wanted = tomllib.loads(args.config.read_text()).get("source", {})
    hashes = json.loads((args.source / "MODULE.bazel.lock").read_text())["registryFileHashes"]
    modules = locked_modules(hashes)

    result = {}
    for repo in sorted(wanted):
        module = repo
        if module not in modules:
            print(f"{repo}: module {module} is not in MODULE.bazel.lock", file=sys.stderr)
            return 1
        registry, version = modules[module]
        base = f"{registry}/modules/{module}/{version}"
        with urllib.request.urlopen(f"{base}/source.json") as response:
            source = json.load(response)
        if "url" not in source:
            print(f"{repo}: unsupported source type {source.get('type')}", file=sys.stderr)
            return 1
        result[repo] = {
            "version": version,
            # Bazel builds against the registry's MODULE.bazel, which may differ
            # from the one in the archive.
            "moduleFile": {"url": f"{base}/MODULE.bazel", "sha256": hashes[f"{base}/MODULE.bazel"]},
            "url": source["url"],
            "hash": source["integrity"],
            "stripPrefix": source.get("strip_prefix", ""),
            "patchStrip": source.get("patch_strip", 0),
            "patches": [
                {"url": f"{base}/patches/{name}", "hash": integrity}
                for name, integrity in source.get("patches", {}).items()
            ],
            "overlay": [
                {"path": path, "url": f"{base}/overlay/{path}", "hash": integrity}
                for path, integrity in source.get("overlay", {}).items()
            ],
        }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
