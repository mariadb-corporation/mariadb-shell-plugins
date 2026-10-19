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

"""Tests for the sql group: sql.check, sql.outline and sql.format.

Most run against a stand-in mariadb-lsp - a shell script that records the
command line it was given and prints a fixed answer - because what matters
there is how the tools call the binary and what they make of its answer. The
rest run the real binary, where one is found, and skip where it is not.
"""

# cSpell:ignore mcpserver SELEC

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

# The Context annotation of the tools comes from the MCP SDK.
pytest.importorskip("mcp")

from mcp_plugin.lib import config, general, sql_functions
import mcp_plugin.tests.unit.helpers as helpers

CHECK_ANSWER = {
    "files": [
        {
            "file": "-",
            "statements": 2,
            "errors": [
                {
                    "start": {"line": 1, "character": 3},
                    "offset": 13,
                    "length": 5,
                    "token": "IDENT",
                    "message": "syntax error near 'SELEC'",
                    "expected": ["SELECT"],
                }
            ],
            "notes": [],
        }
    ],
    "errors": 1,
}


@pytest.fixture
def lsp_settings(tmp_path, monkeypatch):
    """A settings file of the test's own, so lspPath starts unset."""
    monkeypatch.setattr(
        config, "get_settings_file_path", lambda: str(tmp_path / "settings.json")
    )


def _fake_lsp(folder, answer, exit_code=0, version="mariadb-lsp 26.9.4"):
    """Writes a mariadb-lsp that records its arguments and prints answer."""
    path = os.path.join(folder, "mariadb-lsp")
    record = os.path.join(folder, "calls.jsonl")
    with open(path, "w", encoding="utf-8") as script:
        script.write(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            f"    print({version!r}); sys.exit(0)\n"
            f"with open({record!r}, 'a') as calls:\n"
            "    calls.write(json.dumps({'argv': sys.argv[1:], 'stdin':"
            " sys.stdin.read() if sys.argv[-1] == '-' else None}) + '\\n')\n"
            f"sys.stdout.write({answer!r})\n"
            f"sys.exit({exit_code})\n"
        )
    os.chmod(path, 0o755)
    return path


def _calls(folder):
    with open(os.path.join(folder, "calls.jsonl"), encoding="utf-8") as calls:
        return [json.loads(line) for line in calls]


def _tools(monkeypatch):
    """Registers the sql tools and returns them, called as a stdio client."""
    recorder = helpers.ToolRecorder()
    sql_functions.register_sql_tools(recorder)
    context = SimpleNamespace(request_context=SimpleNamespace(request=None))

    return SimpleNamespace(
        names=sorted(recorder.tools),
        check=lambda **kwargs: recorder.tools["sql.check"](context, **kwargs),
        outline=lambda **kwargs: recorder.tools["sql.outline"](context, **kwargs),
        format=lambda **kwargs: recorder.tools["sql.format"](context, **kwargs),
    )


@pytest.fixture
def fake(tmp_path, monkeypatch, lsp_settings, allowed_temp_dir):
    """The sql tools over a stand-in mariadb-lsp answering CHECK_ANSWER."""
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    config.set_lsp_path(_fake_lsp(str(binary_dir), json.dumps(CHECK_ANSWER), 1))
    tools = _tools(monkeypatch)
    tools.folder = str(binary_dir)
    tools.allowed = allowed_temp_dir
    tools.calls = lambda: _calls(str(binary_dir))

    return tools


# --- finding the binary ----------------------------------------------------------


def test_no_binary_no_group(tmp_path, monkeypatch, lsp_settings, capsys):
    """Nowhere to be found: no tool is offered, and the log says why."""
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(general, "get_bin_home", lambda: str(tmp_path / "bin"))
    recorder = helpers.ToolRecorder()

    sql_functions.register_sql_tools(recorder)

    assert recorder.tools == {}
    assert "sql tools not registered" in capsys.readouterr().err


def test_the_configured_binary_or_its_folder_is_used(tmp_path, lsp_settings):
    binary = _fake_lsp(str(tmp_path), "")

    config.set_lsp_path(binary)
    assert sql_functions.find_lsp() == binary
    config.set_lsp_path(str(tmp_path))
    assert sql_functions.find_lsp() == binary


def test_then_the_path_then_local_bin(tmp_path, monkeypatch, lsp_settings):
    on_path = tmp_path / "on_path"
    local_bin = tmp_path / "local_bin"
    on_path.mkdir()
    local_bin.mkdir()
    monkeypatch.setattr(general, "get_bin_home", lambda: str(local_bin))
    in_local_bin = _fake_lsp(str(local_bin), "")
    monkeypatch.setenv("PATH", str(on_path))

    assert sql_functions.find_lsp() == in_local_bin
    assert sql_functions.find_lsp() == in_local_bin
    found_on_path = _fake_lsp(str(on_path), "")
    assert sql_functions.find_lsp() == found_on_path


def test_a_binary_of_that_name_that_is_not_mariadb_lsp_is_not_used(
    tmp_path, monkeypatch, lsp_settings
):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(general, "get_bin_home", lambda: str(tmp_path / "none"))
    _fake_lsp(str(tmp_path), "", version="something else 1.0")

    assert sql_functions.find_lsp() is None


def test_with_a_binary_the_three_tools_are_offered(fake):
    assert fake.names == ["sql.check", "sql.format", "sql.outline"]


# --- positions -------------------------------------------------------------------


def test_positions_count_from_one_in_characters():
    """LSP's 0-based UTF-16 columns become an editor's 1-based characters."""
    lines = ["SELECT '😀' + x"]

    # The emoji is two UTF-16 units and one character: x is unit 13, char 12.
    assert sql_functions._lsp_position(lines, {"line": 0, "character": 13}) == {
        "line": 1,
        "column": 13,
    }
    assert sql_functions._lsp_position(lines, {"line": 0, "character": 0}) == {
        "line": 1,
        "column": 1,
    }


def test_positions_of_an_included_object_are_in_its_file():
    converted = sql_functions._with_positions(
        {"tables": [{"name": "t", "source": 0,
                     "range": {"start": {"line": 0, "character": 2}, "end": {"line": 0, "character": 4}}}],
         "includes": [{"file": "/p/a.sql", "range": {"start": {"line": 1, "character": 0},
                                                     "end": {"line": 1, "character": 5}}}]},
        ["x", "SOURCE 'a.sql';"],
        (("/p/a.sql", ["😀😀 t"]),),
    )

    assert converted["tables"][0] == {
        "name": "t",
        "source": "/p/a.sql",
        # Each emoji is two UTF-16 units and one character.
        "range": {"start": {"line": 1, "column": 2}, "end": {"line": 1, "column": 3}},
    }
    # The directive itself is in the script.
    assert converted["includes"][0]["range"]["start"] == {"line": 2, "column": 1}


def test_every_position_of_an_answer_is_converted():
    converted = sql_functions._with_positions(
        {"range": {"start": {"line": 0, "character": 1}, "end": {"line": 2, "character": 0}},
         "name": "t"},
        ["ab", "", "c"],
    )

    assert converted == {
        "range": {"start": {"line": 1, "column": 2}, "end": {"line": 3, "column": 1}},
        "name": "t",
    }


# --- calling the binary ----------------------------------------------------------


def test_check_reports_the_errors_with_editor_positions(fake):
    result = fake.check(sql="SELECT 1;\n   SELEC 2;\n")

    assert result == {
        "valid": False,
        "statements": 2,
        "errors": [
            {
                "start": {"line": 2, "column": 4},
                "token": "IDENT",
                "message": "syntax error near 'SELEC'",
                "expected": ["SELECT"],
            }
        ],
        "notes": [],
    }
    [call] = fake.calls()
    assert call["argv"] == ["check", "--format", "json", "-"]
    assert call["stdin"] == "SELECT 1;\n   SELEC 2;\n"


def test_the_options_are_passed_on(fake):
    fake.check(
        sql="SELECT 1;", sql_mode="ANSI_QUOTES", delimiter="$$", version_id=110402,
        max_errors=5,
    )

    [call] = fake.calls()
    assert call["argv"] == [
        "check", "--format", "json", "--sql-mode", "ANSI_QUOTES", "--delimiter",
        "$$", "--version-id", "110402", "--max-errors", "5", "-",
    ]


def test_a_file_is_read_by_the_binary_from_its_own_folder(fake):
    """So its SOURCE paths are relative to it - no --source-dir needed."""
    script = helpers.write_file(fake.allowed, "dev.sql", "SELECT 1;\n   SELEC 2;\n")

    fake.check(file_path=script)

    [call] = fake.calls()
    assert call["argv"] == ["check", "--format", "json", script]
    assert call["stdin"] is None


def test_inline_sql_takes_its_source_paths_from_source_dir(fake):
    helpers.write_file(fake.allowed, "sections/a.sql", "SELECT 2;\n")

    fake.check(sql="SOURCE 'sections/a.sql';\n", source_dir=fake.allowed)

    [call] = fake.calls()
    assert call["argv"] == [
        "check", "--format", "json", "--source-dir", fake.allowed, "-",
    ]


def test_an_include_outside_the_allowed_paths_is_refused_before_the_binary_runs(
    fake, tmp_path
):
    outside = helpers.write_file(str(tmp_path / "outside"), "a.sql", "SELECT 2;\n")

    with pytest.raises(Exception, match="SOURCE '.*a.sql' on line 2 of the SQL: Access"):
        fake.check(sql=f"SELECT 1;\nSOURCE '{outside}';\n")
    with pytest.raises(Exception, match="not allowed"):
        fake.outline(sql="SOURCE '../outside/a.sql';", source_dir=fake.allowed)

    assert not os.path.exists(os.path.join(fake.folder, "calls.jsonl"))


def test_a_missing_include_is_left_to_the_binary_to_report(fake):
    fake.check(sql="SOURCE 'gone.sql';", source_dir=fake.allowed)

    assert len(fake.calls()) == 1


def test_without_msm_the_binary_reads_no_include(fake, monkeypatch):
    monkeypatch.setitem(sys.modules, "msm_plugin.lib.source", None)
    if "msm_plugin.lib" in sys.modules:
        monkeypatch.delattr(sys.modules["msm_plugin.lib"], "source", raising=False)

    fake.check(sql="SOURCE '/etc/passwd';")

    [call] = fake.calls()
    assert "--no-source" in call["argv"]


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({}, "exactly one of 'sql' or 'file_path'"),
        ({"sql": "SELECT 1", "file_path": "/x.sql"}, "exactly one of"),
        ({"file_path": "/etc/hosts"}, "is not allowed"),
        ({"sql": "SELECT 1", "source_dir": "/etc"}, "is not allowed"),
    ],
)
def test_bad_input_is_refused_before_the_binary_runs(fake, arguments, message):
    with pytest.raises(Exception, match=message):
        fake.check(**arguments)

    assert not os.path.exists(os.path.join(fake.folder, "calls.jsonl"))


def test_source_dir_does_not_go_with_file_path(fake):
    script = helpers.write_file(fake.allowed, "dev.sql", "SELECT 1;")

    with pytest.raises(Exception, match="'source_dir' goes with 'sql'"):
        fake.check(file_path=script, source_dir=fake.allowed)


def test_a_failing_binary_is_reported_with_what_it_said(tmp_path, monkeypatch, lsp_settings):
    script = os.path.join(tmp_path, "mariadb-lsp")
    with open(script, "w", encoding="utf-8") as fake_binary:
        fake_binary.write(
            "#!/bin/sh\n"
            'if [ "$1" = --version ]; then echo "mariadb-lsp 26.9.4"; exit 0; fi\n'
            'echo "unknown option --frobnicate" >&2; exit 2\n'
        )
    os.chmod(script, 0o755)
    config.set_lsp_path(script)

    with pytest.raises(Exception, match="mariadb-lsp format failed: unknown option"):
        _tools(monkeypatch).format(sql="SELECT 1")


def test_format_returns_the_text(tmp_path, monkeypatch, lsp_settings):
    config.set_lsp_path(_fake_lsp(str(tmp_path), "SELECT\n  1;\n"))

    tools = _tools(monkeypatch)
    result = tools.format(sql="select 1;", line_width=40, keyword_case="lower", indent="4")

    assert result == {"sql": "SELECT\n  1;\n"}
    [call] = _calls(str(tmp_path))
    assert call["argv"] == [
        "format", "--line-width", "40", "--keyword-case", "lower", "--indent", "4", "-",
    ]


# --- the real binary ------------------------------------------------------------

REAL_LSP = sql_functions.find_lsp()
real = pytest.mark.skipif(REAL_LSP is None, reason="no mariadb-lsp found")


def _has_source_dir() -> bool:
    if REAL_LSP is None:
        return False
    usage = subprocess.run(
        [REAL_LSP, "--help"], capture_output=True, text=True, check=False
    ).stdout

    return "--source-dir" in usage


@pytest.fixture
def lsp(monkeypatch, allowed_temp_dir):
    tools = _tools(monkeypatch)
    tools.allowed = allowed_temp_dir

    return tools


@real
def test_the_real_check(lsp):
    assert lsp.check(sql="SELECT 1;\nCREATE TABLE t (id INT);\n") == {
        "valid": True,
        "statements": 2,
        "errors": [],
        "notes": [],
    }
    result = lsp.check(sql="SELECT 1;\n  SELEC 2;\n")
    assert result["valid"] is False
    assert result["errors"][0]["start"] == {"line": 2, "column": 3}
    assert "SELECT" in result["errors"][0]["expected"]


@real
def test_the_real_outline_of_a_file_with_a_section(lsp):
    helpers.write_file(
        lsp.allowed,
        "sections/tables.sql",
        "CREATE TABLE t (id INT PRIMARY KEY, n VARCHAR(10));\n",
    )
    script = helpers.write_file(
        lsp.allowed, "dev.sql", "CREATE SCHEMA s;\nUSE s;\nSOURCE 'sections/tables.sql';\n"
    )

    result = lsp.outline(file_path=script)

    [schema] = [entry for entry in result["schemas"] if entry["name"] == "s"]
    [table] = schema["tables"]
    assert table["name"] == "t"
    assert [column["name"] for column in table["columns"]] == ["id", "n"]
    assert result["file"] == script
    # Defined in the section: its path, and positions in it.
    # As the binary resolves it: absolute and normalized, links not followed.
    assert table["source"] == os.path.normpath(
        os.path.join(lsp.allowed, "sections", "tables.sql")
    )
    assert table["range"]["start"] == {"line": 1, "column": 1}
    assert table["columns"][1]["selection_range"]["start"] == {"line": 1, "column": 37}


@real
@pytest.mark.skipif(not _has_source_dir(), reason="mariadb-lsp has no --source-dir yet")
def test_the_real_check_of_inline_sql_with_a_section(lsp):
    """The include is read from source_dir; one that is not there is an error."""
    helpers.write_file(lsp.allowed, "sections/a.sql", "SELECT 2;\n")

    assert lsp.check(
        sql="SELECT 1;\nSOURCE 'sections/a.sql';\n", source_dir=lsp.allowed
    )["valid"] is True
    result = lsp.check(
        sql="SELECT 1;\nSOURCE 'sections/gone.sql';\n", source_dir=lsp.allowed
    )
    assert result["valid"] is False
    [error] = result["errors"]
    assert error["start"] == {"line": 2, "column": 9}
    assert "cannot read" in error["message"] and "gone.sql" in error["message"]


@real
def test_the_real_format(lsp):
    result = lsp.format(sql="select a,b from t where a=1;", keyword_case="upper")

    assert result["sql"].startswith("SELECT")
    assert "FROM t" in result["sql"]


@real
def test_the_server_offers_the_sql_tools():
    assert {"sql.check", "sql.outline", "sql.format"} <= set(
        helpers.list_tool_names(["sql"])
    )


# --- what is left over ----------------------------------------------------------


def test_a_candidate_that_cannot_run_is_passed_over(tmp_path, monkeypatch, lsp_settings):
    """An executable the system cannot start - no interpreter - is not it."""
    broken = tmp_path / "mariadb-lsp"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(general, "get_bin_home", lambda: str(tmp_path / "none"))

    assert sql_functions.find_lsp() is None


def test_a_source_dir_that_is_no_folder_is_refused(fake):
    not_a_folder = helpers.write_file(fake.allowed, "a.sql", "SELECT 1;")

    with pytest.raises(Exception, match="is no folder"):
        fake.check(sql="SELECT 1;", source_dir=not_a_folder)


def test_a_relative_include_without_a_folder_is_left_to_the_binary(fake):
    """It has no file to check, and the binary says so itself."""
    fake.check(sql="SOURCE 'a.sql';")

    [call] = fake.calls()
    assert "--source-dir" not in call["argv"]


def test_a_binary_that_takes_too_long_is_stopped(tmp_path, monkeypatch, lsp_settings):
    slow = os.path.join(tmp_path, "mariadb-lsp")
    with open(slow, "w", encoding="utf-8") as script:
        script.write(
            "#!/bin/sh\n"
            'if [ "$1" = --version ]; then echo "mariadb-lsp 26.9.4"; exit 0; fi\n'
            "sleep 5\n"
        )
    os.chmod(slow, 0o755)
    config.set_lsp_path(slow)
    tools = _tools(monkeypatch)
    monkeypatch.setattr(sql_functions, "LSP_TIMEOUT", 0.2)

    with pytest.raises(Exception, match="took longer than"):
        tools.format(sql="SELECT 1")


def test_a_binary_that_went_away_is_reported(tmp_path, monkeypatch, lsp_settings):
    config.set_lsp_path(_fake_lsp(str(tmp_path), ""))
    tools = _tools(monkeypatch)
    os.remove(os.path.join(tmp_path, "mariadb-lsp"))

    with pytest.raises(Exception, match="mariadb-lsp could not be run"):
        tools.format(sql="SELECT 1")


def test_mcp_setup_sets_and_clears_the_binary(tmp_path, lsp_settings, capsys):
    from mcp_plugin.lib import setup_cli

    binary = _fake_lsp(str(tmp_path), "")
    setup_cli.apply({"lsp_path": binary})
    assert config.get_lsp_path() == binary
    assert binary in capsys.readouterr().out
    assert setup_cli.configuration()["lsp_path"] == binary

    setup_cli.apply({"lsp_path": ""})
    assert config.get_lsp_path() == ""
    assert "lspPath" not in config.get_settings()
    assert "on the PATH" in capsys.readouterr().out


def test_the_group_is_a_default_one_and_not_multi_tenant():
    assert general.FUNCTION_GROUP_SQL in general.DEFAULT_FUNCTION_GROUPS
    assert general.FUNCTION_GROUP_SQL not in general.MULTI_TENANT_FUNCTION_GROUPS
