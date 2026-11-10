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
(schema_sql), the way a MySQL Workbench model is imported (workbench.Imported).
modeling.create_project_from_live_schema writes them into a new project.

What makes a statement depend on the server it was read from is taken out:
the DEFINER clauses, the schema's own name in qualified names (a project is
deployed under any name), and a table's AUTO_INCREMENT counter. Data, users,
roles and grants are not read.
"""

import os
import re
import time
from typing import Optional

from msm_plugin.lib import management
from msm_plugin.lib import statements as statements_lib
from msm_plugin.lib import workbench

# The kinds of objects listed and selected, as the objects dicts name them
OBJECT_KINDS = ("tables", "views", "procedures", "functions", "triggers", "events", "sequences")

# msm's own objects in a deployed schema (Sections 120, 160 and helpers);
# never imported, as every development script makes them itself.
MSM_PREFIX = "msm_"

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

# A DEFINER clause as SHOW CREATE writes it: DEFINER=`user`@`host`,
# DEFINER=CURRENT_USER or DEFINER=`role`
_QUOTED = r"(?:`(?:[^`]|``)*`|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")"
_DEFINER = re.compile(
    rf"\s+DEFINER\s*=\s*(?:CURRENT_USER(?:\s*\(\s*\))?|(?:{_QUOTED}|[\w.$-]+)"
    rf"(?:\s*@\s*(?:{_QUOTED}|[\w.%$-]+))?)",
    re.IGNORECASE,
)
_AUTO_INCREMENT = re.compile(r"\s+AUTO_INCREMENT=\d+")
_FOREIGN_KEY_LINE = re.compile(
    r"^\s*CONSTRAINT\s+.*?\bFOREIGN\s+KEY\b.*?\bREFERENCES\s+(?P<ref>"
    r"(?:`(?:[^`]|``)+`|\w+)(?:\s*\.\s*(?:`(?:[^`]|``)+`|\w+))?)",
    re.IGNORECASE,
)


def _quote(name: str) -> str:
    return workbench.quote_identifier(name)


def _rows(session, sql: str, params: list) -> list:
    result = session.run_sql(sql, params)
    columns = [column.get_column_label() for column in result.get_columns()]
    return [dict(zip(columns, row)) for row in result.fetch_all()]


def _schema_exists(session, schema: str) -> bool:
    return bool(_rows(
        session, "SELECT 1 FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = ?", [schema]
    ))


def _schema_version(session, schema: str, views: list) -> Optional[str]:
    """The version msm deployed, where the schema has msm's version view."""
    if f"{MSM_PREFIX}schema_version" not in views:
        return None
    try:
        rows = _rows(
            session, f"SELECT major, minor, patch FROM {_quote(schema)}.`msm_schema_version`", []
        )
    except Exception:
        return None
    if not rows:
        return None
    return "%d.%d.%d" % (rows[0]["major"], rows[0]["minor"], rows[0]["patch"])


def list_schema_objects(session, schema: str) -> dict:
    """The objects of a live schema an import can take, by kind.

    Returns:
        {schema, tables: [{name, comment, triggers}], views: [{name,
        comment}], procedures: [{name}], functions: [{name}], triggers:
        [{name, table}], events: [{name}], sequences: [{name}],
        project_folder_name: the folder a project of the schema gets,
        schema_version: the version msm deployed or None, left_out: msm's own
        objects, which are never imported (views and routines named msm_*;
        a schema msm does not manage has none, unless it names its own so)},
        each list sorted by name.

    Raises:
        ValueError: For a schema the server does not have (or the account
            cannot see).
    """
    if not _schema_exists(session, schema):
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
        elif (row["type"] or "").upper() == "FUNCTION":
            result["functions"].append({"name": row["name"]})
        else:
            result["procedures"].append({"name": row["name"]})
    tables = {table["name"]: table for table in result["tables"]}
    for row in _rows(session, _TRIGGERS_SQL, [schema]):
        result["triggers"].append({"name": row["name"], "table": row["table"]})
        if row["table"] in tables:
            tables[row["table"]]["triggers"].append(row["name"])
    result["triggers"].sort(key=lambda trigger: trigger["name"])
    result["events"] = [{"name": row["name"]} for row in _rows(session, _EVENTS_SQL, [schema])]
    result["schema"] = schema
    result["project_folder_name"] = os.path.basename(
        management.project_folder_path("", schema)
    )
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
        missing = [name for name in names if name not in available[kind]]
        if missing:
            raise ValueError(
                f"The schema `{listed['schema']}` has no {kind[:-1]} `{missing[0]}`."
            )
        # In the schema's order, each once
        selected[kind] = [name for name in available[kind] if name in names]
    table_of = {trigger["name"]: trigger["table"] for trigger in listed["triggers"]}
    if "triggers" not in objects:
        selected["triggers"] = [
            trigger for trigger in available["triggers"] if table_of[trigger] in selected["tables"]
        ]
    for trigger in selected["triggers"]:
        if table_of[trigger] not in selected["tables"]:
            raise ValueError(
                f"The trigger `{trigger}` needs its table `{table_of[trigger]}` in the selection."
            )
    return selected


def _show_create(session, kind: str, schema: str, name: str) -> Optional[str]:
    """The CREATE statement the server gives for an object; None where it
    gives none (a routine the account may not read)."""
    rows = _rows(session, f"SHOW CREATE {kind} {_quote(schema)}.{_quote(name)}", [])
    if not rows:
        return None
    row = rows[0]
    for column in ("Create Table", "Create View", "Create Procedure", "Create Function",
                   "SQL Original Statement", "Create Event"):
        if row.get(column):
            return row[column]
    return None


def _without_definer(sql: str) -> str:
    return _DEFINER.sub("", sql, count=1)


def _unqualified(sql: str, schema: str) -> str:
    """The statement without the schema's own name in front of object names."""
    quoted = re.escape(_quote(schema))
    return re.sub(rf"{quoted}\s*\.\s*(?=`)", "", sql)


def _reference_name(reference: str) -> tuple:
    """(schema or None, table) of a REFERENCES target."""
    parts = [part.strip() for part in re.findall(r"`(?:[^`]|``)+`|\w+", reference)]
    names = [part[1:-1].replace("``", "`") if part.startswith("`") else part for part in parts]
    return (None, names[0]) if len(names) == 1 else (names[0], names[1])


def table_sql(sql: str, schema: str, tables: set) -> tuple:
    """A CREATE TABLE statement of SHOW CREATE TABLE without its AUTO_INCREMENT
    counter and without the foreign keys to tables of the schema that are
    not imported; and the names of the foreign keys left out."""
    lines = _AUTO_INCREMENT.sub("", sql).split("\n")
    kept, left_out = [], []
    for line in lines:
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
    return _unqualified("\n".join(kept), schema), left_out


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


READING_STAGE = "Reading the objects"


class _Reporter:
    """Reports to a task (the shell task manager's), where one is given:
    the stage, its progress, and a cancel asked for, raised."""

    def __init__(self, task):
        self.task = task
        self.started = None

    def stage(self, name: str) -> None:
        if self.task is not None:
            self.task.check_cancelled()
            self.task.stage_started(name)
        self.started = time.monotonic()

    def progress(self, done: int, total: int, stage: str) -> None:
        if self.task is not None:
            self.task.check_cancelled()
            self.task.progress(done, total, stage=stage)

    def finished(self, name: str) -> None:
        if self.task is not None:
            self.task.check_cancelled()
            self.task.stage_finished(name, round(time.monotonic() - self.started, 1))


def schema_sql(session, schema: str, selected: dict, task=None) -> workbench.Imported:
    """The CREATE statements of the selected objects of a live schema,
    grouped as an MSM development script keeps them (workbench.Imported).

    Sequences and tables go in Section 140's tables part, each table after
    the tables its foreign keys refer to where no cycle forbids (Section 010
    turns the foreign key checks off, so a cycle deploys too). Views (each
    after the views it selects from), routines, triggers and events are
    written to be run again, in DELIMITER %% blocks as Section 150 has them.

    A task given (the shell task manager's) gets the stage READING_STAGE
    with a step per object; a cancel of it raises TaskCancelled.
    """
    result = workbench.Imported()
    tables = set(selected["tables"])
    report = _Reporter(task)
    report.stage(READING_STAGE)
    total = sum(len(names) for names in selected.values())
    done = [0]

    def show_create(kind: str, name: str) -> Optional[str]:
        sql = _show_create(session, kind, schema, name)
        done[0] += 1
        report.progress(done[0], total, READING_STAGE)
        return sql

    references = {}
    for row in _rows(session, _FOREIGN_KEYS_SQL, [schema]):
        if row["table"] in tables and row["referenced"] in tables:
            references.setdefault(row["table"], set()).add(row["referenced"])
    statements = []
    for name in selected["sequences"]:
        sql = show_create("SEQUENCE", name)
        statements.append(_unqualified(statements_lib.strip_statement(sql), schema) + ";")
    for name in _ordered(selected["tables"], references):
        sql, left_out = table_sql(show_create("TABLE", name), schema, tables)
        for foreign_key, referenced in left_out:
            result.warnings.append(
                f"The foreign key `{foreign_key}` of `{name}` refers to `{referenced}`, which "
                "is not imported; it was left out."
            )
        statements.append(sql + ";")
    result.tables = "\n\n".join(statements) or None

    def idempotent(kind: str, name: str, sql: Optional[str]) -> Optional[str]:
        if not sql:
            result.warnings.append(
                f"The server gave no CREATE statement for the {kind.lower()} `{name}` (this "
                "account may not read its definition); it was left out."
            )
            return None
        sql = _unqualified(_without_definer(sql), schema)
        return workbench._banner(kind, name) + workbench._idempotent(kind, name, sql)

    view_sql = {name: show_create("VIEW", name) for name in selected["views"]}
    view_uses = {
        name: {other for other in selected["views"]
               if other != name and sql and _quote(other) in sql}
        for name, sql in view_sql.items()
    }
    views = [idempotent("VIEW", name, view_sql[name])
             for name in _ordered(selected["views"], view_uses)]
    routines = [idempotent("PROCEDURE", name, show_create("PROCEDURE", name))
                for name in selected["procedures"]]
    routines += [idempotent("FUNCTION", name, show_create("FUNCTION", name))
                 for name in selected["functions"]]
    triggers = [idempotent("TRIGGER", name, show_create("TRIGGER", name))
                for name in selected["triggers"]]
    events = [idempotent("EVENT", name, show_create("EVENT", name))
              for name in selected["events"]]
    views, routines, triggers, events = (
        [sql for sql in group if sql] for group in (views, routines, triggers, events)
    )
    result.views = workbench._block(views, "VIEWs")
    result.routines = workbench._block(routines, "PROCEDUREs and FUNCTIONs")
    result.triggers = workbench._block(triggers, "TRIGGERs")
    result.events = workbench._block(events, "EVENTs")
    report.finished(READING_STAGE)
    result.counts = {
        "tables": len(selected["tables"]),
        "sequences": len(selected["sequences"]),
        "views": len(views),
        "routines": len(routines),
        "triggers": len(triggers),
        "events": len(events),
    }
    return result
