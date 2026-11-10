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

"""A schema on a server, read as the CREATE statements of a project.

The reverse of a deployment: the objects of a live schema are listed
(list_schema_objects), and the CREATE statements the server gives for them
(SHOW CREATE ...) are grouped as an MSM development script keeps them
(schema_sql), the way a MySQL Workbench model is imported (statements.Imported).
modeling.create_project_from_live_schema writes them into a new project.

What makes a statement depend on the server it was read from is taken out:
the DEFINER clauses, the schema's own name in qualified names (a project is
deployed under any name), and a table's AUTO_INCREMENT counter. Data, users,
roles and grants are not read.
"""

import re
import time
from contextlib import contextmanager
from typing import Optional

from msm_plugin.lib import core, diagrams, management
from msm_plugin.lib import statements as statements_lib
from msm_plugin.lib.statements import quote_identifier

# The kinds of objects listed and selected, as the objects dicts name them
OBJECT_KINDS = ("tables", "views", "procedures", "functions", "triggers", "events", "sequences")

# msm's own objects in a deployed schema (Sections 120, 160 and helpers);
# never imported, as every development script makes them itself.
MSM_PREFIX = "msm_"

# The stage schema_sql reports to a task
READING_STAGE = "Reading the objects"

_TABLES_SQL = """
    SELECT TABLE_NAME AS name, TABLE_TYPE AS type, TABLE_COMMENT AS comment
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = ?
    ORDER BY TABLE_NAME
"""
_ROUTINES_SQL = """
    SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS type
    FROM INFORMATION_SCHEMA.ROUTINES
    WHERE ROUTINE_SCHEMA = ?
    ORDER BY ROUTINE_NAME
"""
_TRIGGERS_SQL = """
    SELECT TRIGGER_NAME AS name, EVENT_OBJECT_TABLE AS `table`
    FROM INFORMATION_SCHEMA.TRIGGERS
    WHERE TRIGGER_SCHEMA = ?
    ORDER BY EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_ORDER
"""
_VIEWS_SQL = """
    SELECT TABLE_NAME AS name, VIEW_DEFINITION AS definition
    FROM INFORMATION_SCHEMA.VIEWS
    WHERE TABLE_SCHEMA = ?
"""
_EVENTS_SQL = """
    SELECT EVENT_NAME AS name
    FROM INFORMATION_SCHEMA.EVENTS
    WHERE EVENT_SCHEMA = ?
    ORDER BY EVENT_NAME
"""
_FOREIGN_KEYS_SQL = """
    SELECT TABLE_NAME AS `table`, REFERENCED_TABLE_NAME AS referenced
    FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = ? AND UNIQUE_CONSTRAINT_SCHEMA = CONSTRAINT_SCHEMA
"""

_AUTO_INCREMENT = re.compile(r"\s+AUTO_INCREMENT=\d+")
_FOREIGN_KEY_LINE = re.compile(
    r"^\s*CONSTRAINT\s+.*?\bFOREIGN\s+KEY\b.*?\bREFERENCES\s+(?P<ref>"
    r"(?:`(?:[^`]|``)+`|\w+)(?:\s*\.\s*(?:`(?:[^`]|``)+`|\w+))?)",
    re.IGNORECASE,
)


def _rows(session, sql: str, params: list) -> list:
    return core.MsmDbExec(sql).exec(session, params).items


def _schema_version(session, schema: str, left_out: list) -> Optional[str]:
    """The version msm deployed, where the schema has msm's version view."""
    if f"{MSM_PREFIX}schema_version" not in left_out:
        return None
    try:
        return "%d.%d.%d" % tuple(core.get_db_schema_version(session, schema))
    except Exception:
        return None


# The kinds a view reads, as the listing names them
_READ_KINDS = ("functions", "sequences", "tables", "views")


# The kinds of mariadb-lsp outline's reads, as the listing's kinds take them
_LSP_READ_KINDS = {
    # A sequence read as a table (SELECT ... FROM s) is a table_or_view read
    "table_or_view": ("tables", "views", "sequences"),
    "function": ("functions",),
    "sequence": ("sequences",),
}


def _view_reads(
    definitions: dict, names: dict, schema: str, lsp_path: Optional[str]
) -> dict:
    """What each view reads (its definition by name in definitions), as
    mariadb-lsp's parser finds it (outline's views[].reads, which knows CTE
    names and aliases for what they are): [{kind, name}] of the listed
    objects (names) of the schema, named as the listing names them (names
    compare as diagrams.same_name does), the view itself left out, sorted by
    kind then name; None for a view whose reads cannot be worked out (no
    definition, one that does not parse, no binary that outlines reads)."""
    from msm_plugin.lib import lsp

    unknown = {view: None for view in definitions}
    binary = lsp.find_lsp(lsp_path)
    readable = {view: sql for view, sql in definitions.items() if sql}
    if binary is None or not readable or not lsp.outlines_view_reads(binary):
        return unknown
    script = "".join(
        f"CREATE VIEW {quote_identifier(view)} AS {sql};\n" for view, sql in readable.items()
    )
    try:
        outlined = lsp.outline(binary, script)
    except (ValueError, OSError):
        return unknown
    # The listing's own spelling of each name, lower-cased, as diagrams.same_name compares them
    listed = {kind: {each.lower(): each for each in names[kind]} for kind in names}
    views = [view for each in outlined.get("schemas", []) for view in each.get("views", [])]
    by_view = {}
    for view in views:
        reads = set()
        for read in view["reads"] or []:
            if read.get("schema") is not None and not diagrams.same_name(read["schema"], schema):
                continue
            for kind in _LSP_READ_KINDS.get(read.get("kind"), ()):
                name = listed[kind].get(read["name"].lower())
                if name is not None:
                    reads.add((kind, name))
        name = listed["views"].get(view["name"].lower(), view["name"])
        reads.discard(("views", name))
        by_view[name] = [{"kind": kind, "name": each} for kind, each in sorted(reads)]
    # A view the binary could not parse makes no view of the outline: None
    return {view: by_view.get(view) for view in definitions}


def list_schema_objects(session, schema: str, lsp_path: str = None) -> dict:
    """The objects of a live schema an import can take, by kind.

    Returns:
        {schema, tables: [{name, comment, triggers}], views: [{name,
        comment, reads: what it reads, [{kind, name}] of the listed tables,
        views, sequences and functions (None where its definition cannot be
        read or parsed)}], procedures: [{name}], functions: [{name}], triggers:
        [{name, table}], events: [{name}], sequences: [{name}],
        project_folder_name: the folder a project of the schema gets,
        schema_version: the version msm deployed or None, left_out: msm's own
        objects, which are never imported (views and routines named msm_*;
        a schema msm does not manage has none, unless it names its own so)},
        each list sorted by name.

    A view's reads come from mariadb-lsp's parser (lsp_path, or the binary
    find_lsp finds), so CTE names and aliases are not taken for tables;
    without a binary that outlines them, every view's reads are None.

    Raises:
        ValueError: For a schema the server does not have (or the account
            cannot see).
    """
    if not core.db_schema_exists(session, schema):
        raise ValueError(f"The server has no schema `{schema}`, or this account cannot see it.")
    result = {kind: [] for kind in OBJECT_KINDS}
    left_out = []
    for row in _rows(session, _TABLES_SQL, [schema]):
        name, kind = row["name"], (row["type"] or "").upper()
        if kind == "VIEW":
            if name.startswith(MSM_PREFIX):
                left_out.append(name)
            else:
                result["views"].append({"name": name, "comment": row["comment"] or ""})
        elif kind == "SEQUENCE":
            result["sequences"].append({"name": name})
        elif kind in ("BASE TABLE", "SYSTEM VERSIONED"):
            result["tables"].append({"name": name, "comment": row["comment"] or "", "triggers": []})
    for row in _rows(session, _ROUTINES_SQL, [schema]):
        if row["name"].startswith(MSM_PREFIX):
            left_out.append(row["name"])
        else:
            kind = "functions" if (row["type"] or "").upper() == "FUNCTION" else "procedures"
            result[kind].append({"name": row["name"]})
    tables = {table["name"]: table for table in result["tables"]}
    for row in _rows(session, _TRIGGERS_SQL, [schema]):
        result["triggers"].append({"name": row["name"], "table": row["table"]})
        if row["table"] in tables:
            tables[row["table"]]["triggers"].append(row["name"])
    result["triggers"].sort(key=lambda trigger: trigger["name"])
    result["events"] = [{"name": row["name"]} for row in _rows(session, _EVENTS_SQL, [schema])]
    stored = {row["name"]: row["definition"] for row in _rows(session, _VIEWS_SQL, [schema])}
    reads = _view_reads(
        {view["name"]: stored.get(view["name"]) for view in result["views"]},
        {kind: {item["name"] for item in result[kind]} for kind in _READ_KINDS},
        schema, lsp_path,
    )
    for view in result["views"]:
        view["reads"] = reads[view["name"]]
    result["schema"] = schema
    result["project_folder_name"] = management.project_folder_name(schema)
    result["schema_version"] = _schema_version(session, schema, left_out)
    result["left_out"] = sorted(left_out)
    return result


def selected_objects(listed: dict, objects: Optional[dict]) -> dict:
    """The names of each kind an import takes: all listed for objects None,
    else those objects names (a kind left out: none). Without a `triggers`
    list the selected tables bring their triggers.

    Raises:
        ValueError: For an unknown kind, a name the schema does not have, or
            a trigger whose table is not selected.
    """
    available = {kind: [item["name"] for item in listed[kind]] for kind in OBJECT_KINDS}
    # Triggers in their firing order (each table's list keeps ACTION_ORDER),
    # as the order they are created in sets it on the deployed schema
    available["triggers"] = [
        trigger for table in listed["tables"] for trigger in table["triggers"]
    ]
    if objects is None:
        return available
    if not isinstance(objects, dict):
        raise ValueError("`objects` must be an object of lists of names, or null for all.")
    unknown = set(objects) - set(OBJECT_KINDS)
    if unknown:
        raise ValueError(
            f"Unknown object kind `{sorted(unknown)[0]}`; the kinds are {', '.join(OBJECT_KINDS)}."
        )
    selected = {}
    for kind in OBJECT_KINDS:
        names = objects.get(kind) or []
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError(f"`objects.{kind}` must be a list of names.")
        known = set(available[kind])
        missing = [name for name in names if name not in known]
        if missing:
            raise ValueError(
                f"The schema `{listed['schema']}` has no {kind[:-1]} `{missing[0]}`."
            )
        # In the schema's order, each once
        wanted = set(names)
        selected[kind] = [name for name in available[kind] if name in wanted]
    table_of = {trigger["name"]: trigger["table"] for trigger in listed["triggers"]}
    tables = set(selected["tables"])
    if "triggers" not in objects:
        selected["triggers"] = [t for t in available["triggers"] if table_of[t] in tables]
    for trigger in selected["triggers"]:
        if table_of[trigger] not in tables:
            raise ValueError(
                f"The trigger `{trigger}` needs its table `{table_of[trigger]}` in the selection."
            )
    return selected


@contextmanager
def task_stage(task, name: str):
    """A stage of a task of the shell task manager, where one is given:
    started, timed and finished around the block, which gets a step(done,
    total) to report progress with; a cancel asked for raises TaskCancelled
    at each of them."""
    if task is None:
        yield lambda done, total: None
        return
    task.check_cancelled()
    task.stage_started(name)
    started = time.monotonic()

    def step(done: int, total: int) -> None:
        task.check_cancelled()
        task.progress(done, total, stage=name)

    yield step
    task.check_cancelled()
    task.stage_finished(name, round(time.monotonic() - started, 1))


def _show_create(session, kind: str, schema: str, name: str) -> Optional[str]:
    """The CREATE statement the server gives for an object; None where it
    gives none (a routine the account may not read)."""
    rows = _rows(
        session, f"SHOW CREATE {kind} {quote_identifier(schema)}.{quote_identifier(name)}", []
    )
    if not rows:
        return None
    row = rows[0]
    for column in ("Create Table", "Create View", "Create Procedure", "Create Function",
                   "SQL Original Statement", "Create Event"):
        if row.get(column):
            return row[column]
    return None


def _reference_name(reference: str) -> tuple:
    """(schema or None, table) of a REFERENCES target."""
    parts = re.findall(r"`(?:[^`]|``)+`|\w+", reference)
    names = [part[1:-1].replace("``", "`") if part.startswith("`") else part for part in parts]
    return (None, names[0]) if len(names) == 1 else (names[0], names[1])


def table_sql(sql: str, schema: str, tables: set) -> tuple:
    """A CREATE TABLE statement of SHOW CREATE TABLE without its AUTO_INCREMENT
    counter and without the foreign keys to tables of the schema that are
    not imported; and the (name, table) of the foreign keys left out.

    SHOW CREATE TABLE writes one definition per line, so a foreign key is
    left out by its line."""
    kept, left_out = [], []
    for line in sql.split("\n"):
        if line.startswith(")"):
            # The table options, not a column's DEFAULT or COMMENT
            line = _AUTO_INCREMENT.sub("", line)
        match = _FOREIGN_KEY_LINE.match(line)
        if match:
            ref_schema, ref_table = _reference_name(match.group("ref"))
            if ref_schema in (None, schema) and ref_table not in tables:
                name = re.match(r"\s*CONSTRAINT\s+(`(?:[^`]|``)+`|\w+)", line, re.IGNORECASE)
                left_out.append((name.group(1).strip("`") if name else "", ref_table))
                continue
        kept.append(line)
    # The last definition before the table options ends without a comma
    for pos in range(len(kept) - 1, 0, -1):
        if kept[pos].lstrip().startswith(")"):
            kept[pos - 1] = kept[pos - 1].rstrip().rstrip(",")
            break
    return statements_lib.unqualified("\n".join(kept), schema), left_out


def _ordered(names: list, depends_on: dict) -> list:
    """The names so that each comes after those it depends on, where a
    cycle allows; otherwise in the given order."""
    ordered, placed, visiting = [], set(), set()

    def place(name):
        if name in placed or name in visiting:
            return
        visiting.add(name)
        for other in depends_on.get(name, ()):
            if other != name:
                place(other)
        visiting.discard(name)
        placed.add(name)
        ordered.append(name)

    for name in names:
        place(name)
    return ordered


# The kinds of Section 150, after the views: the selected kinds each takes,
# what SHOW CREATE calls them, the Imported member and its block's title
_SECTION_150 = (
    ("routines", (("procedures", "PROCEDURE"), ("functions", "FUNCTION")), "PROCEDUREs and FUNCTIONs"),
    ("triggers", (("triggers", "TRIGGER"),), "TRIGGERs"),
    ("events", (("events", "EVENT"),), "EVENTs"),
)


def schema_sql(
    session, schema: str, selected: dict, listed: dict, task=None
) -> statements_lib.Imported:
    """The CREATE statements of the selected objects of a live schema,
    grouped as an MSM development script keeps them (statements.Imported).

    Sequences and tables go in Section 140's tables part, each table after
    the tables its foreign keys refer to where no cycle forbids (Section 010
    turns the foreign key checks off, so a cycle deploys too). Views (each
    after the views it selects from), routines, triggers and events are
    written to be run again, in DELIMITER %% blocks as Section 150 has them,
    without their DEFINER and the schema's own name in qualified names.

    Each view comes after the views it reads, and one that reads an object
    the import leaves out gets a warning: it will not deploy without it. Its
    reads are those of listed (list_schema_objects' listing), so the warning
    says what a client showed from the listing beforehand.

    A task given (the shell task manager's) gets the stage READING_STAGE
    with a step per object; a cancel of it raises TaskCancelled.
    """
    result = statements_lib.Imported()
    tables = set(selected["tables"])
    total = sum(len(names) for names in selected.values())
    with task_stage(task, READING_STAGE) as step:
        done = 0

        def show_create(kind: str, name: str) -> Optional[str]:
            nonlocal done
            sql = _show_create(session, kind, schema, name)
            done += 1
            step(done, total)
            return sql

        def idempotent(kind: str, name: str, sql: Optional[str]) -> Optional[str]:
            if not sql:
                result.warnings.append(
                    f"The server gave no CREATE statement for the {kind.lower()} `{name}` "
                    "(this account may not read its definition); it was left out."
                )
                return None
            sql = statements_lib.unqualified(statements_lib.without_definer(sql), schema)
            return statements_lib.banner(kind, name) + statements_lib.idempotent_create(
                kind, name, sql
            )

        references = {}
        for row in _rows(session, _FOREIGN_KEYS_SQL, [schema]):
            if row["table"] in tables and row["referenced"] in tables:
                references.setdefault(row["table"], set()).add(row["referenced"])
        create = []
        for name in selected["sequences"]:
            sql = statements_lib.strip_statement(show_create("SEQUENCE", name))
            create.append(statements_lib.unqualified(sql, schema) + ";")
        for name in _ordered(selected["tables"], references):
            sql, left_out = table_sql(show_create("TABLE", name), schema, tables)
            for foreign_key, referenced in left_out:
                result.warnings.append(
                    f"The foreign key `{foreign_key}` of `{name}` refers to `{referenced}`, "
                    "which is not imported; it was left out."
                )
            create.append(sql + ";")
        result.tables = "\n\n".join(create) or None

        view_sql = {name: show_create("VIEW", name) for name in selected["views"]}
        chosen = {kind: set(selected[kind]) for kind in _READ_KINDS}
        reads_of = {view["name"]: view["reads"] for view in listed["views"]}
        view_uses = {}
        for name in selected["views"]:
            reads = reads_of.get(name)
            if reads is None:
                # Its definition could not be read or parsed: neither its
                # place among the views nor what it misses is known
                result.warnings.append(
                    f"What the view `{name}` reads could not be worked out; it may be "
                    "written before a view it reads, and is not checked for what is not imported."
                )
                reads = []
            view_uses[name] = {
                read["name"] for read in reads
                if read["kind"] == "views" and read["name"] in chosen["views"]
            }
            for read in reads:
                if read["name"] not in chosen[read["kind"]]:
                    result.warnings.append(
                        f"The view `{name}` reads `{read['name']}`, which is not imported; "
                        "the project will not deploy the view without it."
                    )
        views = [idempotent("VIEW", name, view_sql[name])
                 for name in _ordered(selected["views"], view_uses)]
        result.views = statements_lib.delimiter_block([sql for sql in views if sql], "VIEWs")
        result.counts = {
            "tables": len(selected["tables"]),
            "sequences": len(selected["sequences"]),
            "views": len([sql for sql in views if sql]),
        }
        for member, kinds, title in _SECTION_150:
            written = [
                sql
                for kind, show in kinds
                for sql in (idempotent(show, name, show_create(show, name))
                            for name in selected[kind])
                if sql
            ]
            setattr(result, member, statements_lib.delimiter_block(written, title))
            result.counts[member] = len(written)
    return result
