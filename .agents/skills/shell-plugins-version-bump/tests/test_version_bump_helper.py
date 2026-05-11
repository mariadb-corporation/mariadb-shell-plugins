#!/usr/bin/env python3

# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "version_bump_helper.py"


def load_helper_module():
    spec = importlib.util.spec_from_file_location("version_bump_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GatherCommitsTest(unittest.TestCase):
    def test_gather_commits_uses_previous_heading_as_range_start(self):
        helper = load_helper_module()

        git_calls = []

        def fake_git(*args):
            git_calls.append(args)
            if args[:3] == ("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e"):
                return "deadbeef\x1fBUG#123 Fix issue\x1fBody text\x1e"
            self.fail(f"Unexpected git call: {args!r}")

        with mock.patch.object(
            helper, "parse_versions", return_value=("2026.4.0+9.7.0", "2026.2.0+9.6.1")
        ), mock.patch.object(
            helper, "changelog_path", return_value=Path("gui/extension/CHANGELOG.md")
        ), mock.patch.object(
            helper,
            "find_changelog_heading_commit",
            side_effect=lambda version: (
                "previous-heading-commit"
                if version == "2026.2.0+9.6.1"
                else self.fail(f"Unexpected version lookup: {version!r}")
            ),
        ), mock.patch.object(
            helper, "git", side_effect=fake_git
        ):
            commits = helper.gather_commits()

        self.assertEqual(
            git_calls,
            [
                (
                    "log",
                    "--no-merges",
                    "--format=%H%x1f%s%x1f%b%x1e",
                    "previous-heading-commit..HEAD",
                )
            ],
        )
        self.assertEqual(commits[0]["hash"], "deadbeef")
        self.assertEqual(commits[0]["token"], "BUG#123")
        self.assertEqual(commits[0]["section"], "Fixes")
        self.assertFalse(commits[0]["maintenance"])


class DiscoverFilesTest(unittest.TestCase):
    def test_discover_files_excludes_changelog(self):
        helper = load_helper_module()

        changelog = helper.repo_root() / "gui" / "extension" / "CHANGELOG.md"
        candidates = list(helper.iter_candidate_files())

        self.assertNotIn(changelog, candidates)


class RemoteHelperCheckTest(unittest.TestCase):
    def test_validate_remote_helper_urls_matches_summarized_shell_prefix(self):
        helper = load_helper_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            remote_helper = (
                root / "migration_plugin" / "lib" / "backend" / "remote_helper.py"
            )
            remote_helper.parent.mkdir(parents=True)
            remote_helper.write_text(
                """
k_repo_mysqlsh_url = {
    "aarch64": "https://cdn.mysql.com/Downloads/MySQL-Shell/mysql-shell-9.7.0-1.el8.aarch64.rpm",
    "x86_64": "https://cdn.mysql.com/Downloads/MySQL-Shell/mysql-shell-9.7.0-1.el8.x86_64.rpm",
}
""".strip() + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                helper, "repo_root", return_value=root
            ), mock.patch.object(
                helper,
                "parse_versions",
                return_value=("2026.4.0+9.7.0", "2026.2.0+9.6.1"),
            ):
                result = helper.validate_remote_helper_urls()

        self.assertTrue(result["all_match"])
        self.assertEqual(result["summary_version"], "2026.4.0+9.7.0")
        self.assertEqual(result["expected_shell_prefix"], "mysql-shell-9.7.0-")
        self.assertEqual(result["path"], remote_helper.relative_to(root).as_posix())
        self.assertEqual(len(result["entries"]), 2)
        self.assertTrue(
            all(entry["matches_expected_prefix"] for entry in result["entries"])
        )

    def test_validate_remote_helper_urls_detects_mismatch_against_summarized_version(
        self,
    ):
        helper = load_helper_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            remote_helper = (
                root / "migration_plugin" / "lib" / "backend" / "remote_helper.py"
            )
            remote_helper.parent.mkdir(parents=True)
            remote_helper.write_text(
                """
k_repo_mysqlsh_url = {
    "aarch64": "https://cdn.mysql.com/Downloads/MySQL-Shell/mysql-shell-9.6.1-1.el8.aarch64.rpm",
}
""".strip() + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                helper, "repo_root", return_value=root
            ), mock.patch.object(
                helper,
                "parse_versions",
                return_value=("2026.4.0+9.7.0", "2026.2.0+9.6.1"),
            ):
                result = helper.validate_remote_helper_urls()

        self.assertFalse(result["all_match"])
        self.assertEqual(result["summary_version"], "2026.4.0+9.7.0")
        self.assertEqual(
            result["entries"][0]["rpm_name"],
            "mysql-shell-9.6.1-1.el8.aarch64.rpm",
        )
        self.assertFalse(result["entries"][0]["matches_expected_prefix"])


if __name__ == "__main__":
    unittest.main()
