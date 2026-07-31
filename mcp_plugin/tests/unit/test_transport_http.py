# Copyright (c) 2026, MariaDB plc.
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

"""Tests for the default streamable-http transport.

These complement the stdio-based tests: they launch the server with
``--transport=streamable-http`` and drive it with the MCP streamable-http
client, verifying that the HTTP serving path (``lib.server.start`` ->
``mcp_server.run(transport="streamable-http")``) works end to end.
"""

# cSpell:ignore mysqlsh MariaDB streamable

import asyncio

import pytest

import mcp_plugin.tests.unit.helpers as helpers


def test_streamable_http_lists_connections(stored_connections):
    """The server serves tools over streamable-http and returns real results.

    ``db.list_connections`` is a side-effect-free tool that needs neither a
    database connection nor a filesystem path, so it is a clean probe that the
    HTTP transport carries a request to a tool and its result back. The
    ``stored_connections`` fixture seeds a known set of connection URIs to
    assert against.
    """
    pytest.importorskip("mcp")

    async def _run():
        async with helpers.http_session(function_groups=["db"]) as call:
            result = await call("db.list_connections")
            assert result.is_error is False, helpers.tool_payload(result)

            uris = helpers.tool_payload(result)
            assert isinstance(uris, list)
            # Every seeded connection must be reported back over the transport.
            for uri in stored_connections:
                assert uri in uris

    asyncio.run(_run())
