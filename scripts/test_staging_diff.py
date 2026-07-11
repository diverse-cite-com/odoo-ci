#!/usr/bin/env python3
"""Unit tests for staging_diff.py

Focus: the submodule-diff path must NEVER silently drop modules. Before the
fix, `modules_from_submodule_diff` returned an empty set when the base pin
SHA was absent from a shallow submodule clone, which produced a PARTIAL
upgrade list and shipped un-migrated modules to prod. It must now fail loud.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from staging_diff import addons_link_map, modules_from_submodule_diff


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


def _manifest(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "__manifest__.py").write_text(f"{{'name': '{name}'}}\n")


def _run_staging_diff(repo: Path, base: str, head: str) -> dict:
    out = repo / "staging-refresh.json"
    subprocess.run(
        [
            "python3",
            str(Path(__file__).parent / "staging_diff.py"),
            "--repo", str(repo),
            "--base", base,
            "--head", head,
            "--verbose",
            "-o", str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text())


@pytest.fixture
def project_with_vendored(tmp_path):
    """A plain git repo with a client addon under addons/ and a vendored dep
    under vendored/ (on the Odoo addons path, no addons/ symlink)."""
    repo = tmp_path / "project"
    _manifest(repo / "addons" / "rwi_sap_b1_to_odoo", "RWI SAP")
    _manifest(repo / "vendored" / "sap_b1_to_odoo", "SAP B1 to Odoo")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    return repo, base


def test_vendored_module_change_makes_upgrade_list(project_with_vendored):
    """A change touching ONLY a vendored/ module must appear in the upgrade
    list. Regression: the `available` filter used to enumerate addons/ only,
    silently dropping vendored deps (e.g. sap_b1_to_odoo), so a new base model
    they introduced was never reflected and any upgraded dependent crashed on
    ir_model_inherit's NOT NULL parent_id."""
    repo, base = project_with_vendored
    (repo / "vendored" / "sap_b1_to_odoo" / "models.py").write_text("# new model\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add model to vendored dep")
    head = _git(repo, "rev-parse", "HEAD")

    result = _run_staging_diff(repo, base, head)
    assert "sap_b1_to_odoo" in result["modules"]


# ---------------------------------------------------------------------------
# 2026-07-11 fitcrew prod incident (second occurrence of 2026-07-05):
# in CI the filesystem-based sym_map failed to match a bumped submodule's
# modules (unreproducible outside the runner) and the upgrade list silently
# shipped without bemade_sports_clinic. The mapping must not depend on the
# checkout filesystem, and an unmappable KNOWN module must fail the job.
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_committed_symlink(project_with_submodule):
    """The base fixture plus a project-level git history committing the
    addons/<name> symlink, so the git-tree link map has something to read."""
    repo, sub_path, old_sha, new_sha, sym_map = project_with_submodule
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@test")
    _git(repo, "config", "user.name", "ci")
    # Don't commit .repos/ content as part of the project tree; the symlink
    # is what matters for the link map.
    (repo / ".gitignore").write_text(".repos/\n")
    _git(repo, "add", ".gitignore", "addons")
    _git(repo, "commit", "-q", "-m", "project with addons symlink")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, sub_path, old_sha, new_sha, sym_map, head


def test_git_tree_map_is_filesystem_independent(project_with_committed_symlink):
    """The git-tree link map alone (empty filesystem sym_map) must map the
    bumped submodule's module — this is the fix for the incident class where
    the filesystem map came up empty in CI."""
    repo, sub_path, old_sha, new_sha, _sym_map, head = project_with_committed_symlink
    link_map = addons_link_map(repo, head)
    assert link_map.get(f"{sub_path}/my_module") == "my_module"

    result = modules_from_submodule_diff(
        repo, sub_path, old_sha, new_sha, sym_map={}, link_map=link_map
    )
    assert result == {"my_module"}


def test_fails_loud_when_linked_module_cannot_be_mapped(project_with_submodule):
    """A changed path whose top-level dir NAME is a linked module, with a
    mapping that cannot place it (wrong target path, empty sym_map), must
    raise instead of emitting a partial upgrade list."""
    repo, sub_path, old_sha, new_sha, _sym_map, = (*project_with_submodule,)
    broken_link_map = {".repos/somewhere-else/my_module": "my_module"}

    with pytest.raises(SystemExit) as exc:
        modules_from_submodule_diff(
            repo, sub_path, old_sha, new_sha, sym_map={}, link_map=broken_link_map
        )
    assert "partial upgrade list" in str(exc.value)


def test_benign_unlinked_module_bump_stays_empty(project_with_submodule):
    """A bump touching only modules this project does NOT link (e.g. another
    client's modules in a shared addons repo) is a legitimate empty result —
    no exception."""
    repo, sub_path, old_sha, new_sha, _sym_map = project_with_submodule
    other_map = {f"{sub_path}/other_module": "other_module"}

    result = modules_from_submodule_diff(
        repo, sub_path, old_sha, new_sha, sym_map={}, link_map=other_map
    )
    assert result == set()
