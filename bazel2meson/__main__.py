"""Command line entry point: `python -m bazel2meson`."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import plugins, project
from .graph import Config, Resolver
from .meson import Emitter, Settings, SystemRepo, format_problems, write
from .pch import PchSpec


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
    parser.add_argument("--output", type=Path, help="where to write the generated files (default: the source tree)")
    parser.add_argument("--verbose", action="store_true", help="list every problem")
    parser.add_argument("--version", default="0")
    parser.add_argument("--revision", default="0" * 40, help="source revision the binary reports")
    args = parser.parse_args()

    raw = tomllib.loads(args.config.read_text())
    source = args.source.resolve()

    ws, repo_dirs, _ = project.workspace(raw, source, args.repo)

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
        exclude={str(ws.label(item, "", "")) for item in raw.get("exclude", [])},
        extra_protos=[
            (group["root"], file, file in group.get("grpc", []))
            for group in raw.get("extra_protos", [])
            for file in group["files"]
        ],
        forward_headers=raw.get("forward_headers", {}),
        pch={name: PchSpec(**spec) for name, spec in raw.get("pch", {}).items()},
        revision=args.revision,
        project_name=raw.get("name", args.config.stem),
        version=args.version,
    )
    emitter = Emitter(ws, resolver, settings)
    plugins.load(raw.get("plugins", []), args.config.parent, emitter)
    files = emitter.emit()
    write(files, args.output or source)

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
        print(format_problems(emitter.problems, sys.maxsize if args.verbose else 25), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
