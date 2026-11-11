# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for the REST endpoints a release removes: lib/rest_removal.py,
Section 280 of the update scripts and Section 380 of the deployment scripts.

The REST definitions are read by mariadb-lsp's outline: the tests that need
it run where a binary outlines REST definitions as msm needs (MSM_TEST_LSP
names one, the binary or its folder, else the binaries msm would find are
tried).
"""

import os

import pytest

from msm_plugin.lib import lsp, management, rest_removal
from msm_plugin.lib.management import create_schema_project_folder
from msm_plugin.tests.unit import helpers


LSP = helpers.find_test_lsp(lsp.outlines_rest)
pytestmark = pytest.mark.skipif(
    LSP is None, reason="no mariadb-lsp binary that outlines REST definitions as msm needs"
)

REST_V1 = """CREATE OR REPLACE REST SERVICE /notes ADD AUTH APP 'MRS' ADD AUTH APP 'Other';
CREATE OR REPLACE REST SCHEMA /app ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST VIEW /note ON SERVICE /notes SCHEMA /app AS `shop`.`note` {
    id: id
};
CREATE OR REPLACE REST PROCEDURE /add ON SERVICE /notes SCHEMA /app AS `shop`.`add_note`;
CREATE OR REPLACE REST SCHEMA /old ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST FUNCTION /f ON SERVICE /notes SCHEMA /old AS `shop`.`f`;
CREATE OR REPLACE REST SERVICE /gone ADD AUTH APP 'MRS';
CREATE OR REPLACE REST SCHEMA /g ON SERVICE /gone FROM `shop`;
CREATE OR REPLACE REST VIEW /v ON SERVICE /gone SCHEMA /g AS `shop`.`v`;
CREATE OR REPLACE REST SCHEMA /s ON SERVICE /shared FROM `shop`;"""

# /notes keeps MRS and /app (spelled in other cases), drops the Other link
# and /old; /add turns into a function; /gone goes, and /shared's schema
# /s, while /shared itself was never created here
REST_V2 = """CREATE OR REPLACE REST SERVICE /Notes ADD AUTH APP 'mrs';
CREATE OR REPLACE REST SCHEMA /APP ON SERVICE /notes FROM `shop`;
CREATE OR REPLACE REST VIEW /Note ON SERVICE /notes SCHEMA /app AS `shop`.`note` {
    id: id
};
CREATE OR REPLACE REST FUNCTION /add ON SERVICE /notes SCHEMA /app AS `shop`.`f`;"""

V1_TO_V2 = [
    "DROP REST PROCEDURE IF EXISTS /add FROM SERVICE /notes SCHEMA /app;",
    "DROP REST FUNCTION IF EXISTS /f FROM SERVICE /notes SCHEMA /old;",
    "DROP REST VIEW IF EXISTS /v FROM SERVICE /gone SCHEMA /g;",
    "DROP REST SCHEMA IF EXISTS /old FROM SERVICE /notes;",
    "DROP REST SCHEMA IF EXISTS /g FROM SERVICE /gone;",
    "DROP REST SCHEMA IF EXISTS /s FROM SERVICE /shared;",
    "ALTER REST SERVICE /notes REMOVE AUTH APP 'Other' IF EXISTS;",
    "ALTER REST SERVICE /gone REMOVE AUTH APP 'MRS' IF EXISTS;",
    "DROP REST SERVICE IF EXISTS /gone;",
]


def outlined(sql: str) -> dict:
    return rest_removal.definition(lsp.outline(LSP, sql), sql)[0]


def statements(sql: str) -> list:
    return [
        line for line in (sql or "").splitlines() if line.strip() and not line.startswith("--")
    ]


def commented(sql: str) -> list:
    return [line[3:] for line in sql.splitlines() if line.startswith("-- DROP REST")
            or line.startswith("-- ALTER REST")]


# -----------------------------------------------------------------------------
# The statements


def test_what_a_version_no_longer_defines_is_removed():
    removals, warnings = rest_removal.removals(outlined(REST_V1), outlined(REST_V2))
    assert removals == V1_TO_V2
    assert warnings == []


def test_nothing_is_removed_from_the_same_definition():
    assert rest_removal.removals(outlined(REST_V1), outlined(REST_V1)) == ([], [])


def test_a_developer_service_is_another_service():
    removals, _ = rest_removal.removals(
        outlined("CREATE REST SERVICE mike@/s;"), outlined("CREATE REST SERVICE /s;")
    )
    assert removals == ["DROP REST SERVICE IF EXISTS mike@/s;"]


def test_a_quoted_path_is_the_same_path():
    removals, _ = rest_removal.removals(
        outlined("CREATE REST SERVICE `/s`;"), outlined("CREATE REST SERVICE /S;")
    )
    assert removals == []


def on_database(database: str) -> str:
    return (
        "CREATE REST SERVICE /n;\n"
        f"CREATE OR REPLACE REST SCHEMA /app ON SERVICE /n FROM {database};\n"
        "CREATE OR REPLACE REST VIEW /t ON SERVICE /n SCHEMA /app AS `t`;"
    )


def test_a_schema_on_another_database_is_dropped_where_it_still_is():
    removals, warnings = rest_removal.removals(
        outlined(on_database("`a`")), outlined(on_database("`b`"))
    )
    assert removals == ["DROP REST SCHEMA IF EXISTS /app FROM SERVICE /n FOR SCHEMA `a`;"]
    assert warnings == []


def test_a_moved_schema_keeps_the_placeholders_of_its_database():
    removals, _ = rest_removal.removals(
        outlined(on_database("/*<msm:schema_prefix>*/shop")), outlined(on_database("other"))
    )
    assert removals == [
        "DROP REST SCHEMA IF EXISTS /app FROM SERVICE /n "
        "FOR SCHEMA /*<msm:schema_prefix>*/shop;"
    ]


def test_a_change_of_its_placeholders_is_a_move():
    removals, _ = rest_removal.removals(
        outlined(on_database("/*<msm:schema_prefix>*/shop")), outlined(on_database("shop"))
    )
    assert removals == [
        "DROP REST SCHEMA IF EXISTS /app FROM SERVICE /n "
        "FOR SCHEMA /*<msm:schema_prefix>*/shop;"
    ]


def test_the_same_database_quoted_or_not_is_no_move():
    assert rest_removal.removals(
        outlined(on_database("`shop`")), outlined(on_database("shop"))
    ) == ([], [])


def test_a_database_changing_only_in_case_is_named():
    removals, warnings = rest_removal.removals(
        outlined(on_database("`shop`")), outlined(on_database("`Shop`"))
    )
    assert removals == []
    assert "changes its database only in case" in warnings[0]


def test_a_move_back_keeps_the_schema_and_a_move_on_drops_it():
    drop = "DROP REST SCHEMA IF EXISTS /app FROM SERVICE /n FOR SCHEMA `a`;\n"
    [back] = kept(on_database("`a`"), drop)
    assert commented(back) == [drop.strip()]
    [on] = kept(on_database("`c`"), drop)
    assert on == drop


def test_the_first_release_and_a_section_without_rest_remove_nothing(monkeypatch):
    assert rest_removal.release_removals(None, REST_V1, "1.0.0", LSP) is None
    assert rest_removal.release_removals("-- nothing\n", REST_V1, "1.0.0", LSP) is None
    # SQL that is not REST needs no binary that reads REST definitions
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    assert rest_removal.release_removals(
        "CREATE TABLE IF NOT EXISTS `t` (`id` INT);", "", "1.0.0", LSP
    ) is None


def test_a_statement_msm_cannot_place_is_named():
    body = rest_removal.release_removals(
        "CREATE REST VIEW /x AS `shop`.`x`;", "", "1.0.0", LSP
    )
    assert "msm could not place every REST statement" in body
    assert "version 1.0.0, Section 180 line 1: CREATE REST VIEW /x" in body
    assert statements(body) == []


def test_an_old_binary_is_refused(monkeypatch):
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    with pytest.raises(lsp.LspError, match="cannot read REST definitions"):
        rest_removal.release_removals(REST_V1, REST_V2, "1.0.0", LSP)


# -----------------------------------------------------------------------------
# What a deployment keeps


def kept(target, *removals, version="1.2.0") -> list:
    """The Sections 280 of update scripts after keep_redefined."""
    updates = [{"280": {"sql_content": sql}} for sql in removals]
    rest_removal.keep_redefined(updates, target, version, lsp_path=LSP)
    return [update["280"]["sql_content"] for update in updates]


def test_a_removal_of_what_the_target_defines_again_is_commented_out():
    removals = "\n".join(V1_TO_V2) + "\n"
    # The service comes back without its schema /g and the MRS link
    target = "CREATE OR REPLACE REST SERVICE /gone;\n" + REST_V2
    [section] = kept(target, removals)
    assert commented(section) == ["DROP REST SERVICE IF EXISTS /gone;"]
    assert statements(section) == V1_TO_V2[:-1]
    assert "-- msm: version 1.2.0 defines this again, so it is kept" in section


def test_the_target_is_outlined_once_for_every_update(monkeypatch):
    removals = "\n".join(V1_TO_V2) + "\n"
    calls = []
    outline = lsp.outline
    monkeypatch.setattr(lsp, "outline", lambda *a: calls.append(a) or outline(*a))
    # Nothing the target defines: one outline of each section with the target
    assert kept(REST_V2, removals, removals, version="1.1.0") == [removals, removals]
    assert len(calls) == 3


def test_no_binary_is_asked_without_both_sections(monkeypatch):
    def no_binary(lsp_path=None):
        raise AssertionError("asked for a binary")

    monkeypatch.setattr(rest_removal, "require_rest_lsp", no_binary)
    assert kept("", "DROP REST SERVICE /x;") == ["DROP REST SERVICE /x;"]
    assert kept(REST_V1, "-- none\n") == ["-- none\n"]


# -----------------------------------------------------------------------------
# Releases and deployment scripts


@pytest.fixture
def project(tmp_path):
    return create_schema_project_folder(
        schema_name="shop", target_path=str(tmp_path), copyright_holder="Example Ltd.",
        license="MIT",
    )


def release(path, rest_sql, version, next_version) -> list:
    management.set_section_sql_content(
        "180", "\n" + rest_sql + "\n", management.get_schema_development_file_path(path)
    )
    return management.prepare_release(path, version, next_version, lsp_path=LSP)


def update_section_280(path, version_from, version_to, schema="shop") -> str:
    update = os.path.join(
        path, "releases", "updates", f"{schema}_{version_from}_to_{version_to}.sql"
    )
    return management.get_file_sections(update)["280"]["sql_content"]


def test_a_release_lists_its_removals_and_a_deployment_keeps_what_comes_back(project):
    release(project, REST_V1, "1.0.0", "1.1.0")
    release(project, REST_V2, "1.1.0", "1.2.0")
    assert statements(update_section_280(project, "1.0.0", "1.1.0")) == V1_TO_V2

    # 1.2.0 has the service /gone again, without its schema and link
    release(project, REST_V2 + "\nCREATE OR REPLACE REST SERVICE /gone;", "1.2.0", "1.3.0")
    assert statements(update_section_280(project, "1.1.0", "1.2.0")) == []

    for version in ("1.0.0", "1.1.0", "1.2.0"):
        management.generate_deployment_script(project, version, lsp_path=LSP)
    deployment = os.path.join(project, "releases", "deployment", "shop_deployment_{}.sql")
    sections = management.get_file_sections(deployment.format("1.2.0"))
    section_380 = sections["380"]["sql_content"]
    assert commented(section_380) == ["DROP REST SERVICE IF EXISTS /gone;"]
    assert statements(section_380) == V1_TO_V2[:-1]
    assert list(sections).index("380") == list(sections).index("180") - 1

    # 1.1.0's own deployment drops all of it
    section_380 = management.get_file_sections(deployment.format("1.1.0"))["380"]["sql_content"]
    assert statements(section_380) == V1_TO_V2


def test_an_update_whose_removals_all_come_back_is_left_out(project):
    release(project, REST_V1, "1.0.0", "1.1.0")
    release(project, REST_V2, "1.1.0", "1.2.0")
    release(project, REST_V1, "1.2.0", "1.3.0")
    management.generate_deployment_script(project, "1.2.0", lsp_path=LSP)
    deployment = os.path.join(project, "releases", "deployment", "shop_deployment_1.2.0.sql")
    section_380 = management.get_file_sections(deployment)["380"]["sql_content"]
    # 1.1.0's update to 1.2.0 drops the function /add, as 1.1.0 spelled it
    assert statements(section_380) == [
        "DROP REST FUNCTION IF EXISTS /add FROM SERVICE /Notes SCHEMA /APP;",
    ]


def test_a_release_with_an_old_binary_writes_nothing(project, monkeypatch):
    release(project, REST_V1, "1.0.0", "1.1.0")
    monkeypatch.setattr(lsp, "outlines_rest", lambda binary: False)
    with pytest.raises(lsp.LspError):
        release(project, REST_V2, "1.1.0", "1.2.0")
    assert not os.path.exists(os.path.join(project, "releases", "versions", "shop_1.1.0.sql"))
    assert not os.path.exists(
        os.path.join(project, "releases", "updates", "shop_1.0.0_to_1.1.0.sql")
    )


# -----------------------------------------------------------------------------
# Deployed on a server with the REST metadata

SANDBOX_SCHEMA = "msm_rest_removal"
# The database 1.3.0 moves the REST schema /app to, and 1.4.0 back from
OTHER_DATABASE = "msm_rest_removal_b"
SERVICES = ("/notes", "/gone", "/shared")
SANDBOX_OBJECTS = """DELIMITER %%
CREATE OR REPLACE PROCEDURE `add_note`() BEGIN END%%
CREATE OR REPLACE FUNCTION `f`() RETURNS INT RETURN 1%%
CREATE OR REPLACE VIEW `v` AS SELECT 1 AS `id`%%
DELIMITER ;"""


def on_sandbox(sql: str) -> str:
    return sql.replace("`shop`", f"`{SANDBOX_SCHEMA}`")


def rest_state(session) -> dict:
    """{service: {id, links, schemas: {path: {object path: (id, type)}},
    databases: {path: database}}} of the test's services, paths in lower
    case."""
    services = {}
    for row in helpers.query(session, "SELECT `id`, `url_context_root` AS `path` "
                    "FROM `mariadb_rest_service`.`service`"):
        if row["path"].lower() in SERVICES:
            services[row["path"].lower()] = {
                "id": row["id"], "links": set(), "schemas": {}, "databases": {},
            }
    for row in helpers.query(session, """SELECT s.`url_context_root` AS `service`, a.`name`
            FROM `mariadb_rest_service`.`service_has_auth_app` h
            JOIN `mariadb_rest_service`.`service` s ON s.`id` = h.`service_id`
            JOIN `mariadb_rest_service`.`auth_app` a ON a.`id` = h.`auth_app_id`"""):
        if row["service"].lower() in services:
            services[row["service"].lower()]["links"].add(row["name"].lower())
    for row in helpers.query(session, """SELECT s.`url_context_root` AS `service`,
                rs.`request_path` AS `schema`, rs.`name` AS `database`, ro.`request_path` AS `object`,
                ro.`id`, ro.`object_type`
            FROM `mariadb_rest_service`.`rest_schema` rs
            JOIN `mariadb_rest_service`.`service` s ON s.`id` = rs.`service_id`
            LEFT JOIN `mariadb_rest_service`.`rest_object` ro ON ro.`rest_schema_id` = rs.`id`"""):
        service = services.get(row["service"].lower())
        if service is None:
            continue
        objects = service["schemas"].setdefault(row["schema"].lower(), {})
        service["databases"][row["schema"].lower()] = row["database"]
        if row["object"] is not None:
            objects[row["object"].lower()] = (row["id"], row["object_type"])
    return services


def layout(state: dict) -> dict:
    """The state without ids."""
    return {
        path: (sorted(service["links"]), {
            schema: {item: kind for item, (_, kind) in objects.items()}
            for schema, objects in service["schemas"].items()
        })
        for path, service in state.items()
    }


@pytest.fixture
def rest_sandbox(sandbox_session):
    """The sandbox with the REST metadata, the auth apps the definitions
    link and the service /shared another project would own; the test's
    services and schema are dropped before and after."""
    session = sandbox_session
    session.run_sql("CONFIGURE REST METADATA")
    session.run_sql("CREATE REST AUTH APP IF NOT EXISTS 'MRS' VENDOR MRS")
    session.run_sql("CREATE REST AUTH APP IF NOT EXISTS 'Other' VENDOR MRS")

    def clean():
        for path in SERVICES:
            session.run_sql(f"DROP REST SERVICE IF EXISTS {path}")
        session.run_sql(f"DROP SCHEMA IF EXISTS `{SANDBOX_SCHEMA}`")
        session.run_sql(f"DROP SCHEMA IF EXISTS `{OTHER_DATABASE}`")

    def fresh():
        clean()
        session.run_sql("CREATE REST SERVICE /shared")
        # The objects of /app, in the database it moves to
        session.run_sql(f"CREATE SCHEMA `{OTHER_DATABASE}`")
        session.run_sql(f"CREATE TABLE `{OTHER_DATABASE}`.`note` (`id` INT PRIMARY KEY)")
        session.run_sql(f"CREATE FUNCTION `{OTHER_DATABASE}`.`f`() RETURNS INT RETURN 1")

    fresh()
    try:
        yield session, fresh
    finally:
        clean()


V2_LAYOUT = {
    "/notes": (["mrs"], {"/app": {"/note": "TABLE", "/add": "FUNCTION"}}),
    "/shared": ([], {}),
}
# 1.2.0 on: the service /gone again, without its schema and link
V3_LAYOUT = {**V2_LAYOUT, "/gone": ([], {})}


def test_deployments_end_with_the_target_definition(rest_sandbox, tmp_path):
    session, fresh = rest_sandbox
    path = create_schema_project_folder(
        schema_name=SANDBOX_SCHEMA, target_path=str(tmp_path),
        copyright_holder="Example Ltd.", license="MIT",
    )
    dev_file = management.get_schema_development_file_path(path)
    management.set_section_sql_content(
        "140", "\nCREATE TABLE `note` (`id` INT PRIMARY KEY);\n", dev_file
    )
    management.set_section_sql_content("150", "\n" + SANDBOX_OBJECTS + "\n", dev_file)
    release(path, on_sandbox(REST_V1), "1.0.0", "1.1.0")
    release(path, on_sandbox(REST_V2), "1.1.0", "1.2.0")
    # The service /gone comes back, without its schema and link
    with_gone = on_sandbox(REST_V2) + "\nCREATE OR REPLACE REST SERVICE /gone;"
    release(path, with_gone, "1.2.0", "1.3.0")
    # 1.3.0 moves /app to another database, 1.4.0 back
    moved = with_gone.replace(f"`{SANDBOX_SCHEMA}`", f"`{OTHER_DATABASE}`")
    release(path, moved, "1.3.0", "1.4.0")
    assert statements(update_section_280(path, "1.2.0", "1.3.0", SANDBOX_SCHEMA)) == [
        f"DROP REST SCHEMA IF EXISTS /APP FROM SERVICE /Notes FOR SCHEMA `{SANDBOX_SCHEMA}`;",
    ]
    release(path, with_gone, "1.4.0", "1.5.0")
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        management.generate_deployment_script(path, version, lsp_path=LSP)

    def deploy(version):
        management.deploy_schema(session, path, version)

    # A fresh install of 1.1.0: its removals find nothing to drop
    deploy("1.1.0")
    assert layout(rest_state(session)) == V2_LAYOUT

    # 1.0.0, then the update to 1.1.0, which keeps what stays in place
    fresh()
    deploy("1.0.0")
    before = rest_state(session)
    assert set(before) == {"/notes", "/gone", "/shared"}
    deploy("1.1.0")
    after = rest_state(session)
    assert layout(after) == V2_LAYOUT
    assert after["/notes"]["id"] == before["/notes"]["id"]
    assert after["/notes"]["schemas"]["/app"]["/note"] == before["/notes"]["schemas"]["/app"]["/note"]

    # 1.0.0 straight to 1.2.0: /gone keeps its id, not its schema and link
    fresh()
    deploy("1.0.0")
    before = rest_state(session)
    deploy("1.2.0")
    after = rest_state(session)
    assert layout(after) == V3_LAYOUT
    assert after["/gone"]["id"] == before["/gone"]["id"]

    # 1.1.0 to 1.2.0, where 1.0.0's removals run again on what is gone
    fresh()
    deploy("1.1.0")
    deploy("1.2.0")
    assert layout(rest_state(session)) == V3_LAYOUT

    # A fresh install of 1.2.0
    fresh()
    deploy("1.2.0")
    assert layout(rest_state(session)) == V3_LAYOUT

    def app():
        """/app's database and its objects' ids on the server."""
        notes = rest_state(session)["/notes"]
        return notes["databases"]["/app"], {
            item: item_id for item, (item_id, _) in notes["schemas"]["/app"].items()
        }

    # The move, and the move back, each on the deployment that makes it
    fresh()
    deploy("1.2.0")
    database, ids = app()
    assert database == SANDBOX_SCHEMA
    deploy("1.3.0")
    database, moved_ids = app()
    assert database == OTHER_DATABASE
    assert set(moved_ids) == set(ids) and not set(moved_ids.values()) & set(ids.values())
    deploy("1.4.0")
    assert layout(rest_state(session)) == V3_LAYOUT
    database, back_ids = app()
    assert database == SANDBOX_SCHEMA and set(back_ids) == set(ids)

    # 1.2.0 straight to 1.4.0 never moves: 1.3.0's drop is commented out
    fresh()
    deploy("1.2.0")
    _, ids = app()
    deploy("1.4.0")
    assert app() == (SANDBOX_SCHEMA, ids)

    # 1.0.0 straight to 1.3.0 moves it; a fresh 1.3.0 has it there too
    fresh()
    deploy("1.0.0")
    deploy("1.3.0")
    assert app()[0] == OTHER_DATABASE
    fresh()
    deploy("1.3.0")
    assert app()[0] == OTHER_DATABASE
