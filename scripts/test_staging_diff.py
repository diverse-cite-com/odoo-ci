#!/usr/bin/env python3
"""Unit tests for staging_diff.py

Focus: the submodule-diff path must NEVER silently drop modules. Before the
fix, `modules_from_submodule_diff` returned an empty set when the base pin
SHA was absent from a shallow submodule clone, which produced a PARTIAL
upgrade list and shipped un-migrated modules to prod. It must now fail loud.
"""

import os
import subprocess
from pathlib import Path

import pytest

from staging_diff import modules_from_submodule_diff


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def project_with_submodule(tmp_path):
    """Build a project repo with a real submodule clone under .repos/ and an
    addons/<name> symlink, plus a module changed across two submodule commits.

    Returns (repo, sub_path, old_sha, new_sha, sym_map).
    """
    repo = tmp_path / "project"
    sub_path = ".repos/bemade-addons"
    sub_root = repo / sub_path
    sub_root.mkdir(parents=True)

    _git(sub_root, "init", "-q")
    _git(sub_root, "config", "user.email", "ci@test")
    _git(sub_root, "config", "user.name", "ci")

    module = sub_root / "my_module"
    module.mkdir()
    (module / "__manifest__.py").write_text('{"name": "My Module"}')
    _git(sub_root, "add", "-A")
    _git(sub_root, "commit", "-q", "-m", "old")
    old_sha = _git(sub_root, "rev-parse", "HEAD")

    (module / "models.py").write_text("x = 1\n")
    _git(sub_root, "add", "-A")
    _git(sub_root, "commit", "-q", "-m", "new")
    new_sha = _git(sub_root, "rev-parse", "HEAD")

    # addons/my_module -> ../.repos/bemade-addons/my_module
    addons = repo / "addons"
    addons.mkdir()
    os.symlink("../.repos/bemade-addons/my_module", addons / "my_module")
    sym_map = {module.resolve(): "my_module"}

    return repo, sub_path, old_sha, new_sha, sym_map


def test_detects_module_when_both_shas_present(project_with_submodule):
    repo, sub_path, old_sha, new_sha, sym_map = project_with_submodule
    result = modules_from_submodule_diff(repo, sub_path, old_sha, new_sha, sym_map)
    assert result == {"my_module"}


def test_fails_loud_when_base_sha_missing(project_with_submodule):
    """Regression: a base pin absent from the clone (and unfetchable — no
    reachable remote) must raise, NOT return an empty set."""
    repo, sub_path, _old_sha, new_sha, sym_map = project_with_submodule
    bogus_old = "0" * 40  # never committed; no 'origin' remote to fetch from

    with pytest.raises(SystemExit) as exc:
        modules_from_submodule_diff(repo, sub_path, bogus_old, new_sha, sym_map)
    assert "not available" in str(exc.value)


def test_missing_submodule_dir_returns_empty(project_with_submodule):
    """A submodule path that isn't checked out at all is a no-op (handled
    upstream), not a hard error."""
    repo, _sub_path, old_sha, new_sha, sym_map = project_with_submodule
    result = modules_from_submodule_diff(
        repo, ".repos/not-here", old_sha, new_sha, sym_map
    )
    assert result == set()
