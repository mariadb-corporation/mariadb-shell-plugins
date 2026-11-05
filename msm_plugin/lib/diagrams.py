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

"""The schema diagrams of an MSM project: the rows of its diagrams VIEW.

MSM Section 160 creates the VIEW `msm_schema_diagrams` (`name`, `format`,
`document`). Each row is a JSON document:

- one row in format ``msm-schema/1``, named ``schema_definition``: the
  snapshot of the schema objects the development script's CREATE statements
  define, as the MariaDB SQL language server outlines them. The SQL is the
  source of truth; the snapshot is regenerated from it and never edited.
- one row per EER diagram in format ``msm-diagram/1``: its layers, figures
  and connections, which name tables and foreign keys of the snapshot.

This module holds the format alone, with no file and no shell access: the
VIEW's SQL written and read back, the documents' layout, and the snapshot
made from an outline.
"""

import hashlib
import json
import re
from typing import Optional

DIAGRAMS_VIEW = "msm_schema_diagrams"
SCHEMA_FORMAT = "msm-schema/1"
DIAGRAM_FORMAT = "msm-diagram/1"
SCHEMA_ROW_NAME = "schema_definition"

# The outline format the snapshot is made from (lsp-server docs/outline-format.md).
OUTLINE_FORMAT = 1

# The members of an outline schema a snapshot keeps.
SCHEMA_OBJECT_KINDS = ("tables", "views", "routines", "triggers", "events", "sequences")

# A JSON value goes on one line of its own when it is at most this long.
_ONE_LINE_WIDTH = 160


# -----------------------------------------------------------------------------
# The documents' layout


def dump_document(document) -> str:
    """Writes a document as JSON laid out for reading and for diffs.

    A value that fits on a line stays on one; otherwise its members go on
    lines of their own, so a table, column, figure or connection is one line
    where it is short enough and a change touches the lines it changes.

    Args:
        document: The JSON value.

    Returns:
        The JSON text, without a trailing line break.
    """

    def dump(value, indent: str) -> str:
        # Children first, so every value is written once: a container fits on
        # a line exactly when its children each do and the whole is short.
        if not isinstance(value, (dict, list)) or not value:
            return json.dumps(value, ensure_ascii=False)
        inner = indent + "  "
        if isinstance(value, dict):
            parts = [
                f"{json.dumps(key, ensure_ascii=False)}: {dump(item, inner)}"
                for key, item in value.items()
            ]
            opening, closing = "{", "}"
        else:
            parts = [dump(item, inner) for item in value]
            opening, closing = "[", "]"
        one_line = opening + ", ".join(parts) + closing
        if "\n" not in one_line and len(indent) + len(one_line) <= _ONE_LINE_WIDTH:
            return one_line
        return f"{opening}\n{inner}" + f",\n{inner}".join(parts) + f"\n{indent}{closing}"

    return dump(document, "")


# -----------------------------------------------------------------------------
# The VIEW's SQL


def sql_string_literal(text: str) -> str:
    """Quotes a text as a MariaDB string literal.

    Backslashes are escaped, so the literal reads back the same in every
    sql_mode but NO_BACKSLASH_ESCAPES, which the MSM scripts do not set.
    """
    escaped = (
        text.replace("\\", "\\\\").replace("'", "\\'").replace("\0", "\\0")
    )
    return f"'{escaped}'"


def render_view_sql(rows: list) -> str:
    """Writes the CREATE statement of the diagrams VIEW.

    Args:
        rows: The rows, each {name, format, document}; document is a JSON
            value or the JSON text.

    Returns:
        The statement, ending with ";" and no line break.
    """
    head = (
        "CREATE OR REPLACE SQL SECURITY INVOKER\n"
        f"VIEW `{DIAGRAMS_VIEW}` (`name`, `format`, `document`) AS\n"
    )
    if not rows:
        return head + "SELECT NULL, NULL, NULL FROM DUAL WHERE FALSE;"

    selects = []
    for row in rows:
        document = row["document"]
        if not isinstance(document, str):
            document = dump_document(document)
        selects.append(
            f"SELECT {sql_string_literal(row['name'])}, "
            f"{sql_string_literal(row['format'])},\n{sql_string_literal(document)}"
        )
    return head + "\nUNION ALL\n".join(selects) + ";"


_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}

# What a statement is scanned as: comments and quoted identifiers or strings
# are skipped, single-quoted strings are the literals (group "literal").
_TOKENS = re.compile(
    r"--(?=\s|$)[^\n]*|#[^\n]*|/\*.*?(?:\*/|\Z)"
    r"|`(?:[^`]|``)*`|\"(?:[^\"\\]|\\.|\"\")*\""
    r"|'(?P<literal>(?:[^'\\]|\\.|'')*)'"
    r"|'",
    re.DOTALL,
)
_ESCAPE = re.compile(r"\\(.)|''", re.DOTALL)


def _unescape(match) -> str:
    following = match.group(1)
    if following is None:
        return "'"
    # \% and \_ keep their backslash, as the server reads them.
    if following in ("%", "_"):
        return "\\" + following
    return _ESCAPES.get(following, following)


def _string_literals(sql: str) -> list:
    """The string literals of a statement, in order, their contents unescaped.

    Comments, backtick-quoted identifiers and double-quoted strings are
    skipped; only single-quoted literals are returned.
    """
    literals = []
    for match in _TOKENS.finditer(sql):
        if match.group(0) == "'":
            raise ValueError("The diagrams VIEW holds a string that is not closed.")
        literal = match.group("literal")
        if literal is not None:
            literals.append(_ESCAPE.sub(_unescape, literal))
    return literals


def parse_view_sql(sql: str) -> list:
    """Reads the rows back from the diagrams VIEW's CREATE statement.

    The statement is read as render_view_sql() writes it: every row a SELECT
    of three string literals, name, format and document.

    Args:
        sql: The SQL holding the statement; comments around it are fine.

    Returns:
        The rows, each {name, format, document} with the document parsed.
        An SQL text without the statement has none.
    """
    if DIAGRAMS_VIEW not in sql:
        return []
    literals = _string_literals(sql)
    if len(literals) % 3 != 0:
        raise ValueError(
            f"The `{DIAGRAMS_VIEW}` VIEW does not select a name, a format and a "
            "document as string literals in every row."
        )
    rows = []
    for index in range(0, len(literals), 3):
        name, row_format, text = literals[index : index + 3]
        try:
            document = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"The document of the diagram row `{name}` is not valid JSON: {e}"
            )
        rows.append({"name": name, "format": row_format, "document": document})
    return rows


# -----------------------------------------------------------------------------
# The schema snapshot


def source_hash(text: str) -> str:
    """The hash a snapshot records of the SQL it was made from."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _covering_unique_key(table: dict, columns: list) -> bool:
    """Whether a primary or unique key of the table has exactly these columns."""
    wanted = sorted(column.lower() for column in columns)
    for index in table.get("indexes", []):
        if index.get("kind") in ("primary", "unique") and wanted == sorted(
            column.lower() for column in index.get("columns", [])
        ):
            return True
    return False


def _add_relationship_members(table: dict) -> None:
    """Adds what a diagram draws a foreign key with, derived from the SQL.

    The members mean what MySQL Workbench's do, so a diagram draws them as
    Workbench did. many: whether more than one row may refer to the same
    parent row, so no primary or unique key covers exactly the foreign key's
    columns. referenced_mandatory: whether every row has to refer to a parent
    row, so none of the columns is nullable. mandatory: whether a parent row
    needs a child row, which SQL cannot state; True, Workbench's default.
    """
    nullable = {
        column["name"].lower(): column.get("nullable", True)
        for column in table.get("columns", [])
    }
    for foreign_key in table.get("foreign_keys", []):
        columns = foreign_key.get("columns", [])
        foreign_key["many"] = not _covering_unique_key(table, columns)
        foreign_key["mandatory"] = True
        foreign_key["referenced_mandatory"] = bool(columns) and not any(
            nullable.get(column.lower(), True) for column in columns
        )


def _same_name(a: Optional[str], b: Optional[str]) -> bool:
    return a is not None and b is not None and a.lower() == b.lower()


def schema_document_from_outline(
    outline: dict,
    schema_name: str,
    files: list,
    hash_value: str,
) -> dict:
    """Makes the msm-schema/1 snapshot from an outline of the development script.

    The objects of the outline's schema of that name are kept as the outline
    writes them, together with the objects of the part of the script that
    names no schema (before the first USE); the caller leaves the diagrams
    VIEW out of the outlined script. Foreign keys get the members a
    diagram draws them with (see _add_relationship_members), and a foreign key
    that leaves its schema out names this one.

    Args:
        outline: The outline, as the binary writes it.
        schema_name: The project's schema, its substitutions at their defaults.
        files: The files the positions are in: the development script first,
            then each of the outline's includes in order, each a path
            relative to the project's development folder. An object's
            ``source`` n is files[n + 1].
        hash_value: The source_hash() of the SQL the snapshot stands for.

    Returns:
        The document.
    """
    if outline.get("format") != OUTLINE_FORMAT:
        raise ValueError(
            f"The language server writes outline format {outline.get('format')}, "
            f"msm reads format {OUTLINE_FORMAT}."
        )

    document = {
        "format": SCHEMA_FORMAT,
        "schema": schema_name,
        "outline_format": OUTLINE_FORMAT,
        "source_hash": hash_value,
        "files": files,
        "options": {},
    }
    for kind in SCHEMA_OBJECT_KINDS:
        document[kind] = []

    for schema in outline.get("schemas", []):
        name = schema.get("name")
        if name is not None:
            if not _same_name(name, schema_name):
                continue
            document["options"] = schema.get("options", {})
            for key in ("range", "selection_range"):
                if key in schema:
                    document[key] = schema[key]
        for kind in SCHEMA_OBJECT_KINDS:
            document[kind].extend(schema.get(kind, []))

    for table in document["tables"]:
        _add_relationship_members(table)

    document["rest"] = outline.get("rest", {})
    document["notes"] = outline.get("notes", [])
    return document


def unread_statements(outline: dict) -> list:
    """The outline's notes on statements it could not read.

    A statement with a syntax error is left out of the outline, so a snapshot
    made from it would silently miss the objects it defines.
    """
    return [
        note for note in outline.get("notes", []) if "not read" in note.get("message", "")
    ]


# -----------------------------------------------------------------------------
# Diagrams


def validate_diagram(document) -> None:
    """Checks that a document is an msm-diagram/1 diagram.

    Raises:
        ValueError: Naming what is wrong.
    """
    if not isinstance(document, dict):
        raise ValueError("A diagram has to be a JSON object.")
    if document.get("format") != DIAGRAM_FORMAT:
        raise ValueError(f"A diagram has the format `{DIAGRAM_FORMAT}`.")
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A diagram needs a name.")
    if name == SCHEMA_ROW_NAME:
        raise ValueError(f"`{SCHEMA_ROW_NAME}` is the name of the schema row.")
    for member in ("layers", "figures", "connections"):
        if not isinstance(document.get(member), list):
            raise ValueError(f"A diagram's `{member}` has to be a list.")


def reconcile_diagram(diagram: dict, schema_document: dict) -> bool:
    """Drops the figures and connections of objects the schema no longer has.

    A figure shows a table; a connection a foreign key of the table of its
    start figure. New tables are not added: placing them is up to the user
    or the layout.

    Args:
        diagram: The msm-diagram/1 document, changed in place.
        schema_document: The msm-schema/1 snapshot.

    Returns:
        Whether anything was dropped.
    """
    tables = {table["name"].lower(): table for table in schema_document.get("tables", [])}
    figures, connections = diagram["figures"], diagram["connections"]

    def table_of(figure) -> str:
        return (figure.get("table") or figure.get("name") or "").lower()

    kept_figures = [
        figure
        for figure in figures
        if figure.get("type", "TableFigure") != "TableFigure" or table_of(figure) in tables
    ]
    figure_ids = {figure.get("id") for figure in kept_figures}

    def has_foreign_key(connection) -> bool:
        table = tables.get((connection.get("table") or "").lower())
        return table is not None and any(
            _same_name(foreign_key.get("name"), connection.get("foreignKey"))
            for foreign_key in table.get("foreign_keys", [])
        )

    kept_connections = [
        connection
        for connection in connections
        if connection.get("startFigure") in figure_ids
        and connection.get("endFigure") in figure_ids
        and has_foreign_key(connection)
    ]

    changed = len(kept_figures) != len(figures) or len(kept_connections) != len(connections)
    if changed:
        diagram["figures"] = kept_figures
        diagram["connections"] = kept_connections
        for layer in diagram.get("layers", []):
            layer["figures"] = [
                figure for figure in layer.get("figures", []) if figure in figure_ids
            ]
    return changed
