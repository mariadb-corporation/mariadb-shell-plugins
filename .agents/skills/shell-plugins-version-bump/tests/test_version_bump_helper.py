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
import unittest
from pathlib import Path
from unittest import mock


HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "version_bump_helper.py"
)


def load_helper_module():
    spec = importlib.util.spec_from_file_location("version_bump_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GatherCommitsTest(unittest.TestCase):
    def test_gather_commits_uses_current_heading_as_range_start(self):
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
                "current-heading-commit"
                if version == "2026.4.0+9.7.0"
                else self.fail(f"Unexpected version lookup: {version!r}")
            ),
        ), mock.patch.object(helper, "git", side_effect=fake_git):
            commits = helper.gather_commits()

        self.assertEqual(
            git_calls,
            [
                (
                    "log",
                    "--no-merges",
                    "--format=%H%x1f%s%x1f%b%x1e",
                    "current-heading-commit..HEAD",
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


if __name__ == "__main__":
    unittest.main()
