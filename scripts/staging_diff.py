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

Diffing a submodule requires BOTH pin SHAs to be present in the (often
shallow) submodule clone. When the base pin is missing the script fetches
it, and if it still cannot resolve, it EXITS NON-ZERO rather than returning
a partial list — silently dropping a submodule's modules would ship them to
prod un-migrated.

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


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> tuple[int, str, str]:
    """Run a subprocess; return (rc, stdout, stderr).

    If `check` is True (default), exits the script with a descriptive
    message when the command fails. Callers that want to handle failure
    themselves pass check=False.
    """
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(
            f"command failed: {' '.join(cmd)}\n"
            f"  cwd: {cwd}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    return result.returncode, result.stdout, result.stderr


def current_branch(repo: Path) -> str:
    _, out, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    return out.strip()


def derive_base(head: str) -> str:
    if not head.endswith("-staging"):
        raise SystemExit(
            f"head branch {head!r} does not match `<version>-staging` convention; "
            "pass --base explicitly."
        )
    return head[: -len("-staging")]


def changed_paths(base: str, head: str, repo: Path) -> list[str]:
    _, out, _ = run(["git", "diff", "--name-only", f"{base}...{head}"], repo)
    return [line for line in out.splitlines() if line]


def submodule_sha_changes(
    base: str, head: str, repo: Path
) -> dict[str, tuple[str, str]]:
    """Return {submodule_path: (old_sha, new_sha)} for bumped submodules."""
    _, out, _ = run(
        ["git", "diff", "--submodule=short", f"{base}...{head}", "--", ".repos"],
        repo,
    )
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


def ensure_commit(sha: str, sub_root: Path, verbose: bool = False) -> bool:
    """Ensure `sha` is present in the submodule clone, fetching it if missing.

    CI clones submodules shallow (``--recommend-shallow``), so a base pin that
    is an ancestor — or worse, an off-default-branch SHA — of the checked-out
    commit is often absent. Try the cheapest recovery first (fetch the exact
    object, which works when the server allows reachable-SHA fetches), then
    fall back to deepening the whole clone.
    """
    def present() -> bool:
        rc, _, _ = run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], sub_root, check=False)
        return rc == 0

    if present():
        if verbose:
            print(f"[staging-diff] {sub_root.name}: {sha} already present locally", file=sys.stderr)
        return True
    for fetch_args in (
        ["git", "fetch", "--no-tags", "origin", sha],
        ["git", "fetch", "--no-tags", "--unshallow", "origin"],
        ["git", "fetch", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*"],
    ):
        rc, _, err = run(fetch_args, sub_root, check=False)
        if verbose:
            outcome = "ok" if rc == 0 else f"failed (rc={rc}): {err.strip()[:200]}"
            print(f"[staging-diff] {sub_root.name}: fetch attempt {' '.join(fetch_args)} -> {outcome}", file=sys.stderr)
        if present():
            if verbose:
                print(f"[staging-diff] {sub_root.name}: {sha} resolved after fetch", file=sys.stderr)
            return True
    if verbose:
        print(f"[staging-diff] {sub_root.name}: {sha} UNRESOLVED after all fetch attempts", file=sys.stderr)
    return False


def modules_from_submodule_diff(
    repo: Path,
    sub_path: str,
    old_sha: str,
    new_sha: str,
    sym_map: dict[Path, str],
    verbose: bool = False,
) -> set[str]:
    sub_root = repo / sub_path
    if not sub_root.is_dir():
        return set()
    # Both endpoints must be present locally or the diff silently yields nothing,
    # which would emit a PARTIAL upgrade list (modules in this submodule dropped)
    # while the pipeline stays green — the exact failure that shipped un-migrated
    # modules to prod. Fail loud instead.
    for sha in (old_sha, new_sha):
        if not ensure_commit(sha, sub_root, verbose=verbose):
            raise SystemExit(
                f"error: submodule {sub_path}: commit {sha} is not available "
                f"locally and could not be fetched. Refusing to emit a partial "
                f"upgrade list — deepen the submodule clone (full history or "
                f"fetch the base pin) and re-run."
            )
    rc, out, err = run(
        ["git", "diff", "--name-only", f"{old_sha}..{new_sha}"],
        sub_root,
        check=False,
    )
    if rc != 0:
        raise SystemExit(
            f"error: submodule {sub_path}: `git diff {old_sha}..{new_sha}` failed: "
            f"{err.strip()}"
        )
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
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print resolved refs, submodule pointer changes, and the "
        "per-submodule module list to stderr (self-diagnosing CI logs).",
    )
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    addons_dir = repo / "addons"

    head = args.head or current_branch(repo)
    base = args.base or derive_base(head)

    if args.verbose:
        print(f"[staging-diff] repo={repo} base={base} head={head}", file=sys.stderr)

    sym_map = addons_symlink_map(addons_dir)
    modules: set[str] = set()

    sub_changes = submodule_sha_changes(base, head, repo)
    if args.verbose:
        if sub_changes:
            for sub_path, (old, new) in sub_changes.items():
                print(f"[staging-diff] submodule pointer changed: {sub_path}: {old} -> {new}", file=sys.stderr)
        else:
            print("[staging-diff] no submodule pointer changes detected", file=sys.stderr)
    for sub_path, (old, new) in sub_changes.items():
        found = modules_from_submodule_diff(repo, sub_path, old, new, sym_map, verbose=args.verbose)
        if args.verbose:
            print(f"[staging-diff] {sub_path}: modules from submodule diff: {sorted(found) or '(none)'}", file=sys.stderr)
        modules |= found

    submodule_paths = set(sub_changes.keys())
    for path in changed_paths(base, head, repo):
        if path in submodule_paths:
            continue
        if path.startswith(".repos/"):
            continue
        modname = find_module_for_path(Path(path), repo)
        if modname:
            modules.add(modname)

    # Modules exposed to Odoo live either under addons/ (client modules and
    # addons/<name> symlinks into .repos/ submodules) OR directly under
    # vendored/ (vendored deps on the addons path via the vendored/ dir, with
    # no addons/ symlink). Both are installable; a diff that touches only a
    # vendored module (e.g. sap_b1_to_odoo) must still make the upgrade list,
    # otherwise a new base model it introduces is never reflected and any
    # upgraded dependent crashes on ir_model_inherit (NOT NULL parent_id).
    available: set[str] = (
        {p.name for p in addons_dir.iterdir()} if addons_dir.is_dir() else set()
    )
    vendored_dir = repo / "vendored"
    if vendored_dir.is_dir():
        available |= {
            p.name
            for p in vendored_dir.iterdir()
            if p.is_dir()
            and ((p / "__manifest__.py").exists() or (p / "__openerp__.py").exists())
        }
    if args.verbose:
        dropped = modules - available
        if dropped:
            print(
                f"[staging-diff] WARNING: modules found in diff but not under "
                f"addons/ or vendored/, dropped: {sorted(dropped)}",
                file=sys.stderr,
            )
    modules = {m for m in modules if m in available}
    if args.verbose:
        print(f"[staging-diff] final module list: {sorted(modules) or '(empty)'}", file=sys.stderr)

    payload = {"base": base, "head": head, "modules": sorted(modules)}
    text = json.dumps(payload, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
