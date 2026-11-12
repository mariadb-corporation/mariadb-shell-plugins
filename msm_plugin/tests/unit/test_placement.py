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

"""Where msm puts the statements of new schema objects (lib/placement.py).

The tests run the real binary (MSM_TEST_LSP names one, else the binaries
msm would find are tried), one whose add ops take `at` and `form`
(lsp-server #49). Each looks at what msm asked for and at what the files
hold afterwards.
"""

import os

import pytest

from msm_plugin.lib import editing, lsp, management, modeling, sections
from msm_plugin.tests.unit import helpers

LSP = helpers.find_test_lsp(lsp.places_statements)
needs_placement = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary that places statements (lsp-server #49)"
)

TABLES = """CREATE TABLE `customer` (
    `id` INT NOT NULL,
    PRIMARY KEY (`id`)
);

CREATE TABLE `orders` (
    `id` INT NOT NULL,
    `customer_id` INT NOT NULL,
    PRIMARY KEY (`id`)
);"""

ROUTINE = """DELIMITER %%

DROP PROCEDURE IF EXISTS `p_count`%%
CREATE PROCEDURE `p_count`()
BEGIN
    SELECT COUNT(*) FROM `orders`;
END%%

DELIMITER ;"""

VIEW = """-- -----------------------------------------------------------------------------
-- VIEW `shop`.`v_ids`
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW `v_ids` AS
    SELECT `id` FROM `orders`;"""

DROPPED_VIEW = """DROP VIEW IF EXISTS `v_ids`;
CREATE VIEW `v_ids` AS
    SELECT `id` FROM `orders`;"""

EVENT = """CREATE OR REPLACE EVENT `e_daily`
    ON SCHEDULE EVERY 1 DAY
    DO DELETE FROM `orders` WHERE `id` < 0;"""

NEW_VIEW = {"op": "add_view", "view": {"name": "v_new", "definition": "SELECT `id` FROM `customer`"}}
NEW_PROCEDURE = {"op": "add_routine", "routine": {
    "name": "p_new", "kind": "procedure", "parameters": [], "body": "SELECT 1"}}
NEW_TRIGGER = {"op": "add_trigger", "trigger": {
    "name": "tr_new", "timing": "BEFORE", "events": "INSERT", "table": "orders",
    "body": "SET NEW.`id` = NEW.`id`"}}
NEW_EVENT = {"op": "add_event", "event": {
    "name": "e_new", "schedule": "EVERY 1 HOUR", "body": "SELECT 1"}}
NEW_SEQUENCE = {"op": "add_sequence", "sequence": {"name": "s_new"}}
NEW_TABLE = {"op": "add_table", "table": {"name": "t_new", "columns": [{"name": "id", "type": "INT"}]}}


def make_project(tmp_path, section_150: str = "", section_140: str = TABLES) -> str:
    path = management.create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="X", license="MIT")
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content("140", "\n" + section_140 + "\n", dev_file)
    if section_150:
        management.set_section_sql_content("150", "\n" + section_150 + "\n", dev_file)
    modeling.update_schema_definition(path, lsp_path=LSP)
    return path


def base_hash(path) -> str:
    return modeling.read_diagram_rows(path)[0]["document"]["source_hash"]


def script(path) -> str:
    with open(management.get_schema_development_file_path(path)) as f:
        return f.read()


def section(path, section_id: str) -> str:
    return management.get_file_sections(
        management.get_schema_development_file_path(path))[section_id]["sql_content"]


def breakout(path, file_name: str) -> str:
    with open(os.path.join(path, "development", "sections", file_name)) as f:
        return f.read()


@pytest.fixture
def requests(monkeypatch):
    """The edits msm sends the binary, each call's."""
    sent = []
    real = lsp.edit

    def recording(binary, sql, source_dir, request):
        sent.append(request["edits"])
        return real(binary, sql, source_dir, request)

    monkeypatch.setattr(lsp, "edit", recording)
    return sent


def edit(path, *edits):
    return editing.edit_schema(path, list(edits), base_hash(path), lsp_path=LSP)


def placements(sent) -> list:
    return [(e.get("at"), e.get("form")) for e in sent[-1]]


# -----------------------------------------------------------------------------
# Inline sections


@needs_placement
def test_a_new_object_goes_after_the_last_of_its_kind(tmp_path, requests):
    path = make_project(tmp_path, ROUTINE + "\n\n" + VIEW)
    edit(path, NEW_VIEW, NEW_TABLE)
    (view_at, view_form), (table_at, table_form) = placements(requests)
    assert view_at["file"] == "" and "after" in view_at and view_form == "or_replace"
    assert table_at["file"] == "" and "after" in table_at and table_form == "create"
    text = script(path)
    assert text.index("VIEW `v_ids`") < text.index("CREATE OR REPLACE VIEW `v_new`")
    assert text.index("CREATE TABLE `orders`") < text.index("CREATE TABLE `t_new`") < (
        text.index("-- MSM Section 150:"))


@needs_placement
def test_a_neighbour_that_drops_first_is_followed(tmp_path, requests):
    path = make_project(tmp_path, DROPPED_VIEW)
    edit(path, NEW_VIEW, NEW_PROCEDURE)
    (_, view_form), (_, procedure_form) = placements(requests)
    assert view_form == "drop_first"
    # No routine to follow: a plain neighbour does not count
    assert procedure_form == "or_replace"
    objects = section(path, "150")
    assert "DROP VIEW IF EXISTS `v_new`" in objects
    assert "CREATE OR REPLACE PROCEDURE `p_new`" in objects


@needs_placement
def test_a_first_view_follows_the_routines(tmp_path, requests):
    path = make_project(tmp_path, ROUTINE + "\n\n" + EVENT)
    edit(path, NEW_VIEW, NEW_TRIGGER)
    objects = section(path, "150")
    assert objects.index("END%%") < objects.index("VIEW `v_new`") < objects.index("EVENT `e_daily`")
    # A trigger after the view the same request added, both after the routine
    assert objects.index("VIEW `v_new`") < objects.index("TRIGGER `tr_new`") < (
        objects.index("EVENT `e_daily`"))


@needs_placement
def test_a_first_routine_goes_above_the_first_view_and_its_banner(tmp_path, requests):
    path = make_project(tmp_path, VIEW)
    edit(path, NEW_PROCEDURE)
    ((at, form),) = placements(requests)
    assert "line" in at and form == "or_replace"
    objects = section(path, "150")
    assert objects.index("PROCEDURE `p_new`") < objects.index("-- VIEW `shop`.`v_ids`")


@needs_placement
def test_a_first_sequence_goes_above_the_first_table(tmp_path, requests):
    path = make_project(tmp_path)
    edit(path, NEW_SEQUENCE)
    ((at, form),) = placements(requests)
    assert "line" in at and form == "create"
    tables = section(path, "140")
    assert tables.index("CREATE SEQUENCE `s_new`") < tables.index("CREATE TABLE `customer`")


@needs_placement
def test_an_empty_section_takes_the_object_at_its_end(tmp_path, requests):
    path = make_project(tmp_path)
    edit(path, NEW_EVENT)
    ((at, form),) = placements(requests)
    assert at["file"] == "" and "line" in at and form == "or_replace"
    objects = section(path, "150")
    assert "CREATE OR REPLACE EVENT `e_new`" in objects and "ToDo" not in objects


@needs_placement
def test_an_object_an_earlier_edit_drops_is_no_place(tmp_path, requests):
    path = make_project(tmp_path, ROUTINE + "\n\n" + VIEW)
    edit(path, {"op": "rename_view", "view": "v_ids", "new_name": "v_old"},
         {"op": "drop_view", "view": "v_old"}, NEW_VIEW)
    objects = section(path, "150")
    assert "VIEW `v_ids` AS" not in objects and "v_old" not in objects
    # No view left: after the routine
    assert objects.index("END%%") < objects.index("VIEW `v_new`")


@needs_placement
def test_statements_go_in_the_order_their_kinds_run(tmp_path, requests):
    """A view given before the function it calls is still written after
    it, and the statements' entries keep the order they were given in."""
    path = make_project(tmp_path)
    result = editing.set_schema_statements(path, [
        "CREATE VIEW `v_f` AS SELECT `f_one`() AS `one`",
        "CREATE FUNCTION `f_one`() RETURNS INT DETERMINISTIC RETURN 1",
        "CREATE SEQUENCE `s_f`",
    ], base_hash(path), lsp_path=LSP)
    assert [entry["name"] for entry in result["statements"]] == ["v_f", "f_one", "s_f"]
    assert [edit["op"] for edit in requests[-1]] == ["add_sequence", "add_routine", "add_view"]
    objects = section(path, "150")
    assert objects.index("FUNCTION `f_one`") < objects.index("VIEW `v_f`")


@needs_placement
def test_adds_at_one_place_keep_their_order(tmp_path, requests):
    path = make_project(tmp_path, VIEW)
    second = {**NEW_VIEW, "view": {**NEW_VIEW["view"], "name": "v_second"}}
    edit(path, NEW_VIEW, second)
    first_at, second_at = (at for at, _ in placements(requests))
    assert first_at == second_at
    objects = section(path, "150")
    assert objects.index("`v_ids`") < objects.index("`v_new`") < objects.index("`v_second`")


# -----------------------------------------------------------------------------
# Breakout files


def split(path, section_id: str = "150"):
    result = sections.split_sections(path, [section_id], lsp_path=LSP)
    assert section_id in result.split, result.skipped
    modeling.update_schema_definition(path, lsp_path=LSP)


@needs_placement
def test_a_new_object_goes_into_its_kinds_breakout_file(tmp_path, requests):
    path = make_project(tmp_path, ROUTINE + "\n\n" + VIEW)
    split(path)
    edit(path, NEW_VIEW, NEW_PROCEDURE)
    (view_at, _), (procedure_at, _) = placements(requests)
    assert view_at["file"].endswith("/150-20_views.sql")
    assert procedure_at["file"].endswith("/150-10_procedures_functions.sql")
    assert "`v_new`" in breakout(path, "150-20_views.sql")
    assert "`p_new`" in breakout(path, "150-10_procedures_functions.sql")
    assert "`v_new`" not in section(path, "150")


@needs_placement
def test_a_first_sequence_goes_above_the_tables_of_their_breakout_file(tmp_path, requests):
    path = make_project(tmp_path)
    split(path, "140")
    edit(path, NEW_SEQUENCE, NEW_TABLE)
    (sequence_at, _), (table_at, _) = placements(requests)
    assert sequence_at["file"].endswith("/140-10_tables.sql") and "line" in sequence_at
    assert table_at["file"].endswith("/140-10_tables.sql") and "after" in table_at
    tables = breakout(path, "140-10_tables.sql")
    assert tables.index("SEQUENCE `s_new`") < tables.index("TABLE `customer`")
    assert tables.index("TABLE `orders`") < tables.index("TABLE `t_new`")


@needs_placement
def test_a_kind_without_its_breakout_file_goes_between_the_source_lines(tmp_path, requests):
    path = make_project(tmp_path, VIEW + "\n\n" + EVENT)
    split(path)
    edit(path, NEW_TRIGGER, NEW_PROCEDURE)
    (trigger_at, _), (procedure_at, _) = placements(requests)
    assert trigger_at["file"] == "" and procedure_at["file"] == ""
    objects = section(path, "150")
    assert objects.index("150-20_views.sql") < objects.index("TRIGGER `tr_new`") < (
        objects.index("150-40_events.sql"))
    assert objects.index("PROCEDURE `p_new`") < objects.index("150-20_views.sql")
    assert not os.path.exists(os.path.join(path, "development", "sections", "150-30_triggers.sql"))


@needs_placement
def test_a_kind_after_every_breakout_file_goes_below_the_last_source_line(tmp_path, requests):
    path = make_project(tmp_path, VIEW)
    split(path)
    edit(path, NEW_EVENT)
    objects = section(path, "150")
    assert objects.index("150-20_views.sql") < objects.index("EVENT `e_new`")


# -----------------------------------------------------------------------------
# Refusals


@needs_placement
def test_a_caller_cannot_place_statements_itself(tmp_path):
    path = make_project(tmp_path)
    with pytest.raises(ValueError, match="msm places the statements of new objects itself"):
        edit(path, {**NEW_VIEW, "at": {"file": "", "line": 0}})
    with pytest.raises(ValueError, match="`form`"):
        edit(path, {**NEW_VIEW, "form": "create"})


@needs_placement
def test_a_binary_that_cannot_place_statements_is_refused(tmp_path, monkeypatch):
    path = make_project(tmp_path)
    before = script(path)
    monkeypatch.setattr(lsp, "places_statements", lambda binary: False)
    with pytest.raises(lsp.LspError, match="lsp-server #49"):
        edit(path, NEW_VIEW)
    assert script(path) == before
    # Edits that add nothing do not need it
    edit(path, {"op": "rename_table", "table": "orders", "new_name": "purchases"})
    assert "CREATE TABLE `purchases`" in script(path)
