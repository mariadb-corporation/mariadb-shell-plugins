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

"""MCP tools for SQL text: check its syntax, outline it, format it.

They run the MariaDB SQL language server's binary, ``mariadb-lsp``
(mariadb-corporation/lsp-server), on SQL an agent passes inline or names by
file: its ``check``, ``outline`` and ``format`` commands. Nothing is sent to
a database - these work on the text alone, with MariaDB's own grammar.

The group is offered only where the binary is found: the one
``mcp setup --lspPath`` names, else the first on the PATH, else
``~/.local/bin/mariadb-lsp`` - the places the VS Code extension looks.

The binary reads no file of its own: it is given the SQL on stdin and told
not to follow SOURCE directives (--no-source). MSM SOURCE directives are
expanded here instead, by msm_plugin's own code and with the reader
db.execute_sql_script uses, so every included file is resolved and checked
against the caller's allowed paths - as file_path is - before it is read,
and one parser decides what a directive is. The binary's positions, in the
expanded text, are mapped back to the script and the included files.
"""

import bisect
import json
import os
import shutil
import subprocess
from typing import Literal, Optional

from mcp_plugin.lib import config, db_functions, general
from mcp_plugin.lib.tool_registrar import tool_error, tool_registrar

LSP_BINARY_NAME = "mariadb-lsp"

# How long one run may take, and the largest script it is given.
LSP_TIMEOUT = 60
_VERSION_TIMEOUT = 10
MAX_SQL_BYTES = 10 * 1024 * 1024

KeywordCase = Literal["upper", "lower"]
Indent = Literal["tab", "2", "4"]


def _binary_name() -> str:
    return LSP_BINARY_NAME + (".exe" if os.name == "nt" else "")


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _candidates() -> list:
    """The places the binary is looked for, in order."""
    configured = config.get_lsp_path()
    if configured:
        if os.path.isdir(configured):
            return [os.path.join(configured, _binary_name())]
        return [configured]

    found = []
    on_path = shutil.which(_binary_name())
    if on_path:
        found.append(on_path)
    found.append(os.path.join(general.get_bin_home(), _binary_name()))
    return found


def find_lsp() -> Optional[str]:
    """Returns the mariadb-lsp binary the sql tools run, or None.

    The first candidate that is executable and answers ``--version`` with
    ``mariadb-lsp <version>`` - anything else of that name is not this
    program.

    Returns:
        The binary's path, or None where there is none.
    """
    for candidate in _candidates():
        if not _is_executable(candidate):
            continue
        try:
            answer = subprocess.run(
                [candidate, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if answer.returncode == 0 and answer.stdout.startswith(LSP_BINARY_NAME + " "):
            return candidate

    return None


def _line_starts(text: str) -> list:
    """Where each line of a text starts; lines end at "\\n" only."""
    return [0] + [index + 1 for index, character in enumerate(text) if character == "\n"]


def _characters(text: str, units: int) -> int:
    """How many characters of the text the first UTF-16 units cover."""
    if text.isascii():
        # One unit per character: most SQL, and no walk along the line.
        return min(units, len(text))
    counted = 0
    for characters, character in enumerate(text):
        if counted >= units:
            return characters
        counted += 2 if ord(character) > 0xFFFF else 1
    return len(text)


def _position_converter(sent: str, script: str, expansion):
    """Returns what turns the binary's positions into the caller's.

    The binary's json counts lines from 0 and columns in UTF-16 units, as LSP
    does, in the text it was given - the script with its SOURCE directives
    expanded. The caller gets lines and columns from 1, in characters, in
    the script as written; or, for a place in an included part, in that
    file, which ``file`` then names.

    Args:
        sent (str): The text the binary was given.
        script (str): The script as written.
        expansion: Its segments, or None for a script without directives.
    """
    from msm_plugin.lib import source

    sent_starts = _line_starts(sent)
    script_starts = _line_starts(script)

    def convert(position: dict) -> dict:
        line = min(max(position.get("line", 0), 0), len(sent_starts) - 1)
        line_start = sent_starts[line]
        line_end = sent.find("\n", line_start)
        text = sent[line_start : len(sent) if line_end < 0 else line_end]
        offset = line_start + _characters(text, position.get("character", 0))
        if expansion is None:
            return {"line": line + 1, "column": offset - line_start + 1}

        origin, file, file_line, column = source.locate(sent, expansion, offset)
        if file is not None:
            return {"file": file, "line": file_line, "column": column}
        script_line = bisect.bisect_right(script_starts, origin) - 1
        return {
            "line": script_line + 1,
            "column": origin - script_starts[script_line] + 1,
        }

    return convert


def _with_positions(value, convert):
    """Returns the binary's json with every position converted."""
    if isinstance(value, dict):
        if len(value) == 2 and "line" in value and "character" in value:
            return convert(value)
        return {key: _with_positions(member, convert) for key, member in value.items()}
    if isinstance(value, list):
        return [_with_positions(member, convert) for member in value]
    return value


def register_sql_tools(server, function_groups=()) -> None:
    """Registers the SQL text tools on the given server, if mariadb-lsp is there.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    lsp = find_lsp()
    if lsp is None:
        general.log_event(
            "sql tools not registered: no mariadb-lsp was found ("
            + ", ".join(_candidates())
            + "); set its path with 'mariadb-shell -- mcp setup --lspPath=...'"
        )
        return

    from mcp.server.mcpserver import Context

    tool = tool_registrar(server)

    def _prepare(ctx, sql, file_path, source_dir, sql_mode, delimiter,
                 version_id, sources=True) -> tuple:
        """Returns the script, the text to give the binary, and the options.

        The binary reads no file of its own (--no-source): SOURCE directives
        are expanded here, by msm_plugin's own code, each include resolved
        and checked against the caller's allowed paths before it is read -
        the reader db.execute_sql_script uses. read_sql_input has made the
        line breaks "\\n", so a directive after a lone "\\r" is found too.
        """
        user = general.get_client_identity(ctx).user
        sql, source_dir, where = db_functions.read_sql_input(
            user, sql, file_path, source_dir, "sql", "the SQL"
        )
        if len(sql.encode("utf-8")) > MAX_SQL_BYTES:
            raise tool_error(
                f"The SQL is larger than {MAX_SQL_BYTES // (1024 * 1024)} MB."
            )
        expansion = (
            db_functions.expand_sources(
                sql, source_dir, where, user, "Nothing was checked."
            )
            if sources
            else None
        )
        options = ["--no-source"] if sources else []
        if sql_mode is not None:
            options += ["--sql-mode", sql_mode]
        if delimiter is not None:
            options += ["--delimiter", delimiter]
        if version_id is not None:
            options += ["--version-id", str(version_id)]
        sent, segments = expansion if expansion else (sql, None)
        return sql, sent, segments, options

    def _run(command: list, sql: str, accept=(0,)) -> str:
        """Runs mariadb-lsp on the SQL, given on stdin; returns stdout."""
        try:
            done = subprocess.run(
                [lsp, *command, "-"],
                input=sql,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=LSP_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise tool_error(
                f"mariadb-lsp {command[0]} took longer than {LSP_TIMEOUT}s."
            ) from None
        except OSError as error:
            raise tool_error(f"mariadb-lsp could not be run: {error}") from None
        if done.returncode not in accept:
            raise tool_error(
                f"mariadb-lsp {command[0]} failed: "
                + (done.stderr.strip() or f"exit code {done.returncode}")
            )
        return done.stdout

    @tool(name="sql.check")
    def check(
        ctx: Context,
        sql: Optional[str] = None,
        file_path: Optional[str] = None,
        source_dir: Optional[str] = None,
        sql_mode: Optional[str] = None,
        delimiter: Optional[str] = None,
        version_id: Optional[int] = None,
        max_errors: Optional[int] = None,
    ) -> dict:
        """Checks the syntax of SQL with MariaDB's grammar, without a server.

        Use it on SQL before running or saving it. Nothing is executed and no
        connection is needed.

        Give the SQL inline (sql) or as a file (file_path); exactly one. A
        file_path must be within the allowed directories. MSM SOURCE
        directives (SOURCE '<path>'[<start>:<end>];) are followed, as
        MariaDB Schema Management expands them: a relative path is relative
        to the file's folder, or to source_dir for inline SQL. Every included
        file must be within the allowed directories too.

        Args:
            sql: The SQL to check: one or more statements.
            file_path: A .sql file to check instead.
            source_dir: For inline sql, the folder relative SOURCE paths are
                taken from - the folder the SQL belongs in. Must be within
                the allowed directories.
            sql_mode: The sql_mode at the start, comma-separated (default the
                server's default); a SET sql_mode in the SQL is followed.
            delimiter: The delimiter at the start (default ;).
            version_id: The server release versioned comments are judged
                against, as major*10000 + minor*100 + patch (130100 = 13.1.0).
            max_errors: List at most this many errors (default 100).

        Returns:
            valid (true when there are no errors), statements (how many), and
            errors: one per error, with start (line and column, from 1),
            message, token (where it failed) and expected (what the grammar
            would have taken there). notes holds remarks that are not
            errors. The statements of SOURCE includes are checked too: an
            error in one has file, the included file, in its start, and its
            line and column are in that file. An include that cannot be read
            or may not be fails the call, naming its directive's line.
        """
        script, sent, segments, options = _prepare(
            ctx, sql, file_path, source_dir, sql_mode, delimiter, version_id
        )
        command = ["check", "--format", "json", *options]
        if max_errors is not None:
            command += ["--max-errors", str(max_errors)]

        # Exit 1 is the answer "there are errors", not a failure.
        report = json.loads(_run(command, sent, accept=(0, 1)))
        [checked] = report["files"]
        convert = _position_converter(sent, script, segments)
        errors = [
            {key: value for key, value in error.items() if key not in ("offset", "length")}
            for error in checked.get("errors", [])
        ]
        return {
            "valid": not errors,
            "statements": checked.get("statements", 0),
            "errors": _with_positions(errors, convert),
            "notes": _with_positions(checked.get("notes", []), convert),
        }

    @tool(name="sql.outline")
    def outline(
        ctx: Context,
        sql: Optional[str] = None,
        file_path: Optional[str] = None,
        source_dir: Optional[str] = None,
        sql_mode: Optional[str] = None,
        delimiter: Optional[str] = None,
        version_id: Optional[int] = None,
    ) -> dict:
        """Lists the schema objects SQL defines, without a server.

        Folds the CREATE, ALTER, DROP, RENAME and USE statements of the SQL
        in order, as a server would apply them, and reports what results:
        schemas with their tables (columns, indexes, foreign keys, checks),
        views, routines, triggers, events and sequences, and the MariaDB
        REST Service objects. Use it to understand a schema script - a
        project's development script, say - without running it.

        The SQL is given as for sql.check, SOURCE directives and
        source_dir included.

        Args:
            sql: The SQL to outline.
            file_path: A .sql file to outline instead.
            source_dir: For inline sql, the folder relative SOURCE paths are
                taken from. Must be within the allowed directories.
            sql_mode: The sql_mode at the start, comma-separated.
            delimiter: The delimiter at the start (default ;).
            version_id: The server release versioned comments are judged
                against (major*10000 + minor*100 + patch).

        Returns:
            The outline (format 1 of mariadb-lsp's outline, see the
            lsp-server repository's docs/outline-format.md): schemas, rest,
            statements (ddl and applied counts) and notes (statements that
            were not read, and why), and includes: the file each SOURCE
            directive included and the directive's line. Every range is a
            start and an end, each a line and a column counted from 1; names
            are as written, without quotes. A position in an included file
            has file, that file, and its line and column are in it.
        """
        script, sent, segments, options = _prepare(
            ctx, sql, file_path, source_dir, sql_mode, delimiter, version_id
        )
        command = ["outline", *options]

        result = json.loads(_run(command, sent))
        result["file"] = file_path
        result = _with_positions(result, _position_converter(sent, script, segments))
        if segments:
            result["includes"] = [
                {"file": segment.file, "line": script.count("\n", 0, segment.origin) + 1}
                for segment in segments
                if segment.file is not None
            ]
        return result

    @tool(name="sql.format")
    def format_sql(
        ctx: Context,
        sql: Optional[str] = None,
        file_path: Optional[str] = None,
        line_width: Optional[int] = None,
        keyword_case: Optional[KeywordCase] = None,
        indent: Optional[Indent] = None,
        sql_mode: Optional[str] = None,
        delimiter: Optional[str] = None,
        version_id: Optional[int] = None,
    ) -> dict:
        """Lays SQL out readably, with MariaDB's grammar, without a server.

        SELECT, WITH, INSERT ... SELECT, UPDATE, DELETE, CREATE VIEW and
        CREATE and ALTER TABLE statements that parse are laid out - clauses
        on lines of their own, keywords in one case, lines kept within a
        width; any other statement, and one that does not parse, is left as
        it is. Comments are kept. Use it on SQL before showing it to the
        user or saving it.

        Args:
            sql: The SQL to format.
            file_path: A .sql file to format instead; it is not changed, the
                formatted text is returned.
            line_width: The width lines are kept within (default 80, 0 for
                no limit).
            keyword_case: upper (the default) or lower.
            indent: One level of indentation: tab, 2 (the default) or 4
                spaces.
            sql_mode: The sql_mode at the start, comma-separated.
            delimiter: The delimiter at the start (default ;).
            version_id: The server release versioned comments are judged
                against (major*10000 + minor*100 + patch).

        Returns:
            sql: the formatted SQL.
        """
        _, sent, _, options = _prepare(
            ctx, sql, file_path, None, sql_mode, delimiter, version_id,
            sources=False,
        )
        command = ["format", *options]
        if line_width is not None:
            command += ["--line-width", str(line_width)]
        if keyword_case is not None:
            command += ["--keyword-case", keyword_case]
        if indent is not None:
            command += ["--indent", str(indent)]

        return {"sql": _run(command, sent)}
