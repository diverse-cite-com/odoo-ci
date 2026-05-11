#!/usr/bin/env python3
"""Compute the module list for a staging refresh.

Compares the current branch (head) against its base branch — derived from
the naming convention `<major.version>-staging` → `<major.version>` — and
emits a JSON document of the form:

    {"base": "18.0", "head": "18.0-staging", "modules": [...]}

The consumer is expected to pass the same list to both `-i` and `-u`:
Odoo treats `-i` as a no-op for already-installed modules and `-u` as a
no-op for uninstalled ones, so the combination correctly installs new
modules and upgrades existing ones without needing live DB state.

Submodule pointer bumps under `.repos/` are recursed into: the script runs
the same name-only diff between the old and new submodule SHAs and maps
modified module directories back to the `addons/<name>` symlinks that
expose them to Odoo. Modules not exposed via `addons/` are ignored.

This script is bundled in the odoo-ci repo and operates on whichever
project directory is passed via --repo (default: current working
directory), so individual projects don't need to vendor it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout


def current_branch(repo: Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo).strip()


def derive_base(head: str) -> str:
    if not head.endswith("-staging"):
        raise SystemExit(
            f"head branch {head!r} does not match `<version>-staging` convention; "
            "pass --base explicitly."
        )
    return head[: -len("-staging")]


def changed_paths(base: str, head: str, repo: Path) -> list[str]:
    out = run(["git", "diff", "--name-only", f"{base}...{head}"], repo)
    return [line for line in out.splitlines() if line]


def submodule_sha_changes(
    base: str, head: str, repo: Path
) -> dict[str, tuple[str, str]]:
    """Return {submodule_path: (old_sha, new_sha)} for bumped submodules."""
    out = run(["git", "diff", f"{base}...{head}", "--", ".repos"], repo)
    changes: dict[str, tuple[str, str]] = {}
    current_path: str | None = None
    old_sha: str | None = None
    for line in out.splitlines():
        m = re.match(r"^diff --git a/(\.repos/[^ ]+) b/\1$", line)
        if m:
            current_path = m.group(1)
            old_sha = None
            continue
        if current_path is None:
            continue
        m = re.match(r"^-Subproject commit ([0-9a-f]+)", line)
        if m:
            old_sha = m.group(1)
            continue
        m = re.match(r"^\+Subproject commit ([0-9a-f]+)", line)
        if m and old_sha is not None:
            changes[current_path] = (old_sha, m.group(1))
            current_path = None
            old_sha = None
    return changes


def find_module_for_path(path: Path, repo: Path) -> str | None:
    """Walk up from `path` until a directory containing __manifest__.py.

    If the path itself sits directly under `addons/` (e.g. an `addons/<name>`
    symlink that was added or removed), the entry name is the module name —
    even if the target no longer exists.
    """
    parts = path.parts
    if len(parts) == 2 and parts[0] == "addons":
        return parts[1]
    abs_path = repo / path
    cur = abs_path if abs_path.is_dir() else abs_path.parent
    repo_resolved = repo.resolve()
    while True:
        try:
            cur_resolved = cur.resolve()
        except OSError:
            cur_resolved = cur
        if cur_resolved == repo_resolved or cur_resolved == Path(cur_resolved.anchor):
            return None
        if (cur / "__manifest__.py").exists() or (cur / "__openerp__.py").exists():
            return cur.name
        cur = cur.parent


def addons_symlink_map(addons_dir: Path) -> dict[Path, str]:
    """Map resolved real-paths of addons/<name> entries → module name."""
    mapping: dict[Path, str] = {}
    if not addons_dir.is_dir():
        return mapping
    for entry in addons_dir.iterdir():
        if entry.is_symlink():
            try:
                real = entry.resolve(strict=True)
            except OSError:
                continue
            mapping[real] = entry.name
        elif entry.is_dir() and (entry / "__manifest__.py").exists():
            mapping[entry.resolve()] = entry.name
    return mapping


def modules_from_submodule_diff(
    repo: Path,
    sub_path: str,
    old_sha: str,
    new_sha: str,
    sym_map: dict[Path, str],
) -> set[str]:
    sub_root = repo / sub_path
    if not sub_root.is_dir():
        return set()
    try:
        out = run(
            ["git", "diff", "--name-only", f"{old_sha}..{new_sha}"], sub_root
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"warning: could not diff submodule {sub_path}: {exc.stderr}",
            file=sys.stderr,
        )
        return set()
    found: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        cur = (sub_root / line).parent
        while cur != sub_root.parent and cur != Path(cur.anchor):
            if (cur / "__manifest__.py").exists() or (cur / "__openerp__.py").exists():
                key = cur.resolve()
                if key in sym_map:
                    found.add(sym_map[key])
                break
            cur = cur.parent
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        default=".",
        help="Project repo root (default: current working directory)",
    )
    ap.add_argument("--base", help="Base ref (default: derived from head)")
    ap.add_argument("--head", help="Head ref (default: current branch)")
    ap.add_argument(
        "--output", "-o", help="Write JSON to this file (default: stdout)"
    )
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    addons_dir = repo / "addons"

    head = args.head or current_branch(repo)
    base = args.base or derive_base(head)

    sym_map = addons_symlink_map(addons_dir)
    modules: set[str] = set()

    sub_changes = submodule_sha_changes(base, head, repo)
    for sub_path, (old, new) in sub_changes.items():
        modules |= modules_from_submodule_diff(repo, sub_path, old, new, sym_map)

    submodule_paths = set(sub_changes.keys())
    for path in changed_paths(base, head, repo):
        if path in submodule_paths:
            continue
        if path.startswith(".repos/"):
            continue
        modname = find_module_for_path(Path(path), repo)
        if modname:
            modules.add(modname)

    available = (
        {p.name for p in addons_dir.iterdir()} if addons_dir.is_dir() else set()
    )
    modules = {m for m in modules if m in available}

    payload = {"base": base, "head": head, "modules": sorted(modules)}
    text = json.dumps(payload, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
