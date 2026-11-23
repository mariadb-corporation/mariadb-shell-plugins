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

"""Tests for the REST SDK of a project's service: lib/rest_sdk_data.py and
lib/rest_sdk.py.

The tests that outline a project run where a mariadb-lsp binary tells the
details of REST objects (outline features rest_object_details and
rest_settings): MSM_TEST_LSP names one, else the binaries msm would find are
tried. The rest need nothing.
"""

import json
import os

import pytest

from msm_plugin.lib import lsp, management, rest_sdk, rest_sdk_data
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib.management import create_schema_project_folder
from msm_plugin.tests.unit import helpers


LSP = helpers.find_test_lsp(
    lambda binary: rest_sdk_data.SDK_OUTLINE_FEATURES <= lsp.outline_features(binary)
)
needs_lsp = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary that tells the details of REST objects"
)

TABLES_SQL = """CREATE TABLE customer (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(45) NOT NULL,
    email VARCHAR(100) UNIQUE,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id INT NOT NULL PRIMARY KEY,
    customer_id INT NOT NULL,
    total DECIMAL(10,2),
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customer (id)
);

CREATE TABLE customer_profile (
    customer_id INT NOT NULL PRIMARY KEY,
    bio TEXT,
    CONSTRAINT fk_profile_customer FOREIGN KEY (customer_id) REFERENCES customer (id)
);"""

ROUTINES_SQL = """DELIMITER %%
CREATE OR REPLACE FUNCTION order_count(c INT) RETURNS INT
RETURN (SELECT COUNT(*) FROM orders WHERE customer_id = c)%%

CREATE OR REPLACE PROCEDURE find_orders(IN c INT, OUT n INT)
BEGIN
    SELECT id, total FROM orders WHERE customer_id = c;
END%%
DELIMITER ;"""

REST_SQL = """CREATE OR REPLACE REST SERVICE /svc AUTHENTICATION PATH '/login';

CREATE OR REPLACE REST SCHEMA /shop ON SERVICE /svc FROM `shop`;

CREATE OR REPLACE REST VIEW /customers ON SERVICE /svc SCHEMA /shop AS `shop`.`customer`
    CLASS Customer @INSERT @UPDATE @DELETE {
    id: id @KEY @SORTABLE,
    firstName: first_name @NOFILTERING,
    email: email,
    orders: shop.orders @UNNEST {
        total: total
    },
    profile: shop.customer_profile {
        bio: bio
    }
} AUTHENTICATION NOT REQUIRED;

CREATE OR REPLACE REST VIEW /orders ON SERVICE /svc SCHEMA /shop AS `shop`.`orders`;

CREATE OR REPLACE REST FUNCTION /orderCount ON SERVICE /svc SCHEMA /shop AS `shop`.`order_count`;

CREATE OR REPLACE REST PROCEDURE /findOrders ON SERVICE /svc SCHEMA /shop AS `shop`.`find_orders`
    RESULT OrderRow {
        id: id @DATATYPE("int"),
        total: total @DATATYPE("decimal(10,2)")
    };

CREATE OR REPLACE REST VIEW /missing ON SERVICE /svc SCHEMA /shop AS `shop`.`no_such_table`;

CREATE OR REPLACE REST VIEW /hidden ON SERVICE /svc SCHEMA /shop AS `shop`.`orders` DISABLED;

CREATE OR REPLACE REST SERVICE /off DISABLED;"""


# -----------------------------------------------------------------------------
# Names, as MariaDB Shell makes them


@pytest.mark.parametrize(
    "name, expected",
    [
        ("first_name", "firstName"),
        ("ID", "id"),
        ("customer_ID", "customerId"),
        ("lastUpdate", "lastUpdate"),
        ("order items", "orderItems"),
        ("$", ""),
        ("préfixe_été", "préfixeété"),
    ],
)
def test_names_are_camel_cased_as_the_shell_does(name, expected):
    assert rest_sdk_data.name_to_camel_case(name) == expected


def test_a_name_without_letters_becomes_field():
    assert rest_sdk_data.field_name("$") == "field"


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/svc/shop/customers", "SvcShopCustomers"),
        ("/svc/shop/order_items", "SvcShopOrderItems"),
        ("mike@/svc/shop/x2", "MikeSvcShopX2"),
    ],
)
def test_paths_become_class_names_as_the_shell_makes_them(path, expected):
    assert rest_sdk_data.path_to_pascal_case(path) == expected


# -----------------------------------------------------------------------------
# Tables, as table_columns_with_references describes them


def _catalog(tables, column_types=True):
    return rest_sdk_data.Catalog({"tables": tables}, "shop", column_types)


def _table(name, columns, indexes=(), foreign_keys=()):
    return {"name": name, "columns": list(columns), "indexes": list(indexes),
            "foreign_keys": list(foreign_keys)}


def _column(name, type_, nullable=True, **members):
    return {"name": name, "type": type_, "nullable": nullable, **members}


def _fk(name, columns, table, ref_columns):
    return {"name": name, "columns": columns,
            "references": {"schema": None, "table": table, "columns": ref_columns}}


def test_columns_take_the_servers_type_and_keys():
    catalog = _catalog([_table(
        "t",
        [_column("id", "SERIAL", False, column_type="bigint(20) unsigned", auto_increment=True),
         _column("flag", "BOOLEAN", column_type="tinyint(1)"),
         _column("code", "CHAR(3)", False, column_type="char(3)")],
        indexes=[{"kind": "primary", "columns": ["id"]}, {"kind": "unique", "columns": ["code"]}],
    )])
    by_name = {c["name"]: c["db_column"] for c in catalog.table_columns(None, "t")}
    assert by_name["id"]["datatype"] == "bigint(20) unsigned"
    assert by_name["id"]["is_primary"] and not by_name["id"]["is_unique"]
    assert by_name["id"]["id_generation"] == "auto_inc"
    assert by_name["flag"]["datatype"] == "tinyint(1)" and not by_name["flag"]["not_null"]
    assert by_name["flag"]["column_default"] == "NULL"
    assert by_name["code"]["is_unique"] and by_name["code"]["column_default"] is None


def test_without_column_types_the_type_is_taken_as_written():
    catalog = _catalog([_table("t", [_column("flag", "BOOLEAN", column_type="tinyint(1)")])],
                       column_types=False)
    assert catalog.table_columns(None, "t")[0]["db_column"]["datatype"] == "boolean"


def test_a_unique_not_null_key_is_the_primary_key_of_a_table_without_one():
    catalog = _catalog([_table(
        "t", [_column("a", "INT", False), _column("b", "INT")],
        indexes=[{"kind": "unique", "columns": ["a"]}],
    )])
    (a, b) = catalog.table_columns(None, "t")
    assert a["db_column"]["is_primary"] and not a["db_column"]["is_unique"]
    assert not b["db_column"]["is_primary"]


def test_references_are_n_to_1_1_to_1_and_1_to_n():
    parent = _table("parent", [_column("id", "INT", False)],
                    indexes=[{"kind": "primary", "columns": ["id"]}])
    child = _table("child", [_column("id", "INT", False), _column("parent_id", "INT", False)],
                   indexes=[{"kind": "primary", "columns": ["id"]}],
                   foreign_keys=[_fk("fk_child", ["parent_id"], "parent", ["id"])])
    detail = _table("detail", [_column("parent_id", "INT", False)],
                    indexes=[{"kind": "primary", "columns": ["parent_id"]}],
                    foreign_keys=[_fk("fk_detail", ["parent_id"], "parent", ["id"])])
    catalog = _catalog([parent, child, detail])

    references = {r["name"]: r["reference_mapping"]
                  for r in catalog.table_columns(None, "parent") if r["reference_mapping"]}
    assert references["child"]["kind"] == "1:n" and references["child"]["to_many"]
    assert references["child"]["column_mapping"] == [{"base": "id", "ref": "parent_id"}]
    # The detail's primary key is the parent's: one detail per parent
    assert references["detail"]["kind"] == "1:1" and not references["detail"]["to_many"]

    (up,) = [r for r in catalog.table_columns(None, "child") if r["reference_mapping"]]
    assert up["name"] == "parent" and up["position"] == 102
    assert up["reference_mapping"]["kind"] == "n:1"
    assert up["reference_mapping"]["constraint"] == "shop.fk_child"


def test_a_table_of_another_schema_is_not_known():
    assert _catalog([_table("t", [])]).table_columns("other", "t") is None


# -----------------------------------------------------------------------------
# The service document, from a project's outline


def make_project(tmp_path, rest_sql: str = REST_SQL) -> str:
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.",
        license="MIT",
    )
    dev_file = management.get_schema_development_file_path(path)
    sections = management.get_file_sections(dev_file)
    sections_lib.set_section_body(sections["140"], TABLES_SQL)
    sections_lib.set_section_body(sections["150"], ROUTINES_SQL)
    sections_lib.set_section_body(sections["180"], rest_sql)
    management.write_sections_to_file(dev_file, sections, overwrite_existing=True)
    return path


@pytest.fixture
def project(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    return make_project(projects)


def _objects(service_res) -> dict:
    (schema,) = service_res["rest_schemas"]
    return {o["request_path"]: o for o in schema["rest_objects"]}


def _fields(rest_object, kind="RESULT") -> dict:
    """A data mapping's fields by name; a name a nested table repeats is the
    top level's (the levels come in order)."""
    (mapping,) = [m for m in rest_object["data_mappings"] if m["kind"] == kind]
    fields = {}
    for field in mapping["fields"]:
        fields.setdefault(field["name"], field)
    return fields


@needs_lsp
def test_the_service_document_is_the_metadatas(project):
    node, service_res, warnings = rest_sdk.service_data(project, "/SVC", LSP)
    assert node["path"] == "/svc"
    assert service_res["url_context_root"] == "/svc"
    assert service_res["auth_path"] == "/login"

    objects = _objects(service_res)
    # A disabled object is not in the SDK; one on a missing table is left out
    assert set(objects) == {"/customers", "/orders", "/orderCount", "/findOrders"}
    assert any("/svc/shop/missing" in w and "no_such_table" in w for w in warnings)

    customers = objects["/customers"]
    assert customers["object_type"] == "TABLE"
    assert customers["crud_operations"] == "READ,CREATE,UPDATE,DELETE"
    assert customers["requires_auth"] is False
    (mapping,) = customers["data_mappings"]
    assert mapping["name"] == "Customer"
    fields = _fields(customers)
    assert fields["id"]["db_column"]["is_primary"] and fields["id"]["allow_sorting"]
    assert fields["firstName"]["allow_filtering"] is False
    assert fields["firstName"]["db_column"]["name"] == "first_name"
    # Columns the mapping leaves out are there, disabled
    assert fields["active"]["enabled"] is False
    # 1:n unnested to its one column, 1:1 nested
    orders = fields["orders"]["data_mapping_reference"]
    assert orders["reference_mapping"]["kind"] == "1:n" and orders["unnest"]
    assert orders["reduce_to_value_of_field_id"] == fields["total"]["id"]
    assert fields["total"]["lev"] == 2
    profile = fields["profile"]["data_mapping_reference"]
    assert profile["reference_mapping"]["kind"] == "1:1" and not profile["unnest"]

    # Without a mapping every column is exposed, the n:1 reference is not
    order_fields = _fields(objects["/orders"])
    assert order_fields["customerId"]["enabled"] and not order_fields["customer"]["enabled"]
    assert objects["/orders"]["data_mappings"][0]["name"] == "SvcShopOrders"
    assert objects["/orders"]["requires_auth"] is True

    count = objects["/orderCount"]
    assert count["crud_operations"] == "CREATE"
    assert [m["name"] for m in count["data_mappings"]] == [
        "SvcShopOrderCountParams", "SvcShopOrderCount"
    ]
    assert _fields(count, "PARAMETERS")["c"]["db_column"]["in"]
    assert _fields(count)["result"]["db_column"]["datatype"].startswith("int")

    find = objects["/findOrders"]
    params = _fields(find, "PARAMETERS")
    assert params["c"]["db_column"]["in"] and not params["c"]["db_column"]["out"]
    assert params["n"]["db_column"]["out"]
    assert _fields(find)["total"]["db_column"]["datatype"] == "decimal(10,2)"
    assert [m["name"] for m in find["data_mappings"]] == ["SvcShopFindOrdersParams", "OrderRow"]


@needs_lsp
def test_the_ids_are_the_same_every_time(project):
    first = rest_sdk.service_data(project, "/svc", LSP)[1]
    assert rest_sdk.service_data(project, "/svc", LSP)[1] == first


@needs_lsp
def test_an_unknown_or_disabled_service_is_refused(project):
    with pytest.raises(ValueError, match="defines no REST service /nope"):
        rest_sdk.service_data(project, "/nope", LSP)
    with pytest.raises(ValueError, match="requires a service to be enabled"):
        rest_sdk.service_data(project, "/off", LSP)


def test_a_binary_without_the_rest_details_is_refused(monkeypatch):
    monkeypatch.setattr(lsp, "require_lsp", lambda lsp_path=None: "/bin/mariadb-lsp")
    # What msm's other REST work needs, but not the details of REST objects
    monkeypatch.setattr(lsp, "outline_features", lambda binary: lsp.REST_OUTLINE_FEATURES)
    with pytest.raises(lsp.LspError, match="cannot tell the details of REST objects"):
        rest_sdk.require_lsp()


# -----------------------------------------------------------------------------
# The files


def _config(directory) -> dict:
    with open(os.path.join(directory, "mrs.config.json")) as f:
        return json.load(f)


@needs_lsp
def test_the_sdk_files_are_written_and_record_the_project(project, tmp_path):
    directory = str(tmp_path / "sdk" / "svc.mrs.sdk")
    result = rest_sdk.dump_rest_sdk(
        project, "/svc", directory,
        {"service_url": "https://localhost:8443/svc", "sdk_language": "TypeScript"},
        lsp_path=LSP,
    )
    assert result["directory"] == directory
    assert result["service"] == "/svc" and result["sdk_language"] == "TypeScript"
    names = sorted(os.path.basename(f) for f in result["files"])
    assert names == ["MrsBaseClasses.ts", "mrs.config.json", "svc.ts"]

    config = _config(directory)
    assert config["serviceId"] is None and "dbConnectionUri" not in config
    assert config["serviceUrl"] == "https://localhost:8443/svc"
    assert config["msmProject"]["service"] == "/svc"
    assert os.path.realpath(os.path.join(directory, config["msmProject"]["path"])) == \
        os.path.realpath(project)

    with open(os.path.join(directory, "svc.ts")) as f:
        code = f.read()
    assert "class SvcShopCustomers" in code
    assert "Customer" in code and "firstName" in code
    assert "/findOrders" in code and "/orderCount" in code
    assert "/missing" not in code and "/hidden" not in code
    # The extension-only part (it needs metadata ids) is not in the files
    assert "mrsEditDbObject" not in code


@needs_lsp
def test_the_sdk_is_made_again_from_the_stored_options(project, tmp_path):
    directory = str(tmp_path / "svc.mrs.sdk")
    rest_sdk.dump_rest_sdk(
        project, "/svc", directory,
        {"service_url": "https://example.com/svc", "sdk_language": "Python"}, lsp_path=LSP,
    )
    first = _config(directory)
    result = rest_sdk.dump_rest_sdk(project, "/svc", directory, {}, lsp_path=LSP)
    assert result["sdk_language"] == "Python"
    assert sorted(os.path.basename(f) for f in result["files"]) == [
        "__init__.py", "mrs.config.json", "mrs_base_classes.py", "svc.py"
    ]
    again = _config(directory)
    assert again["serviceUrl"] == first["serviceUrl"]
    assert again["msmProject"] == first["msmProject"]
    # No header unless one is given
    assert again["header"] == ""
    with open(os.path.join(directory, "__init__.py")) as f:
        assert f.read() == ""


@needs_lsp
def test_a_given_header_is_kept_and_the_former_default_dropped(project, tmp_path):
    directory = str(tmp_path / "svc.mrs.sdk")
    options = {"service_url": "https://example.com/svc", "sdk_language": "Python"}
    rest_sdk.dump_rest_sdk(project, "/svc", directory, {**options, "header": "# (c) Me"},
                           lsp_path=LSP)
    rest_sdk.dump_rest_sdk(project, "/svc", directory, {}, lsp_path=LSP)
    assert _config(directory)["header"] == "# (c) Me"

    # A folder written with the former default header
    config = _config(directory)
    config["header"] = "# Copyright (c) 2023, 2026, Oracle and/or its affiliates."
    with open(os.path.join(directory, "mrs.config.json"), "w") as f:
        json.dump(config, f)
    rest_sdk.dump_rest_sdk(project, "/svc", directory, {}, lsp_path=LSP)
    assert _config(directory)["header"] == ""


@needs_lsp
def test_unknown_options_and_a_missing_url_are_refused(project, tmp_path):
    directory = str(tmp_path / "svc.mrs.sdk")
    with pytest.raises(ValueError, match="Unknown SDK options: service_id"):
        rest_sdk.dump_rest_sdk(project, "/svc", directory, {"service_id": "x"}, lsp_path=LSP)
    with pytest.raises(Exception, match="The service URL is required"):
        rest_sdk.dump_rest_sdk(project, "/svc", directory, {}, lsp_path=LSP)
    assert not os.path.exists(directory)
