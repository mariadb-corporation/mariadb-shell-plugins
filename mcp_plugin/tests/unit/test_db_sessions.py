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

Covers the safeguards that matter while serving over HTTP: a connection may only
be used by the client that opened it - same peer address AND same MCP session -
a connection that has been unused for too long has its session closed and
transparently opened again when it is used next, and no connection lives past its
maximum lifetime, however much it is used in the meantime. The first and the last
of those hold whatever the transport is - including when no transport was
recorded at all - so they are exercised in those states too.

The end of a connection's life is also what makes removing one take effect: the
URI is checked against the configured connections on every open, so a session
reopened after an idle period cannot come back on a connection that has been
taken away.

There is also a limit on how many connections there can be at once, per client
and in total, and it is checked and claimed in one step - a burst of concurrent
calls must not be able to overshoot it between them, and a call that is going to
be refused must not cost the database a connection first.

Where the work happens matters too: a connection's lock is a thread lock held for
a whole tool call, so taking it on the thread that runs the event loop would stop
the server answering anybody until the call finished. That is refused outright.

A connection that reopens its session tells the call that triggered it, and only
that call: everything which lived in the old session is gone, and the client
cannot see that by itself. Whether the flag is per-call is the whole of it - one
left standing would tell later callers their transaction had been lost.

Closing a connection is final, which two tests pin by reproducing the windows a
concurrent call can fall into: between resolving a connection and locking it, and
between a session being taken out of a connection and being closed. A call that
loses either race must be refused, not handed a new session on a connection that
has left the cache - that session would never be closed by anything.

Also covers what these leave behind on stderr: a refused use is invisible to
the client by design, so the log line is the only evidence of an attempted
takeover there is - and it must carry enough to act on without writing out the
connection id or the MCP session id that would let its reader use the connection.

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

# Deliberately NOT loopback: every loopback form normalizes to one token, so
# loopback addresses could not stand in for two different clients here.
CLIENT_ADDRESS = "192.0.2.10"
OTHER_ADDRESS = "192.0.2.20"

# MCP session ids as the transport generates them (uuid4().hex).
SESSION_ID = "0123456789abcdef0123456789abcdef"
OTHER_SESSION_ID = "fedcba9876543210fedcba9876543210"

# The identity a request over stdio presents: it has neither part.
STDIO_CLIENT = general.ClientIdentity()


class _StubResult:
    """The least a shell SQL result has to be for _serialize_result."""

    affected_items_count = 0
    warnings_count = 0

    def has_data(self):
        return False


class _StubSession:
    """Stands in for an open shell session, recording what happened to it."""

    def __init__(self):
        self.closed = False
        self.statements = []

    def run_sql(self, sql, _params=None):
        """Accepts any statement without looking at it.

        Here so that a tool call which reaches a session runs to completion: a
        test asserting that a call was REFUSED has to be able to tell that from a
        call that failed for some other reason on its way through.
        """
        self.statements.append(sql)

        return _StubResult()

    def close(self):
        self.closed = True


class _UnclosableSession(_StubSession):
    """A session whose close() fails, as one on a dead connection would."""

    def close(self):
        raise RuntimeError("the server went away")


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


def _identity(client_address, session_id=SESSION_ID):
    """Builds the identity a client at the given address on that session has."""
    return general.ClientIdentity(client_address, session_id)


def _context(client_address, session_id=SESSION_ID):
    """Builds a request context reporting the given client address and session.

    Mirrors what the HTTP transports attach to the context: the request they
    received, whose ``client`` carries the peer address and whose headers carry
    the MCP session id. Both being None produces a context without a request at
    all, which is what a tool sees over stdio; either one alone produces a
    request missing that part, which is what the fail-closed branch of
    db.connect is there for.
    """
    request = None
    if client_address is not None or session_id is not None:
        client = None
        if client_address is not None:
            client = SimpleNamespace(host=client_address, port=54321)

        headers = {}
        if session_id is not None:
            headers[general.MCP_SESSION_ID_HEADER] = session_id

        request = SimpleNamespace(client=client, headers=headers)

    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _register_connection(client, session=None, connection_id="test-connection-id"):
    """Registers a connection with a stub session and returns its id."""
    connection = db_functions._Connection("root@127.0.0.1:3306", client)
    connection.session = session if session is not None else _StubSession()

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


# --- the identity of the requesting client ---------------------------------


def test_client_address_is_read_from_the_request():
    """The address comes from the transport's request, not from a header."""
    assert general.get_client_address(_context(CLIENT_ADDRESS)) == CLIENT_ADDRESS

    # It comes back normalized, so what db.connect stores and what a later
    # request is compared against are in the same form by construction.
    assert (
        general.get_client_address(_context("::ffff:192.0.2.10")) == CLIENT_ADDRESS
    )
    assert (
        general.get_client_address(_context("::1")) == general.LOOPBACK_ADDRESS
    )

    # A request without a peer (stdio) and no context at all both yield None,
    # rather than an address that could be mistaken for a real one.
    assert general.get_client_address(_context(None, None)) is None
    assert general.get_client_address(None) is None

    # Outside of a request the context has no request context to read.
    class _NoRequestContext:
        @property
        def request_context(self):
            raise ValueError("Context is not available outside of a request")

    assert general.get_client_address(_NoRequestContext()) is None


def test_client_identity_carries_the_session_id_too():
    """The identity is the peer address plus the MCP session id.

    The session id is what an address cannot be: a secret. It is generated by
    the server when the client initializes and echoed back in the
    Mcp-Session-Id header, so it distinguishes clients that share an address -
    everything behind one NAT or reverse proxy, and every process on the
    machine on the default loopback bind.
    """
    assert general.get_client_identity(_context(CLIENT_ADDRESS)) == _identity(
        CLIENT_ADDRESS
    )

    # The address half is normalized inside the identity, the session id is
    # compared exactly as the transport issued it.
    assert general.get_client_identity(
        _context("::ffff:192.0.2.10", SESSION_ID)
    ) == _identity(CLIENT_ADDRESS)

    # Over stdio a request has neither part, and neither has a call made
    # outside of a request.
    assert general.get_client_identity(_context(None, None)) == STDIO_CLIENT
    assert general.get_client_identity(None) == STDIO_CLIENT

    # Half an identity stays half an identity - db.connect refuses those over
    # HTTP rather than binding a connection to something incomplete.
    assert general.get_client_identity(_context(CLIENT_ADDRESS, None)) == _identity(
        CLIENT_ADDRESS, None
    )
    assert general.get_client_identity(_context(None, SESSION_ID)) == _identity(
        None, SESSION_ID
    )


# --- binding a connection to the client that opened it --------------------


def test_a_connection_is_bound_to_its_client_over_http(http_transport):
    """Only the client that opened a connection can use it."""
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))

    # The client that opened it gets its session back.
    with db_functions.use_session(connection_id, _identity(CLIENT_ADDRESS)) as session:
        assert session is connection.session

    # Another client is told the connection does not exist, exactly as it is
    # told for an id that was never handed out - so guessing a UUID reveals
    # nothing about whether it is a real one.
    with pytest.raises(mysqlsh.Error) as taken_over:
        with db_functions.use_session(connection_id, _identity(OTHER_ADDRESS)):
            pass

    with pytest.raises(mysqlsh.Error) as unknown:
        with db_functions.use_session(
            "no-such-connection-id", _identity(CLIENT_ADDRESS)
        ):
            pass

    assert str(taken_over.value).replace(connection_id, "") == str(
        unknown.value
    ).replace("no-such-connection-id", "")

    # A request the server cannot attribute to any client is refused as well.
    for client in (STDIO_CLIENT, None):
        with pytest.raises(mysqlsh.Error):
            with db_functions.use_session(connection_id, client):
                pass

    # The session was left alone by the refused attempts.
    assert connection.session.closed is False


def test_a_connection_is_bound_to_its_mcp_session(http_transport):
    """The same address on a different MCP session is a different client.

    This is the half of the binding that is worth anything when addresses do
    not distinguish clients - behind a NAT or a reverse proxy, or between two
    processes on the machine talking to a loopback-bound server. The session id
    is server-generated, so a client cannot present one it was not given.
    """
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))

    # Same address, another session: refused.
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(
            connection_id, _identity(CLIENT_ADDRESS, OTHER_SESSION_ID)
        ):
            pass

    # Same address, no session at all: refused.
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(
            connection_id, _identity(CLIENT_ADDRESS, None)
        ):
            pass

    # The right session from the wrong address is refused too, so neither half
    # of the identity is sufficient on its own.
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(connection_id, _identity(OTHER_ADDRESS)):
            pass

    # Both parts matching is what gets the session back.
    with db_functions.use_session(connection_id, _identity(CLIENT_ADDRESS)) as session:
        assert session is connection.session

    assert connection.session.closed is False


def test_a_connection_is_usable_over_stdio(stdio_transport):
    """Over stdio the single client always matches itself.

    No request over stdio carries a peer address or a session id, so a
    connection is opened with an empty identity and every later call presents an
    empty identity again. The comparison therefore lets the one client through
    without having to know it is stdio - which is what keeps it from being a
    check that can be switched off.
    """
    connection_id, connection = _register_connection(STDIO_CLIENT)

    for _ in range(2):
        with db_functions.use_session(connection_id, STDIO_CLIENT) as session:
            assert session is connection.session

    # A stdio connection is not a connection that anybody may use: an identity
    # cannot reach it either. This cannot happen while serving over stdio (no
    # request has one there) and is asserted only to pin that the comparison is
    # an equality and not an "unless we are over HTTP".
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(connection_id, _identity(CLIENT_ADDRESS)):
            pass


def test_a_connection_stays_bound_without_an_active_transport():
    """The binding does not depend on the transport having been recorded.

    ``lib.server.start`` records the transport before serving, but
    ``build_mcp_server`` is public: an embedder can register the tools and
    serve them itself, and nothing resets the recorded transport when a server
    stops. If the check consulted that global it would fail open in exactly
    those cases, with no error to notice it by. So it does not - a connection
    bound to a client is only ever usable by that client, no matter what the
    transport global says.
    """
    db_functions._sessions.clear()
    general.set_active_transport(None)
    try:
        assert general.is_http_transport() is False

        connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))

        with db_functions.use_session(
            connection_id, _identity(CLIENT_ADDRESS)
        ) as session:
            assert session is connection.session

        for client in (
            _identity(OTHER_ADDRESS),
            _identity(CLIENT_ADDRESS, OTHER_SESSION_ID),
            STDIO_CLIENT,
            None,
        ):
            with pytest.raises(mysqlsh.Error):
                with db_functions.use_session(connection_id, client):
                    pass

        assert connection.session.closed is False
    finally:
        db_functions._sessions.clear()


def test_equivalent_spellings_of_an_address_are_the_same_client():
    """A client is recognized however its address happens to be written.

    The check is an equality, so every spelling of one address has to be
    reduced to a single form first: an IPv4 client on a dual-stack socket is
    reported as IPv4-mapped, IPv6 addresses can be written uncompressed, and a
    local client shows up as ``127.0.0.1`` or ``::1`` depending on which stack
    it used. Without this, the same client is refused its own connection.
    """
    normalize = general.normalize_client_address

    # Every form of loopback is the same, local client.
    loopback = {normalize(address) for address in ("127.0.0.1", "::1", "127.1.2.3")}
    assert loopback == {general.LOOPBACK_ADDRESS}

    # An IPv4-mapped address is the IPv4 address it maps to.
    assert normalize("::ffff:192.0.2.10") == normalize(CLIENT_ADDRESS)
    assert normalize("::ffff:192.0.2.10") != normalize(OTHER_ADDRESS)

    # IPv6 addresses compare by value, not by spelling.
    assert normalize("2001:0db8:0000:0000:0000:0000:0000:0001") == normalize(
        "2001:db8::1"
    )

    # Nothing that is not an address is invented, and anything that is not an
    # address at all is left as it is - it only gets compared with itself.
    assert normalize(None) is None
    assert normalize("  ") is None
    assert normalize("/var/run/some.sock") == "/var/run/some.sock"


def test_a_connection_is_reachable_over_either_ip_stack(http_transport):
    """The two loopback forms of one client reach the same connection."""
    connection_id, connection = _register_connection(_identity("127.0.0.1"))

    for client_address in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        with db_functions.use_session(
            connection_id, _identity(client_address)
        ) as session:
            assert session is connection.session


def test_the_tools_pass_the_client_identity_on(http_transport, monkeypatch):
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
    assert db_functions._sessions[connection_id].client == _identity(CLIENT_ADDRESS)

    # Opening a connection starts no threads: the reaper belongs to the server
    # that is serving, not to whichever tool call came first (see
    # test_server_binding).
    assert db_functions._reaper is None

    second_id = tools.tools["db.connect"](
        _context(OTHER_ADDRESS, OTHER_SESSION_ID), uri
    )
    assert opened == [uri, uri]

    # Each connection is bound to its own client, so the second one is no way
    # into the first.
    other = _context(OTHER_ADDRESS, OTHER_SESSION_ID)
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.execute_sql"](other, connection_id, "SELECT 1")
    tools.tools["db.close"](other, second_id)

    # A tool called by another client does not reach the connection.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.execute_sql"](other, connection_id, "SELECT 1")

    # Nor does one sharing the address but not the MCP session, which is the
    # case that an address alone cannot tell apart.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.execute_sql"](
            _context(CLIENT_ADDRESS, OTHER_SESSION_ID), connection_id, "SELECT 1"
        )

    # Nor can another client close it.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.close"](other, connection_id)
    assert connection_id in db_functions._sessions

    # Half an identity is not enough to bind a connection to, so over HTTP
    # db.connect refuses it: no address, no MCP session, and neither.
    for context in (
        _context(None, SESSION_ID),
        _context(CLIENT_ADDRESS, None),
        _context(None, None),
    ):
        with pytest.raises(mysqlsh.Error):
            tools.tools["db.connect"](context, uri)
    assert opened == [uri, uri]

    # The client that opened the connection closes it.
    tools.tools["db.close"](_context(CLIENT_ADDRESS), connection_id)
    assert connection_id not in db_functions._sessions


# --- closing idle connections ---------------------------------------------


def test_an_idle_session_is_closed_and_opened_again(http_transport, monkeypatch):
    """An unused session is closed; using the connection opens a new one."""
    first_session = _StubSession()
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS), first_session)

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

    # An idle close is not the end of the connection: closing its session must
    # not raise the flag that would keep it from ever opening another.
    assert connection.closed is False

    second_session = _StubSession()
    monkeypatch.setattr(
        db_functions, "_open_session", lambda uri: second_session
    )

    # Using the connection opens a new session, transparently to the caller.
    with db_functions.use_session(connection_id, _identity(CLIENT_ADDRESS)) as session:
        assert session is second_session
    assert connection.session is second_session

    # And it counts as used again, so the next pass does not close it.
    assert db_functions._close_idle_sessions() == 0
    assert second_session.closed is False


def test_a_session_in_use_is_not_closed(http_transport):
    """The reaper never closes a session another thread is working with."""
    session = _StubSession()
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS), session)

    closed = []
    with db_functions.use_session(connection_id, _identity(CLIENT_ADDRESS)):
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
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS), session)

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


# --- what the connection handling records ----------------------------------


def test_opening_a_connection_is_logged(http_transport, monkeypatch, capsys):
    """db.connect records which client a connection was bound to.

    The line every later one about the connection refers back to. It carries
    only a prefix of the connection id and of the MCP session id: both are
    credentials - whoever has one can use the connection - so the log must not
    be a place to read them out of.
    """
    uri = "root@127.0.0.1:3306"
    monkeypatch.setattr(db_functions, "_open_session", lambda _uri: _StubSession())
    monkeypatch.setattr(db_functions.config, "list_connection_uris", lambda: [uri])

    tools = _ToolRecorder()
    db_functions.register_db_tools(tools)
    connection_id = tools.tools["db.connect"](_context(CLIENT_ADDRESS), uri)

    logged = capsys.readouterr().err
    assert "db.connect: opened connection" in logged
    assert general.log_id_prefix(connection_id) in logged
    assert uri in logged
    assert f"address={CLIENT_ADDRESS}" in logged
    assert general.log_id_prefix(SESSION_ID) in logged

    # Neither of the two secrets is written out in full.
    assert connection_id not in logged
    assert SESSION_ID not in logged

    # A request that cannot be attributed to a client is refused, and that is
    # recorded too - it is a client trying to open a connection the server
    # would not be able to keep anybody else off.
    with pytest.raises(mysqlsh.Error):
        tools.tools["db.connect"](_context(CLIENT_ADDRESS, None), uri)

    logged = capsys.readouterr().err
    assert "db.connect: REFUSED" in logged


def test_a_refused_connection_use_is_logged(http_transport, capsys):
    """A client using somebody else's connection leaves a trace.

    This is the whole point of the log: the client is told the connection does
    not exist, exactly as it is told for an id that was never handed out, so
    nothing about the attempt reaches whoever is reading tool errors.
    """
    connection_id, _ = _register_connection(_identity(CLIENT_ADDRESS))
    capsys.readouterr()

    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(
            connection_id, _identity(OTHER_ADDRESS, OTHER_SESSION_ID)
        ):
            pass

    logged = capsys.readouterr().err
    assert "db: REFUSED use of connection" in logged
    assert general.log_id_prefix(connection_id) in logged
    # Both sides of the comparison, which is what makes the line worth having:
    # who the connection belongs to and who asked for it.
    assert f"bound to address={CLIENT_ADDRESS}" in logged
    assert f"request from address={OTHER_ADDRESS}" in logged
    assert general.log_id_prefix(OTHER_SESSION_ID) in logged
    assert OTHER_SESSION_ID not in logged

    # An id that was never handed out is a stale connection id, not an attempt
    # to use one that exists - the client cannot tell the two answers apart, but
    # the log deliberately does.
    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(
            "no-such-connection-id", _identity(CLIENT_ADDRESS)
        ):
            pass

    assert "REFUSED" not in capsys.readouterr().err


def test_closing_an_idle_session_is_logged(http_transport, capsys):
    """The reaper says which connection it closed the session of, and why."""
    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    capsys.readouterr()

    assert db_functions._close_idle_sessions() == 1

    logged = capsys.readouterr().err
    assert "db: closed the idle session of connection" in logged
    assert general.log_id_prefix(connection_id) in logged
    assert f"{general.SESSION_IDLE_TIMEOUT:g}s unused" in logged
    # The connection is kept, and the line says so - a reader must not take
    # this for the connection having been closed.
    assert "stays valid" in logged

    # Nothing to close, nothing to say.
    assert db_functions._close_idle_sessions() == 0
    assert capsys.readouterr().err == ""


def test_a_session_that_fails_to_close_is_logged(http_transport, capsys):
    """A session that cannot be closed is dropped, but not silently."""
    _, connection = _register_connection(
        _identity(CLIENT_ADDRESS), _UnclosableSession()
    )
    capsys.readouterr()

    # The failure is contained: the caller is not made to deal with a session
    # that is being thrown away anyway.
    connection.close_session()
    assert connection.session is None

    logged = capsys.readouterr().err
    assert "db: closing the session of 'root@127.0.0.1:3306' failed" in logged
    assert "the server went away" in logged


def test_a_failed_reaper_pass_is_logged(monkeypatch, capsys):
    """The reaper survives a failing pass and reports it.

    A pass that raises must not end the thread - the idle timeout and the
    maximum lifetime would then quietly stop being applied for the rest of the
    server's life - and it must not be swallowed either. The reaper is driven for
    two passes here, the second of which leaves its endless loop.
    """
    passes = []

    def _failing_pass():
        passes.append(1)
        if len(passes) > 1:
            # Not an Exception, so it passes through the reaper's own handler
            # and ends the loop this test is driving.
            raise KeyboardInterrupt

        raise RuntimeError("no connection cache today")

    monkeypatch.setattr(db_functions, "_REAP_INTERVAL", 0)
    monkeypatch.setattr(db_functions, "_close_idle_sessions", _failing_pass)

    with pytest.raises(KeyboardInterrupt):
        db_functions._reap_connections()

    # Two passes: the first one failed and the reaper came back for another.
    assert len(passes) == 2

    logged = capsys.readouterr().err
    assert "db: a connection reaper pass failed" in logged
    assert "RuntimeError: no connection cache today" in logged


def test_logging_never_breaks_its_caller(monkeypatch, capsys):
    """A log line that cannot be written is not worth an exception.

    The reaper logs from inside its own except block, where a raise would end
    the thread, and every other call site is in the middle of a tool call.
    """

    class _BrokenStream:
        def write(self, _text):
            raise OSError("stderr is gone")

        def flush(self):
            pass

    monkeypatch.setattr(general.sys, "stderr", _BrokenStream())

    general.log_event("this cannot be written anywhere")


# --- the end of a connection's life ----------------------------------------


def _age(connection, seconds):
    """Moves a connection's clocks back, as if it had been open that long."""
    connection.opened_at -= seconds
    connection.last_used -= seconds


def test_a_connection_does_not_live_for_ever(http_transport, capsys):
    """Past its maximum lifetime a connection is gone, however much it is used.

    The session idle timeout only recycles the session behind a connection, so
    on its own it leaves the UUID valid for as long as the server runs - and a
    UUID that never expires is one that stays worth guessing, and stays usable
    after the connection it was opened on has been taken away.
    """
    open_session = _StubSession()
    connection_id, connection = _register_connection(
        _identity(CLIENT_ADDRESS), open_session
    )
    client = _identity(CLIENT_ADDRESS)

    # Just short of the limit, and used the whole time: still the same
    # connection.
    _age(connection, general.CONNECTION_MAX_LIFETIME - 1)
    with db_functions.use_session(connection_id, client) as session:
        assert session is open_session
    capsys.readouterr()

    # Using it does not buy it any more time - the lifetime runs from when the
    # connection was opened, which is the whole point of it. The call above
    # stamped last_used, so this leaves a connection used a second ago that is
    # nevertheless over its lifetime.
    _age(connection, 1)

    with pytest.raises(mysqlsh.Error) as expired:
        with db_functions.use_session(connection_id, client):
            pass

    # Reported exactly as an id that was never handed out, which is what it is
    # from now on - and the client's move is the same either way: db.connect.
    with pytest.raises(mysqlsh.Error) as unknown:
        with db_functions.use_session("no-such-connection-id", client):
            pass

    assert str(expired.value).replace(connection_id, "") == str(
        unknown.value
    ).replace("no-such-connection-id", "")

    # It is not merely refused, it is gone: its record is out of the cache and
    # its session closed, so it holds nothing on the server either.
    assert connection_id not in db_functions._sessions
    assert connection.session is None
    assert open_session.closed is True

    logged = capsys.readouterr().err
    assert "db: dropped connection" in logged
    assert general.log_id_prefix(connection_id) in logged
    assert f"maximum lifetime of {general.CONNECTION_MAX_LIFETIME:g}s" in logged


def test_the_lifetime_holds_without_a_reaper(stdio_transport):
    """The maximum lifetime does not depend on the reaper having been started.

    The reaper only runs over HTTP, but a connection can be revoked over stdio
    too - sandbox.delete removes the connection its sandbox.deploy registered -
    so the limit is applied whenever a connection is used, whatever the
    transport. A limit that only the reaper enforced would not be one here.
    """
    assert general.is_http_transport() is False

    open_session = _StubSession()
    connection_id, connection = _register_connection(STDIO_CLIENT, open_session)
    _age(connection, general.CONNECTION_MAX_LIFETIME)

    with pytest.raises(mysqlsh.Error):
        with db_functions.use_session(connection_id, STDIO_CLIENT):
            pass

    assert connection_id not in db_functions._sessions
    assert open_session.closed is True


def test_the_reaper_drops_a_connection_nobody_comes_back_to(http_transport, capsys):
    """An abandoned connection does not keep its place for ever.

    Nothing calls db.close for a client that simply went away, and checking the
    lifetime when a connection is used never reaches a connection that is not
    used again - so the reaper has to sweep them.
    """
    live_session = _StubSession()
    abandoned_session = _StubSession()
    live_id, live = _register_connection(
        _identity(CLIENT_ADDRESS), live_session, "live-connection-id"
    )
    abandoned_id, abandoned = _register_connection(
        _identity(OTHER_ADDRESS), abandoned_session, "abandoned-connection-id"
    )

    _age(abandoned, general.CONNECTION_MAX_LIFETIME)
    capsys.readouterr()

    assert db_functions._drop_expired_connections() == 1

    # The expired one is gone, session and all; the other is untouched.
    assert abandoned_id not in db_functions._sessions
    assert abandoned_session.closed is True
    assert db_functions._sessions[live_id] is live
    assert live_session.closed is False

    logged = capsys.readouterr().err
    assert "db: dropped connection" in logged
    assert general.log_id_prefix(abandoned_id) in logged

    # Nothing left to expire, and a second pass says nothing.
    assert db_functions._drop_expired_connections() == 0
    assert capsys.readouterr().err == ""


def test_removing_a_connection_revokes_it(http_transport, monkeypatch):
    """A connection removed from the configuration cannot be opened again.

    The password is read from the secret store on every open, so a session
    reopened after an idle period would otherwise come back on a URI that is no
    longer configured: mcp.setup would appear to remove a connection while every
    live UUID for it went on working. db.connect's check is not enough - it only
    ever runs before the UUID exists.
    """
    # The real _open_session runs here - stubbing it out would take the check
    # under test with it - so only the configured list is stood in for. It is
    # consulted before anything touches the secret store or the network, which
    # is what lets this run without either.
    uri = "root@127.0.0.1:3306"
    configured = [uri]
    monkeypatch.setattr(
        db_functions.config, "list_connection_uris", lambda: list(configured)
    )

    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))
    client = _identity(CLIENT_ADDRESS)

    # The connection falls idle, so its session is closed and the next use has
    # to open a new one.
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1
    assert connection.session is None

    # Now the connection is removed with mcp.setup. The stored password would
    # still be read on the reopen, so this is the only thing standing between a
    # removed connection and a UUID that goes on working.
    configured.clear()

    with pytest.raises(mysqlsh.Error) as revoked:
        with db_functions.use_session(connection_id, client):
            pass

    # And it says what happened, rather than failing somewhere down in the
    # secret store, which is what a missing password read looks like.
    assert "no longer a configured connection" in str(revoked.value)
    assert connection.session is None


def test_a_first_open_is_validated_too(http_transport, monkeypatch):
    """The check sits in the one place every open goes through.

    db.connect keeps its own check for the better error message, but the one in
    _open_session is what covers every path - a first open and a reopen alike -
    so a caller reaching the cache another way cannot open an unconfigured URI.
    """
    monkeypatch.setattr(db_functions.config, "list_connection_uris", lambda: [])

    with pytest.raises(mysqlsh.Error) as refused:
        db_functions._open_session("root@192.0.2.99:3306")

    assert "no longer a configured connection" in str(refused.value)


# --- how many connections there can be -------------------------------------


def _registered_tools(monkeypatch, opened):
    """Registers the db tools with a stubbed-out session factory.

    Returns the tools by name and the one configured URI; ``opened`` collects an
    entry per session actually opened, which is how the tests tell a call that
    was refused from one that cost the database a connection.
    """
    uri = "root@127.0.0.1:3306"

    def _fake_open_session(session_uri):
        opened.append(session_uri)
        return _StubSession()

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)
    monkeypatch.setattr(
        db_functions.config, "list_connection_uris", lambda: [uri]
    )

    tools = _ToolRecorder()
    db_functions.register_db_tools(tools)

    return tools.tools, uri


def test_one_client_cannot_open_connections_without_end(
    http_transport, monkeypatch, capsys
):
    """A client gets a limited number of connections, and is told the limit.

    Nothing about db.connect costs the caller anything, while each call costs the
    server a database session for as long as the connection lives - so a loop of
    them is a way to use up the database's connections and this process's memory
    at no cost at all.
    """
    monkeypatch.setattr(general, "MAX_CONNECTIONS_PER_CLIENT", 3)
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    ids = [tools["db.connect"](context, uri) for _ in range(3)]
    assert len(opened) == 3
    capsys.readouterr()

    with pytest.raises(mysqlsh.Error) as refused:
        tools["db.connect"](context, uri)

    assert "maximum of 3" in str(refused.value)
    assert "db.close" in str(refused.value)

    # Refused before the database was asked for anything: a call that is going to
    # be turned away must not be the reason a connection was taken.
    assert len(opened) == 3
    assert len(db_functions._sessions) == 3

    logged = capsys.readouterr().err
    assert "db.connect: REFUSED" in logged
    assert f"address={CLIENT_ADDRESS}" in logged

    # It is the client's own limit, not the server's: another client is
    # unaffected by the first one having used up its share.
    other_id = tools["db.connect"](
        _context(OTHER_ADDRESS, OTHER_SESSION_ID), uri
    )
    assert other_id in db_functions._sessions

    # And closing one gives the first client its slot back.
    tools["db.close"](context, ids[0])
    assert tools["db.connect"](context, uri) in db_functions._sessions


def test_the_server_as_a_whole_has_a_limit_too(http_transport, monkeypatch):
    """Several clients cannot do together what one of them may not do alone.

    The per-client limit rests on the client identity, and a server with no
    authentication cannot insist that two identities are two people: one client
    behind a changing address, or simply on a new MCP session, is a new client as
    far as that limit is concerned.
    """
    monkeypatch.setattr(general, "MAX_CONNECTIONS_TOTAL", 2)
    monkeypatch.setattr(general, "MAX_CONNECTIONS_PER_CLIENT", 1)
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)

    for session_id in (SESSION_ID, OTHER_SESSION_ID):
        tools["db.connect"](_context(CLIENT_ADDRESS, session_id), uri)

    # A third client is within its own limit and still refused.
    with pytest.raises(mysqlsh.Error) as refused:
        tools["db.connect"](_context(OTHER_ADDRESS, "0" * 32), uri)

    assert "maximum of 2" in str(refused.value)
    assert len(opened) == 2
    assert len(db_functions._sessions) == 2


def test_a_connection_that_fails_to_open_gives_its_slot_back(
    http_transport, monkeypatch
):
    """A refused server, a wrong password: the attempt costs the client nothing.

    Room is claimed before the session is opened, so that a call over the limit
    does not take a database connection on its way to being refused. The other
    side of that has to hold too: an attempt that then fails to open must give
    the slot back, or a client whose database is briefly unreachable spends its
    allowance on connections it never got and cannot try again until they expire.
    """
    monkeypatch.setattr(general, "MAX_CONNECTIONS_PER_CLIENT", 1)

    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    def _fail_to_open(_uri):
        raise mysqlsh.DBError(2003, "Can't connect to MariaDB server")

    monkeypatch.setattr(db_functions, "_open_session", _fail_to_open)

    # The error reaches the client rather than being turned into a connection
    # that does not work.
    with pytest.raises(mysqlsh.DBError) as failed:
        tools["db.connect"](context, uri)

    assert failed.value.code == 2003
    # And nothing is left holding the client's one slot.
    assert db_functions._sessions == {}

    # Which is what lets it try again once the server is back.
    monkeypatch.setattr(db_functions, "_open_session", lambda _uri: _StubSession())
    assert tools["db.connect"](context, uri) in db_functions._sessions


def test_db_connect_refuses_a_uri_that_is_not_configured(
    http_transport, monkeypatch
):
    """Only the connections configured with mcp.setup can be opened.

    The first thing db.connect does, and the reason the tool cannot be used to
    reach an arbitrary server with the shell's credentials.
    """
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)

    with pytest.raises(mysqlsh.Error) as refused:
        tools["db.connect"](_context(CLIENT_ADDRESS), "root@192.0.2.99:3306")

    assert "is not a configured connection" in str(refused.value)
    # Refused before anything was opened or recorded.
    assert opened == []
    assert db_functions._sessions == {}


def test_an_expired_connection_does_not_hold_a_slot(
    http_transport, monkeypatch
):
    """A connection past its lifetime no longer counts against the limits.

    It is already gone in every way that matters - the reaper drops it, and so
    does using it - so counting it would keep a client out of a slot that nothing
    holds any more.
    """
    monkeypatch.setattr(general, "MAX_CONNECTIONS_PER_CLIENT", 1)
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    connection_id = tools["db.connect"](context, uri)
    with pytest.raises(mysqlsh.Error):
        tools["db.connect"](context, uri)

    _age(db_functions._sessions[connection_id], general.CONNECTION_MAX_LIFETIME)

    assert tools["db.connect"](context, uri) in db_functions._sessions
    assert len(opened) == 2


# --- serializing a result ---------------------------------------------------


class _StubColumn:
    """Stands in for a column's metadata, which is only asked for its label."""

    def __init__(self, label):
        self._label = label

    def get_column_label(self):
        return self._label


class _StubRow(list):
    """A row read by position, as the shell's rows are."""

    def get_field(self, label):  # pragma: no cover - here to be NOT used
        raise AssertionError(
            "values must be read by position: a label cannot reach the second "
            "of two columns that share it"
        )


class _StubDataResult:
    """A result with data, built from labels and rows of values."""

    affected_items_count = 0
    warnings_count = 0

    def __init__(self, labels, rows):
        self._labels = labels
        self._rows = rows

    def has_data(self):
        return True

    def get_columns(self):
        return [_StubColumn(label) for label in self._labels]

    def fetch_all(self):
        return [_StubRow(values) for values in self._rows]


def test_two_columns_with_one_label_both_survive():
    """A row is a dict, so columns sharing a label have to be keyed apart.

    SELECT a.id, b.id FROM a JOIN b is ordinary SQL and a client can send
    anything. Keying both on "id" dropped one of them while still listing two
    columns, which made the server's data loss look like the client's bug.
    """
    result = _StubDataResult(["id", "id", "other"], [[1, 2, 3]])

    output = db_functions._serialize_result(result)

    # Every column is reported, and the keys are exactly what columns says.
    assert output["columns"] == ["id", "id_2", "other"]
    assert output["rows"] == [{"id": 1, "id_2": 2, "other": 3}]
    assert list(output["rows"][0]) == output["columns"]


def test_a_made_up_label_never_collides_with_a_real_one():
    """The suffix is checked against every label already used.

    Otherwise a query selecting id, id and a column genuinely called id_2 would
    have the invented key land on the real one - losing a column while fixing a
    column.
    """
    result = db_functions._serialize_result(
        _StubDataResult(["id", "id", "id_2", "id"], [[1, 2, 3, 4]])
    )

    assert result["columns"] == ["id", "id_2", "id_2_2", "id_3"]
    assert result["rows"] == [{"id": 1, "id_2": 2, "id_2_2": 3, "id_3": 4}]

    # The other way round, the column that is really called id_2 comes first and
    # keeps its name; the duplicate takes the next free suffix.
    result = db_functions._serialize_result(
        _StubDataResult(["id_2", "id", "id"], [[1, 2, 3]])
    )

    assert result["columns"] == ["id_2", "id", "id_3"]


def test_distinct_labels_are_left_exactly_as_they_are():
    """The ordinary case is untouched: no renaming, no suffixes."""
    result = db_functions._serialize_result(
        _StubDataResult(["id", "name", "comment"], [[1, "a", None], [2, "b", "x"]])
    )

    assert result["columns"] == ["id", "name", "comment"]
    assert result["rows"] == [
        {"id": 1, "name": "a", "comment": None},
        {"id": 2, "name": "b", "comment": "x"},
    ]


# --- the reaper's lifetime --------------------------------------------------


def test_the_reaper_can_be_started_and_stopped():
    """A reaper belongs to the server that started it, not to the process.

    It used to be started by the first db.connect and never stopped: a second
    server in the same process silently kept the first one's thread, and a
    stopped server left one waking every interval for nothing.
    """
    assert db_functions._reaper is None

    db_functions.start_connection_reaper()
    first = db_functions._reaper
    assert first is not None and first.is_alive()

    # Idempotent: asking again while one runs does not start a second.
    db_functions.start_connection_reaper()
    assert db_functions._reaper is first

    db_functions.stop_connection_reaper()
    assert db_functions._reaper is None
    # It waits on the stop signal rather than sleeping through its interval, so
    # it is gone long before the 30 seconds it would otherwise have been in.
    assert first.is_alive() is False

    # Stopping again is harmless, and a new server gets a NEW thread rather than
    # the dead one.
    db_functions.stop_connection_reaper()
    db_functions.start_connection_reaper()
    second = db_functions._reaper
    try:
        assert second is not None and second.is_alive()
        assert second is not first
    finally:
        db_functions.stop_connection_reaper()

    assert db_functions._reaper is None


# --- where a session may be worked with -------------------------------------


def test_a_session_cannot_be_taken_on_the_event_loop_thread(http_transport):
    """Session work is refused on the thread that runs the event loop.

    Holding a connection's lock there is not a private cost: while the thread
    waits, the server answers nobody, on any transport. Measured on this
    runtime - a coroutine blocking on a lock a worker thread held for 0.6s let
    zero other tasks run - which is why async tool code hands the work to a
    worker thread and why this is refused rather than merely discouraged.
    """
    import anyio
    import anyio.to_thread

    connection_id, connection = _register_connection(STDIO_CLIENT)

    async def from_the_event_loop():
        with db_functions.use_session(connection_id, STDIO_CLIENT):
            pass

    with pytest.raises(RuntimeError, match="event loop"):
        anyio.run(from_the_event_loop)

    # Nothing was handed out, and the connection is untouched.
    assert connection.session_restarted is False

    # A worker thread is exactly where it belongs, which is how the sync db
    # tools already run and where the async ones now put this work.
    async def from_a_worker_thread():
        def _work():
            with db_functions.use_session(connection_id, STDIO_CLIENT) as session:
                return session

        return await anyio.to_thread.run_sync(_work)

    assert anyio.run(from_a_worker_thread) is connection.session

    # And an ordinary synchronous caller, with no loop anywhere, is fine.
    with db_functions.use_session(connection_id, STDIO_CLIENT) as session:
        assert session is connection.session


# --- telling the caller its session was replaced ----------------------------


def test_a_call_that_opens_a_new_session_says_so(http_transport, monkeypatch):
    """The call that reopens reports it; the calls around it do not.

    Without the flag the loss is silent, and the silent case is the dangerous
    one: a COMMIT that arrives on a session which never saw the START
    TRANSACTION succeeds and commits nothing at all.
    """
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    connection_id = tools["db.connect"](context, uri)
    connection = db_functions._sessions[connection_id]

    # An ordinary call has nothing to report.
    result = tools["db.execute_sql"](context, connection_id, "START TRANSACTION")
    assert "session_restarted" not in result

    # The session is closed for being idle. The connection stays valid, which is
    # exactly why the client needs telling.
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1

    result = tools["db.execute_sql"](context, connection_id, "COMMIT")
    assert result["session_restarted"] is True

    # It belongs to that call and stops existing with it. Asserted here, before
    # another call is made: a flag left standing on the connection is a flag
    # that lies to whoever reads it next, and the assignment made when the next
    # call starts would hide that.
    assert connection.session_restarted is False

    # And the next call does not repeat it.
    result = tools["db.execute_sql"](context, connection_id, "SELECT 1")
    assert "session_restarted" not in result


def test_a_script_reports_the_restart_on_its_first_statement(
    http_transport, monkeypatch
):
    """One session is opened per call, so one statement reports it."""
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    connection_id = tools["db.connect"](context, uri)
    connection = db_functions._sessions[connection_id]
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1

    results = tools["db.execute_sql_script"](
        context, connection_id, "SELECT 1; SELECT 2; SELECT 3"
    )

    assert len(results) == 3
    assert results[0]["session_restarted"] is True
    assert all("session_restarted" not in result for result in results[1:])


def test_a_session_lost_mid_flight_is_reported_on_the_next_call(
    http_transport, monkeypatch
):
    """The report covers a session discarded for being dead, not just idle."""
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    connection_id = tools["db.connect"](context, uri)
    client = _identity(CLIENT_ADDRESS)

    with pytest.raises(mysqlsh.DBError):
        with db_functions.use_session(connection_id, client):
            raise mysqlsh.DBError(2013, "Lost connection to server during query")

    result = tools["db.execute_sql"](context, connection_id, "SELECT 1")
    assert result["session_restarted"] is True


# --- a session that has died -----------------------------------------------


def test_a_lost_connection_throws_the_session_away(http_transport, monkeypatch):
    """A statement that reports the connection is gone discards the session.

    The shell cannot tell a dead connection from a live one - its is_open() only
    says whether a handle exists on this side, and that stays true after the
    server has dropped the connection - so the error code is the only signal
    there is. Without acting on it, every call on the connection fails on the
    same corpse until somebody closes it.
    """
    sessions = []

    def _open(_uri):
        sessions.append(_StubSession())
        return sessions[-1]

    monkeypatch.setattr(db_functions, "_open_session", _open)
    monkeypatch.setattr(
        db_functions.config,
        "list_connection_uris",
        lambda: ["root@127.0.0.1:3306"],
    )

    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))
    first_session = connection.session
    client = _identity(CLIENT_ADDRESS)

    # CR_SERVER_LOST, as the shell reports it for a killed connection.
    lost = mysqlsh.DBError(2013, "Lost connection to server during query")

    with pytest.raises(mysqlsh.DBError) as reported:
        with db_functions.use_session(connection_id, client):
            raise lost

    # The caller is told what happened - the call is not retried behind its
    # back, since a statement may have run in part and any transaction is gone.
    assert reported.value.code == 2013

    # The session is gone, the connection is not.
    assert first_session.closed is True
    assert connection.session is None
    assert connection.closed is False
    assert connection_id in db_functions._sessions

    # And the next call gets a working session again.
    with db_functions.use_session(connection_id, client) as session:
        assert session is sessions[-1]
        assert session is not first_session


def test_an_ordinary_sql_error_keeps_the_session(http_transport, monkeypatch):
    """A statement that merely failed does not cost the caller its session.

    This is the other half of reading the error code: reconnecting on every
    failed statement would throw away a perfectly good session - and with it the
    temporary tables, session variables and open transaction on it - because of a
    typo.
    """
    monkeypatch.setattr(db_functions, "_open_session", lambda _uri: _StubSession())

    connection_id, connection = _register_connection(_identity(CLIENT_ADDRESS))
    session_before = connection.session
    client = _identity(CLIENT_ADDRESS)

    for error in (
        # A server-side error: the table is not there.
        mysqlsh.DBError(1146, "Table 'db.nope' doesn't exist"),
        # A shell-side error, which carries no DB error code at all.
        mysqlsh.Error("something else went wrong"),
        RuntimeError("and something with no code either"),
    ):
        with pytest.raises(type(error)):
            with db_functions.use_session(connection_id, client):
                raise error

        assert connection.session is session_before
        assert session_before.closed is False


# --- closing a connection while a call is using it -------------------------


def test_closing_a_connection_beats_a_call_that_races_it(
    http_transport, monkeypatch
):
    """A call that loses the race to db.close does not get a new session.

    use_session resolves its connection out of the cache and only then takes the
    connection's lock, so a db.close can run entirely in between: it takes the
    record out of the cache and closes the session. What must not happen then is
    the losing call opening a fresh session - the record is unreachable by then,
    so nothing would ever close that session again, and the caller would go on
    working on a connection its client had been told was closed.

    The window is hit deliberately here, by closing the connection from inside
    the lookup: threads make this rare, not impossible, and the SDK does run the
    sync db tools in worker threads.
    """
    opened = []
    tools, uri = _registered_tools(monkeypatch, opened)
    context = _context(CLIENT_ADDRESS)

    connection_id = tools["db.connect"](context, uri)
    first_session = db_functions._sessions[connection_id].session

    real_get_connection = db_functions._get_connection
    raced = []

    def _close_it_in_the_window(*args, **kwargs):
        connection = real_get_connection(*args, **kwargs)
        if not raced:
            raced.append(True)
            # A whole db.close, start to finish, in the window between the
            # lookup and the lock. In its own thread, so the connection lock is
            # not held by this one - which is the situation being reproduced.
            closer = threading.Thread(
                target=lambda: tools["db.close"](context, connection_id)
            )
            closer.start()
            closer.join(timeout=10)

        return connection

    monkeypatch.setattr(db_functions, "_get_connection", _close_it_in_the_window)

    with pytest.raises(mysqlsh.Error):
        tools["db.execute_sql"](context, connection_id, "SELECT 1")

    # The leak, measured: no second session was ever opened.
    assert len(opened) == 1
    assert first_session.closed is True
    assert connection_id not in db_functions._sessions


def test_a_session_being_closed_is_not_replaced_underneath(
    http_transport, monkeypatch
):
    """The second window: close_session lets go of the lock before closing.

    _Connection.close_session takes the session out of the connection, releases
    the lock and only then closes it, so for that moment the connection holds no
    session and its lock is free - exactly what a call needs to open one. The
    flag is what refuses it; keeping the lock for the whole close would not,
    since the racing call would simply open its session afterwards.

    Driven from inside session.close() rather than from a second thread, because
    that IS the window. The connection lock is genuinely not held at that point,
    so this stands in for a thread that acquires it right there.
    """
    # A stand-in session factory, so that a racer which is NOT refused really
    # does end up holding a second session - that leaked session is the whole
    # point, and the test has to be able to see it.
    monkeypatch.setattr(db_functions, "_open_session", lambda _uri: _StubSession())

    attempted = []
    connection = db_functions._Connection("root@127.0.0.1:3306", STDIO_CLIENT)

    class _RacedSession(_StubSession):
        def close(self):
            try:
                attempted.append(connection.open_session())
            except db_functions._ConnectionClosed:
                attempted.append("refused")

            super().close()

    session = _RacedSession()
    connection.session = session

    connection.close()

    assert attempted == ["refused"]
    assert session.closed is True
    assert connection.session is None
