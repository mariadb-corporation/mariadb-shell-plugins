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

import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest

# The Context annotation of the db tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import db_functions, general
import mcp_plugin.tests.unit.helpers as helpers

# The header the extension's New SQL Editor button writes at the top of every
# file it generates, followed by a query - the script that first showed this.
GENERATED_FILE = "-- MariaDB connection: dba@localhost:3310\n\nSELECT 1;\n"


class _ModeResult:
    """What SELECT @@sql_mode answers: one row of one value."""

    def __init__(self, sql_mode):
        self._sql_mode = sql_mode

    def fetch_one(self):
        if isinstance(self._sql_mode, Exception):
            raise self._sql_mode

        return [self._sql_mode]


class _RecordingSession:
    """An open session that records every statement it is asked to run.

    It answers SELECT @@sql_mode with its sql_mode - or raises it, when that
    is an exception - and fails the statements in fail_on.
    """

    def __init__(self):
        self.statements = []
        self.closed = False
        self.sql_mode = "STRICT_TRANS_TABLES"
        self.fail_on = set()

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        if sql in self.fail_on:
            raise RuntimeError("no")
        if sql == "SELECT @@sql_mode":
            return _ModeResult(self.sql_mode)

        return helpers.MultiSetResult([None])

    def close(self):
        self.closed = True


@pytest.fixture
def tools(monkeypatch):
    """Registers the db tools over sessions recording what they run."""
    with helpers.stub_db_tools(monkeypatch, _RecordingSession) as stubbed:
        yield stubbed


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
    results = tools.script(GENERATED_FILE)

    assert _ran(tools) == ["SELECT 1"]
    assert len(results) == 1


def test_a_comment_keeps_its_place_in_the_numbering(tools):
    """The statement after a comment is numbered as if the comment had run.

    A caller splits the script for itself and pairs the results with what it
    split by this number. Numbering the query 0 here would hand its result to
    the caller's statement 0 - the comment.
    """
    results = tools.script(GENERATED_FILE)

    assert results[0]["statement_index"] == 1


def test_a_hash_comment_is_left_out_too(tools):
    """The splitter treats `# ...` the same way, and so does this."""
    results = tools.script("# a note\nSELECT 1;\n")

    assert _ran(tools) == ["SELECT 1"]
    assert [result["statement_index"] for result in results] == [1]


def test_a_comment_between_two_statements_is_left_out(tools):
    """A comment anywhere in the script, not only at the top of it."""
    results = tools.script("SELECT 1;\n-- mid\nSELECT 2;\n")

    assert _ran(tools) == ["SELECT 1", "SELECT 2"]
    # Zero, the comment, two: the numbering counts what was split, not what
    # was run.
    assert [result["statement_index"] for result in results] == [0, 2]


def test_a_script_of_nothing_but_comments_runs_nothing(tools):
    """Nothing reaches the server, and there is nothing to report."""
    assert tools.script("-- one\n-- two\n") == []
    assert _ran(tools) == []


# --- what is NOT a comment to leave out -------------------------------------


def test_a_comment_inside_a_statement_stays_with_it(tools):
    """The splitter keeps it, so the statement arrives whole.

    Only a comment that OPENS a statement is split out, which is what makes
    "begins with a line comment" enough to recognize one.
    """
    tools.script("SELECT 1 -- a note\nAS a;\n")

    assert _ran(tools) == ["SELECT 1 -- a note\nAS a"]


def test_a_version_comment_is_a_statement_and_runs(tools):
    """`/*!...*/` carries SQL, however much it looks like a comment."""
    tools.script("/*!40101 SET NAMES utf8 */;\n")

    assert _ran(tools) == ["/*!40101 SET NAMES utf8 */"]


def test_an_optimizer_hint_is_not_mistaken_for_a_comment(tools):
    """`/*+...*/` belongs to the statement it leads, and goes with it."""
    tools.script("/*+ MAX_EXECUTION_TIME(1000) */ SELECT 1;\n")

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

    results = tools.script(GENERATED_FILE)

    assert results[0]["session_restarted"] is True



def test_a_failed_first_statement_still_carries_the_restart_flag(
    tools, monkeypatch
):
    """The flag rides on the first entry even when that entry is a failure.

    A statement that fails BECAUSE the session was replaced - on a temporary
    table that went with the old one - is where the caller most needs to
    hear it, and the next entry must not carry it instead.
    """
    connection = next(iter(db_functions._sessions.values()))
    connection.last_used -= general.SESSION_IDLE_TIMEOUT + 1
    assert db_functions._close_idle_sessions() == 1
    open_session = db_functions._open_session

    def _failing_on_the_temporary_table(*args, **kwargs):
        session = open_session(*args, **kwargs)
        session.fail_on = {"SELECT * FROM tmp"}
        return session

    monkeypatch.setattr(
        db_functions, "_open_session", _failing_on_the_temporary_table
    )

    results = tools.script("SELECT * FROM tmp; SELECT 1;", stop_on_error=False)

    assert results[0]["error"] == "no"
    assert results[0]["session_restarted"] is True
    assert "session_restarted" not in results[1]

def test_every_result_set_of_a_call_is_read():
    """Every set is in result_sets, in order; the one without data is not."""
    result = helpers.MultiSetResult([
        (["a"], [[1]]),
        (["b", "c"], [[2, 3], [4, 5]]),
        None,
    ])

    output = db_functions._serialize_result(result)

    assert output["result_sets"] == [
        {"columns": ["a"], "rows": [{"a": 1}]},
        {"columns": ["b", "c"], "rows": [{"b": 2, "c": 3}, {"b": 4, "c": 5}]},
    ]


def test_column_metadata_is_what_the_shell_reports():
    """Nothing is worked out: a BLOB is BYTES with its flags alongside."""
    result = helpers.MultiSetResult([(
        [
            helpers.MetadataColumn("i", "INT", "NOT_NULL NUM"),
            helpers.MetadataColumn("vb", "BYTES", "BINARY "),
            helpers.MetadataColumn("b", "BYTES", "BLOB BINARY "),
        ],
        [[1, b"\x00\xff", b"x"]],
    )])

    output = db_functions._serialize_result(result, column_metadata=True)

    [result_set] = output["result_sets"]
    assert result_set["column_metadata"][0] == {
        "type": "INT",
        "flags": ["NOT_NULL", "NUM"],
    }
    assert [(column["type"], column["flags"])
            for column in result_set["column_metadata"][1:]] == [
        ("BYTES", ["BINARY"]),
        ("BYTES", ["BLOB", "BINARY"]),
    ]
    # Binary values still come as hex text.
    assert result_set["rows"][0]["vb"] == "00ff"


def test_column_metadata_is_left_out_unless_asked_for():
    result = helpers.MultiSetResult([([helpers.MetadataColumn("a", "INT")], [[1]])])

    output = db_functions._serialize_result(result)

    assert "column_metadata" not in output["result_sets"][0]


def test_every_result_set_of_a_call_has_its_own_metadata():
    result = helpers.MultiSetResult([
        ([helpers.MetadataColumn("a", "INT")], [[1]]),
        ([helpers.MetadataColumn("b", "BYTES", "BLOB BINARY")], [[b"x"]]),
        None,
    ])

    output = db_functions._serialize_result(result, column_metadata=True)

    assert [[column["type"] for column in result_set["column_metadata"]]
            for result_set in output["result_sets"]] == [["INT"], ["BYTES"]]


def test_a_result_with_no_data_has_no_result_sets():
    """A statement with no result set reports an empty list of them."""
    output = db_functions._serialize_result(helpers.MultiSetResult([None]))

    assert output["result_sets"] == []
    assert "columns" not in output


def test_a_result_that_cannot_walk_on_reads_its_one_set():
    """A result without next_result is read as the one set it has."""
    result = helpers.MultiSetResult([(["a"], [[1]])])
    del_next = SimpleNamespace(
        has_data=result.has_data,
        get_columns=result.get_columns,
        fetch_all=result.fetch_all,
        affected_items_count=0,
        warnings_count=0,
        get_warnings=result.get_warnings,
    )

    output = db_functions._serialize_result(del_next)

    assert output["result_sets"] == [{"columns": ["a"], "rows": [{"a": 1}]}]


# --- ANSI_QUOTES, and reporting the sql_mode --------------------------------


# A backslash ending a double-quoted identifier: as a string, "a\" would
# escape its closing quote and swallow the next statement.
ENDS_IN_BACKSLASH = 'SELECT "a\\"; SELECT 1;'


def test_double_quotes_are_split_as_a_string_by_default(tools):
    """The default sql_mode's reading: the backslash escapes the quote.

    So the string never closes, and the rest of the script - its last
    semicolon included - is one unfinished statement.
    """
    tools.script(ENDS_IN_BACKSLASH)

    assert _ran(tools) == [ENDS_IN_BACKSLASH]


def test_double_quotes_are_split_as_an_identifier_with_ansi_quotes(tools):
    """The server's reading under ANSI_QUOTES: two statements."""
    tools.script(ENDS_IN_BACKSLASH, ansi_quotes=True)

    assert _ran(tools) == ['SELECT "a\\"', "SELECT 1"]


def test_the_sql_mode_is_not_reported_unless_asked_for(tools):
    """Nothing extra reaches the server, and nothing extra comes back."""
    results = tools.script("SELECT 1; SELECT 2;", ansi_quotes=True)

    assert _ran(tools) == ["SELECT 1", "SELECT 2"]
    assert all(
        "sql_mode" not in result and "ansi_quotes" not in result
        for result in results
    )


def test_the_last_entry_reports_the_split_and_the_sql_mode(tools):
    """Read after the script, so a script that changed it says so."""
    results = tools.script(
        "SELECT 1; SELECT 2;", ansi_quotes=True, report_sql_mode=True
    )

    assert _ran(tools) == ["SELECT 1", "SELECT 2", "SELECT @@sql_mode"]
    assert "sql_mode" not in results[0]
    assert results[-1]["ansi_quotes"] is True
    assert results[-1]["sql_mode"] == "STRICT_TRANS_TABLES"


def test_a_failed_statement_still_carries_the_report(tools):
    """The entry of the statement a script stopped at is the last one."""
    tools.script("SELECT 0;")
    tools.opened[0].fail_on = {"SELECT 2"}

    results = tools.script("SELECT 1; SELECT 2; SELECT 3;", report_sql_mode=True)

    assert results[-1]["error"] == "no"
    assert results[-1]["ansi_quotes"] is False
    assert results[-1]["sql_mode"] == "STRICT_TRANS_TABLES"


def test_a_mode_that_cannot_be_read_leaves_only_the_split(tools):
    """The script's results still come back; sql_mode is left out."""
    tools.script("SELECT 0;")
    tools.opened[0].sql_mode = RuntimeError("gone")

    results = tools.script("SELECT 1;", report_sql_mode=True)

    assert results[-1]["ansi_quotes"] is False
    assert "sql_mode" not in results[-1]


def test_a_script_of_comments_reports_nothing_and_asks_nothing(tools):
    """There is no entry to carry the report."""
    assert tools.script("-- only a note\n", report_sql_mode=True) == []
    assert _ran(tools) == []


# --- SOURCE directives ---------------------------------------------------------


SECTION = "SELECT 2;\nSELECT 3;\n"


def test_a_script_file_runs_with_its_sections(tools, allowed_temp_dir):
    """Relative to the file's own folder, as msm takes them to development/."""
    section = helpers.write_file(allowed_temp_dir, "sections/a.sql", SECTION)
    script = helpers.write_file(
        allowed_temp_dir,
        "dev.sql",
        "SELECT 1;\nSOURCE 'sections/a.sql'; -- the a part\nSELECT 9;\n",
    )

    results = tools.script(None, file_path=script)

    assert _ran(tools) == ["SELECT 1", "SELECT 2", "SELECT 3", "SELECT 9"]
    # The index is the script's own: both included statements are the
    # directive's, and the statement after it keeps its place.
    assert [entry["statement_index"] for entry in results] == [0, 1, 1, 3]
    assert [entry.get("source") for entry in results] == [
        None,
        {"file": os.path.realpath(section), "line": 1},
        {"file": os.path.realpath(section), "line": 2},
        None,
    ]


def test_an_inline_script_takes_relative_paths_from_source_dir(
    tools, allowed_temp_dir
):
    helpers.write_file(allowed_temp_dir, "sections/a.sql", SECTION)

    tools.script("SOURCE 'sections/a.sql';", source_dir=allowed_temp_dir)

    assert _ran(tools) == ["SELECT 2", "SELECT 3"]


def test_an_absolute_path_needs_no_source_dir(tools, allowed_temp_dir):
    section = helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    tools.script(f"SOURCE '{section}';")

    assert _ran(tools) == ["SELECT 2", "SELECT 3"]


@pytest.mark.parametrize(
    "slice_, ran",
    [
        ("", ["SELECT 2", "SELECT 3"]),
        ("[:]", ["SELECT 2", "SELECT 3"]),
        ("[0:9]", ["SELECT 2"]),
        ("[10:]", ["SELECT 3"]),
        ("[ln2:ln2]", ["SELECT 3"]),
        ("[ln1:ln-2]", ["SELECT 2"]),
        ("[ln-1:]", ["SELECT 3"]),
    ],
)
def test_a_slice_selects_by_characters_or_lines(
    tools, allowed_temp_dir, slice_, ran
):
    helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    tools.script(f"SOURCE 'a.sql'{slice_};", source_dir=allowed_temp_dir)

    assert _ran(tools) == ran


def test_a_directive_in_a_procedure_body_is_expanded_before_the_split(
    tools, allowed_temp_dir
):
    """The include is text: it becomes part of the routine, not a statement."""
    helpers.write_file(
        allowed_temp_dir,
        "body.sql",
        "-- the body\nINSERT INTO t VALUES (3);\nSELECT 4;\n-- left out\n",
    )
    script = (
        "DELIMITER %%\n"
        "CREATE PROCEDURE p()\n"
        "BEGIN\n"
        "    SOURCE 'body.sql'[ln2:ln3];\n"
        "END%%\n"
        "DELIMITER ;\n"
        "SELECT 5;\n"
    )

    results = tools.script(script, source_dir=allowed_temp_dir)

    assert _ran(tools) == [
        "CREATE PROCEDURE p()\nBEGIN\n"
        "    INSERT INTO t VALUES (3);\n    SELECT 4;\nEND",
        "SELECT 5",
    ]
    # The routine starts in the script, so it has the script's index and no
    # source; the statement after it keeps its own.
    assert [(entry["statement_index"], entry.get("source")) for entry in results] == [
        (0, None),
        (1, None),
    ]


def test_ansi_quotes_and_limit_apply_to_the_expanded_text(tools, allowed_temp_dir):
    helpers.write_file(allowed_temp_dir, "a.sql", 'SELECT "a\\"; SELECT 1;\nSELECT n FROM t;\n')

    tools.script(
        "SOURCE 'a.sql';", source_dir=allowed_temp_dir, ansi_quotes=True, limit=5
    )

    # Split as the server reads "..." under ANSI_QUOTES - two statements, not
    # one unfinished string - and every SELECT limited.
    assert _ran(tools) == [
        'SELECT "a\\"\nLIMIT 6',
        "SELECT 1\nLIMIT 6",
        "SELECT n FROM t\nLIMIT 6",
    ]


def test_a_failed_included_statement_says_where_it_came_from(
    tools, allowed_temp_dir
):
    section = helpers.write_file(allowed_temp_dir, "a.sql", SECTION)
    tools.opened[0].fail_on = {"SELECT 3"}

    results = tools.script("SOURCE 'a.sql';", source_dir=allowed_temp_dir)

    assert results[-1]["error"] == "no"
    assert results[-1]["statement"] == "SELECT 3"
    assert results[-1]["source"] == {"file": os.path.realpath(section), "line": 2}


def _refused(tools, match, script, **kwargs):
    """Asserts the call is refused with nothing run."""
    with pytest.raises(Exception, match=match):
        tools.script(script, **kwargs)
    assert _ran(tools) == []


def test_an_include_outside_the_allowed_paths_is_refused(tools, allowed_temp_dir):
    with tempfile.TemporaryDirectory() as elsewhere:
        outside = helpers.write_file(elsewhere, "a.sql", SECTION)

        _refused(
            tools,
            r"SOURCE '.*a\.sql' on line 2 of the script: Access to path .* is not allowed",
            f"SELECT 1;\nSOURCE '{outside}';",
            source_dir=allowed_temp_dir,
        )


def test_dot_dot_cannot_leave_the_allowed_paths(tools, allowed_temp_dir):
    """Resolved before it is checked, symlinks too."""
    inner = os.path.join(allowed_temp_dir, "inner")
    os.makedirs(inner)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(allowed_temp_dir)) as near:
        helpers.write_file(near, "a.sql", SECTION)
        os.symlink(os.path.join(near, "a.sql"), os.path.join(inner, "link.sql"))
        escape = os.path.join("..", "..", os.path.basename(near), "a.sql")

        _refused(tools, "is not allowed", f"SOURCE '{escape}';", source_dir=inner)
        _refused(tools, "is not allowed", "SOURCE 'link.sql';", source_dir=inner)


def test_a_missing_include_is_refused(tools, allowed_temp_dir):
    _refused(
        tools,
        "SOURCE 'gone.sql' on line 1 of the script: .* is not a file that exists",
        "SOURCE 'gone.sql';\nSELECT 1;",
        source_dir=allowed_temp_dir,
    )


@pytest.mark.parametrize("slice_", ["[ln0:]", "[ln:ln2]", "[-:]"])
def test_a_malformed_slice_is_refused(tools, allowed_temp_dir, slice_):
    helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    _refused(
        tools,
        "neither a character offset nor a line number",
        f"SELECT 1;\nSOURCE 'a.sql'{slice_};",
        source_dir=allowed_temp_dir,
    )


def test_a_source_line_in_an_include_is_refused(tools, allowed_temp_dir):
    """msm would leave it in place, to fail halfway through the script."""
    helpers.write_file(allowed_temp_dir, "b.sql", "SELECT 2;\n")
    helpers.write_file(allowed_temp_dir, "a.sql", "SELECT 1;\nSOURCE 'b.sql';\n")

    _refused(
        tools,
        "holds a SOURCE directive itself",
        "SELECT 0;\nSOURCE 'a.sql';",
        source_dir=allowed_temp_dir,
    )
    # Outside the slice it is never included, and does no harm.
    tools.script("SOURCE 'a.sql'[ln1:ln1];", source_dir=allowed_temp_dir)
    assert _ran(tools) == ["SELECT 1"]


def test_a_relative_include_inline_needs_source_dir(tools):
    _refused(
        tools,
        "SOURCE 'a.sql' on line 1 of the script: a relative path, and no folder",
        "SOURCE 'a.sql';",
    )


def test_a_source_dir_outside_the_allowed_paths_is_refused(tools, allowed_temp_dir):
    with tempfile.TemporaryDirectory() as elsewhere:
        _refused(tools, "is not allowed", "SELECT 1;", source_dir=elsewhere)


def test_source_dir_does_not_go_with_file_path(tools, allowed_temp_dir):
    script = helpers.write_file(allowed_temp_dir, "dev.sql", "SELECT 1;")

    _refused(
        tools,
        "'source_dir' goes with 'sql_script'",
        None,
        file_path=script,
        source_dir=allowed_temp_dir,
    )


def test_the_file_is_named_when_its_directive_fails(tools, allowed_temp_dir):
    script = helpers.write_file(allowed_temp_dir, "dev.sql", "SELECT 1;\n\nSOURCE 'gone.sql';\n")

    _refused(
        tools, f"on line 3 of {re.escape(script)}", None, file_path=script
    )


def test_a_source_dir_that_is_no_folder_is_refused(tools, allowed_temp_dir):
    not_a_folder = helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    _refused(tools, "is no folder", "SELECT 1;", source_dir=not_a_folder)


def test_without_msm_a_directive_is_refused_and_other_scripts_run(
    tools, monkeypatch
):
    """The db tools do not need msm_plugin, until a script holds SOURCE."""
    # Gone as a module and as the package's attribute, as on a shell
    # without the plugin.
    monkeypatch.setitem(sys.modules, "msm_plugin.lib.source", None)
    if "msm_plugin.lib" in sys.modules:
        monkeypatch.delattr(sys.modules["msm_plugin.lib"], "source", raising=False)

    _refused(tools, "needs msm_plugin", "SELECT 1;\nSOURCE '/x.sql';")
    tools.script("SELECT 'SOURCE';")
    assert _ran(tools) == ["SELECT 'SOURCE'"]


def test_included_statements_say_where_they_came_from_in_table_format(
    tools, allowed_temp_dir
):
    section = helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    results = tools.script(
        "SOURCE 'a.sql';", source_dir=allowed_temp_dir, output_format="table"
    )

    assert [entry["source"]["line"] for entry in results] == [1, 2]
    assert {entry["source"]["file"] for entry in results} == {
        os.path.realpath(section)
    }


def test_inline_line_breaks_are_made_newlines_as_a_files_are(tools, allowed_temp_dir):
    """A directive after a lone \\r is expanded, as it is in a file."""
    helpers.write_file(allowed_temp_dir, "a.sql", SECTION)

    tools.script("SELECT 1;\rSOURCE 'a.sql';\r\nSELECT 'x\ry';", source_dir=allowed_temp_dir)

    assert _ran(tools) == ["SELECT 1", "SELECT 2", "SELECT 3", "SELECT 'x\ny'"]
