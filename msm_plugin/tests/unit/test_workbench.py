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

"""Tests for the MySQL Workbench import: lib/workbench.py, lib/sections.py and
lib/modeling.create_project_from_workbench_model."""

import os
import sqlite3
import zipfile
from xml.sax.saxutils import escape

import pytest

from msm_plugin.lib import diagrams, management, modeling, projects, sections, workbench

# The project, as the diagram tests have it, and their mark for the binary.
from test_diagrams import figures_by_type, needs_lsp, project  # noqa: F401


# -----------------------------------------------------------------------------
# A model, written as Workbench writes document.mwb.xml


class Link(str):
    """A member that refers to another object by its id."""


def grt(key, value) -> str:
    attribute = f' key="{key}"' if key is not None else ""
    if isinstance(value, Link):
        return f'<link type="object"{attribute}>{escape(value)}</link>'
    if isinstance(value, bool) or isinstance(value, int):
        return f'<value type="int"{attribute}>{int(value)}</value>'
    if isinstance(value, float):
        return f'<value type="real"{attribute}>{value}</value>'
    if isinstance(value, str):
        return f'<value type="string"{attribute}>{escape(value)}</value>'
    if isinstance(value, list):
        return (f'<value type="list" content-type="object"{attribute}>'
                + "".join(grt(None, item) for item in value) + "</value>")
    if isinstance(value, dict) and "_struct" in value:
        members = "".join(grt(k, v) for k, v in value.items() if not k.startswith("_"))
        return (f'<value type="object" struct-name="{value["_struct"]}" id="{value["_id"]}"'
                f"{attribute}>{members}</value>")
    raise TypeError(value)


def obj(struct, object_id, **members):
    return {"_struct": struct, "_id": object_id, **members}


def column(object_id, name, simple=None, user=None, not_null=True, **more):
    members = dict(name=name, isNotNull=not_null, length=-1, precision=-1, scale=-1,
                   datatypeExplicitParams="", defaultValue="", defaultValueIsNull=0,
                   autoIncrement=0, generated=0, comment="", flags=[], checks=[])
    if simple:
        members["simpleType"] = Link("com.mysql.rdbms.mysql.datatype." + simple)
    if user:
        members["userType"] = Link(user)
    members.update(more)
    return obj("db.mysql.Column", object_id, **members)


def index(object_id, name, kind, column_ids, primary=False):
    return obj("db.mysql.Index", object_id, name=name, indexType=kind, isPrimary=primary,
               visible=1, comment="", algorithm="",
               columns=[obj("db.mysql.IndexColumn", f"{object_id}-{n}", descend=0,
                            columnLength=0, referencedColumn=Link(c))
                        for n, c in enumerate(column_ids)])


def foreign_key(object_id, name, owner, columns, table, referenced, delete="CASCADE"):
    return obj("db.mysql.ForeignKey", object_id, name=name, owner=Link(owner),
               columns=[Link(c) for c in columns], referencedTable=Link(table),
               referencedColumns=[Link(c) for c in referenced], deleteRule=delete,
               updateRule="NO ACTION", many=1, mandatory=1, referencedMandatory=1)


def shop_model():
    """customer <- orders -> employee <-> department (a cycle), a user type,
    a view, a routine, a trigger, a role, one diagram with a layer."""
    tables = [
        obj("db.mysql.Table", "{T-CUSTOMER}", name="customer", tableEngine="InnoDB",
            comment="The customers", isStub=0, partitionType="",
            columns=[column("{C-CUSTOMER-ID}", "id", user="{UDT-UUID}"),
                     column("{C-CUSTOMER-NAME}", "name", simple="varchar", length=100,
                            comment="It's the name")],
            indices=[index("{I-CUSTOMER-PK}", "PRIMARY", "PRIMARY", ["{C-CUSTOMER-ID}"], True)],
            foreignKeys=[], triggers=[]),
        obj("db.mysql.Table", "{T-ORDERS}", name="orders", tableEngine="InnoDB", isStub=0,
            partitionType="",
            columns=[column("{C-ORDERS-ID}", "id", simple="int", autoIncrement=1),
                     column("{C-ORDERS-CUSTOMER}", "customer_id", user="{UDT-UUID}"),
                     column("{C-ORDERS-STATE}", "state", simple="enum",
                            datatypeExplicitParams="('NEW', 'DONE')", defaultValue="'NEW'"),
                     column("{C-ORDERS-AT}", "placed_at", simple="timestamp_f", length=6,
                            not_null=False)],
            indices=[index("{I-ORDERS-PK}", "PRIMARY", "PRIMARY", ["{C-ORDERS-ID}"], True),
                     index("{I-ORDERS-FK}", "fk_orders_customer_idx", "INDEX",
                           ["{C-ORDERS-CUSTOMER}"])],
            foreignKeys=[foreign_key("{FK-ORDERS-CUSTOMER}", "fk_orders_customer", "{T-ORDERS}",
                                     ["{C-ORDERS-CUSTOMER}"], "{T-CUSTOMER}", ["{C-CUSTOMER-ID}"])],
            triggers=[obj("db.mysql.Trigger", "{TR-1}", name="orders_bi",
                          sqlDefinition="CREATE TRIGGER `orders_bi` BEFORE INSERT ON `orders` "
                                        "FOR EACH ROW BEGIN SET NEW.state = 'NEW'; END")]),
        obj("db.mysql.Table", "{T-EMPLOYEE}", name="employee", tableEngine="InnoDB", isStub=0,
            partitionType="",
            columns=[column("{C-EMP-ID}", "id", simple="int"),
                     column("{C-EMP-DEPT}", "department_id", simple="int", not_null=False)],
            indices=[index("{I-EMP-PK}", "PRIMARY", "PRIMARY", ["{C-EMP-ID}"], True)],
            foreignKeys=[foreign_key("{FK-EMP-DEPT}", "fk_employee_department", "{T-EMPLOYEE}",
                                     ["{C-EMP-DEPT}"], "{T-DEPT}", ["{C-DEPT-ID}"], "SET NULL")],
            triggers=[]),
        obj("db.mysql.Table", "{T-DEPT}", name="department", tableEngine="InnoDB", isStub=0,
            partitionType="",
            columns=[column("{C-DEPT-ID}", "id", simple="int"),
                     column("{C-DEPT-HEAD}", "head_id", simple="int", not_null=False)],
            indices=[index("{I-DEPT-PK}", "PRIMARY", "PRIMARY", ["{C-DEPT-ID}"], True)],
            foreignKeys=[foreign_key("{FK-DEPT-HEAD}", "fk_department_head", "{T-DEPT}",
                                     ["{C-DEPT-HEAD}"], "{T-EMPLOYEE}", ["{C-EMP-ID}"], "SET NULL")],
            triggers=[]),
    ]
    schema = obj(
        "db.mysql.Schema", "{SCHEMA}", name="shop", comment="", tables=tables,
        views=[obj("db.mysql.View", "{V-1}", name="big_orders",
                   sqlDefinition="CREATE VIEW `big_orders` AS SELECT * FROM `orders` WHERE id > 10")],
        routines=[obj("db.mysql.Routine", "{R-1}", name="order_count", routineType="function",
                      sqlDefinition="CREATE FUNCTION `order_count`() RETURNS INT\nREADS SQL DATA\n"
                                    "BEGIN\n  RETURN (SELECT COUNT(*) FROM `orders`);\nEND")],
        events=[],
        routineGroups=[obj("db.mysql.RoutineGroup", "{RG-1}", name="Counting",
                           routines=[Link("{R-1}")])],
    )
    roles = [obj("db.Role", "{ROLE-1}", name="shop_reader", childRoles=[], privileges=[
        obj("db.RolePrivilege", "{RP-1}", databaseObjectName="**.*", databaseObjectType="TABLE",
            privileges=["SELECT"]),
        obj("db.RolePrivilege", "{RP-2}", databaseObjectName="", databaseObjectType="TABLE",
            databaseObject=Link("{T-ORDERS}"), privileges=["UPDATE"]),
    ])]
    catalog = obj("db.mysql.Catalog", "{CATALOG}", name="default", schemata=[schema],
                  roles=roles, userDatatypes=[
                      obj("db.UserDatatype", "UDT-UUID", name="UUID", sqlDefinition="UUID",
                          flags="", actualType=Link("com.mysql.rdbms.mysql.datatype.binary"))])

    def figure(struct, object_id, left, top, layer, **members):
        """A figure of a diagram as Workbench writes one, its own members on top."""
        return obj(struct, object_id, layer=Link(layer), left=float(left), top=float(top),
                   **{"width": 160.0, "height": 120.0, "color": "", "expanded": 1,
                      "manualSizing": 0, "name": "", **members})

    def table_figure(object_id, table, left, top, layer):
        return figure("workbench.physical.TableFigure", object_id, left, top, layer,
                      table=Link(table), color="#98BFDA", columnsExpanded=1, indicesExpanded=0,
                      foreignKeysExpanded=0, triggersExpanded=0)

    figures = [table_figure("{F-CUSTOMER}", "{T-CUSTOMER}", 20, 20, "{L-ROOT}"),
               table_figure("{F-ORDERS}", "{T-ORDERS}", 20, 200, "{L-ROOT}"),
               table_figure("{F-EMP}", "{T-EMPLOYEE}", 10, 30, "{L-HR}"),
               figure("workbench.physical.ViewFigure", "{F-VIEW}", 400, 400, "{L-ROOT}",
                      view=Link("{V-1}"), width=120.0, height=46.0, color="#fede58"),
               figure("workbench.physical.RoutineGroupFigure", "{F-RG}", 600, 400, "{L-ROOT}",
                      routineGroup=Link("{RG-1}"), width=140.0, height=80.0),
               figure("workbench.model.NoteFigure", "{F-NOTE}", 10, 250, "{L-HR}",
                      width=137.0, height=25.0, color="#FEFDED", manualSizing=1, name="text1",
                      text="The staff", font="", textColor=""),
               figure("workbench.model.ImageFigure", "{F-IMAGE}", 800, 20, "{L-ROOT}",
                      width=64.0, height=64.0, manualSizing=1, keepAspectRatio=1, name="Logo",
                      filename="@images/logo.png"),
               figure("workbench.model.ImageFigure", "{F-LOST}", 900, 20, "{L-ROOT}",
                      width=64.0, height=64.0, manualSizing=1, keepAspectRatio=1, name="Lost",
                      filename="@images/missing.png")]
    diagram = obj(
        "workbench.physical.Diagram", "{DIAGRAM}", name="Shop", width=1000.0, height=800.0,
        zoom=1.0, description="",
        rootLayer=obj("workbench.physical.Layer", "{L-ROOT}", name="", figures=[]),
        layers=[obj("workbench.physical.Layer", "{L-HR}", name="HR", left=400.0, top=20.0,
                    width=300.0, height=300.0, color="#F0F1FE", description="",
                    figures=[Link("{F-EMP}")])],
        figures=figures,
        connections=[obj("workbench.physical.Connection", "{CONN-1}",
                         foreignKey=Link("{FK-ORDERS-CUSTOMER}"), startFigure=Link("{F-ORDERS}"),
                         endFigure=Link("{F-CUSTOMER}"), visible=1, caption="fk_orders_customer",
                         middleSegmentOffset=0.0, drawSplit=0)],
    )
    model = obj("workbench.physical.Model", "{PHYSICAL}", catalog=catalog, diagrams=[diagram])
    return obj("workbench.Document", "{DOCUMENT}", physicalModels=[model])


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)


def write_mwb(path, document, rows=None, scripts=("audit.sql",), images={"logo.png": PNG}):
    xml = ('<?xml version="1.0"?>\n<data grt_format="2.0" document_type="MySQL Workbench Model"'
           ' version="1.4.4">' + grt(None, document) + "</data>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("document.mwb.xml", xml)
        for number, script in enumerate(scripts):
            # Either folder, by the Workbench version.
            folder = "@scripts" if number % 2 == 0 else "@sqlscripts"
            archive.writestr(f"{folder}/{script}", "SELECT 1;\n")
        for name, data in images.items():
            archive.writestr(f"@images/{name}", data)
        if rows is not None:
            data = str(path) + ".db"
            connection = sqlite3.connect(data)
            for table_id, (columns, values) in rows.items():
                names = ", ".join(f'"{c}"' for c in ["_"] + columns)
                connection.execute(f'CREATE TABLE "{table_id}" ({names})')
                for number, value in enumerate(values):
                    connection.execute(
                        f'INSERT INTO "{table_id}" VALUES ({", ".join("?" * (len(columns) + 1))})',
                        [number] + list(value),
                    )
            connection.commit()
            connection.close()
            archive.write(data, "@db/data.db")
            os.remove(data)
    return str(path)


ROWS = {
    "{T-CUSTOMER}": (["{C-CUSTOMER-ID}", "{C-CUSTOMER-NAME}"],
                     [("\\func UUID_v7()", "Ann's shop"), ("31000000-0000-0000-0000-000000000000", "Bob")]),
    "{T-ORDERS}": (["{C-ORDERS-ID}", "{C-ORDERS-CUSTOMER}", "{C-ORDERS-STATE}", "{C-ORDERS-AT}"],
                   [("1", "31000000-0000-0000-0000-000000000000", "NEW", None)]),
    "{T-GONE}": (["{C-X}"], [("1",)]),
}


@pytest.fixture
def mwb(tmp_path):
    return write_mwb(tmp_path / "shop.mwb", shop_model(), ROWS)


# -----------------------------------------------------------------------------
# lib/workbench.py


def test_a_model_is_read(mwb):
    model = workbench.read_model(mwb)
    assert [schema["name"] for schema in model.schemas()] == ["shop"]
    assert model.schema()["name"] == "shop"
    assert model.schema("SHOP")["name"] == "shop"
    assert model.scripts == ["audit.sql"]
    with pytest.raises(ValueError, match="no schema `other`"):
        model.schema("other")


def test_scripts_in_either_folder_are_listed(tmp_path):
    path = write_mwb(tmp_path / "shop.mwb", shop_model(), scripts=("a.sql", "b.sql"))
    with zipfile.ZipFile(path) as archive:
        assert {n for n in archive.namelist() if "scripts" in n} == {
            "@scripts/a.sql", "@sqlscripts/b.sql",
        }
    assert workbench.read_model(path).scripts == ["a.sql", "b.sql"]


def test_what_is_not_a_model_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not a MySQL Workbench model"):
        workbench.read_model(str(tmp_path / "missing.mwb"))
    (tmp_path / "x.mwb").write_text("not a zip")
    with pytest.raises(ValueError, match="not a MySQL Workbench model"):
        workbench.read_model(str(tmp_path / "x.mwb"))
    with zipfile.ZipFile(tmp_path / "y.mwb", "w") as archive:
        archive.writestr("other.xml", "<data/>")
    with pytest.raises(ValueError, match="has no document.mwb.xml"):
        workbench.read_model(str(tmp_path / "y.mwb"))


def test_the_schema_becomes_sql(mwb):
    model = workbench.read_model(mwb)
    result = workbench.schema_sql(model, model.schema())
    tables = result.tables
    # A table comes after the tables it refers to.
    assert tables.index("`customer` (") < tables.index("`orders` (")
    # The user type is replaced by its definition, Workbench's types as it writes them.
    assert "`id` UUID NOT NULL" in tables
    assert "`name` VARCHAR(100) NOT NULL COMMENT 'It\\'s the name'" in tables
    assert "`state` ENUM('NEW', 'DONE') NOT NULL DEFAULT 'NEW'" in tables
    assert "`placed_at` TIMESTAMP(6) NULL" in tables
    assert "`id` INT NOT NULL AUTO_INCREMENT" in tables
    assert "PRIMARY KEY (`id`)" in tables
    assert "INDEX `fk_orders_customer_idx` (`customer_id` ASC)" in tables
    assert "REFERENCES `customer` (`id`)\n      ON DELETE CASCADE" in tables
    assert "COMMENT = 'The customers'" in tables
    # The cycle: one of its foreign keys is added once both tables exist.
    assert result.table_additions.count("ALTER TABLE") == 1
    assert "ADD CONSTRAINT" in result.table_additions
    # Rows: an expression as it is, numbers unquoted, NULL, the stale table left out.
    assert "VALUES (UUID_v7(), 'Ann\\'s shop'),\n    ('31000000-0000-0000-0000-000000000000', 'Bob');" \
        in result.inserts
    assert (
        "INSERT INTO `orders` (`id`, `customer_id`, `state`, `placed_at`)\n"
        "VALUES (1, '31000000-0000-0000-0000-000000000000', 'NEW', NULL);"
    ) in result.inserts
    assert any("belong to no table" in warning for warning in result.warnings)
    # Section 150 statements, to be run again.
    assert "CREATE OR REPLACE VIEW `big_orders`" in result.views
    assert "DROP FUNCTION IF EXISTS `order_count`%%\nCREATE FUNCTION" in result.routines
    assert "DROP TRIGGER IF EXISTS `orders_bi`%%" in result.triggers
    assert result.routines.startswith("-- PROCEDUREs and FUNCTIONs\n\nDELIMITER %%")
    assert result.routines.endswith("DELIMITER ;")
    assert result.events is None
    assert "CREATE ROLE IF NOT EXISTS `shop_reader`;" in result.roles
    assert "GRANT SELECT ON * TO `shop_reader`;" in result.roles
    assert "GRANT UPDATE ON `orders` TO `shop_reader`;" in result.roles
    assert result.counts == {"tables": 4, "views": 1, "routines": 1, "triggers": 1,
                             "events": 0, "roles": 1, "inserted_rows": 3}


def test_a_foreign_key_to_a_table_outside_the_schema_is_left_out(tmp_path):
    document = shop_model()
    catalog = document["physicalModels"][0]["catalog"]
    schema = catalog["schemata"][0]
    stub = obj("db.mysql.Table", "{T-STUB}", name="stub", isStub=1, columns=[
        column("{C-STUB-ID}", "id", simple="int")], indices=[], foreignKeys=[], triggers=[])
    schema["tables"].append(stub)
    other = obj("db.mysql.Schema", "{S2}", name="crm", tables=[
        obj("db.mysql.Table", "{T-CRM}", name="account", isStub=0, columns=[
            column("{C-CRM-ID}", "id", simple="int")], indices=[], foreignKeys=[], triggers=[])],
        views=[], routines=[], events=[])
    catalog["schemata"].append(other)
    orders = schema["tables"][1]
    orders["foreignKeys"] += [
        foreign_key("{FK-STUB}", "fk_orders_stub", "{T-ORDERS}", ["{C-ORDERS-ID}"], "{T-STUB}",
                    ["{C-STUB-ID}"]),
        foreign_key("{FK-CRM}", "fk_orders_account", "{T-ORDERS}", ["{C-ORDERS-ID}"], "{T-CRM}",
                    ["{C-CRM-ID}"]),
    ]
    # Privileges on the whole schema, as the model names it.
    catalog["roles"][0]["privileges"].append(
        obj("db.RolePrivilege", "{RP-3}", databaseObjectName="shop.*", databaseObjectType="TABLE",
            privileges=["INSERT"]))

    model = workbench.read_model(write_mwb(tmp_path / "shop.mwb", document))
    result = workbench.schema_sql(model, model.schema("shop"))
    assert "fk_orders_stub" not in result.tables
    assert "fk_orders_account" not in result.tables
    assert "REFERENCES `customer`" in result.tables
    assert sorted(w for w in result.warnings if "not a table of the schema" in w) == [
        "The foreign key `fk_orders_account` of `orders` refers to `account`, which is not a "
        "table of the schema; it was left out.",
        "The foreign key `fk_orders_stub` of `orders` refers to `stub`, which is not a table "
        "of the schema; it was left out.",
    ]
    assert "GRANT INSERT ON * TO `shop_reader`;" in result.roles


def test_a_large_image_is_reported_not_embedded(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "MAX_IMAGE_SIZE", 8 * 1024)
    big = {"logo.png": PNG + b"\x00" * (8 * 1024)}
    model = workbench.read_model(write_mwb(tmp_path / "shop.mwb", shop_model(), images=big))
    assert model.images["@images/logo.png"] == len(big["logo.png"])
    _, warnings = workbench.diagram_documents(model, model.schema())
    assert ("The image `@images/logo.png` of the diagram `Shop` was left out: it is larger "
            "than 8 KB.") in warnings


def test_a_diagram_s_pictures_are_capped_together(tmp_path, monkeypatch):
    # Room for one logo, and two figures showing it: the second is reported.
    monkeypatch.setattr(workbench, "MAX_DIAGRAM_IMAGES_SIZE", len(PNG) + 1)
    document = shop_model()
    diagram = document["physicalModels"][0]["diagrams"][0]
    logo = next(f for f in diagram["figures"] if f["_id"] == "{F-IMAGE}")
    diagram["figures"].append(dict(logo, _id="{F-IMAGE-2}", name="Logo again"))
    model = workbench.read_model(write_mwb(tmp_path / "shop.mwb", document))
    documents, warnings = workbench.diagram_documents(model, model.schema())
    images = [f["name"] for f in documents[0]["figures"] if f["type"] == "ImageFigure"]
    assert images == ["Logo"]
    assert [w for w in warnings if w.startswith("The image `@images/logo.png`")] == [
        "The image `@images/logo.png` of the diagram `Shop` was left out: the diagram's "
        f"pictures would be larger than {workbench.MAX_DIAGRAM_IMAGES_SIZE // 2 ** 20} MB together."
    ]


def test_diagram_names_are_made_unique(tmp_path):
    document = shop_model()
    physical = document["physicalModels"][0]
    first = physical["diagrams"][0]
    first["name"] = "schema_definition"
    for number, name in enumerate(("Shop", "Shop", "Shop (2)")):
        copy = dict(first, _id=f"{{DIAGRAM-{number}}}", name=name, figures=[], layers=[],
                    connections=[])
        physical["diagrams"].append(copy)
    model = workbench.read_model(write_mwb(tmp_path / "shop.mwb", document))
    documents, _ = workbench.diagram_documents(model, model.schema())
    names = [d["name"] for d in documents]
    assert names[0] == "schema_definition (2)"
    assert len(set(names)) == len(names) == 4
    assert names[1:3] == ["Shop", "Shop (2)"]


def test_a_list_member_that_is_a_link_is_followed(tmp_path):
    # As Workbench writes a routine in a routine group: the object under the
    # group (routineGroups comes first alphabetically), a link in routines.
    document = shop_model()
    schema = document["physicalModels"][0]["catalog"]["schemata"][0]
    routine = schema["routines"][0]
    members = {}
    for key, value in schema.items():
        if key == "routineGroups":
            continue
        if key == "routines":
            members["routineGroups"] = [obj("db.mysql.RoutineGroup", "{RG-1}", name="all",
                                            routines=[routine])]
            value = [Link(routine["_id"]), Link("{R-GONE}")]
        members[key] = value
    schema.clear()
    schema.update(members)
    model = workbench.read_model(write_mwb(tmp_path / "shop.mwb", document))
    result = workbench.schema_sql(model, model.schema())
    assert "CREATE FUNCTION `order_count`" in result.routines
    assert result.routines.count("CREATE FUNCTION") == 1


def test_a_statement_s_kind_and_an_event_saved_as_a_routine():
    assert workbench.statement_kind("CREATE DEFINER=`a`@`%` PROCEDURE p() BEGIN END") == "PROCEDURE"
    assert workbench.statement_kind("DELIMITER $$\nCREATE EVENT e ON SCHEDULE EVERY 1 DAY DO "
                                    "DELETE FROM t$$\nDELIMITER ;") == "EVENT"
    assert workbench.statement_kind("CREATE OR REPLACE ALGORITHM=MERGE VIEW v AS SELECT 1") == "VIEW"
    assert workbench.statement_kind("SELECT 1") is None
    # As Workbench saves a view: a comment first.
    assert workbench.statement_kind("--\n-- View structure for view `v`\n--\n\nCREATE VIEW v \n"
                                    "AS \nSELECT 1") == "VIEW"
    assert workbench._idempotent("VIEW", "v", "/* x */ -- y\nCREATE VIEW v AS SELECT 1;") == (
        "CREATE OR REPLACE VIEW v AS SELECT 1%%")


def test_the_diagrams_become_documents(mwb):
    model = workbench.read_model(mwb)
    documents, warnings = workbench.diagram_documents(model, model.schema())
    assert warnings == [
        "The image `@images/missing.png` of the diagram `Shop` was left out: the model file "
        "does not hold it.",
    ]
    (document,) = documents
    diagrams.validate_diagram(document)
    assert document["name"] == "Shop"
    by_type = figures_by_type(document)
    figures = {figure["table"]: figure for figure in by_type["TableFigure"]}
    assert set(figures) == {"customer", "orders", "employee"}
    (view,) = by_type["ViewFigure"]
    assert (view["view"], view["name"], view["color"], view["left"]) == (
        "big_orders", "big_orders", "#fede58", 400.0,
    )
    (group,) = by_type["RoutineGroupFigure"]
    assert (group["name"], group["routines"], group["color"]) == (
        "Counting", ["order_count"], "#98D8A5",
    )
    (note,) = by_type["NoteFigure"]
    assert (note["text"], note["name"], note["layer"], note["manualSizing"]) == (
        "The staff", "text1", "{L-HR}", True,
    )
    assert "font" not in note
    (image,) = by_type["ImageFigure"]
    assert image["image"].startswith("data:image/png;base64,iVBORw0KGgo")
    assert (image["name"], image["keepAspectRatio"]) == ("Logo", True)
    assert "layer" not in figures["customer"]
    assert figures["employee"]["layer"] == "{L-HR}"
    assert (figures["orders"]["left"], figures["orders"]["top"]) == (20.0, 200.0)
    assert document["layers"][0]["figures"] == ["{F-EMP}"]
    (connection,) = document["connections"]
    assert connection == {
        "id": "{CONN-1}", "foreignKey": "fk_orders_customer", "table": "orders",
        "startFigure": "{F-ORDERS}", "endFigure": "{F-CUSTOMER}", "visible": True,
        "caption": "fk_orders_customer", "middleSegmentOffset": 0.0, "drawSplit": False,
    }


# -----------------------------------------------------------------------------
# lib/sections.py


def test_a_small_section_stays_inline(project):
    files = sections.write_section_parts(project, "170", [
        sections.Part("170_roles.sql", "ROLEs", "CREATE ROLE IF NOT EXISTS r;"),
        sections.Part("170_more.sql", "More", None),
    ])
    dev_file = management.get_schema_development_file_path(project)
    assert files == [dev_file]
    body = management.get_file_sections(dev_file)["170"]["sql_content"]
    assert body.strip() == "CREATE ROLE IF NOT EXISTS r;"
    assert sections.write_section_parts(project, "170", [sections.Part("x.sql", "x", "")]) == []


def test_a_large_section_goes_into_breakout_files(project):
    big = "\n".join(f"CREATE TABLE t{n} (id INT);" for n in range(800))
    files = sections.write_section_parts(project, "140", [
        sections.Part("140-10_tables.sql", "TABLEs", big),
        sections.Part("140-30_inserts.sql", "INSERTs", "INSERT INTO t1 VALUES (1);"),
    ])
    development = os.path.join(project, "development")
    assert [os.path.relpath(f, development) for f in files] == [
        "shop_next.sql", "sections/140-10_tables.sql", "sections/140-30_inserts.sql",
    ]
    dev_file = management.get_schema_development_file_path(project)
    body = management.get_file_sections(dev_file)["140"]["sql_content"]
    assert "SOURCE './sections/140-10_tables.sql'[ln" in body
    assert "SOURCE './sections/140-30_inserts.sql'[ln" in body
    with open(dev_file) as f:
        script = f.read()
    from msm_plugin.lib import source
    expanded = source.expand_source_directives(script, development)[0]
    assert big in expanded
    assert "Copyright" not in expanded[expanded.index("CREATE TABLE t0"):expanded.index("INSERT INTO t1")]


# -----------------------------------------------------------------------------
# The import


@needs_lsp
def test_a_project_is_created_from_a_model(mwb, tmp_path):
    result = modeling.create_project_from_workbench_model(
        mwb, str(tmp_path / "projects"), copyright_holder="Example Ltd.",
        enforce_target_path=True,
    )
    path = result["project_path"]
    assert os.path.basename(path) == "shop.msm.project"
    assert result["schema"] == "shop"
    assert (result["tables"], result["views"], result["routines"], result["triggers"],
            result["roles"], result["inserted_rows"]) == (4, 1, 1, 1, 1, 3)
    assert result["diagrams"] == ["Shop"]
    assert result["scripts_not_imported"] == ["audit.sql"]

    rows = modeling.read_diagram_rows(path)
    assert [row["name"] for row in rows] == ["schema_definition", "Shop"]
    snapshot = rows[0]["document"]
    assert {t["name"] for t in snapshot["tables"]} == {"customer", "orders", "employee", "department"}
    assert snapshot["notes"] == [] or all(note.get("applied", True) for note in snapshot["notes"])
    tables = {t["name"]: t for t in snapshot["tables"]}
    # The cycle's foreign key added by ALTER TABLE is in the snapshot too.
    assert {fk["name"] for t in ("employee", "department") for fk in tables[t]["foreign_keys"]} == {
        "fk_employee_department", "fk_department_head",
    }
    assert [r["name"] for r in snapshot["routines"]] == ["order_count"]
    assert [v["name"] for v in snapshot["views"] if v["name"] != "msm_schema_version"] == ["big_orders"]
    assert len(rows[1]["document"]["connections"]) == 1
    assert modeling.list_diagrams(path)["schema_definition"]["current"] is True


@needs_lsp
def test_a_failed_import_leaves_no_project(tmp_path):
    document = shop_model()
    schema = document["physicalModels"][0]["catalog"]["schemata"][0]
    schema["views"][0]["sqlDefinition"] = "CREATE VIEW `broken` AS SELECT FROM WHERE"
    model = write_mwb(tmp_path / "broken.mwb", document)
    with pytest.raises(ValueError, match="not applied"):
        modeling.create_project_from_workbench_model(model, str(tmp_path), copyright_holder="X")
    assert not os.path.exists(tmp_path / "shop.msm.project")


def test_a_model_with_several_schemas_needs_one_named(tmp_path):
    document = shop_model()
    catalog = document["physicalModels"][0]["catalog"]
    catalog["schemata"].append(obj("db.mysql.Schema", "{S2}", name="other", tables=[],
                                   views=[], routines=[], events=[]))
    model = write_mwb(tmp_path / "two.mwb", document)
    with pytest.raises(ValueError, match="name the one to import"):
        modeling.create_project_from_workbench_model(model, str(tmp_path), copyright_holder="X")
    assert os.listdir(tmp_path) == ["two.mwb"]


@needs_lsp
def test_an_imported_project_deploys(sandbox_session, tmp_path, monkeypatch):
    """The SQL written for the model runs on the server, from breakout files."""
    from msm_plugin import lib as msm_lib
    from msm_plugin.management import deploy_schema, generate_deployment_script, prepare_release

    schema_name = "msm_wb_import_test"
    document = shop_model()
    document["physicalModels"][0]["catalog"]["schemata"][0]["name"] = schema_name
    model = write_mwb(tmp_path / "shop.mwb", document, ROWS)
    # Every section into its breakout files.
    monkeypatch.setattr(sections, "DEFAULT_MAX_INLINE_SECTION_SIZE", 10)
    result = modeling.create_project_from_workbench_model(
        model, str(tmp_path), copyright_holder="Example Ltd."
    )
    path = result["project_path"]
    breakouts = sorted(os.listdir(os.path.join(path, "development", "sections")))
    assert breakouts == [
        "140-10_tables.sql", "140-20_table_additions.sql", "140-30_inserts.sql",
        "150-10_views.sql", "150-20_procedures_functions.sql", "150-30_triggers.sql",
        "160_schema_diagrams.sql", "170_roles.sql",
    ]

    prepare_release(schema_project_path=path, version="1.0.0", next_version="1.1.0")
    generate_deployment_script(schema_project_path=path, version="1.0.0")

    def query(sql):
        return msm_lib.core.MsmDbExec(sql).exec(sandbox_session).items

    sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")
    try:
        deploy_schema(schema_project_path=path, version="1.0.0")
        tables = [row["TABLE_NAME"] for row in query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema_name}' AND TABLE_TYPE = 'BASE TABLE' ORDER BY 1"
        )]
        assert tables == ["customer", "department", "employee", "orders"]
        constraints = [row["CONSTRAINT_NAME"] for row in query(
            "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
            f"WHERE CONSTRAINT_SCHEMA = '{schema_name}' ORDER BY 1"
        )]
        assert constraints == ["fk_department_head", "fk_employee_department", "fk_orders_customer"]
        assert query(f"SELECT COUNT(*) AS n FROM `{schema_name}`.`customer`")[0]["n"] == 2
        assert query(f"SELECT `{schema_name}`.`order_count`() AS n")[0]["n"] == 1
        assert query(
            "SELECT COUNT(*) AS n FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA = '{schema_name}'"
        )[0]["n"] == 1
        assert [row["name"] for row in query(
            f"SELECT `name` FROM `{schema_name}`.`msm_schema_diagrams`"
        )] == ["schema_definition", "Shop"]
        assert query(
            "SELECT COUNT(*) AS n FROM mysql.roles_mapping WHERE Role = 'shop_reader'"
        )[0]["n"] >= 1
    finally:
        sandbox_session.run_sql(f"DROP SCHEMA IF EXISTS `{schema_name}`")
        sandbox_session.run_sql("DROP ROLE IF EXISTS shop_reader")
