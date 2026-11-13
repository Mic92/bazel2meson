"""Set up the workspace a configuration file describes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import modules
from .starlark import Repo, Workspace


def workspace(
    raw: dict[str, Any], source: Path, repo_args: list[str]
) -> tuple[Workspace, dict[str, str], modules.Module]:
    """Returns the workspace, where each repository lives relative to source, and the root module."""
    repo_dirs: dict[str, str] = {"": "."}
    repo_dirs.update(raw.get("repos", {}))
    repo_dirs.update(dict(item.split("=", 1) for item in repo_args))
    repos = {name: Repo(name, source / directory) for name, directory in repo_dirs.items()}
    # Bazel resolves "@name" through the MODULE.bazel of the repository the
    # label is written in.
    mappings: dict[str, dict[str, str]] = {"*": raw.get("repo_aliases", {})}
    redirects: dict[str, str] = dict(raw.get("redirect", {}))
    root = modules.Module()
    for name, repo in list(repos.items()):
        module_file = repo.root / "MODULE.bazel"
        if not module_file.is_file():
            continue
        module = modules.parse(module_file)
        mappings[name] = module.mapping
        if name != "":
            continue
        root = module
        repos[module.name] = repo
        # Some registry modules (Envoy's) take root-provided targets through a
        # `<module>.deps(key = label)` tag and expose them as @<module>_deps//:key.
        for extension, tags in module.tags.items():
            for call in tags.get("deps", []):
                for key, target in call.items():
                    redirects.setdefault(f"@{extension}_deps//:{key}", target)
    return Workspace(repos=repos, mappings=mappings, redirects=redirects), repo_dirs, root
