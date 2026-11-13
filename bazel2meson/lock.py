"""Pin the sources of Bazel modules that have to be built from source.

For the repositories listed under `[source]` in the config, write URL,
hashes, patches and overlay files into one JSON file that a package manager
can fetch from without Bazel. A repository is looked up in, in this order:

- `archive_override()` in the project's MODULE.bazel;
- MODULE.bazel.lock, which names registry and version of every module; the
  registry's `source.json` has the rest;
- the repositories declared by the project's module extensions.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path
from typing import Any

from . import project
from .starlark import Repository, Workspace

_SOURCE_JSON = re.compile(r"(?P<registry>.+)/modules/(?P<module>[^/]+)/(?P<version>[^/]+)/source\.json$")


def locked_modules(hashes: dict[str, str]) -> dict[str, tuple[str, str]]:
    """module name -> (registry URL, version)"""
    modules = {}
    for url in hashes:
        match = _SOURCE_JSON.match(url)
        if match:
            modules[match["module"]] = (match["registry"], match["version"])
    return modules


def sri(sha256_hex: str) -> str:
    return "sha256-" + base64.b64encode(bytes.fromhex(sha256_hex)).decode()


def local_file(ws: Workspace, label: str, origin: str) -> dict[str, str]:
    """A file of another repository, e.g. a patch the project ships for a dependency."""
    resolved = ws.label(label, origin, "")
    return {"repo": resolved.repo, "file": f"{resolved.pkg}/{resolved.name}".lstrip("/")}


def from_registry(module: str, registry: str, version: str, hashes: dict[str, str]) -> dict[str, Any]:
    base = f"{registry}/modules/{module}/{version}"
    with urllib.request.urlopen(f"{base}/source.json") as response:
        source = json.load(response)
    if "url" not in source:
        msg = f"unsupported source type {source.get('type')}"
        raise ValueError(msg)
    return {
        "version": version,
        # Bazel builds against the registry's MODULE.bazel, which may differ
        # from the one in the archive.
        "moduleFile": {"url": f"{base}/MODULE.bazel", "sha256": hashes[f"{base}/MODULE.bazel"]},
        "urls": [source["url"], *source.get("mirror_urls", [])],
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


def from_override(ws: Workspace, override: dict[str, Any]) -> dict[str, Any]:
    """archive_override() in the root MODULE.bazel."""
    prefix = override.get("strip_prefix", "")
    return {
        "version": prefix.rsplit("-", 1)[-1] or "0",
        "urls": override["urls"],
        "hash": override["integrity"],
        "stripPrefix": prefix,
        "patchStrip": override.get("patch_strip", 0),
        "patches": [local_file(ws, patch, "") for patch in override.get("patches", [])],
        "overlay": [],
    }


def from_extension(ws: Workspace, repository: Repository) -> dict[str, Any]:
    """An archive declared by a module extension, in the style of http_archive()."""
    attrs = repository.attrs
    urls = attrs.get("urls") or [attrs["url"]]
    prefix = attrs.get("strip_prefix", "")
    overlay = [
        {"path": dest, **local_file(ws, label, repository.origin)}
        for label, dest in attrs.get("link_files", {}).items()
    ]
    if attrs.get("build_file"):
        # BUILD.bazel takes precedence over a BUILD file in the archive.
        overlay.append({"path": "BUILD.bazel", **local_file(ws, str(attrs["build_file"]), repository.origin)})
    patches = attrs.get("patch_file") or attrs.get("patches") or []
    return {
        "version": prefix.rsplit("-", 1)[-1] or "0",
        "urls": urls,
        "hash": attrs.get("integrity") or sri(attrs["sha256"]),
        "stripPrefix": prefix,
        # tf_http_archive applies its patch_file with -p1.
        "patchStrip": 1 if attrs.get("patch_file") else attrs.get("patch_strip", 0),
        "patches": [local_file(ws, str(patch), repository.origin) for patch in patches],
        "overlay": overlay,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="bazel2meson-lock", description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="source tree of the project")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=DIR",
        help="where an external Bazel repository is unpacked; needed to evaluate module extensions it defines",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw = tomllib.loads(args.config.read_text())
    source = args.source.resolve()
    ws, _, root = project.workspace(raw, source, args.repo)

    lock = source / "MODULE.bazel.lock"
    hashes = json.loads(lock.read_text())["registryFileHashes"] if lock.is_file() else {}
    registry = locked_modules(hashes)
    skipped = [f"{bzl}%{name}" for bzl, name in root.extensions if not ws.run_extension(bzl, name)]

    result = {}
    missing = []
    for repo in sorted(raw.get("source", {})):
        if repo in root.archives:
            result[repo] = from_override(ws, root.archives[repo])
        elif repo in registry:
            result[repo] = from_registry(repo, *registry[repo], hashes)
        elif repo in ws.repositories and (
            "urls" in ws.repositories[repo].attrs or "url" in ws.repositories[repo].attrs
        ):
            result[repo] = from_extension(ws, ws.repositories[repo])
        else:
            missing.append(repo)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if missing:
        print(f"no archive found for: {', '.join(missing)}", file=sys.stderr)
        if skipped:
            print("module extensions that could not be evaluated (is their repository unpacked?):", file=sys.stderr)
            print("".join(f"  {item}\n" for item in skipped), end="", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
