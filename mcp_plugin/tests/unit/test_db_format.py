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

# cSpell:ignore mysqlsh MariaDB timedelta timedeltas

import datetime

import pytest

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions
import mcp_plugin.tests.unit.helpers as helpers


def _table(result, column_metadata=False):
    return db_functions._shape_result(result, False, None, "table", column_metadata)


# --- the grid -----------------------------------------------------------------


def test_numbers_go_right_and_the_rest_left():
    result = helpers.MultiSetResult([(
        [helpers.MetadataColumn("id", "INT", "NOT_NULL NUM"), "name"],
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
    result = helpers.MultiSetResult([(
        [helpers.MetadataColumn("h", "BYTES", "BINARY"), "t"],
        [[b"\x00\xff", "a\nb\tc"], [None, None]],
    )])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[3:5] == [
        "| 0x00FF | a\\nb\\tc |",
        "| NULL   | NULL    |",
    ]


def test_a_backslash_and_a_label_line_break_are_escaped():
    result = helpers.MultiSetResult([(
        ["a\nb"],
        [["c:\\new"], ["x\ny"]],
    )])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines() == [
        "+---------+",
        "| a\\nb    |",
        "+---------+",
        "| c:\\\\new |",
        "| x\\ny    |",
        "+---------+",
    ]


def test_wide_characters_take_two_cells():
    result = helpers.MultiSetResult([(["w"], [["日本"], ["abc"]])])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines() == [
        "+------+",
        "| w    |",
        "+------+",
        "| 日本 |",
        "| abc  |",
        "+------+",
    ]



def test_zero_width_characters_take_no_cell():
    """Thai vowel signs, a variation selector and a joiner draw in no cell."""
    result = helpers.MultiSetResult([(["w"], [["\u0e01\u0e31"], ["a\u200db\ufe0f"]])])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[3:5] == [
        "| \u0e01\u0e31  |",
        "| a\u200db\ufe0f |",
    ]


def test_other_control_characters_are_escaped_too():
    """None of them may break the line or reach the terminal raw."""
    result = helpers.MultiSetResult([(["t"], [["a\x0bb\x0c\x1b[2J\x85\u2028z"]])])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[3] == (
        "| a\\x0bb\\x0c\\x1b[2J\\x85\\u2028z |"
    )


def test_labels_are_the_ones_the_server_sent():
    """Two columns called id are both id: the _2 suffix is for JSON keys."""
    result = helpers.MultiSetResult([(["id", "id"], [[1, 2]])])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[1] == "| id | id |"


@pytest.mark.parametrize(
    "value, text",
    [
        # As the mariadb client shows them, from a 12.3 server.
        (1.1, "1.1"),
        (123456.0, "123456"),
        (1234570.0, "1234570"),
        (1e15, "1e15"),
        (1e16, "1e16"),
        (1e21, "1e21"),
        (3.4e38, "3.4e38"),
        (1.7976931348623157e308, "1.7976931348623157e308"),
        (1.2345678901234567e19, "1.2345678901234567e19"),
        (0.30000000000000004, "0.30000000000000004"),
        (1e-4, "0.0001"),
        (1e-5, "0.00001"),
        (1e-7, "0.0000001"),
        (-2.5, "-2.5"),
        (-0.0001, "-0.0001"),
        (100.0, "100"),
        (0.0, "0"),
        # Where my_gcvt turns to the e format below 1.
        (1e-15, "0.000000000000001"),
        (1e-16, "1e-16"),
        (-1.5e-20, "-1.5e-20"),
    ],
)
def test_a_double_is_written_as_the_server_writes_it(value, text):
    assert db_functions._double_text(value) == text


def test_bit_and_bool_values_are_written_as_the_client_writes_them():
    """BIT as the bytes of its column, in hex; a bool as 1 or 0."""
    result = helpers.MultiSetResult([(
        [
            helpers.MetadataColumn("b16", "BIT", "UNSIGNED", length=16),
            helpers.MetadataColumn("b1", "BIT", "UNSIGNED", length=1),
            helpers.MetadataColumn("t", "INT", "NUM"),
            helpers.MetadataColumn("d", "DOUBLE", "NUM"),
        ],
        [[5, 1, True, 1e16]],
    )])

    [result_set] = _table(result)["result_sets"]

    assert result_set["table"].splitlines()[3] == "| 0x0005 | 0x01 | 1 | 1e16 |"

def test_a_set_without_rows_still_shows_its_header():
    result = helpers.MultiSetResult([(["a", "b"], [])])

    [result_set] = _table(result)["result_sets"]

    assert result_set == {
        "table": "+---+---+\n| a | b |\n+---+---+",
        "row_count": 0,
    }


def test_every_result_set_of_a_call_gets_its_own_grid():
    result = helpers.MultiSetResult([
        ([helpers.MetadataColumn("a", "INT", "NUM")], [[1]]),
        (["b"], [["x"], ["y"]]),
        None,
    ])

    output = _table(result)

    assert [result_set["row_count"] for result_set in output["result_sets"]] == [1, 2]
    assert "| b |" in output["result_sets"][1]["table"]


def test_column_metadata_is_kept_only_when_asked_for():
    def result():
        return helpers.MultiSetResult([([helpers.MetadataColumn("a", "INT", "NUM")], [[1]])])

    assert "column_metadata" not in _table(result())["result_sets"][0]
    assert _table(result(), column_metadata=True)["result_sets"][0][
        "column_metadata"
    ] == [{"type": "INT", "flags": ["NUM"]}]



def _time_result():
    """A TIME(2) column with the values a fixed shell returns as timedeltas."""
    return helpers.MultiSetResult([(
        [helpers.MetadataColumn("t", "TIME", "BINARY", fractional_digits=2)],
        [
            [-datetime.timedelta(hours=838, minutes=59, seconds=59)],
            [datetime.timedelta(hours=838, minutes=59, seconds=59)],
            [-datetime.timedelta(hours=12, microseconds=250000)],
            [-datetime.timedelta(minutes=30)],
            [datetime.timedelta(days=1)],
            [datetime.time(10, 20, 30, 500000)],
        ],
    )])


# As the server writes them, from a 12.3 server's TIME(2) column.
_SERVER_TIMES = [
    "-838:59:59.00",
    "838:59:59.00",
    "-12:00:00.25",
    "-00:30:00.00",
    "24:00:00.00",
    "10:20:30.50",
]


def test_a_time_outside_one_day_is_written_as_the_server_writes_it():
    [result_set] = _table(_time_result())["result_sets"]

    assert [
        line.strip("| ") for line in result_set["table"].splitlines()[3:-1]
    ] == _SERVER_TIMES


def test_a_time_outside_one_day_comes_as_the_servers_text_in_json():
    """Not as str(timedelta), which says -1 day, 11:59:59.750000."""
    output = db_functions._shape_result(_time_result(), False, None, "json", False)

    [result_set] = output["result_sets"]
    # The time of day is left to the str() it always had.
    assert [row["t"] for row in result_set["rows"]][:5] == _SERVER_TIMES[:5]

def test_json_is_left_as_it_was():
    result = helpers.MultiSetResult([(["a"], [["x"]])])

    assert db_functions._shape_result(result, False, None, "json", False)[
        "result_sets"
    ] == [{"columns": ["a"], "rows": [{"a": "x"}]}]


# --- through the tools --------------------------------------------------------


@pytest.fixture
def tools(monkeypatch):
    """Registers the db tools over a session answering three rows."""
    with helpers.stub_db_tools(
        monkeypatch,
        lambda: helpers.TableSession(
            range(1, 4), helpers.MetadataColumn("n", "BIGINT", "NOT_NULL NUM")
        ),
    ) as stubbed:
        yield stubbed


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
