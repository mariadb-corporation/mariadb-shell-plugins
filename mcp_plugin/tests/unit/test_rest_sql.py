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

"""End-to-end test for running MRS REST SQL through the db tools.

Executes the MySQL REST Service SQL statement ``CONFIGURE REST METADATA`` against
the shared sandbox (deployed by ``test_sandbox_deploy``) over a single db MCP
stdio session. The statement is not plain server SQL: it is intercepted by the
REST SQL handler that ``mrs_plugin`` registers with the shell (see
``mrs_plugin/script.py``), so it exercises the path where ``db.execute_sql``
routes a statement through a shell SQL handler. A successful run provisions the
``mysql_rest_service_metadata`` schema on the target server.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver mrs

import asyncio

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers

# The schema that CONFIGURE REST METADATA provisions on the target server.
_METADATA_SCHEMA = "mysql_rest_service_metadata"


async def _rest_sql_flow(uri):
    """Drives connect -> CONFIGURE REST METADATA -> verify -> close."""
    async with helpers.mcp_session(["db"]) as call:
        connect_result = await call("db.connect", {"uri": uri})
        assert connect_result.is_error is False
        connection_id = helpers.tool_payload(connect_result)
        assert isinstance(connection_id, str) and connection_id != ""

        async def _metadata_schema_rows():
            result = await call(
                "db.execute_sql",
                {
                    "connection_id": connection_id,
                    "sql": (
                        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                        "WHERE SCHEMA_NAME = ?"
                    ),
                    "params": [_METADATA_SCHEMA],
                },
            )
            assert result.is_error is False
            return helpers.tool_payload(result)["rows"]

        try:
            # The REST metadata schema does not exist on the fresh sandbox yet.
            assert await _metadata_schema_rows() == []

            # Run the REST SQL command. It is handled by the mrs_plugin SQL
            # handler rather than sent verbatim to the server.
            configure_result = await call(
                "db.execute_sql",
                {"connection_id": connection_id, "sql": "CONFIGURE REST METADATA;"},
            )
            assert configure_result.is_error is False

            # Running it provisioned the REST metadata schema.
            assert len(await _metadata_schema_rows()) == 1
        finally:
            close_result = await call("db.close", {"connection_id": connection_id})
            assert close_result.is_error is False


def test_rest_configure_metadata(sandbox):
    """Runs CONFIGURE REST METADATA against the shared sandbox via the db tools.

    Runs after test_sandbox_deploy and before test_sandbox_shutdown; the sandbox
    connection is already registered in the secret store by the fixture.
    """
    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    asyncio.run(_rest_sql_flow(sandbox.uri))
