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

"""Tests for the connection handling of the db tools.

Covers the two safeguards that apply while serving over HTTP: a connection may
only be used by the client that opened it, and a connection that has been
unused for too long has its session closed and transparently opened again when
it is used next.

These drive lib/db_functions.py in-process with a stub session, so no database
server is needed and no time has to be waited out. The tools themselves are
registered on a recorder standing in for the MCPServer, which makes the tool
functions callable directly.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

import threading
from types import SimpleNamespace

import pytest

import mysqlsh

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions, general

CLIENT_ADDRESS = "192.0.2.10"
OTHER_ADDRESS = "192.0.2.20"


class _StubSession:
    """Stands in for an open shell session, recording that it was closed."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _context(client_address):
    """Builds a request context reporting the given client address.

    Mirrors what the HTTP transports attach to the context: the request they
    received, whose ``client`` carries the peer address. ``None`` produces a
    context without a request, which is what a tool sees over stdio.
    """
    request = None
    if client_address is not None:
        request = SimpleNamespace(
            client=SimpleNamespace(host=client_address, port=54321)
        )

    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _register_connection(client_address, session=None):
    """Registers a connection with a stub session and returns its id."""
    connection = db_functions._Connection("root@127.0.0.1:3306", client_address)
    connection.session = session if session is not None else _StubSession()

    connection_id = "test-connection-id"
    db_functions._sessions[connection_id] = connection

    return connection_id, connection


@pytest.fixture
def http_transport():
    """Serves the tests as if the server ran over the HTTP transport.

    Restores the transport and empties the connection cache afterwards, so the
    tests that talk to a real server over stdio are unaffected.
    """
    db_functions._sessions.clear()
    general.set_active_transport(general.TRANSPORT_STREAMABLE_HTTP)
    try:
        yield
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


@pytest.fixture
def stdio_transport():
    """Serves the tests as if the server ran over the stdio transport."""
    db_functions._sessions.clear()
    general.set_active_transport(general.TRANSPORT_STDIO)
    try:
        yield
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


# --- the client address ---------------------------------------------------


def test_client_address_is_read_from_the_request():
    """The address comes from the transport's request, not from a header."""
    assert general.get_client_address(_context(CLIENT_ADDRESS)) == CLIENT_ADDRESS

    # A request without a peer (stdio) and no context at all both yield None,
    # rather than an address that could be mistaken for a real one.
    assert general.get_client_address(_context(None)) is None
    assert general.get_client_address(None) is None

    # Outside of a request the context has no request context to read.
    class _NoRequestContext:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")

    assert general.get_client_address(_NoRequestContext()) is None


# --- binding a connection to the client that opened it --------------------


def test_a_connection_is_bound_to_its_client_over_http(http_transport):
    """Only the client that opened a connection can use it."""
    connection_id, connection = _register_connection(CLIENT_ADDRESS)

    # The client that opened it gets its session back.
    with db_functions.use_session(connection_id, CLIENT_ADDRESS) as session:
        assert session is connection.session

    # Another client is told the connection does not exist, exactly as it is
    # told for an id that was never handed out - so guessing a UUID reveals
    # nothing about whether it is a real one.
    with pytest.raises(mysqlsh.Error) as taken_over:
        with db_functions.use_session(connection_id, OTHER_ADDRESS):
            pass

    with pytest.raises(mysqlsh.Error) as unknown:
        with db_functions.use_session("no-such-connection-id", CLIENT_ADDRESS):
            pass

    assert str(taken_over.value).replace(connection_id, "") == str(
        unknown.value
    ).replace("no-such-connection-id", "")

    # A request the server cannot attribute to any client is refused as well.
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(connection_id, None):
            pass

    # The session was left alone by the refused attempts.
    assert connection.session.closed is False


def test_a_connection_is_not_bound_to_its_client_over_stdio(stdio_transport):
    """Over stdio there is only one client, so no address is checked."""
    connection_id, connection = _register_connection(None)

    for client_address in (None, CLIENT_ADDRESS, OTHER_ADDRESS):
        with db_functions.use_session(connection_id, client_address) as session:
            assert session is connection.session


def test_the_tools_pass_the_client_address_on(http_transport, monkeypatch):
    """db.connect binds to the caller and the db tools check the binding."""
    opened = []

    def _fake_open_session(uri):
        opened.append(uri)
        return _StubSession()

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)
    uri = "root@127.0.0.1:3306"
    monkeypatch.setattr(
        db_functions.config, "list_connection_uris", lambda: [uri]
    )

    tools = _ToolRecorder()
    db_functions.register_db_tools(tools)

    connection_id = tools.tools["db.connect"](_context(CLIENT_ADDRESS), uri)
    assert opened == [uri]
    assert db_functions._sessions[connection_id].client_address == CLIENT_ADDRESS

    # Opening a connection over HTTP starts the reaper that closes the idle
    # sessions - once, however many connections are opened.
    reaper = db_functions._idle_reaper
    assert reaper is not None and reaper.is_alive()

    second_id = tools.tools["db.connect"](_context(OTHER_ADDRESS), uri)
    assert db_functions._idle_reaper is reaper
    assert opened == [uri, uri]

    # Each connection is bound to its own client, so the second one is no way
    # into the first.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.execute_sql"](
            _context(OTHER_ADDRESS), connection_id, "SELECT 1"
        )
    tools.tools["db.close"](_context(OTHER_ADDRESS), second_id)

    # A tool called by another client does not reach the connection.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.execute_sql"](
            _context(OTHER_ADDRESS), connection_id, "SELECT 1"
        )

    # Nor can another client close it.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.close"](_context(OTHER_ADDRESS), connection_id)
    assert connection_id in db_functions._sessions

    # Without a client address to bind it to, no connection is opened at all.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.connect"](_context(None), uri)
    assert opened == [uri, uri]

    # The client that opened the connection closes it.
    tools.tools["db.close"](_context(CLIENT_ADDRESS), connection_id)
    assert connection_id not in db_functions._sessions


# --- closing idle connections ---------------------------------------------


def test_an_idle_session_is_closed_and_opened_again(http_transport, monkeypatch):
    """An unused session is closed; using the connection opens a new one."""
    first_session = _StubSession()
    connection_id, connection = _register_connection(CLIENT_ADDRESS, first_session)

    # A connection that has just been used is left alone.
    assert db_functions._close_idle_sessions() == 0
    assert first_session.closed is False

    # Idle for longer than the timeout: the session is closed, but the
    # connection itself is kept so it can be used again.
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1
    assert first_session.closed is True
    assert connection.session is None
    assert connection_id in db_functions._sessions

    # With no session left there is nothing to close on the next pass.
    assert db_functions._close_idle_sessions() == 0

    second_session = _StubSession()
    monkeypatch.setattr(
        db_functions, "_open_session", lambda uri: second_session
    )

    # Using the connection opens a new session, transparently to the caller.
    with db_functions.use_session(connection_id, CLIENT_ADDRESS) as session:
        assert session is second_session
    assert connection.session is second_session

    # And it counts as used again, so the next pass does not close it.
    assert db_functions._close_idle_sessions() == 0
    assert second_session.closed is False


def test_a_session_in_use_is_not_closed(http_transport):
    """The reaper never closes a session another thread is working with."""
    session = _StubSession()
    connection_id, connection = _register_connection(CLIENT_ADDRESS, session)

    closed = []
    with db_functions.use_session(connection_id, CLIENT_ADDRESS):
        # Idle for long enough on paper - a statement running longer than the
        # timeout must still not have its session pulled away.
        connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1

        # The reaper runs in a thread of its own, which is what the connection
        # lock keeps out for the duration of the call.
        reaper = threading.Thread(
            target=lambda: closed.append(db_functions._close_idle_sessions())
        )
        reaper.start()
        reaper.join(timeout=10)

    assert closed == [0]
    assert session.closed is False

    # Leaving the tool call marks the connection as used, so it is not closed
    # on the next pass either.
    assert db_functions._close_idle_sessions() == 0


def test_close_does_not_open_an_idle_session_again(http_transport, monkeypatch):
    """db.close on a connection whose session was closed opens nothing."""
    session = _StubSession()
    connection_id, connection = _register_connection(CLIENT_ADDRESS, session)

    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1

    def _fail_to_open(uri):
        raise AssertionError("db.close must not open a session")

    monkeypatch.setattr(db_functions, "_open_session", _fail_to_open)

    tools = _ToolRecorder()
    db_functions.register_db_tools(tools)
    tools.tools["db.close"](_context(CLIENT_ADDRESS), connection_id)

    assert connection_id not in db_functions._sessions
    assert session.closed is True
