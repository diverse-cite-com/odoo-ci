#!/usr/bin/env python3
"""
Detect which Odoo modules have changed between git commits.

This script analyzes git diffs to identify changed Odoo modules,
handling both direct addons and symlinked modules from submodules.

Usage:
    python detect_changed_modules.py [OPTIONS]

Environment Variables:
    CI_COMMIT_BEFORE_SHA: GitLab CI base ref
    CI_COMMIT_SHA: GitLab CI head ref
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def run_git_command(args: list[str], cwd: Optional[Path] = None) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # Return empty string on error, let caller handle it
        return ""


def get_changed_files(
    base_ref: str, head_ref: str, cwd: Optional[Path] = None
) -> list[str]:
    """Get list of files changed between two git refs."""
    output = run_git_command(["diff", "--name-only", base_ref, head_ref], cwd=cwd)
    if not output:
        # Fallback to comparing with previous commit
        output = run_git_command(["diff", "--name-only", "HEAD~1", "HEAD"], cwd=cwd)

    if not output:
        return []

    return [f for f in output.split("\n") if f]


def build_symlink_map(addons_dir: Path) -> dict[str, str]:
    """
    Build a mapping from .repos paths to module names.

    Returns:
        Dict mapping normalized repo paths to module names.
        e.g., {"bemade-addons/caldav_sync": "caldav_sync"}
    """
    symlink_map = {}

    if not addons_dir.exists():
        return symlink_map

    for item in addons_dir.iterdir():
        if item.is_symlink():
            target = os.readlink(item)
            module_name = item.name

            # Normalize the target path
            # Targets look like: ../.repos/submodule/module_name
            if ".repos/" in target or ".repos\\" in target:
                # Extract the part after .repos/
                # Handle both ../.repos/ and .repos/ formats
                if ".repos/" in target:
                    repos_part = target.split(".repos/", 1)[1]
                else:
                    repos_part = target.split(".repos\\", 1)[1]

                normalized = repos_part.rstrip("/").rstrip("\\")
                symlink_map[normalized] = module_name

                # Also store just the module part for simpler lookups
                parts = normalized.replace("\\", "/").split("/")
                if len(parts) >= 2:
                    # Store submodule/module -> module_name
                    symlink_map["/".join(parts[:2])] = module_name

    return symlink_map


def is_odoo_module(path: Path) -> bool:
    """Check if a directory is an Odoo module (follows symlinks)."""
    # For symlinks, resolve relative to parent directory
    if path.is_symlink():
        try:
            target = os.readlink(path)
            if not os.path.isabs(target):
                # Resolve relative symlink from parent directory
                resolved = (path.parent / target).resolve()
            else:
                resolved = Path(target)
        except (OSError, ValueError):
            resolved = path
    else:
        resolved = path

    if not resolved.is_dir():
        return False
    return (resolved / "__manifest__.py").exists() or (
        resolved / "__openerp__.py"
    ).exists()


def extract_module_from_path(
    file_path: str,
    addons_dir: Path,
    symlink_map: dict[str, str],
) -> Optional[str]:
    """
    Extract the Odoo module name from a changed file path.

    Args:
        file_path: Relative path to changed file
        addons_dir: Path to addons directory
        symlink_map: Mapping from .repos paths to module names

    Returns:
        Module name if the file belongs to an Odoo module, None otherwise.
    """
    parts = file_path.split("/")

    # Case 1: Direct changes in addons/ directory
    # e.g., addons/my_module/models/model.py -> my_module
    if parts[0] == "addons" and len(parts) >= 2:
        module_name = parts[1]
        module_path = addons_dir / module_name

        # Follow symlink if needed
        if module_path.is_symlink():
            module_path = module_path.resolve()

        if is_odoo_module(module_path) or is_odoo_module(addons_dir / module_name):
            return module_name

    # Case 2: Changes in .repos/ directory (submodules)
    # e.g., .repos/bemade-addons/caldav_sync/models/model.py
    elif parts[0] == ".repos" and len(parts) >= 3:
        submodule = parts[1]
        potential_module = parts[2]

        # Check various path formats in symlink map
        lookup_paths = [
            f"{submodule}/{potential_module}",
            potential_module,
        ]

        for lookup in lookup_paths:
            if lookup in symlink_map:
                module_name = symlink_map[lookup]
                if is_odoo_module(addons_dir / module_name):
                    return module_name

    return None


def detect_changed_modules(
    base_ref: str,
    head_ref: str,
    addons_dir: Path,
    cwd: Optional[Path] = None,
) -> set[str]:
    """
    Detect which Odoo modules have changed between two git refs.

    Args:
        base_ref: Base git reference (e.g., commit SHA, branch name)
        head_ref: Head git reference
        addons_dir: Path to the addons directory
        cwd: Working directory for git commands

    Returns:
        Set of module names that have changed.
    """
    changed_files = get_changed_files(base_ref, head_ref, cwd=cwd)

    if not changed_files:
        return set()

    symlink_map = build_symlink_map(addons_dir)
    changed_modules = set()

    for file_path in changed_files:
        module = extract_module_from_path(file_path, addons_dir, symlink_map)
        if module:
            changed_modules.add(module)

    return changed_modules


def get_codependent_modules(
    modules: set[str],
    addons_dir: Path,
) -> set[str]:
    """
    Find modules that depend on the given modules using manifestoo.

    Args:
        modules: Set of module names to find co-dependents for
        addons_dir: Path to addons directory

    Returns:
        Set of co-dependent module names (modules that depend on input modules).
    """
    if not modules:
        return set()

    try:
        # Check if manifestoo is available
        subprocess.run(["manifestoo", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "Warning: manifestoo not found, skipping co-dependency detection",
            file=sys.stderr,
        )
        return set()

    try:
        select_modules = ",".join(modules)
        result = subprocess.run(
            [
                "manifestoo",
                "--addons-path",
                str(addons_dir),
                "--select",
                select_modules,
                "list-codepends",
                "--separator=,",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        codepends = result.stdout.strip()
        if codepends:
            # Filter to only modules that exist in our addons directory
            codep_set = set(codepends.split(","))
            return {m for m in codep_set if is_odoo_module(addons_dir / m)}

    except subprocess.CalledProcessError as e:
        print(f"Warning: manifestoo failed: {e.stderr}", file=sys.stderr)

    return set()


def format_output(modules: set[str], output_format: str) -> str:
    """Format the module list for output."""
    module_list = sorted(modules)

    if output_format == "comma":
        return ",".join(module_list)
    elif output_format == "newline":
        return "\n".join(module_list)
    elif output_format == "json":
        return json.dumps(module_list)
    else:
        raise ValueError(f"Unknown output format: {output_format}")


def main():
    parser = argparse.ArgumentParser(
        description="Detect which Odoo modules have changed between git commits."
    )
    parser.add_argument(
        "--addons-dir",
        type=Path,
        default=Path("./addons"),
        help="Path to addons directory (default: ./addons)",
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help="Base git ref to compare from (default: HEAD~1 or CI_COMMIT_BEFORE_SHA)",
    )
    parser.add_argument(
        "--head-ref",
        default=None,
        help="Head git ref to compare to (default: HEAD or CI_COMMIT_SHA)",
    )
    parser.add_argument(
        "--include-codepends",
        action="store_true",
        help="Include modules that depend on changed modules (requires manifestoo)",
    )
    parser.add_argument(
        "--output",
        choices=["comma", "newline", "json"],
        default="comma",
        help="Output format (default: comma)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output to stderr",
    )

    args = parser.parse_args()

    # Determine refs from args or environment
    base_ref = args.base_ref
    head_ref = args.head_ref

    # Use GitLab CI variables if available
    ci_before = os.environ.get("CI_COMMIT_BEFORE_SHA", "")
    ci_sha = os.environ.get("CI_COMMIT_SHA", "")

    if not base_ref:
        if ci_before and ci_before != "0" * 40:
            base_ref = ci_before
        else:
            base_ref = "HEAD~1"

    if not head_ref:
        head_ref = ci_sha if ci_sha else "HEAD"

    if args.verbose:
        print(f"Base ref: {base_ref}", file=sys.stderr)
        print(f"Head ref: {head_ref}", file=sys.stderr)
        print(f"Addons dir: {args.addons_dir}", file=sys.stderr)

    # Detect changed modules
    changed_modules = detect_changed_modules(
        base_ref=base_ref,
        head_ref=head_ref,
        addons_dir=args.addons_dir,
    )

    if args.verbose:
        print(
            f"Directly changed modules ({len(changed_modules)}): {sorted(changed_modules)}",
            file=sys.stderr,
        )

    # Optionally include co-dependencies
    if args.include_codepends and changed_modules:
        codepends = get_codependent_modules(changed_modules, args.addons_dir)
        if codepends:
            if args.verbose:
                print(f"Co-dependent modules: {sorted(codepends)}", file=sys.stderr)
            changed_modules.update(codepends)

    # Output result
    if changed_modules:
        print(format_output(changed_modules, args.output))
    elif args.verbose:
        print("No Odoo modules changed", file=sys.stderr)


if __name__ == "__main__":
    main()
