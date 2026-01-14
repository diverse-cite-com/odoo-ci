#!/usr/bin/env python3
"""
Unit tests for detect_changed_modules.py
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from detect_changed_modules import (
    build_symlink_map,
    is_odoo_module,
    extract_module_from_path,
    detect_changed_modules,
    get_changed_files,
    format_output,
)


@pytest.fixture
def temp_addons_dir():
    """Create a temporary addons directory with test modules."""
    with tempfile.TemporaryDirectory() as tmpdir:
        addons_dir = Path(tmpdir) / "addons"
        addons_dir.mkdir()

        # Create a direct module
        direct_module = addons_dir / "direct_module"
        direct_module.mkdir()
        (direct_module / "__manifest__.py").write_text('{"name": "Direct Module"}')

        # Create another direct module
        another_module = addons_dir / "another_module"
        another_module.mkdir()
        (another_module / "__manifest__.py").write_text('{"name": "Another Module"}')

        # Create a .repos directory with submodule
        repos_dir = Path(tmpdir) / ".repos"
        repos_dir.mkdir()

        submodule_dir = repos_dir / "bemade-addons"
        submodule_dir.mkdir()

        submodule_module = submodule_dir / "caldav_sync"
        submodule_module.mkdir()
        (submodule_module / "__manifest__.py").write_text('{"name": "CalDAV Sync"}')

        # Create symlink in addons pointing to .repos
        # Use relative path that works: from addons/caldav_sync -> ../.repos/bemade-addons/caldav_sync
        symlink_target = addons_dir / "caldav_sync"
        os.symlink("../.repos/bemade-addons/caldav_sync", symlink_target)

        yield Path(tmpdir)


class TestBuildSymlinkMap:
    def test_empty_directory(self, tmp_path):
        """Test with empty addons directory."""
        addons_dir = tmp_path / "addons"
        addons_dir.mkdir()

        result = build_symlink_map(addons_dir)
        assert result == {}

    def test_nonexistent_directory(self, tmp_path):
        """Test with non-existent directory."""
        result = build_symlink_map(tmp_path / "nonexistent")
        assert result == {}

    def test_with_symlinks(self, temp_addons_dir):
        """Test building symlink map with actual symlinks."""
        addons_dir = temp_addons_dir / "addons"
        result = build_symlink_map(addons_dir)

        # Should map the .repos path to module name
        assert "bemade-addons/caldav_sync" in result
        assert result["bemade-addons/caldav_sync"] == "caldav_sync"

    def test_ignores_regular_directories(self, temp_addons_dir):
        """Test that regular directories are not included in map."""
        addons_dir = temp_addons_dir / "addons"
        result = build_symlink_map(addons_dir)

        # direct_module is not a symlink, should not be in map
        assert "direct_module" not in result.values() or "direct_module" not in result


class TestIsOdooModule:
    def test_valid_module_with_manifest(self, tmp_path):
        """Test detection of module with __manifest__.py."""
        module_dir = tmp_path / "my_module"
        module_dir.mkdir()
        (module_dir / "__manifest__.py").write_text('{"name": "Test"}')

        assert is_odoo_module(module_dir) is True

    def test_valid_module_with_openerp(self, tmp_path):
        """Test detection of module with __openerp__.py (legacy)."""
        module_dir = tmp_path / "old_module"
        module_dir.mkdir()
        (module_dir / "__openerp__.py").write_text('{"name": "Old Test"}')

        assert is_odoo_module(module_dir) is True

    def test_invalid_module_no_manifest(self, tmp_path):
        """Test that directory without manifest is not a module."""
        module_dir = tmp_path / "not_a_module"
        module_dir.mkdir()
        (module_dir / "some_file.py").write_text("# not a module")

        assert is_odoo_module(module_dir) is False

    def test_file_not_directory(self, tmp_path):
        """Test that a file is not detected as module."""
        file_path = tmp_path / "some_file.py"
        file_path.write_text("# just a file")

        assert is_odoo_module(file_path) is False

    def test_nonexistent_path(self, tmp_path):
        """Test with non-existent path."""
        assert is_odoo_module(tmp_path / "nonexistent") is False


class TestExtractModuleFromPath:
    def test_direct_addon_change(self, temp_addons_dir):
        """Test extracting module from direct addons/ path."""
        addons_dir = temp_addons_dir / "addons"
        symlink_map = build_symlink_map(addons_dir)

        result = extract_module_from_path(
            "addons/direct_module/models/model.py",
            addons_dir,
            symlink_map,
        )
        assert result == "direct_module"

    def test_symlinked_addon_via_addons_path(self, temp_addons_dir):
        """Test extracting module from symlinked path via addons/."""
        addons_dir = temp_addons_dir / "addons"
        symlink_map = build_symlink_map(addons_dir)

        result = extract_module_from_path(
            "addons/caldav_sync/models/calendar.py",
            addons_dir,
            symlink_map,
        )
        assert result == "caldav_sync"

    def test_repos_path_change(self, temp_addons_dir):
        """Test extracting module from .repos/ path."""
        addons_dir = temp_addons_dir / "addons"
        symlink_map = build_symlink_map(addons_dir)

        result = extract_module_from_path(
            ".repos/bemade-addons/caldav_sync/models/calendar.py",
            addons_dir,
            symlink_map,
        )
        assert result == "caldav_sync"

    def test_unrelated_file(self, temp_addons_dir):
        """Test that unrelated files return None."""
        addons_dir = temp_addons_dir / "addons"
        symlink_map = build_symlink_map(addons_dir)

        result = extract_module_from_path(
            "README.md",
            addons_dir,
            symlink_map,
        )
        assert result is None

    def test_repos_not_symlinked(self, temp_addons_dir):
        """Test .repos module that isn't symlinked in addons/."""
        addons_dir = temp_addons_dir / "addons"

        # Create a module in .repos that's NOT symlinked
        other_module = temp_addons_dir / ".repos" / "other-repo" / "unused_module"
        other_module.mkdir(parents=True)
        (other_module / "__manifest__.py").write_text('{"name": "Unused"}')

        symlink_map = build_symlink_map(addons_dir)

        result = extract_module_from_path(
            ".repos/other-repo/unused_module/models/model.py",
            addons_dir,
            symlink_map,
        )
        # Should return None because it's not symlinked in addons/
        assert result is None


class TestGetChangedFiles:
    @patch("detect_changed_modules.run_git_command")
    def test_returns_file_list(self, mock_git):
        """Test parsing git diff output."""

        def side_effect(args, cwd=None):
            if "--submodule=diff" in args:
                return ""
            return "addons/module1/file.py\naddons/module2/file.py"

        mock_git.side_effect = side_effect

        result = get_changed_files("HEAD~1", "HEAD")

        assert result == ["addons/module1/file.py", "addons/module2/file.py"]

    @patch("detect_changed_modules.run_git_command")
    def test_empty_diff(self, mock_git):
        """Test handling empty diff."""
        mock_git.return_value = ""

        result = get_changed_files("HEAD~1", "HEAD")

        assert result == []

    @patch("detect_changed_modules.run_git_command")
    def test_filters_empty_lines(self, mock_git):
        """Test that empty lines are filtered out."""

        def side_effect(args, cwd=None):
            if "--submodule=diff" in args:
                return ""
            return "file1.py\n\nfile2.py\n"

        mock_git.side_effect = side_effect

        result = get_changed_files("HEAD~1", "HEAD")

        assert result == ["file1.py", "file2.py"]

    @patch("detect_changed_modules.run_git_command")
    def test_parses_submodule_diff(self, mock_git):
        """Test parsing submodule diff output to extract file paths."""

        def side_effect(args, cwd=None):
            if "--submodule=diff" in args:
                return """Submodule .repos/bemade-addons 8416794..980ac3b:
diff --git a/.repos/bemade-addons/odoo_herd/__manifest__.py b/.repos/bemade-addons/odoo_herd/__manifest__.py
index 699b338..a5cad92 100644
--- a/.repos/bemade-addons/odoo_herd/__manifest__.py
+++ b/.repos/bemade-addons/odoo_herd/__manifest__.py
@@ -1,6 +1,6 @@
 {
     "name": "Odoo Herd",
-    "version": "19.0.1.2.0",
+    "version": "19.0.1.2.1",
"""
            return ".repos/bemade-addons"  # Regular diff just shows submodule changed

        mock_git.side_effect = side_effect

        result = get_changed_files("HEAD~1", "HEAD")

        assert ".repos/bemade-addons/odoo_herd/__manifest__.py" in result

    @patch("detect_changed_modules.run_git_command")
    def test_combines_regular_and_submodule_changes(self, mock_git):
        """Test that regular and submodule changes are combined."""

        def side_effect(args, cwd=None):
            if "--submodule=diff" in args:
                return """Submodule .repos/bemade-addons 123..456:
diff --git a/.repos/bemade-addons/module_a/file.py b/.repos/bemade-addons/module_a/file.py
"""
            return "addons/direct_module/file.py"

        mock_git.side_effect = side_effect

        result = get_changed_files("HEAD~1", "HEAD")

        assert "addons/direct_module/file.py" in result
        assert ".repos/bemade-addons/module_a/file.py" in result


class TestDetectChangedModules:
    @patch("detect_changed_modules.get_changed_files")
    def test_detects_direct_module_changes(self, mock_files, temp_addons_dir):
        """Test detection of changes in direct modules."""
        mock_files.return_value = [
            "addons/direct_module/models/model.py",
            "addons/direct_module/__manifest__.py",
        ]

        result = detect_changed_modules(
            "HEAD~1",
            "HEAD",
            temp_addons_dir / "addons",
        )

        assert result == {"direct_module"}

    @patch("detect_changed_modules.get_changed_files")
    def test_detects_symlinked_module_changes(self, mock_files, temp_addons_dir):
        """Test detection of changes in symlinked modules via .repos."""
        mock_files.return_value = [
            ".repos/bemade-addons/caldav_sync/models/calendar.py",
        ]

        result = detect_changed_modules(
            "HEAD~1",
            "HEAD",
            temp_addons_dir / "addons",
        )

        assert result == {"caldav_sync"}

    @patch("detect_changed_modules.get_changed_files")
    def test_detects_multiple_modules(self, mock_files, temp_addons_dir):
        """Test detection of changes across multiple modules."""
        mock_files.return_value = [
            "addons/direct_module/models/model.py",
            "addons/another_module/views/view.xml",
            ".repos/bemade-addons/caldav_sync/models/calendar.py",
        ]

        result = detect_changed_modules(
            "HEAD~1",
            "HEAD",
            temp_addons_dir / "addons",
        )

        assert result == {"direct_module", "another_module", "caldav_sync"}

    @patch("detect_changed_modules.get_changed_files")
    def test_ignores_non_module_changes(self, mock_files, temp_addons_dir):
        """Test that non-module changes are ignored."""
        mock_files.return_value = [
            "README.md",
            "requirements.txt",
            ".gitlab-ci.yml",
        ]

        result = detect_changed_modules(
            "HEAD~1",
            "HEAD",
            temp_addons_dir / "addons",
        )

        assert result == set()

    @patch("detect_changed_modules.get_changed_files")
    def test_no_changes(self, mock_files, temp_addons_dir):
        """Test handling no changes."""
        mock_files.return_value = []

        result = detect_changed_modules(
            "HEAD~1",
            "HEAD",
            temp_addons_dir / "addons",
        )

        assert result == set()


class TestFormatOutput:
    def test_comma_format(self):
        """Test comma-separated output."""
        modules = {"module_b", "module_a", "module_c"}
        result = format_output(modules, "comma")
        assert result == "module_a,module_b,module_c"

    def test_newline_format(self):
        """Test newline-separated output."""
        modules = {"module_b", "module_a"}
        result = format_output(modules, "newline")
        assert result == "module_a\nmodule_b"

    def test_json_format(self):
        """Test JSON output."""
        modules = {"module_b", "module_a"}
        result = format_output(modules, "json")
        parsed = json.loads(result)
        assert parsed == ["module_a", "module_b"]

    def test_empty_set(self):
        """Test formatting empty set."""
        result = format_output(set(), "comma")
        assert result == ""

    def test_invalid_format(self):
        """Test that invalid format raises error."""
        with pytest.raises(ValueError):
            format_output({"module"}, "invalid")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
