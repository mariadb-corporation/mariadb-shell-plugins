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
  ``development/<schema>_update_next.sql``, whose Sections 240 and 290
  prepare_release moves into the update script of the next release.

Everything is written, or nothing: a failure after the first file was
written puts every file back.
"""

import json
import os
import re
from typing import Optional

from msm_plugin.lib import diagrams, lsp, management, modeling, online_alter, source, statements
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib.project_changes import changes_project

# The sections of the pending update script, as in the update script.
CHANGES_SECTION_ID = "240"
REMOVALS_SECTION_ID = "290"

# What a migration statement does, as the binary tags it (action, object):
# - the CREATE of a view, routine, trigger or event is not kept. Section 150
#   of the release, which every deployment runs in full after the 240
#   steps, creates it (add_view, add_routine, add_trigger, add_event, the
#   second statement of rename_routine and rename_trigger), and none but a
#   view's CREATE can run inside the update procedure (MariaDB 1303 for a
#   routine or trigger, 1576 for an event);
# - the DROP of a routine goes in Section 290: a stored routine is not
#   dropped from within another (MariaDB 1357);
# - the DROP of a table, sequence, view, routine, trigger or event is kept
#   with IF EXISTS, as the binary writes it without: Section 150 runs before
#   290 and may have dropped the routine already (MariaDB 1305);
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


def _offset(text: str, line_starts: list, position: dict) -> int:
    """The index in text of an LSP position (0-based line, UTF-16 character)."""
    line, character = position["line"], position["character"]
    if not 0 <= line < len(line_starts):
        raise ValueError(f"The language server named line {line + 1}, which the file lacks.")
    index = line_starts[line]
    units = 0
    while units < character and index < len(text) and text[index] != "\n":
        units += 2 if ord(text[index]) > 0xFFFF else 1
        index += 1
    if units != character:
        raise ValueError(
            f"The language server named character {character} of line {line + 1}, "
            "which the line lacks."
        )
    return index


def edit_spans(text: str, edits: list) -> list:
    """LSP text edits ({range, new_text}) as (start, end, new_text) indexes
    into the text, in order.

    Raises:
        ValueError: For a position the text lacks, or edits that overlap.
    """
    line_starts = [0] + [match.end() for match in re.finditer("\n", text)]
    # By position only, and stable: inserts at one position keep the order
    # the server gave them in, as LSP has it.
    spans = sorted(
        (
            (
                _offset(text, line_starts, edit["range"]["start"]),
                _offset(text, line_starts, edit["range"]["end"]),
                edit["new_text"],
            )
            for edit in edits
        ),
        key=lambda span: span[:2],
    )
    at = 0
    for start, end, _ in spans:
        if start < at or end < start:
            raise ValueError("The language server's text edits overlap.")
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
    routine, an event or a sequence: its figures and connections name
    objects, and would otherwise be dropped as showing what the schema no
    longer has. A renamed trigger needs nothing: the table card lists the
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
    if op == "rename_table":
        for connection in diagram.get("connections", []):
            if same(connection.get("table"), old):
                connection["table"] = new_name
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
        f"-- MSM Section {CHANGES_SECTION_ID}: Non-idempotent Schema Object Changes and All DROPs\n"
        "-- -----------------------------------------------------------------------------\n"
        "-- The changes made to the TABLEs of the development script since the last\n"
        "-- release, written by msm.editSchema in the order they were made.\n"
        "-- msm.prepareRelease moves them into this section of the update script of\n"
        "-- the next release and empties it. Statements end with ; and run inside a\n"
        "-- stored procedure, so no DELIMITER.\n"
        "-- #############################################################################\n"
        "\n\n"
        "-- #############################################################################\n"
        f"-- MSM Section {REMOVALS_SECTION_ID}: Removal of Update Helpers\n"
        "-- -----------------------------------------------------------------------------\n"
        "-- The DROPs of PROCEDUREs and FUNCTIONs, which cannot run inside Section 240.\n"
        "-- #############################################################################\n"
        "\n"
    )


def _section_body(section: Optional[dict]) -> str:
    return (section or {}).get("sql_content", "").strip("\n")


def read_pending_update(path: str) -> dict:
    """The statements of a pending update script, by section ({"240": sql,
    "290": sql}, empty where none)."""
    text = sections_lib.read_if_exists(path)
    if text is None:
        return {CHANGES_SECTION_ID: "", REMOVALS_SECTION_ID: ""}
    sections = management.get_script_sections(text)
    return {
        section_id: _section_body(sections.get(section_id))
        for section_id in (CHANGES_SECTION_ID, REMOVALS_SECTION_ID)
    }


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
    schema_project_path: str, project_settings: dict, update_file_path: str
) -> Optional[str]:
    """Moves the statements of the pending update script into the sections
    of a release's update script, and empties the pending script.

    Returns:
        The pending script, where it was emptied; else None.
    """
    path = pending_update_path(schema_project_path, project_settings)
    pending = read_pending_update(path)
    if not any(pending.values()):
        return None
    update_sections = management.get_file_sections(update_file_path)
    for section_id, sql in pending.items():
        if sql:
            sections_lib.set_section_body(update_sections[section_id], sql)
    management.write_sections_to_file(
        file_path=update_file_path, sections=update_sections, overwrite_existing=True
    )
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
        edits: The edits, each {op, ...} (lsp-server docs/edit-format.md).
        base_hash: The source_hash of the snapshot the edits were made
            against; the edits are refused where the project's snapshot is
            another one, made again since.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.

    Returns:
        {rows, files, migration, migration_file, notes}: the rows of the
        diagrams VIEW afterwards (the new snapshot first), the files
        written, the statements kept for the next release's update script
        (none before the first release, which creates the schema from the
        script) and the file they went into, and the binary's notes on what
        it could not change itself.

    Raises:
        StaleSchemaError: Where the snapshot is no longer base_hash's.
        ValueError: For a request the binary refuses, naming the edit.
    """
    _check_request(edits)
    project = modeling._Project.load(schema_project_path)
    # Against the snapshot the client could read, not the script's hash: a
    # release moves the version of Section 910, which the snapshot does not
    # follow until it is made again. The binary refuses names that went.
    snapshot = next(
        (row["document"] for row in modeling._rows(project)
         if row["format"] == diagrams.SCHEMA_FORMAT),
        {},
    )
    stored = snapshot.get("source_hash")
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
    # After base_hash was checked: moving the years along gives the snapshot
    # the hash of the script with the new notices. Not undone where the edit
    # fails, as it is a change of its own.
    bumped = management.update_copyright_years(schema_project_path)
    if bumped:
        project = modeling._Project.load(schema_project_path)
    script, _ = modeling._outline_input(project)
    schema_name = management.get_effective_schema_name(project.settings)
    request = {"format": lsp.EDIT_FORMAT, "schema": schema_name, "edits": edits}
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

    # The files as they are on disk, to put back: the development script's
    # old text above is the completed one (sections msm adds), not the file.
    originals = {path: old if path == settings_path else sections_lib.read_if_exists(path)
                 for path, (old, _) in texts.items()}
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
        project = modeling._Project.load(schema_project_path)
        rows = modeling._rows(project)
        for row in rows:
            if modeling._is_diagram(row):
                for edit in edits:
                    rename_in_diagram(row["document"], edit)
        # The binary's outline of the texts it edited is the outline of the
        # files as now written (Section 160 blanked as in the request); an
        # older answer without it has the binary run again.
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

    return {
        "rows": rows,
        # The files the years were moved along in, too
        "files": bumped + [path for path in written if path not in bumped],
        "migration": kept,
        "migration_file": migration_file,
        "notes": result.get("notes", []),
    }
