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
from dataclasses import dataclass
from typing import Callable, Optional

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
# The width a number of a position ({line, character}) counts as when
# dump_document decides whether a value fits on a line, whatever its digits:
# the layout, and so the line count, of a snapshot then does not depend on
# its positions, which msm moves after writing Section 160 (see
# modeling._write).
_POSITION_NUMBER_WIDTH = 7


# -----------------------------------------------------------------------------
# The documents' layout


def is_position(value) -> bool:
    """Whether a value is a position ({line, character}) of the snapshot."""
    return (
        isinstance(value, dict) and len(value) == 2
        # type() rather than isinstance(): a bool is an int too
        and type(value.get("line")) is int and type(value.get("character")) is int
    )


def dump_document(document) -> str:
    """Writes a document as JSON laid out for reading and for diffs.

    A value that fits on a line stays on one; otherwise its members go on
    lines of their own, so a table, column, figure or connection is one line
    where it is short enough and a change touches the lines it changes. The
    numbers of a position count as _POSITION_NUMBER_WIDTH wide, so moving
    positions never changes the layout.

    Args:
        document: The JSON value.

    Returns:
        The JSON text, without a trailing line break.
    """

    def dump(value, indent: str) -> tuple:
        """The value's text, and the width it counts as on one line."""
        # Children first, so every value is written once: a container fits on
        # a line exactly when its children each do and the whole is short.
        if not isinstance(value, (dict, list)) or not value:
            text = json.dumps(value, ensure_ascii=False)
            return text, len(text)
        inner = indent + "  "
        if isinstance(value, dict):
            position = is_position(value)
            parts = []
            for key, item in value.items():
                text, width = dump(item, inner)
                if position:
                    width = _POSITION_NUMBER_WIDTH
                key_text = f"{json.dumps(key, ensure_ascii=False)}: "
                parts.append((key_text + text, len(key_text) + width))
            opening, closing = "{", "}"
        else:
            parts = [dump(item, inner) for item in value]
            opening, closing = "[", "]"
        # A child on lines of its own counts as wider than a line.
        width = 2 + sum(width for _, width in parts) + 2 * (len(parts) - 1)
        if len(indent) + width <= _ONE_LINE_WIDTH:
            return opening + ", ".join(text for text, _ in parts) + closing, width
        return (
            f"{opening}\n{inner}" + f",\n{inner}".join(text for text, _ in parts)
            + f"\n{indent}{closing}",
            _ONE_LINE_WIDTH + 1,
        )

    return dump(document, "")[0]


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
    r"|`(?:[^`]++|``)*`|\"(?:[^\"\\]++|\\.|\"\")*\""
    r"|'(?P<literal>(?:[^'\\]++|\\.|'')*)'"
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


def name_foreign_keys(table: dict) -> None:
    """Gives every foreign key the SQL leaves unnamed the name MariaDB gives it.

    A diagram's connection names its foreign key, so each needs one. A
    foreign key with neither a CONSTRAINT name nor an index name is named
    `<table>_ibfk_<n>` by InnoDB, n one more than the highest such number the
    table has so far (ha_innodb.cc: dict_table_get_foreign_id), so 1, 2, ...
    in a CREATE TABLE, continued by each ALTER TABLE. The outline lists them
    in that order. Such a name is marked ``generated_name``, as the SQL does
    not have it.
    """
    prefix = f"{table['name']}_ibfk_"
    foreign_keys = table.get("foreign_keys", [])
    # A name the SQL gives one already is not given to another.
    taken = {fk["name"].lower() for fk in foreign_keys if fk.get("name") is not None}
    used = 0
    for foreign_key in foreign_keys:
        if foreign_key.get("name") is None:
            used += 1
            while f"{prefix}{used}".lower() in taken:
                used += 1
            foreign_key["name"] = f"{prefix}{used}"
            foreign_key["generated_name"] = True


def same_name(a, b) -> bool:
    """Whether two names are the same, ignoring case; never for a non-name."""
    return isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower()


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
            if not same_name(name, schema_name):
                continue
            document["options"] = schema.get("options", {})
            for key in ("range", "selection_range"):
                if key in schema:
                    document[key] = schema[key]
        for kind in SCHEMA_OBJECT_KINDS:
            document[kind].extend(schema.get(kind, []))

    for table in document["tables"]:
        name_foreign_keys(table)
        _add_relationship_members(table)

    document["rest"] = outline.get("rest", {})
    document["notes"] = outline.get("notes", [])
    return document


def unread_statements(outline: dict) -> list:
    """The outline's notes on statements it did not apply.

    A statement with a syntax error, an ALTER TABLE of a table the script
    does not create, an include that cannot be read and so on is left out of
    the outline, so a snapshot made from it would silently miss what it
    defines. The language server says so with each note's ``applied``; a
    binary from before that member is judged by its message.
    """
    return [
        note
        for note in outline.get("notes", [])
        if (not note["applied"] if "applied" in note else "not read" in note.get("message", ""))
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


TABLE_FIGURE = "TableFigure"
VIEW_FIGURE = "ViewFigure"
ROUTINE_GROUP_FIGURE = "RoutineGroupFigure"
EVENT_GROUP_FIGURE = "EventGroupFigure"
SEQUENCE_FIGURE = "SequenceFigure"
# A note's text and an image embedded as a data URL: msm keeps them as they
# are, the schema has no say in them.
NOTE_FIGURE = "NoteFigure"
IMAGE_FIGURE = "ImageFigure"

# The member of a table in catalog() listing its triggers, for the card.
TABLE_TRIGGERS = "triggers"

# The colours new figures get, Workbench's.
FIGURE_COLOR = "#98BFDA"
VIEW_FIGURE_COLOR = "#FEDE58"
ROUTINE_FIGURE_COLOR = "#98D8A5"
# Workbench has neither: msm's own.
EVENT_FIGURE_COLOR = "#F5C39A"
SEQUENCE_FIGURE_COLOR = "#C9B8E8"
NOTE_FIGURE_COLOR = "#FEFDED"
# The names of the group figures msm adds to a diagram.
ROUTINE_GROUP_NAME = "Routines"
EVENT_GROUP_NAME = "Events"


def routine_label(routine: dict) -> str:
    """The row a routine group figure shows for a routine: its name with the
    parameters' types, and what a function returns."""
    parameters = ", ".join(
        parameter.get("type") or "" for parameter in routine.get("parameters") or []
    )
    label = f"{routine.get('name', '')}({parameters})"
    if routine.get("returns"):
        label += f": {routine['returns']}"
    return label


def event_label(event: dict) -> str:
    """The row an event group figure shows for an event: its name and schedule."""
    schedule = event.get("schedule")
    return f"{event.get('name', '')} {schedule}" if schedule else event.get("name", "")


def sequence_rows(sequence: dict) -> list:
    """The rows a sequence figure shows, as the VS Code extension's modeler
    draws them: the type, START WITH and INCREMENT BY, BIGINT and 1 where
    the CREATE SEQUENCE does not write them (the server's default type and
    increment; its START is then MINVALUE, or MAXVALUE for a negative
    INCREMENT, which the card does not work out)."""
    return [
        sequence.get("type") or "BIGINT",
        f"START WITH {sequence.get('start') or 1}",
        f"INCREMENT BY {sequence.get('increment') or 1}",
    ]


def _table_sections(table: dict, figure: dict) -> list:
    """The table card's sections: the columns and triggers unless collapsed,
    the indexes and foreign keys where expanded (Workbench's defaults, the
    triggers shown like the columns)."""
    sections = []
    if figure.get("columnsExpanded") is not False:
        sections.append(len(table.get("columns", [])))
    if figure.get("indicesExpanded") is True:
        sections.append(len(table.get("indexes", [])))
    if figure.get("foreignKeysExpanded") is True:
        sections.append(len(table.get("foreign_keys", [])))
    if figure.get("triggersExpanded") is not False:
        sections.append(len(table.get(TABLE_TRIGGERS, [])))
    return sections


@dataclass(frozen=True)
class FigureKind:
    """A kind of figure that shows objects of the snapshot, and what its
    card shows of them.

    Attributes:
        object: What one of its objects is called: an edit's member naming
            it (rename_<object>) and the word in messages.
        key: The figure's member naming what it shows: the object's name
            (Workbench's link key as well), or for a group the names listed
            (Workbench links the group instead, ``routineGroup``).
        objects: The snapshot list the objects are in, a catalog() key.
        many: Whether the figure lists several objects (a routine group).
        color: The colour a new figure gets.
        rows: The card's rows for the object (or the objects), a text each.
        sections: The rows of each expanded section of the card, for the
            object(s) and the figure's flags; an empty section takes no room.
        group_name: The name of the group figure msm adds (many only).
    """

    object: str
    key: str
    objects: str
    many: bool
    color: str
    rows: Callable
    sections: Callable
    group_name: str = ""


FIGURE_KINDS = {
    TABLE_FIGURE: FigureKind(
        "table", "table", "tables", False, FIGURE_COLOR,
        # The triggers' rows as well (name, timing and events), so the card
        # is wide enough for them.
        rows=lambda table: [
            column.get("name", "") + (column.get("type") or "")
            for column in table.get("columns", [])
        ] + [
            " ".join((trigger.get("name", ""), trigger.get("timing") or "",
                      trigger.get("events") or ""))
            for trigger in table.get(TABLE_TRIGGERS, [])
        ],
        sections=_table_sections,
    ),
    VIEW_FIGURE: FigureKind(
        "view", "view", "views", False, VIEW_FIGURE_COLOR,
        rows=lambda view: [str(column) for column in view.get("columns") or []],
        sections=lambda view, figure: (
            [len(view.get("columns") or [])] if figure.get("columnsExpanded") is not False
            else []
        ),
    ),
    ROUTINE_GROUP_FIGURE: FigureKind(
        "routine", "routines", "routines", True, ROUTINE_FIGURE_COLOR,
        rows=lambda routines: [routine_label(routine) for routine in routines],
        sections=lambda routines, figure: [len(routines)],
        group_name=ROUTINE_GROUP_NAME,
    ),
    EVENT_GROUP_FIGURE: FigureKind(
        "event", "events", "events", True, EVENT_FIGURE_COLOR,
        rows=lambda events: [event_label(event) for event in events],
        sections=lambda events, figure: [len(events)],
        group_name=EVENT_GROUP_NAME,
    ),
    SEQUENCE_FIGURE: FigureKind(
        "sequence", "sequence", "sequences", False, SEQUENCE_FIGURE_COLOR,
        rows=sequence_rows,
        sections=lambda sequence, figure: [len(sequence_rows(sequence))],
    ),
}


def figure_type(figure: dict) -> str:
    """A figure's type: a table (the default), a view, a sequence, a group
    of routines or events, or something msm leaves alone."""
    return figure.get("type") or TABLE_FIGURE


def figure_kind(figure: dict) -> Optional[FigureKind]:
    """The kind of a figure that shows snapshot objects; None for a note, an
    image or a type msm does not know."""
    return FIGURE_KINDS.get(figure_type(figure))


def is_table_figure(figure: dict) -> bool:
    """Whether a diagram figure shows a table (the default type)."""
    return figure_type(figure) == TABLE_FIGURE


def figure_object_name(figure: dict) -> str:
    """The name of the table, view or sequence a figure shows, as written;
    the figure's own name for every other kind."""
    kind = figure_kind(figure)
    if kind is not None and not kind.many:
        return figure.get(kind.key) or figure.get("name") or ""
    return figure.get("name") or ""


def figure_table(figure: dict) -> str:
    """The lower-case name of the table a figure shows."""
    return (figure.get("table") or figure.get("name") or "").lower()


def figure_members(figure: dict) -> list:
    """The names of the objects a group figure (routines, events) lists, as
    written; none for other figures."""
    kind = figure_kind(figure)
    if kind is None or not kind.many:
        return []
    return [name for name in figure.get(kind.key) or [] if isinstance(name, str)]


def elide_images(document: dict) -> dict:
    """A copy of a diagram document without the pictures of its image
    figures: each `image` (a data URL of up to hundreds of KB) is replaced
    by `image_size`, its length. For a reader that cannot use the bytes."""
    figures = []
    for figure in document.get("figures", []):
        picture = figure.get("image")
        if isinstance(picture, str):
            figure = {key: value for key, value in figure.items() if key != "image"}
            figure["image_size"] = len(picture)
        figures.append(figure)
    return {**document, "figures": figures}


def elide_row_images(rows: list) -> list:
    """The rows of the diagrams VIEW with the diagrams' pictures left out,
    see elide_images."""
    return [
        {**row, "document": elide_images(row["document"])}
        if row["format"] == DIAGRAM_FORMAT else row
        for row in rows
    ]


def keep_images(document: dict, stored: Optional[dict]) -> None:
    """Gives an image figure that comes without its picture the one the
    stored diagram holds for the figure of the same id, in place."""
    pictures = {
        figure.get("id"): figure["image"]
        for figure in (stored or {}).get("figures", [])
        if isinstance(figure.get("image"), str) and figure.get("id")
    }
    for figure in document.get("figures", []):
        if figure_type(figure) == IMAGE_FIGURE and not figure.get("image"):
            if figure.get("id") in pictures:
                figure["image"] = pictures[figure["id"]]


def catalog(schema_document: dict) -> dict:
    """The snapshot's tables, views, routines, events and sequences by their
    lower-case name; a table with triggers lists them as TABLE_TRIGGERS."""
    objects = {
        kind: {item["name"].lower(): item for item in schema_document.get(kind, [])}
        for kind in dict.fromkeys(each.objects for each in FIGURE_KINDS.values())
    }
    tables = objects["tables"]
    by_table = {}
    for trigger in schema_document.get("triggers", []):
        by_table.setdefault((trigger.get("table") or "").lower(), []).append(trigger)
    for name, triggers in by_table.items():
        if name in tables:
            # A copy: the snapshot's table stays as the outline has it.
            tables[name] = {**tables[name], TABLE_TRIGGERS: triggers}
    return objects


def figure_objects(figure: dict, catalog: dict):
    """What of the snapshot a figure shows: the table, view or sequence, the
    routines or events a group lists (those the schema has), or None for a
    figure of nothing the schema has, a note or an image."""
    kind = figure_kind(figure)
    if kind is None:
        return None
    objects = catalog[kind.objects]
    if kind.many:
        found = [
            objects[name.lower()] for name in figure_members(figure) if name.lower() in objects
        ]
        return found or None
    return objects.get(figure_object_name(figure).lower())


def reconcile_diagram(diagram: dict, schema_document: dict) -> bool:
    """Drops the figures and connections of objects the schema no longer has.

    A table figure shows a table, a view figure a view, a sequence figure a
    sequence, and a group figure lists routines or events, losing the ones
    the schema no longer has and going with the last of them; a connection
    is a foreign key of the table of its start figure. Figures of other
    types are left alone. New objects are not added: placing them is up to
    the user or the layout.

    Args:
        diagram: The msm-diagram/1 document, changed in place.
        schema_document: The msm-schema/1 snapshot.

    Returns:
        Whether anything was dropped.
    """
    objects = catalog(schema_document)
    tables = objects["tables"]
    figures, connections = diagram["figures"], diagram["connections"]

    changed = False
    kept_figures = []
    for figure in figures:
        kind = figure_kind(figure)
        if kind is not None and kind.many:
            shown = [
                name for name in figure_members(figure) if name.lower() in objects[kind.objects]
            ]
            if shown != (figure.get(kind.key) or []):
                figure[kind.key] = shown
                changed = True
            if not shown:
                continue
        elif kind is not None and figure_objects(figure, objects) is None:
            continue
        kept_figures.append(figure)
    figure_ids = {figure.get("id") for figure in kept_figures}

    def has_foreign_key(connection) -> bool:
        table = tables.get((connection.get("table") or "").lower())
        return table is not None and any(
            same_name(foreign_key.get("name"), connection.get("foreignKey"))
            for foreign_key in table.get("foreign_keys", [])
        )

    kept_connections = [
        connection
        for connection in connections
        if connection.get("startFigure") in figure_ids
        and connection.get("endFigure") in figure_ids
        and has_foreign_key(connection)
    ]

    if len(kept_figures) != len(figures) or len(kept_connections) != len(connections):
        changed = True
        diagram["figures"] = kept_figures
        diagram["connections"] = kept_connections
        for layer in diagram.get("layers", []):
            layer["figures"] = [
                figure for figure in layer.get("figures", []) if figure in figure_ids
            ]
    return changed
