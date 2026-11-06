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

import os
import re
from typing import Optional

from msm_plugin.lib import diagrams, lsp, management, modeling, source
from msm_plugin.lib import sections as sections_lib

# The sections of the pending update script, as in the update script.
CHANGES_SECTION_ID = "240"
REMOVALS_SECTION_ID = "290"

# A statement that cannot run inside the update procedure of Section 240
# (MariaDB 1357: a stored routine is not dropped from within another).
_ROUTINE_DROP = re.compile(r"^\s*DROP\s+(?:PROCEDURE|FUNCTION)\b", re.IGNORECASE)
# A routine's DROP without IF EXISTS. Section 290 runs after the target's
# Section 150, which may have dropped the routine already (a DROP ... IF
# EXISTS of the old name left in the script), so the DROP must not fail on a
# routine that is gone (MariaDB 1305).
_ROUTINE_DROP_WITHOUT_IF_EXISTS = re.compile(
    r"^(\s*DROP\s+(?:PROCEDURE|FUNCTION)\s+)(?!IF\s+EXISTS\b)", re.IGNORECASE
)
# A routine's CREATE (rename_routine drops the routine and creates it again):
# it cannot run inside the update procedure either (MariaDB 1303), and its
# body would need a DELIMITER. Section 150 of the release, which every
# deployment runs in full, creates the routine under its new name, so it is
# not kept.
_ROUTINE_CREATE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?"
    r"(?:AGGREGATE\s+)?(?:PROCEDURE|FUNCTION)\b",
    re.IGNORECASE,
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


# The rename edits a diagram follows: the figure type showing the object,
# and the edit's member naming it.
_RENAMES = {
    "rename_table": (diagrams.TABLE_FIGURE, "table"),
    "rename_view": (diagrams.VIEW_FIGURE, "view"),
    "rename_routine": (diagrams.ROUTINE_GROUP_FIGURE, "routine"),
}


def rename_in_diagram(diagram: dict, edit: dict) -> bool:
    """Makes a diagram follow an edit that renames a table, a view or a
    routine: its figures and connections name objects, and would otherwise
    be dropped as showing what the schema no longer has.

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
    changed = False
    if figure_type == diagrams.ROUTINE_GROUP_FIGURE:
        for figure in figures:
            routines = diagrams.figure_routines(figure)
            if any(same(name, old) for name in routines):
                figure["routines"] = [new_name if same(name, old) else name for name in routines]
                changed = True
        return changed

    for figure in figures:
        if same(diagrams.figure_object_name(figure), old):
            figure[name_key] = new_name
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


def _describe(edit: dict) -> str:
    """One line naming an edit, for the comment above its statements."""
    names = [
        str(edit[key]) for key in ("table", "view", "routine", "column", "name")
        if isinstance(edit.get(key), str)
    ]
    for key in ("table", "column", "index", "foreign_key"):
        value = edit.get(key)
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            names.append(value["name"])
    if isinstance(edit.get("new_name"), str):
        names.append(f"-> {edit['new_name']}")
    return " ".join([str(edit.get("op"))] + names)


def _kept_statement(item: dict) -> Optional[str]:
    """A migration statement as the pending update script keeps it, without
    its delimiter, a routine's DROP with IF EXISTS; None for one it does not
    keep (empty, or a routine's CREATE)."""
    sql = (item.get("sql") or "").strip().rstrip(";").strip()
    if not sql or _ROUTINE_CREATE.match(sql):
        return None
    return _ROUTINE_DROP_WITHOUT_IF_EXISTS.sub(r"\1IF EXISTS ", sql, count=1)


def migration_statements(edits: list, migration: list) -> dict:
    """The binary's migration statements as the sections of the pending
    update script: each edit's statements under a comment naming it, a
    routine's DROP in Section 290, its CREATE left to Section 150 (see
    _ROUTINE_CREATE), everything else in 240."""
    by_section = {CHANGES_SECTION_ID: [], REMOVALS_SECTION_ID: []}
    described = {CHANGES_SECTION_ID: set(), REMOVALS_SECTION_ID: set()}
    for item in migration:
        sql = _kept_statement(item)
        if sql is None:
            continue
        section_id = REMOVALS_SECTION_ID if _ROUTINE_DROP.match(sql) else CHANGES_SECTION_ID
        index = item.get("edit")
        if index not in described[section_id]:
            described[section_id].add(index)
            if isinstance(index, int) and 0 <= index < len(edits):
                by_section[section_id].append(f"-- msm.editSchema: {_describe(edits[index])}")
        by_section[section_id].append(f"{sql};")
    return {section_id: "\n".join(lines) for section_id, lines in by_section.items()}


# -----------------------------------------------------------------------------
# Editing a project


def _diagrams_breakout_path(project) -> str:
    """The breakout file _write may move the diagrams VIEW into."""
    return source.resolve_include_path(modeling.BREAKOUT_FILE, project.development)


def _check_request(edits) -> None:
    if not isinstance(edits, list) or not edits:
        raise ValueError("No edits given: pass a list of edits, each with its `op`.")
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or not isinstance(edit.get("op"), str):
            raise ValueError(f"Edit {index + 1} has to be an object with its `op`.")


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
    if not lsp.supports_edit(binary):
        raise lsp.LspError(
            f"The MariaDB SQL language server's binary `{binary}` cannot edit schemas: "
            f"it has no `{lsp.LSP_BINARY_NAME} edit` command. Update the MariaDB Shell."
        )
    script, _ = modeling._outline_input(project)
    schema_name = management.get_effective_schema_name(project.settings)
    result = lsp.edit(
        binary,
        script,
        project.development,
        {"format": lsp.EDIT_FORMAT, "schema": schema_name, "edits": edits},
    )

    # The new texts, worked out before anything is written.
    texts = {}
    section = project.sections.get(modeling.DIAGRAMS_SECTION_ID)
    for changed in result.get("files", []):
        name = changed.get("file")
        if name == "-":
            path, old = project.dev_file, project.script
        else:
            path = source.checked_include_path(
                source.resolve_include_path(name, project.development)
            )
            old = project.read(path)[1]
        spans = edit_spans(old, changed.get("edits", []))
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

    migration = result.get("migration", [])
    migration_file = None
    if migration and management.get_last_released_version(schema_project_path) is not None:
        migration_file = pending_update_path(schema_project_path, project.settings)
        old = sections_lib.read_if_exists(migration_file)
        texts[migration_file] = (
            old,
            pending_update_text(old, project.settings, migration_statements(edits, migration)),
        )

    # The files as they are on disk, to put back: the development script's
    # old text above is the completed one (sections msm adds), not the file.
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
        project = modeling._Project.load(schema_project_path)
        rows = modeling._rows(project)
        for row in rows:
            if modeling._is_diagram(row):
                for edit in edits:
                    rename_in_diagram(row["document"], edit)
        # The binary's outline of the texts it edited is the outline of the
        # files as now written (Section 160 blanked as in the request); an
        # older answer without it has the binary run again.
        outline = result.get("outline")
        _, rows, _ = modeling._replace_snapshot(
            project, rows, lsp_path, outline if isinstance(outline, dict) and outline else None
        )
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
        "files": written,
        "migration": [
            sql for sql in map(_kept_statement, migration) if sql is not None
        ] if migration_file else [],
        "migration_file": migration_file,
        "notes": result.get("notes", []),
    }
