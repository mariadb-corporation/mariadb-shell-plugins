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

"""Tests for taking the client address from trusted reverse proxies.

Behind a reverse proxy every request arrives from the proxy, so the server
cannot tell its clients apart by address: they share the sign-in rate limit,
the throttle on refused tokens and the allowed client networks. Naming the
proxy with ``--trustedProxies`` has the server read the client from the
proxy's ``X-Forwarded-For`` header - from that proxy only, as any client can
send the header (see ``lib.server.TrustedProxyMiddleware``).
"""

# cSpell:ignore mysqlsh MariaDB streamable

import asyncio
import ipaddress
import subprocess
from types import SimpleNamespace

import pytest

import mysqlsh

import mcp_plugin.tests.unit.helpers as helpers
from mcp_plugin.lib import auth, general, server

# A reverse proxy in front of the server, on a network of its own, and a client
# beyond it.
PROXY = "192.0.2.3"
PROXY_NETWORK = "192.0.2.0/24"
CLIENT = "203.0.113.7"


def _forwarded(peer, *values, networks=(PROXY_NETWORK,), scope_type="http"):
    """Returns the ASGI scope the app behind the middleware is handed.

    Args:
        peer (str): The TCP peer the request arrives from.
        *values (str): The ``X-Forwarded-For`` header lines, in order.
        networks: The trusted proxies.
        scope_type (str): The ASGI scope type.

    Returns:
        The scope the wrapped app saw, and the one passed in.
    """
    seen = []

    async def app(scope, receive, send):
        seen.append(scope)

    headers = [(b"host", b"mcp.example.com")] + [
        (b"x-forwarded-for", value.encode("latin-1")) for value in values
    ]
    scope = {"type": scope_type, "path": "/mcp", "client": (peer, 40000),
             "headers": headers}
    middleware = server.TrustedProxyMiddleware(
        app, server.parse_trusted_proxies(list(networks)))

    asyncio.run(middleware(scope, None, None))

    return seen[0], scope


def _client_of(peer, *values, **keywords):
    """Returns the client address the app behind the middleware sees."""
    return _forwarded(peer, *values, **keywords)[0]["client"][0]


async def _status(app, peer, forwarded_for, path="/mcp", headers=()):
    """Sends an HTTP request through an ASGI app; returns the status it answers."""
    sent = []

    async def send(message):
        sent.append(message)

    await app({"type": "http", "path": path, "client": (peer, 40000),
               "headers": [(b"x-forwarded-for", forwarded_for.encode()), *headers]},
              None, send)
    return sent[0]["status"]


def test_an_untrusted_peer_cannot_forge_its_address():
    """The header of a request that did not come from a trusted proxy is ignored."""
    seen, original = _forwarded("198.51.100.9", "198.51.100.66")

    assert seen["client"] == ("198.51.100.9", 40000)
    # Left as it was, not even copied.
    assert seen is original


def test_a_trusted_proxy_names_the_client():
    """One hop: the proxy's own peer is the client."""
    seen, original = _forwarded(PROXY, CLIENT)

    assert seen["client"] == (CLIENT, 0)
    # A copy is rewritten; the server's own scope is left alone.
    assert seen is not original
    assert original["client"] == (PROXY, 40000)
    assert seen["headers"] == original["headers"]


def test_a_request_without_the_header_is_left_alone():
    """A trusted proxy that sent no header is seen as itself."""
    seen, original = _forwarded(PROXY)

    assert seen is original


def test_a_chain_of_trusted_proxies_is_followed_back_to_the_client():
    """Read from the right, past every trusted proxy, and no further.

    What a client wrote into the header itself is to the left of what the
    first proxy added, so it is never reached.
    """
    networks = (PROXY_NETWORK, "198.51.100.10")

    # client -> 198.51.100.10 (an outer proxy) -> 192.0.2.3 -> server
    assert _client_of(PROXY, f"{CLIENT}, 198.51.100.10", networks=networks) == CLIENT

    # The client forged an entry of its own: it stays out of reach.
    assert _client_of(
        PROXY, f"198.51.100.66, {CLIENT}, 198.51.100.10", networks=networks
    ) == CLIENT

    # Not past the first address that is not a trusted proxy, even when it is
    # followed by one: that untrusted hop could have written anything before it.
    assert _client_of(PROXY, f"{CLIENT}, 198.51.100.1", networks=networks) == (
        "198.51.100.1"
    )


def test_a_chain_of_nothing_but_trusted_proxies_names_its_leftmost():
    """With every hop a trusted proxy, the first of them is the client."""
    assert _client_of(PROXY, "192.0.2.20, 192.0.2.5") == "192.0.2.20"


def test_an_entry_that_is_not_an_address_leaves_the_request_alone():
    """Fail closed: the request is seen as coming from the proxy."""
    for value in (
        "unknown",
        "_hidden",
        f"{CLIENT}:51234",  # with a port
        "[2001:db8::1]:443",
        f"{CLIENT},",  # an empty entry
        "",
        # Before a valid client is reached from the right.
        f"{CLIENT}, unknown",
    ):
        seen, original = _forwarded(PROXY, value)
        assert seen is original, value

    # To the left of the client it is never reached, so it does not matter.
    assert _client_of(PROXY, f"unknown, {CLIENT}") == CLIENT


def test_every_header_line_is_read_in_order():
    """Several header lines are one list, the first line leftmost."""
    networks = (PROXY_NETWORK, "198.51.100.10")

    assert _client_of(PROXY, "198.51.100.66", CLIENT, "198.51.100.10",
                      networks=networks) == CLIENT
    assert _client_of(PROXY, f"198.51.100.66, {CLIENT}", "198.51.100.10",
                      networks=networks) == CLIENT


def test_ipv6_proxies_and_clients():
    """IPv6 works as IPv4 does, and an IPv4-mapped peer is its IPv4 address."""
    networks = ("2001:db8:1::/64", PROXY_NETWORK)

    assert _client_of("2001:db8:1::3", "2001:db8:ffff::7", networks=networks) == (
        "2001:db8:ffff::7"
    )
    assert _client_of("2001:db8:2::3", "2001:db8:ffff::7", networks=networks) == (
        "2001:db8:2::3"
    )

    # A proxy connecting to a dual-stack socket over IPv4.
    assert _client_of(f"::ffff:{PROXY}", CLIENT, networks=networks) == CLIENT
    # And a mapped client is written as the IPv4 address it is.
    assert _client_of(PROXY, f"::ffff:{CLIENT}", networks=networks) == CLIENT
    # A mapped address that is not in a trusted network is not trusted.
    assert _client_of("::ffff:198.51.100.9", CLIENT, networks=networks) == (
        "::ffff:198.51.100.9"
    )


def test_a_loopback_proxy_is_trusted_over_either_ip_version():
    """127.0.0.1 and ::1 are the same host, as everywhere else in the server."""
    assert _client_of("::1", CLIENT, networks=("127.0.0.1",)) == CLIENT
    assert _client_of("127.0.0.1", CLIENT, networks=("::1",)) == CLIENT


def test_websockets_are_rewritten_and_everything_else_passes_through():
    """Only requests have a client; lifespan events are left alone."""
    assert _client_of(PROXY, CLIENT, scope_type="websocket") == CLIENT

    seen, original = _forwarded(PROXY, CLIENT, scope_type="lifespan")
    assert seen is original


def test_the_trusted_proxies_are_checked():
    """Anything that is not an address or network, or trusts everyone, is refused."""
    assert server.parse_trusted_proxies(None) == []
    assert server.parse_trusted_proxies("") == []
    assert server.parse_trusted_proxies(" 192.0.2.3 , 192.0.2.0/24,,2001:db8::/32 ") == [
        ipaddress.ip_network("192.0.2.3/32"),
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("2001:db8::/32"),
    ]
    # Host bits are allowed, as the network they are in.
    assert server.parse_trusted_proxies(["192.0.2.3/24"]) == [
        ipaddress.ip_network("192.0.2.0/24")]

    with pytest.raises(mysqlsh.Error) as error:
        server.parse_trusted_proxies("192.0.2.3, proxy-host, 192.0.2.0/33")
    # Every bad entry is named, and only those.
    assert "proxy-host" in str(error.value)
    assert "192.0.2.0/33" in str(error.value)
    assert "192.0.2.3," not in str(error.value)

    for everyone in ("0.0.0.0/0", "::/0", "192.0.2.3, 0.0.0.0/0"):
        with pytest.raises(mysqlsh.Error, match="cannot trust every address"):
            server.parse_trusted_proxies(everyone)


def test_start_refuses_trusted_proxies_it_cannot_use():
    """Refused before anything is built or recorded, like the other checks."""
    for trusted_proxies, transport, expected in (
        ("not-an-address", general.TRANSPORT_STREAMABLE_HTTP, "not-an-address"),
        ("::/0", general.TRANSPORT_STREAMABLE_HTTP, "every address"),
        # stdio has no proxy in front of it, and no client address at all.
        (PROXY_NETWORK, general.TRANSPORT_STDIO, "only applies to --transport"),
    ):
        with pytest.raises(mysqlsh.Error, match=expected):
            server.start("127.0.0.1", 8080, transport, ["db"],
                         trusted_proxies=trusted_proxies)

        assert general.is_http_transport() is False


def test_start_serves_with_the_trusted_proxies_and_says_so(monkeypatch, capsys):
    """The networks reach the server, and the log says which they are."""
    pytest.importorskip("mcp")

    from mcp_plugin.lib import db_functions

    served = {}
    monkeypatch.setattr(db_functions, "start_connection_reaper", lambda: None)
    monkeypatch.setattr(db_functions, "stop_connection_reaper", lambda: None)
    monkeypatch.setattr(
        server, "_serve_streamable_http",
        lambda *arguments, **keywords: served.update(keywords),
    )

    try:
        server.start("127.0.0.1", 8080, general.TRANSPORT_STREAMABLE_HTTP, ["db"],
                     trusted_proxies=f"{PROXY_NETWORK},2001:db8::1")

        assert served["trusted_proxies"] == [
            ipaddress.ip_network(PROXY_NETWORK), ipaddress.ip_network("2001:db8::1")]
        log = capsys.readouterr().err
        assert "[mcp] server:" in log
        assert "X-Forwarded-For" in log
        assert f"{PROXY_NETWORK}, 2001:db8::1/128" in log

        # Without the option nothing is trusted, and nothing is said.
        served.clear()
        server.start("127.0.0.1", 8080, general.TRANSPORT_STREAMABLE_HTTP, ["db"])

        assert served["trusted_proxies"] == []
        assert "X-Forwarded-For" not in capsys.readouterr().err
    finally:
        general.set_active_transport(None)


# --- what the rest of the server sees ------------------------------------------


def test_the_client_address_of_a_tool_call_is_the_forwarded_one():
    """``general.get_client_address`` reads the rewritten scope."""
    from starlette.requests import Request

    def address_seen(peer):
        scope = _forwarded(peer, CLIENT)[0]
        ctx = SimpleNamespace(request_context=SimpleNamespace(request=Request(scope)))
        return general.get_client_address(ctx)

    assert address_seen(PROXY) == CLIENT
    assert address_seen("198.51.100.9") == "198.51.100.9"


def test_the_throttles_count_each_client_behind_the_proxy_on_its_own():
    """One client's refused tokens or sign-ins do not lock out the others.

    The middleware is outermost, as ``lib.server._serve_streamable_http`` wraps
    it, so the throttle and the rate limit inside it see the forwarded address.
    """
    async def refuse(scope, receive, send):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    throttle = auth.AuthFailureThrottle(refuse, max_failures=2)
    limit = auth.AuthorizeRateLimit(throttle, max_requests=2)
    app = server.TrustedProxyMiddleware(limit, server.parse_trusted_proxies(PROXY_NETWORK))

    def status(path, forwarded_for):
        token = [(b"authorization", b"Bearer not-a-key")] if path == "/mcp" else []
        return _status(app, PROXY, forwarded_for, path, token)

    async def scenario():
        assert [await status("/mcp", CLIENT) for _ in range(3)] == [401, 401, 429]
        assert await status("/mcp", "203.0.113.8") == 401

        # The sign-in starts carry no token, so only the rate limit counts them.
        assert [await status("/authorize", CLIENT) for _ in range(3)] == [
            401, 401, 429]
        assert await status("/authorize", "203.0.113.9") == 401

    asyncio.run(scenario())

    assert throttle.is_throttled(CLIENT, "-")
    assert not throttle.is_throttled(PROXY, "-")
    assert not throttle.is_throttled("203.0.113.8", "-")


def test_the_allowed_client_networks_see_the_forwarded_address():
    """``--allowedClientNetworks`` can name the clients, not just the proxy."""
    async def accept(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = server.TrustedProxyMiddleware(
        auth.ClientNetworkMiddleware(accept, ["203.0.113.0/24"]),
        server.parse_trusted_proxies(PROXY_NETWORK),
    )

    async def scenario():
        assert await _status(app, PROXY, CLIENT) == 200
        assert await _status(app, PROXY, "198.51.100.9") == 403
        # Claiming an allowed address from anywhere but the proxy gets nowhere.
        assert await _status(app, "198.51.100.9", CLIENT) == 403

    asyncio.run(scenario())


def test_the_command_line_takes_the_option(stored_connections):
    """``--trusted-proxies`` reaches the server from a real command line.

    The tests above call the plugin in-process, which proves nothing about how
    the shell's CLI spells and passes the option: a comma-separated list here.
    """
    pytest.importorskip("mcp")

    async def _run():
        async with helpers.http_server(
            function_groups=["db"],
            extra_args=[f"--trusted-proxies=127.0.0.1,{PROXY_NETWORK},2001:db8::/32"],
        ) as url:
            client = helpers.mcp_http_client()
            client.headers["X-Forwarded-For"] = CLIENT
            async with helpers.http_client_session(url, http_client=client) as call:
                result = await call("db.list_connections")
                assert result.is_error is False, helpers.tool_payload(result)

    asyncio.run(_run())

    # One refusal proves the CLI passes the option's errors on; what is refused
    # is covered in-process above.
    completed = subprocess.run(
        [helpers.shell_binary(), "--quiet-start=2", "--", "mcp", "start-server",
         f"--port={helpers.find_free_port()}", "--trusted-proxies=::/0"],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert completed.returncode != 0
    assert "cannot trust every address" in completed.stdout + completed.stderr


def test_a_server_behind_a_trusted_proxy_binds_connections_to_the_client(sandbox):
    """The real server, started with the option on its command line.

    The counterpart of ``test_streamable_http_ignores_a_forwarded_for_header``:
    with loopback named as a trusted proxy, the header that server ignores is
    believed, so the client it names is a different one from the client that
    opened the connection, and the connection is refused to it.
    """
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        client = helpers.mcp_http_client()

        async with helpers.http_server(
            function_groups=["db"],
            extra_args=[f"--trusted-proxies=127.0.0.1,{PROXY_NETWORK}"],
        ) as url:
            async with helpers.http_client_session(url, http_client=client) as call:
                client.headers["X-Forwarded-For"] = CLIENT
                connect_result = await call("db.connect", {"uri": sandbox.uri})
                assert connect_result.is_error is False, helpers.tool_payload(
                    connect_result
                )
                connection_id = helpers.tool_payload(connect_result)

                arguments = {"connection_id": connection_id, "sql": "SELECT 1 AS one"}
                result = await call("db.execute_sql", arguments)
                assert result.is_error is False, helpers.tool_payload(result)

                # The same session, forwarded for somebody else.
                client.headers["X-Forwarded-For"] = "203.0.113.8"
                result = await call("db.execute_sql", arguments)
                assert result.is_error is True, helpers.tool_payload(result)

                # And back: the refusal changed nothing.
                client.headers["X-Forwarded-For"] = CLIENT
                result = await call("db.execute_sql", arguments)
                assert result.is_error is False, helpers.tool_payload(result)

                await call("db.close", {"connection_id": connection_id})

    asyncio.run(_run())
