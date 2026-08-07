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

"""Tests for how the bind address is handled and what is said about it.

The MCP server has no authentication: whoever can reach the port can open the
configured database connections. Listening on loopback is the whole of what
keeps that local, so binding anywhere else has to be called out rather than
left to be discovered - and a browser that can reach the port has to be kept
from driving the tools, which is what the Host/Origin validation is for.
"""

# cSpell:ignore mysqlsh MariaDB

import pytest

from mcp_plugin.lib import general, server


def test_loopback_hosts_are_told_apart_from_reachable_ones():
    """Only addresses that keep the server on this machine count as loopback."""
    for host in ("127.0.0.1", "127.1.2.3", "::1", "[::1]", "localhost", "LocalHost"):
        assert general.is_loopback_host(host) is True, host

    # The wildcards bind every interface. They include loopback, which is
    # exactly why answering "is loopback" for them would be wrong.
    for host in ("0.0.0.0", "::", "[::]", "", None):
        assert general.is_loopback_host(host) is False, host

    # Anything routable, and any name that is not a known local one - a name is
    # deliberately not resolved, so the warning errs towards being shown.
    for host in ("192.0.2.10", "10.0.0.1", "2001:db8::1", "db.example.com"):
        assert general.is_loopback_host(host) is False, host


def test_a_non_loopback_bind_warns_that_there_is_no_authentication(capsys):
    """Binding beyond this machine prints a warning naming the real risk."""
    server._warn_if_reachable_from_the_network("0.0.0.0", 8080)

    warning = capsys.readouterr().err
    assert "WARNING" in warning
    # The warning has to say what is at stake, not merely that a non-default
    # host was used: no authentication, and the stored credentials behind it.
    assert "NO AUTHENTICATION" in warning
    assert "credential" in warning
    assert "0.0.0.0:8080" in warning


def test_the_default_bind_stays_quiet(capsys):
    """A loopback bind is the safe default and says nothing."""
    assert general.is_loopback_host(general.DEFAULT_HOST) is True

    server._warn_if_reachable_from_the_network(
        general.DEFAULT_HOST, general.DEFAULT_PORT
    )

    assert capsys.readouterr().err == ""


# --- DNS-rebinding protection ---------------------------------------------


def test_wildcard_hosts_are_recognized():
    """The "all interfaces" binds have no single name to validate against."""
    for host in ("0.0.0.0", "::", "[::]", "", None):
        assert general.is_wildcard_host(host) is True, host

    for host in ("127.0.0.1", "localhost", "::1", "192.0.2.10", "db.example.com"):
        assert general.is_wildcard_host(host) is False, host


def test_dns_rebinding_protection_is_always_on():
    """It is configured here, not left to the SDK's host string match.

    ``streamable_http_app`` enables it only when the host is exactly
    ``"127.0.0.1"``, ``"localhost"`` or ``"::1"``, compared case-sensitively.
    Every other bind - including loopback spelled differently - would serve with
    no Host or Origin validation whatsoever.
    """
    pytest.importorskip("mcp")

    for host in (
        "127.0.0.1",
        "LOCALHOST",  # the SDK's comparison is case-sensitive
        "[::1]",  # ... and does not expect brackets
        "127.0.0.2",  # ... and only knows the one 127.0.0.0/8 address
        "0.0.0.0",  # ... and gives up entirely on a non-loopback bind
        "192.0.2.10",
    ):
        settings = server._transport_security_settings(host, 8080)
        assert settings.enable_dns_rebinding_protection is True, host
        assert settings.allowed_hosts, host


def test_a_loopback_bind_accepts_every_loopback_spelling():
    """Which loopback name a client dials is the client's choice."""
    pytest.importorskip("mcp")

    # Bound as one spelling, reachable as any of them - so all are accepted.
    for host in ("127.0.0.1", "localhost", "127.0.0.2"):
        settings = server._transport_security_settings(host, 8080)

        for name in ("127.0.0.1", "localhost", "[::1]"):
            assert f"{name}:8080" in settings.allowed_hosts, (host, name)
            # The bare name too, for when the port is the scheme's default.
            assert name in settings.allowed_hosts, (host, name)
            assert f"http://{name}:*" in settings.allowed_origins, (host, name)

        # Nothing else gets in, least of all an attacker's own name.
        assert "evil.example.com" not in settings.allowed_hosts
        assert "evil.example.com:8080" not in settings.allowed_hosts


def test_a_specific_bind_accepts_only_that_host():
    """A server bound to one address answers to that address and no other."""
    pytest.importorskip("mcp")

    settings = server._transport_security_settings("192.0.2.10", 9000)
    assert settings.allowed_hosts == ["192.0.2.10", "192.0.2.10:9000"]

    # Loopback is NOT reachable on a server bound to a single other address,
    # so it is not accepted either.
    assert "127.0.0.1:9000" not in settings.allowed_hosts

    # A bare IPv6 address is bracketed, as a Host header carries it.
    settings = server._transport_security_settings("2001:db8::1", 9000)
    assert settings.allowed_hosts == ["[2001:db8::1]", "[2001:db8::1]:9000"]


def test_extra_allowed_hosts_are_added():
    """A name only a proxy or DNS alias knows has to be given explicitly."""
    pytest.importorskip("mcp")

    settings = server._transport_security_settings(
        "0.0.0.0", 8080, ["mcp.example.com", "mcp.example.com:443", "  "]
    )

    assert "mcp.example.com" in settings.allowed_hosts
    assert "mcp.example.com:443" in settings.allowed_hosts
    assert "https://mcp.example.com:*" in settings.allowed_origins
    # Blank entries are dropped rather than allowing an empty Host.
    assert "" not in settings.allowed_hosts

    # A wildcard bind is reachable at loopback as well as under this machine's
    # own name, so those stay accepted alongside the extra names.
    assert "127.0.0.1:8080" in settings.allowed_hosts


def test_serving_over_http_owns_the_connection_reaper(monkeypatch):
    """start() starts the reaper before serving and stops it afterwards.

    It used to be started by the first db.connect, which made a thread nobody
    ever stopped the side effect of a tool call, and left a second server in the
    same process running on the first one's thread. Serving is where a server
    begins and ends, so that is where the thread does too.
    """
    pytest.importorskip("mcp")

    from mcp_plugin.lib import db_functions

    events = []
    monkeypatch.setattr(
        db_functions, "start_connection_reaper", lambda: events.append("start")
    )
    monkeypatch.setattr(
        db_functions, "stop_connection_reaper", lambda: events.append("stop")
    )
    monkeypatch.setattr(
        server,
        "_serve_streamable_http",
        lambda *arguments, **keywords: events.append("served"),
    )
    monkeypatch.setattr(server, "_serve_stdio", lambda *arguments: events.append("stdio"))

    try:
        server.start("127.0.0.1", 8080, general.TRANSPORT_STREAMABLE_HTTP, ["db"])

        assert events == ["start", "served", "stop"]

        # Even when serving ends by raising: a server that fell over must not
        # leave its reaper behind.
        events.clear()
        monkeypatch.setattr(
            server,
            "_serve_streamable_http",
            lambda *arguments, **keywords: (_ for _ in ()).throw(KeyboardInterrupt),
        )
        with pytest.raises(KeyboardInterrupt):
            server.start("127.0.0.1", 8080, general.TRANSPORT_STREAMABLE_HTTP, ["db"])

        assert events == ["start", "stop"]

        # Over stdio there is nothing to reap: one client owns the process.
        events.clear()
        server.start("127.0.0.1", 8080, general.TRANSPORT_STDIO, ["db"])

        assert events == ["stdio"]
    finally:
        general.set_active_transport(None)
