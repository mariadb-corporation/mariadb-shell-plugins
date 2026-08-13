# Copyright (c) 2026, MariaDB plc.
#
# SPDX-License-Identifier: GPL-2.0-only
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
``lib.server._serve_streamable_http``) works end to end, that the peer address
the connection binding depends on cannot be forged with a header, and that a
request naming a host the server does not answer to is refused.
"""

# cSpell:ignore mysqlsh MariaDB streamable uvicorn

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


def test_streamable_http_connect_execute_and_close(sandbox):
    """A connection opened over HTTP is usable by the client that opened it.

    Over HTTP a connection is bound to the address of the client that opened
    it, so this drives the whole db flow - connect, run a statement, close -
    through the transport that applies the binding, against the shared sandbox
    deployed by ``test_sandbox_deploy``.
    """
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        async with helpers.http_session(function_groups=["db"]) as call:
            connect_result = await call("db.connect", {"uri": sandbox.uri})
            assert connect_result.is_error is False, helpers.tool_payload(
                connect_result
            )
            connection_id = helpers.tool_payload(connect_result)
            assert isinstance(connection_id, str) and connection_id != ""

            # The client's own connection works for it across calls, i.e. the
            # address it is bound to is stable over the HTTP session.
            for _ in range(2):
                result = await call(
                    "db.execute_sql",
                    {"connection_id": connection_id, "sql": "SELECT 1 AS one"},
                )
                assert result.is_error is False, helpers.tool_payload(result)
                assert helpers.tool_payload(result)["rows"] == [{"one": 1}]

            close_result = await call("db.close", {"connection_id": connection_id})
            assert close_result.is_error is False

            # The connection id is no longer usable after closing.
            reused = await call(
                "db.execute_sql",
                {"connection_id": connection_id, "sql": "SELECT 1"},
            )
            assert reused.is_error is True

    asyncio.run(_run())


def test_streamable_http_ignores_a_forwarded_for_header(sandbox):
    """A client cannot choose the address its connection is bound to.

    The address a connection is bound to must be the peer address of the
    connection the request arrived on, and nothing a client can set. Uvicorn's
    ProxyHeadersMiddleware is enabled by default and rewrites exactly that peer
    address from ``X-Forwarded-For`` for any request coming from one of its
    trusted addresses - which, with the default loopback bind, means every
    request. ``lib.server._serve_streamable_http`` therefore serves the app on
    a uvicorn server configured with ``proxy_headers=False``.

    One client, one MCP session: the connection is opened with no header, then
    the header appears on the calls that use it. Since neither the real peer
    address nor the session has changed, the connection must still be usable.
    With the middleware left enabled the later calls are seen as coming from
    ``10.99.99.99`` and are refused.

    The header is added mid-session rather than by a second client because a
    second client would have a session id of its own, which the binding refuses
    on its own merits - that is what
    ``test_streamable_http_binds_a_connection_to_its_mcp_session`` covers.
    """
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        client = helpers.mcp_http_client()

        async with helpers.http_server(function_groups=["db"]) as url:
            async with helpers.http_client_session(url, http_client=client) as call:
                connect_result = await call("db.connect", {"uri": sandbox.uri})
                assert connect_result.is_error is False, helpers.tool_payload(
                    connect_result
                )
                connection_id = helpers.tool_payload(connect_result)

                # From here on every request claims to come from elsewhere.
                client.headers["X-Forwarded-For"] = "10.99.99.99"

                result = await call(
                    "db.execute_sql",
                    {"connection_id": connection_id, "sql": "SELECT 1 AS one"},
                )
                assert result.is_error is False, helpers.tool_payload(result)
                assert helpers.tool_payload(result)["rows"] == [{"one": 1}]

                close_result = await call(
                    "db.close", {"connection_id": connection_id}
                )
                assert close_result.is_error is False

    asyncio.run(_run())


def test_streamable_http_binds_a_connection_to_its_mcp_session(sandbox):
    """A second client on the same address cannot use the first's connection.

    Both clients here really do come from 127.0.0.1 - they are the same process
    - so the address half of the binding cannot tell them apart. This is the
    situation of every client behind one NAT or reverse proxy, and of every
    process on the machine while the server is bound to loopback as it is by
    default. What separates them is the MCP session id, which the server hands
    out on initialize and which the other client was never given.
    """
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        async with helpers.http_server(function_groups=["db"]) as url:
            async with helpers.http_client_session(url) as call:
                connect_result = await call("db.connect", {"uri": sandbox.uri})
                assert connect_result.is_error is False, helpers.tool_payload(
                    connect_result
                )
                connection_id = helpers.tool_payload(connect_result)

                # A second session, same address, its own session id.
                async with helpers.http_client_session(url) as other_call:
                    # It can open a connection of its own - there is no
                    # authentication, and that is a separate matter - but it
                    # cannot touch this one.
                    for tool in ("db.execute_sql", "db.close"):
                        arguments = {"connection_id": connection_id}
                        if tool == "db.execute_sql":
                            arguments["sql"] = "SELECT 1"

                        result = await other_call(tool, arguments)
                        assert result.is_error is True, helpers.tool_payload(result)

                # The owner still has it, i.e. the refusals changed nothing.
                result = await call(
                    "db.execute_sql",
                    {"connection_id": connection_id, "sql": "SELECT 1 AS one"},
                )
                assert result.is_error is False, helpers.tool_payload(result)
                assert helpers.tool_payload(result)["rows"] == [{"one": 1}]

                await call("db.close", {"connection_id": connection_id})

    asyncio.run(_run())


def _initialize_request():
    """Returns an MCP initialize request body, the first call of any session."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "mcp-pytest", "version": "0"},
        },
    }


def test_streamable_http_rejects_a_foreign_host_header(stored_connections):
    """A request naming a host the server does not answer to is refused.

    This is DNS-rebinding protection. The server has no authentication, so a
    page in a browser that can reach the port could otherwise drive the
    database tools: an attacker resolves their own name to the address the
    server is on, which makes their page same-origin with it and drops the
    cross-origin restrictions. What gives it away is the Host header, which the
    browser sets to the name the page was loaded from.

    The server is started with ``--host=LOCALHOST`` on purpose. It still binds
    loopback, but the SDK only auto-enables the protection for the exact strings
    "127.0.0.1", "localhost" and "::1" - so with this spelling the SDK would
    leave it off entirely, and only the settings
    ``lib.server._transport_security_settings`` passes explicitly turn it on.
    """
    pytest.importorskip("mcp")
    httpx = pytest.importorskip("httpx2")

    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }

    async def _run():
        async with helpers.http_server(
            function_groups=["db"], bind_host="LOCALHOST"
        ) as url:
            async with httpx.AsyncClient() as client:
                # A page at http://evil.example.com whose name resolves here.
                forged = await client.post(
                    url,
                    json=_initialize_request(),
                    headers={**headers, "Host": "evil.example.com"},
                )
                assert forged.status_code == 421, forged.text
                assert "Host" in forged.text

                # An Origin the server does not serve is refused as well, even
                # when the Host is right.
                cross_origin = await client.post(
                    url,
                    json=_initialize_request(),
                    headers={**headers, "Origin": "http://evil.example.com"},
                )
                assert cross_origin.status_code == 403, cross_origin.text

                # The control: the same request as any real client makes it,
                # with the Host httpx derives from the URL and no Origin at all,
                # is served. So the protection is not simply refusing
                # everything.
                allowed = await client.post(
                    url, json=_initialize_request(), headers=headers
                )
                assert allowed.status_code == 200, allowed.text

    asyncio.run(_run())
