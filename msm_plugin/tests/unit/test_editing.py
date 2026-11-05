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

The binary's `edit` command is stood in for by a fake that renames a
backtick-quoted name wherever the script has it; the snapshot is still made
by the real binary's `outline`.
"""

import os
import re

import pytest

from msm_plugin.lib import diagrams, editing, lsp, management, modeling
from msm_plugin.lib.management import create_schema_project_folder

LSP = lsp.find_lsp()
needs_lsp = pytest.mark.skipif(LSP is None, reason="no mariadb-lsp binary")

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
        return {"format": 1, "files": [{"file": "-", "edits": changes}], "migration": migration,
                "notes": [], "outline": {}}

    return edit


@pytest.fixture
def fake_edit(monkeypatch):
    calls = []
    monkeypatch.setattr(lsp, "supports_edit", lambda binary: True)
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
        {"edit": 1, "sql": "DROP PROCEDURE IF EXISTS `p`"},
    ])
    assert statements == {
        "240": "-- msm.editSchema: rename_table customer -> client\n"
               "RENAME TABLE `customer` TO `client`;",
        "290": "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;",
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

    editing.edit_schema(project, [{"op": "rename_table", "table": "client", "new_name": "buyer"},
                                  {"op": "rename_routine", "routine": "p", "new_name": "q"}],
                        base_hash(project))
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
