"""Command line entry point: `python -m bazel2meson`."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import modules
from .graph import Config, Resolver
from .meson import Emitter, Settings, SystemRepo, format_problems, write
from .starlark import Repo, Workspace


def main() -> int:
    parser = argparse.ArgumentParser(prog="bazel2meson", description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="TOML description of the build to generate")
    parser.add_argument("--source", type=Path, required=True, help="source tree; becomes the Meson project root")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=DIR",
        help="where an external Bazel repository is unpacked, relative to --source",
    )
    parser.add_argument("--cpu", default="x86_64", choices=["x86_64", "aarch64"])
    parser.add_argument("--version", default="0")
    args = parser.parse_args()

    raw = tomllib.loads(args.config.read_text())
    source = args.source.resolve()

    repo_dirs: dict[str, str] = {"": "."}
    repo_dirs.update(raw.get("repos", {}))
    repo_dirs.update(dict(item.split("=", 1) for item in args.repo))
    repos = {name: Repo(name, source / directory) for name, directory in repo_dirs.items()}
    # Bazel resolves "@name" through the MODULE.bazel of the repository the
    # label is written in.
    mappings: dict[str, dict[str, str]] = {"*": raw.get("repo_aliases", {})}
    redirects: dict[str, str] = dict(raw.get("redirect", {}))
    for name, repo in list(repos.items()):
        module_file = repo.root / "MODULE.bazel"
        if not module_file.is_file():
            continue
        module = modules.parse(module_file)
        mappings[name] = module.mapping
        if name != "":
            continue
        repos[module.name] = repo
        # Envoy's registry modules take root-provided targets through a
        # `<module>.deps(key = label)` tag and expose them as @<module>_deps//:key.
        for extension, tags in module.tags.items():
            for call in tags.get("deps", []):
                for key, target in call.items():
                    redirects.setdefault(f"@{extension}_deps//:{key}", target)
    ws = Workspace(repos=repos, mappings=mappings, redirects=redirects)

    bazel_cpu = {"x86_64": "k8", "aarch64": "aarch64"}[args.cpu]
    cfg = raw.get("config", {})
    config = Config(
        defines=cfg.get("defines", {}),
        values={"cpu": bazel_cpu, **cfg.get("values", {})},
        flags=cfg.get("flags", {}),
        constraints={"@platforms//os:linux", f"@platforms//cpu:{args.cpu}", *cfg.get("constraints", [])},
        conditions=cfg.get("conditions", {}),
    )
    resolver = Resolver(ws, config)
    settings = Settings(
        root=ws.label(raw["root"], "", ""),
        repo_dirs=repo_dirs,
        system={name: SystemRepo(**spec) for name, spec in raw.get("system", {}).items()},
        ignore=set(raw.get("ignore", [])),
        project_name=raw.get("name", "envoy"),
        version=args.version,
    )
    emitter = Emitter(ws, resolver, settings)
    files = emitter.emit()
    write(files, source)

    if resolver.unknown_conditions:
        emitter.problems["select() condition could not be evaluated (treated as false)"].update(
            resolver.unknown_conditions
        )
    if resolver.unknown_values:
        emitter.problems["attribute value comes from an unavailable .bzl file (ignored)"].update(
            resolver.unknown_values
        )
    print(f"wrote {len(files)} meson.build files, {emitter.proto_count} protos", file=sys.stderr)
    if emitter.problems:
        print(format_problems(emitter.problems), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
