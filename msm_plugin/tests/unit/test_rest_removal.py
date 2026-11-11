# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for the REST endpoints a release removes: lib/rest_removal.py,
Section 280 of the update scripts and Section 380 of the deployment scripts.

The REST definitions are read by mariadb-lsp's outline: the tests that need
it run where a binary outlines REST definitions as msm needs (MSM_TEST_LSP
names one, the binary or its folder, else the binaries msm would find are
tried).
"""

import os

import pytest

from msm_plugin.lib import lsp, management, rest_removal
from msm_plugin.lib.management import create_schema_project_folder


def _find_rest_lsp():
    given = os.environ.get("MSM_TEST_LSP")
    candidates = [given] if given else lsp._candidates(None)
    for candidate in candidates:
        binary = lsp.find_lsp(candidate)
        if binary is not None and lsp.outlines_rest(binary):
            return binary
    return None


LSP = _find_rest_lsp()
pytestmark = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary that outlines REST definitions as msm needs"
)

REST_V1 = """CREATE OR REPLACE REST SERVICE /notes ADD AUTH APP 'MRS' ADD AUTH APP 'Other';
CREATE OR REPLACE REST SCHEMA /app ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST VIEW /note ON SERVICE /notes SCHEMA /app AS `shop`.`note` {
    id: id
};
CREATE OR REPLACE REST PROCEDURE /add ON SERVICE /notes SCHEMA /app AS `shop`.`add_note`;
CREATE OR REPLACE REST SCHEMA /old ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST FUNCTION /f ON SERVICE /notes SCHEMA /old AS `shop`.`f`;
CREATE OR REPLACE REST SERVICE /gone ADD AUTH APP 'MRS';
CREATE OR REPLACE REST SCHEMA /g ON SERVICE /gone FROM `shop`;
CREATE OR REPLACE REST VIEW /v ON SERVICE /gone SCHEMA /g AS `shop`.`v`;
CREATE OR REPLACE REST SCHEMA /s ON SERVICE /shared FROM `shop`;"""

# /notes keeps MRS and /app (spelled in other cases), drops the Other link
# and /old; /add turns into a function; /gone goes, and /shared's schema
# /s, while /shared itself was never created here
REST_V2 = """CREATE OR REPLACE REST SERVICE /Notes ADD AUTH APP 'mrs';
CREATE OR REPLACE REST SCHEMA /APP ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST VIEW /Note ON SERVICE /notes SCHEMA /app AS `shop`.`note` {
    id: id
};
CREATE OR REPLACE REST FUNCTION /add ON SERVICE /notes SCHEMA /app AS `shop`.`add_note`;"""

V1_TO_V2 = [
    "DROP REST PROCEDURE IF EXISTS /add FROM SERVICE /notes SCHEMA /app;",
    "DROP REST FUNCTION IF EXISTS /f FROM SERVICE /notes SCHEMA /old;",
    "DROP REST VIEW IF EXISTS /v FROM SERVICE /gone SCHEMA /g;",
    "DROP REST SCHEMA IF EXISTS /old FROM SERVICE /notes;",
    "DROP REST SCHEMA IF EXISTS /g FROM SERVICE /gone;",
    "DROP REST SCHEMA IF EXISTS /s FROM SERVICE /shared;",
    "ALTER REST SERVICE /notes REMOVE AUTH APP 'Other' IF EXISTS;",
    "ALTER REST SERVICE /gone REMOVE AUTH APP 'MRS' IF EXISTS;",
    "DROP REST SERVICE IF EXISTS /gone;",
]


def outlined(sql: str) -> dict:
    return rest_removal.definition(lsp.outline(LSP, sql))[0]


def statements(sql: str) -> list:
    return [
        line for line in (sql or "").splitlines() if line.strip() and not line.startswith("--")
    ]


def commented(sql: str) -> list:
    return [line[3:] for line in sql.splitlines() if line.startswith("-- DROP REST")
            or line.startswith("-- ALTER REST")]


# -----------------------------------------------------------------------------
# The statements


def test_what_a_version_no_longer_defines_is_removed():
    removals, warnings = rest_removal.removals(outlined(REST_V1), outlined(REST_V2))
    assert removals == V1_TO_V2
    assert warnings == []


def test_nothing_is_removed_from_the_same_definition():
    assert rest_removal.removals(outlined(REST_V1), outlined(REST_V1)) == ([], [])


def test_a_developer_service_is_another_service():
    removals, _ = rest_removal.removals(
        outlined("CREATE REST SERVICE mike@/s;"), outlined("CREATE REST SERVICE /s;")
    )
    assert removals == ["DROP REST SERVICE IF EXISTS mike@/s;"]


def test_a_quoted_path_is_the_same_path():
    removals, _ = rest_removal.removals(
        outlined("CREATE REST SERVICE `/s`;"), outlined("CREATE REST SERVICE /S;")
    )
    assert removals == []


def test_the_first_release_and_a_section_without_rest_remove_nothing(monkeypatch):
    assert rest_removal.release_removals(None, REST_V1, "1.0.0", LSP) is None
    assert rest_removal.release_removals("-- nothing\n", REST_V1, "1.0.0", LSP) is None
    # SQL that is not REST needs no binary that reads REST definitions
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    assert rest_removal.release_removals(
        "CREATE TABLE IF NOT EXISTS `t` (`id` INT);", "", "1.0.0", LSP
    ) is None


def test_a_statement_msm_cannot_place_is_named():
    body = rest_removal.release_removals(
        "CREATE REST VIEW /x AS `shop`.`x`;", "", "1.0.0", LSP
    )
    assert "msm could not place every REST statement" in body
    assert "version 1.0.0, line 1: CREATE REST VIEW /x" in body
    assert statements(body) == []


def test_an_old_binary_is_refused(monkeypatch):
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    with pytest.raises(lsp.LspError, match="cannot read REST definitions"):
        rest_removal.release_removals(REST_V1, REST_V2, "1.0.0", LSP)


# -----------------------------------------------------------------------------
# What a deployment keeps


def test_a_removal_of_what_the_target_defines_again_is_commented_out():
    removals = "\n".join(V1_TO_V2) + "\n"
    # The service comes back without its schema /g and the MRS link
    target = "CREATE OR REPLACE REST SERVICE /gone;\n" + REST_V2
    kept = rest_removal.keep_redefined(lambda: LSP, target, removals, "1.2.0")
    assert commented(kept) == ["DROP REST SERVICE IF EXISTS /gone;"]
    assert statements(kept) == V1_TO_V2[:-1]
    assert "-- msm: version 1.2.0 defines this again, so it is kept" in kept


def test_removals_of_nothing_the_target_defines_need_one_outline(monkeypatch):
    removals = "\n".join(V1_TO_V2) + "\n"
    calls = []
    outline = lsp.outline
    monkeypatch.setattr(lsp, "outline", lambda *a: calls.append(a) or outline(*a))
    assert rest_removal.keep_redefined(lambda: LSP, REST_V2, removals, "1.1.0") is removals
    assert len(calls) == 2  # the target, then the target with the whole section


def test_no_binary_is_asked_without_both_sections():
    def no_binary():
        raise AssertionError("asked for a binary")

    assert rest_removal.keep_redefined(no_binary, "", "DROP REST SERVICE /x;", "1.0.0") == (
        "DROP REST SERVICE /x;"
    )
    assert rest_removal.keep_redefined(no_binary, REST_V1, "-- none\n", "1.0.0") == "-- none\n"


# -----------------------------------------------------------------------------
# Releases and deployment scripts


@pytest.fixture
def project(tmp_path):
    return create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.",
        license="MIT",
    )


def release(path, rest_sql, version, next_version) -> list:
    management.set_section_sql_content(
        "180", "\n" + rest_sql + "\n", management.get_schema_development_file_path(path)
    )
    return management.prepare_release(path, version, next_version, lsp_path=LSP)


def update_section_280(path, version_from, version_to) -> str:
    update = os.path.join(path, "releases", "updates", f"shop_{version_from}_to_{version_to}.sql")
    return management.get_file_sections(update)["280"]["sql_content"]


def test_a_release_lists_its_removals_and_a_deployment_keeps_what_comes_back(project):
    release(project, REST_V1, "1.0.0", "1.1.0")
    release(project, REST_V2, "1.1.0", "1.2.0")
    assert statements(update_section_280(project, "1.0.0", "1.1.0")) == V1_TO_V2

    # 1.2.0 has the service /gone again, without its schema and link
    release(project, REST_V2 + "\nCREATE OR REPLACE REST SERVICE /gone;", "1.2.0", "1.3.0")
    assert statements(update_section_280(project, "1.1.0", "1.2.0")) == []

    for version in ("1.0.0", "1.1.0", "1.2.0"):
        management.generate_deployment_script(project, version, lsp_path=LSP)
    deployment = os.path.join(project, "releases", "deployment", "shop_deployment_{}.sql")
    sections = management.get_file_sections(deployment.format("1.2.0"))
    section_380 = sections["380"]["sql_content"]
    assert commented(section_380) == ["DROP REST SERVICE IF EXISTS /gone;"]
    assert statements(section_380) == V1_TO_V2[:-1]
    assert list(sections).index("380") == list(sections).index("180") - 1

    # 1.1.0's own deployment drops all of it
    section_380 = management.get_file_sections(deployment.format("1.1.0"))["380"]["sql_content"]
    assert statements(section_380) == V1_TO_V2


def test_an_update_whose_removals_all_come_back_is_left_out(project):
    release(project, REST_V1, "1.0.0", "1.1.0")
    release(project, REST_V2, "1.1.0", "1.2.0")
    release(project, REST_V1, "1.2.0", "1.3.0")
    management.generate_deployment_script(project, "1.2.0", lsp_path=LSP)
    deployment = os.path.join(project, "releases", "deployment", "shop_deployment_1.2.0.sql")
    section_380 = management.get_file_sections(deployment)["380"]["sql_content"]
    # 1.1.0's update to 1.2.0 drops the function /add, as 1.1.0 spelled it
    assert statements(section_380) == [
        "DROP REST FUNCTION IF EXISTS /add FROM SERVICE /Notes SCHEMA /APP;",
    ]


def test_a_release_with_an_old_binary_writes_nothing(project, monkeypatch):
    release(project, REST_V1, "1.0.0", "1.1.0")
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    with pytest.raises(lsp.LspError):
        release(project, REST_V2, "1.1.0", "1.2.0")
    assert not os.path.exists(os.path.join(project, "releases", "versions", "shop_1.1.0.sql"))
    assert not os.path.exists(
        os.path.join(project, "releases", "updates", "shop_1.0.0_to_1.1.0.sql")
    )
