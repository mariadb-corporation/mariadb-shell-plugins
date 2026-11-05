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

import pytest

from msm_plugin.lib import diagrams, lsp, management, modeling, source
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
                "views": [{"name": "msm_schema_diagrams"}, {"name": "v"}],
            },
            {"name": "other", "tables": [{"name": "elsewhere"}]},
        ],
    }
    document = diagrams.schema_document_from_outline(outline, "shop", ["shop_next.sql"], "x")
    assert document["format"] == diagrams.SCHEMA_FORMAT
    assert document["schema"] == "shop"
    assert document["source_hash"] == diagrams.source_hash("x")
    assert [table["name"] for table in document["tables"]] == ["orders"]
    assert [view["name"] for view in document["views"]] == ["v"]
    a, b = document["tables"][0]["foreign_keys"]
    assert (a["many"], a["mandatory"], a["referenced_mandatory"]) == (True, True, True)
    assert (b["many"], b["mandatory"], b["referenced_mandatory"]) == (False, True, False)
    with pytest.raises(ValueError, match="outline format 2"):
        diagrams.schema_document_from_outline({"format": 2}, "shop", [], "")


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


# -----------------------------------------------------------------------------
# Section 160 of a project


def test_a_new_project_has_an_empty_diagrams_section(project):
    assert "MSM Section 160: Schema Diagrams" in dev_script(project)
    assert modeling.read_diagram_rows(project) == []


def test_small_diagrams_stay_inline(project):
    modeling.set_diagram(project, diagram())
    assert "CREATE OR REPLACE SQL SECURITY INVOKER" in section_160(project)
    assert not os.path.exists(
        os.path.join(project, "development", "sections", "160_schema_diagrams.sql")
    )
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Main"]
    # The next section's banner keeps its blank lines
    assert "}';\n\n\n-- ####" in dev_script(project)

    modeling.set_diagram(project, {**diagram(), "width": 10.0})
    modeling.set_diagram(project, diagram("Second"))
    rows = modeling.read_diagram_rows(project)
    assert [row["name"] for row in rows] == ["Main", "Second"]
    assert rows[0]["document"]["width"] == 10.0

    modeling.remove_diagram(project, "Main")
    assert [row["name"] for row in modeling.read_diagram_rows(project)] == ["Second"]
    with pytest.raises(ValueError, match="no diagram `Main`"):
        modeling.remove_diagram(project, "Main")


def test_large_diagrams_move_into_a_breakout_file(project):
    settings_file = os.path.join(project, "msm.project.json")
    with open(settings_file) as f:
        settings = json.load(f)
    settings["maxInlineSectionSize"] = 200
    with open(settings_file, "w") as f:
        json.dump(settings, f)

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

    modeling.set_diagram(project, diagram())
    ids = list(management.get_script_sections(dev_script(project)))
    assert ids.index("160") == ids.index("150") + 1


def test_an_include_can_be_refused(project):
    settings_file = os.path.join(project, "msm.project.json")
    with open(settings_file) as f:
        settings = json.load(f)
    settings["maxInlineSectionSize"] = 0
    with open(settings_file, "w") as f:
        json.dump(settings, f)

    def refuse(path):
        raise ValueError(f"not allowed: {path}")

    with pytest.raises(ValueError, match="not allowed"):
        modeling.set_diagram(project, diagram(), check=refuse)
    modeling.set_diagram(project, diagram())
    with pytest.raises(ValueError, match="not allowed"):
        modeling.read_diagram_rows(project, check=refuse)


def test_a_bad_inline_size_setting_is_refused():
    for value in (-1, "16k", True):
        with pytest.raises(ValueError, match="maxInlineSectionSize"):
            modeling.get_max_inline_section_size({"maxInlineSectionSize": value})
    assert modeling.get_max_inline_section_size({}) == modeling.DEFAULT_MAX_INLINE_SECTION_SIZE


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

    with pytest.raises(ValueError, match="not allowed"):
        modeling.update_schema_definition(project, check=refuse)


@needs_lsp
def test_a_statement_that_cannot_be_read_stops_the_snapshot(project):
    management.set_section_sql_content(
        "140",
        TABLES_SQL + "\nCREATE TABLE broken (id INT,);",
        management.get_schema_development_file_path(project),
    )
    with pytest.raises(ValueError, match="could not be read"):
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
