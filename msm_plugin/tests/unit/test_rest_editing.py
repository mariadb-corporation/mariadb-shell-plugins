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

"""Tests for changing the REST definition of a project: lib/rest_editing.py.

They run where a mariadb-lsp binary lists every statement of each REST item
(outline feature rest_statements): MSM_TEST_LSP names one, the binary or its
folder, else the binaries msm would find are tried.
"""

import os

import pytest

from msm_plugin.lib import editing, lsp, management, modeling, rest_editing
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib.management import create_schema_project_folder
from msm_plugin.tests.unit import helpers


def _lists_statements(binary: str) -> bool:
    return rest_editing.STATEMENTS_FEATURE in lsp.outline_features(binary)


LSP = helpers.find_test_lsp(_lists_statements)
pytestmark = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary that lists the statements of REST items"
)

REST_SQL = """-- The shop service
CREATE OR REPLACE REST SERVICE /svc ADD AUTH APP 'MRS';

USE REST SERVICE /svc SCHEMA /shop;

CREATE OR REPLACE REST SCHEMA /shop ON SERVICE /svc FROM `shop`;

-- Items
CREATE OR REPLACE REST VIEW /item ON SERVICE /svc SCHEMA /shop AS `shop`.`item`;

ALTER REST SERVICE /svc COMMENT 'the shop';

ALTER REST SERVICE /svc ADD AUTH APP 'Other';

CREATE OR REPLACE REST SERVICE /other;"""


def make_project(tmp_path, rest_sql: str = REST_SQL) -> str:
    path = create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.", license="MIT"
    )
    if rest_sql:
        set_section_180(path, rest_sql)
    modeling.update_schema_definition(path, LSP)
    return path


def set_section_180(path, sql: str) -> None:
    """Section 180's body, a blank line after its banner as msm writes it."""
    dev_file = management.get_schema_development_file_path(path)
    sections = management.get_file_sections(dev_file)
    sections_lib.set_section_body(sections["180"], sql)
    management.write_sections_to_file(dev_file, sections, overwrite_existing=True)


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path)


def base_hash(path) -> str:
    return modeling.read_diagram_rows(path)[0]["document"]["source_hash"]


def section_180(path) -> str:
    with open(management.get_schema_development_file_path(path)) as f:
        script = f.read()
    return management.get_script_sections(script)["180"]["sql_content"].strip()


def body(path) -> str:
    """Section 180 without its banner comments."""
    text = section_180(path)
    marker = "-- Create a REST service, schema and endpoints"
    return text[text.index(marker) + len(marker):].strip() if marker in text else text


def set_statements(path, *statements, **kwargs):
    return rest_editing.set_rest_statements(path, list(statements), base_hash(path),
                                            lsp_path=LSP, **kwargs)


def drop(path, kind, service, schema=None, rest_object=None):
    return rest_editing.drop_rest(path, kind, service, base_hash(path), schema=schema,
                                  rest_object=rest_object, lsp_path=LSP)


# -----------------------------------------------------------------------------
# set_rest_statements


def test_a_new_service_goes_into_an_empty_section(tmp_path):
    path = make_project(tmp_path, rest_sql="")
    result = set_statements(path, "CREATE REST SERVICE /notes")
    assert body(path) == "CREATE OR REPLACE REST SERVICE /notes;"
    (entry,) = result["statements"]
    assert entry["kind"] == "service" and entry["service"] == "/notes"
    assert entry["replaced"] is False
    assert entry["file"] == management.get_schema_development_file_path(path)
    with open(entry["file"]) as f:
        assert f.read().split("\n")[entry["line"] - 1] == "CREATE OR REPLACE REST SERVICE /notes;"
    assert result["migration"] == [] and result["migration_file"] is None
    # The snapshot is made again
    assert result["rows"][0]["document"]["source_hash"] == base_hash(path)


def test_a_new_schema_and_its_objects_go_after_their_service(project):
    set_statements(
        project,
        "CREATE REST SCHEMA /hr ON SERVICE /svc FROM `hr`;",
        "CREATE OR REPLACE REST VIEW /staff ON SERVICE /svc SCHEMA /hr AS `hr`.`staff`;",
        "CREATE REST VIEW /pay ON SERVICE /svc SCHEMA /hr AS `hr`.`pay`",
    )
    text = body(project)
    assert text.endswith(
        "ALTER REST SERVICE /svc ADD AUTH APP 'Other';\n\n"
        "CREATE OR REPLACE REST SCHEMA /hr ON SERVICE /svc FROM `hr`;\n\n"
        "CREATE OR REPLACE REST VIEW /staff ON SERVICE /svc SCHEMA /hr AS `hr`.`staff`;\n\n"
        "CREATE OR REPLACE REST VIEW /pay ON SERVICE /svc SCHEMA /hr AS `hr`.`pay`;\n\n"
        "CREATE OR REPLACE REST SERVICE /other;"
    )


def test_a_new_object_goes_after_its_schema(project):
    set_statements(
        project, "CREATE REST PROCEDURE /add ON SERVICE /svc SCHEMA /shop AS `shop`.`add_item`"
    )
    text = body(project)
    assert text.index("REST VIEW /item") < text.index("REST PROCEDURE /add") < text.index(
        "COMMENT 'the shop'"
    )


def test_an_item_there_is_replaced_where_it_stands(project):
    result = set_statements(
        project, "CREATE OR REPLACE REST VIEW /item ON SERVICE /svc SCHEMA /shop "
                 "AS `shop`.`item` CLASS `Item`;"
    )
    assert result["statements"][0]["replaced"] is True
    assert body(project) == REST_SQL.replace("AS `shop`.`item`;", "AS `shop`.`item` CLASS `Item`;")


def test_a_replaced_service_loses_its_alters_but_its_auth_app_links(project):
    set_statements(project, "CREATE OR REPLACE REST SERVICE /svc COMMENT 'new' ADD AUTH APP 'MRS'")
    text = body(project)
    assert text.startswith("-- The shop service\n"
                           "CREATE OR REPLACE REST SERVICE /svc COMMENT 'new' ADD AUTH APP 'MRS';")
    assert "COMMENT 'the shop'" not in text
    assert "ALTER REST SERVICE /svc ADD AUTH APP 'Other';" in text
    assert "\n\n\n" not in text


def test_an_alter_that_also_links_an_auth_app_is_refused(tmp_path):
    path = make_project(tmp_path, "CREATE OR REPLACE REST SERVICE /svc;\n\n"
                                  "ALTER REST SERVICE /svc COMMENT 'x' ADD AUTH APP 'MRS';")
    before = section_180(path)
    with pytest.raises(ValueError, match="links an auth app and changes more"):
        set_statements(path, "CREATE REST SERVICE /svc")
    assert section_180(path) == before


def test_a_renamed_object_takes_the_place_of_the_old_one(project):
    set_statements(
        project,
        "CREATE OR REPLACE REST VIEW /items ON SERVICE /svc SCHEMA /shop AS `shop`.`item`;",
        replaces={"kind": "object", "service": "/svc", "schema": "/shop", "object": "/item"},
    )
    assert body(project) == REST_SQL.replace("REST VIEW /item ", "REST VIEW /items ")


def test_a_rename_onto_an_object_there_replaces_it_and_drops_the_old_one(tmp_path):
    path = make_project(tmp_path, REST_SQL.replace(
        "\nALTER REST SERVICE /svc COMMENT",
        "\nCREATE OR REPLACE REST VIEW /items ON SERVICE /svc SCHEMA /shop AS `shop`.`x`;\n"
        "\nALTER REST SERVICE /svc COMMENT",
    ))
    set_statements(
        path,
        "CREATE OR REPLACE REST VIEW /items ON SERVICE /svc SCHEMA /shop AS `shop`.`item`;",
        replaces={"kind": "object", "service": "/svc", "schema": "/shop", "object": "/item"},
    )
    text = body(path)
    assert "REST VIEW /item " not in text and "-- Items" not in text
    assert "REST VIEW /items ON SERVICE /svc SCHEMA /shop AS `shop`.`item`;" in text


@pytest.mark.parametrize("statement, message", [
    ("CREATE REST SERVICE IF NOT EXISTS /x", "IF NOT EXISTS"),
    ("CREATE REST VIEW /x AS `shop`.`x`", "ON SERVICE /svc SCHEMA /schema"),
    ("CREATE REST SCHEMA /x FROM `shop`", "names its service"),
    ("CREATE REST SERVICE /x; CREATE REST SERVICE /y;", "one statement"),
    ("CREATE TABLE t (id INT)", "one CREATE REST SERVICE"),
    ("CREATE REST VIEW /x ON SERVICE /svc SCHEMA /shop AS", "cannot be read"),
    ("", "empty"),
])
def test_what_is_not_one_create_of_a_rest_item_is_refused(project, statement, message):
    before = section_180(project)
    with pytest.raises(ValueError, match=message):
        set_statements(project, statement)
    assert section_180(project) == before


def test_a_service_or_schema_rename_is_refused(project):
    with pytest.raises(ValueError, match="cannot be renamed"):
        set_statements(project, "CREATE REST SERVICE /svc2",
                       replaces={"kind": "service", "service": "/svc"})


def test_a_rename_of_an_object_not_there_is_refused(project):
    with pytest.raises(ValueError, match="creates no REST object"):
        set_statements(
            project, "CREATE REST VIEW /n ON SERVICE /svc SCHEMA /shop AS `shop`.`n`",
            replaces={"kind": "object", "service": "/svc", "schema": "/shop", "object": "/nope"},
        )


def test_two_statements_of_one_item_are_refused(project):
    with pytest.raises(ValueError, match="same REST item"):
        set_statements(project, "CREATE REST SERVICE /x", "CREATE REST SERVICE /X")


def test_a_stale_base_hash_is_refused(project):
    with pytest.raises(editing.StaleSchemaError):
        rest_editing.set_rest_statements(project, ["CREATE REST SERVICE /x"], "stale", lsp_path=LSP)


def test_a_failed_check_puts_every_file_back(project, monkeypatch):
    before, rows = section_180(project), modeling.read_diagram_rows(project)

    def refuse(*args):
        raise ValueError("refused")

    monkeypatch.setattr(rest_editing, "_check_result", refuse)
    with pytest.raises(ValueError, match="refused"):
        set_statements(project, "CREATE REST SERVICE /x")
    assert section_180(project) == before
    assert modeling.read_diagram_rows(project) == rows


def test_a_service_path_with_developers_is_the_same_service(tmp_path):
    path = make_project(tmp_path, "CREATE OR REPLACE REST SERVICE mike,anna@/svc;")
    result = set_statements(path, "CREATE REST SERVICE anna, mike@/svc COMMENT 'x'")
    assert result["statements"][0]["replaced"] is True
    assert body(path) == "CREATE OR REPLACE REST SERVICE anna, mike@/svc COMMENT 'x';"


def test_a_new_object_goes_into_the_breakout_file_its_schema_is_in(tmp_path):
    path = make_project(tmp_path, "")
    development = os.path.dirname(management.get_schema_development_file_path(path))
    os.makedirs(os.path.join(development, "sections"))
    breakout = os.path.join(development, "sections", "180_rest.sql")
    with open(breakout, "w") as f:
        f.write("-- header\n" + REST_SQL + "\n")
    set_section_180(path, "SOURCE './sections/180_rest.sql'[ln2:]; -- Without the header")
    modeling.update_schema_definition(path, LSP)
    result = set_statements(
        path, "CREATE REST VIEW /n ON SERVICE /svc SCHEMA /shop AS `shop`.`n`"
    )
    with open(breakout) as f:
        text = f.read()
    assert "AS `shop`.`item`;\n\nCREATE OR REPLACE REST VIEW /n ON SERVICE /svc SCHEMA /shop " \
           "AS `shop`.`n`;\n\nALTER REST SERVICE /svc COMMENT" in text
    assert result["statements"][0]["file"] == breakout


# -----------------------------------------------------------------------------
# drop_rest


def test_an_object_is_dropped_with_its_comments(project):
    result = drop(project, "object", "/svc", "/shop", "/item")
    assert body(project) == REST_SQL.replace(
        "-- Items\nCREATE OR REPLACE REST VIEW /item ON SERVICE /svc SCHEMA /shop "
        "AS `shop`.`item`;\n\n", ""
    )
    (removed,) = result["removed"]
    assert removed["file"] == management.get_schema_development_file_path(project)
    assert result["notes"] == []


def test_a_schema_is_dropped_with_its_objects_and_keeps_the_service_in_use(project):
    drop(project, "schema", "/svc", "/shop")
    assert body(project) == (
        "-- The shop service\n"
        "CREATE OR REPLACE REST SERVICE /svc ADD AUTH APP 'MRS';\n\n"
        "USE REST SERVICE /svc;\n\n"
        "ALTER REST SERVICE /svc COMMENT 'the shop';\n\n"
        "ALTER REST SERVICE /svc ADD AUTH APP 'Other';\n\n"
        "CREATE OR REPLACE REST SERVICE /other;"
    )


def test_a_service_is_dropped_with_everything_on_it(project):
    result = drop(project, "service", "/SVC")
    assert body(project) == "CREATE OR REPLACE REST SERVICE /other;"
    # CREATE, USE, the schema, the view and both ALTERs
    assert len(result["removed"]) == 6


def test_what_is_put_on_a_service_only_named_is_dropped_with_a_note(tmp_path):
    path = make_project(tmp_path, "CREATE OR REPLACE REST SERVICE /svc;\n\n"
                                  "CREATE OR REPLACE REST SCHEMA /s ON SERVICE /shared FROM `shop`;")
    result = drop(path, "service", "/shared")
    assert body(path) == "CREATE OR REPLACE REST SERVICE /svc;"
    assert "only names it" in result["notes"][0]


def test_an_item_not_there_is_refused(project):
    with pytest.raises(ValueError, match="has no REST object"):
        drop(project, "object", "/svc", "/shop", "/nope")
    with pytest.raises(ValueError, match="Name the REST schema"):
        drop(project, "schema", "/svc")
    with pytest.raises(ValueError, match="Unknown kind"):
        drop(project, "table", "/svc")
