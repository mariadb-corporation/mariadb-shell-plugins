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

"""Tests for how db.execute_sql_script splits a script into statements.

The splitting itself is the shell's, and its splitter returns a whole-line
``--`` or ``#`` comment as a statement of its own - which is right for the
interactive shell, whose line editor has to echo comment lines, and wrong
here: the comment carries no SQL, and the server accepts one as a query
instead of rejecting it, so running it produces a result for a statement
nobody wrote.

So a comment is not run. It keeps its statement_index all the same, because
that number is how a caller pairs the results with the statements it split
for itself - the VS Code extension does exactly this to put each result
beside the statement in the file that produced it - and renumbering would
move every result after a comment onto the wrong statement.

The session here is a stub that records what it was asked to run, because
what matters is which statements reached it, not what the server made of
them. The split is the real one: these run inside the shell, so
``mysqlsh.mysql.split_script`` is the same function the tool calls.
"""

# cSpell:ignore mysqlsh MariaDB

from types import SimpleNamespace

import pytest

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions, general

CLIENT_ADDRESS = "192.0.2.10"
SESSION_ID = "0123456789abcdef0123456789abcdef"

# The header the extension's New SQL Editor button writes at the top of every
# file it generates, followed by a query - the script that first showed this.
GENERATED_FILE = "-- MariaDB connection: dba@localhost:3310\n\nSELECT 1;\n"


class _StubResult:
    """The least a shell SQL result has to be for _serialize_result."""

    affected_items_count = 0
    warnings_count = 0

    def has_data(self):
        return False


class _RecordingSession:
    """An open session that records every statement it is asked to run."""

    def __init__(self):
        self.statements = []
        self.closed = False

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)

        return _StubResult()

    def close(self):
        self.closed = True


@pytest.fixture
def tools(monkeypatch):
    """Registers the db tools over a stub session factory.

    Yields the tools by name, the context to call them with and the list the
    opened sessions land in, so a test can see what reached the server.
    """
    uri = "root@127.0.0.1:3306"
    opened = []

    def _fake_open_session(_uri, kind=None):
        session = _RecordingSession()
        opened.append(session)

        return session

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)
    monkeypatch.setattr(
        db_functions.config, "list_stored_connection_uris", lambda kind=None: [uri]
    )

    recorder = SimpleNamespace(tools={})

    def _tool(name):
        def decorator(fn):
            recorder.tools[name] = fn

            return fn

        return decorator

    recorder.tool = _tool
    db_functions.register_db_tools(recorder)

    db_functions._sessions.clear()
    general.set_active_transport(general.TRANSPORT_STREAMABLE_HTTP)
    context = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                client=SimpleNamespace(host=CLIENT_ADDRESS, port=54321),
                headers={general.MCP_SESSION_ID_HEADER: SESSION_ID},
            )
        )
    )
    try:
        connection_id = recorder.tools["db.connect"](context, uri)
        yield SimpleNamespace(
            run=lambda script, **kwargs: recorder.tools[
                "db.execute_sql_script"
            ](context, connection_id, script, **kwargs),
            opened=opened,
        )
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


def _ran(tools):
    """Returns the statements that actually reached the session."""
    assert len(tools.opened) == 1

    return tools.opened[0].statements


# --- a comment is not a statement to run ------------------------------------


def test_a_leading_line_comment_is_not_run(tools):
    """The case this was found on: a generated file's connection header.

    Without this the header ran on every execution and reported a result of
    its own, so a one-statement file looked like a two-statement one.
    """
    results = tools.run(GENERATED_FILE)

    assert _ran(tools) == ["SELECT 1"]
    assert len(results) == 1


def test_a_comment_keeps_its_place_in_the_numbering(tools):
    """The statement after a comment is numbered as if the comment had run.

    A caller splits the script for itself and pairs the results with what it
    split by this number. Numbering the query 0 here would hand its result to
    the caller's statement 0 - the comment.
    """
    results = tools.run(GENERATED_FILE)

    assert results[0]["statement_index"] == 1


def test_a_hash_comment_is_left_out_too(tools):
    """The splitter treats `# ...` the same way, and so does this."""
    results = tools.run("# a note\nSELECT 1;\n")

    assert _ran(tools) == ["SELECT 1"]
    assert [result["statement_index"] for result in results] == [1]


def test_a_comment_between_two_statements_is_left_out(tools):
    """A comment anywhere in the script, not only at the top of it."""
    results = tools.run("SELECT 1;\n-- mid\nSELECT 2;\n")

    assert _ran(tools) == ["SELECT 1", "SELECT 2"]
    # Zero, the comment, two: the numbering counts what was split, not what
    # was run.
    assert [result["statement_index"] for result in results] == [0, 2]


def test_a_script_of_nothing_but_comments_runs_nothing(tools):
    """Nothing reaches the server, and there is nothing to report."""
    assert tools.run("-- one\n-- two\n") == []
    assert _ran(tools) == []


# --- what is NOT a comment to leave out -------------------------------------


def test_a_comment_inside_a_statement_stays_with_it(tools):
    """The splitter keeps it, so the statement arrives whole.

    Only a comment that OPENS a statement is split out, which is what makes
    "begins with a line comment" enough to recognize one.
    """
    tools.run("SELECT 1 -- a note\nAS a;\n")

    assert _ran(tools) == ["SELECT 1 -- a note\nAS a"]


def test_a_version_comment_is_a_statement_and_runs(tools):
    """`/*!...*/` carries SQL, however much it looks like a comment."""
    tools.run("/*!40101 SET NAMES utf8 */;\n")

    assert _ran(tools) == ["/*!40101 SET NAMES utf8 */"]


def test_an_optimizer_hint_is_not_mistaken_for_a_comment(tools):
    """`/*+...*/` belongs to the statement it leads, and goes with it."""
    tools.run("/*+ MAX_EXECUTION_TIME(1000) */ SELECT 1;\n")

    assert _ran(tools) == ["/*+ MAX_EXECUTION_TIME(1000) */ SELECT 1"]


def test_a_subtraction_of_a_negative_is_not_a_comment():
    """`1--2` is arithmetic. `--` opens a comment only before whitespace.

    Asserted on the predicate rather than through a script, because the
    splitter never offers this shape on its own: it is here so that widening
    the predicate later cannot quietly start dropping arithmetic.
    """
    assert db_functions._is_comment_only("--2") is False
    assert db_functions._is_comment_only("-- 2") is True
    assert db_functions._is_comment_only("--") is True


# --- the interaction with a reopened session --------------------------------


def test_a_leading_comment_does_not_swallow_the_restart_flag(tools):
    """The flag rides on the first ENTRY, which a comment never produces.

    A script that opens a new session says so on its first result. If the
    comment counted as one, the flag would go onto an entry that is not
    returned and the caller would never hear that its session was replaced -
    the silent case, and the dangerous one.
    """
    connection = next(iter(db_functions._sessions.values()))
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1

    results = tools.run(GENERATED_FILE)

    assert results[0]["session_restarted"] is True


class _MultiSetResult:
    """A result over several result sets, walked as the shell walks them.

    Each entry of ``sets`` is ``(columns, rows)`` for a set with data, or
    None for one without - the status a CALL ends on.
    """

    affected_items_count = 0
    warnings_count = 0

    def __init__(self, sets):
        self._sets = sets
        self._at = 0

    def has_data(self):
        return self._sets[self._at] is not None

    def get_columns(self):
        return [
            SimpleNamespace(get_column_label=lambda label=label: label)
            for label in self._sets[self._at][0]
        ]

    def fetch_all(self):
        return self._sets[self._at][1]

    def next_result(self):
        self._at += 1
        return self._at < len(self._sets)

    def get_warnings(self):
        return []


def test_every_result_set_of_a_call_is_read():
    """The first set stays in columns/rows; the rest follow, in order."""
    result = _MultiSetResult([
        (["a"], [[1]]),
        (["b", "c"], [[2, 3], [4, 5]]),
        None,
    ])

    output = db_functions._serialize_result(result)

    assert output["columns"] == ["a"]
    assert output["rows"] == [{"a": 1}]
    assert output["additional_result_sets"] == [
        {"columns": ["b", "c"], "rows": [{"b": 2, "c": 3}, {"b": 4, "c": 5}]}
    ]


def test_a_result_with_no_data_has_no_result_sets():
    """A statement with no result set reports none, and no empty extra list."""
    output = db_functions._serialize_result(_MultiSetResult([None]))

    assert "columns" not in output
    assert "additional_result_sets" not in output


def test_a_result_that_cannot_walk_on_reads_its_one_set():
    """A result without next_result is read as the one set it has."""
    result = _MultiSetResult([(["a"], [[1]])])
    del_next = SimpleNamespace(
        has_data=result.has_data,
        get_columns=result.get_columns,
        fetch_all=result.fetch_all,
        affected_items_count=0,
        warnings_count=0,
        get_warnings=result.get_warnings,
    )

    output = db_functions._serialize_result(del_next)

    assert output["rows"] == [{"a": 1}]
    assert "additional_result_sets" not in output
