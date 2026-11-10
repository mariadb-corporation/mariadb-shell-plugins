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


def needs_edit_ops(*ops):
    """Skips a test where the binary lacks one of the ops."""
    return pytest.mark.skipif(
        EDIT_LSP is None or not set(ops) <= lsp.edit_ops(EDIT_LSP),
        reason="no mariadb-lsp binary with the ops " + ", ".join(ops),
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
    # The ops the fake does, whatever binary the machine has, and its
    # statements are untagged, as from a binary before `edit --ops`
    monkeypatch.setattr(lsp, "edit_ops", lambda binary: lsp.RENAME_OPS)
    monkeypatch.setattr(lsp, "tags_migration", lambda binary: False)
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


def test_untagged_migrations_are_sorted_into_the_sections():
    """A binary before `edit --ops`: msm reads what a statement does from its SQL."""
    edits = [RENAME, {"op": "rename_routine", "routine": "p", "new_name": "q"}]
    statements, _ = editing.migration_statements(edits, [
        {"edit": 0, "sql": "RENAME TABLE `customer` TO `client`;"},
        # As the binary writes it; Section 150 may have dropped it already
        {"edit": 1, "sql": "DROP PROCEDURE `p`"},
        # Section 150 creates it again; it cannot run inside Section 240.
        {"edit": 1, "sql": "CREATE PROCEDURE `q`()\nBEGIN\n    SELECT 1;\nEND"},
        {"edit": 1, "sql": "drop function if exists `f`"},
    ], tagged=False)
    assert statements == {
        "240": "-- msm.editSchema: rename_table customer -> client\n"
               "RENAME TABLE `customer` TO `client`;",
        "260": "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;\n"
               "drop function if exists `f`;",
    }


def test_tagged_migrations_are_placed_by_their_tags():
    """The tags decide, not the SQL: the SQL below would mislead the reading
    of an untagged statement."""
    edits = [{"op": "rename_routine", "routine": "p", "new_name": "q"},
             {"op": "drop_table", "table": "t"}, {"op": "drop_column", "table": "t", "column": "c"}]
    statements, _ = editing.migration_statements(edits, [
        {"edit": 0, "sql": "DROP PROCEDURE `p`", "action": "drop", "object": "procedure",
         "name": "p"},
        {"edit": 0, "sql": "/* q */ CREATE PROCEDURE `q`() SELECT 1", "action": "create",
         "object": "procedure", "name": "q"},
        {"edit": 1, "sql": "/* t */ DROP TABLE `t`", "action": "drop", "object": "table",
         "name": "t"},
        {"edit": 2, "sql": "ALTER TABLE `t` DROP COLUMN `c`", "action": "drop",
         "object": "column", "name": "c", "table": "t"},
        {"edit": 2, "sql": "CREATE VIEW `v` AS SELECT 1", "action": "create", "object": "table",
         "name": "v"},
    ])
    assert statements == {
        "240": "-- msm.editSchema: drop_table t\n/* t */ DROP TABLE IF EXISTS `t`;\n"
               "-- msm.editSchema: drop_column t c\nALTER TABLE `t` DROP COLUMN `c`;\n"
               "CREATE VIEW `v` AS SELECT 1;",
        "260": "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;",
    }
    with pytest.raises(ValueError, match="without its `action`"):
        editing.migration_statements(edits, [{"edit": 1, "sql": "DROP TABLE `t`"}])


def test_triggers_events_and_sequences_are_placed_by_their_tags():
    edits = [{"op": "rename_trigger", "trigger": "t", "new_name": "u"},
             {"op": "add_event", "event": {"name": "e"}}, {"op": "rename_event", "event": "e",
                                                         "new_name": "f"},
             {"op": "drop_event", "event": "f"}, {"op": "add_sequence", "sequence": {"name": "s"}},
             {"op": "drop_sequence", "sequence": "s"}]
    by_section, kept = editing.migration_statements(edits, [
        {"edit": 0, "sql": "DROP TRIGGER `t`", "action": "drop", "object": "trigger", "name": "t"},
        # MariaDB 1303 and 1576 inside the update procedure: Section 150 creates them
        {"edit": 0, "sql": "CREATE TRIGGER `u` BEFORE INSERT ON `x` FOR EACH ROW SET @a = 1",
         "action": "create", "object": "trigger", "name": "u"},
        {"edit": 1, "sql": "CREATE EVENT `e` ON SCHEDULE EVERY 1 DAY DO SELECT 1",
         "action": "create", "object": "event", "name": "e"},
        {"edit": 2, "sql": "ALTER EVENT `e` RENAME TO `f`", "action": "rename", "object": "event",
         "name": "e", "new_name": "f"},
        {"edit": 3, "sql": "DROP EVENT `f`", "action": "drop", "object": "event", "name": "f"},
        {"edit": 4, "sql": "CREATE SEQUENCE `s`", "action": "create", "object": "sequence",
         "name": "s"},
        {"edit": 5, "sql": "DROP SEQUENCE `s`", "action": "drop", "object": "sequence", "name": "s"},
    ])
    assert by_section == {
        "240": "-- msm.editSchema: rename_trigger t -> u\nDROP TRIGGER IF EXISTS `t`;\n"
               "-- msm.editSchema: rename_event e -> f\nALTER EVENT `e` RENAME TO `f`;\n"
               "-- msm.editSchema: drop_event f\nDROP EVENT IF EXISTS `f`;\n"
               "-- msm.editSchema: add_sequence s\nCREATE SEQUENCE `s`;\n"
               "-- msm.editSchema: drop_sequence s\nDROP SEQUENCE IF EXISTS `s`;",
        "260": "",
    }
    assert kept == ["DROP TRIGGER IF EXISTS `t`", "ALTER EVENT `e` RENAME TO `f`",
                    "DROP EVENT IF EXISTS `f`", "CREATE SEQUENCE `s`",
                    "DROP SEQUENCE IF EXISTS `s`"]


def test_a_pending_script_written_before_section_260_keeps_its_drops(project):
    """Routine DROPs went into the pending script's Section 290 before Section
    260 took them: they are read from there and moved when the next is added."""
    settings = management.get_project_settings(project)
    legacy = editing.pending_update_text(
        None, settings, {"240": "ALTER TABLE `t` ADD `c` INT;"}
    ).replace(
        "MSM Section 260: Removal of PROCEDUREs and FUNCTIONs",
        "MSM Section 290: Removal of Update Helpers",
    ) + "DROP PROCEDURE IF EXISTS `p`;\n"
    path = editing.pending_update_path(project, settings)
    with open(path, "w") as f:
        f.write(legacy)
    assert editing.read_pending_update(path) == {
        "240": "ALTER TABLE `t` ADD `c` INT;",
        "260": "DROP PROCEDURE IF EXISTS `p`;",
    }

    text = editing.pending_update_text(legacy, settings, {"260": "DROP FUNCTION IF EXISTS `f`;"})
    assert "MSM Section 290" not in text
    with open(path, "w") as f:
        f.write(text)
    assert editing.read_pending_update(path) == {
        "240": "ALTER TABLE `t` ADD `c` INT;",
        "260": "DROP PROCEDURE IF EXISTS `p`;\n\nDROP FUNCTION IF EXISTS `f`;",
    }


def test_diagrams_follow_renamed_events_and_sequences():
    diagram = {"figures": [
        {"id": "e", "type": "EventGroupFigure", "name": "Events", "events": ["a", "NIGHTLY"]},
        {"id": "s", "type": "SequenceFigure", "name": "numbers", "sequence": "numbers"},
    ], "connections": []}
    assert editing.rename_in_diagram(
        diagram, {"op": "rename_event", "event": "nightly", "new_name": "daily"})
    assert diagram["figures"][0]["events"] == ["a", "daily"]
    assert editing.rename_in_diagram(
        diagram, {"op": "rename_sequence", "sequence": "numbers", "new_name": "ids"})
    assert (diagram["figures"][1]["sequence"], diagram["figures"][1]["name"]) == ("ids", "ids")
    # The table card lists the snapshot's triggers: nothing to follow
    assert not editing.rename_in_diagram(
        diagram, {"op": "rename_trigger", "trigger": "t", "new_name": "u"})


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
def test_the_first_edit_of_a_year_moves_the_copyright_years_along(
    project, fake_edit, monkeypatch
):
    created = management.current_year()
    later = str(int(created) + 1)
    hash_before = base_hash(project)
    monkeypatch.setattr(management, "current_year", lambda: later)

    # The hash the client read before the notices changed is still accepted
    result = editing.edit_schema(project, [RENAME], hash_before)

    copyrights = management.get_project_settings(project)["copyrights"]
    assert copyrights[0]["yearOfLastUpdate"] == later
    assert f" * Copyright (c) {created}, {later}, Example Ltd.\n" in dev_script(project)
    assert result["rows"][0]["document"]["source_hash"] == modeling.current_source_hash(project)
    # The files are the edit's own, not the ones the years were moved in
    assert os.path.join(project, "README.md") not in result["files"]


@needs_lsp
def test_moving_the_copyright_years_keeps_a_current_snapshot_current(project, monkeypatch):
    assert base_hash(project) == modeling.current_source_hash(project)
    before = dev_script(project)

    later = str(int(management.current_year()) + 1)
    monkeypatch.setattr(management, "current_year", lambda: later)
    written = management.update_copyright_years(project)

    assert management.get_schema_development_file_path(project) in written
    # Only the notice and the snapshot's hash changed
    after = dev_script(project)
    assert after.count("\n") == before.count("\n")
    assert base_hash(project) == modeling.current_source_hash(project)
    assert [table["name"] for table in modeling.read_diagram_rows(project)[0]["document"]["tables"]] \
        == ["customer", "orders"]


@needs_lsp
def test_new_holders_keep_a_current_snapshot_current(project):
    assert base_hash(project) == modeling.current_source_hash(project)
    year = management.current_year()
    lines_before = dev_script(project).count("\n")

    management.set_project_settings(project, {"copyrights": [
        {"holder": "Example Ltd.", "yearOfCreation": year, "tracksUpdates": True},
        {"holder": "Second Holder", "yearOfCreation": year},
    ]}, lsp_path=EDIT_LSP)

    # The license block grew by a line; the snapshot was made again
    assert dev_script(project).count("\n") == lines_before + 1
    assert base_hash(project) == modeling.current_source_hash(project)
    assert [table["name"] for table in modeling.read_diagram_rows(project)[0]["document"]["tables"]] \
        == ["customer", "orders"]


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
    assert kept["260"] == "-- msm.editSchema: rename_routine p -> q\nDROP PROCEDURE IF EXISTS `p`;"
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
    monkeypatch.setattr(lsp, "edit_ops", lambda binary: frozenset())
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
    assert editing.read_pending_update(pending) == {"240": "", "260": ""}


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


def test_edit_ops_come_from_the_binary(monkeypatch):
    answers = {
        # lsp-server #29 and later: `edit --ops`, and tagged statements
        ("current", ("edit", "--ops")): (0, '{"format": 1, "ops": ["rename_table", "add_view"]}', ""),
        ("current", ("--help",)): (0, "  edit     apply schema edits\n"
                                      "           edit ops: rename_table add_view\n", ""),
        # lsp-server #33 and later: merged ALTER TABLE statements on request
        ("merging", ("edit", "--ops")): (
            0, '{"format": 1, "ops": ["rename_table"], "merge_alters": true}', ""),
        # #26 to #28: the `edit ops:` line of --help
        ("listed", ("edit", "--ops")): (2, "", "unknown option --ops"),
        ("listed", ("--help",)): (0, "  edit     apply schema edits\n"
                                     "           edit ops: rename_table add_table drop_view\n"
                                     "  format   print\n", ""),
        # #25: the renames
        ("renames", ("edit", "--ops")): (2, "", "unknown option --ops"),
        ("renames", ("--help",)): (0, "  edit     apply schema edits\n  format   print\n", ""),
        # Before the edit command
        ("none", ("edit", "--ops")): (1, "", "unknown command edit"),
        ("none", ("--help",)): (0, "  format   print\n", ""),
    }
    monkeypatch.setattr(lsp, "_answer", lambda binary, arguments: answers[binary, arguments])
    assert lsp.edit_ops("current") == {"rename_table", "add_view"}
    assert lsp.edit_ops("listed") == {"rename_table", "add_table", "drop_view"}
    assert lsp.edit_ops("renames") == lsp.RENAME_OPS
    assert lsp.edit_ops("none") == frozenset()
    assert [lsp.tags_migration(binary) for binary in ("current", "listed", "renames", "none")] == [
        True, False, False, False]
    assert [lsp.merges_alters(binary) for binary in ("merging", "current", "listed", "none")] == [
        True, False, False, False]


def test_a_merged_alter_table_is_kept_once_under_each_of_its_edits():
    edits = [
        {"op": "add_column", "table": "orders", "column": {"name": "qty", "type": "INT"}},
        {"op": "add_index", "table": "orders", "index": {"name": "ix_qty", "columns": ["qty"]}},
        {"op": "drop_column", "table": "orders", "column": "old"},
    ]
    statements, kept = editing.migration_statements(edits, [
        {"edit": 0, "edits": [0, 1], "sql": "ALTER TABLE `orders` ADD COLUMN `qty` INT, "
         "ADD INDEX `ix_qty` (`qty`)", "action": "alter", "object": "table", "name": "orders",
         "table": "orders", "spec": "ADD COLUMN `qty` INT, ADD INDEX `ix_qty` (`qty`)"},
        {"edit": 2, "edits": [2], "sql": "ALTER TABLE `orders` DROP COLUMN `old`",
         "action": "drop", "object": "column", "name": "old", "table": "orders"},
    ])
    assert statements["240"] == (
        "-- msm.editSchema: add_column orders qty\n"
        "-- msm.editSchema: add_index orders ix_qty\n"
        "ALTER TABLE `orders` ADD COLUMN `qty` INT, ADD INDEX `ix_qty` (`qty`);\n"
        "-- msm.editSchema: drop_column orders old\n"
        "ALTER TABLE `orders` DROP COLUMN `old`;")
    assert len(kept) == 2


def test_a_tables_online_alter_settings_follow_renames_and_drops():
    import json

    text = json.dumps({"schemaName": "shop", "onlineAlter": {"enabled": True, "tables": {
        "customer": {"maxRebuildBytes": 0}, "orders": {"lockWait": 2}, "audit": {"enabled": False}}}},
        indent=4)
    moved = editing.online_alter_settings_text(text, [
        {"op": "rename_table", "table": "customer", "new_name": "client"},
        {"op": "rename_table", "table": "client", "new_name": "buyer"},
        {"op": "drop_table", "table": "audit"},
        {"op": "add_column", "table": "orders", "column": {"name": "n", "type": "INT"}},
    ])
    assert json.loads(moved) == {"schemaName": "shop", "onlineAlter": {"enabled": True, "tables": {
        "orders": {"lockWait": 2}, "buyer": {"maxRebuildBytes": 0}}}}
    # The last entry dropped: no `tables` left
    assert json.loads(editing.online_alter_settings_text(moved, [
        {"op": "drop_table", "table": "orders"}, {"op": "drop_table", "table": "buyer"}]))[
        "onlineAlter"] == {"enabled": True}
    # Nothing to move
    for unchanged in (None, "{not json", json.dumps({"onlineAlter": True}),
                      json.dumps({"onlineAlter": {"tables": {"x": {}}}})):
        assert editing.online_alter_settings_text(unchanged, [
            {"op": "rename_table", "table": "customer", "new_name": "client"}]) is None


@needs_edit_ops("add_column", "add_index", "set_table_options")
def test_one_save_of_a_table_deploys_as_one_online_alter(deployed):
    import os
    from msm_plugin import lib

    path, column = deployed
    if not lsp.merges_alters(EDIT_LSP):
        pytest.skip("the binary does not merge ALTER TABLE statements (lsp-server #33)")
    management.set_project_settings(path, {"onlineAlter": {"tables": {"customer": {"lockWait": 3}}}})
    result = editing.edit_schema(path, [
        {"op": "add_column", "table": "customer",
         "column": {"name": "email", "type": "VARCHAR(255)"}},
        {"op": "add_index", "table": "customer",
         "index": {"name": "customer_email", "kind": "unique", "columns": ["email"]}},
        {"op": "set_table_options", "table": "customer", "options": {"comment": "Buyers"}},
        {"op": "rename_table", "table": "customer", "new_name": "client"},
    ], base_hash(path), lsp_path=EDIT_LSP)
    kept = pending(path)["240"]
    # One ALTER TABLE for the three edits of the table, under three comments
    assert kept.count("ALTER TABLE `customer`") == 1
    assert kept.count("-- msm.editSchema:") == 4
    # The table's settings follow the rename, in the same write
    settings_file = os.path.join(path, "msm.project.json")
    assert settings_file in result["files"]
    assert management.get_online_alter_settings(path, "client")["options"]["lockWait"] == 3
    assert "customer" not in management.get_online_alter_settings(path)["setting"]["tables"]

    log_path = lib.core.get_msm_schema_update_log_path()
    start = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    release(path, "1.0.1", "1.0.2")
    with open(log_path) as f:
        f.seek(start)
        log = f.read()
    assert log.count("ALTER TABLE `customer`:") == 1
    assert column("SELECT TABLE_COMMENT FROM information_schema.TABLES "
                  f"WHERE TABLE_SCHEMA = '{DEPLOYED_SCHEMA}' AND TABLE_NAME = 'client'") == ["Buyers"]


@needs_lsp
def test_ops_the_binary_lacks_are_refused_before_it_runs(project, fake_edit):
    with pytest.raises(lsp.LspError, match=r"cannot do `add_column`, `drop_routine`, `drop_view`, `modify_column`, `set_table_options` yet"):
        editing.edit_schema(project, STRUCTURAL_EDITS[1:] + [RENAME], base_hash(project))
    assert fake_edit == []


def test_untagged_update_script_gets_tables_and_drops_but_no_views_or_routines():
    statements, _ = editing.migration_statements(STRUCTURAL_EDITS, [
        {"edit": 0, "sql": "CREATE TABLE `note` (\n  `id` INT UNSIGNED NOT NULL\n)"},
        {"edit": 4, "sql": "DROP VIEW `customer_ids`"},
        {"edit": 5, "sql": "DROP PROCEDURE `count_customers`"},
        # Section 150 creates views and routines on every deployment
        {"edit": 0, "sql": "CREATE VIEW `note_texts` AS SELECT `text` FROM `note`"},
        {"edit": 0, "sql": "CREATE OR REPLACE ALGORITHM=MERGE SQL SECURITY INVOKER VIEW `v` AS "
                           "SELECT 1"},
        {"edit": 0, "sql": "CREATE DEFINER=`root`@`%` FUNCTION `f`() RETURNS INT RETURN 1"},
    ], tagged=False)
    assert statements == {
        "240": "-- msm.editSchema: add_table note\n"
               "CREATE TABLE `note` (\n  `id` INT UNSIGNED NOT NULL\n);\n"
               "-- msm.editSchema: drop_view customer_ids\n"
               "DROP VIEW IF EXISTS `customer_ids`;",
        "260": "-- msm.editSchema: drop_routine count_customers\n"
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


# The information_schema tables of user_objects: their name and schema
# columns, and a condition of their own.
_USER_OBJECTS = {
    "VIEWS": ("TABLE_NAME", "TABLE_SCHEMA", ""),
    "ROUTINES": ("ROUTINE_NAME", "ROUTINE_SCHEMA", ""),
    "SEQUENCES": ("TABLE_NAME", "TABLE_SCHEMA", " AND TABLE_TYPE = 'SEQUENCE'"),
    "TRIGGERS": ("TRIGGER_NAME", "TRIGGER_SCHEMA", ""),
    "EVENTS": ("EVENT_NAME", "EVENT_SCHEMA", ""),
}


def user_objects(column, kind: str) -> list:
    """The views, routines, sequences, triggers or events (VIEWS, ROUTINES,
    SEQUENCES, TRIGGERS, EVENTS) of the deployed schema, msm's own left out."""
    name, schema, condition = _USER_OBJECTS[kind]
    table = "TABLES" if kind == "SEQUENCES" else kind
    return sorted(column(
        f"SELECT {name} FROM information_schema.{table} "
        f"WHERE {schema} = '{DEPLOYED_SCHEMA}' AND {name} NOT LIKE 'msm%'{condition}"))


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
    assert kept["260"].endswith("DROP PROCEDURE IF EXISTS `count_customers`;")

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
    assert "CREATE" not in kept["240"].replace("CREATE TABLE", "") + kept["260"]
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


@needs_edit_ops("add_sequence", "rename_sequence", "drop_sequence", "add_trigger",
                "rename_trigger", "drop_trigger", "add_event", "rename_event", "drop_event")
def test_sequences_triggers_and_events_reach_a_deployed_schema(deployed):
    path, column = deployed

    def deployed_objects():
        return tuple(user_objects(column, kind) for kind in ("SEQUENCES", "TRIGGERS", "EVENTS"))

    def numbers():
        return column(f"SELECT `number` FROM `{DEPLOYED_SCHEMA}`.`orders` ORDER BY `id`")

    result = editing.edit_schema(path, [
        {"op": "add_sequence", "sequence": {"name": "order_numbers", "type": "INT UNSIGNED",
                                            "start": "1000"}},
        {"op": "add_column", "table": "orders", "column": {
            "name": "number", "type": "INT UNSIGNED",
            "default": "(NEXT VALUE FOR `order_numbers`)"}},
        {"op": "add_trigger", "trigger": {
            "name": "orders_check", "timing": "BEFORE", "events": "INSERT", "table": "orders",
            "body": "SET NEW.`customer_id` = NEW.`customer_id`"}},
        {"op": "add_event", "event": {
            "name": "nightly", "schedule": "EVERY 1 DAY",
            "body": "DELETE FROM `orders` WHERE 0"}},
    ], base_hash(path), lsp_path=EDIT_LSP)
    assert result["rows"][0]["document"] == fresh_snapshot(path)
    kept = pending(path)
    # Section 150 creates the trigger and the event; the sequence goes with the tables
    assert "CREATE SEQUENCE `order_numbers`" in kept["240"]
    assert "CREATE TRIGGER" not in kept["240"] + kept["260"]
    assert "CREATE EVENT" not in kept["240"] + kept["260"]
    modeling.create_diagram(path, "Objects", tables=[], lsp_path=EDIT_LSP,
                            events=["nightly"], sequences=["order_numbers"])

    release(path, "1.0.1", "1.0.2")
    assert deployed_objects() == (["order_numbers"], ["orders_check"], ["nightly"])
    sandbox_insert = f"INSERT INTO `{DEPLOYED_SCHEMA}`.`orders` (`id`, `customer_id`) VALUES "
    column(sandbox_insert + "(2, 7)")
    # The order deployed before took the first number when the column came
    assert numbers() == [1000, 1001]

    result = editing.edit_schema(path, [
        {"op": "rename_sequence", "sequence": "order_numbers", "new_name": "order_ids"},
        {"op": "rename_trigger", "trigger": "orders_check", "new_name": "orders_guard"},
        {"op": "rename_event", "event": "nightly", "new_name": "daily"},
    ], base_hash(path), lsp_path=EDIT_LSP)
    assert result["rows"][0]["document"] == fresh_snapshot(path)
    (diagram,) = [row["document"] for row in result["rows"] if row["name"] == "Objects"]
    by_type = {figure["type"]: figure for figure in diagram["figures"]}
    assert by_type["SequenceFigure"]["sequence"] == "order_ids"
    assert by_type["EventGroupFigure"]["events"] == ["daily"]
    kept = pending(path)
    assert "RENAME TABLE `order_numbers` TO `order_ids`;" in kept["240"]
    assert "SET DEFAULT" in kept["240"] and "DROP TRIGGER IF EXISTS `orders_check`;" in kept["240"]
    assert "ALTER EVENT `nightly` RENAME TO `daily`;" in kept["240"]

    release(path, "1.0.2", "1.0.3")
    assert deployed_objects() == (["order_ids"], ["orders_guard"], ["daily"])
    # The deployed default takes its values from the renamed sequence
    column(sandbox_insert + "(3, 7)")
    *kept_numbers, number = numbers()
    # Past the cached values (CACHE 1000 by default), which the rename drops
    assert kept_numbers == [1000, 1001] and number > 1001
    assert column(f"SELECT NEXT VALUE FOR `{DEPLOYED_SCHEMA}`.`order_ids`") == [number + 1]

    editing.edit_schema(path, [
        {"op": "drop_column", "table": "orders", "column": "number"},
        {"op": "drop_sequence", "sequence": "order_ids"},
        {"op": "drop_trigger", "trigger": "orders_guard"},
        {"op": "drop_event", "event": "daily"},
    ], base_hash(path), lsp_path=EDIT_LSP)
    kept = pending(path)
    for statement in ("DROP SEQUENCE IF EXISTS `order_ids`;",
                      "DROP TRIGGER IF EXISTS `orders_guard`;", "DROP EVENT IF EXISTS `daily`;"):
        assert statement in kept["240"]
    # The diagram lost what the schema lost
    (diagram,) = [row["document"] for row in modeling.read_diagram_rows(path)
                  if row["name"] == "Objects"]
    assert diagram["figures"] == []

    release(path, "1.0.3", "1.0.4")
    assert deployed_objects() == ([], [], [])


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
def test_the_template_examples_go_with_a_sections_first_statement(tmp_path):
    from msm_plugin.lib.management import create_schema_project_folder

    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="X", license="MIT")
    modeling.update_schema_definition(path, lsp_path=EDIT_LSP)

    def bodies():
        found = management.get_file_sections(management.get_schema_development_file_path(path))
        return found["140"]["sql_content"], found["150"]["sql_content"]

    result = editing.edit_schema(path, [{"op": "add_table", "table": {
        "name": "t", "columns": [{"name": "id", "type": "INT"}]}}],
        base_hash(path), lsp_path=EDIT_LSP)
    tables, objects = bodies()
    assert "CREATE TABLE `t`" in tables and "ToDo" not in tables and "my_table" not in tables
    assert "-- ToDo:" in objects  # no statement there yet
    # The snapshot is that of the script as written, without the example
    assert result["rows"][0]["document"] == fresh_snapshot(path)

    result = editing.edit_schema(path, [{"op": "add_view", "view": {
        "name": "v", "definition": "SELECT `id` FROM `t`"}}], base_hash(path), lsp_path=EDIT_LSP)
    tables, objects = bodies()
    assert "CREATE OR REPLACE VIEW `v`" in objects and "ToDo" not in objects
    assert "DELIMITER" not in objects
    assert result["rows"][0]["document"] == fresh_snapshot(path)


@needs_edit_lsp
def test_a_first_procedure_keeps_its_delimiters_and_deploys(tmp_path, sandbox_session):
    """Section 150's example goes with its DELIMITER pair; the procedure the
    edit adds brings its own, so the script still runs."""
    from msm_plugin.lib.management import create_schema_project_folder
    from msm_plugin.tests.unit import helpers

    path = create_schema_project_folder(
        schema_name=DEPLOYED_SCHEMA, target_path=str(tmp_path), copyright_holder="X",
        license="MIT")
    modeling.update_schema_definition(path, lsp_path=EDIT_LSP)
    editing.edit_schema(path, [
        {"op": "add_table", "table": {"name": "t", "columns": [{"name": "id", "type": "INT"}]}},
        {"op": "add_routine", "routine": {
            "name": "count_t", "kind": "procedure", "parameters": [],
            "body": "BEGIN\n    SELECT COUNT(*) FROM `t`;\nEND"}},
    ], base_hash(path), lsp_path=EDIT_LSP)
    objects = management.get_file_sections(
        management.get_schema_development_file_path(path))["150"]["sql_content"]
    assert "ToDo" not in objects and objects.count("DELIMITER %%") == 1
    assert objects.index("DELIMITER %%") < objects.index("CREATE PROCEDURE `count_t`") < (
        objects.index("DELIMITER ;"))

    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{DEPLOYED_SCHEMA}`")
    try:
        release(path, "1.0.0", "1.0.1")
        assert [row["ROUTINE_NAME"] for row in helpers.query(sandbox_session, (
            "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
            f"WHERE ROUTINE_SCHEMA = '{DEPLOYED_SCHEMA}' AND ROUTINE_NAME NOT LIKE 'msm%'"
        ))] == ["count_t"]
    finally:
        sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{DEPLOYED_SCHEMA}`")


@needs_edit_lsp
def test_renames_reach_a_deployed_schema_through_the_next_release(deployed):
    path, column = deployed
    result = editing.edit_schema(path, RENAMES, base_hash(path), lsp_path=EDIT_LSP)
    assert result["migration_file"] == editing.pending_update_path(
        path, management.get_project_settings(path))
    kept = pending(path)
    assert "RENAME TABLE `customer` TO `client`;" in kept["240"]
    assert "ALTER TABLE `client` RENAME COLUMN `id` TO `client_id`;" in kept["240"]
    # Section 150 creates the routine under its new name; the old one goes in 260
    assert "CREATE PROCEDURE" not in kept["240"] + kept["260"]
    assert "DROP PROCEDURE" in kept["260"] and "count_customers" in kept["260"]

    files = release(path, "1.0.1", "1.0.2")
    update = os.path.join(path, "releases", "updates", f"{DEPLOYED_SCHEMA}_1.0.0_to_1.0.1.sql")
    assert update in files
    assert pending(path) == {"240": "", "260": ""}

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


# -----------------------------------------------------------------------------
# Adding an object from a template


def test_the_object_templates_are_plain_create_statements():
    result = editing.get_object_templates()
    kinds = ["view", "procedure", "function", "trigger", "event", "sequence"]
    assert list(result["templates"]) == kinds and list(result["placeholders"]) == kinds
    assert result["templates"]["view"] == "CREATE VIEW `${name}` AS\nSELECT 1"
    assert result["placeholders"]["trigger"] == ["name", "table", "schema"]
    assert result["placeholders"]["view"] == ["name", "schema"]
    # The caller's copy is its own
    result["templates"]["view"] = "x"
    assert editing.get_object_templates()["templates"]["view"] != "x"


@needs_edit_ops("add_view", "add_routine", "add_trigger", "add_event", "add_sequence")
@pytest.mark.parametrize("kind, name, table, created", [
    ("view", "v_new", None, "VIEW `v_new`"),
    ("procedure", "p_new", None, "PROCEDURE `p_new`"),
    ("function", "f_new", None, "FUNCTION `f_new`"),
    ("trigger", "tr_new", "orders", "TRIGGER `tr_new`"),
    ("event", "e_new", None, "EVENT `e_new`"),
    ("sequence", "s_new", None, "SEQUENCE `s_new`"),
])
def test_an_object_is_added_from_msms_template(project, kind, name, table, created):
    result = editing.add_schema_object(
        project, kind, name, base_hash(project), table=table, lsp_path=EDIT_LSP
    )
    # Where the statement starts, for an editor to open
    with open(result["file"]) as f:
        lines = f.read().split("\n")
    assert created in lines[result["line"] - 1] or (
        kind != "sequence" and created in " ".join(lines[result["line"] - 1:result["line"] + 1])
    )
    snapshot = result["rows"][0]["document"]
    member = {"procedure": "routines", "function": "routines"}.get(kind, kind + "s")
    assert name in [item["name"] for item in snapshot[member]]
    assert snapshot["source_hash"] == modeling.current_source_hash(project)
    script = dev_script(project)
    if kind == "sequence":
        # Section 140, before the first table
        assert script.index(created) < script.index("CREATE TABLE IF NOT EXISTS `customer`")
    else:
        assert script.index(created) > script.index("MSM Section 150")


@needs_edit_ops("add_view")
def test_a_users_template_is_filled(project):
    template = "CREATE VIEW `${name}` AS SELECT `id` FROM `customer` -- in ${schema}"
    result = editing.add_schema_object(
        project, "view", "customer_ids", base_hash(project), template=template, lsp_path=EDIT_LSP
    )
    view = next(v for v in result["rows"][0]["document"]["views"] if v["name"] == "customer_ids")
    assert "`customer`" in view["definition"]


@needs_lsp
@pytest.mark.parametrize("kind, name, template, table, message", [
    ("index", "x", None, None, "Unknown kind `index`"),
    ("view", "", None, None, "A name is needed"),
    ("view", "a`b", None, None, "cannot be a name"),
    ("trigger", "tr", None, None, "A trigger needs its table"),
    ("view", "v", "CREATE VIEW `${name}` AS SELECT * FROM `${table}`", None, "placeholder \\$\\{table\\}"),
    ("view", "v", "CREATE TABLE `${name}` (id INT)", None, "one CREATE VIEW statement"),
    ("function", "f", "CREATE PROCEDURE `${name}`() SELECT 1", None, "one CREATE FUNCTION statement"),
    ("view", "v", "CREATE VIEW `other` AS SELECT 1", None, "creates `other`, not `v`"),
    ("view", "v", "CREATE VIEW `${name}` AS SELEC 1", None, "one CREATE VIEW statement"),
    ("view", "v", "CREATE VIEW `other`.`${name}` AS SELECT 1", None, "names the schema `other`"),
    ("trigger", "tr", "CREATE TRIGGER `${name}` BEFORE INSERT ON `customer` FOR EACH ROW SET @x = 1",
     "orders", "on `customer`, not on `orders`"),
    ("trigger", "tr", None, "a`b", "cannot be a name"),
])
def test_a_bad_object_is_refused_before_anything_is_written(project, kind, name, template, table, message):
    before = dev_script(project)
    with pytest.raises(ValueError, match=message):
        editing.add_schema_object(
            project, kind, name, base_hash(project), template=template, table=table, lsp_path=EDIT_LSP
        )
    assert dev_script(project) == before


@needs_edit_ops("add_view", "add_trigger")
def test_what_the_edit_refuses_is_refused(project):
    before = dev_script(project)
    # Tables, views and sequences share one namespace
    with pytest.raises(ValueError, match="customer"):
        editing.add_schema_object(project, "view", "customer", base_hash(project), lsp_path=EDIT_LSP)
    with pytest.raises(ValueError, match="nowhere"):
        editing.add_schema_object(
            project, "trigger", "tr", base_hash(project), table="nowhere", lsp_path=EDIT_LSP
        )
    with pytest.raises(editing.StaleSchemaError):
        editing.add_schema_object(project, "view", "v", "sha256:old", lsp_path=EDIT_LSP)
    assert dev_script(project) == before
