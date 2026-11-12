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

"""Schema edits: changes to the objects of a project, made in its SQL.

The CREATE statements of the development script are the source of truth, so
a client that changes a schema object - a diagram whose table is renamed, a
column added - asks for the change here, and msm makes it in the SQL:

- the MariaDB SQL language server's binary works the edits out (``mariadb-lsp
  edit``): the text edits to the development script and the files it
  includes, and the statements that take a deployed schema along;
- the files are written, the diagrams follow renamed objects, and the
  snapshot (MSM Section 160) is made again;
- the statements are kept in the pending update script,
  ``development/<schema>_update_next.sql``, whose Sections 240 and 260
  prepare_release moves into the update script of the next release.

Everything is written, or nothing: a failure after the first file was
written puts every file back.
"""

import json
import os
import re
from typing import Callable, Optional

from msm_plugin.lib import diagrams, lsp, management, modeling, online_alter, placement, source, statements
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib.project_changes import changes_project

# The sections of the pending update script, as in the update script.
CHANGES_SECTION_ID = "240"
REMOVALS_SECTION_ID = "260"
# Where a pending update script written before Section 260 keeps them
_LEGACY_REMOVALS_SECTION_ID = "290"

# What a migration statement does, as the binary tags it (action, object):
# - the CREATE of a view, routine, trigger or event is not kept. Section 150
#   of the release, which every deployment runs in full after the 240
#   steps, creates it (add_view, add_routine, add_trigger, add_event, the
#   second statement of rename_routine and rename_trigger), and none but a
#   view's CREATE can run inside the update procedure (MariaDB 1303 for a
#   routine or trigger, 1576 for an event);
# - the DROP of a routine goes in Section 260: a stored routine is not
#   dropped from within another (MariaDB 1357);
# - the DROP of a table, sequence, view, routine, trigger or event is kept
#   with IF EXISTS, as the binary writes it without: for a routine, every
#   deployment runs every update script's Section 260, a fresh install too,
#   where the routine does not exist (MariaDB 1305);
# - everything else goes in Section 240: a trigger's or event's DROP and an
#   event's ALTER EVENT ... RENAME TO run inside a procedure, and sequences
#   go with the tables.
_ROUTINES = ("procedure", "function")
_NOT_KEPT_CREATES = ("view", "trigger", "event") + _ROUTINES
_DROPPED_IF_EXISTS = ("table", "sequence", "view", "trigger", "event") + _ROUTINES
# The comments before it are skipped, as statements.strip_statement does.
_DROP_WITHOUT_IF_EXISTS = re.compile(
    r"^((?:\s*(?:--[^\n]*\n|#[^\n]*\n|/\*(?!!).*?\*/))*"
    rf"\s*DROP\s+(?:{'|'.join(_DROPPED_IF_EXISTS)})\s+)(?!IF\s+EXISTS\b)",
    re.IGNORECASE | re.DOTALL,
)


class StaleSchemaError(ValueError):
    """The development script changed since the snapshot the edits are for."""


# -----------------------------------------------------------------------------
# Text edits


# Positions in a text, kept here under their old names for their callers
_offset = source.position_offset
line_starts = source.line_starts


def edit_spans(text: str, edits: list) -> list:
    """LSP text edits ({range, new_text}) as (start, end, new_text) indexes
    into the text, in order.

    Raises:
        ValueError: For a position the text lacks, or edits that overlap.
    """
    starts = line_starts(text)
    return checked_spans(
        [
            (
                _offset(text, starts, edit["range"]["start"]),
                _offset(text, starts, edit["range"]["end"]),
                edit["new_text"],
            )
            for edit in edits
        ],
        "The language server's text edits overlap.",
    )


def checked_spans(spans: list, overlap: str) -> list:
    """(start, end, new_text) spans in order, refused with the message
    overlap where two overlap.

    By position only, and stable: inserts at one position keep the order
    they were given in, as LSP has it.
    """
    spans = sorted(spans, key=lambda span: span[:2])
    at = 0
    for start, end, _ in spans:
        if start < at or end < start:
            raise ValueError(overlap)
        at = end
    return spans


def apply_spans(text: str, spans: list) -> str:
    """Applies the (start, end, new_text) spans of edit_spans to a text."""
    result, at = [], 0
    for start, end, new_text in spans:
        result += [text[at:start], new_text]
        at = end
    result.append(text[at:])
    return "".join(result)


def apply_text_edits(text: str, edits: list) -> str:
    """Applies LSP text edits ({range, new_text}) to a text, see edit_spans."""
    return apply_spans(text, edit_spans(text, edits))


# -----------------------------------------------------------------------------
# The diagrams


# The rename edits a diagram follows (rename_table, rename_view, ...): the
# figure type showing the object, and the edit's member naming it.
_RENAMES = {
    f"rename_{kind.object}": (figure_type, kind.object)
    for figure_type, kind in diagrams.FIGURE_KINDS.items()
}


def rename_in_diagram(diagram: dict, edit: dict) -> bool:
    """Makes a diagram follow an edit that renames a table, a view, a
    routine, an event or a sequence: its figures name objects, and would
    otherwise be dropped as showing what the schema no longer has. A
    connection needs nothing: reconcile_diagram takes its table from its
    start figure. Nor does a renamed trigger: the table card lists the
    snapshot's triggers.

    Returns:
        Whether the diagram changed.
    """
    op, new_name = edit.get("op"), edit.get("new_name")
    if op not in _RENAMES or not isinstance(new_name, str):
        return False
    figure_type, name_key = _RENAMES[op]
    old = edit.get(name_key)
    same = diagrams.same_name
    figures = [
        figure for figure in diagram.get("figures", [])
        if diagrams.figure_type(figure) == figure_type
    ]
    kind = diagrams.FIGURE_KINDS[figure_type]
    changed = False
    if kind.many:
        for figure in figures:
            members = diagrams.figure_members(figure)
            if any(same(name, old) for name in members):
                figure[kind.key] = [new_name if same(name, old) else name for name in members]
                changed = True
        return changed

    for figure in figures:
        if same(diagrams.figure_object_name(figure), old):
            figure[kind.key] = new_name
            if same(figure.get("name"), old):
                figure["name"] = new_name
            changed = True
    return changed


# -----------------------------------------------------------------------------
# The pending update script


def pending_update_path(schema_project_path: str, project_settings: dict) -> str:
    """The pending update script of a project: the statements that take a
    deployed schema to the development script's, gathered until the next
    release."""
    return os.path.join(
        schema_project_path,
        "development",
        f"{project_settings.get('schemaFileName')}_update_next.sql",
    )


def _pending_update_template(project_settings: dict) -> str:
    notices = management.render_copyright_notices(project_settings, prefix="-- ")
    return (
        f"{notices}\n"
        "-- #############################################################################\n"
        f"-- MSM Section {CHANGES_SECTION_ID}: Non-idempotent Schema Object Changes and DROPs\n"
        "-- -----------------------------------------------------------------------------\n"
        "-- The changes made to the TABLEs of the development script since the last\n"
        "-- release, written by msm.editSchema in the order they were made.\n"
        "-- msm.prepareRelease moves them into this section of the update script of\n"
        "-- the next release and empties it. Statements end with ; and run inside a\n"
        "-- stored procedure, so no DELIMITER.\n"
        "-- #############################################################################\n"
        "\n\n"
        "-- #############################################################################\n"
        f"-- MSM Section {REMOVALS_SECTION_ID}: Removal of PROCEDUREs and FUNCTIONs\n"
        "-- -----------------------------------------------------------------------------\n"
        "-- The DROPs of PROCEDUREs and FUNCTIONs, which cannot run inside Section 240.\n"
        "-- #############################################################################\n"
        "\n"
    )


def _section_body(section: Optional[dict]) -> str:
    return (section or {}).get("sql_content", "").strip("\n")


def _pending_bodies(text: str) -> dict:
    """The section bodies of a pending update script's text, by section id;
    the routine DROPs of one written before Section 260 from its Section 290."""
    sections = management.get_script_sections(text)
    removals = sections.get(REMOVALS_SECTION_ID) or sections.get(_LEGACY_REMOVALS_SECTION_ID)
    return {
        CHANGES_SECTION_ID: _section_body(sections.get(CHANGES_SECTION_ID)),
        REMOVALS_SECTION_ID: _section_body(removals),
    }


def read_pending_update(path: str) -> dict:
    """The statements of a pending update script, by section ({"240": sql,
    "260": sql}, empty where none)."""
    text = sections_lib.read_if_exists(path)
    if text is None:
        return {CHANGES_SECTION_ID: "", REMOVALS_SECTION_ID: ""}
    return _pending_bodies(text)


def pending_update_text(old: Optional[str], project_settings: dict, statements: dict) -> str:
    """The pending update script with statements appended to its sections.

    Args:
        old: The script's text; None where there is none yet.
        project_settings: The project's msm.project.json, for the notices.
        statements: The SQL to append, by section id; "" or a missing
            section appends nothing. With every section empty and old None,
            the result is the empty script.
    """
    script = old if old is not None else _pending_update_template(project_settings)
    sections = management.get_script_sections(script)
    if REMOVALS_SECTION_ID not in sections:
        # Written before Section 260: made again, its statements kept
        statements = {
            section_id: "\n\n".join(part for part in (body, statements.get(section_id)) if part)
            for section_id, body in _pending_bodies(script).items()
        }
        script = _pending_update_template(project_settings)
        sections = management.get_script_sections(script)
    for section_id, sql in statements.items():
        if not sql:
            continue
        section = sections[section_id]
        body = _section_body(section)
        sections_lib.set_section_body(section, f"{body}\n\n{sql}" if body else sql)
    return "\n".join(item["full_content"] for item in sections.values())


def clear_pending_update(path: str, project_settings: dict) -> bool:
    """Empties a pending update script once a release took its statements.

    Returns:
        Whether it was written.
    """
    old = sections_lib.read_if_exists(path)
    if old is None:
        return False
    return sections_lib.write_if_changed(path, _pending_update_template(project_settings), old)


def move_pending_update(
    schema_project_path: str,
    project_settings: dict,
    update_file_path: str,
    bodies: Optional[dict] = None,
) -> Optional[str]:
    """Moves the statements of the pending update script into the sections
    of a release's update script, and empties the pending script.

    Args:
        schema_project_path: The project folder.
        project_settings: Its settings.
        update_file_path: The release's update script.
        bodies: More section bodies for the update script, by section id
            ({"280": sql}); a None or empty one is left out.

    Returns:
        The pending script, where it was emptied; else None.
    """
    path = pending_update_path(schema_project_path, project_settings)
    pending = read_pending_update(path)
    filled = {
        section_id: sql
        for section_id, sql in [*pending.items(), *(bodies or {}).items()]
        if sql
    }
    if not filled:
        return None
    update_sections = management.get_file_sections(update_file_path)
    for section_id, sql in filled.items():
        sections_lib.set_section_body(update_sections[section_id], sql)
    management.write_sections_to_file(
        file_path=update_file_path, sections=update_sections, overwrite_existing=True
    )
    if not any(pending.values()):
        return None
    return path if clear_pending_update(path, project_settings) else None


# The members of an edit naming the object it is about.
_OBJECT_MEMBERS = ("table", "view", "routine", "trigger", "event", "sequence", "column")


def _describe(edit: dict) -> str:
    """One line naming an edit, for the comment above its statements."""
    names = [
        str(edit[key]) for key in _OBJECT_MEMBERS + ("name",) if isinstance(edit.get(key), str)
    ]
    # The objects add_* and modify_column send, in the outline's shape
    for key in _OBJECT_MEMBERS + ("new_column", "index", "foreign_key"):
        value = edit.get(key)
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            # modify_column names the column twice where it keeps its name
            if not (key == "new_column" and value["name"] == edit.get("column")):
                names.append(value["name"])
    if isinstance(edit.get("new_name"), str):
        names.append(f"-> {edit['new_name']}")
    return " ".join([str(edit.get("op"))] + names)


def _tags(item: dict, sql: str, tagged: bool) -> tuple:
    """A migration statement's (action, object), as the binary tagged it;
    for a binary without tags (whose ops are those of tables, views and
    routines), read from the SQL as far as msm needs them: a CREATE or DROP
    of an object, else (None, None)."""
    if tagged:
        action, kind = item.get("action"), item.get("object")
        if not isinstance(action, str) or not isinstance(kind, str):
            raise ValueError(
                "The language server sent a migration statement without its `action` "
                f"and `object`: {sql}"
            )
        return action, kind
    # A binary that does not tag them (lsp-server #25 to #28)
    created = statements.statement_kind(sql)
    if created:
        return "create", created.lower()
    dropped = statements.dropped_kind(sql)
    return ("drop", dropped.lower()) if dropped else (None, None)


def _placed_statement(item: dict, tagged: bool) -> Optional[tuple]:
    """A migration statement as the pending update script keeps it: its
    (section id, SQL without the delimiter); None for one it does not keep
    (empty, or the CREATE of what Section 150 creates). See _ROUTINES."""
    sql = (item.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return None
    action, kind = _tags(item, sql, tagged)
    if action == "create" and kind in _NOT_KEPT_CREATES:
        return None
    if action == "drop" and kind in _DROPPED_IF_EXISTS:
        sql = _DROP_WITHOUT_IF_EXISTS.sub(r"\1IF EXISTS ", sql, count=1)
        if kind in _ROUTINES:
            return REMOVALS_SECTION_ID, sql
    return CHANGES_SECTION_ID, sql


def migration_statements(edits: list, migration: list, tagged: bool = True) -> tuple:
    """The binary's migration statements as the sections of the pending
    update script: each edit's statements under a comment naming it, placed
    by what they do (see _ROUTINES).

    Args:
        edits: The edits of the request.
        migration: The binary's statements, [{edit, sql, action, object, ...}].
        tagged: Whether the binary tags them (lsp.tags_migration); else
            msm reads what it needs from the SQL.

    Returns:
        ({section id: SQL}, [the statements kept, in order]).
    """
    kept = []
    by_section = {CHANGES_SECTION_ID: [], REMOVALS_SECTION_ID: []}
    described = {CHANGES_SECTION_ID: set(), REMOVALS_SECTION_ID: set()}
    for item in migration:
        placed = _placed_statement(item, tagged)
        if placed is None:
            continue
        section_id, sql = placed
        kept.append(sql)
        # A merged ALTER TABLE (merge_alters) holds several edits' clauses
        indexes = item.get("edits") if isinstance(item.get("edits"), list) else [item.get("edit")]
        for index in indexes:
            if index not in described[section_id]:
                described[section_id].add(index)
                if isinstance(index, int) and 0 <= index < len(edits):
                    by_section[section_id].append(
                        f"-- msm.editSchema: {_describe(edits[index])}")
        by_section[section_id].append(f"{sql};")
    return {section_id: "\n".join(lines) for section_id, lines in by_section.items()}, kept


def online_alter_settings_text(text: Optional[str], edits: list) -> Optional[str]:
    """msm.project.json's text with the `onlineAlter` entries of tables the
    edits rename moved to the new names and those of dropped tables
    removed, in the edits' order; None where nothing changes.
    """
    if text is None:
        return None
    try:
        settings = json.loads(text)
    except json.JSONDecodeError:
        return None
    setting = settings.get(online_alter.SETTING) if isinstance(settings, dict) else None
    tables = setting.get("tables") if isinstance(setting, dict) else None
    if not isinstance(tables, dict) or not tables:
        return None

    def change(tables):
        for edit in edits:
            # Case aside, as the diagrams follow the edits (diagrams.same_name)
            key = next((name for name in tables if diagrams.same_name(name, edit.get("table"))),
                       None)
            if key is None:
                continue
            if edit.get("op") == "rename_table":
                tables[edit.get("new_name")] = tables.pop(key)
            elif edit.get("op") == "drop_table":
                tables.pop(key)

    setting = online_alter.with_tables(setting, change)
    if setting is None:
        return None
    settings[online_alter.SETTING] = setting
    return json.dumps(settings, indent=4)


# -----------------------------------------------------------------------------
# Editing a project


def _diagrams_breakout_path(project) -> str:
    """The breakout file _write may move the diagrams VIEW into."""
    return source.resolve_include_path(modeling.BREAKOUT_FILE, project.development)


def _edited_sections(project, starts: list, includes: set) -> set:
    """The ids of the development script's sections the binary's edits
    write into: at the starts (indexes into the script), or into one of
    the includes (resolved paths of the files a SOURCE line names)."""
    return {
        section_id for section_id, section in project.sections.items()
        if any(section["start_position"] <= start <= section["end_position"] for start in starts)
        or any(
            source.resolve_include_path(path, project.development) in includes
            for _, path in source.directives(section["sql_content"])
        )
    }


def check_base_hash(project, base_hash: str) -> list:
    """Refuses a change made against another snapshot than the project's.

    Checked against the snapshot the client could read, not the script's
    hash: a release moves the version of Section 910, which the snapshot
    does not follow until it is made again.

    Returns:
        The rows of the diagrams VIEW, the snapshot first.

    Raises:
        StaleSchemaError: Where the project has no snapshot, or another one.
    """
    rows = modeling._rows(project)
    snapshot = modeling._schema_row(rows)
    stored = snapshot["document"].get("source_hash") if snapshot else None
    if stored is None:
        raise StaleSchemaError(
            "The project has no schema snapshot yet: make it with "
            "msm.updateSchemaDefinition, read the diagrams and redo the edits."
        )
    if base_hash != stored:
        raise StaleSchemaError(
            "The schema changed since the snapshot the edits were made against "
            f"(`{base_hash}`, now `{stored}`): read the diagrams again and redo them."
        )
    return rows


def edit_result(rows: list, files: list = (), notes: list = (), **members) -> dict:
    """edit_schema's answer: the rows of the diagrams VIEW, the files
    written, the update-script statements kept (none) and the notes, and
    a caller's own members."""
    return {"rows": rows, "files": list(files), "migration": [], "migration_file": None,
            "notes": list(notes), **members}


def without_place(value, members=("range", "selection_range", "source")):
    """An outline object (or list, or value) without the members that say
    where it is written, at every level."""
    if isinstance(value, dict):
        return {key: without_place(member, members) for key, member in value.items()
                if key not in members}
    if isinstance(value, list):
        return [without_place(member, members) for member in value]
    return value


def write_texts(
    project, texts: dict, lsp_path: str = None, outline: dict = None,
    follow_rows: Callable[[list], None] = None, check: Callable = None,
) -> tuple:
    """Writes new texts of a project's files and makes the snapshot again:
    everything, or nothing.

    Args:
        project: The project the texts were worked out from.
        texts: {path: (old, new)}; the development script's old text is
            the completed one (project.script), not the file.
        lsp_path: The mariadb-lsp binary or its folder.
        outline: The binary's outline of the files as they will be written
            (Section 160 blanked, as modeling._outline_input gives it); None
            has the binary run.
        follow_rows: Changes the diagrams VIEW's rows before the snapshot
            is put in them (diagrams following renamed objects).
        check: Called with the project as written, before the snapshot is
            made; returns the outline to make it from, or None. An exception
            puts every file back.

    Returns:
        (the rows of the diagrams VIEW afterwards, the files written, the
        outline the snapshot was made from: the one given or check's, else
        None).
    """
    # The files as they are on disk, to put back.
    originals = {path: sections_lib.read_if_exists(path) for path in texts}
    originals[project.dev_file] = sections_lib.read_if_exists(project.dev_file)
    originals[_diagrams_breakout_path(project)] = sections_lib.read_if_exists(
        _diagrams_breakout_path(project)
    )
    for path in project.files:
        originals.setdefault(path, sections_lib.read_if_exists(path))

    written, touched = [], []
    try:
        for path, (old, new) in texts.items():
            touched.append(path)
            if sections_lib.write_if_changed(path, new, old):
                written.append(path)
        project = modeling._Project.load(project.path)
        if check is not None:
            outline = check(project)
        rows = modeling._rows(project)
        if follow_rows is not None:
            follow_rows(rows)
        _, rows, _ = modeling._replace_snapshot(project, rows, lsp_path, outline)
        # _write may write the script and the VIEW's breakout file.
        touched += [path for path in originals if path not in touched]
        written += [path for path in modeling._write(project, rows) if path not in written]
    except BaseException:
        for path in touched:
            old = originals.get(path)
            if old is None:
                if os.path.exists(path):
                    os.remove(path)
            elif sections_lib.read_if_exists(path) != old:
                with open(path, "w") as f:
                    f.write(old)
        raise
    return rows, written, outline


def _check_request(edits) -> None:
    if not isinstance(edits, list) or not edits:
        raise ValueError("No edits given: pass a list of edits, each with its `op`.")
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or not isinstance(edit.get("op"), str):
            raise ValueError(f"Edit {index + 1} has to be an object with its `op`.")


@changes_project
def edit_schema(
    schema_project_path: str, edits: list, base_hash: str, lsp_path: str = None
) -> dict:
    """Makes schema edits in the project's SQL.

    Args:
        schema_project_path: The project folder.
        edits: The edits, each {op, ...} (lsp-server docs/edit-format.md),
            without `at` and `form`: msm says where a new object's
            statement goes (lib/placement.py).
        base_hash: The source_hash of the snapshot the edits were made
            against; the edits are refused where the project's snapshot is
            another one, made again since.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.

    Returns:
        {rows, files, migration, migration_file, notes}: the rows of the
        diagrams VIEW afterwards (the new snapshot first), the files the
        edits wrote (not those the copyright years were moved in), the statements kept for the next release's update script
        (none before the first release, which creates the schema from the
        script) and the file they went into, and the binary's notes on what
        it could not change itself.

    Raises:
        StaleSchemaError: Where the snapshot is no longer base_hash's.
        LspError: For an add op where the binary cannot take msm's
            placement (before lsp-server #49).
        ValueError: For a request the binary refuses, naming the edit, and
            for an edit that gives `at` or `form`.
    """
    _check_request(edits)
    project = modeling._Project.load(schema_project_path)
    # The binary refuses names that went.
    check_base_hash(project, base_hash)
    return _edit(project, edits, lsp_path)


def _edit(project, edits: list, lsp_path: str = None) -> dict:
    """edit_schema on a project loaded and checked against base_hash."""
    schema_project_path = project.path

    binary = lsp.require_lsp(lsp_path)
    supported = lsp.edit_ops(binary)
    if not supported:
        raise lsp.LspError(
            f"The MariaDB SQL language server's binary `{binary}` cannot edit schemas: "
            f"it has no `{lsp.LSP_BINARY_NAME} edit` command. Update the MariaDB Shell."
        )
    unsupported = sorted({edit["op"] for edit in edits} - supported)
    if unsupported:
        raise lsp.LspError(
            f"The MariaDB SQL language server's binary `{binary}` cannot do "
            + ", ".join(f"`{op}`" for op in unsupported)
            + f" yet; it does {', '.join(sorted(supported))}. A newer MariaDB Shell may."
        )
    # Refused before anything is written, the copyright years included
    placement.check_request(binary, edits)
    # After base_hash was checked: moving the years along gives the snapshot
    # the hash of the script with the new notices. Not undone where the edit
    # fails, as it is a change of its own.
    if management.update_copyright_years(schema_project_path):
        project = modeling._Project.load(schema_project_path)
    script, _ = modeling._outline_input(project)
    schema_name = management.get_effective_schema_name(project.settings)
    # msm says where each new object's statement goes, from an outline of
    # the script as sent (the years moved)
    request = {"format": lsp.EDIT_FORMAT, "schema": schema_name,
               "edits": placement.placed(project, binary, script, edits, schema_name)}
    # One ALTER TABLE per table where the binary can: each one is an online
    # ALTER TABLE of its own in a deployment, maybe a table copy of its own
    if lsp.merges_alters(binary):
        request["merge_alters"] = True
    result = lsp.edit(binary, script, project.development, request)

    # The new texts, worked out before anything is written.
    texts = {}
    # Where the edits go, for the template's examples below.
    starts, includes = [], set()
    section = project.sections.get(modeling.DIAGRAMS_SECTION_ID)
    for changed in result.get("files", []):
        name = changed.get("file")
        if name == "-":
            path, old = project.dev_file, project.script
        else:
            resolved = source.resolve_include_path(name, project.development)
            includes.add(resolved)
            path = source.checked_include_path(resolved)
            old = project.read(path)[1]
        spans = edit_spans(old, changed.get("edits", []))
        if path == project.dev_file:
            starts += [start for start, _, _ in spans]
        if path == project.dev_file and section is not None:
            # The VIEW is msm's to write: an edit there would be lost.
            start = section["start_position"] + section["sql_start"]
            end = section["end_position"]
            if any(
                edit_start < end and edit_end > start
                for edit_start, edit_end, _ in spans
            ):
                raise ValueError(
                    "The language server's edits reach into MSM Section "
                    f"{modeling.DIAGRAMS_SECTION_ID}, which msm writes itself."
                )
        texts[path] = (old, apply_spans(old, spans))

    # A section the edits gave its first statement loses the template's
    # example; the binary's outline then has the lines before it, so the
    # snapshot is made from a new one.
    outline = result.get("outline")
    old, new = texts.get(project.dev_file, (project.script, project.script))
    cleaned = sections_lib.without_template_examples(
        new, _edited_sections(project, starts, includes)
    )
    if cleaned != new:
        texts[project.dev_file] = (old, cleaned)
        outline = None

    migration = result.get("migration", [])
    migration_file, kept = None, []
    if migration and management.get_last_released_version(schema_project_path) is not None:
        migration_file = pending_update_path(schema_project_path, project.settings)
        old = sections_lib.read_if_exists(migration_file)
        by_section, kept = migration_statements(edits, migration, lsp.tags_migration(binary))
        texts[migration_file] = (old, pending_update_text(old, project.settings, by_section))

    # A renamed table keeps its online ALTER TABLE settings, a dropped one's go
    settings_path = os.path.join(schema_project_path, "msm.project.json")
    old_settings = sections_lib.read_if_exists(settings_path)
    new_settings = online_alter_settings_text(old_settings, edits)
    if new_settings is not None:
        texts[settings_path] = (old_settings, new_settings)

    def follow_renames(rows):
        for row in rows:
            if modeling._is_diagram(row):
                for edit in edits:
                    rename_in_diagram(row["document"], edit)

    # The binary's outline of the texts it edited is the outline of the
    # files as written (Section 160 blanked as in the request); an older
    # answer without it has the binary run again.
    rows, written, _ = write_texts(project, texts, lsp_path, outline, follow_renames)

    return {
        "rows": rows,
        "files": written,
        "migration": kept,
        "migration_file": migration_file,
        "notes": result.get("notes", []),
    }


# -----------------------------------------------------------------------------
# Adding a schema object from a template

# The kinds an object can be added of, with msm's templates: the CREATE
# statement a user would write, its names unqualified (MSM scripts name the
# schema in its USE only). string.Template placeholders: ${name} for every
# kind, ${table} for a trigger, ${schema} for any.
OBJECT_TEMPLATES = {
    "view": "CREATE VIEW `${name}` AS\nSELECT 1",
    "procedure": "CREATE PROCEDURE `${name}`()\nBEGIN\n    SELECT 1;\nEND",
    "function": "CREATE FUNCTION `${name}`()\nRETURNS INT\nDETERMINISTIC\nRETURN 1",
    "trigger": (
        "CREATE TRIGGER `${name}`\nBEFORE INSERT ON `${table}`\nFOR EACH ROW\nBEGIN\nEND"
    ),
    "event": "CREATE EVENT `${name}`\nON SCHEDULE EVERY 1 DAY\nDO\nBEGIN\nEND",
    "sequence": "CREATE SEQUENCE `${name}`\nSTART WITH 1\nINCREMENT BY 1",
}
OBJECT_PLACEHOLDERS = {
    "view": ["name", "schema"],
    "procedure": ["name", "schema"],
    "function": ["name", "schema"],
    "trigger": ["name", "table", "schema"],
    "event": ["name", "schema"],
    "sequence": ["name", "schema"],
}


def _object_type(kind: str) -> str:
    """The outline's and the edit ops' name of a kind: routine for a
    procedure or a function (outline `routines`, op `add_routine`)."""
    return "routine" if kind in ("procedure", "function") else kind


def _is_object(kind: str, name: str, item: dict) -> bool:
    """Whether an object of the outline (of the kind's `<type>s`) is the
    kind's object of that name: a routine of the kind, for a routine."""
    return diagrams.same_name(item.get("name") or "", name) and (
        _object_type(kind) != "routine" or item.get("kind") == kind
    )


def get_object_templates() -> dict:
    """msm's templates of the objects a client adds with set_schema_statements.

    Returns:
        {templates: {view, procedure, function, trigger, event, sequence:
        the CREATE statement with string.Template placeholders}, placeholders:
        {kind: the placeholders it may use}}.
    """
    return {
        "templates": dict(OBJECT_TEMPLATES),
        "placeholders": {kind: list(names) for kind, names in OBJECT_PLACEHOLDERS.items()},
    }


def _object_name_problem(name) -> Optional[str]:
    if not isinstance(name, str) or not name:
        return "A name is needed."
    if len(name) > 64 or name.endswith(" ") or "\0" in name or "`" in name:
        return f"`{name}` cannot be a name: at most 64 characters, no backtick, no NUL, no space at its end."
    return None


STATEMENT_KINDS = ("table", "sequence", "view", "procedure", "function", "trigger", "event")
# The kinds a statement replaces where the project has the object: those of
# Section 150, which every deployment runs again
REPLACED_KINDS = _NOT_KEPT_CREATES


def _read_object(binary: str, sql, where: str, expected: dict = None) -> tuple:
    """The object one CREATE statement makes, in the outline's shape the
    edit ops take: (kind, item).

    Refused unless sql is one CREATE of a table, sequence, view, procedure,
    function, trigger or event, named unqualified (an MSM script names its
    schema in its USE only), and, with expected {kind, name, table?}, of
    that kind and name and on that table. Messages start with where.
    """
    expected = expected or {}
    kind_wanted = expected.get("kind")
    one = f"one CREATE {kind_wanted.upper()} statement" if kind_wanted else (
        "one CREATE TABLE, SEQUENCE, VIEW, PROCEDURE, FUNCTION, TRIGGER or EVENT statement"
    )
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError(f"{where} must be {one}.")
    # In a DELIMITER block of its own, so a body with ; parses; the delimiter
    # on a line of its own, so a comment ending the statement does not hide it
    sql = sql.strip().rstrip(";").rstrip()
    outlined = lsp.outline(binary, f"DELIMITER $$\n{sql}\n$$\nDELIMITER ;\n")
    errors = [note for note in outlined.get("notes", []) if note.get("kind") == "syntax-error"]
    found = [
        (item.get("kind") if member == "routines" else member[:-1], item, schema.get("name"))
        for schema in outlined.get("schemas", [])
        for member in diagrams.SCHEMA_OBJECT_KINDS
        for item in schema.get(member, [])
    ]
    if errors or len(found) != 1 or (kind_wanted and found[0][0] != kind_wanted):
        raise ValueError(f"{where} must be {one}"
                         + (f": {errors[0].get('message')}" if errors else "."))
    kind, item, qualifier = found[0][0], dict(found[0][1]), found[0][2]
    if qualifier is not None:
        raise ValueError(
            f"{where} names the schema `{qualifier}`: an MSM script names its objects "
            "unqualified, after its USE."
        )
    name = expected.get("name")
    if name is not None and not diagrams.same_name(item.get("name") or "", name):
        raise ValueError(f"{where} creates `{item.get('name')}`, not `{name}`.")
    table = expected.get("table")
    if kind == "trigger" and table is not None and not diagrams.same_name(
        item.get("table") or "", table
    ):
        raise ValueError(f"{where} is on `{item.get('table')}`, not on `{table}`.")
    return kind, item


def _snapshot_object(snapshot: dict, kind: str, name: str) -> Optional[dict]:
    """The snapshot's object of that kind and name, or None."""
    return next(
        (item for item in snapshot.get(_object_type(kind) + "s", [])
         if _is_object(kind, name, item)),
        None,
    )


def _object_place(development: str, snapshot: dict, kind: str, name: str) -> tuple:
    """(file, line (1-based)) where the snapshot's object starts; Nones
    where it has none."""
    item = _snapshot_object(snapshot, kind, name)
    if item is None or "range" not in item:
        return None, None
    # The snapshot's files are relative to the development folder; an
    # object's source n is files[n + 1], one without it is in files[0]
    files = snapshot.get("files", [])
    source = item.get("source")
    relative = files[source + 1] if isinstance(source, int) else (files[0] if files else None)
    file = os.path.normpath(os.path.join(development, relative)) if relative is not None else None
    return file, item["range"]["start"]["line"] + 1


def _check_kind_and_name(kind, name, kinds) -> None:
    """Refuses a kind not among kinds, and a name no object can have."""
    if kind not in kinds:
        raise ValueError(f"Unknown kind `{kind}`; the kinds are {', '.join(kinds)}.")
    problem = _object_name_problem(name)
    if problem:
        raise ValueError(problem)


def _check_expect(expect, count: int) -> list:
    """expect as a list of {kind, name, table?} dicts, one per statement, or Nones."""
    if expect is None:
        return [None] * count
    if not isinstance(expect, list) or len(expect) != count:
        raise ValueError("expect has to hold one {kind, name, table?} per statement.")
    checked = []
    for index, wanted in enumerate(expect):
        if not hasattr(wanted, "keys") or wanted.get("kind") not in STATEMENT_KINDS:
            raise ValueError(
                f"expect {index + 1} has to name its kind: one of {', '.join(STATEMENT_KINDS)}."
            )
        problem = _object_name_problem(wanted.get("name"))
        if problem:
            raise ValueError(f"expect {index + 1}: {problem}")
        checked.append(dict(wanted))
    return checked


@changes_project
def set_schema_statements(
    schema_project_path: str,
    statements: list,
    base_hash: str,
    expect: list = None,
    lsp_path: str = None,
) -> dict:
    """Puts CREATE statements of schema objects into a project, where msm
    keeps them.

    Each statement is read by mariadb-lsp and becomes an edit of
    edit_schema: an object the project lacks is added (add_table,
    add_sequence, add_view, add_routine, add_trigger, add_event: a table or
    sequence in Section 140, the others in Section 150, written to run
    again, next to the objects of its kind, in a breakout file too:
    lib/placement.py). A view,
    routine, trigger or event the project has is replaced where it stands
    (replace_*), the form it is written in kept; one whose definition is
    the snapshot's already is left as it is. A table or sequence the
    project has is refused: msm.editSchema changes its columns, keys and
    options, which a deployed server needs ALTER statements for. All
    statements are one edit: everything is written, or nothing.

    Args:
        schema_project_path: The project folder.
        statements: The CREATE statements, one object each, named
            unqualified; the form they are written in does not matter.
        base_hash: The source_hash of the snapshot the caller knows, as for
            edit_schema.
        expect: One {kind, name, table?} per statement: refused unless the
            statement creates that object (a trigger on that table).
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        edit_schema's result, and statements: [{kind, name, file, line
        (1-based), replaced (the project had the object), changed (written;
        False for an unchanged definition)}].

    Raises:
        StaleSchemaError: For a stale base_hash.
        LspError: For a binary that cannot take the outline's objects back
            (before lsp-server #48).
        ValueError: For a statement that is not one CREATE of an object
            named unqualified, one that is not what expect names, two of one
            object, a table or sequence the project has, and what edit_schema
            refuses (a name taken, an unknown table).
    """
    if not isinstance(statements, list) or not statements:
        raise ValueError("No statements given: pass a list of CREATE statements.")
    expect = _check_expect(expect, len(statements))

    def where(index):
        return f"Statement {index + 1}"

    project = modeling._Project.load(schema_project_path)
    rows = check_base_hash(project, base_hash)
    binary = lsp.require_lsp(lsp_path)
    if not lsp.edits_take_outline_objects(binary):
        raise lsp.LspError(
            f"The MariaDB SQL language server's binary `{binary}` cannot take schema objects "
            "back as its outline writes them (lsp-server #48). Update "
            f"{lsp.LSP_BINARY_NAME}, or the MariaDB Shell, which comes with it."
        )
    read = [
        _read_object(binary, sql, where(index), wanted)
        for index, (sql, wanted) in enumerate(zip(statements, expect))
    ]
    keys = [(kind, (item.get("name") or "").lower()) for kind, item in read]
    if len(set(keys)) != len(keys):
        raise ValueError("Two statements create the same object: pass one statement each.")

    snapshot = modeling._schema_row(rows)["document"]
    edits, entries = [], []
    for index, (kind, item) in enumerate(read):
        name = item.get("name")
        existing = _snapshot_object(snapshot, kind, name)
        object_type = _object_type(kind)
        # A replace never renames, not even the case: the project's spelling stays
        entry = {"kind": kind, "name": existing["name"] if existing else name,
                 "replaced": existing is not None, "changed": True}
        entries.append(entry)
        if existing is not None and kind not in REPLACED_KINDS:
            raise ValueError(
                f"{where(index)}: the project has the {kind} `{name}` already. Change its "
                "columns, keys and options with msm.editSchema, which writes the ALTER "
                "statements a deployed server needs."
            )
        # Sent as the outline has it; a replace keeps the script's spelling
        op = "replace" if existing is not None else "add"
        edits.append({"op": f"{op}_{object_type}", object_type: item})

    # Sent in the order the kinds run (a function before the view that
    # calls it, a sequence before the table whose default takes from it),
    # as adds at one place keep the request's order; statements of one kind
    # keep theirs. The binary's indexes are taken back to the statements'.
    order = sorted(range(len(edits)), key=lambda index: placement.run_rank(edits[index]["op"]))
    result = _renumbered(_edit(project, [edits[index] for index in order], lsp_path), order)
    # A replace that changes nothing writes nothing, and the binary says so
    for note in result.get("notes", []):
        if note.get("kind") == "unchanged" and isinstance(note.get("edit"), int):
            entries[note["edit"]]["changed"] = False
    snapshot = modeling._schema_row(result["rows"])["document"]
    for entry in entries:
        entry["file"], entry["line"] = _object_place(
            project.development, snapshot, entry["kind"], entry["name"]
        )
    return {**result, "statements": entries}


def _renumbered(result: dict, order: list) -> dict:
    """edit_schema's result for edits sent in that order (order[sent] =
    the index given), its notes' edit indexes the given ones."""
    notes = [
        {**note, "edit": order[note["edit"]]} if isinstance(note.get("edit"), int) else note
        for note in result.get("notes", [])
    ]
    return {**result, "notes": notes}


@changes_project
def drop_schema_object(
    schema_project_path: str, kind: str, name: str, base_hash: str, lsp_path: str = None
) -> dict:
    """Drops a table, sequence, view, procedure, function, trigger or event
    from a project: edit_schema's drop_table, drop_sequence, drop_view,
    drop_routine, drop_trigger or drop_event, which remove its statements;
    once the project has a release, the DROP is kept for the next update
    script.

    Args:
        schema_project_path: The project folder.
        kind: table, sequence, view, procedure, function, trigger or event.
        name: The object's name.
        base_hash: The source_hash of the snapshot the caller knows.
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        edit_schema's result.
    """
    _check_kind_and_name(kind, name, STATEMENT_KINDS)
    object_type = _object_type(kind)
    edit = {"op": f"drop_{object_type}", object_type: name}
    if object_type == "routine":
        edit["kind"] = kind
    return edit_schema(schema_project_path, [edit], base_hash, lsp_path)
