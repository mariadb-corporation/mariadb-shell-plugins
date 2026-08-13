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

"""Tests that a connection recovers from having its session killed.

Driven against the shared sandbox, in this process, because the point is what a
real server connection does when it is taken away: the connection is KILLed from
a second session, exactly as a restart, an administrator or a network device with
its own idle timeout would take it away.

Why this cannot be tested with a stub: the failure is only recognizable from the
error code the real client library produces (2013 CR_SERVER_LOST, and 2006
CR_SERVER_GONE_ERROR on every call after it). The shell itself offers no way to
notice - ``session.is_open()`` is ``_mysql != nullptr``, which stays true for a
connection the server dropped long ago, verified against this build.
"""

# cSpell:ignore mysqlsh MariaDB

import pytest

import mysqlsh

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions, general

CONNECTION_ID_SQL = "SELECT CONNECTION_ID() AS id"


def _connection_id(session):
    """Returns the server-side id of the given session's connection."""
    return session.run_sql(CONNECTION_ID_SQL, []).fetch_all()[0].get_field("id")


def test_a_killed_session_is_replaced_on_the_next_call(sandbox):
    """A connection whose session was killed works again on the next call.

    Before this, the cached session object stayed in place - non-None, and
    reported as open by the shell - so every call on the connection failed until
    the client gave up and closed it. The UUID stayed valid the whole time, which
    made it worse: nothing about the connection said it had to be reopened.
    """
    if not sandbox.deployed:
        pytest.skip("Sandbox deployment failed or was skipped.")

    # sandbox.deploy registered this connection in the secret store, which is
    # what lets the session be opened again: every open re-checks the URI.
    assert sandbox.uri in db_functions.config.list_connection_uris()

    client = general.ClientIdentity()
    connection = db_functions._Connection(sandbox.uri, client)
    connection_id = "recovery-test-connection"
    db_functions._sessions[connection_id] = connection

    monitor = mysqlsh.globals.shell.open_session(
        {
            "host": "127.0.0.1",
            "port": sandbox.port,
            "user": "root",
            "password": sandbox.password,
        }
    )

    try:
        with db_functions.use_session(connection_id, client) as session:
            first = _connection_id(session)
            first_session = session

        # Taken away from outside, with the connection sitting idle and its
        # session still cached and still reported as open.
        monitor.run_sql(f"KILL {first}", [])
        assert first_session.is_open() is True

        # The next statement fails - the client only finds out by trying - and
        # the caller is told rather than having it retried behind its back.
        with pytest.raises(mysqlsh.DBError) as lost:
            with db_functions.use_session(connection_id, client) as session:
                session.run_sql("SELECT 1", [])

        assert lost.value.code in db_functions._CONNECTION_LOST_ERRORS

        # The dead session was thrown away rather than kept and handed out again.
        assert connection.session is None
        assert connection_id in db_functions._sessions

        # And the connection works, on a new server connection.
        with db_functions.use_session(connection_id, client) as session:
            second = _connection_id(session)

        assert second != first

        # Which is not a one-off: it survives being killed again.
        monitor.run_sql(f"KILL {second}", [])
        with pytest.raises(mysqlsh.DBError):
            with db_functions.use_session(connection_id, client) as session:
                session.run_sql("SELECT 1", [])

        with db_functions.use_session(connection_id, client) as session:
            assert _connection_id(session) not in (first, second)
    finally:
        db_functions._sessions.pop(connection_id, None)
        connection.close()
        monitor.close()
