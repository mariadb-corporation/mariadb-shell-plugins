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

"""Tests that a shell session survives being used from several threads.

The db tools do this whether or not anyone intended it. The MCP SDK runs a sync
tool body with ``anyio.to_thread.run_sync``, so every db.connect opens its
session on an anyio worker thread rather than on the event-loop thread, and the
next call on that connection may well land on a different worker thread. The idle
reaper then closes sessions from a thread of its own. None of those threads is
created by the shell.

That is safe in this build, and this test is here to notice if it ever stops
being safe. The evidence it rests on:

* ``mysql_thread_init()`` and ``mysql_thread_end()`` are empty functions in the
  MariaDB Connector/C that the shell statically links, so there is no per-thread
  client-library state to set up in the first place. (The shell's ``Mysql_thread``
  RAII helper, and its "initialization ... when connecting from threads" comment,
  come from libmysqlclient, where that was not true.)
* ``Session_impl::close()`` resets the previous result, calls ``mysql_close()``
  and drops the pointer. There is no ``thread_local`` anywhere under
  ``mysqlshdk/libs/db/``.

What the plugin does guarantee, and what this does NOT test, is that only one
thread at a time works with a session: ``_Connection.lock`` serializes that, and
``test_db_sessions.test_a_session_in_use_is_not_closed`` covers it. Connector/C
is thread-safe for distinct connections, not for concurrent use of one.

A crash here would take the process down rather than fail an assertion - which is
the point of having it: a shell build that made sessions thread-affine would break
this test loudly, in a run, instead of taking down a server in production.
"""

# cSpell:ignore mysqlsh MariaDB PROCESSLIST

import threading
import time

import pytest

import mysqlsh

# How long to wait for the server to notice a disconnect, in seconds.
DISCONNECT_TIMEOUT = 10


def _on_thread(name, function):
    """Runs a function on a brand-new thread and returns what it returned.

    Args:
        name (str): The thread name, so a hang or a crash says which one.
        function: The callable to run.

    Returns:
        Whatever the callable returned.
    """
    outcome = {}

    def _run():
        try:
            outcome["value"] = function()
        except BaseException as error:  # noqa: BLE001 - re-raised below
            outcome["error"] = error

    thread = threading.Thread(target=_run, name=name, daemon=True)
    thread.start()
    thread.join(timeout=60)

    assert not thread.is_alive(), f"thread {name} did not finish"

    if "error" in outcome:
        raise outcome["error"]

    return outcome["value"]


def _server_sees(session, connection_id) -> bool:
    """Returns whether the server still lists the given connection."""
    result = session.run_sql(
        "SELECT COUNT(*) AS live FROM information_schema.PROCESSLIST "
        "WHERE ID = ?",
        [connection_id],
    )

    return int(result.fetch_all()[0].get_field("live")) > 0


def test_a_session_survives_being_used_across_threads(sandbox):
    """Open on one thread, query on another, close on a third.

    The third one is named after the reaper and has never touched the client
    library before, which is the case the shell gives the fewest promises about.
    """
    if not sandbox.deployed:
        pytest.skip("Sandbox deployment failed or was skipped.")

    connection_data = {
        "host": "127.0.0.1",
        "port": sandbox.port,
        "user": "root",
        "password": sandbox.password,
    }

    shell = mysqlsh.globals.shell
    # A second session, kept on this thread, to ask the server what it thinks.
    monitor = shell.open_session(connection_data)

    try:
        # Opened on a worker thread, as the SDK opens it for a sync db tool.
        session = _on_thread(
            "AnyIO worker thread", lambda: shell.open_session(connection_data)
        )

        # Used from a different thread again, as a second tool call would be.
        connection_id = _on_thread(
            "AnyIO worker thread 2",
            lambda: session.run_sql("SELECT CONNECTION_ID() AS id", [])
            .fetch_all()[0]
            .get_field("id"),
        )
        assert _server_sees(monitor, connection_id) is True

        # Closed from the reaper's own thread, which neither opened nor used it.
        _on_thread("mcp-db-connection-reaper", session.close)

        # The close really reached the server, rather than quietly doing nothing
        # on a thread that was not allowed to do it.
        deadline = time.monotonic() + DISCONNECT_TIMEOUT
        while _server_sees(monitor, connection_id):
            assert time.monotonic() < deadline, (
                "the server still lists the connection that was closed from "
                "the reaper thread"
            )
            time.sleep(0.1)

        # And the closed session is unusable, cleanly, from any thread.
        with pytest.raises(Exception):
            _on_thread(
                "AnyIO worker thread 3",
                lambda: session.run_sql("SELECT 1", []),
            )
    finally:
        monitor.close()
