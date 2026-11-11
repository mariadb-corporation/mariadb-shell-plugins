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

"""Tests for the schema diagrams: lib/diagrams.py and lib/modeling.py."""

import json
import os
import re

import pytest

from msm_plugin.tests.unit import helpers
from msm_plugin.lib import diagrams, layout, lsp, management, modeling, sections, source
from msm_plugin.lib.management import create_schema_project_folder

LSP = lsp.find_lsp()
needs_lsp = pytest.mark.skipif(LSP is None, reason="no mariadb-lsp binary")

TABLES_SQL = """CREATE TABLE IF NOT EXISTS `customer` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(100) NOT NULL COMMENT 'The customer''s name',
    PRIMARY KEY (`id`)
);

CREATE TABLE IF NOT EXISTS `orders` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
    `customer_id` INT UNSIGNED NOT NULL,
    `reviewer_id` INT UNSIGNED,
    PRIMARY KEY (`id`),
    CONSTRAINT `fk_orders_customer` FOREIGN KEY (`customer_id`)
        REFERENCES `customer` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_orders_reviewer` FOREIGN KEY (`reviewer_id`)
        REFERENCES `customer` (`id`)
);

CREATE TABLE IF NOT EXISTS `invoice` (
    `order_id` INT UNSIGNED NOT NULL,
    UNIQUE KEY `uk_order` (`order_id`),
    CONSTRAINT `fk_invoice_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)
);"""


def diagram(name="Main", tables=("customer", "orders")):
    """A diagram with a figure per table and the connection between the first two."""
    figures = [
        {
            "id": f"f-{table}",
            "type": "TableFigure",
            "name": table,
            "table": table,
            "left": 20.0 + 200 * index,
            "top": 20.0,
            "width": 160.0,
            "height": 100.0,
            "color": "#98BFDA",
        }
        for index, table in enumerate(tables)
    ]
    connections = []
    if "orders" in tables and "customer" in tables:
        connections.append(
            {
                "id": "c-1",
                "foreignKey": "fk_orders_customer",
                "table": "orders",
                "startFigure": "f-orders",
                "endFigure": "f-customer",
                "visible": True,
            }
        )
    return {
        "format": diagrams.DIAGRAM_FORMAT,
        "name": name,
        "width": 2000.0,
        "height": 1400.0,
        "layers": [],
        "figures": figures,
        "connections": connections,
    }


@pytest.fixture
def project(tmp_path):
    """A new project whose development script has three tables in section 140."""
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.", license="MIT"
    )
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content("140", "\n" + TABLES_SQL + "\n", dev_file)
    return path


def figures_by_type(document) -> dict:
    """A diagram's figures grouped by their type."""
    by_type = {}
    for figure in document["figures"]:
        by_type.setdefault(figure.get("type", "TableFigure"), []).append(figure)
    return by_type


def dev_script(project_path) -> str:
    with open(management.get_schema_development_file_path(project_path)) as f:
        return f.read()


def section_160(project_path) -> str:
    return management.get_script_sections(dev_script(project_path))["160"]["sql_content"]


# -----------------------------------------------------------------------------
# The format


def test_the_view_reads_back_what_was_written():
    rows = [
        {"name": "schema_definition", "format": diagrams.SCHEMA_FORMAT, "document": {"tables": []}},
        {
            "name": "It's \\ \"quoted\"",
            "format": diagrams.DIAGRAM_FORMAT,
            "document": {
                "text": "a 'quote', a \\backslash\\, \\% and \\_, a line\nbreak, a tab\t, ü, ;",
                "nested": [{"a": 1, "b": [1, 2, 3]}] * 30,
            },
        },
    ]
    sql = diagrams.render_view_sql(rows)
    assert sql.startswith("CREATE OR REPLACE SQL SECURITY INVOKER\nVIEW `msm_schema_diagrams`")
    assert sql.endswith(";")
    assert diagrams.parse_view_sql(sql) == rows
    # Comments around the statement, with quotes in them, are not literals
    assert diagrams.parse_view_sql(f"-- It's the view\n/* 'x' */ {sql}\n# don't") == rows


def test_an_empty_view_has_no_rows():
    sql = diagrams.render_view_sql([])
    assert "WHERE FALSE" in sql
    assert diagrams.parse_view_sql(sql) == []
    assert diagrams.parse_view_sql("-- nothing here") == []


def test_a_view_that_is_not_msm_s_is_refused():
    with pytest.raises(ValueError, match="not valid JSON"):
        diagrams.parse_view_sql("CREATE VIEW msm_schema_diagrams AS SELECT 'a', 'b', 'c';")
    with pytest.raises(ValueError, match="string literals"):
        diagrams.parse_view_sql("CREATE VIEW msm_schema_diagrams AS SELECT 'a', 'b';")


def test_documents_are_laid_out_one_short_value_per_line():
    text = diagrams.dump_document(diagram())
    lines = text.splitlines()
    # Each figure, each connection is a line of its own
    assert sum('"type": "TableFigure"' in line for line in lines) == 2
    assert any('"foreignKey": "fk_orders_customer"' in line for line in lines)
    assert json.loads(text) == diagram()
    assert diagrams.dump_document({"a": []}) == '{"a": []}'


def test_moved_positions_do_not_change_the_layout():
    # msm moves the snapshot's positions after writing Section 160 and relies
    # on the line count staying: the numbers of a position count as fixed
    # width, so no line wraps differently however many digits they have.
    # Every name length up to the width meets the line-width limit somewhere.
    def line_count(name_length, line):
        position = {"line": line, "character": 0}
        column = {"name": "a" * name_length, "range": {"start": position, "end": position}}
        return diagrams.dump_document({"columns": [column]}).count("\n")

    for name_length in range(1, 161):
        assert line_count(name_length, 0) == line_count(name_length, 9999999), name_length


def test_a_diagram_is_checked():
    diagrams.validate_diagram(diagram())
    for change, message in (
        ({"format": "mwb-diagram/1"}, "format"),
        ({"name": ""}, "name"),
        ({"name": diagrams.SCHEMA_ROW_NAME}, "schema row"),
        ({"figures": None}, "figures"),
    ):
        with pytest.raises(ValueError, match=message):
            diagrams.validate_diagram({**diagram(), **change})


def test_foreign_keys_get_what_a_diagram_draws_them_with():
    outline = {
        "format": 1,
        "schemas": [
            {
                "name": None,
                "tables": [
                    {
                        "name": "orders",
                        "columns": [
                            {"name": "customer_id", "nullable": False},
                            {"name": "reviewer_id", "nullable": True},
                        ],
                        "indexes": [{"name": "uk", "kind": "unique", "columns": ["reviewer_id"]}],
                        "foreign_keys": [
                            {"name": "a", "columns": ["customer_id"]},
                            {"name": "b", "columns": ["reviewer_id"]},
                        ],
                    }
                ],
                "views": [{"name": "v"}],
            },
            {"name": "other", "tables": [{"name": "elsewhere"}]},
        ],
    }
    document = diagrams.schema_document_from_outline(
        outline, "shop", ["shop_next.sql"], diagrams.source_hash("x")
    )
    assert document["format"] == diagrams.SCHEMA_FORMAT
    assert document["schema"] == "shop"
    assert document["source_hash"] == diagrams.source_hash("x")
    assert [table["name"] for table in document["tables"]] == ["orders"]
    assert [view["name"] for view in document["views"]] == ["v"]
    a, b = document["tables"][0]["foreign_keys"]
    assert (a["many"], a["mandatory"], a["referenced_mandatory"]) == (True, True, True)
    assert (b["many"], b["mandatory"], b["referenced_mandatory"]) == (False, True, False)
    with pytest.raises(ValueError, match="outline format 2"):
        diagrams.schema_document_from_outline({"format": 2}, "shop", [], "sha256:")


def test_notes_on_statements_that_were_not_applied_are_found():
    notes = [
        {"kind": "syntax-error", "applied": False, "message": "not read: the statement has a syntax error"},
        {"kind": "unknown-table", "applied": False, "message": "ALTER TABLE t: the script does not create this table"},
        {"kind": "unknown-table", "applied": True, "message": "CREATE TABLE ... LIKE t, which the script does not create"},
        {"kind": "a-kind-added-later", "applied": False, "message": "anything"},
    ]
    assert [note["kind"] for note in diagrams.unread_statements({"notes": notes})] == [
        "syntax-error",
        "unknown-table",
        "a-kind-added-later",
    ]
    # A binary from before `applied`: by the message
    older = [
        {"message": "not read: the statement has a syntax error"},
        {"message": "ALTER TABLE t: the script does not create this table"},
    ]
    assert diagrams.unread_statements({"notes": older}) == older[:1]


def test_a_diagram_loses_what_the_schema_no_longer_has():
    schema = {
        "tables": [
            {"name": "customer", "foreign_keys": []},
            {"name": "orders", "foreign_keys": []},
        ]
    }
    kept = diagram(tables=("customer", "orders", "gone"))
    kept["layers"] = [{"id": "l", "figures": ["f-gone", "f-orders"]}]
    assert diagrams.reconcile_diagram(kept, schema)
    assert [figure["table"] for figure in kept["figures"]] == ["customer", "orders"]
    # The foreign key is gone, so is its connection
    assert kept["connections"] == []
    assert kept["layers"][0]["figures"] == ["f-orders"]
    assert not diagrams.reconcile_diagram(kept, schema)


def _orders_schema(*keys):
    """customer, address and orders, whose foreign keys are (name, parent)
    or (name, parent, schema)."""
    return {
        "schema": "shop",
        "tables": [
            {"name": "customer", "foreign_keys": []},
            {"name": "address", "foreign_keys": []},
            {
                "name": "orders",
                "foreign_keys": [
                    {
                        "name": name,
                        "references": {"schema": (schema or [None])[0], "table": parent,
                                       "columns": ["id"]},
                    }
                    for name, parent, *schema in keys
                ],
            },
        ],
    }


def test_a_connection_follows_its_foreign_key_to_a_new_name():
    document = diagram()
    connection = document["connections"][0]
    connection.update(caption="places", middleSegmentOffset=12.5, drawSplit=True, visible=False)
    expected = {**connection, "foreignKey": "orders_ibfk_1"}
    # Dropped and added again under another name, as the table editor does
    schema = _orders_schema(("orders_ibfk_1", "customer"))
    assert diagrams.reconcile_diagram(document, schema)
    assert document["connections"] == [expected]
    assert not diagrams.reconcile_diagram(document, schema)


def test_a_connection_is_matched_by_name_first_then_in_the_order_of_the_keys():
    document = diagram(tables=("customer", "orders"))
    first = document["connections"][0]
    second = {**first, "id": "c-2", "foreignKey": "fk_gone"}
    third = {**first, "id": "c-3", "foreignKey": "fk_also_gone"}
    document["connections"] += [second, third]
    first["foreignKey"] = "FK_BILLED"  # its name, in another case
    schema = _orders_schema(
        ("fk_shipped", "customer"), ("fk_billed", "customer"), ("fk_other", "address")
    )
    assert diagrams.reconcile_diagram(document, schema)
    # c-1 keeps its key; c-2 takes the first key left; c-3 finds none to
    # the customer table (fk_other references address) and is dropped.
    assert [(c["id"], c["foreignKey"]) for c in document["connections"]] == [
        ("c-1", "fk_billed"),
        ("c-2", "fk_shipped"),
    ]


def test_a_connection_whose_key_now_references_another_table_is_re_pointed_or_dropped():
    document = diagram(tables=("customer", "orders", "address"))
    # The key of the connection's name now references address: the line to
    # customer draws the key that does reference customer.
    schema = _orders_schema(("fk_orders_customer", "address"), ("fk_buyer", "customer"))
    assert diagrams.reconcile_diagram(document, schema)
    assert [c["foreignKey"] for c in document["connections"]] == ["fk_buyer"]
    # No key to customer is left: the connection goes.
    schema = _orders_schema(("fk_orders_customer", "address"))
    assert diagrams.reconcile_diagram(document, schema)
    assert document["connections"] == []


def test_a_key_to_a_table_of_another_schema_draws_no_connection():
    document = diagram()
    assert not diagrams.reconcile_diagram(
        document, _orders_schema(("fk_orders_customer", "customer", "SHOP"))
    )
    assert diagrams.reconcile_diagram(
        document, _orders_schema(("fk_orders_customer", "customer", "crm"))
    )
    assert document["connections"] == []


# -----------------------------------------------------------------------------
# Section 160 of a project


def test_pictures_are_elided_and_kept():
    picture = "data:image/png;base64,iVBORw0KGgo="
    stored = diagram("D", tables=("customer",))
    stored["figures"].append({"id": "i", "type": "ImageFigure", "name": "Logo", "image": picture})
    elided = diagrams.elide_images(stored)
    assert "image" not in elided["figures"][1]
    assert elided["figures"][1]["image_size"] == len(picture)
    assert stored["figures"][1]["image"] == picture  # a copy
    assert "image_size" not in elided["figures"][0]

    sent = diagram("D", tables=("customer",))
    sent["figures"] += [
        {"id": "i", "type": "ImageFigure", "name": "Logo"},
        {"id": "new", "type": "ImageFigure", "name": "No picture"},
    ]
    diagrams.keep_images(sent, stored)
    assert sent["figures"][1]["image"] == picture
    assert "image" not in sent["figures"][2]
    diagrams.keep_images(sent, None)


def test_reconcile_keeps_views_routines_notes_and_images_the_schema_allows():
    schema = {"tables": [], "views": [{"name": "V"}], "routines": [{"name": "f"}]}
    document = diagram("D", tables=())
    document["figures"] = [
        {"id": "v", "type": "ViewFigure", "view": "v"},
        {"id": "gone", "type": "ViewFigure", "view": "gone"},
        {"id": "g", "type": "RoutineGroupFigure", "name": "G", "routines": ["F", "gone"]},
        {"id": "empty", "type": "RoutineGroupFigure", "name": "E", "routines": ["gone"]},
        {"id": "n", "type": "NoteFigure", "text": "kept as it is"},
        {"id": "i", "type": "ImageFigure", "image": "data:image/png;base64,AA=="},
    ]
    assert diagrams.reconcile_diagram(document, schema)
    assert [f["id"] for f in document["figures"]] == ["v", "g", "n", "i"]
    assert document["figures"][1]["routines"] == ["F"]
    assert not diagrams.reconcile_diagram(document, schema)


def rest_figure(figure_id, service, schema, rest_object, **members):
    return {
        "id": figure_id, "type": diagrams.REST_OBJECT_FIGURE, "name": rest_object,
        "restService": service, "restSchema": schema, "restObject": rest_object, **members,
    }


REST_PART = {"services": [{
    "path": "mike,anna@/notes", "declared": True,
    "schemas": [{"path": "/app", "declared": True, "objects": [{"kind": "view", "path": "/note"}]}],
}]}


def test_reconcile_keeps_the_rest_objects_section_180_creates():
    schema = {"tables": [], "rest": REST_PART}
    document = diagram("D", tables=())
    document["figures"] = [
        # Paths compared as the server does: case ignored, quotes left out
        rest_figure("kept", "MIKE,anna@/Notes", "`/app`", "/NOTE"),
        rest_figure("gone", "mike,anna@/notes", "/app", "/gone"),
        rest_figure("other service", "mike@/notes", "/app", "/note"),
    ]
    assert diagrams.reconcile_diagram(document, schema)
    assert [f["id"] for f in document["figures"]] == ["kept"]
    assert not diagrams.reconcile_diagram(document, schema)

    # Without Section 180's REST, none is left
    assert diagrams.reconcile_diagram(document, {"tables": []})
    assert document["figures"] == []


def test_reconcile_keeps_rest_figures_where_the_snapshot_cannot_tell():
    # A snapshot of a mariadb-lsp before the REST outline features
    old = {"services": [{"path": "/notes", "schemas": []}]}
    document = diagram("D", tables=())
    document["figures"] = [rest_figure("r", "/notes", "/app", "/note")]
    assert not diagrams.reconcile_diagram(document, {"tables": [], "rest": old})
    assert [f["id"] for f in document["figures"]] == ["r"]


def test_a_rest_figure_covers_its_depth_and_stays_in_place():
    standing = rest_figure("r", "/s", "/a", "/o", left=40, top=40, width=220, height=160,
                           depth=24)
    assert diagrams.footprint(standing) == (220, 24)
    assert diagrams.footprint({**standing, "depth": None}) == (220, diagrams.REST_OBJECT_DEPTH)
    assert diagrams.footprint({"width": 100, "height": 80}) == (100, 80)

    document = diagram("D", tables=())
    document["figures"] = [
        dict(standing),
        {"id": "t", "type": "TableFigure", "name": "t", "table": "t"},
    ]
    catalog = diagrams.catalog({"tables": [{"name": "t", "columns": []}]})
    assert modeling._place(document, catalog, keep_placed=False) == ["t"]
    assert document["figures"][0] == standing
    # Below the card's footprint, not below how high it stands
    assert document["figures"][1]["top"] == 40 + 24 + layout.ROW_GAP


def test_reconcile_keeps_the_events_and_sequences_the_schema_has():
    schema = {"tables": [], "events": [{"name": "nightly"}], "sequences": [{"name": "S"}]}
    document = diagram("D", tables=())
    document["figures"] = [
        {"id": "e", "type": "EventGroupFigure", "name": "Events", "events": ["NIGHTLY", "gone"]},
        {"id": "empty", "type": "EventGroupFigure", "name": "E", "events": ["gone"]},
        {"id": "s", "type": "SequenceFigure", "sequence": "s"},
        {"id": "gone", "type": "SequenceFigure", "sequence": "gone"},
    ]
    assert diagrams.reconcile_diagram(document, schema)
    assert [f["id"] for f in document["figures"]] == ["e", "s"]
    assert document["figures"][0]["events"] == ["NIGHTLY"]
    assert not diagrams.reconcile_diagram(document, schema)


def test_a_sequence_card_shows_its_type_start_and_increment():
    rows = diagrams.sequence_rows
    assert rows({"name": "s", "type": None, "start": None, "increment": None}) == [
        "BIGINT", "START WITH 1", "INCREMENT BY 1"]
    assert rows({"name": "s", "type": "INT UNSIGNED", "start": "1000", "increment": "10",
                 "cache": "0", "cycle": True}) == [
        "INT UNSIGNED", "START WITH 1000", "INCREMENT BY 10"]
    assert diagrams.FIGURE_KINDS["SequenceFigure"].sections({"name": "s"}, {}) == [3]


def test_a_tables_card_counts_its_triggers():
    schema = {"tables": [{"name": "orders", "columns": [{"name": "id"}]}, {"name": "other"}],
              "triggers": [{"name": "a", "table": "ORDERS"}, {"name": "b", "table": "orders"},
                           {"name": "c", "table": "gone"}]}
    tables = diagrams.catalog(schema)["tables"]
    assert [trigger["name"] for trigger in tables["orders"]["triggers"]] == ["a", "b"]
    assert "triggers" not in schema["tables"][0]  # the snapshot stays as it is
    sections = diagrams.FIGURE_KINDS["TableFigure"].sections
    assert sections(tables["orders"], {}) == [1, 2]
    assert sections(tables["orders"], {"triggersExpanded": False}) == [1]
    assert sections(tables["other"], {}) == [0, 0]


def test_a_picture_is_read_by_its_figure(project):
    picture = "data:image/svg+xml;base64,PHN2Zy8+"
    document = diagram()
    document["figures"] += [
        {"id": "logo", "type": "ImageFigure", "name": "Logo", "image": picture},
        {"id": "empty", "type": "ImageFigure", "name": "Empty"},
    ]
    modeling.set_diagram(project, document)
    assert modeling.get_diagram_image(project, "Main", "logo") == picture
    with pytest.raises(ValueError, match="no diagram `Other`"):
        modeling.get_diagram_image(project, "Other", "logo")
    with pytest.raises(ValueError, match="no figure `gone`"):
        modeling.get_diagram_image(project, "Main", "gone")
    with pytest.raises(ValueError, match="holds no picture"):
        modeling.get_diagram_image(project, "Main", "empty")
    table_figure = document["figures"][0]["id"]
    with pytest.raises(ValueError, match="holds no picture"):
        modeling.get_diagram_image(project, "Main", table_figure)


def test_a_new_project_has_an_empty_diagrams_section(project):
    assert "MSM Section 160: Schema Diagrams" in dev_script(project)
    assert modeling.read_diagram_rows(project) == []


def test_only_script_positions_after_section_160_move():
    def at(line):
        return {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 1}}

    document = {"range": at(3), "tables": [
        # Included, but a column an ALTER TABLE of the script adds is in the script
        {"source": 0, "range": at(20), "columns": [
            {"range": at(30)}, {"source": 0, "range": at(40)}]},
        {"range": at(50), "columns": [{"source": 1, "range": at(60)}]},
    ], "notes": [{"start": {"line": 70, "character": 0}},
                 {"source": 0, "start": {"line": 80, "character": 0}}]}
    modeling._shift_script_lines(document, 10, 5)
    lines = [
        document["range"]["start"]["line"],
        document["tables"][0]["range"]["start"]["line"],
        document["tables"][0]["columns"][0]["range"]["start"]["line"],
        document["tables"][0]["columns"][1]["range"]["start"]["line"],
        document["tables"][1]["range"]["end"]["line"],
        document["tables"][1]["columns"][0]["range"]["start"]["line"],
        document["notes"][0]["start"]["line"],
        document["notes"][1]["start"]["line"],
    ]
    assert lines == [3, 20, 35, 40, 55, 60, 75, 80]


def test_small_diagrams_stay_inline(project):
    modeling.set_diagram(project, diagram())
    assert "CREATE OR REPLACE SQL SECURITY INVOKER" in section_160(project)
    assert not os.path.exists(
        os.path.join(project, "development", "sections", "160_schema_diagrams.sql")
    )
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main"]
    # The next section's banner keeps its blank lines
    assert "}';\n\n\n-- ####" in dev_script(project)

    # Nothing changes, nothing is written
    assert modeling.set_diagram(project, diagram()) == []
    modeling.set_diagram(project, {**diagram(), "width": 10.0})
    modeling.set_diagram(project, diagram("Second"))
    rows = modeling.read_diagram_rows(project)
    assert [row["name"] for row in rows] == ["Main", "Second"]
    assert rows[0]["document"]["width"] == 10.0

    modeling.remove_diagram(project, "Main")
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Second"]
    with pytest.raises(ValueError, match="no diagram `Main`"):
        modeling.remove_diagram(project, "Main")


def test_a_diagram_is_renamed_with_its_pictures_and_place(project):
    picture = "data:image/png;base64,iVBORw0KGgo="
    first = diagram("Main", tables=("customer",))
    first["figures"].append({"id": "i", "type": "ImageFigure", "name": "Logo", "image": picture})
    modeling.set_diagram(project, first)
    modeling.set_diagram(project, diagram("Second"))
    modeling.set_diagram(project, diagram("Third"))

    assert modeling.rename_diagram(project, "Second", "  Orders  ") != []
    rows = modeling.read_diagram_rows(project)
    assert [row["name"] for row in rows] == ["Main", "Orders", "Third"]
    assert rows[1]["document"] == {**diagram("Second"), "name": "Orders"}

    # A change of case of the diagram itself; the picture stays
    modeling.rename_diagram(project, "Main", "main")
    rows = modeling.read_diagram_rows(project)
    assert [row["name"] for row in rows] == ["main", "Orders", "Third"]
    assert rows[0]["document"]["figures"][1]["image"] == picture
    assert rows[0]["document"]["name"] == "main"

    # The same name: nothing is written
    assert modeling.rename_diagram(project, "Third", "Third") == []


@pytest.mark.parametrize("name, new_name, message", [
    ("Gone", "New", "The project has no diagram `Gone`."),
    ("Main", "   ", "A diagram needs a name."),
    ("Main", None, "A diagram needs a name."),
    ("Main", "schema_definition", "`schema_definition` is the name of the schema row."),
    ("Main", "SECOND", "The project has a diagram `Second` already."),
    ("schema_definition", "New", "The project has no diagram `schema_definition`."),
])
def test_a_rename_is_refused(project, name, new_name, message):
    modeling.set_diagram(project, diagram("Main"))
    modeling.set_diagram(project, diagram("Second"))
    with pytest.raises(ValueError, match=re.escape(message)):
        modeling.rename_diagram(project, name, new_name)
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main", "Second"]


@needs_lsp
@pytest.mark.parametrize("breaks_out", [False, True])
def test_the_snapshot_positions_follow_a_section_160_that_changes_size(project, breaks_out):
    # Inline, the VIEW holding the snapshot grows with every diagram, which
    # moves everything after it (the version VIEW of Section 910); past the
    # limit it moves into the breakout file and shrinks to one line.
    modeling.update_schema_definition(project)
    # Just above the snapshot alone: the first diagram moves the VIEW out
    limit = len(section_160(project)) + 100 if breaks_out else 10**7
    helpers.set_project_setting(project, "maxInlineSectionSize", limit)

    for name in ("Main", "Second", "Third"):
        modeling.create_diagram(project, name)
        stored = modeling.read_diagram_rows(project)[0]["document"]
        assert stored == modeling.build_schema_definition(project)
    version_view = next(view for view in stored["views"] if view["name"] == "msm_schema_version")
    line = version_view["range"]["start"]["line"]
    assert dev_script(project).split("\n")[line].startswith("CREATE OR REPLACE")
    if breaks_out:
        assert "SOURCE './sections/160_schema_diagrams.sql'" in section_160(project)


def test_large_diagrams_move_into_a_breakout_file(project):
    helpers.set_project_setting(project, "maxInlineSectionSize", 200)

    modeling.set_diagram(project, diagram())
    body = section_160(project)
    assert "SOURCE './sections/160_schema_diagrams.sql'[ln" in body
    breakout = os.path.join(project, "development", "sections", "160_schema_diagrams.sql")
    with open(breakout) as f:
        content = f.read()
    assert content.startswith("-- Copyright (c) ")
    assert "Example Ltd." in content

    # It stays there, its header kept, whatever its size
    modeling.set_diagram(project, diagram("Second"))
    with open(breakout) as f:
        second = f.read()
    assert second.splitlines()[:3] == content.splitlines()[:3]
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main", "Second"]
    # The release's expansion of the development script sees the same rows
    expanded = source.expand_source_directives(
        dev_script(project), os.path.join(project, "development")
    )[0]
    assert len(diagrams.parse_view_sql(management.get_script_sections(expanded)["160"]["sql_content"])) == 2


def test_a_project_without_the_section_gets_it(project):
    dev_file = management.get_schema_development_file_path(project)
    sections = management.get_file_sections(dev_file)
    del sections["160"]
    management.write_sections_to_file(dev_file, sections, overwrite_existing=True)
    assert "Section 160" not in dev_script(project)

    hash_without = modeling.current_source_hash(project)
    modeling.set_diagram(project, diagram())
    ids = list(management.get_script_sections(dev_script(project)))
    assert ids.index("160") == ids.index("150") + 1
    # The section msm added does not make a snapshot out of date
    assert modeling.current_source_hash(project) == hash_without


def test_an_include_that_would_cut_the_view_is_refused(project):
    sections_dir = os.path.join(project, "development", "sections")
    os.makedirs(sections_dir)
    with open(os.path.join(sections_dir, "160.sql"), "w") as f:
        f.write("-- header\n" + diagrams.render_view_sql([]) + "\n-- footer\n")
    dev_file = management.get_schema_development_file_path(project)
    # An end line counted from the start does not move with a larger VIEW
    management.set_section_sql_content("160", "SOURCE './sections/160.sql'[ln2:ln3];", dev_file)
    with pytest.raises(ValueError, match="whole diagrams VIEW"):
        modeling.set_diagram(project, diagram())
    # Counted from the end, it does
    management.set_section_sql_content("160", "SOURCE './sections/160.sql'[ln2:ln-2];", dev_file)
    modeling.set_diagram(project, diagram())
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main"]
    with open(os.path.join(sections_dir, "160.sql")) as f:
        content = f.read()
    assert content.startswith("-- header\n") and content.endswith("\n-- footer\n")


def test_an_include_can_be_refused(project):
    helpers.set_project_setting(project, "maxInlineSectionSize", 0)

    def refuse(path):
        raise ValueError(f"not allowed: {path}")

    with pytest.raises(ValueError, match="not allowed"), source.checked_includes(refuse):
        modeling.set_diagram(project, diagram())
    modeling.set_diagram(project, diagram())
    with pytest.raises(ValueError, match="not allowed"), source.checked_includes(refuse):
        modeling.read_diagram_rows(project)
    # The check gives the path to use
    with source.checked_includes(lambda path: path):
        assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main"]


def test_a_bad_inline_size_setting_is_refused():
    for value in (-1, "16k", True):
        with pytest.raises(ValueError, match="maxInlineSectionSize"):
            sections.get_max_inline_section_size({"maxInlineSectionSize": value})
    assert sections.get_max_inline_section_size({}) == sections.DEFAULT_MAX_INLINE_SECTION_SIZE


# -----------------------------------------------------------------------------
# The snapshot, by the language server


def test_without_the_binary_there_is_no_snapshot(project, tmp_path):
    with pytest.raises(lsp.LspError, match="could not be found"):
        modeling.update_schema_definition(project, lsp_path=str(tmp_path / "nothing"))


@needs_lsp
def test_the_snapshot_is_made_from_the_create_statements(project):
    modeling.set_diagram(project, diagram(tables=("customer", "orders")))
    result = modeling.update_schema_definition(project)
    assert result["tables"] == 3
    assert result["changed_diagrams"] == []

    rows = modeling.read_diagram_rows(project)
    assert [row["name"] for row in rows] == ["schema_definition", "Main"]
    schema = rows[0]["document"]
    assert schema["format"] == diagrams.SCHEMA_FORMAT
    assert schema["schema"] == "shop"
    assert schema["files"] == ["shop_next.sql"]
    tables = {table["name"]: table for table in schema["tables"]}
    assert set(tables) == {"customer", "orders", "invoice"}
    # The objects of the VIEW and the version are kept, the diagrams VIEW is not
    assert [view["name"] for view in schema["views"]] == ["msm_schema_version"]
    assert tables["customer"]["columns"][1]["comment"] == "The customer's name"
    by_name = {fk["name"]: fk for fk in tables["orders"]["foreign_keys"]}
    assert by_name["fk_orders_customer"]["on_delete"] == "CASCADE"
    assert by_name["fk_orders_customer"]["referenced_mandatory"] is True
    assert by_name["fk_orders_reviewer"]["referenced_mandatory"] is False
    assert tables["invoice"]["foreign_keys"][0]["many"] is False
    # Positions are those of the development script
    lines = dev_script(project).splitlines()
    start = tables["orders"]["selection_range"]["start"]
    assert lines[start["line"]][start["character"] :].startswith("`orders`")

    listing = modeling.list_diagrams(project)
    assert listing["schema_definition"] == {"tables": 3, "current": True}
    management.set_section_sql_content(
        "140",
        TABLES_SQL.replace("`reviewer_id` INT UNSIGNED,", "`reviewer_id` INT UNSIGNED NOT NULL,"),
        management.get_schema_development_file_path(project),
    )
    assert modeling.list_diagrams(project)["schema_definition"]["current"] is False


@needs_lsp
def test_dropped_tables_leave_the_diagrams(project):
    modeling.set_diagram(project, diagram(tables=("customer", "orders", "invoice")))
    management.set_section_sql_content(
        "140",
        TABLES_SQL.split("CREATE TABLE IF NOT EXISTS `orders`")[0],
        management.get_schema_development_file_path(project),
    )
    result = modeling.update_schema_definition(project)
    assert result["changed_diagrams"] == ["Main"]
    figures = modeling.get_diagram_rows(project, "Main")[1]["document"]["figures"]
    assert [figure["table"] for figure in figures] == ["customer"]


@needs_lsp
def test_a_rest_object_section_180_drops_leaves_the_diagrams(project):
    if not lsp.outlines_rest(LSP):
        pytest.skip("a mariadb-lsp without the REST outline features")
    dev_file = management.get_schema_development_file_path(project)
    rest = (
        "CREATE OR REPLACE REST SERVICE /notes;\n"
        "CREATE OR REPLACE REST SCHEMA /app ON SERVICE /notes FROM `shop`;\n"
        "CREATE OR REPLACE REST VIEW /customer ON SERVICE /notes SCHEMA /app AS `customer`;\n"
    )
    orders = "CREATE OR REPLACE REST VIEW /orders ON SERVICE /notes SCHEMA /app AS `orders`;\n"
    management.set_section_sql_content("180", "\n" + rest + orders, dev_file)
    document = diagram(tables=("customer",))
    document["figures"] += [
        rest_figure("c", "/notes", "/app", "/customer", left=0, top=300, width=220,
                    height=160, depth=20),
        rest_figure("o", "/notes", "/app", "/orders", left=240, top=300, width=220,
                    height=160, depth=20),
    ]
    modeling.set_diagram(project, document)
    kept = modeling.get_diagram_rows(project, "Main")[-1]["document"]["figures"]
    assert [figure["id"] for figure in kept][-2:] == ["c", "o"]
    assert kept[-1] == document["figures"][-1]  # kept as it was sent

    management.set_section_sql_content("180", "\n" + rest, dev_file)
    result = modeling.update_schema_definition(project)
    assert result["changed_diagrams"] == ["Main"]
    figures = modeling.get_diagram_rows(project, "Main")[-1]["document"]["figures"]
    assert [figure["id"] for figure in figures if figure["type"] == "RestObjectFigure"] == ["c"]


@needs_lsp
def test_included_objects_name_their_file(project):
    sections = os.path.join(project, "development", "sections")
    os.makedirs(sections)
    with open(os.path.join(sections, "140-10_tables.sql"), "w") as f:
        f.write("-- Copyright (c) 2026, Example Ltd.\n" + TABLES_SQL + "\n")
    management.set_section_sql_content(
        "140",
        "SOURCE './sections/140-10_tables.sql'[ln2:];",
        management.get_schema_development_file_path(project),
    )
    modeling.update_schema_definition(project)
    schema = modeling.read_diagram_rows(project)[0]["document"]
    assert schema["files"] == ["shop_next.sql", "sections/140-10_tables.sql"]
    customer = next(table for table in schema["tables"] if table["name"] == "customer")
    assert customer["source"] == 0
    assert customer["selection_range"]["start"]["line"] == 1

    def refuse(path):
        raise ValueError(f"not allowed: {path}")

    with pytest.raises(ValueError, match="not allowed"), source.checked_includes(refuse):
        modeling.update_schema_definition(project)


@needs_lsp
def test_a_statement_that_cannot_be_read_stops_the_snapshot(project):
    management.set_section_sql_content(
        "140",
        TABLES_SQL + "\nCREATE TABLE broken (id INT,);",
        management.get_schema_development_file_path(project),
    )
    with pytest.raises(ValueError, match="were not applied"):
        modeling.update_schema_definition(project)
    assert modeling.read_diagram_rows(project) == []


# -----------------------------------------------------------------------------
# Releases and deployments


@needs_lsp
def test_releases_carry_the_diagrams_to_the_server(sandbox_session, tmp_path):
    from msm_plugin import lib as msm_lib
    from msm_plugin.management import (
        deploy_schema,
        generate_deployment_script,
        prepare_release,
    )

    schema_name = "msm_diagrams_test"
    path = create_schema_project_folder(
        schema_name=schema_name,
        target_path=str(tmp_path),
        copyright_holder="Example Ltd.",
        license="MIT",
    )
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content("140", "\n" + TABLES_SQL + "\n", dev_file)
    modeling.set_diagram(path, diagram())

    def rows_on_server():
        return [
            (row["name"], row["format"], row["size"])
            for row in msm_lib.core.MsmDbExec(
                f"SELECT `name`, `format`, LENGTH(`document`) AS size "
                f"FROM `{schema_name}`.`msm_schema_diagrams`"
            )
            .exec(sandbox_session)
            .items
        ]

    # A release makes the snapshot first; it ships inlined in the version
    prepare_release(schema_project_path=path, version="1.0.0", next_version="1.1.0")
    version_file = os.path.join(path, "releases", "versions", f"{schema_name}_1.0.0.sql")
    with open(version_file) as f:
        released = management.get_script_sections(f.read())
    assert [row["name"] for row in diagrams.parse_view_sql(released["160"]["sql_content"])] == [
        "schema_definition",
        "Main",
    ]

    # The second release's diagrams are large: a breakout file, a big VIEW
    large = diagram("Large")
    large["description"] = "x" * 1_000_000
    modeling.set_diagram(path, large)
    assert source.contains_source_directive(section_160(path))
    prepare_release(schema_project_path=path, version="1.1.0", next_version="1.2.0")
    for version in ("1.0.0", "1.1.0"):
        generate_deployment_script(schema_project_path=path, version=version)

    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")
    try:
        deploy_schema(schema_project_path=path, version="1.0.0")
        assert [row[:2] for row in rows_on_server()] == [
            ("schema_definition", diagrams.SCHEMA_FORMAT),
            ("Main", diagrams.DIAGRAM_FORMAT),
        ]
        deploy_schema(schema_project_path=path, version="1.1.0")
        rows = rows_on_server()
        assert [row[0] for row in rows] == ["schema_definition", "Main", "Large"]
        assert rows[2][2] > 1_000_000
    finally:
        sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")


def test_a_release_needs_the_snapshot(tmp_path, monkeypatch):
    from msm_plugin.management import prepare_release

    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.", license="MIT"
    )
    monkeypatch.setattr(lsp, "find_lsp", lambda lsp_path=None: None)
    with pytest.raises(lsp.LspError, match="could not be found"):
        prepare_release(schema_project_path=path, version="1.0.0", next_version="1.0.1")
    assert os.listdir(os.path.join(path, "releases", "versions")) == ["README.md"]


def test_a_diagram_can_be_given_as_a_file(project, tmp_path):
    """The command line parses every option value as JSON it can, so a whole
    document is given as a file."""
    from msm_plugin import modeling as wrappers

    path = tmp_path / "diagram.json"
    path.write_text(json.dumps(diagram("File")))
    wrappers.set_schema_diagram(document_file=str(path), schema_project_path=project)
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["File"]
    with pytest.raises(ValueError, match="not both"):
        wrappers.set_schema_diagram(diagram(), document_file=str(path), schema_project_path=project)
    path.write_text("{")
    with pytest.raises(ValueError, match="not valid JSON"):
        wrappers.set_schema_diagram(document_file=str(path), schema_project_path=project)
    with pytest.raises(ValueError, match="could not be read"):
        wrappers.set_schema_diagram(document_file=str(tmp_path / "missing.json"), schema_project_path=project)
    with pytest.raises(ValueError, match="No diagram"):
        wrappers.set_schema_diagram(schema_project_path=project)
