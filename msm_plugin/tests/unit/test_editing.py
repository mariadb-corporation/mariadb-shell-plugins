# Copyright (c) 2026, MariaDB plc.
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
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for schema edits: lib/editing.py.

Most tests stand a fake in for the binary's `edit` command, which renames a
backtick-quoted name wherever the script has it; the snapshot is still made
by the real binary's `outline`. The tests at the end run the real `edit`
where a binary has it (MSM_TEST_LSP names one, the binary or its folder, else
the binaries msm would find are tried): on a project of their own, released
and deployed on the sandbox, and on a copy of the MRS metadata project
(MSM_TEST_MRS_PROJECT, else the mariadb-shell checkout next to this one).
"""

import os
import re
import shutil

import pytest

from msm_plugin.lib import diagrams, editing, lsp, management, modeling
from msm_plugin.lib.management import create_schema_project_folder

LSP = lsp.find_lsp()
needs_lsp = pytest.mark.skipif(LSP is None, reason="no mariadb-lsp binary")


def _find_edit_lsp():
    """A binary that has the edit command, or None."""
    given = os.environ.get("MSM_TEST_LSP")
    candidates = [given] if given else lsp._candidates(None)
    for candidate in candidates:
        binary = lsp.find_lsp(candidate)
        if binary is not None and lsp.supports_edit(binary):
            return binary
    return None


EDIT_LSP = _find_edit_lsp()
needs_edit_lsp = pytest.mark.skipif(
    EDIT_LSP is None,
    reason="no mariadb-lsp binary with the edit command (MSM_TEST_LSP names one)",
)

MRS_PROJECT = os.environ.get("MSM_TEST_MRS_PROJECT") or os.path.join(
    # The runner links the plugin into its config home: the checkout is where the link points
    os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "..",
    "mariadb-shell", "mysqlshdk", "libs", "mrs", "db_schema", "mariadb_rest_service.msm.project",
)

TABLES_SQL = """CREATE TABLE IF NOT EXISTS `customer` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `orders` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `customer_id` INT UNSIGNED NOT NULL,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`)
        REFERENCES `customer` (`id`)
);"""

RENAME = {"op": "rename_table", "table": "customer", "new_name": "client"}


@pytest.fixture
def project(tmp_path):
    """A project with two tables, a diagram of both and a current snapshot."""
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.", license="MIT"
    )
    management.set_section_sql_content(
        "140", "\n" + TABLES_SQL + "\n", management.get_schema_development_file_path(path)
    )
    if LSP is not None:
        modeling.set_diagram(path, {
            "format": diagrams.DIAGRAM_FORMAT,
            "name": "Main",
            "layers": [],
            "figures": [
                {"id": "c", "type": "TableFigure", "name": "customer", "table": "customer",
                 "left": 20.0, "top": 20.0, "width": 160.0, "height": 80.0},
                {"id": "o", "type": "TableFigure", "name": "orders", "table": "orders",
                 "left": 220.0, "top": 20.0, "width": 160.0, "height": 80.0},
            ],
            "connections": [
                {"id": "k", "foreignKey": "fk_orders_customer", "table": "orders",
                 "startFigure": "o", "endFigure": "c", "visible": True},
            ],
        })
        modeling.update_schema_definition(path)
    return path


def base_hash(path) -> str:
    return modeling.read_diagram_rows(path)[0]["document"]["source_hash"]


def dev_script(path) -> str:
    with open(management.get_schema_development_file_path(path)) as f:
        return f.read()


def renaming_edit(calls=None):
    """A stand-in for lsp.edit: each rename_table renames `old` everywhere
    in the script, and migrates with a RENAME TABLE."""

    def edit(binary, sql, source_dir, request):
        if calls is not None:
            calls.append(request)
        lines = sql.split("\n")
        changes, migration = [], []
        for index, item in enumerate(request["edits"]):
            if item["op"] == "rename_table":
                old, new = f"`{item['table']}`", f"`{item['new_name']}`"
                for number, line in enumerate(lines):
                    for match in re.finditer(re.escape(old), line):
                        changes.append({
                            "range": {
                                "start": {"line": number, "character": match.start()},
                                "end": {"line": number, "character": match.end()},
                            },
                            "new_text": new,
                        })
                migration.append({"edit": index, "sql": f"RENAME TABLE {old} TO {new}"})
            elif item["op"] == "rename_routine":
                migration.append({"edit": index, "sql": f"DROP PROCEDURE IF EXISTS `{item['routine']}`"})
                migration.append({"edit": index, "sql": (
                    f"CREATE PROCEDURE `{item['new_name']}`()\nBEGIN\n    SELECT 1;\nEND")})
        # No outline: the snapshot is made by running the real binary.
        return {"format": 1, "files": [{"file": "-", "edits": changes}], "migration": migration,
                "notes": []}

    return edit


@pytest.fixture
def fake_edit(monkeypatch):
    calls = []
    monkeypatch.setattr(lsp, "supports_edit", lambda binary: True)
    # The ops the fake does, whatever binary the machine has
    monkeypatch.setattr(lsp, "edit_ops", lambda binary: lsp.RENAME_OPS)
    monkeypatch.setattr(lsp, "edit", renaming_edit(calls))
    return calls


# -----------------------------------------------------------------------------
# Text edits and diagrams


def test_text_edits_count_utf16_characters():
    text = "SELECT '😀' AS `a`;\nSELECT 2;\n"
    # 😀 is two UTF-16 units, so `a` starts at character 15 of the line.
    edits = [
        {"range": {"start": {"line": 0, "character": 15}, "end": {"line": 0, "character": 18}},
         "new_text": "`b`"},
        {"range": {"start": {"line": 1, "character": 7}, "end": {"line": 1, "character": 8}},
         "new_text": "3"},
    ]
    assert editing.apply_text_edits(text, edits) == "SELECT '😀' AS `b`;\nSELECT 3;\n"
    with pytest.raises(ValueError, match="overlap"):
        editing.apply_text_edits(text, edits + [edits[0]])
    with pytest.raises(ValueError, match="lacks"):
        editing.apply_text_edits(text, [{"range": {"start": {"line": 0, "character": 40},
                                                   "end": {"line": 0, "character": 40}},
                                         "new_text": ""}])


def test_inserts_at_one_position_keep_their_order():
    at = {"line": 0, "character": 3}
    edits = [{"range": {"start": at, "end": at}, "new_text": text} for text in ("z", "a")]
    assert editing.apply_text_edits("abcdef", edits) == "abczadef"


def test_diagrams_follow_renamed_views_and_routines():
    diagram = {"figures": [
        {"id": "v", "type": "ViewFigure", "name": "Summary", "view": "totals"},
        {"id": "r", "type": "RoutineGroupFigure", "name": "Routines", "routines": ["a", "B"]},
        {"id": "t", "type": "TableFigure", "name": "totals", "table": "totals"},
    ], "connections": []}
    assert editing.rename_in_diagram(diagram, {"op": "rename_view", "view": "TOTALS", "new_name": "sums"})
    assert diagram["figures"][0] == {"id": "v", "type": "ViewFigure", "name": "Summary", "view": "sums"}
    assert diagram["figures"][2]["table"] == "totals"
    assert editing.rename_in_diagram(diagram, {"op": "rename_routine", "routine": "b", "new_name": "c"})
    assert diagram["figures"][1]["routines"] == ["a", "c"]
    assert not editing.rename_in_diagram(diagram, {"op": "add_column", "table": "totals"})


def test_migrations_are_sorted_into_the_sections():
    edits = [RENAME, {"op": "rename_routine", "routine": "p", "new_name": "q"}]
    statements = editing.migration_statements(edits, [
        {"edit": 0, "sql": "RENAME TABLE `customer` TO `client`;"},
        # As the binary writes it; Section 150 may have dropped it already
        {"edit": 1, "sql": "DROP PROCEDURE `p`"},
        # Section 150 creates it again; it cannot run inside Section 240.
        {"edit": 1, "sql": "CREATE PROCEDURE `q`()\nBEGIN\n    SELECT 1;\nEND"},
        {"edit": 1, "sql": "drop function if exists `f`"},
    ])
    assert statements == {
        "240": "-- msm.editSchema: rename_table customer -> client\n"
               "RENAME TABLE `customer` TO `client`;",
        "290": "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;\n"
               "drop function if exists `f`;",
    }


# -----------------------------------------------------------------------------
# Editing a project


@needs_lsp
def test_a_rename_reaches_the_sql_the_diagrams_and_the_snapshot(project, fake_edit):
    result = editing.edit_schema(project, [RENAME], base_hash(project))

    assert "`customer`" not in dev_script(project)
    assert "REFERENCES `client` (`id`)" in dev_script(project)
    snapshot, main = result["rows"][0]["document"], result["rows"][1]["document"]
    assert [table["name"] for table in snapshot["tables"]] == ["client", "orders"]
    assert snapshot["source_hash"] == modeling.current_source_hash(project)
    assert main["figures"][0]["table"] == main["figures"][0]["name"] == "client"
    assert len(main["connections"]) == 1
    # No release yet: a deployment creates the schema from the script.
    assert result["migration_file"] is None
    assert result["files"] == [management.get_schema_development_file_path(project)]
    assert fake_edit[0]["schema"] == "shop"
    assert fake_edit[0]["format"] == lsp.EDIT_FORMAT


@needs_lsp
def test_edits_against_an_old_snapshot_are_refused(project, fake_edit):
    before = dev_script(project)
    with pytest.raises(editing.StaleSchemaError, match="changed since"):
        editing.edit_schema(project, [RENAME], "sha256:old")
    assert dev_script(project) == before
    assert fake_edit == []
    with pytest.raises(ValueError, match="No edits"):
        editing.edit_schema(project, [], base_hash(project))


@needs_lsp
def test_a_released_project_keeps_the_migration(project, fake_edit, monkeypatch):
    monkeypatch.setattr(management, "get_last_released_version", lambda path: [1, 0, 0])
    result = editing.edit_schema(project, [RENAME], base_hash(project))
    pending = editing.pending_update_path(project, management.get_project_settings(project))
    assert result["migration_file"] == pending
    assert pending in result["files"]

    result = editing.edit_schema(
        project, [{"op": "rename_table", "table": "client", "new_name": "buyer"},
                  {"op": "rename_routine", "routine": "p", "new_name": "q"}],
        base_hash(project))
    # The routine's CREATE is left to Section 150, and so not listed as kept
    assert result["migration"] == ["RENAME TABLE `client` TO `buyer`", "DROP PROCEDURE IF EXISTS `p`"]
    kept = editing.read_pending_update(pending)
    assert kept["240"] == (
        "-- msm.editSchema: rename_table customer -> client\n"
        "RENAME TABLE `customer` TO `client`;\n\n"
        "-- msm.editSchema: rename_table client -> buyer\n"
        "RENAME TABLE `client` TO `buyer`;"
    )
    assert kept["290"] == "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;"
    with open(pending) as f:
        assert f.read().startswith("-- Copyright (c)")


@needs_lsp
def test_a_failure_puts_every_file_back(project, fake_edit, monkeypatch):
    monkeypatch.setattr(management, "get_last_released_version", lambda path: [1, 0, 0])
    before = dev_script(project)

    def fail(*args, **kwargs):
        raise ValueError("snapshot failed")

    monkeypatch.setattr(modeling, "_replace_snapshot", fail)
    with pytest.raises(ValueError, match="snapshot failed"):
        editing.edit_schema(project, [RENAME], base_hash(project))
    assert dev_script(project) == before
    settings = management.get_project_settings(project)
    assert not os.path.exists(editing.pending_update_path(project, settings))


@needs_lsp
def test_edits_inside_the_diagrams_section_are_refused(project, monkeypatch):
    script = dev_script(project)
    line = script[: script.index("VIEW `msm_schema_diagrams`")].count("\n")
    monkeypatch.setattr(lsp, "supports_edit", lambda binary: True)
    monkeypatch.setattr(lsp, "edit", lambda *args: {"files": [{"file": "-", "edits": [
        {"range": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 6}},
         "new_text": "ALTER"}]}], "migration": [], "notes": []})
    with pytest.raises(ValueError, match="Section 160"):
        editing.edit_schema(project, [RENAME], base_hash(project))
    assert dev_script(project) == script


@needs_lsp
def test_a_binary_without_edit_is_named(project, monkeypatch):
    monkeypatch.setattr(lsp, "supports_edit", lambda binary: False)
    with pytest.raises(lsp.LspError, match="no `mariadb-lsp edit` command"):
        editing.edit_schema(project, [RENAME], base_hash(project))


@needs_lsp
def test_a_release_takes_the_pending_migration(project, fake_edit):
    from msm_plugin.management import prepare_release

    prepare_release(schema_project_path=project, version="1.0.0", next_version="1.0.1")
    editing.edit_schema(project, [RENAME], base_hash(project))
    settings = management.get_project_settings(project)
    pending = editing.pending_update_path(project, settings)
    assert editing.read_pending_update(pending)["240"]

    files = prepare_release(schema_project_path=project, version="1.0.1", next_version="1.0.2")
    update = os.path.join(project, "releases", "updates", "shop_1.0.0_to_1.0.1.sql")
    assert update in files and pending in files
    sections = management.get_file_sections(update)
    assert sections["240"]["sql_content"].strip("\n") == (
        "-- msm.editSchema: rename_table customer -> client\n"
        "RENAME TABLE `customer` TO `client`;"
    )
    # The next section's banner keeps its blank lines.
    assert "`client`;\n\n\n-- ####" in sections["240"]["full_content"] + "\n-- ####"
    assert editing.read_pending_update(pending) == {"240": "", "290": ""}


# -----------------------------------------------------------------------------
# Against the real binary

ROUTINES_SQL = """DELIMITER %%

DROP PROCEDURE IF EXISTS `count_customers`%%
CREATE PROCEDURE `count_customers`()
BEGIN
    SELECT COUNT(*) AS `customers` FROM `customer`;
END%%

CREATE OR REPLACE VIEW `customer_ids` AS
    SELECT `id` FROM `customer`%%

DELIMITER ;"""

RENAMES = [
    RENAME,
    {"op": "rename_column", "table": "client", "column": "id", "new_name": "client_id"},
    {"op": "rename_routine", "routine": "count_customers", "new_name": "count_clients",
     "kind": "procedure"},
]


def real_project(tmp_path, schema_name="shop") -> str:
    """A project with two tables, a procedure and a view on one of them, a
    diagram of both tables and a current snapshot, all by the real binary."""
    path = create_schema_project_folder(
        schema_name=schema_name, target_path=str(tmp_path), copyright_holder="Example Ltd.",
        license="MIT",
    )
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content("140", "\n" + TABLES_SQL + "\n", dev_file)
    management.set_section_sql_content("150", "\n" + ROUTINES_SQL + "\n", dev_file)
    modeling.create_diagram(path, "Main", lsp_path=EDIT_LSP)
    return path


def fresh_snapshot(path) -> dict:
    """The snapshot as the binary's outline of the files on disk gives it."""
    return modeling.build_schema_definition(path, lsp_path=EDIT_LSP)


@needs_edit_lsp
def test_the_real_binary_renames_and_its_outline_is_the_snapshot(tmp_path, monkeypatch):
    path = real_project(tmp_path)
    outlines = []
    real_outline = lsp.outline
    monkeypatch.setattr(lsp, "outline", lambda *args: outlines.append(args) or real_outline(*args))

    result = editing.edit_schema(path, RENAMES, base_hash(path), lsp_path=EDIT_LSP)

    # The snapshot is made from the outline edit returned, not by a second run
    assert outlines == []
    monkeypatch.undo()
    snapshot = result["rows"][0]["document"]
    assert snapshot == fresh_snapshot(path)
    assert snapshot["source_hash"] == modeling.current_source_hash(path)

    script = dev_script(path)
    assert "`customer`" not in script
    assert "CREATE PROCEDURE `count_clients`()" in script
    assert "REFERENCES `client` (`client_id`)" in script
    assert "SELECT COUNT(*) AS `customers` FROM `client`" in script
    assert "SELECT `client_id` FROM `client`" in script
    tables = {table["name"]: table for table in snapshot["tables"]}
    assert sorted(tables) == ["client", "orders"]
    assert [column["name"] for column in tables["client"]["columns"]] == ["client_id"]
    assert "count_clients" in [routine["name"] for routine in snapshot["routines"]]

    # The diagram follows: the figure and its connection are kept, renamed
    main = result["rows"][1]["document"]
    assert sorted(figure["table"] for figure in main["figures"]) == ["client", "orders"]
    assert [connection["table"] for connection in main["connections"]] == ["orders"]
    # No release yet, so nothing is kept for an update script
    assert result["migration_file"] is None and result["migration"] == []


@needs_edit_lsp
def test_a_renamed_routine_is_dropped_under_its_new_name_before_its_create(tmp_path):
    # Section 150 runs on every deployment: the DROP before the CREATE has
    # to name the routine the CREATE makes (lsp-server #26)
    path = real_project(tmp_path)
    editing.edit_schema(path, RENAMES[2:], base_hash(path), lsp_path=EDIT_LSP)
    script = dev_script(path)
    assert "DROP PROCEDURE IF EXISTS `count_clients`%%" in script
    assert "count_customers" not in script


# The edits beyond the renames (lsp-server #26), each of a kind the update
# script places differently.
STRUCTURAL_EDITS = [
    {"op": "add_table", "table": {
        "name": "note",
        "columns": [{"name": "id", "type": "INT UNSIGNED", "nullable": False},
                    {"name": "text", "type": "VARCHAR(200)"}],
        "indexes": [{"kind": "primary", "columns": ["id"]}],
    }},
    {"op": "add_column", "table": "customer",
     "column": {"name": "email", "type": "VARCHAR(255)"}},
    # A foreign key uses the column: it is dropped and added again (1833)
    {"op": "modify_column", "table": "orders", "column": "customer_id",
     "new_column": {"name": "customer_id", "type": "INT UNSIGNED", "nullable": False,
                    "comment": "The buyer"}},
    {"op": "set_table_options", "table": "orders", "options": {"comment": "Orders"}},
    {"op": "drop_view", "view": "customer_ids"},
    {"op": "drop_routine", "routine": "count_customers", "kind": "procedure"},
]

# Keys, views and routines (lsp-server #27), on the schema STRUCTURAL_EDITS left.
KEY_AND_OBJECT_EDITS = [
    {"op": "add_column", "table": "note",
     "column": {"name": "customer_id", "type": "INT UNSIGNED"}},
    {"op": "add_index", "table": "customer",
     "index": {"name": "customer_email", "kind": "unique", "columns": ["email"]}},
    {"op": "add_foreign_key", "table": "note", "foreign_key": {
        "name": "fk_note_customer", "columns": ["customer_id"],
        "references": {"table": "customer", "columns": ["id"]}}},
    {"op": "drop_foreign_key", "table": "orders", "name": "fk_orders_customer"},
    {"op": "add_view", "view": {"name": "note_texts", "definition": "SELECT `text` FROM `note`"}},
    {"op": "add_routine", "routine": {
        "name": "count_notes", "kind": "procedure", "parameters": [],
        "body": "BEGIN\n    SELECT COUNT(*) FROM `note`;\nEND"}},
]


def test_edit_ops_come_from_the_help(monkeypatch):
    help_texts = {
        "renames": "  edit     apply schema edits\n  format   print\n",
        "listed": "  edit     apply schema edits\n"
                  "           edit ops: rename_table add_table drop_view\n  format   print\n",
        "none": "  format   print\n",
    }
    monkeypatch.setattr(lsp, "_help", lambda binary: help_texts[binary])
    assert lsp.edit_ops("renames") == lsp.RENAME_OPS
    assert lsp.edit_ops("listed") == {"rename_table", "add_table", "drop_view"}
    assert lsp.edit_ops("none") == frozenset()


@needs_lsp
def test_ops_the_binary_lacks_are_refused_before_it_runs(project, fake_edit):
    with pytest.raises(lsp.LspError, match=r"cannot do `add_column`, `drop_routine`, `drop_view`, `modify_column`, `set_table_options` yet"):
        editing.edit_schema(project, STRUCTURAL_EDITS[1:] + [RENAME], base_hash(project))
    assert fake_edit == []


def test_the_update_script_gets_tables_and_drops_but_no_views_or_routines():
    statements = editing.migration_statements(STRUCTURAL_EDITS, [
        {"edit": 0, "sql": "CREATE TABLE `note` (\n  `id` INT UNSIGNED NOT NULL\n)"},
        {"edit": 4, "sql": "DROP VIEW `customer_ids`"},
        {"edit": 5, "sql": "DROP PROCEDURE `count_customers`"},
        # Section 150 creates views and routines on every deployment
        {"edit": 0, "sql": "CREATE VIEW `note_texts` AS SELECT `text` FROM `note`"},
        {"edit": 0, "sql": "CREATE OR REPLACE ALGORITHM=MERGE SQL SECURITY INVOKER VIEW `v` AS "
                           "SELECT 1"},
        {"edit": 0, "sql": "CREATE DEFINER=`root`@`%` FUNCTION `f`() RETURNS INT RETURN 1"},
    ])
    assert statements == {
        "240": "-- msm.editSchema: add_table note\n"
               "CREATE TABLE `note` (\n  `id` INT UNSIGNED NOT NULL\n);\n"
               "-- msm.editSchema: drop_view customer_ids\n"
               "DROP VIEW IF EXISTS `customer_ids`;",
        "290": "-- msm.editSchema: drop_routine count_customers\n"
               "DROP PROCEDURE IF EXISTS `count_customers`;",
    }
    assert editing._describe(STRUCTURAL_EDITS[2]) == "modify_column orders customer_id"


# The schema the deployment tests deploy on the sandbox.
DEPLOYED_SCHEMA = "msm_edit_test"


def release(path, version: str, next_version: str) -> list:
    """Releases the version, generates its deployment script and deploys it.

    Returns:
        The files the release wrote.
    """
    from msm_plugin.management import deploy_schema, generate_deployment_script, prepare_release

    files = prepare_release(schema_project_path=path, version=version,
                            next_version=next_version, lsp_path=EDIT_LSP)
    generate_deployment_script(schema_project_path=path, version=version)
    deploy_schema(schema_project_path=path, version=version)
    return files


def pending(path) -> dict:
    """The sections of the project's pending update script."""
    return editing.read_pending_update(
        editing.pending_update_path(path, management.get_project_settings(path)))


@pytest.fixture
def deployed(sandbox_session, tmp_path):
    """real_project, released as 1.0.0 and deployed with a customer and an order;
    yields the project and a function returning the first column of a query."""
    from msm_plugin.tests.unit import helpers

    def column(sql) -> list:
        return [next(iter(row.values())) for row in helpers.query(sandbox_session, sql)]

    path = real_project(tmp_path, DEPLOYED_SCHEMA)
    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{DEPLOYED_SCHEMA}`")
    try:
        release(path, "1.0.0", "1.0.1")
        sandbox_session.run_sql(f"INSERT INTO `{DEPLOYED_SCHEMA}`.`customer` VALUES (7)")
        sandbox_session.run_sql(f"INSERT INTO `{DEPLOYED_SCHEMA}`.`orders` VALUES (1, 7)")
        yield path, column
    finally:
        sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{DEPLOYED_SCHEMA}`")


def user_objects(column, kind: str) -> list:
    """The views (VIEWS) or routines (ROUTINES) of the deployed schema, msm's own left out."""
    name, schema = ("TABLE_NAME", "TABLE_SCHEMA") if kind == "VIEWS" else (
        "ROUTINE_NAME", "ROUTINE_SCHEMA")
    return sorted(column(
        f"SELECT {name} FROM information_schema.{kind} "
        f"WHERE {schema} = '{DEPLOYED_SCHEMA}' AND {name} NOT LIKE 'msm%'"))


def foreign_keys(column, what: str = "CONSTRAINT_NAME") -> list:
    return column(
        f"SELECT {what} FROM information_schema.REFERENTIAL_CONSTRAINTS "
        f"WHERE CONSTRAINT_SCHEMA = '{DEPLOYED_SCHEMA}'")


@needs_edit_lsp
def test_structural_edits_reach_a_deployed_schema_through_the_next_release(deployed):
    path, column = deployed
    result = editing.edit_schema(path, STRUCTURAL_EDITS, base_hash(path), lsp_path=EDIT_LSP)
    assert result["rows"][0]["document"] == fresh_snapshot(path)
    tables = {table["name"]: table for table in result["rows"][0]["document"]["tables"]}
    assert sorted(tables) == ["customer", "note", "orders"]
    assert [c["name"] for c in tables["customer"]["columns"]] == ["id", "email"]
    assert "customer_ids" not in dev_script(path)
    assert "count_customers" not in dev_script(path)
    kept = pending(path)
    assert "CREATE TABLE `note`" in kept["240"]
    assert "DROP FOREIGN KEY `fk_orders_customer`" in kept["240"]
    assert "DROP VIEW IF EXISTS `customer_ids`;" in kept["240"]
    assert kept["290"].endswith("DROP PROCEDURE IF EXISTS `count_customers`;")

    release(path, "1.0.1", "1.0.2")
    assert column(
        "SELECT CONCAT(TABLE_NAME, '.', COLUMN_NAME) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND TABLE_NAME NOT LIKE 'msm%' "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    ) == ["customer.id", "customer.email", "note.id", "note.text", "orders.id",
          "orders.customer_id"]
    assert column(
        "SELECT COLUMN_COMMENT FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND COLUMN_NAME = 'customer_id'"
    ) == ["The buyer"]
    assert column(
        "SELECT TABLE_COMMENT FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND TABLE_NAME = 'orders'") == ["Orders"]
    # The foreign key is back after the column change, and the row is kept
    assert foreign_keys(column, "REFERENCED_TABLE_NAME") == ["customer"]
    assert column(f"SELECT `customer_id` FROM `{DEPLOYED_SCHEMA}`.`orders`") == [7]
    assert user_objects(column, "ROUTINES") == user_objects(column, "VIEWS") == []

    # Keys, a view and a routine: the key changes go into 240, the view and
    # the routine reach the server through Section 150
    result = editing.edit_schema(path, KEY_AND_OBJECT_EDITS, base_hash(path), lsp_path=EDIT_LSP)
    assert result["rows"][0]["document"] == fresh_snapshot(path)
    kept = pending(path)
    assert "ADD CONSTRAINT `fk_note_customer` FOREIGN KEY" in kept["240"]
    assert "DROP FOREIGN KEY `fk_orders_customer`" in kept["240"]
    assert "CREATE" not in kept["240"].replace("CREATE TABLE", "") + kept["290"]
    release(path, "1.0.2", "1.0.3")
    assert foreign_keys(column) == ["fk_note_customer"]
    assert column(
        "SELECT NON_UNIQUE FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND INDEX_NAME = 'customer_email'") == [0]
    assert user_objects(column, "VIEWS") == ["note_texts"]
    assert user_objects(column, "ROUTINES") == ["count_notes"]

    # The next upgrade runs Section 150 again over what the edits wrote
    release(path, "1.0.3", "1.0.4")


@needs_edit_lsp
def test_added_views_and_routines_can_be_created_again(tmp_path):
    path = real_project(tmp_path)
    editing.edit_schema(path, [
        {"op": "add_view", "view": {"name": "order_ids", "definition": "SELECT `id` FROM `orders`"}},
        {"op": "add_routine", "routine": {
            "name": "count_orders", "kind": "procedure", "parameters": [],
            "body": "BEGIN\n    SELECT COUNT(*) FROM `orders`;\nEND"}},
    ], base_hash(path), lsp_path=EDIT_LSP)
    script = dev_script(path)
    assert "CREATE OR REPLACE VIEW `order_ids`" in script
    assert re.search(r"DROP PROCEDURE IF EXISTS `count_orders`%%\s*CREATE PROCEDURE "
                     r"`count_orders`|CREATE OR REPLACE PROCEDURE `count_orders`", script)


@needs_edit_lsp
def test_a_first_view_goes_into_section_150(tmp_path):
    path = real_project(tmp_path)
    editing.edit_schema(path, [
        {"op": "drop_view", "view": "customer_ids"},
        {"op": "add_view", "view": {"name": "order_ids", "definition": "SELECT `id` FROM `orders`"}},
    ], base_hash(path), lsp_path=EDIT_LSP)
    sections = management.get_file_sections(management.get_schema_development_file_path(path))
    assert "`order_ids`" in sections["150"]["sql_content"]
    assert "`order_ids`" not in sections["140"]["sql_content"]


@needs_edit_lsp
def test_renames_reach_a_deployed_schema_through_the_next_release(deployed):
    path, column = deployed
    result = editing.edit_schema(path, RENAMES, base_hash(path), lsp_path=EDIT_LSP)
    assert result["migration_file"] == editing.pending_update_path(
        path, management.get_project_settings(path))
    kept = pending(path)
    assert "RENAME TABLE `customer` TO `client`;" in kept["240"]
    assert "ALTER TABLE `client` RENAME COLUMN `id` TO `client_id`;" in kept["240"]
    # Section 150 creates the routine under its new name; the old one goes in 290
    assert "CREATE PROCEDURE" not in kept["240"] + kept["290"]
    assert "DROP PROCEDURE" in kept["290"] and "count_customers" in kept["290"]

    files = release(path, "1.0.1", "1.0.2")
    update = os.path.join(path, "releases", "updates", f"{DEPLOYED_SCHEMA}_1.0.0_to_1.0.1.sql")
    assert update in files
    assert pending(path) == {"240": "", "290": ""}

    assert sorted(column(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND TABLE_TYPE = 'BASE TABLE'"
    )) == ["client", "orders"]
    # The row came along, and the foreign key follows the renames
    assert column(f"SELECT `client_id` FROM `{DEPLOYED_SCHEMA}`.`client`") == [7]
    assert foreign_keys(column, "REFERENCED_TABLE_NAME") == ["client"]
    assert user_objects(column, "ROUTINES") == ["count_clients"]
    assert column(f"SELECT `client_id` FROM `{DEPLOYED_SCHEMA}`.`customer_ids`") == [7]


@needs_edit_lsp
@pytest.mark.skipif(not os.path.isdir(MRS_PROJECT), reason="no MRS metadata project")
def test_the_real_binary_renames_a_table_of_the_mrs_project(tmp_path):
    path = str(tmp_path / "mariadb_rest_service.msm.project")
    shutil.copytree(MRS_PROJECT, path)
    schema = next((row for row in modeling.read_diagram_rows(path)
                   if row["format"] == diagrams.SCHEMA_FORMAT), None)
    if not modeling._snapshot_is_current(modeling._Project.load(path), schema):
        modeling.update_schema_definition(path, lsp_path=EDIT_LSP)
    before = modeling.read_diagram_rows(path)

    # url_host is referenced by foreign keys, triggers, views, routines,
    # GRANTs and INSERTs, in several of the project's breakout files
    result = editing.edit_schema(
        path,
        [{"op": "rename_table", "table": "url_host", "new_name": "url_hosts"},
         {"op": "rename_column", "table": "url_hosts", "column": "name",
          "new_name": "host_name"}],
        base_hash(path),
        lsp_path=EDIT_LSP,
    )

    snapshot = result["rows"][0]["document"]
    assert snapshot == fresh_snapshot(path)
    tables = {table["name"]: table for table in snapshot["tables"]}
    assert "url_host" not in tables
    assert "host_name" in [column["name"] for column in tables["url_hosts"]["columns"]]
    referenced = {
        key["references"]["table"]
        for table in snapshot["tables"] for key in table.get("foreign_keys", [])
    }
    assert "url_hosts" in referenced and "url_host" not in referenced
    assert len([f for f in result["files"] if os.sep + "sections" + os.sep in f]) > 1
    # The release migration is kept: the project has releases
    assert result["migration"][0] == "RENAME TABLE `url_host` TO `url_hosts`"
    # Every diagram that showed the table shows it under its new name
    shown_before = {
        row["name"] for row in before if row["format"] == diagrams.DIAGRAM_FORMAT
        and "url_host" in {figure.get("table") for figure in row["document"]["figures"]}
    }
    assert shown_before
    for row in result["rows"][1:]:
        tables = {figure.get("table") for figure in row["document"]["figures"]}
        assert "url_host" not in tables
        assert ("url_hosts" in tables) == (row["name"] in shown_before)
