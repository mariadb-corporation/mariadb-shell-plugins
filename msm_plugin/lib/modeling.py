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

"""The schema diagrams of a project: MSM Section 160 of its development script.

The section holds the diagrams VIEW (see lib/diagrams.py), inline or, once it
is larger than the project's maxInlineSectionSize, in a breakout file that a
SOURCE directive includes. Both stay supported: a project may keep its whole
script in one file.
"""

import os
import re
from pathlib import Path
from typing import Optional

from msm_plugin.lib import diagrams, lsp, management, source

DIAGRAMS_SECTION_ID = "160"
BREAKOUT_FILE = "./sections/160_schema_diagrams.sql"

# A section body larger than this is kept in a breakout file, unless the
# project's msm.project.json sets maxInlineSectionSize.
DEFAULT_MAX_INLINE_SECTION_SIZE = 16 * 1024

# Every file of a SOURCE directive is read through source.read_include, so a
# caller that has to check them sets source.checked_includes() around the call.


def get_max_inline_section_size(project_settings: dict) -> int:
    """The largest section body kept inline in the development script."""
    value = project_settings.get("maxInlineSectionSize", DEFAULT_MAX_INLINE_SECTION_SIZE)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "The project setting maxInlineSectionSize has to be a number of "
            f"characters, not `{value}`."
        )
    return value


def _development_folder(schema_project_path: str) -> str:
    return os.path.join(schema_project_path, "development")


def ensure_diagrams_section(sections: dict) -> dict:
    """Returns the sections with Section 160, added after 150 where missing.

    Projects created before the section existed do not have it.
    """
    if DIAGRAMS_SECTION_ID in sections:
        return sections
    template = os.path.join(
        Path(__file__).parent.parent, "templates", "scripts", "schema_next.sql"
    )
    with open(template, "r") as f:
        added = management.get_script_sections(f.read())[DIAGRAMS_SECTION_ID]
    ids = list(sections)
    position = next(
        (index for index, section_id in enumerate(ids)
         if section_id.isdigit() and int(section_id) > int(DIAGRAMS_SECTION_ID)),
        len(ids),
    )
    ids.insert(position, DIAGRAMS_SECTION_ID)
    return {
        section_id: added if section_id == DIAGRAMS_SECTION_ID else sections[section_id]
        for section_id in ids
    }


def _section_directive(sql_content: str) -> Optional[tuple]:
    """The (path, start, end) of the one SOURCE directive of a section body, or None."""
    found = source.directive_slices(sql_content)
    if not found:
        return None
    if len(found) > 1:
        raise ValueError(
            f"MSM Section {DIAGRAMS_SECTION_ID} may include one file only, it has "
            f"{len(found)} SOURCE directives."
        )
    return found[0][1:]


def read_diagram_rows(schema_project_path: str) -> list:
    """Returns the rows of the project's diagrams VIEW.

    Returns:
        The rows, each {name, format, document}; none where the section is
        missing or empty.
    """
    sections = management.get_schema_development_sections(schema_project_path)
    section = sections.get(DIAGRAMS_SECTION_ID)
    if section is None:
        return []
    content = section["sql_content"]
    if source.contains_source_directive(content):
        content = source.expand_source_directives(
            content, _development_folder(schema_project_path)
        )[0]
    return diagrams.parse_view_sql(content)


def _breakout_header(project_settings: dict) -> str:
    """The first lines of a new breakout file: the project's notices."""
    notices = management.render_copyright_notices(project_settings, prefix="-- ")
    return (
        f"{notices}\n"
        "-- -----------------------------------------------------------------------------\n"
        f"-- MSM Section {DIAGRAMS_SECTION_ID}: Schema Diagrams, written by msm\n"
    )


def _write_if_changed(path: str, text: str) -> bool:
    """Writes a file unless it holds the text already; says whether it wrote."""
    try:
        with open(path, "r") as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return True


def write_diagram_rows(schema_project_path: str, rows: list) -> list:
    """Writes the rows of the project's diagrams VIEW into Section 160.

    A section that includes a file keeps doing so: the file's part the
    directive selects is replaced, its header and footer stay. An inline
    section stays inline while the VIEW fits maxInlineSectionSize; a larger
    one moves into the breakout file sections/160_schema_diagrams.sql. A file
    that would not change is not written.

    Returns:
        The files written.
    """
    project_settings = management.get_project_settings(schema_project_path)
    development = _development_folder(schema_project_path)
    existing = management.get_schema_development_sections(schema_project_path)
    sections = ensure_diagrams_section(existing)
    section = sections[DIAGRAMS_SECTION_ID]
    view_sql = diagrams.render_view_sql(rows)
    written = []

    included = _section_directive(section["sql_content"])
    if included is None and len(view_sql) > get_max_inline_section_size(project_settings):
        header = _breakout_header(project_settings)
        included = (BREAKOUT_FILE, f"ln{header.count(chr(10)) + 1}", "")
        before, after = header, ""
        body = f"SOURCE '{BREAKOUT_FILE}'[{included[1]}:]; -- Without the header"
    elif included is not None:
        before = after = ""
        body = None
    else:
        body = view_sql

    if included is not None:
        path = source.checked_include_path(
            source.resolve_include_path(included[0], development)
        )
        if body is None:
            # The parts of the file outside the directive's slice stay.
            try:
                with open(path, "r") as f:
                    old = f.read()
            except FileNotFoundError:
                old = ""
            start = source.get_source_slice_offset(old, included[1], False) or 0
            end = source.get_source_slice_offset(old, included[2], True)
            before, after = old[:start], "" if end is None else old[end:]
        if _write_if_changed(path, before + view_sql + "\n" + after):
            written.append(path)

    if body is not None:
        # A blank line after the banner and two before the next one, as the
        # templates have them; set_sql_content strips both.
        section["full_content"] = (
            section["full_content"][: section["sql_start"]] + "\n\n" + body + "\n\n"
        )
    if body is not None or sections is not existing:
        dev_file = management.get_schema_development_file_path(schema_project_path)
        script = "\n".join(item["full_content"] for item in sections.values())
        if _write_if_changed(dev_file, script):
            written.insert(0, dev_file)
    return written


def outline_input(schema_project_path: str) -> tuple:
    """The development script as the language server reads it for the snapshot.

    The snapshot must not describe the VIEW that holds it, so Section 160 is
    left out: blanked for the outline, its lines kept so that positions stay
    those of the file, and removed for the hash, which then does not change
    when only the diagrams do.

    Returns:
        The script for the outline, the script for the hash, and the script's
        file name.
    """
    dev_file = management.get_schema_development_file_path(schema_project_path)
    with open(dev_file, "r") as f:
        script = f.read()
    hashed = script
    section = management.get_script_sections(script).get(DIAGRAMS_SECTION_ID)
    if section is not None:
        start = section["start_position"] + section["sql_start"]
        end = section["end_position"]
        hashed = script[:start] + script[end:]
        script = script[:start] + "\n" * script.count("\n", start, end) + script[end:]
    return script, hashed, os.path.basename(dev_file)


def _source_hash(schema_project_path: str, hashed: str) -> str:
    """The hash of the script without Section 160, its SOURCE files expanded."""
    expanded = source.expand_source_directives(
        hashed, _development_folder(schema_project_path)
    )[0]
    return diagrams.source_hash(expanded)


def build_schema_definition(schema_project_path: str, lsp_path: str = None) -> dict:
    """Makes the msm-schema/1 snapshot of the project's development script.

    Args:
        schema_project_path: The project folder.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.

    Returns:
        The snapshot document.

    Raises:
        ValueError: Without the binary, for an include that is refused, or
            when a statement could not be read: the snapshot would miss what
            it defines.
    """
    binary = lsp.require_lsp(lsp_path)
    project_settings = management.get_project_settings(schema_project_path)
    development = _development_folder(schema_project_path)
    script, hashed, script_name = outline_input(schema_project_path)

    # msm's reading of the directives decides what is included (and checked),
    # and the binary must not find others: it also ends a line at a lone "\r".
    if re.search(r"\r(?!\n)", script):
        raise ValueError(
            f"`{script_name}` has a line ending in a lone carriage return; "
            "save it with \\n or \\r\\n line endings."
        )
    # Expanded first, so every include is checked before the binary reads it.
    hash_value = _source_hash(schema_project_path, hashed)

    outline = lsp.outline(binary, script, development)
    unread = diagrams.unread_statements(outline)
    if unread:
        details = "; ".join(
            f"line {note['start']['line'] + 1}: {note['message']}" for note in unread[:10]
        )
        raise ValueError(
            f"{len(unread)} statement(s) of `{script_name}` could not be read, so the "
            f"schema snapshot would miss what they define: {details}"
        )

    files = [script_name] + [
        os.path.relpath(include["file"], development).replace(os.sep, "/")
        for include in outline.get("includes", [])
    ]
    schema_name = management.get_effective_schema_name(project_settings)
    return diagrams.schema_document_from_outline(outline, schema_name, files, hash_value)


def current_source_hash(schema_project_path: str) -> str:
    """The hash the snapshot of the development script as it is now would have."""
    return _source_hash(schema_project_path, outline_input(schema_project_path)[1])


def _is_diagram(row: dict, name: str = None) -> bool:
    return row["format"] == diagrams.DIAGRAM_FORMAT and (name is None or row["name"] == name)


def update_schema_definition(schema_project_path: str, lsp_path: str = None) -> dict:
    """Makes the snapshot again and writes it into the diagrams VIEW.

    Diagrams lose the figures and connections of objects the schema no longer
    has.

    Returns:
        {tables, views, routines, triggers, events, sequences} counts, the
        diagrams that changed and the files written.
    """
    document = build_schema_definition(schema_project_path, lsp_path)
    rows = [
        row for row in read_diagram_rows(schema_project_path)
        if row["format"] != diagrams.SCHEMA_FORMAT
    ]
    changed = [
        row["name"] for row in rows
        if _is_diagram(row) and diagrams.reconcile_diagram(row["document"], document)
    ]
    rows.insert(
        0,
        {"name": diagrams.SCHEMA_ROW_NAME, "format": diagrams.SCHEMA_FORMAT, "document": document},
    )
    result = {kind: len(document[kind]) for kind in diagrams.SCHEMA_OBJECT_KINDS}
    result["changed_diagrams"] = changed
    result["files"] = write_diagram_rows(schema_project_path, rows)
    return result


def list_diagrams(schema_project_path: str) -> dict:
    """Says what the project's diagrams VIEW holds.

    Returns:
        {schema_definition: {tables, current} or None, diagrams: [{name,
        figures, connections}]}; current says whether the snapshot was made
        from the development script as it is now.
    """
    rows = read_diagram_rows(schema_project_path)
    schema = next((row for row in rows if row["format"] == diagrams.SCHEMA_FORMAT), None)
    info = None
    if schema is not None:
        info = {
            "tables": len(schema["document"].get("tables", [])),
            "current": schema["document"].get("source_hash")
            == current_source_hash(schema_project_path),
        }
    return {
        "schema_definition": info,
        "diagrams": [
            {
                "name": row["name"],
                "figures": len(row["document"].get("figures", [])),
                "connections": len(row["document"].get("connections", [])),
            }
            for row in rows
            if _is_diagram(row)
        ],
    }


def get_diagram_rows(schema_project_path: str, name: str = None) -> list:
    """Returns the rows of the diagrams VIEW: all, or the schema row and one diagram.

    Raises:
        ValueError: For a diagram name the VIEW does not hold.
    """
    rows = read_diagram_rows(schema_project_path)
    if name is None:
        return rows
    found = [row for row in rows if _is_diagram(row, name)]
    if not found:
        raise ValueError(f"The project has no diagram `{name}`.")
    return [row for row in rows if row["format"] == diagrams.SCHEMA_FORMAT] + found


def set_diagram(schema_project_path: str, document: dict) -> list:
    """Adds a diagram to the VIEW, or replaces the one of the same name.

    Returns:
        The files written.
    """
    diagrams.validate_diagram(document)
    rows = read_diagram_rows(schema_project_path)
    row = {"name": document["name"], "format": diagrams.DIAGRAM_FORMAT, "document": document}
    index = next(
        (index for index, existing in enumerate(rows) if _is_diagram(existing, document["name"])),
        None,
    )
    if index is None:
        rows.append(row)
    else:
        rows[index] = row
    return write_diagram_rows(schema_project_path, rows)


def remove_diagram(schema_project_path: str, name: str) -> list:
    """Removes a diagram from the VIEW.

    Returns:
        The files written.
    """
    rows = read_diagram_rows(schema_project_path)
    kept = [row for row in rows if not _is_diagram(row, name)]
    if len(kept) == len(rows):
        raise ValueError(f"The project has no diagram `{name}`.")
    return write_diagram_rows(schema_project_path, kept)
