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

"""Tests for renaming a project's schema: lib/renaming.py.

The SQL is renamed by the binary's `edit` op rename_schema: the tests that
need it run where a binary has it (MSM_TEST_LSP names one, the binary or its
folder, else the binaries msm would find are tried); the refusals that come
before the binary is asked run everywhere.
"""

import json
import os

import pytest

from msm_plugin import management as msm
from msm_plugin.lib import core, lsp, management, modeling, projects, renaming
from msm_plugin.lib.management import create_schema_project_folder
from msm_plugin.tests.unit import helpers


LSP = helpers.find_test_lsp(lambda binary: renaming.RENAME_OP in lsp.edit_ops(binary))
needs_rename_lsp = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary with the edit op rename_schema"
)

TABLES_SQL = """CREATE TABLE IF NOT EXISTS customer (
    id INT UNSIGNED NOT NULL PRIMARY KEY,
    note VARCHAR(20) DEFAULT 'shop.x'
);

CREATE TABLE IF NOT EXISTS shop.orders (
    id INT UNSIGNED NOT NULL PRIMARY KEY,
    customer_id INT UNSIGNED NOT NULL,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id)
        REFERENCES `shop`.`customer` (id)
);

CREATE OR REPLACE VIEW shop_view AS SELECT shop.id FROM customer AS shop;"""


@pytest.fixture
def data_path(tmp_path, monkeypatch):
    """The project lists, in a folder of the test's own."""
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setattr(projects, "get_data_path", lambda: str(path))
    return path


@pytest.fixture
def project(tmp_path, data_path):
    """A listed project of the schema `shop` with tables, a diagram and a
    current snapshot."""
    folder = tmp_path / "work"
    folder.mkdir()
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(folder), copyright_holder="Example Ltd.",
        license="MIT",
    )
    management.set_section_sql_content(
        "140", "\n" + TABLES_SQL + "\n", management.get_schema_development_file_path(path)
    )
    if LSP is not None:
        modeling.create_diagram(path, "Main")
        modeling.update_schema_definition(path, LSP)
    projects.add_project(path, folder="/Shop", caption="The shop", color="green")
    return path


def rename(path, *args, **kwargs):
    return renaming.rename_schema(path, *args, lsp_path=LSP, **kwargs)


def read(path, *parts) -> str:
    with open(os.path.join(path, *parts)) as f:
        return f.read()


def folder_contents(path) -> list:
    return sorted(os.listdir(os.path.dirname(path)))


def release(path) -> None:
    with open(os.path.join(path, "releases", "versions", "shop_1.0.0.sql"), "w") as f:
        f.write("-- released\n")


# -----------------------------------------------------------------------------
# Refused before the binary is asked


def test_a_released_project_is_refused(project):
    release(project)
    answer = rename(project, "store", dry_run=True)
    assert answer["allowed"] is False
    assert answer["reason"] == renaming.REASON_RELEASED
    assert "1.0.0" in answer["message"]
    assert answer["project_path"] == answer["previous_project_path"] == project
    with pytest.raises(renaming.RenameRefused, match="released already"):
        rename(project, "store")
    assert os.path.isdir(project)


def test_a_released_project_is_refused_without_a_name(project):
    release(project)
    assert rename(project, None, dry_run=True)["reason"] == renaming.REASON_RELEASED


def test_a_binary_without_the_op_is_named(project, monkeypatch):
    monkeypatch.setattr(lsp, "find_lsp", lambda lsp_path=None: "/opt/old/mariadb-lsp")
    monkeypatch.setattr(lsp, "edit_ops", lambda binary: frozenset({"rename_table"}))
    answer = rename(project, None, dry_run=True)
    assert answer["reason"] == renaming.REASON_LSP_MISSING
    assert "/opt/old/mariadb-lsp" in answer["message"]
    assert "cannot rename schemas" in answer["message"]


def test_a_missing_binary_is_named(project, monkeypatch):
    monkeypatch.setattr(lsp, "find_lsp", lambda lsp_path=None: None)
    answer = rename(project, "store", dry_run=True)
    assert answer["reason"] == renaming.REASON_LSP_MISSING
    assert "was not found" in answer["message"]
    with pytest.raises(renaming.RenameRefused):
        rename(project, "store")


def test_released_versions_are_listed(project):
    assert management.listed_last_released_version(project) is None
    release(project)
    assert management.listed_last_released_version(project) == "1.0.0"
    assert management.listed_last_released_version(project + ".missing") is None


# -----------------------------------------------------------------------------
# The name and the folder


@needs_rename_lsp
def test_a_nameless_dry_run_checks_the_project_only(project):
    answer = rename(project, None, dry_run=True)
    assert answer == renaming.answer(project)
    with pytest.raises(renaming.RenameRefused, match="has to be given"):
        rename(project, None)


@needs_rename_lsp
@pytest.mark.parametrize("name, reason", [
    ("shop", renaming.REASON_SAME_NAME),
    ("", renaming.REASON_INVALID_NAME),
    ("  ", renaming.REASON_INVALID_NAME),
    ("my shop", renaming.REASON_INVALID_NAME),
    ("1234", renaming.REASON_INVALID_NAME),
    ("x" * 65, renaming.REASON_INVALID_NAME),
    ("/*<msm:undeclared>*/store", renaming.REASON_INVALID_NAME),
])
def test_names_msm_refuses(project, name, reason):
    answer = rename(project, name, dry_run=True)
    assert (answer["allowed"], answer["reason"]) == (False, reason)
    assert answer["message"]
    with pytest.raises(renaming.RenameRefused):
        rename(project, name)


@needs_rename_lsp
def test_a_name_with_quotes_is_allowed_when_asked_for(project):
    answer = rename(project, "my shop", dry_run=True, allow_special_chars=True)
    assert answer["allowed"] is True
    assert answer["project_path"].endswith("my_shop.msm.project")


@needs_rename_lsp
def test_an_existing_folder_of_the_new_name_is_refused(project):
    os.makedirs(os.path.join(os.path.dirname(project), "store.msm.project"))
    answer = rename(project, "store", dry_run=True)
    assert answer["reason"] == renaming.REASON_FOLDER_EXISTS
    with pytest.raises(renaming.RenameRefused, match="exists already"):
        rename(project, "store")


@needs_rename_lsp
def test_a_folder_the_user_named_keeps_its_name(project, tmp_path):
    custom = str(tmp_path / "work" / "my-project")
    os.rename(project, custom)
    answer = rename(custom, "store")
    assert answer["project_path"] == answer["previous_project_path"] == custom
    assert os.path.exists(os.path.join(custom, "development", "store_next.sql"))
    assert folder_contents(custom) == ["my-project"]


# -----------------------------------------------------------------------------
# The rename


@needs_rename_lsp
def test_a_dry_run_changes_nothing(project):
    before = {
        name: read(project, name)
        for name in ("msm.project.json", "README.md", "development/shop_next.sql")
    }
    answer = rename(project, "store", dry_run=True)
    new_path = os.path.join(os.path.dirname(project), "store.msm.project")
    assert answer["allowed"] is True
    assert answer["project_path"] == new_path
    assert answer["previous_project_path"] == project
    assert os.path.join(new_path, "development", "store_next.sql") in answer["files"]
    assert answer["migration"] == []
    assert folder_contents(project) == ["shop.msm.project"]
    assert {name: read(project, name) for name in before} == before


@needs_rename_lsp
def test_a_rename_reaches_every_file(project):
    answer = rename(project, "store")
    path = answer["project_path"]
    assert path == os.path.join(os.path.dirname(project), "store.msm.project")
    assert answer["previous_project_path"] == project
    assert folder_contents(project) == ["store.msm.project"]

    settings = json.loads(read(path, "msm.project.json"))
    assert (settings["schemaName"], settings["schemaFileName"]) == ("store", "store")
    assert os.listdir(os.path.join(path, "development")).count("shop_next.sql") == 0

    script = read(path, "development", "store_next.sql")
    assert "CREATE SCHEMA IF NOT EXISTS `store`" in script
    assert "USE `store`;" in script
    assert "CREATE TABLE IF NOT EXISTS store.orders" in script
    assert "REFERENCES `store`.`customer`" in script
    assert "-- `store`" in script
    # Not the schema: an alias and a string only spelled as it
    assert "SELECT shop.id FROM customer AS shop" in script
    assert "'shop.x'" in script
    # The banners of Section 150's commented-out example
    assert "-- VIEW `store`.`my_view`" in script
    assert "-- PROCEDURE `store`.`my_proc`" in script
    assert "`shop`.`my_" not in script

    assert read(path, "README.md").startswith("# Database Schema Project `store`")
    assert read(path, "CHANGELOG.md").startswith("# Database Schema Change Log for `store`")

    rows = modeling.read_diagram_rows(path)
    snapshot = modeling._schema_row(rows)["document"]
    assert snapshot["schema"] == "store"
    assert {table["name"] for table in snapshot["tables"]} == {"customer", "orders"}
    assert modeling.snapshot_is_current(path)

    assert sorted(answer["files"]) == answer["files"]
    assert all(file.startswith(path + os.sep) for file in answer["files"])
    assert any("string" in warning and "store_next.sql" in warning
               for warning in answer["warnings"])


@needs_rename_lsp
def test_the_project_lists_follow_the_folder(project):
    owner = "0b8e1c7e-3a7f-4c1e-9a55-2f1c2d3e4f50"
    projects.add_project(project, folder="/Theirs", owner=owner, mcp_access=False)
    answer = rename(project, "store")
    [mine] = projects.list_projects()
    assert mine["project_path"] == answer["project_path"]
    assert (mine["folder"], mine["caption"], mine["color"]) == ("/Shop", "The shop", "green")
    assert mine["schema_name"] == "store"
    [theirs] = projects.list_projects(owner)
    assert (theirs["project_path"], theirs["folder"], theirs["mcp_access"]) == (
        answer["project_path"], "/Theirs", False
    )


@needs_rename_lsp
def test_projects_depending_on_the_schema_are_named(project, tmp_path):
    other = create_schema_project_folder(
        schema_name="reports", target_path=str(tmp_path / "work"), copyright_holder="X",
        license="MIT",
    )
    management.set_project_settings(other, {"schemaDependencies": ["shop"]})
    projects.add_project(other)
    answer = rename(project, "store", dry_run=True)
    assert any(other in warning and "schemaDependencies" in warning
               for warning in answer["warnings"])


@needs_rename_lsp
def test_a_markdown_line_the_user_changed_stays(project):
    readme = os.path.join(project, "README.md")
    text = read(readme).replace(
        "# Database Schema Project `shop`", "# Our `shop` schema"
    )
    with open(readme, "w") as f:
        f.write(text)
    path = rename(project, "store")["project_path"]
    readme = read(path, "README.md")
    assert readme.startswith("# Our `shop` schema")
    assert "development of the `store` database schema" in readme


@needs_rename_lsp
def test_an_example_banner_the_user_changed_stays(project):
    dev_file = management.get_schema_development_file_path(project)
    text = read(dev_file).replace("-- VIEW `shop`.`my_view`", "-- VIEW `shop`.`our_view`")
    with open(dev_file, "w") as f:
        f.write(text)
    script = read(rename(project, "store")["project_path"], "development", "store_next.sql")
    assert "-- VIEW `shop`.`our_view`" in script
    assert "-- PROCEDURE `store`.`my_proc`" in script


@needs_rename_lsp
def test_a_change_of_case_renames_the_folder(project):
    answer = rename(project, "Shop")
    assert answer["project_path"].endswith("Shop.msm.project")
    assert folder_contents(project) == ["Shop.msm.project"]
    assert json.loads(read(answer["project_path"], "msm.project.json"))["schemaName"] == "Shop"


@needs_rename_lsp
def test_a_name_with_placeholders_is_written_as_is(project):
    management.set_project_settings(
        project, {"substitutions": {"schema_prefix": {"default": "dev_"}}}
    )
    name = "/*<msm:schema_prefix>*/store"
    path = rename(project, name)["project_path"]
    settings = json.loads(read(path, "msm.project.json"))
    assert settings["schemaName"] == name
    script = read(path, "development", f"{settings['schemaFileName']}_next.sql")
    assert f"USE {name};" in script
    assert f"CREATE TABLE IF NOT EXISTS {name}.orders" in script

    # And back to a plain name
    answer = rename(path, "shop")
    script = read(answer["project_path"], "development", "shop_next.sql")
    # Written as the references were: unquoted, as the plain name needs no quotes
    assert "USE shop;" in script
    assert "CREATE TABLE IF NOT EXISTS shop.orders" in script


@needs_rename_lsp
def test_a_failure_leaves_the_project_as_it_was(project, monkeypatch):
    before = read(project, "development", "shop_next.sql")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(modeling, "_replace_snapshot", fail)
    with pytest.raises(OSError, match="disk full"):
        rename(project, "store")
    assert folder_contents(project) == ["shop.msm.project"]
    assert read(project, "development", "shop_next.sql") == before
    assert projects.list_projects()[0]["project_path"] == project


@needs_rename_lsp
def test_a_failing_move_puts_the_folder_back(project, monkeypatch):
    real_rename = os.rename

    def fail_second(source, target):
        if target.endswith("store.msm.project"):
            raise OSError("busy")
        real_rename(source, target)

    monkeypatch.setattr(renaming.os, "rename", fail_second)
    with pytest.raises(ValueError, match="could not be moved"):
        rename(project, "store")
    assert folder_contents(project) == ["shop.msm.project"]
    assert json.loads(read(project, "msm.project.json"))["schemaName"] == "shop"


@needs_rename_lsp
def test_a_name_that_would_merge_two_schemas_is_refused(project):
    management.set_project_settings(
        project, {"substitutions": {"schema_prefix": {"default": "dev_"}}}
    )
    path = rename(project, "/*<msm:schema_prefix>*/store")["project_path"]
    # A bare `store` the script names besides: one schema with the prefix
    # left out, so the two would be one after substitution
    settings = management.get_project_settings(path)
    management.set_section_sql_content(
        "150", "\nCREATE OR REPLACE VIEW v AS SELECT id FROM store.t;\n",
        os.path.join(path, "development", f"{settings['schemaFileName']}_next.sql"),
    )
    answer = rename(path, "store", dry_run=True)
    assert (answer["allowed"], answer["reason"]) == (False, renaming.REASON_INVALID_NAME)
    assert "`store`" in answer["message"]


@needs_rename_lsp
def test_a_file_of_the_new_name_is_not_replaced(project):
    other = os.path.join(project, "development", "store_next.sql")
    with open(other, "w") as f:
        f.write("-- someone else's\n")
    answer = rename(project, "store", dry_run=True)
    assert (answer["allowed"], answer["reason"]) == (False, renaming.REASON_INVALID_NAME)
    assert "development/store_next.sql" in answer["message"]
    with pytest.raises(renaming.RenameRefused):
        rename(project, "store")
    assert read(other) == "-- someone else's\n"
    assert folder_contents(project) == ["shop.msm.project"]


@needs_rename_lsp
def test_a_project_kept_from_llms_is_not_named_to_them(project, tmp_path):
    other = create_schema_project_folder(
        schema_name="reports", target_path=str(tmp_path / "work"), copyright_holder="X",
        license="MIT",
    )
    management.set_project_settings(other, {"schemaDependencies": ["shop"]})
    projects.add_project(other, mcp_access=False)
    for mcp_access_only, named in ((False, True), (True, False)):
        answer = rename(project, "store", dry_run=True, mcp_access_only=mcp_access_only)
        assert any(other in warning for warning in answer["warnings"]) is named


@pytest.fixture
def msm_config(tmp_path, monkeypatch):
    """msm's config.json (the working directory), in a folder of the test's own."""
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setattr(core, "get_msm_plugin_data_path", lambda: str(path))
    return path


def set_working_dir(path) -> None:
    config = core.ConfigFile()
    config.settings["workingDirectory"] = path
    config.store()


@needs_rename_lsp
@pytest.mark.parametrize("inside", ["", "development"])
def test_the_working_directory_follows_the_folder(project, msm_config, inside):
    set_working_dir(os.path.join(project, inside) if inside else project)
    answer = msm.rename_schema(
        "store", schema_project_path=project, lsp_path=LSP
    )
    expected = os.path.join(answer["project_path"], inside) if inside else answer["project_path"]
    assert core.get_working_dir() == expected


@needs_rename_lsp
def test_another_working_directory_and_a_dry_run_leave_it(project, msm_config, tmp_path):
    elsewhere = str(tmp_path)
    set_working_dir(elsewhere)
    msm.rename_schema("store", schema_project_path=project, lsp_path=LSP)
    assert core.get_working_dir() == elsewhere

    path = os.path.join(os.path.dirname(project), "store.msm.project")
    set_working_dir(path)
    msm.rename_schema("shop", schema_project_path=path, lsp_path=LSP, dry_run=True)
    assert core.get_working_dir() == path


def test_a_missing_releases_folder_is_no_release(project, monkeypatch):
    import shutil

    shutil.rmtree(os.path.join(project, "releases", "versions"))
    monkeypatch.setattr(lsp, "find_lsp", lambda lsp_path=None: None)
    # Past the release check: refused for the binary, not for a release
    assert rename(project, None, dry_run=True)["reason"] == renaming.REASON_LSP_MISSING


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_an_unreadable_releases_folder_is_not_taken_for_no_release(project):
    versions = os.path.join(project, "releases", "versions")
    os.chmod(versions, 0)
    try:
        with pytest.raises(ValueError, match="could not be read"):
            rename(project, "store", dry_run=True)
    finally:
        os.chmod(versions, 0o755)
