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
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from msm_plugin.lib import diagrams, layout, lsp, management, source
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib import workbench
from msm_plugin.lib.project_changes import changes_project

DIAGRAMS_SECTION_ID = "160"
BREAKOUT_FILE_NAME = "160_schema_diagrams.sql"
BREAKOUT_FILE = f"./{sections_lib.BREAKOUT_FOLDER}/{BREAKOUT_FILE_NAME}"

# Every file of a SOURCE directive is read through source.read_include, so a
# caller that has to check them sets source.checked_includes() around the call.


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


@dataclass
class _Project:
    """A project's settings and development script, read once per operation.

    Attributes:
        path: The project folder.
        settings: Its msm.project.json.
        dev_file: The development script's path.
        script: The script's text as read.
        sections: Its MSM sections.
        files: The text of every file a SOURCE directive was read from, by
            the path read, so a file is read once.
    """

    path: str
    settings: dict
    dev_file: str
    script: str
    sections: dict
    files: dict = field(default_factory=dict)

    @classmethod
    def load(cls, schema_project_path: str) -> "_Project":
        settings = management.get_project_settings(schema_project_path)
        dev_file = os.path.join(
            schema_project_path, "development", f"{settings.get('schemaFileName')}_next.sql"
        )
        if not os.path.exists(dev_file):
            raise ValueError(
                f"The MSM project folder does not contain a schema development file `{dev_file}`."
            )
        with open(dev_file, "r") as f:
            script = f.read()
        # Every template section the script lacks is added here, so that the
        # snapshot's hash, the outline's positions and what _write writes
        # are all of the same, complete script.
        script, sections, _ = sections_lib.complete_script(schema_project_path, script, settings)
        return cls(schema_project_path, settings, dev_file, script, sections)

    @property
    def development(self) -> str:
        return os.path.dirname(self.dev_file)

    def read(self, path: str) -> tuple:
        """Reads a SOURCE directive's file, as source.read_include does, once."""
        read_path, content = self.files.get(path) or source.read_include(path)
        self.files[path] = (read_path, content)
        return read_path, content


def _rows(project: _Project) -> list:
    section = project.sections.get(DIAGRAMS_SECTION_ID)
    if section is None:
        return []
    content = section["sql_content"]
    if source.contains_source_directive(content):
        content = source.expand_source_directives(
            content, project.development, read=project.read
        )[0]
    return diagrams.parse_view_sql(content)


def read_diagram_rows(schema_project_path: str) -> list:
    """Returns the rows of the project's diagrams VIEW.

    Returns:
        The rows, each {name, format, document}; none where the section is
        missing or empty.
    """
    return _rows(_Project.load(schema_project_path))


def _plan_write(project: _Project, rows: list, moves_out: bool = None) -> tuple:
    """Works out what _write writes, writing nothing.

    Args:
        project: The project, as it is on disk.
        rows: The rows of the diagrams VIEW.
        moves_out: Whether an inline section moves into the breakout file;
            None decides by maxInlineSectionSize.

    Returns:
        (breakout, sections, moves_out): the VIEW's breakout file as (path,
        text, old text), or None where the section stays inline; the
        script's sections with Section 160's new body (project.sections
        unchanged); and whether an inline section moved out.
    """
    section = dict(project.sections[DIAGRAMS_SECTION_ID])
    view_sql = diagrams.render_view_sql(rows)
    breakout = None

    included = _section_directive(section["sql_content"])
    if moves_out is None:
        moves_out = included is None and len(view_sql) > sections_lib.get_max_inline_section_size(
            project.settings
        )
    if included is None and moves_out:
        header = sections_lib.breakout_header(
            project.settings, DIAGRAMS_SECTION_ID, "Schema Diagrams, written by msm"
        )
        included = (BREAKOUT_FILE, sections_lib.header_end(header), "")
        before, after = header, ""
        body = sections_lib.include_directive(BREAKOUT_FILE_NAME, header)
    elif included is not None:
        before = after = ""
        body = None
    else:
        body = view_sql

    if included is not None:
        path = source.checked_include_path(
            source.resolve_include_path(included[0], project.development)
        )
        try:
            old = project.read(path)[1]
        except ValueError:
            old = None
        if body is None:
            # The parts of the file outside the directive's slice stay.
            current = old or ""
            start = source.get_source_slice_offset(current, included[1], False) or 0
            end = source.get_source_slice_offset(current, included[2], True)
            before, after = current[:start], "" if end is None else current[end:]
        text = before + view_sql + "\n" + after
        # A bound that does not move with the VIEW (an end line or offset
        # counted from the start, a start past the file's end) would select
        # part of it only.
        if source.get_source_slice(text, included[1], included[2]).strip("\n") != view_sql:
            raise ValueError(
                f"The SOURCE directive of MSM Section {DIAGRAMS_SECTION_ID} selects "
                f"[{included[1]}:{included[2]}] of `{included[0]}`, which would not hold "
                "the whole diagrams VIEW once written. Leave the end bound out (or count "
                "it from the end of the file) and make sure the file exists."
            )
        breakout = (path, text, old)

    if body is not None:
        sections_lib.set_section_body(section, body)
    sections = dict(project.sections)
    sections[DIAGRAMS_SECTION_ID] = section
    return breakout, sections, moves_out


def _shift_script_lines(node, first_line: int, delta: int, in_script: bool = True) -> None:
    """Moves the positions in the development script on or after first_line
    by delta lines; positions in included files (of an object or note with a
    `source`) stay."""
    if isinstance(node, dict):
        if "range" in node or isinstance(node.get("source"), int):
            # An object (or a note) and its members each carry their own
            # `source`: one without it is in the script.
            in_script = not isinstance(node.get("source"), int)
        # The positions dump_document lays out at a fixed width
        if diagrams.is_position(node):
            if in_script and node["line"] >= first_line:
                node["line"] += delta
            return
        for value in node.values():
            _shift_script_lines(value, first_line, delta, in_script)
    elif isinstance(node, list):
        for value in node:
            _shift_script_lines(value, first_line, delta, in_script)


def _write(project: _Project, rows: list) -> list:
    """Writes the rows of the project's diagrams VIEW into Section 160.

    A section that includes a file keeps doing so: the file's part the
    directive selects is replaced, its header and footer stay. An inline
    section stays inline while the VIEW fits maxInlineSectionSize; a larger
    one moves into the breakout file sections/160_schema_diagrams.sql. A file
    that would not change is not written.

    The snapshot's positions are those of the script as it is: where the
    section's new body has another number of lines (an inline VIEW that
    grew, or one moved into the breakout file), the positions after it are
    moved with it, so they are those of the script as written; the snapshot
    in rows is changed in place.

    Returns:
        The files written.
    """
    old_lines = project.script.count("\n")
    first_line = project.script.count(
        "\n", 0, project.sections[DIAGRAMS_SECTION_ID]["end_position"]
    )
    schema = _schema_row(rows)

    def joined(sections) -> str:
        return "\n".join(item["full_content"] for item in sections.values())

    breakout, sections, moves_out = _plan_write(project, rows)
    script = joined(sections)
    delta = script.count("\n") - old_lines
    if delta and schema is not None:
        # In place, so the caller's snapshot is the one written. Moving a
        # position changes no line break (diagrams.dump_document), so the
        # section keeps its line count; whether it moves into its breakout
        # file stays as decided.
        _shift_script_lines(schema["document"], first_line, delta)
        breakout, sections, _ = _plan_write(project, rows, moves_out)
        script = joined(sections)
        if script.count("\n") - old_lines != delta:
            raise ValueError(
                f"MSM Section {DIAGRAMS_SECTION_ID} changed its line count when the "
                "snapshot's positions were moved with it."
            )

    written = []
    if breakout is not None:
        path, text, old = breakout
        if sections_lib.write_if_changed(path, text, old):
            written.append(path)
            project.files[path] = (path, text)
    if script != project.script:
        management.write_sections_to_file(project.dev_file, sections, overwrite_existing=True)
        written.insert(0, project.dev_file)
        project.script, project.sections = script, management.get_script_sections(script)
    return written


@changes_project
def write_diagram_rows(schema_project_path: str, rows: list) -> list:
    """Writes the rows of the project's diagrams VIEW into Section 160, see _write().

    Returns:
        The files written.
    """
    return _write(_Project.load(schema_project_path), rows)


def _outline_input(project: _Project) -> tuple:
    """The development script as the language server reads it for the snapshot.

    The snapshot must not describe the VIEW that holds it, so Section 160 is
    left out: blanked for the outline, its lines kept so that positions stay
    those of the file, and removed for the hash with its banner and the line
    break joining it to the next section, which then does not change when
    only the diagrams do, nor when msm adds the section to an older script.

    Returns:
        The script for the outline and the script for the hash.
    """
    script = hashed = project.script
    section = project.sections.get(DIAGRAMS_SECTION_ID)
    if section is not None:
        start = section["start_position"] + section["sql_start"]
        end = section["end_position"]
        hashed = script[: section["start_position"]] + script[end + 1 :]
        script = script[:start] + "\n" * script.count("\n", start, end) + script[end:]
    return script, hashed


def _source_hash(project: _Project, hashed: str) -> str:
    """The hash of the script without Section 160, its SOURCE files expanded."""
    expanded = source.expand_source_directives(hashed, project.development, read=project.read)[0]
    return diagrams.source_hash(expanded)


def _build_schema_definition(
    project: _Project, lsp_path: str = None, outline: dict = None
) -> dict:
    """Makes the msm-schema/1 snapshot of the project's development script.

    Args:
        project: The project, as it is on disk.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        outline: The binary's outline of the script as _outline_input()
            gives it, where the caller has one already (``mariadb-lsp edit``
            returns the outline of the texts it edited); without one the
            binary is run.

    Raises:
        ValueError: Without the binary, for an include that is refused, or
            when a statement could not be read: the snapshot would miss what
            it defines.
    """
    script, hashed = _outline_input(project)
    script_name = os.path.basename(project.dev_file)

    # Expanded first, so every include is checked before the binary reads it.
    hash_value = _source_hash(project, hashed)

    if not outline:
        outline = lsp.outline(lsp.require_lsp(lsp_path), script, project.development)
    files = [script_name] + [
        os.path.relpath(include["file"], project.development).replace(os.sep, "/")
        for include in outline.get("includes", [])
    ]
    unread = diagrams.unread_statements(outline)
    if unread:
        # A note on an included statement is at that statement, in its file
        # (newer binaries), and its message starts with the file and line.
        details = "; ".join(
            note["message"] if "source" in note
            else f"line {note['start']['line'] + 1}: {note['message']}"
            for note in unread[:10]
        )
        raise ValueError(
            f"{len(unread)} statement(s) of `{script_name}` were not applied, so the "
            f"schema snapshot would miss what they define: {details}"
        )

    schema_name = management.get_effective_schema_name(project.settings)
    return diagrams.schema_document_from_outline(outline, schema_name, files, hash_value)


def build_schema_definition(schema_project_path: str, lsp_path: str = None) -> dict:
    """Makes the msm-schema/1 snapshot of the project's development script.

    Args:
        schema_project_path: The project folder.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.

    Returns:
        The snapshot document.
    """
    return _build_schema_definition(_Project.load(schema_project_path), lsp_path)


def _current_hash(project: _Project) -> str:
    """The hash the snapshot of the development script as it is now would have."""
    return _source_hash(project, _outline_input(project)[1])


def current_source_hash(schema_project_path: str) -> str:
    """The hash the snapshot of the development script as it is now would have."""
    return _current_hash(_Project.load(schema_project_path))


def snapshot_is_current(schema_project_path: str) -> bool:
    """Whether the project's schema snapshot was made from its development
    script as it is; False where the script cannot be read."""
    try:
        project = _Project.load(schema_project_path)
        return _snapshot_is_current(project, _schema_row(_rows(project)))
    except (ValueError, OSError):
        return False


def keeping_snapshot_current(schema_project_path: str, change: Callable[[], list]) -> list:
    """Runs change, which rewrites only comments of the development script,
    and gives a snapshot that matched the script before the hash of the
    script after: nothing it describes changed.

    Args:
        schema_project_path: The project folder.
        change: Writes the files and returns the ones it wrote.

    Returns:
        The files written, by change and for the snapshot.
    """
    current = snapshot_is_current(schema_project_path)
    written = change()
    if current:
        project = _Project.load(schema_project_path)
        rows = _rows(project)
        snapshot = _schema_row(rows)
        new_hash = _current_hash(project)
        if snapshot["document"].get("source_hash") != new_hash:
            snapshot["document"]["source_hash"] = new_hash
            written += [path for path in _write(project, rows) if path not in written]
    return written


def _is_diagram(row: dict, name: str = None) -> bool:
    return row["format"] == diagrams.DIAGRAM_FORMAT and (name is None or row["name"] == name)


def _replace_snapshot(
    project: _Project, rows: list, lsp_path: str = None, outline: dict = None
) -> tuple:
    """Makes the snapshot again and puts it in the rows, first.

    Diagrams lose the figures and connections of objects the schema no
    longer has.

    Args:
        project: The project, as it is on disk.
        rows: The rows of the diagrams VIEW.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        outline: The outline to make the snapshot from, see
            _build_schema_definition().

    Returns:
        The snapshot, the rows and the names of the diagrams that changed.
    """
    document = _build_schema_definition(project, lsp_path, outline)
    rows = [row for row in rows if row["format"] != diagrams.SCHEMA_FORMAT]
    changed = [
        row["name"] for row in rows
        if _is_diagram(row) and diagrams.reconcile_diagram(row["document"], document)
    ]
    rows.insert(
        0,
        {"name": diagrams.SCHEMA_ROW_NAME, "format": diagrams.SCHEMA_FORMAT, "document": document},
    )
    return document, rows, changed


@changes_project
def update_schema_definition(schema_project_path: str, lsp_path: str = None) -> dict:
    """Makes the snapshot again and writes it into the diagrams VIEW.

    Diagrams lose the figures and connections of objects the schema no longer
    has.

    Returns:
        {tables, views, routines, triggers, events, sequences} counts, the
        diagrams that changed and the files written.
    """
    project = _Project.load(schema_project_path)
    document, rows, changed = _replace_snapshot(project, _rows(project), lsp_path)
    result = {kind: len(document[kind]) for kind in diagrams.SCHEMA_OBJECT_KINDS}
    result["changed_diagrams"] = changed
    result["files"] = _write(project, rows)
    return result


def _schema_row(rows: list) -> Optional[dict]:
    """The schema snapshot's row, or None."""
    return next((row for row in rows if row["format"] == diagrams.SCHEMA_FORMAT), None)


def _snapshot_is_current(project: _Project, schema: Optional[dict]) -> bool:
    """Whether the snapshot row was made from the development script as it is."""
    return schema is not None and schema["document"].get("source_hash") == _current_hash(project)


@changes_project
def split_sections(schema_project_path: str, section_ids: list = None, lsp_path: str = None) -> dict:
    """Splits sections into breakout files by object kind (sections.split_sections).

    The snapshot records where each object is written, so one that matched
    the script before the split is made again; a stale one stays stale, so
    no diagram is reconciled unasked.

    Returns:
        The SplitResult's members, snapshot_updated, and snapshot_error
        where the snapshot could not be made again.
    """
    project = _Project.load(schema_project_path)
    current = _snapshot_is_current(project, _schema_row(_rows(project)))
    result = asdict(
        sections_lib.split_sections(schema_project_path, section_ids, lsp_path)
    )
    result["snapshot_updated"] = bool(result["split"]) and current
    if result["snapshot_updated"]:
        # The split is written already; a snapshot that cannot be made again
        # is reported, not raised, so that the caller learns what changed.
        try:
            written = update_schema_definition(schema_project_path, lsp_path)["files"]
        except ValueError as e:
            result["snapshot_updated"] = False
            result["snapshot_error"] = str(e)
        else:
            result["files"] = list(dict.fromkeys([*result["files"], *written]))
    return result


def list_diagrams(schema_project_path: str) -> dict:
    """Says what the project's diagrams VIEW holds.

    Returns:
        {schema_definition: {tables, current} or None, diagrams: [{name,
        figures, connections}]}; current says whether the snapshot was made
        from the development script as it is now.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    schema = _schema_row(rows)
    info = None
    if schema is not None:
        info = {
            "tables": len(schema["document"].get("tables", [])),
            "current": _snapshot_is_current(project, schema),
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


def get_diagram_image(schema_project_path: str, name: str, figure_id: str) -> str:
    """Returns the picture of an image figure, which get_diagram_rows'
    callers that elide pictures (diagrams.elide_images) leave out.

    Returns:
        The picture, a data URL.

    Raises:
        ValueError: For a diagram, or an image figure with a picture, the
            VIEW does not hold.
    """
    document = next(
        row["document"] for row in get_diagram_rows(schema_project_path, name)
        if _is_diagram(row, name)
    )
    figure = next(
        (figure for figure in document.get("figures", [])
         if figure.get("id") == figure_id),
        None,
    )
    if figure is None:
        raise ValueError(f"The diagram `{name}` has no figure `{figure_id}`.")
    picture = figure.get("image")
    if diagrams.figure_type(figure) != diagrams.IMAGE_FIGURE or not isinstance(picture, str):
        raise ValueError(f"The figure `{figure_id}` of the diagram `{name}` holds no picture.")
    return picture


@changes_project
def set_diagram(schema_project_path: str, document: dict) -> list:
    """Adds a diagram to the VIEW, or replaces the one of the same name.

    An image figure given without its picture keeps the one the replaced
    diagram holds for that figure id (get_diagram_rows' callers may leave
    the data URL out).

    Returns:
        The files written.
    """
    diagrams.validate_diagram(document)
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    row = {"name": document["name"], "format": diagrams.DIAGRAM_FORMAT, "document": document}
    index = next(
        (index for index, existing in enumerate(rows) if _is_diagram(existing, document["name"])),
        None,
    )
    if index is None:
        rows.append(row)
    else:
        diagrams.keep_images(document, rows[index]["document"])
        rows[index] = row
    return _write(project, rows)


@changes_project
def remove_diagram(schema_project_path: str, name: str) -> list:
    """Removes a diagram from the VIEW.

    Returns:
        The files written.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    kept = [row for row in rows if not _is_diagram(row, name)]
    if len(kept) == len(rows):
        raise ValueError(f"The project has no diagram `{name}`.")
    return _write(project, kept)


@changes_project
def rename_diagram(schema_project_path: str, name: str, new_name: str) -> list:
    """Renames a diagram: its row's name and its document's. Everything else
    stays as stored (pictures and the rows' order included).

    Raises:
        ValueError: For no diagram `name`, an empty new name, the schema
            row's name, or a new name another diagram has already, whatever
            its case (a change of case of the diagram itself is a rename).

    Returns:
        The files written; none where the name stays as it is.
    """
    new_name = (new_name or "").strip() if isinstance(new_name, str) else ""
    if not new_name:
        raise ValueError("A diagram needs a name.")
    if new_name == diagrams.SCHEMA_ROW_NAME:
        raise ValueError(f"`{diagrams.SCHEMA_ROW_NAME}` is the name of the schema row.")
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    row = next((row for row in rows if _is_diagram(row, name)), None)
    if row is None:
        raise ValueError(f"The project has no diagram `{name}`.")
    taken = next(
        (other["name"] for other in rows
         if other is not row and _is_diagram(other) and other["name"].lower() == new_name.lower()),
        None,
    )
    if taken is not None:
        raise ValueError(f"The project has a diagram `{taken}` already.")
    if new_name == name:
        return []
    row["name"] = new_name
    row["document"]["name"] = new_name
    return _write(project, rows)


# -----------------------------------------------------------------------------
# Laying diagrams out


def _current_snapshot(project: _Project, rows: list, lsp_path: str = None) -> tuple:
    """The snapshot of the script as it is now: the VIEW's, or made again.

    Returns:
        The snapshot and the rows, holding it.
    """
    schema = _schema_row(rows)
    if _snapshot_is_current(project, schema):
        # A snapshot written before foreign keys were named has unnamed ones.
        for table in schema["document"].get("tables", []):
            diagrams.name_foreign_keys(table)
        return schema["document"], rows
    document, rows, _ = _replace_snapshot(project, rows, lsp_path)
    return document, rows


def _new_id(taken: set) -> str:
    while True:
        new = str(uuid.uuid4())
        if new not in taken:
            taken.add(new)
            return new


def _require_known(what: str, objects: dict, names: list) -> None:
    unknown = [name for name in names if name.lower() not in objects]
    if unknown:
        raise ValueError(
            f"The schema has no {what} "
            + ", ".join(f"`{name}`" for name in unknown)
            + " (the snapshot is made from the development script's CREATE statements)."
        )


def _new_figure(figure_type: str, name: str, taken: set, **members) -> dict:
    """A figure msm adds: not placed or sized yet, expanded, in the kind's
    colour, with the members naming what it shows."""
    return {
        "id": _new_id(taken),
        "type": figure_type,
        "name": name,
        **members,
        "color": diagrams.FIGURE_KINDS[figure_type].color,
        "expanded": True,
        "manualSizing": False,
    }


def _add_object_figures(
    diagram: dict, figure_type: str, catalog: dict, names: list, taken: set
) -> list:
    """Adds a figure, not placed or sized yet, for each of the named tables,
    views or sequences not on the diagram.

    Args:
        diagram: The diagram, changed in place.
        figure_type: diagrams.TABLE_FIGURE, VIEW_FIGURE or SEQUENCE_FIGURE.
        catalog: The snapshot's objects, see diagrams.catalog.
        names: The objects to add.
        taken: The ids in use, which the new ones join.

    Returns:
        The names of the objects added.
    """
    kind = diagrams.FIGURE_KINDS[figure_type]
    objects = catalog[kind.objects]
    _require_known(kind.object, objects, names)
    shown = {
        diagrams.figure_object_name(figure).lower()
        for figure in diagram["figures"]
        if diagrams.figure_type(figure) == figure_type
    }
    added = []
    for name in names:
        item = objects[name.lower()]
        if name.lower() in shown:
            continue
        shown.add(name.lower())
        diagram["figures"].append(
            _new_figure(figure_type, item["name"], taken, **{kind.key: item["name"]})
        )
        added.append(item["name"])
    return added


def _add_group_members(
    diagram: dict, figure_type: str, catalog: dict, names: list, taken: set
) -> list:
    """Lists routines or events on the diagram's group figure of that kind,
    adding the figure (not placed yet) where there is none.

    Objects the diagram lists already, on any of its group figures of the
    kind, are left where they are. The figure grows with its list unless it
    is sized by hand.

    Args:
        figure_type: diagrams.ROUTINE_GROUP_FIGURE or EVENT_GROUP_FIGURE.

    Returns:
        The names of the objects added.
    """
    kind = diagrams.FIGURE_KINDS[figure_type]
    objects = catalog[kind.objects]
    _require_known(kind.object, objects, names)
    groups = [figure for figure in diagram["figures"] if diagrams.figure_type(figure) == figure_type]
    shown = {name.lower() for group in groups for name in diagrams.figure_members(group)}
    added = list(dict.fromkeys(
        objects[name.lower()]["name"] for name in names if name.lower() not in shown
    ))
    if not added:
        return []
    if groups:
        group = groups[0]
        group[kind.key] = diagrams.figure_members(group) + added
        # A placed group is not laid out again: its card grows here.
        _size_figure(group, diagrams.figure_objects(group, catalog), grow=True)
    else:
        diagram["figures"].append(
            _new_figure(figure_type, kind.group_name, taken, **{kind.key: added})
        )
    return added


def _add_connections(diagram: dict, tables: dict, schema_name: str, taken: set) -> int:
    """Adds a connection for every foreign key between two tables on the
    diagram that has none.

    Returns:
        How many were added.
    """
    figure_of = {}
    for figure in diagram["figures"]:
        if diagrams.is_table_figure(figure):
            figure_of.setdefault(diagrams.figure_table(figure), figure)
    drawn = {
        ((connection.get("table") or "").lower(), (connection.get("foreignKey") or "").lower())
        for connection in diagram["connections"]
    }
    added = 0
    for name, start in figure_of.items():
        for foreign_key in tables[name].get("foreign_keys", []) if name in tables else []:
            parent = diagrams.foreign_key_parent(foreign_key, schema_name)
            end = None if parent is None else figure_of.get(parent)
            key = (name, foreign_key["name"].lower())
            if end is None or key in drawn:
                continue
            drawn.add(key)
            diagram["connections"].append(
                {
                    "id": _new_id(taken),
                    "foreignKey": foreign_key["name"],
                    "table": tables[name]["name"],
                    "startFigure": start["id"],
                    "endFigure": end["id"],
                    "visible": True,
                }
            )
            added += 1
    return added


def _size_figure(figure: dict, objects, grow: bool = False) -> None:
    """Gives a figure the size its card needs for what it shows (see
    diagrams.figure_objects): the width where it has none, the height
    unless sized by hand. With grow, a width it has is kept only when it is
    wide enough."""
    kind = diagrams.figure_kind(figure)
    title = figure.get("name") or "" if kind.many else objects.get("name", "")
    width, height = layout.card_size(
        title, kind.rows(objects), kind.sections(objects, figure), figure
    )
    manual = figure.get("manualSizing") is True
    if not diagrams.is_number(figure.get("width")) or (grow and not manual and figure["width"] < width):
        figure["width"] = width
    if not (manual and diagrams.is_number(figure.get("height"))):
        figure["height"] = height


def _place(diagram: dict, catalog: dict, keep_placed: bool) -> list:
    """Lays out the figures of the diagram that are to move.

    Figures on a layer keep their place: the layer is the user's grouping
    and their positions are relative to it. With keep_placed, so does every
    figure that has a position. Notes, images and figures of objects the
    schema does not have are never moved. The others are laid out (see
    lib/layout.py), as one block below everything that stays, or at the
    diagram's corner.

    Returns:
        The names of the tables, views, sequences and groups placed.
    """
    layers = {layer.get("id"): layer for layer in diagram.get("layers", [])}
    member_of = {}
    for layer_id, layer in layers.items():
        for figure_id in layer.get("figures", []):
            member_of.setdefault(figure_id, layer_id)

    def layer_of(figure):
        layer_id = figure.get("layer")
        if layer_id is None:
            layer_id = member_of.get(figure.get("id"))
        return layers.get(layer_id) if layer_id is not None else None

    moving = []  # (figure, what it shows)
    staying = []
    for figure in diagram["figures"]:
        placed = diagrams.is_number(figure.get("left")) and diagrams.is_number(figure.get("top"))
        objects = diagrams.figure_objects(figure, catalog)
        if objects is not None and layer_of(figure) is None and not (keep_placed and placed):
            moving.append((figure, objects))
        elif placed:
            staying.append(figure)

    # What stays, in diagram coordinates.
    boxes = []
    for figure in staying:
        layer = layer_of(figure)
        left = figure["left"] + (layer.get("left", 0) if layer else 0)
        top = figure["top"] + (layer.get("top", 0) if layer else 0)
        width, height = diagrams.footprint(figure)
        boxes.append((left, top, left + width, top + height))
    for layer in layers.values():
        if all(diagrams.is_number(layer.get(key)) for key in ("left", "top", "width", "height")):
            boxes.append(
                (layer["left"], layer["top"], layer["left"] + layer["width"],
                 layer["top"] + layer["height"])
            )

    if moving:
        sizes = {}
        for figure, objects in moving:
            _size_figure(figure, objects)
            sizes[figure["id"]] = (figure["width"], figure["height"])

        ids = set(sizes)
        edges = [
            (connection.get("startFigure"), connection.get("endFigure"))
            for connection in diagram["connections"]
            if connection.get("startFigure") in ids and connection.get("endFigure") in ids
        ]
        positions = layout.layout([figure["id"] for figure, _ in moving], sizes, edges)
        if boxes:
            origin_left = min(box[0] for box in boxes)
            origin_top = max(box[3] for box in boxes) + layout.ROW_GAP
        else:
            origin_left = origin_top = layout.MARGIN
        for figure, _ in moving:
            left, top = positions[figure["id"]]
            figure["left"] = float(origin_left + left)
            figure["top"] = float(origin_top + top)
            boxes.append(
                (figure["left"], figure["top"], figure["left"] + figure["width"],
                 figure["top"] + figure["height"])
            )

    if boxes:
        right = max(box[2] for box in boxes) + layout.MARGIN
        bottom = max(box[3] for box in boxes) + layout.MARGIN
        width, height = diagram.get("width"), diagram.get("height")
        diagram["width"] = float(max(right, width if diagrams.is_number(width) else 0))
        diagram["height"] = float(max(bottom, height if diagrams.is_number(height) else 0))
    return [diagrams.figure_object_name(figure) for figure, _ in moving]


# The figures a diagram shows besides its tables, in the order they are
# added; create_diagram and layout_diagram name their objects by the kind's
# `objects` (views=, sequences=, routines=, events=).
_OTHER_FIGURES = (
    diagrams.VIEW_FIGURE,
    diagrams.SEQUENCE_FIGURE,
    diagrams.ROUTINE_GROUP_FIGURE,
    diagrams.EVENT_GROUP_FIGURE,
)


def _fill(diagram: dict, document: dict, tables: list, keep_placed: bool, **others) -> tuple:
    """Adds the figures of the tables and of the other objects (lists named
    by the kinds' `objects`, see _OTHER_FIGURES; None for none), the missing
    connections, and lays the figures out.

    Returns:
        The objects added, the number of connections added and the figures
        placed (by what they show).
    """
    catalog = diagrams.catalog(document)
    items = diagram["figures"] + diagram["connections"]
    taken = {item["id"] for item in items if item.get("id")}
    # The layout keys the figures by id; a document may leave one out.
    for item in items:
        if not item.get("id"):
            item["id"] = _new_id(taken)
    added = _add_object_figures(diagram, diagrams.TABLE_FIGURE, catalog, tables, taken)
    for figure_type in _OTHER_FIGURES:
        kind = diagrams.FIGURE_KINDS[figure_type]
        add = _add_group_members if kind.many else _add_object_figures
        added += add(diagram, figure_type, catalog, others.get(kind.objects) or [], taken)
    connections_added = _add_connections(diagram, catalog["tables"], document["schema"], taken)
    placed = _place(diagram, catalog, keep_placed)
    return added, connections_added, placed


@changes_project
def create_diagram(
    schema_project_path: str,
    name: str,
    tables: list = None,
    lsp_path: str = None,
    views: list = None,
    routines: list = None,
    events: list = None,
    sequences: list = None,
) -> dict:
    """Adds a diagram of the schema's tables, laid out.

    The snapshot is made again first where the development script changed
    since it was made.

    Args:
        schema_project_path: The project folder.
        name: The new diagram's name.
        tables: The tables to show; all of the schema's by default.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        views: Views to show as well; none by default.
        routines: Routines to list on a routine group figure; none by default.
        events: Events to list on an event group figure; none by default.
        sequences: Sequences to show; none by default.

    Returns:
        {name, figures, connections, files}.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    if any(_is_diagram(row, name) for row in rows):
        raise ValueError(
            f"The project has a diagram `{name}` already; lay it out with "
            "layoutSchemaDiagram or remove it first."
        )
    document, rows = _current_snapshot(project, rows, lsp_path)
    diagram = {
        "format": diagrams.DIAGRAM_FORMAT,
        "name": name,
        "width": 0.0,
        "height": 0.0,
        "layers": [],
        "figures": [],
        "connections": [],
    }
    names = [table["name"] for table in document["tables"]] if tables is None else list(tables)
    _fill(
        diagram, document, names, False,
        views=views, routines=routines, events=events, sequences=sequences,
    )
    if not diagram["figures"]:
        diagram["width"], diagram["height"] = 1000.0, 800.0
    rows.append({"name": name, "format": diagrams.DIAGRAM_FORMAT, "document": diagram})
    return {
        "name": name,
        "figures": len(diagram["figures"]),
        "connections": len(diagram["connections"]),
        "files": _write(project, rows),
    }


@changes_project
def layout_diagram(
    schema_project_path: str,
    name: str,
    tables: list = None,
    keep_placed: bool = True,
    lsp_path: str = None,
    views: list = None,
    routines: list = None,
    events: list = None,
    sequences: list = None,
) -> dict:
    """Adds tables, views, sequences, routines and events to a diagram and
    lays its figures out.

    The snapshot is made again first where the development script changed
    since it was made. Foreign keys between tables on the diagram that are
    not drawn get a connection.

    Args:
        schema_project_path: The project folder.
        name: The diagram.
        tables: Tables to add to the diagram; none by default.
        keep_placed: Whether figures that have a position keep it, so only
            the ones added or without a position are laid out; else all are.
            Figures on a layer always keep theirs.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        views: Views to add; none by default.
        routines: Routines to list on the diagram's routine group figure,
            added where it has none; none by default.
        events: Events to list on its event group figure, likewise.
        sequences: Sequences to add; none by default.

    Returns:
        {name, added: the objects added, placed: the figures laid out (the
        tables, views and sequences, and the groups they show),
        connections_added, files}.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    if not any(_is_diagram(row, name) for row in rows):
        raise ValueError(f"The project has no diagram `{name}`.")
    document, rows = _current_snapshot(project, rows, lsp_path)
    diagram = next(row for row in rows if _is_diagram(row, name))["document"]
    added, connections_added, placed = _fill(
        diagram, document, tables or [], keep_placed,
        views=views, routines=routines, events=events, sequences=sequences,
    )
    return {
        "name": name,
        "added": added,
        "placed": placed,
        "connections_added": connections_added,
        "files": _write(project, rows),
    }


# -----------------------------------------------------------------------------
# MySQL Workbench models


def _write_imported(project_path: str, imported) -> list:
    """Writes imported SQL (statements.Imported) into a new project's
    script; the files written."""
    # The import's SQL by the object kinds each part takes (None: every kind)
    by_kinds = {
        frozenset({"TABLE"}): imported.tables,
        frozenset({"TABLE_ADDITION"}): imported.table_additions,
        frozenset({"INSERT"}): imported.inserts,
        frozenset({"PROCEDURE", "FUNCTION"}): imported.routines,
        frozenset({"VIEW"}): imported.views,
        frozenset({"TRIGGER"}): imported.triggers,
        frozenset({"EVENT"}): imported.events,
        None: imported.roles,
    }
    return sections_lib.write_sections_parts(project_path, {
        section_id: [
            sections_lib.Part(layout.file_name, layout.title, by_kinds[layout.kinds])
            for layout in layouts
        ]
        for section_id, layouts in sections_lib.SECTION_PARTS.items()
    })


def _imported_snapshot(project_path: str, lsp_path: str = None) -> tuple:
    """The schema snapshot of a new project's imported script: the project,
    the snapshot and the diagrams VIEW's rows, not written yet."""
    project = _Project.load(project_path)
    schema_document, rows, _ = _replace_snapshot(project, _rows(project), lsp_path)
    return project, schema_document, rows


def _new_imported_project(schema_name: str, target_path: str, fill, **options) -> dict:
    """Creates the project folder of an import (create_schema_project_folder
    with options) and fills it, fill(project_path) -> its result; a project
    that cannot be completed is removed again, and a project folder it was
    to overwrite is put back."""
    license = options.pop("license", None)
    # A folder to overwrite is moved aside until the new project is complete,
    # so that a failed or cancelled import does not lose it
    existing = management.project_folder_path(target_path, schema_name)
    backup = None
    if options.get("overwrite_existing") and os.path.exists(existing):
        backup = f"{existing}.{uuid.uuid4().hex}.replaced"
        os.rename(existing, backup)
    try:
        project_path = management.create_schema_project_folder(
            schema_name=schema_name,
            target_path=target_path,
            license=license if license is not None else "None",
            allow_special_chars=True,
            **options,
        )
    except BaseException:
        if backup is not None:
            os.rename(backup, existing)
        raise
    try:
        result = {"project_path": project_path, **fill(project_path)}
    except BaseException:
        shutil.rmtree(project_path, ignore_errors=True)
        if backup is not None:
            os.rename(backup, existing)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
    return result


def _shown_count(document: dict) -> int:
    """How many figures, connections and group members a diagram shows."""
    return (
        len(document["figures"])
        + len(document["connections"])
        + sum(len(diagrams.figure_members(figure)) for figure in document["figures"])
    )


def _import_into(project_path: str, model, schema: dict, lsp_path: str = None) -> dict:
    """Writes a schema of a Workbench model into a new project's script."""
    imported = workbench.schema_sql(model, schema)
    files = _write_imported(project_path, imported)
    project, schema_document, rows = _imported_snapshot(project_path, lsp_path)
    documents, warnings = workbench.diagram_documents(model, schema)
    warnings = imported.warnings + warnings
    for document in documents:
        diagrams.validate_diagram(document)
        # reconcile_diagram also reports names it rewrote (a key's case, a
        # key matched by definition): warn only for what it left out.
        shown = _shown_count(document)
        diagrams.reconcile_diagram(document, schema_document)
        if _shown_count(document) < shown:
            warnings.append(
                f"The diagram `{document['name']}` shows tables or foreign keys the "
                "imported SQL does not define; they were left out."
            )
        rows.append({"name": document["name"], "format": diagrams.DIAGRAM_FORMAT,
                     "document": document})
    files += _write(project, rows)

    result = dict(imported.counts)
    result["diagrams"] = [document["name"] for document in documents]
    result["warnings"] = warnings
    result["files"] = list(dict.fromkeys(files))
    return result


def create_project_from_workbench_model(
    model_path: str,
    target_path: str,
    copyright_holder: str = None,
    license: str = None,
    schema: str = None,
    overwrite_existing: bool = False,
    enforce_target_path: bool = False,
    copyrights: list = None,
    lsp_path: str = None,
    model: workbench.Model = None,
    year_of_creation: str = None,
) -> dict:
    """Creates a schema project from a schema of a MySQL Workbench model.

    The project's development script gets the schema's CREATE statements
    (tables, foreign keys, the model's rows as INSERTs in Section 140; views,
    routines, triggers and events in Section 150; roles and their GRANTs in
    Section 170), each section in breakout files where it is larger than
    maxInlineSectionSize. Then the schema snapshot is made from that SQL, and
    the model's EER diagrams are added. A project that cannot be completed is
    removed again.

    Args:
        model_path: The .mwb file.
        target_path: The folder to create the project folder in.
        copyright_holder: The project's copyright holder.
        license: The project's license.
        schema: The model's schema to import; needed when it has several.
        overwrite_existing: Replace an existing project folder.
        enforce_target_path: Create target_path where it is missing.
        copyrights: The copyright notices, instead of copyright_holder.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        model: The model read from model_path already (read_model), which
            is then not read again.
        year_of_creation: The year of creation of the copyright, defaulting
            to the current one.

    Returns:
        {project_path, schema, tables, views, routines, triggers, events,
        roles, inserted_rows, diagrams, scripts_not_imported, warnings,
        files}.
    """
    if model is None:
        model = workbench.read_model(model_path)
    model_schema = model.schema(schema)
    # Checked first: the snapshot needs the binary, and a project without
    # it would be removed again.
    lsp.require_lsp(lsp_path)

    result = _new_imported_project(
        model_schema["name"], target_path,
        lambda project_path: _import_into(project_path, model, model_schema, lsp_path),
        copyright_holder=copyright_holder, license=license,
        overwrite_existing=overwrite_existing, enforce_target_path=enforce_target_path,
        copyrights=copyrights, year_of_creation=year_of_creation,
    )
    return {
        "project_path": result.pop("project_path"),
        "schema": model_schema["name"],
        **result,
        "scripts_not_imported": model.scripts,
    }


# The stages of a live import after live_schema.READING_STAGE
LIVE_WRITING_STAGE = "Writing the project"
LIVE_SNAPSHOT_STAGE = "Making the schema snapshot"


def prepare_live_import(
    session,
    schema: str,
    target_path: str,
    objects: dict = None,
    copyright_holder: str = None,
    license: str = None,
    overwrite_existing: bool = False,
    enforce_target_path: bool = False,
    copyrights: list = None,
    lsp_path: str = None,
    year_of_creation: str = None,
) -> tuple:
    """What refuses an import of a live schema, checked before an object is
    read (the arguments are create_project_from_live_schema's): the new
    project (management.check_new_project), the binary the snapshot needs
    (one that tells what views read, where views are imported), the schema
    and the selection.

    Returns:
        (listed, selected): live_schema.list_schema_objects' listing and
        the names the import takes, for create_project_from_live_schema's
        prepared.

    Raises:
        ValueError: For what create_schema_project_folder refuses, a
            missing mariadb-lsp, an unknown schema or a bad selection.
    """
    from msm_plugin.lib import live_schema

    management.check_new_project(
        schema, target_path, copyright_holder, license, overwrite_existing,
        True, enforce_target_path, copyrights, year_of_creation,
    )
    binary = lsp.require_lsp(lsp_path)
    listed = live_schema.list_schema_objects(session, schema, binary)
    selected = live_schema.selected_objects(listed, objects)
    # The views are written in the order of what they read, which only the
    # binary's parser tells
    if selected["views"] and not lsp.outlines_view_reads(binary):
        raise ValueError(
            f"The mariadb-lsp binary `{binary}` does not tell what a view reads, which the "
            "import orders the views by: a newer MariaDB Shell brings one that does."
        )
    return listed, selected


def create_project_from_live_schema(
    session,
    schema: str,
    target_path: str,
    objects: dict = None,
    copyright_holder: str = None,
    license: str = None,
    overwrite_existing: bool = False,
    enforce_target_path: bool = False,
    copyrights: list = None,
    lsp_path: str = None,
    year_of_creation: str = None,
    task=None,
    prepared: tuple = None,
) -> dict:
    """Creates a schema project from a schema on a server (live_schema).

    The project's development script gets the CREATE statements the server
    gives for the schema's objects, or for the selected ones: tables and
    sequences in Section 140, views, routines, triggers and events in
    Section 150, each section in breakout files where it is larger than
    maxInlineSectionSize; then the schema snapshot is made from that SQL.
    Data, users, roles and grants are not imported, nor msm's own objects. A
    project that cannot be completed is removed again.

    Args:
        session: The session on the server.
        schema: The schema to import; the project gets its name.
        target_path: The folder to create the project folder in.
        objects: {tables?, views?, procedures?, functions?, triggers?,
            events?, sequences?: [names]}, the objects to import (a kind left
            out: none of it; no triggers list: the selected tables'
            triggers); None for all.
        copyright_holder: The project's copyright holder.
        license: The project's license.
        overwrite_existing: Replace an existing project folder.
        enforce_target_path: Create target_path where it is missing.
        copyrights: The copyright notices, instead of copyright_holder.
        lsp_path: The mariadb-lsp binary or its folder, instead of searching.
        year_of_creation: The year of creation of the copyright, defaulting
            to the current one.
        task: A task of the shell task manager to report to: the stages
            live_schema.READING_STAGE (a step per object), LIVE_WRITING_STAGE
            and LIVE_SNAPSHOT_STAGE; a cancel of it raises TaskCancelled, and
            the project is removed again.
        prepared: What prepare_live_import returned for these arguments,
            where the caller checked them already; None checks them here.

    Returns:
        {project_path, schema, tables, sequences, views, routines, triggers,
        events, schema_version: the version msm deployed of it or None,
        warnings, files}.
    """
    from msm_plugin.lib import live_schema

    options = dict(
        copyright_holder=copyright_holder, license=license,
        overwrite_existing=overwrite_existing, enforce_target_path=enforce_target_path,
        copyrights=copyrights, year_of_creation=year_of_creation,
    )
    if prepared is None:
        prepared = prepare_live_import(
            session, schema, target_path, objects, lsp_path=lsp_path, **options
        )
    listed, selected = prepared
    imported = live_schema.schema_sql(session, schema, selected, listed, task)

    def fill(project_path: str) -> dict:
        with live_schema.task_stage(task, LIVE_WRITING_STAGE):
            files = _write_imported(project_path, imported)
        with live_schema.task_stage(task, LIVE_SNAPSHOT_STAGE):
            project, _, rows = _imported_snapshot(project_path, lsp_path)
            files += _write(project, rows)
        return {"files": list(dict.fromkeys(files))}

    result = _new_imported_project(schema, target_path, fill, **options)
    return {
        "project_path": result["project_path"],
        "schema": schema,
        **imported.counts,
        "schema_version": listed["schema_version"],
        "warnings": imported.warnings,
        "files": result["files"],
    }
