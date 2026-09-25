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

"""Tests for the limit and offset of db.execute_sql and db.execute_sql_script.

A client pages through a large result by asking for a limit: every SELECT
that can take one is given ``LIMIT limit+1``, the extra row is dropped again,
and ``has_more_pages`` says whether it came back. What can take a LIMIT is
decided by the statement's top-level words, so most of these are about
telling a SELECT that can from one that cannot - and about never changing
anything else.

The session is a stub that records what reached it and answers with the rows
it is given, because what matters is the SQL that was sent and what came back
of the rows, not what a server made of them. The flow against a real server
is in test_db_sql.py.
"""

# cSpell:ignore mysqlsh MariaDB NOWAIT

from types import SimpleNamespace

import pytest

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

import mysqlsh

from mcp_plugin.lib import db_functions, general

CLIENT_ADDRESS = "192.0.2.10"
SESSION_ID = "0123456789abcdef0123456789abcdef"

limit_statement = db_functions._limit_statement


# --- which statements get a LIMIT --------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM t",
        "select a from t where b = 1 order by a",
        "SELECT 1",
        "SELECT * FROM t1 UNION SELECT * FROM t2",
        "(SELECT a FROM t1) UNION (SELECT a FROM t2 LIMIT 5)",
        "WITH c AS (SELECT 1 AS a) SELECT * FROM c",
        "WITH RECURSIVE c AS (SELECT 1 UNION SELECT 2) SELECT * FROM c",
        "SELECT * FROM (SELECT * FROM t LIMIT 10) AS d",
        "SELECT * FROM t WHERE a IN (SELECT a FROM u LIMIT 1)",
        "SELECT * FROM t FOR SYSTEM_TIME ALL",
        "SELECT 'LIMIT 5' AS a",
        "SELECT `limit` FROM t",
        "SELECT a /* LIMIT 5 */ FROM t",
        "-- LIMIT 5\nSELECT a FROM t",
        "SELECT a, ROW_NUMBER() OVER w FROM t WINDOW w AS (ORDER BY a)",
        "SELECT \"it's\" FROM t",
        "SELECT 'a\\'b' FROM t",
    ],
)
def test_a_select_that_can_take_one_gets_a_limit(statement):
    assert limit_statement(statement, 201) == f"{statement}\nLIMIT 201"


@pytest.mark.parametrize(
    "statement",
    [
        # Limits itself already.
        "SELECT * FROM t LIMIT 10",
        "SELECT * FROM t LIMIT 10 OFFSET 5",
        "SELECT * FROM t LIMIT 5, 10",
        "SELECT * FROM t ORDER BY a FETCH FIRST 3 ROWS ONLY",
        "SELECT * FROM t ORDER BY a OFFSET 2 ROWS",
        "SELECT * FROM t1 UNION SELECT * FROM t2 LIMIT 3",
        # A LIMIT would have to come before these clauses.
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t FOR UPDATE NOWAIT",
        "SELECT * FROM t FOR SHARE",
        "SELECT * FROM t LOCK IN SHARE MODE",
        "SELECT a INTO @x FROM t",
        "SELECT a FROM t INTO @x",
        "SELECT * FROM t INTO OUTFILE '/tmp/t.txt'",
        "SELECT * FROM t PROCEDURE ANALYSE()",
        # Not a SELECT at all.
        "INSERT INTO t SELECT * FROM u",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "SHOW TABLES",
        "EXPLAIN SELECT * FROM t",
        "CALL p()",
        "VALUES (1), (2)",
        "WITH c AS (SELECT 1 AS a) INSERT INTO t SELECT * FROM c",
        "",
        "-- only a comment",
    ],
)
def test_anything_else_is_left_as_written(statement):
    assert limit_statement(statement, 201) is None


def test_no_limit_asked_for_changes_nothing():
    assert limit_statement("SELECT * FROM t", None) is None


def test_an_offset_goes_after_the_limit():
    assert (
        limit_statement("SELECT * FROM t", 201, 400)
        == "SELECT * FROM t\nLIMIT 201 OFFSET 400"
    )


def test_a_zero_offset_is_left_out():
    assert limit_statement("SELECT 1", 3, 0) == "SELECT 1\nLIMIT 3"


def test_the_limit_is_not_swallowed_by_a_trailing_comment():
    """On a line of its own, so a closing -- comment cannot comment it out."""
    assert (
        limit_statement("SELECT a FROM t -- the rows\n", 5)
        == "SELECT a FROM t -- the rows\nLIMIT 5"
    )


# --- the arguments ------------------------------------------------------------


@pytest.mark.parametrize(
    "limit, offset",
    [(-1, None), (1.5, None), ("10", None), (True, None), (10, -1), (None, 5)],
)
def test_a_limit_or_offset_that_is_no_row_count_is_refused(limit, offset):
    with pytest.raises(mysqlsh.Error):
        db_functions._check_paging(limit, offset)


def test_a_whole_number_of_rows_is_fine():
    db_functions._check_paging(None)
    db_functions._check_paging(0)
    db_functions._check_paging(200, 400)


# --- cutting the result back --------------------------------------------------


def test_the_extra_row_says_there_is_more_and_is_dropped():
    output = db_functions._page_result({"rows": [1, 2, 3]}, 2)

    assert output == {"rows": [1, 2], "has_more_pages": True}


def test_no_extra_row_is_the_last_page():
    output = db_functions._page_result({"rows": [1, 2]}, 2)

    assert output == {"rows": [1, 2], "has_more_pages": False}


def test_a_result_without_rows_has_no_more_pages():
    output = db_functions._page_result({"affected_items_count": 0}, 2)

    assert output["has_more_pages"] is False


class _SyntaxError(Exception):
    """A server error, carrying its code as a mysqlsh.DBError does."""

    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


class _RefusingSession:
    """Refuses any statement that ends in a LIMIT, with the given code."""

    def __init__(self, code):
        self.code = code
        self.statements = []

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        if "\nLIMIT" in sql:
            raise _SyntaxError(self.code)

        return "result"


def test_a_limit_the_server_finds_bad_syntax_is_run_without():
    """1064 means nothing ran, so the statement can go again as written."""
    session = _RefusingSession(1064)

    result, limited = db_functions._run_limited(session, "SELECT 1", [], 5)

    assert (result, limited) == ("result", False)
    assert session.statements == ["SELECT 1\nLIMIT 6", "SELECT 1"]


def test_any_other_failure_is_not_retried():
    """It may have run, and running it twice could take effect twice."""
    session = _RefusingSession(1146)

    with pytest.raises(_SyntaxError):
        db_functions._run_limited(session, "SELECT 1", [], 5)
    assert session.statements == ["SELECT 1\nLIMIT 6"]


# --- through the tools --------------------------------------------------------


class _RowsResult:
    """A result with one column, holding the rows it is given."""

    affected_items_count = 0
    warnings_count = 0

    def __init__(self, rows):
        self._rows = rows
        self._read = False

    def has_data(self):
        return not self._read

    def get_columns(self):
        return [SimpleNamespace(get_column_label=lambda: "n")]

    def fetch_all(self):
        return [[value] for value in self._rows]

    def next_result(self):
        self._read = True

        return False

    def get_warnings(self):
        return []


class _TableSession:
    """Answers every SELECT from a table of rows, honouring its LIMIT."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        rows = self.rows
        if "\nLIMIT " in sql:
            parts = sql.rsplit("\nLIMIT ", 1)[1].split(" OFFSET ")
            offset = int(parts[1]) if len(parts) > 1 else 0
            rows = rows[offset:offset + int(parts[0])]

        return _RowsResult(rows)

    def close(self):
        pass


@pytest.fixture
def tools(monkeypatch):
    """Registers the db tools over a session answering from 25 rows."""
    uri = "root@127.0.0.1:3306"
    opened = []

    def _fake_open_session(_uri, kind=None):
        session = _TableSession(list(range(25)))
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
            script=lambda script, **kwargs: recorder.tools[
                "db.execute_sql_script"
            ](context, connection_id, script, **kwargs),
            sql=lambda sql, **kwargs: recorder.tools["db.execute_sql"](
                context, connection_id, sql, **kwargs
            ),
            opened=opened,
        )
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


def _values(result):
    return [row["n"] for row in result["rows"]]


def test_a_script_returns_the_first_page_of_each_select(tools):
    results = tools.script("SELECT n FROM t; SELECT n FROM t LIMIT 3;", limit=10)

    assert tools.opened[0].statements == [
        "SELECT n FROM t\nLIMIT 11",
        "SELECT n FROM t LIMIT 3",
    ]
    assert _values(results[0]) == list(range(10))
    assert results[0]["has_more_pages"] is True
    # Its own LIMIT: run as written, all of its rows, nothing to page.
    assert "has_more_pages" not in results[1]


def test_a_script_without_a_limit_is_unchanged(tools):
    results = tools.script("SELECT n FROM t;")

    assert tools.opened[0].statements == ["SELECT n FROM t"]
    assert len(results[0]["rows"]) == 25
    assert "has_more_pages" not in results[0]


def test_a_statement_pages_on_with_an_offset(tools):
    second = tools.sql("SELECT n FROM t", limit=10, offset=10)
    last = tools.sql("SELECT n FROM t", limit=10, offset=20)

    assert _values(second) == list(range(10, 20))
    assert second["has_more_pages"] is True
    assert _values(last) == list(range(20, 25))
    assert last["has_more_pages"] is False
    assert tools.opened[0].statements[-1] == "SELECT n FROM t\nLIMIT 11 OFFSET 20"


def test_a_page_that_ends_exactly_on_the_limit_is_the_last(tools):
    """25 rows in pages of 5: the fifth has five rows and nothing after."""
    page = tools.sql("SELECT n FROM t", limit=5, offset=20)

    assert _values(page) == [20, 21, 22, 23, 24]
    assert page["has_more_pages"] is False


def test_a_statement_that_cannot_be_limited_runs_as_written(tools):
    result = tools.sql("SELECT n FROM t FOR UPDATE", limit=10, offset=10)

    assert tools.opened[0].statements == ["SELECT n FROM t FOR UPDATE"]
    assert "has_more_pages" not in result


def test_a_bad_limit_is_refused_before_anything_runs(tools):
    # The registrar hands the refusal to the client as the SDK's ToolError.
    with pytest.raises(Exception, match="needs a 'limit'"):
        tools.sql("SELECT n FROM t", offset=10)
    with pytest.raises(Exception, match="non-negative integer"):
        tools.script("SELECT n FROM t;", limit=-1)

    assert tools.opened[0].statements == []
