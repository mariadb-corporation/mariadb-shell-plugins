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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from msm_plugin.lib import diagrams, layout, lsp, management, source
from msm_plugin.lib import sections as sections_lib
from msm_plugin.lib import workbench

DIAGRAMS_SECTION_ID = "160"
BREAKOUT_FILE_NAME = "160_schema_diagrams.sql"
BREAKOUT_FILE = f"./{sections_lib.BREAKOUT_FOLDER}/{BREAKOUT_FILE_NAME}"

# Every file of a SOURCE directive is read through source.read_include, so a
# caller that has to check them sets source.checked_includes() around the call.


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
        return cls(
            schema_project_path, settings, dev_file, script, management.get_script_sections(script)
        )

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


def _write(project: _Project, rows: list) -> list:
    """Writes the rows of the project's diagrams VIEW into Section 160.

    A section that includes a file keeps doing so: the file's part the
    directive selects is replaced, its header and footer stay. An inline
    section stays inline while the VIEW fits maxInlineSectionSize; a larger
    one moves into the breakout file sections/160_schema_diagrams.sql. A file
    that would not change is not written.

    Returns:
        The files written.
    """
    sections = ensure_diagrams_section(project.sections)
    section = sections[DIAGRAMS_SECTION_ID]
    view_sql = diagrams.render_view_sql(rows)
    written = []

    included = _section_directive(section["sql_content"])
    if included is None and len(view_sql) > sections_lib.get_max_inline_section_size(
        project.settings
    ):
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
        if sections_lib.write_if_changed(path, text, old):
            written.append(path)
            project.files[path] = (path, text)

    if body is not None:
        sections_lib.set_section_body(section, body)
    script = "\n".join(item["full_content"] for item in sections.values())
    if script != project.script:
        management.write_sections_to_file(project.dev_file, sections, overwrite_existing=True)
        written.insert(0, project.dev_file)
        project.script, project.sections = script, management.get_script_sections(script)
    return written


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


def _build_schema_definition(project: _Project, lsp_path: str = None) -> dict:
    """Makes the msm-schema/1 snapshot of the project's development script.

    Raises:
        ValueError: Without the binary, for an include that is refused, or
            when a statement could not be read: the snapshot would miss what
            it defines.
    """
    binary = lsp.require_lsp(lsp_path)
    script, hashed = _outline_input(project)
    script_name = os.path.basename(project.dev_file)

    # Expanded first, so every include is checked before the binary reads it.
    hash_value = _source_hash(project, hashed)

    outline = lsp.outline(binary, script, project.development)
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


def _is_diagram(row: dict, name: str = None) -> bool:
    return row["format"] == diagrams.DIAGRAM_FORMAT and (name is None or row["name"] == name)


def _replace_snapshot(project: _Project, rows: list, lsp_path: str = None) -> tuple:
    """Makes the snapshot again and puts it in the rows, first.

    Diagrams lose the figures and connections of objects the schema no
    longer has.

    Returns:
        The snapshot, the rows and the names of the diagrams that changed.
    """
    document = _build_schema_definition(project, lsp_path)
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


def list_diagrams(schema_project_path: str) -> dict:
    """Says what the project's diagrams VIEW holds.

    Returns:
        {schema_definition: {tables, current} or None, diagrams: [{name,
        figures, connections}]}; current says whether the snapshot was made
        from the development script as it is now.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    schema = next((row for row in rows if row["format"] == diagrams.SCHEMA_FORMAT), None)
    info = None
    if schema is not None:
        info = {
            "tables": len(schema["document"].get("tables", [])),
            "current": schema["document"].get("source_hash") == _current_hash(project),
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


# -----------------------------------------------------------------------------
# Laying diagrams out


def _current_snapshot(project: _Project, rows: list, lsp_path: str = None) -> tuple:
    """The snapshot of the script as it is now: the VIEW's, or made again.

    Returns:
        The snapshot and the rows, holding it.
    """
    schema = next((row for row in rows if row["format"] == diagrams.SCHEMA_FORMAT), None)
    if schema is not None and schema["document"].get("source_hash") == _current_hash(project):
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
    """Adds a figure, not placed or sized yet, for each of the named tables
    or views not on the diagram.

    Args:
        diagram: The diagram, changed in place.
        figure_type: diagrams.TABLE_FIGURE or VIEW_FIGURE.
        catalog: The snapshot's objects, see diagrams.catalog.
        names: The objects to add.
        taken: The ids in use, which the new ones join.

    Returns:
        The names of the objects added.
    """
    kind = diagrams.FIGURE_KINDS[figure_type]
    objects = catalog[kind.objects]
    _require_known(kind.key, objects, names)
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


def _add_routines(diagram: dict, catalog: dict, names: list, taken: set) -> list:
    """Lists routines on the diagram's routine group figure, adding the
    figure (not placed yet) where there is none.

    Routines the diagram lists already, on any of its routine group
    figures, are left where they are. The figure grows with its list unless
    it is sized by hand.

    Returns:
        The names of the routines added.
    """
    routines = catalog["routines"]
    _require_known("routine", routines, names)
    groups = [figure for figure in diagram["figures"] if diagrams.is_routine_group_figure(figure)]
    shown = {name.lower() for group in groups for name in diagrams.figure_routines(group)}
    added = list(dict.fromkeys(
        routines[name.lower()]["name"] for name in names if name.lower() not in shown
    ))
    if not added:
        return []
    if groups:
        group = groups[0]
        group["routines"] = diagrams.figure_routines(group) + added
        # A placed group is not laid out again: its card grows here.
        _size_figure(group, diagrams.figure_objects(group, catalog), grow=True)
    else:
        diagram["figures"].append(
            _new_figure(
                diagrams.ROUTINE_GROUP_FIGURE, diagrams.ROUTINE_GROUP_NAME, taken, routines=added
            )
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
            references = foreign_key.get("references") or {}
            other_schema = references.get("schema")
            if other_schema is not None and other_schema.lower() != schema_name.lower():
                continue
            end = figure_of.get((references.get("table") or "").lower())
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


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
    if not _is_number(figure.get("width")) or (grow and not manual and figure["width"] < width):
        figure["width"] = width
    if not (manual and _is_number(figure.get("height"))):
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
        The names of the tables, views and routine groups placed.
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
        placed = _is_number(figure.get("left")) and _is_number(figure.get("top"))
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
        boxes.append((left, top, left + figure.get("width", 0), top + figure.get("height", 0)))
    for layer in layers.values():
        if all(_is_number(layer.get(key)) for key in ("left", "top", "width", "height")):
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
        diagram["width"] = float(max(right, width if _is_number(width) else 0))
        diagram["height"] = float(max(bottom, height if _is_number(height) else 0))
    return [diagrams.figure_object_name(figure) for figure, _ in moving]


def _fill(
    diagram: dict,
    document: dict,
    tables: list,
    views: Optional[list],
    routines: Optional[list],
    keep_placed: bool,
) -> tuple:
    """Adds the figures of the tables, views and routines, the missing
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
    added += _add_object_figures(diagram, diagrams.VIEW_FIGURE, catalog, views or [], taken)
    added += _add_routines(diagram, catalog, routines or [], taken)
    connections_added = _add_connections(diagram, catalog["tables"], document["schema"], taken)
    placed = _place(diagram, catalog, keep_placed)
    return added, connections_added, placed


def create_diagram(
    schema_project_path: str,
    name: str,
    tables: list = None,
    lsp_path: str = None,
    views: list = None,
    routines: list = None,
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
    _fill(diagram, document, names, views, routines, keep_placed=False)
    if not diagram["figures"]:
        diagram["width"], diagram["height"] = 1000.0, 800.0
    rows.append({"name": name, "format": diagrams.DIAGRAM_FORMAT, "document": diagram})
    return {
        "name": name,
        "figures": len(diagram["figures"]),
        "connections": len(diagram["connections"]),
        "files": _write(project, rows),
    }


def layout_diagram(
    schema_project_path: str,
    name: str,
    tables: list = None,
    keep_placed: bool = True,
    lsp_path: str = None,
    views: list = None,
    routines: list = None,
) -> dict:
    """Adds tables, views and routines to a diagram and lays its figures out.

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

    Returns:
        {name, added: the objects added, placed: the figures laid out (the
        tables, views and routine groups they show), connections_added,
        files}.
    """
    project = _Project.load(schema_project_path)
    rows = _rows(project)
    if not any(_is_diagram(row, name) for row in rows):
        raise ValueError(f"The project has no diagram `{name}`.")
    document, rows = _current_snapshot(project, rows, lsp_path)
    diagram = next(row for row in rows if _is_diagram(row, name))["document"]
    added, connections_added, placed = _fill(
        diagram, document, tables or [], views, routines, keep_placed
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


def _import_into(project_path: str, model, schema: dict, lsp_path: str = None) -> dict:
    """Writes a schema of a Workbench model into a new project's script."""
    imported = workbench.schema_sql(model, schema)
    Part = sections_lib.Part
    files = []
    files += sections_lib.write_section_parts(project_path, "140", [
        Part("140-10_tables.sql", "TABLEs", imported.tables),
        Part("140-20_table_additions.sql", "Foreign keys added once all TABLEs exist",
             imported.table_additions),
        Part("140-30_inserts.sql", "INSERTs", imported.inserts),
    ])
    files += sections_lib.write_section_parts(project_path, "150", [
        Part("150-10_views.sql", "VIEWs", imported.views),
        Part("150-20_procedures_functions.sql", "PROCEDUREs and FUNCTIONs", imported.routines),
        Part("150-30_triggers.sql", "TRIGGERs", imported.triggers),
        Part("150-40_events.sql", "EVENTs", imported.events),
    ])
    files += sections_lib.write_section_parts(project_path, "170", [
        Part("170_roles.sql", "ROLEs and GRANTs", imported.roles),
    ])

    project = _Project.load(project_path)
    schema_document, rows, _ = _replace_snapshot(project, _rows(project), lsp_path)
    documents, warnings = workbench.diagram_documents(model, schema)
    warnings = imported.warnings + warnings
    for document in documents:
        diagrams.validate_diagram(document)
        if diagrams.reconcile_diagram(document, schema_document):
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

    project_path = management.create_schema_project_folder(
        schema_name=model_schema["name"],
        target_path=target_path,
        copyright_holder=copyright_holder,
        license=license if license is not None else "None",
        overwrite_existing=overwrite_existing,
        allow_special_chars=True,
        enforce_target_path=enforce_target_path,
        copyrights=copyrights,
    )
    try:
        result = _import_into(project_path, model, model_schema, lsp_path)
    except BaseException:
        shutil.rmtree(project_path, ignore_errors=True)
        raise
    return {
        "project_path": project_path,
        "schema": model_schema["name"],
        **result,
        "scripts_not_imported": model.scripts,
    }
