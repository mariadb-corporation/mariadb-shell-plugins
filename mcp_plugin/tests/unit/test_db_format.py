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

"""Tests for the output_format of db.execute_sql and db.execute_sql_script.

With output_format table a result set comes back as the text grid the
mariadb client prints, so an agent can show it to the user as it is. The
results are stubs carrying the column metadata the shell reports, as what
matters is the text drawn from it; the grid of a real server's result is
checked in test_db_sql.py.
"""

# cSpell:ignore mysqlsh MariaDB

from types import SimpleNamespace

import pytest

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions, general

CLIENT_ADDRESS = "192.0.2.10"
SESSION_ID = "0123456789abcdef0123456789abcdef"


class _Type:
    """A mysql.Type constant: its name is its data."""

    def __init__(self, data):
        self.data = data


def _column(label, kind="STRING", flags=""):
    """A column with the shell's Column getters the tools read."""
    return SimpleNamespace(
        get_column_label=lambda: label,
        get_type=lambda: _Type(kind),
        get_flags=lambda: flags,
    )


class _SetsResult:
    """A result over result sets of (columns, rows), None for a status."""

    affected_items_count = 0
    warnings_count = 0

    def __init__(self, sets):
        self._sets = sets
        self._at = 0

    def has_data(self):
        return self._sets[self._at] is not None

    def get_columns(self):
        return self._sets[self._at][0]

    def fetch_all(self):
        return self._sets[self._at][1]

    def next_result(self):
        self._at += 1
        return self._at < len(self._sets)

    def get_warnings(self):
        return []


def _table(result, column_metadata=False):
    serialized = db_functions._serialize_result(result, column_metadata=True)
    return db_functions._format_result_sets(serialized, "table", column_metadata)


# --- the grid -----------------------------------------------------------------


def test_numbers_go_right_and_the_rest_left():
    result = _SetsResult([(
        [_column("id", "INT", "NOT_NULL NUM"), _column("name")],
        [[1, "a"], [100, "longer"]],
    )])

    [result_set] = _table(result)["result_sets"]

    assert result_set == {
        "table": (
            "+-----+--------+\n"
            "| id  | name   |\n"
            "+-----+--------+\n"
            "|   1 | a      |\n"
            "| 100 | longer |\n"
            "+-----+--------+"
        ),
        "row_count": 2,
    }


def test_null_binary_and_line_breaks_are_written_as_the_client_writes_them():
    result = _SetsResult([(
        [_column("h", "BYTES", "BINARY"), _column("t")],
        [[b"\x00\xff", "a\nb\tc"], [None, None]],
    )])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[3:5] == [
        "| 0x00ff | a\\nb\\tc |",
        "| NULL   | NULL    |",
    ]


def test_wide_characters_take_two_cells():
    result = _SetsResult([([_column("w")], [["日本"], ["abc"]])])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines() == [
        "+------+",
        "| w    |",
        "+------+",
        "| 日本 |",
        "| abc  |",
        "+------+",
    ]


def test_a_set_without_rows_still_shows_its_header():
    result = _SetsResult([([_column("a"), _column("b")], [])])

    [result_set] = _table(result)["result_sets"]

    assert result_set == {
        "table": "+---+---+\n| a | b |\n+---+---+",
        "row_count": 0,
    }


def test_every_result_set_of_a_call_gets_its_own_grid():
    result = _SetsResult([
        ([_column("a", "INT", "NUM")], [[1]]),
        ([_column("b")], [["x"], ["y"]]),
        None,
    ])

    output = _table(result)

    assert [result_set["row_count"] for result_set in output["result_sets"]] == [1, 2]
    assert "| b |" in output["result_sets"][1]["table"]


def test_column_metadata_is_kept_only_when_asked_for():
    def result():
        return _SetsResult([([_column("a", "INT", "NUM")], [[1]])])

    assert "column_metadata" not in _table(result())["result_sets"][0]
    assert _table(result(), column_metadata=True)["result_sets"][0][
        "column_metadata"
    ] == [{"type": "INT", "flags": ["NUM"]}]


def test_json_is_left_as_it_was():
    serialized = db_functions._serialize_result(
        _SetsResult([([_column("a")], [["x"]])])
    )

    assert db_functions._format_result_sets(serialized, "json", False)[
        "result_sets"
    ] == [{"columns": ["a"], "rows": [{"a": "x"}]}]


# --- through the tools --------------------------------------------------------


class _Session:
    """Answers every statement with rows of one numeric column n."""

    def __init__(self):
        self.statements = []

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        count = 3
        if "\nLIMIT " in sql:
            count = int(sql.rsplit("\nLIMIT ", 1)[1])

        return _SetsResult([(
            [_column("n", "BIGINT", "NOT_NULL NUM")],
            [[value] for value in range(1, count + 1)],
        )])

    def close(self):
        pass


@pytest.fixture
def tools(monkeypatch):
    """Registers the db tools over a session answering three rows."""
    uri = "root@127.0.0.1:3306"

    monkeypatch.setattr(
        db_functions,
        "_open_session",
        lambda _uri, kind=None, mcp_user_id=None: _Session(),
    )
    monkeypatch.setattr(
        db_functions.config,
        "list_stored_connection_uris",
        lambda kind=None, mcp_user_id=None: [uri],
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
        )
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


def test_a_statement_comes_back_as_a_grid(tools):
    result = tools.sql("SELECT n FROM t", output_format="table")

    assert result["result_sets"] == [{
        "table": "+---+\n| n |\n+---+\n| 1 |\n| 2 |\n| 3 |\n+---+",
        "row_count": 3,
    }]


def test_a_page_is_drawn_without_the_extra_row(tools):
    """The row fetched to tell whether there is more is not in the grid."""
    result = tools.sql("SELECT n FROM t", limit=2, output_format="table")

    assert result["result_sets"] == [{
        "table": "+---+\n| n |\n+---+\n| 1 |\n| 2 |\n+---+",
        "row_count": 2,
        "has_more_pages": True,
    }]


def test_every_statement_of_a_script_comes_back_as_a_grid(tools):
    results = tools.script("SELECT n FROM t; SELECT n FROM u;", output_format="table")

    assert [entry["result_sets"][0]["row_count"] for entry in results] == [3, 3]
    assert all("rows" not in entry["result_sets"][0] for entry in results)


def test_an_unknown_format_is_refused(tools):
    with pytest.raises(Exception, match="'output_format' must be"):
        tools.sql("SELECT n FROM t", output_format="csv")
    with pytest.raises(Exception, match="'output_format' must be"):
        tools.script("SELECT n FROM t;", output_format="vertical")
