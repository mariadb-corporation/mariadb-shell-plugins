# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The MariaDB SQL language server's binary, mariadb-lsp, as msm runs it.

msm reads the schema objects of a project's development script through the
binary's ``outline`` command: that is what the schema snapshot of the
diagrams (MSM Section 160) is made from. No shell import at module scope, so
the module can be tested without a shell.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

LSP_BINARY_NAME = "mariadb-lsp"

# How long the binary may take: --version, and an outline or split of a large script.
_VERSION_TIMEOUT = 10
_RUN_TIMEOUT = 120
# The edit request's format (lsp-server docs/edit-format.md).
EDIT_FORMAT = 1

# A binary's answers to --version, --help and `edit --ops`, by its path,
# modification time and the arguments: asked once per build, so an updated
# binary is asked again.
_answers = {}

# The edit ops of a binary whose --help has the edit command but no
# `edit ops:` line (lsp-server #25): the renames.
RENAME_OPS = frozenset(("rename_table", "rename_column", "rename_view", "rename_routine"))


class LspError(ValueError):
    """The binary is missing, or did not answer as it should.

    Attributes:
        kind: The kind of error the binary gave for refused edits
            (edit-format.md: bad-request, unknown-object, name-taken, ...);
            None for any other failure.
    """

    def __init__(self, message: str, kind: Optional[str] = None):
        super().__init__(message)
        self.kind = kind


def _binary_name() -> str:
    return LSP_BINARY_NAME + (".exe" if os.name == "nt" else "")


def _shell_folder() -> Optional[str]:
    """The folder of the shell msm runs in, where its package installs the binary."""
    try:
        import mysqlsh
    except ImportError:
        return None
    own = getattr(mysqlsh, "executable", None)
    return os.path.dirname(own) if own else None


def _candidates(lsp_path: Optional[str]) -> list:
    """The places the binary is looked for, in order."""
    if lsp_path:
        if os.path.isdir(lsp_path):
            return [os.path.join(lsp_path, _binary_name())]
        return [lsp_path]

    found = []
    shell_folder = _shell_folder()
    if shell_folder:
        found.append(os.path.join(shell_folder, _binary_name()))
    on_path = shutil.which(_binary_name())
    if on_path:
        found.append(on_path)
    found.append(os.path.join(os.path.expanduser("~"), ".local", "bin", _binary_name()))
    return found


def find_lsp(lsp_path: Optional[str] = None) -> Optional[str]:
    """Returns the mariadb-lsp binary, or None where there is none.

    Looked for as mcp_plugin's sql tools and the VS Code extension do: the
    given path (the binary or its folder), else next to the shell, on PATH,
    then in ~/.local/bin. A candidate counts only if ``--version`` answers
    ``mariadb-lsp <version>``.

    Args:
        lsp_path: The binary or its folder, to take instead of searching.

    Returns:
        The binary's path, or None.
    """
    for candidate in _candidates(lsp_path):
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            continue
        code, stdout, _ = _answer(candidate, ("--version",))
        if code == 0 and stdout.startswith(LSP_BINARY_NAME + " "):
            return candidate
    return None


def require_lsp(lsp_path: Optional[str] = None) -> str:
    """Returns the mariadb-lsp binary, or raises where there is none."""
    binary = find_lsp(lsp_path)
    if binary is None:
        where = f" at `{lsp_path}`" if lsp_path else ""
        raise LspError(
            f"The MariaDB SQL language server's binary `{LSP_BINARY_NAME}` could not "
            f"be found{where}. It is installed with the MariaDB Shell and needed to "
            "read the schema objects of the development script."
        )
    return binary


def _run(binary: str, arguments: list, sql: str) -> subprocess.CompletedProcess:
    """Runs a subcommand of the binary on SQL text from stdin; stdout and
    stderr are bytes.

    Raises:
        LspError: Where the binary could not be run or did not finish.
    """
    name = f"{LSP_BINARY_NAME} {arguments[0]}"
    # Bytes, not text mode: text mode writes os.linesep for every line break,
    # and the offsets `split` answers count the bytes the binary read.
    try:
        return subprocess.run(
            [binary, *arguments, "-"],
            input=sql.encode("utf-8"),
            capture_output=True,
            timeout=_RUN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise LspError(f"`{name}` did not finish in {_RUN_TIMEOUT} s.")
    except OSError as e:
        raise LspError(f"`{LSP_BINARY_NAME}` could not be run: {e}")


def _stderr(answer: subprocess.CompletedProcess) -> str:
    return answer.stderr.decode("utf-8", "replace").strip()


def _run_json(binary: str, arguments: list, sql: str):
    """Runs a subcommand of the binary on SQL text from stdin; returns its JSON."""
    name = f"{LSP_BINARY_NAME} {arguments[0]}"
    answer = _run(binary, arguments, sql)
    if answer.returncode != 0:
        raise LspError(f"`{name}` failed (exit {answer.returncode}): {_stderr(answer)}")
    try:
        return json.loads(answer.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LspError(f"`{name}` wrote no JSON: {e}")


def outline(binary: str, sql: str, source_dir: Optional[str] = None) -> dict:
    """Outlines SQL text with the binary.

    The binary expands the SOURCE directives itself, relative to source_dir,
    so the positions of an included object are in its own file.

    Args:
        binary: The binary, from find_lsp().
        sql: The SQL text.
        source_dir: The folder relative SOURCE paths are taken from; None
            for SQL without SOURCE directives (an empty folder is given).

    Returns:
        The outline, as the binary writes it (docs/outline-format.md of the
        lsp-server).
    """
    if source_dir is None:
        with tempfile.TemporaryDirectory() as empty:
            return _run_json(binary, ["outline", "--source-dir", empty], sql)
    return _run_json(binary, ["outline", "--source-dir", source_dir], sql)


def split(binary: str, sql: str) -> list:
    """Lists the statements of SQL text with the binary's splitter.

    The splitter is the one MariaDB Shell runs scripts with: it follows
    DELIMITER and SET sql_mode. SOURCE directives are not expanded.

    Args:
        binary: The binary, from find_lsp().
        sql: The SQL text.

    Returns:
        The statements, as `mariadb-lsp split --format json` writes them:
        kind ("sql" or "comment"), begin, body_end and end (UTF-8 byte
        offsets; end is after the delimiter), delimiter, sql_mode.
    """
    result = _run_json(binary, ["split", "--format", "json"], sql)
    if result.get("open_at_end"):
        raise LspError(
            f"The script ends inside an open {result['open_at_end']}; it cannot be split "
            "into statements."
        )
    return result.get("statements", [])


def _answer_key(binary: str, question: tuple) -> tuple:
    """What an answer of the binary is kept under: its build (path and
    modification time) and the question."""
    try:
        return (binary, os.stat(binary).st_mtime_ns, question)
    except OSError:
        return (binary, None, question)


def _answer(binary: str, arguments: tuple) -> tuple:
    """The binary's (exit code, stdout, stderr) for arguments without
    input, asked once per build; (None, "", "") where it could not be run
    (asked again next time)."""
    key = _answer_key(binary, arguments)
    if key not in _answers:
        try:
            answer = subprocess.run(
                [binary, *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            # Not kept: a run that timed out (a first, slow start) is tried again
            return None, "", ""
        _answers[key] = (answer.returncode, answer.stdout, answer.stderr)
    return _answers[key]


def _help(binary: str) -> str:
    """The binary's --help."""
    _, stdout, stderr = _answer(binary, ("--help",))
    return stdout + stderr


def _ops_answer(binary: str) -> Optional[dict]:
    """What `edit --ops` answers, with a valid ops list; None for a binary
    that does not answer it (exit 2 from #25 to #28, an unknown command
    before)."""
    code, stdout, _ = _answer(binary, ("edit", "--ops"))
    if code != 0:
        return None
    try:
        answer = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    ops = answer.get("ops") if isinstance(answer, dict) else None
    if not isinstance(ops, list) or not all(isinstance(op, str) for op in ops):
        return None
    return answer


def _listed_ops(binary: str) -> Optional[frozenset]:
    """The ops `edit --ops` lists; None for a binary that does not answer it."""
    answer = _ops_answer(binary)
    return frozenset(answer["ops"]) if answer is not None else None


def supports_edit(binary: str) -> bool:
    """Whether the binary has the ``edit`` command, as its --help lists it.

    A binary from before the command answers ``edit`` with an unknown
    command, which would read as an edit that failed.
    """
    return re.search(r"^\s+edit\s", _help(binary), re.MULTILINE) is not None


def tags_migration(binary: str) -> bool:
    """Whether the binary tags each migration statement with what it does
    (``action``, ``object``, ``name``, ``table``, ``new_name``): one that
    answers ``edit --ops``."""
    return _listed_ops(binary) is not None


def merges_alters(binary: str) -> bool:
    """Whether the binary merges a table's ALTER TABLE statements into one
    where the request asks for it (``"merge_alters": true``; lsp-server
    #33): its ``edit --ops`` says ``"merge_alters": true``. An older binary
    ignores the member, so it is sent only where this holds."""
    answer = _ops_answer(binary)
    return answer is not None and answer.get("merge_alters") is True


def outlines_view_reads(binary: str) -> bool:
    """Whether the binary's outline tells what each view reads (its
    ``views[].reads``; lsp-server #36): asked once per build, by outlining a
    view and looking for the member, which an older binary leaves out."""
    key = _answer_key(binary, ("outline-view-reads",))
    if key not in _answers:
        try:
            outlined = outline(binary, "CREATE VIEW v AS SELECT 1;\n")
        except (ValueError, OSError):
            return False  # not kept: tried again next time
        views = [view for schema in outlined.get("schemas", []) for view in schema.get("views", [])]
        _answers[key] = bool(views) and all("reads" in view for view in views)
    return _answers[key]


def edits_take_outline_objects(binary: str) -> bool:
    """Whether the binary's edit ops take objects as its outline writes
    them (lsp-server #48): a table's indexes with both `columns` and
    `key_parts`, a view's `definition` with its CHECK OPTION, and a
    replace_* of an unchanged object answered with an `unchanged` note.
    Asked once per build, by sending an outlined table with an index back
    through add_table, which an older binary refuses."""
    key = _answer_key(binary, ("edit-outline-objects",))
    if key not in _answers:
        try:
            outlined = outline(binary, "CREATE TABLE t (id INT, KEY k (id));\n")
            table = next(t for schema in outlined.get("schemas", []) for t in schema.get("tables", []))
            table = {**table, "name": "u"}
            with tempfile.TemporaryDirectory() as folder:
                edit(binary, "CREATE TABLE t (id INT);\n", folder, {
                    "format": EDIT_FORMAT, "schema": None,
                    "edits": [{"op": "add_table", "table": table}],
                })
        except LspError:
            _answers[key] = False
        except (ValueError, OSError, StopIteration):
            return False  # not kept: tried again next time
        else:
            _answers[key] = True
    return _answers[key]


def outline_features(binary: str) -> frozenset:
    """What `outline --features` lists (lsp-server #39); none for an older
    binary, which exits 2 on the option.

    Raises:
        LspError: Where the binary could not be run (a first start that
            timed out is tried again next time).
    """
    code, stdout, _ = _answer(binary, ("outline", "--features"))
    if code is None:
        raise LspError(f"`{LSP_BINARY_NAME} outline --features` could not be run; try again.")
    if code != 0:
        return frozenset()
    try:
        features = json.loads(stdout).get("features")
    except (json.JSONDecodeError, AttributeError):
        return frozenset()
    if not isinstance(features, list):
        return frozenset()
    return frozenset(feature for feature in features if isinstance(feature, str))


# What msm needs from the outline's REST part to work out the REST
# endpoints a release removed: the auth app links of each service, the
# service and schema a statement that names none of them works on, and
# which services and schemas the file creates rather than only names, and
# the DROP REST SCHEMA ... FOR SCHEMA that applies only on that database.
REST_OUTLINE_FEATURES = frozenset(
    ("rest_auth_app_links", "rest_current_schema", "rest_declared", "rest_drop_for_database")
)


def outlines_rest(binary: str) -> bool:
    """Whether the binary's outline tells the REST definition as msm needs
    it (REST_OUTLINE_FEATURES)."""
    return REST_OUTLINE_FEATURES <= outline_features(binary)


def edit_ops(binary: str) -> frozenset:
    """The edit ops the binary does: what ``edit --ops`` lists; for an
    older binary its --help's ``edit ops:`` line, the renames for one with
    ``edit`` but without that line, none without ``edit``."""
    listed = _listed_ops(binary)
    if listed is not None:
        return listed
    if not supports_edit(binary):
        return frozenset()
    listed = re.search(r"^\s+edit ops:(.*)$", _help(binary), re.MULTILINE)
    return frozenset(listed.group(1).split()) if listed else RENAME_OPS


def edit(binary: str, sql: str, source_dir: str, request: dict) -> dict:
    """Has the binary work out schema edits on SQL text and the files it
    includes, without writing anything.

    Args:
        binary: The binary, from find_lsp(); one that supports_edit().
        sql: The SQL text, as for outline().
        source_dir: The folder relative SOURCE paths are taken from.
        request: {format, schema, edits} (lsp-server docs/edit-format.md).

    Returns:
        {format, files: [{file, edits: [{range, new_text}]}], migration:
        [{edit, sql, action?, object?, name?, table?, new_name?}], notes,
        outline}: the tags where tags_migration(binary): ``file`` is "-" for sql, else a path
        relative to source_dir.

    Raises:
        LspError: Where the binary refused the edits (naming the edit and
            why) or did not answer as it should.
    """
    with tempfile.TemporaryDirectory() as folder:
        edits_file = os.path.join(folder, "edits.json")
        with open(edits_file, "w", encoding="utf-8") as f:
            json.dump(request, f)
        answer = _run(binary, ["edit", "--source-dir", source_dir, "--edits", edits_file], sql)
    try:
        result = json.loads(answer.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = None
    if answer.returncode != 0 or not isinstance(result, dict) or "error" in result:
        error = result.get("error") if isinstance(result, dict) else None
        if isinstance(error, dict):
            which = error.get("edit")
            where = f"Edit {which + 1}" if isinstance(which, int) else "The edits"
            raise LspError(
                f"{where} could not be made ({error.get('kind', 'error')}): "
                f"{error.get('message', '')}",
                error.get("kind"),
            )
        if isinstance(error, str) and error:
            raise LspError(f"The edits could not be made: {error}")
        raise LspError(
            f"`{LSP_BINARY_NAME} edit` failed (exit {answer.returncode}): {_stderr(answer)}"
        )
    return result
