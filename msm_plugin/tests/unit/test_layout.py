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

"""Tests for the schema diagram layout: lib/layout.py, and lib/modeling.py's
createSchemaDiagram / layoutSchemaDiagram."""

import pytest

from msm_plugin.lib import diagrams, layout, management, modeling
# The project, its tables and a diagram of them, as the diagram tests have them.
from test_diagrams import TABLES_SQL, diagram, figures_by_type, needs_lsp, project  # noqa: F401


def boxes(positions, sizes):
    return {
        node: (left, top, left + sizes[node][0], top + sizes[node][1])
        for node, (left, top) in positions.items()
    }


def apart(a, b, gap=0):
    return a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]


def crossings(positions, sizes, edges):
    """Crossings of the straight lines from each child's top to its parent's bottom."""
    def segment(child, parent):
        cl, ct = positions[child]
        pl, pt = positions[parent]
        return (cl + sizes[child][0] / 2, ct), (pl + sizes[parent][0] / 2, pt + sizes[parent][1])

    def cross(s, t):
        def orient(a, b, c):
            value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            return (value > 0) - (value < 0)
        (a, b), (c, d) = s, t
        if len({a, b, c, d}) < 4:
            return False
        return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0

    segments = [segment(c, p) for c, p in edges]
    return sum(
        cross(segments[i], segments[j])
        for i in range(len(segments))
        for j in range(i + 1, len(segments))
    )


# -----------------------------------------------------------------------------
# lib/layout.py


def test_referenced_tables_go_above_the_tables_referring_to_them():
    nodes = ["orders", "customer", "item", "product", "country"]
    sizes = {node: (150, 100) for node in nodes}
    edges = [
        ("orders", "customer"), ("item", "orders"), ("item", "product"),
        ("customer", "country"),
    ]
    positions = layout.layout(nodes, sizes, edges)
    for child, parent in edges:
        assert positions[parent][1] + sizes[parent][1] < positions[child][1]
    # A parent sits just above its highest child, not at the very top.
    assert positions["product"][1] == positions["orders"][1]
    assert min(left for left, _ in positions.values()) == 0
    assert min(top for _, top in positions.values()) == 0


def test_figures_keep_workbench_s_gap():
    nodes = [f"t{i}" for i in range(12)]
    sizes = {node: (100 + 20 * (i % 4), 60 + 40 * (i % 3)) for i, node in enumerate(nodes)}
    edges = [(nodes[i], nodes[i // 2]) for i in range(1, 12)]
    positions = layout.layout(nodes, sizes, edges)
    placed = boxes(positions, sizes)
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            assert apart(placed[a], placed[b], layout.GAP - 1), (a, b)


def test_crossings_are_taken_out():
    # Drawn in this order, every connection crosses the others.
    nodes = ["p1", "p2", "p3", "hub", "c1", "c2", "c3"]
    sizes = {node: (150, 100) for node in nodes}
    edges = [("c1", "p3"), ("c2", "p2"), ("c3", "p1"), ("c1", "hub"), ("c3", "hub")]
    positions = layout.layout(nodes, sizes, edges)
    assert crossings(positions, sizes, edges) == 0


def test_cycles_and_self_references_are_laid_out():
    nodes = ["a", "b", "c", "employee"]
    sizes = {node: (150, 100) for node in nodes}
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("employee", "employee"), ("employee", "a")]
    positions = layout.layout(nodes, sizes, edges)
    assert set(positions) == set(nodes)
    assert len({top for _, top in positions.values()}) >= 2


def test_tables_without_foreign_keys_go_below():
    nodes = ["lonely1", "parent", "child", "lonely2"]
    sizes = {"lonely1": (120, 50), "parent": (150, 300), "child": (150, 100), "lonely2": (120, 50)}
    positions = layout.layout(nodes, sizes, [("child", "parent")])
    lowest_linked = max(positions[n][1] + sizes[n][1] for n in ("parent", "child"))
    assert positions["lonely1"][1] > lowest_linked
    assert positions["lonely1"][1] == positions["lonely2"][1]
    assert positions["lonely1"][0] < positions["lonely2"][0]


def test_the_layout_is_deterministic_and_quick():
    import random
    import time

    generator = random.Random(7)
    nodes = [f"t{i}" for i in range(400)]
    edges = [
        (nodes[i], nodes[generator.randrange(i)])
        for i in range(1, 400)
        for _ in range(generator.choice([0, 1, 1, 2]))
    ]
    sizes = {node: (generator.choice([120, 180, 240]), generator.choice([66, 120, 300])) for node in nodes}
    start = time.monotonic()
    first = layout.layout(nodes, sizes, edges)
    assert time.monotonic() - start < 10
    assert layout.layout(nodes, sizes, edges) == first


def test_figure_sizes_follow_the_table_card():
    table = {
        "name": "customer",
        "columns": [
            {"name": "id", "type": "INT UNSIGNED"},
            {"name": "name", "type": "VARCHAR(100)"},
        ],
        "indexes": [{"name": "PRIMARY"}],
        "foreign_keys": [],
    }
    # Header 30, padding 8, heading 16, two rows of 18, padding 8.
    assert layout.figure_height(table) == 30 + 8 + 16 + 2 * 18 + 8
    assert layout.figure_height(table, {"expanded": False}) == 30
    assert layout.figure_height(table, {"indicesExpanded": True}) == 30 + 8 + 16 + 36 + 6 + 16 + 18 + 8
    assert layout.figure_width({"name": "t", "columns": [{"name": "id", "type": "INT"}]}) == (
        layout.MIN_FIGURE_WIDTH
    )
    wide = {"name": "t", "columns": [{"name": "a_rather_long_column_name", "type": "VARCHAR(255)"}]}
    assert layout.MIN_FIGURE_WIDTH < layout.figure_width(wide) <= layout.MAX_FIGURE_WIDTH
    assert layout.figure_width(wide) % 10 == 0
    huge = {"name": "t", "columns": [{"name": "x" * 200, "type": "TEXT"}]}
    assert layout.figure_width(huge) == layout.MAX_FIGURE_WIDTH


def card_size(figure_type, objects, figure=None, title=None):
    """The size of a figure's card, as modeling sizes it: by the kind's rows and sections."""
    kind = diagrams.FIGURE_KINDS[figure_type]
    title = title if title is not None else objects.get("name", "")
    return layout.card_size(title, kind.rows(objects), kind.sections(objects, figure or {}), figure)


def test_view_and_routine_cards_are_sized_like_table_cards():
    view = {"name": "big_orders", "columns": []}
    assert card_size("ViewFigure", view) == (140, 30 + 2 * 8)  # as wide as its title
    assert card_size("ViewFigure", {"name": "v", "columns": ["a", "b"]}) == (
        layout.MIN_FIGURE_WIDTH, 30 + 8 + 16 + 36 + 8,
    )
    assert card_size("ViewFigure", view, {"expanded": False})[1] == 30
    routines = [
        {"name": "order_count", "kind": "function", "parameters": [], "returns": "INT"},
        {"name": "add_order", "kind": "procedure",
         "parameters": [{"mode": "IN", "name": "c", "type": "INT UNSIGNED"}], "returns": None},
    ]
    assert [diagrams.routine_label(r) for r in routines] == [
        "order_count(): INT", "add_order(INT UNSIGNED)",
    ]
    width, height = card_size("RoutineGroupFigure", routines, title="Routines")
    assert height == 30 + 8 + 16 + 36 + 8
    assert layout.MIN_FIGURE_WIDTH < width <= layout.MAX_FIGURE_WIDTH
    assert card_size("RoutineGroupFigure", routines, {"expanded": False}, "R")[1] == 30
    # The table card's own helpers say the same as the kind.
    table = {"name": "t", "columns": [{"name": "id", "type": "INT"}]}
    assert card_size("TableFigure", table) == (layout.figure_width(table), layout.figure_height(table))


# -----------------------------------------------------------------------------
# Unnamed foreign keys


def test_unnamed_foreign_keys_get_mariadb_s_names():
    outline = {
        "format": 1,
        "schemas": [
            {
                "name": None,
                "tables": [
                    {
                        "name": "c",
                        "columns": [],
                        "foreign_keys": [
                            {"name": None, "columns": ["a"]},
                            {"name": "named", "columns": ["b"]},
                            {"name": "c_ibfk_2", "columns": ["c"]},
                            {"name": None, "columns": ["d"]},
                        ],
                    }
                ],
            }
        ],
    }
    document = diagrams.schema_document_from_outline(outline, "s", ["s_next.sql"], "sha256:")
    keys = document["tables"][0]["foreign_keys"]
    assert [key["name"] for key in keys] == ["c_ibfk_1", "named", "c_ibfk_2", "c_ibfk_3"]
    assert [key.get("generated_name", False) for key in keys] == [True, False, False, True]


# -----------------------------------------------------------------------------
# lib/modeling.py


def diagram_document(project_path, name="Main"):
    return modeling.get_diagram_rows(project_path, name)[-1]["document"]


@needs_lsp
def test_a_new_diagram_shows_the_schema_laid_out(project):
    result = modeling.create_diagram(project, "Main")
    assert (result["figures"], result["connections"]) == (3, 3)

    document = diagram_document(project)
    diagrams.validate_diagram(document)
    figure = {f["table"]: f for f in document["figures"]}
    # customer above orders above invoice
    assert figure["customer"]["top"] < figure["orders"]["top"] < figure["invoice"]["top"]
    assert figure["customer"]["left"] >= layout.MARGIN
    assert figure["customer"]["height"] == 30 + 8 + 16 + 2 * 18 + 8
    assert all(f["color"] == diagrams.FIGURE_COLOR for f in document["figures"])
    connections = {(c["table"], c["foreignKey"]) for c in document["connections"]}
    assert connections == {
        ("orders", "fk_orders_customer"), ("orders", "fk_orders_reviewer"),
        ("invoice", "fk_invoice_order"),
    }
    assert document["width"] >= max(f["left"] + f["width"] for f in document["figures"])
    # The snapshot was made, as the script had none.
    assert modeling.list_diagrams(project)["schema_definition"]["current"] is True

    with pytest.raises(ValueError, match="already"):
        modeling.create_diagram(project, "Main")
    with pytest.raises(ValueError, match="no table `nothing`"):
        modeling.create_diagram(project, "Other", ["customer", "nothing"])


@needs_lsp
def test_a_diagram_of_some_tables(project):
    modeling.create_diagram(project, "Orders", ["ORDERS", "customer"])
    document = diagram_document(project, "Orders")
    assert sorted(f["table"] for f in document["figures"]) == ["customer", "orders"]
    assert len(document["connections"]) == 2


@needs_lsp
def test_tables_added_later_go_below_the_placed_ones(project):
    modeling.set_diagram(project, diagram(tables=("customer", "orders")))
    before = {f["table"]: (f["left"], f["top"]) for f in diagram_document(project)["figures"]}

    result = modeling.layout_diagram(project, "Main", ["invoice"])
    assert result["added"] == ["invoice"]
    assert result["placed"] == ["invoice"]
    # fk_orders_reviewer had no connection, fk_invoice_order is new
    assert result["connections_added"] == 2

    document = diagram_document(project)
    figures = {f["table"]: f for f in document["figures"]}
    assert {t: (figures[t]["left"], figures[t]["top"]) for t in before} == before
    lowest = max(f["top"] + f["height"] for t, f in figures.items() if t != "invoice")
    assert figures["invoice"]["top"] >= lowest + layout.ROW_GAP

    # Adding it again changes nothing.
    again = modeling.layout_diagram(project, "Main", ["invoice"])
    assert (again["added"], again["placed"], again["connections_added"], again["files"]) == (
        [], [], 0, [],
    )


@needs_lsp
def test_a_full_layout_moves_every_figure_but_those_on_a_layer(project):
    document = diagram(tables=("customer", "orders", "invoice"))
    for figure in document["figures"]:
        figure["top"] = 20.0  # all in one row: children beside their parents
    document["layers"] = [
        {"id": "l1", "name": "Billing", "left": 900.0, "top": 50.0, "width": 300.0,
         "height": 200.0, "color": "#F0F1FE", "figures": ["f-invoice"]},
    ]
    figure = next(f for f in document["figures"] if f["id"] == "f-invoice")
    figure.update(layer="l1", left=10.0, top=10.0)
    modeling.set_diagram(project, document)

    result = modeling.layout_diagram(project, "Main", keep_placed=False)
    assert sorted(result["placed"]) == ["customer", "orders"]
    figures = {f["table"]: f for f in diagram_document(project)["figures"]}
    assert (figures["invoice"]["left"], figures["invoice"]["top"]) == (10.0, 10.0)
    assert figures["customer"]["top"] < figures["orders"]["top"]
    # Below the layer
    assert figures["customer"]["top"] >= 50 + 200 + layout.ROW_GAP


@needs_lsp
def test_figures_without_a_position_are_placed(project):
    document = diagram(tables=("customer", "orders"))
    for figure in document["figures"]:
        del figure["left"], figure["top"], figure["width"]
    modeling.set_diagram(project, document)
    result = modeling.layout_diagram(project, "Main")
    assert sorted(result["placed"]) == ["customer", "orders"]
    figures = {f["table"]: f for f in diagram_document(project)["figures"]}
    assert figures["customer"]["top"] < figures["orders"]["top"]
    assert figures["customer"]["width"] >= layout.MIN_FIGURE_WIDTH


@needs_lsp
def test_a_snapshot_with_unnamed_foreign_keys_is_laid_out(project):
    """A snapshot written before foreign keys were named is reused as long
    as the script is unchanged; its foreign keys get their names then."""
    modeling.create_diagram(project, "Main", ["customer"])
    rows = modeling.read_diagram_rows(project)
    for table in rows[0]["document"]["tables"]:
        for foreign_key in table["foreign_keys"]:
            foreign_key["name"] = None
    management.set_section_sql_content(
        "160", diagrams.render_view_sql(rows), management.get_schema_development_file_path(project)
    )
    assert modeling.list_diagrams(project)["schema_definition"]["current"] is True

    result = modeling.layout_diagram(project, "Main", ["orders"])
    assert result["connections_added"] == 2
    names = {c["foreignKey"] for c in diagram_document(project)["connections"]}
    assert names == {"orders_ibfk_1", "orders_ibfk_2"}


@needs_lsp
def test_figures_without_an_id_are_placed(project):
    document = diagram(tables=("customer", "orders"))
    document["connections"] = []
    for figure in document["figures"]:
        del figure["id"], figure["left"], figure["top"]
    modeling.set_diagram(project, document)
    result = modeling.layout_diagram(project, "Main")
    assert sorted(result["placed"]) == ["customer", "orders"]
    figures = diagram_document(project)["figures"]
    assert len({figure["id"] for figure in figures}) == 2
    assert result["connections_added"] == 2


ROUTINES_SQL = """CREATE OR REPLACE VIEW `big_orders` AS SELECT `id` FROM `orders` WHERE `id` > 10;

DELIMITER %%

CREATE FUNCTION `order_count`() RETURNS INT
READS SQL DATA
BEGIN
    RETURN (SELECT COUNT(*) FROM `orders`);
END%%

CREATE PROCEDURE `add_order`(IN `customer` INT UNSIGNED)
BEGIN
    INSERT INTO `orders` (`customer_id`) VALUES (`customer`);
END%%

DELIMITER ;
"""


def with_routines(project_path, sql=ROUTINES_SQL):
    dev_file = management.get_schema_development_file_path(project_path)
    management.set_section_sql_content("150", sql, dev_file)


@needs_lsp
def test_views_and_routines_are_placed_without_connections(project):
    with_routines(project)
    result = modeling.create_diagram(
        project, "Main", views=["big_orders"], routines=["order_count", "ADD_ORDER"]
    )
    assert (result["figures"], result["connections"]) == (5, 3)
    document = diagram_document(project)
    by_type = figures_by_type(document)
    (view,) = by_type["ViewFigure"]
    assert (view["view"], view["name"], view["color"]) == (
        "big_orders", "big_orders", diagrams.VIEW_FIGURE_COLOR,
    )
    (group,) = by_type["RoutineGroupFigure"]
    assert group["routines"] == ["order_count", "add_order"]
    assert group["name"] == diagrams.ROUTINE_GROUP_NAME
    assert group["height"] == 30 + 8 + 16 + 2 * 18 + 8
    # Below the tables, which are connected; nothing connects to them.
    lowest_table = max(f["top"] + f["height"] for f in by_type["TableFigure"])
    assert view["top"] >= lowest_table and group["top"] >= lowest_table
    assert len(document["connections"]) == 3

    with pytest.raises(ValueError, match="no view `nothing`"):
        modeling.layout_diagram(project, "Main", views=["nothing"])
    with pytest.raises(ValueError, match="no routine `nothing`"):
        modeling.layout_diagram(project, "Main", routines=["nothing"])
    # Listed already: nothing to add; the view keeps its place.
    again = modeling.layout_diagram(project, "Main", views=["BIG_ORDERS"], routines=["order_count"])
    assert (again["added"], again["placed"]) == ([], [])

    # A routine added later joins the card, which grows; it stays in place.
    with_routines(project, ROUTINES_SQL + "\nCREATE FUNCTION `two`() RETURNS INT RETURN 2;\n")
    later = modeling.layout_diagram(project, "Main", routines=["two"])
    assert (later["added"], later["placed"]) == (["two"], [])
    (group2,) = [f for f in diagram_document(project)["figures"] if f["type"] == "RoutineGroupFigure"]
    assert group2["routines"] == ["order_count", "add_order", "two"]
    assert (group2["left"], group2["top"]) == (group["left"], group["top"])
    assert group2["height"] == group["height"] + 18

    # Objects the schema loses leave the diagram: the view, and the routines
    # of the card, which goes with the last one.
    with_routines(project, "SELECT 1;")
    modeling.update_schema_definition(project)
    assert [f["type"] for f in diagram_document(project)["figures"]] == ["TableFigure"] * 3


EVENTS_AND_SEQUENCES_SQL = """
DELIMITER %%

CREATE OR REPLACE TRIGGER `orders_check` BEFORE INSERT ON `orders` FOR EACH ROW
    SET NEW.`customer_id` = NEW.`customer_id`%%

CREATE OR REPLACE TRIGGER `orders_audit` AFTER UPDATE ON `orders` FOR EACH ROW
    SET @changed = 1%%

CREATE OR REPLACE EVENT `nightly` ON SCHEDULE EVERY 1 DAY DO
    DELETE FROM `orders` WHERE 0%%

CREATE OR REPLACE EVENT `hourly` ON SCHEDULE EVERY 1 HOUR DO SELECT 1%%

DELIMITER ;
"""


def with_sequence(project_path, sequence=True):
    """Section 140 with the tables, after a sequence unless sequence is False."""
    dev_file = management.get_schema_development_file_path(project_path)
    first = (
        "CREATE SEQUENCE `order_numbers` AS INT UNSIGNED START WITH 1000 INCREMENT BY 10;\n\n"
        if sequence else ""
    )
    management.set_section_sql_content("140", "\n" + first + TABLES_SQL + "\n", dev_file)


@needs_lsp
def test_events_sequences_and_a_tables_triggers_are_shown(project):
    modeling.create_diagram(project, "Before")
    with_routines(project, EVENTS_AND_SEQUENCES_SQL)
    with_sequence(project)
    modeling.create_diagram(project, "Main", events=["NIGHTLY"], sequences=["order_numbers"])
    by_type = figures_by_type(diagram_document(project))
    (group,) = by_type["EventGroupFigure"]
    assert (group["events"], group["name"], group["color"]) == (
        ["nightly"], diagrams.EVENT_GROUP_NAME, diagrams.EVENT_FIGURE_COLOR)
    assert group["height"] == 30 + 8 + 16 + 1 * 18 + 8
    (sequence,) = by_type["SequenceFigure"]
    assert (sequence["sequence"], sequence["name"], sequence["color"]) == (
        "order_numbers", "order_numbers", diagrams.SEQUENCE_FIGURE_COLOR)
    # A row for each of the type, start and increment written
    assert sequence["height"] == 30 + 8 + 16 + 3 * 18 + 8
    # The orders card has a section for its two triggers: a heading, two
    # rows and the gap to the columns.
    def heights(name):
        return {figure["table"]: figure["height"]
                for figure in figures_by_type(diagram_document(project, name))["TableFigure"]}
    before, after = heights("Before"), heights("Main")
    assert after["orders"] == before["orders"] + 16 + 2 * 18 + layout._SECTION_GAP
    assert after["customer"] == before["customer"]

    with pytest.raises(ValueError, match="no event `nothing`"):
        modeling.layout_diagram(project, "Main", events=["nothing"])
    with pytest.raises(ValueError, match="no sequence `nothing`"):
        modeling.layout_diagram(project, "Main", sequences=["nothing"])
    later = modeling.layout_diagram(project, "Main", events=["hourly"])
    assert (later["added"], later["placed"]) == (["hourly"], [])
    (group2,) = figures_by_type(diagram_document(project))["EventGroupFigure"]
    assert group2["events"] == ["nightly", "hourly"]
    assert group2["height"] == group["height"] + 18

    # Gone from the schema, gone from the diagram.
    with_routines(project, "SELECT 1;")
    with_sequence(project, sequence=False)
    modeling.update_schema_definition(project)
    assert [f["type"] for f in diagram_document(project)["figures"]] == ["TableFigure"] * 3


@needs_lsp
def test_a_changed_script_gets_a_new_snapshot_before_the_layout(project):
    modeling.create_diagram(project, "Main", ["customer"])
    management.set_section_sql_content(
        "140",
        TABLES_SQL + "\nCREATE TABLE `note` (`id` INT, `customer_id` INT UNSIGNED,"
        " FOREIGN KEY (`customer_id`) REFERENCES `customer` (`id`));\n",
        management.get_schema_development_file_path(project),
    )
    result = modeling.layout_diagram(project, "Main", ["note"])
    assert result["connections_added"] == 1
    connection = diagram_document(project)["connections"][0]
    assert connection["foreignKey"] == "note_ibfk_1"
    assert modeling.list_diagrams(project)["schema_definition"]["current"] is True
