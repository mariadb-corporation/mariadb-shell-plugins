# Copyright (c) 2026, MariaDB plc and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""End-to-end tests for the db.connect / execute_sql / close tools.

Uses the shared sandbox deployed by ``test_sandbox_deploy`` (see the ``sandbox``
fixture in conftest.py, which also stores its connection in the secret store).
The db tools are driven against it over a single persistent MCP stdio session.
The test schema it creates is dropped and the connection closed at the end; the
sandbox itself is torn down by ``test_sandbox_shutdown``.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp mariadbd

import asyncio
import os
import uuid

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers


async def _db_flow(uri, script_dir):
    """Drives connect -> execute_sql -> execute_sql_script -> close.

    ``script_dir`` is an allowed directory used to exercise
    db.execute_sql_script with a script read from a file on disk.
    """
    async with helpers.mcp_session(["db"]) as call:
        # Open the connection and cache it in the server process.
        connect_result = await call("db.connect", {"uri": uri})
        assert connect_result.isError is False
        connection_id = helpers.tool_payload(connect_result)
        assert isinstance(connection_id, str) and connection_id != ""

        # SELECT @@version returns a single non-empty version string.
        version_result = await call(
            "db.execute_sql",
            {"connection_id": connection_id, "sql": "SELECT @@version"},
        )
        assert version_result.isError is False
        version_rows = helpers.tool_payload(version_result)["rows"]
        assert len(version_rows) == 1
        version_value = list(version_rows[0].values())[0]
        assert isinstance(version_value, str) and version_value != ""

        # Values with no JSON representation of their own survive the trip as
        # the text the shell hands them back as, decimals included.
        typed_result = await call(
            "db.execute_sql",
            {
                "connection_id": connection_id,
                "sql": (
                    "SELECT CAST(2 AS DECIMAL(10,2)) AS whole, "
                    "CAST(1.5 AS DECIMAL(10,2)) AS fraction, "
                    "CAST('2026-07-29 12:00:00' AS DATETIME) AS moment"
                ),
            },
        )
        assert typed_result.isError is False
        typed_row = helpers.tool_payload(typed_result)["rows"][0]
        assert typed_row["whole"] == "2.00"
        assert typed_row["fraction"] == "1.50"
        assert typed_row["moment"].startswith("2026-07-29")

        # Multi-statement script: create schema, table and insert rows in one
        # call, exercising db.execute_sql_script.
        schema = "mcp_test_" + uuid.uuid4().hex
        script = (
            f"CREATE SCHEMA `{schema}`;"
            f"CREATE TABLE `{schema}`.`items` "
            f"(id INT PRIMARY KEY, name VARCHAR(50));"
            f"INSERT INTO `{schema}`.`items` (id, name) "
            f"VALUES (1, 'a'), (2, 'b'), (3, 'c');"
        )
        script_result = await call(
            "db.execute_sql_script",
            {"connection_id": connection_id, "sql_script": script},
        )
        assert script_result.isError is False
        statements = helpers.tool_payload(script_result)
        assert isinstance(statements, list) and len(statements) == 3
        # The INSERT (third statement) affected three rows.
        assert statements[2]["affected_items_count"] == 3

        # db.list_schemas sees the freshly created schema as a user schema, plus
        # the server's own system schemas.
        schemas_result = await call(
            "db.list_schemas", {"connection_id": connection_id}
        )
        assert schemas_result.isError is False
        schemas = helpers.tool_payload(schemas_result)
        assert isinstance(schemas, list)
        by_name = {entry["schema_name"]: entry for entry in schemas}
        assert by_name[schema]["schema_type"] == "User Schema"
        assert by_name["mysql"]["schema_type"] == "System Schema"
        assert (
            by_name["information_schema"]["schema_type"]
            == "System Information Schema"
        )

        # One object of every type db.list_objects knows about. All are single
        # statements without embedded semicolons, so split_script handles them
        # without a DELIMITER dance. The trigger sits on `noted` rather than on
        # `items` so it cannot interfere with the row assertions below.
        objects_script = (
            f"CREATE TABLE `{schema}`.`noted` (id INT) COMMENT 'a noted table';"
            f"CREATE TABLE `{schema}`.`versioned` (id INT) WITH SYSTEM VERSIONING;"
            f"CREATE TABLE `{schema}`.`orders` "
            f"(id INT PRIMARY KEY, item_id INT NOT NULL, "
            f"CONSTRAINT fk_orders_item FOREIGN KEY (item_id) "
            f"REFERENCES `{schema}`.`items` (id));"
            f"CREATE VIEW `{schema}`.`item_ids` AS "
            f"SELECT id FROM `{schema}`.`items`;"
            f"CREATE FUNCTION `{schema}`.`answer`(bonus INT) RETURNS INT "
            f"COMMENT 'the answer' RETURN 42 + bonus;"
            f"CREATE PROCEDURE `{schema}`.`noop`(OUT total INT, step INT DEFAULT 1) "
            f"SET total = step;"
            f"CREATE SEQUENCE `{schema}`.`counter`;"
            f"CREATE TRIGGER `{schema}`.`noted_bi` BEFORE INSERT "
            f"ON `{schema}`.`noted` FOR EACH ROW SET NEW.id = NEW.id;"
            f"CREATE EVENT `{schema}`.`nightly` ON SCHEDULE EVERY 1 DAY "
            f"DO SELECT 1;"
        )
        objects_result = await call(
            "db.execute_sql_script",
            {"connection_id": connection_id, "sql_script": objects_script},
        )
        assert objects_result.isError is False
        assert len(helpers.tool_payload(objects_result)) == 9

        async def list_objects(object_type=None):
            """Calls db.list_objects, omitting object_type when not given.

            Normalizes the payload back into a list: tool_payload yields None
            for an empty list and the bare entry for a single-entry one.
            """
            arguments = {"connection_id": connection_id, "schema_name": schema}
            if object_type is not None:
                arguments["object_type"] = object_type
            result = await call("db.list_objects", arguments)
            assert result.isError is False
            payload = helpers.tool_payload(result)
            if payload is None:
                return []
            return payload if isinstance(payload, list) else [payload]

        # object_type defaults to table: base tables and the system-versioned
        # table, but neither the view nor the sequence.
        default_tables = await list_objects()
        assert [entry["name"] for entry in default_tables] == [
            "items",
            "noted",
            "orders",
            "versioned",
        ]
        assert default_tables == await list_objects("table")
        # Tables carry their comment.
        comments = {entry["name"]: entry["comment"] for entry in default_tables}
        assert comments["noted"] == "a noted table"

        # Views are listed separately, also with a comment column.
        views = await list_objects("view")
        assert [entry["name"] for entry in views] == ["item_ids"]
        assert "comment" in views[0]

        # Routines are split by ROUTINE_TYPE, and carry a name only.
        assert await list_objects("function") == [{"name": "answer"}]
        assert await list_objects("procedure") == [{"name": "noop"}]

        # Triggers and events likewise.
        assert await list_objects("trigger") == [{"name": "noted_bi"}]
        assert await list_objects("event") == [{"name": "nightly"}]

        # Sequences carry their value data type instead of a comment.
        sequences = await list_objects("sequence")
        assert [entry["name"] for entry in sequences] == ["counter"]
        assert "bigint" in sequences[0]["datatype"].lower()

        # The object type is matched case-insensitively, an unknown one errors
        # out and an unknown schema is simply empty.
        assert await list_objects("TABLE") == default_tables
        bad_type = await call(
            "db.list_objects",
            {
                "connection_id": connection_id,
                "schema_name": schema,
                "object_type": "sequences",
            },
        )
        assert bad_type.isError is True
        missing_schema = await call(
            "db.list_objects",
            {"connection_id": connection_id, "schema_name": "no_such_schema_here"},
        )
        assert missing_schema.isError is False
        assert (helpers.tool_payload(missing_schema) or []) == []

        async def get_details(object_name, object_type=None):
            """Calls db.get_object_details, omitting object_type if not given."""
            arguments = {
                "connection_id": connection_id,
                "schema_name": schema,
                "object_name": object_name,
            }
            if object_type is not None:
                arguments["object_type"] = object_type
            result = await call("db.get_object_details", arguments)
            assert result.isError is False
            return helpers.tool_payload(result)

        # The referenced side of the foreign key: `items` is described with its
        # own columns and primary key, and sees the incoming 1:n reference.
        items_details = await get_details("items")
        assert items_details["basic"] == {
            "schema": schema,
            "name": "items",
            "type": "table",
            "comment": "",
        }
        item_columns = items_details["columns"]
        assert [entry["name"] for entry in item_columns] == ["id", "name"]
        # The flag columns are SQL comparisons, so they arrive as 1/0 rather
        # than as JSON booleans.
        assert item_columns[0]["is_primary"]
        assert item_columns[0]["not_null"]
        assert item_columns[0]["datatype"].startswith("int")
        assert not item_columns[1]["is_primary"]

        # Only `items`' own PRIMARY constraint, on its own `id` column: joining
        # the constraint tables on the schema alone would drag in the primary
        # keys of every other table in the schema.
        assert items_details["constraints"] == [
            {
                "constraint_name": "PRIMARY",
                "constraint_type": "PRIMARY KEY",
                "column_name": "id",
            }
        ]

        # The incoming reference is reported as 1:n with an expanded mapping.
        assert len(items_details["references"]) == 1
        incoming = items_details["references"][0]
        assert incoming["name"] == "orders"
        assert incoming["ref_column_names"] == "item_id"
        assert incoming["reference_mapping"]["kind"] == "1:n"
        assert incoming["reference_mapping"]["to_many"]
        assert incoming["reference_mapping"]["referenced_table"] == "orders"
        assert incoming["reference_mapping"]["column_mapping"] == [
            {"base": "id", "ref": "item_id"}
        ]

        # The referencing side sees the same foreign key as an outgoing n:1.
        orders_details = await get_details("orders", "table")
        outgoing = [
            entry
            for entry in orders_details["references"]
            if entry["reference_mapping"]["kind"] == "n:1"
        ]
        assert len(outgoing) == 1
        assert outgoing[0]["name"] == "items"
        assert not outgoing[0]["reference_mapping"]["to_many"]
        assert outgoing[0]["reference_mapping"]["referenced_table"] == "items"
        assert outgoing[0]["reference_mapping"]["column_mapping"] == [
            {"base": "item_id", "ref": "id"}
        ]
        # Both constraints of `orders` are reported, the primary key and the
        # foreign key.
        assert {
            (entry["constraint_type"], entry["column_name"])
            for entry in orders_details["constraints"]
        } == {("PRIMARY KEY", "id"), ("FOREIGN KEY", "item_id")}

        # A view is described by its columns alone: no constraints, no
        # references.
        view_details = await get_details("item_ids", "view")
        assert view_details["basic"]["type"] == "view"
        assert sorted(view_details) == ["basic", "columns"]
        assert [entry["name"] for entry in view_details["columns"]] == ["id"]

        # A table comment is reported as part of the basic data.
        assert (await get_details("noted"))["basic"]["comment"] == "a noted table"

        # A function reports its parameters and the type it returns, and the
        # RETURNS clause is not mistaken for a parameter.
        function_details = await get_details("answer", "function")
        assert function_details["basic"]["comment"] == "the answer"
        assert function_details["parameters"] == [
            {
                "name": "bonus",
                "mode": "IN",
                "datatype": "int(11)",
                "parameter_default": None,
            }
        ]
        assert function_details["returns"] == "int(11)"

        # A procedure reports its parameters with their mode and default, and
        # returns nothing.
        procedure_details = await get_details("noop", "procedure")
        assert procedure_details["parameters"] == [
            {
                "name": "total",
                "mode": "OUT",
                "datatype": "int(11)",
                "parameter_default": None,
            },
            {
                "name": "step",
                "mode": "IN",
                "datatype": "int(11)",
                "parameter_default": "1",
            },
        ]
        assert procedure_details["returns"] is None

        # A sequence reports its value type and stepping.
        sequence_details = (await get_details("counter", "sequence"))["details"]
        assert sequence_details["data_type"] == "bigint"
        # The information_schema reports the sequence bounds as text, not as
        # numbers, so they arrive as strings.
        assert int(sequence_details["start_value"]) == 1
        assert int(sequence_details["increment"]) == 1

        # A trigger reports what it fires on, and its body.
        trigger_details = (await get_details("noted_bi", "trigger"))["details"]
        assert trigger_details["event_manipulation"] == "INSERT"
        assert trigger_details["action_timing"] == "BEFORE"
        assert trigger_details["action_orientation"] == "ROW"
        assert trigger_details["table_name"] == "noted"
        assert "NEW.id" in trigger_details["action_statement"]
        # A DATETIME has no JSON representation, so it arrives as a string.
        assert isinstance(trigger_details["created"], str)

        # An event reports its schedule, its status and its body.
        event_details = (await get_details("nightly", "event"))["details"]
        assert event_details["event_type"] == "RECURRING"
        assert str(event_details["interval_value"]) == "1"
        assert event_details["interval_field"] == "DAY"
        assert event_details["event_definition"] == "SELECT 1"
        assert isinstance(event_details["starts"], str)

        # An object that does not exist is an error rather than an empty
        # description, whatever its type.
        for arguments in (
            {"object_name": "no_such_table_here"},
            {"object_name": "no_such_routine", "object_type": "procedure"},
            {"object_name": "items", "object_type": "sequence"},
        ):
            rejected = await call(
                "db.get_object_details",
                {
                    "connection_id": connection_id,
                    "schema_name": schema,
                    **arguments,
                },
            )
            assert rejected.isError is True

        # The same script read from a file on disk (within an allowed path)
        # adds two more rows, exercising the file_path parameter.
        script_path = os.path.join(script_dir, f"{schema}.sql")
        with open(script_path, "w", encoding="utf-8") as script_file:
            script_file.write(
                f"INSERT INTO `{schema}`.`items` (id, name) VALUES (4, 'd');"
                f"INSERT INTO `{schema}`.`items` (id, name) VALUES (5, 'e');"
            )
        file_result = await call(
            "db.execute_sql_script",
            {"connection_id": connection_id, "file_path": script_path},
        )
        assert file_result.isError is False
        file_statements = helpers.tool_payload(file_result)
        assert isinstance(file_statements, list) and len(file_statements) == 2
        assert file_statements[0]["affected_items_count"] == 1
        assert file_statements[1]["affected_items_count"] == 1

        # A file outside the allowed paths is rejected.
        denied_result = await call(
            "db.execute_sql_script",
            {"connection_id": connection_id, "file_path": "/etc/hosts"},
        )
        assert denied_result.isError is True

        try:
            # Aggregate SELECT.
            count_result = await call(
                "db.execute_sql",
                {
                    "connection_id": connection_id,
                    "sql": f"SELECT COUNT(*) AS cnt FROM `{schema}`.`items`",
                },
            )
            assert count_result.isError is False
            assert helpers.tool_payload(count_result)["rows"][0]["cnt"] == 5

            # Ordered SELECT returns the rows in insertion order.
            rows_result = await call(
                "db.execute_sql",
                {
                    "connection_id": connection_id,
                    "sql": f"SELECT id, name FROM `{schema}`.`items` ORDER BY id",
                },
            )
            rows = helpers.tool_payload(rows_result)["rows"]
            assert [row["name"] for row in rows] == ["a", "b", "c", "d", "e"]

            # Parameterized SELECT binds the ? placeholder.
            one_result = await call(
                "db.execute_sql",
                {
                    "connection_id": connection_id,
                    "sql": f"SELECT name FROM `{schema}`.`items` WHERE id = ?",
                    "params": [2],
                },
            )
            one_rows = helpers.tool_payload(one_result)["rows"]
            assert len(one_rows) == 1 and one_rows[0]["name"] == "b"
        finally:
            # Drop the test schema.
            drop_result = await call(
                "db.execute_sql",
                {"connection_id": connection_id, "sql": f"DROP SCHEMA `{schema}`"},
            )
            assert drop_result.isError is False

        # Close the connection.
        close_result = await call("db.close", {"connection_id": connection_id})
        assert close_result.isError is False

        # The connection id is no longer usable after closing.
        reused = await call(
            "db.execute_sql",
            {"connection_id": connection_id, "sql": "SELECT 1"},
        )
        assert reused.isError is True


def test_db_connect_execute_and_close(sandbox):
    """Exercises the db connection/SQL tools against the shared sandbox.

    Runs after test_sandbox_deploy and before test_sandbox_shutdown; the sandbox
    connection is already registered in the secret store by the fixture.
    """
    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    asyncio.run(_db_flow(sandbox.uri, sandbox.sandbox_dir))
