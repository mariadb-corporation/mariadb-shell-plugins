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
import uuid

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers


async def _db_flow(uri):
    """Drives connect -> execute_sql -> execute_sql_script -> close."""
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
            assert helpers.tool_payload(count_result)["rows"][0]["cnt"] == 3

            # Ordered SELECT returns the rows in insertion order.
            rows_result = await call(
                "db.execute_sql",
                {
                    "connection_id": connection_id,
                    "sql": f"SELECT id, name FROM `{schema}`.`items` ORDER BY id",
                },
            )
            rows = helpers.tool_payload(rows_result)["rows"]
            assert [row["name"] for row in rows] == ["a", "b", "c"]

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

    asyncio.run(_db_flow(sandbox.uri))
