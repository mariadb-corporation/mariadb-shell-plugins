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

"""Where the statement of a new schema object goes in an MSM project.

msm owns the layout of a project, so it tells the language server's add ops
(add_table, add_sequence, add_view, add_routine, add_trigger, add_event)
where each new statement goes and in what form (lsp-server
docs/edit-format.md, "Placement": the members `at` and `form`). The binary
writes the statement's text there.

A table or sequence goes into Section 140, any other object into Section
150, which every deployment runs again. Within a section the kinds keep the
order split_sections writes their breakout files in: sequences and tables;
routines, views (which may call a function), triggers, events. A new
object goes:

1. after the section's last object of its kind (a routine: of either kind),
   in that object's file;
2. where the section is kept in breakout files and has none of the kind,
   inline at the SOURCE line of the next kind's file (below the last SOURCE
   line where none follows); with one, the rules below apply within it,
   its end taking the section's;
3. else after the section's last object of a kind before its own;
4. else above the section's first object of a kind after its own (a
   sequence above the first table, whose column defaults may use it),
   above its comments and the DROP ... IF EXISTS before it;
5. else at the end of the section.

A table or sequence is a plain CREATE; any other object CREATE OR REPLACE,
unless the object of its kind it follows drops itself first, as its new
neighbour then does too.
"""

import bisect
from dataclasses import dataclass
from typing import Optional

from msm_plugin.lib import diagrams, lsp, sections, source
from msm_plugin.lib import statements as statements_lib

# The add ops msm places, and the kind of object each one adds
_ADD_OPS = {
    "add_table": "table",
    "add_sequence": "sequence",
    "add_view": "view",
    "add_routine": "routine",
    "add_trigger": "trigger",
    "add_event": "event",
}
_PLACEMENT_MEMBERS = ("at", "form")

# The kinds of each section, in the order sections.SECTION_PARTS runs them;
# a sequence shares the tables' breakout file, ahead of them
_SECTION_KINDS = {
    "140": ("sequence", "table"),
    "150": ("routine", "view", "trigger", "event"),
}
_SECTION_OF = {kind: section_id for section_id, kinds in _SECTION_KINDS.items() for kind in kinds}
# The statements.object_kind of a kind's breakout file, where it is not the
# kind's name upper-cased
_PART_KIND = {"sequence": "TABLE", "routine": "PROCEDURE"}


def _part_files(section_id: str, kind: str) -> tuple:
    """(the breakout file of a kind's part of a section, those of the parts
    after it)."""
    parts = [part for part in sections.SECTION_PARTS[section_id] if part.kinds is not None]
    statement_kind = _PART_KIND.get(kind, kind.upper())
    index = next(i for i, part in enumerate(parts) if statement_kind in part.kinds)
    return parts[index].file_name, [part.file_name for part in parts[index + 1:]]


def _run_order(item: dict, includes: list) -> tuple:
    """Where an object runs: its SOURCE line's position in the script, then
    its own; positions compare as (line, character)."""
    start = item["range"]["start"]
    source_index = item.get("source")
    if isinstance(source_index, int):
        directive = includes[source_index]["range"]["start"]
        return directive["line"], directive["character"], start["line"], start["character"]
    return start["line"], start["character"], -1, -1


@dataclass
class _Object:
    """An object of the outline the edits may name: its kind, outline item,
    and its name in lower case as the edits before an add left it."""

    kind: str
    item: dict
    name: str


class _Placer:
    """The objects of a project's outline by section and file, and the texts
    of those files around them."""

    def __init__(self, project, binary: str, script: str, outlined: dict, schema_name: str):
        self.binary = binary
        self.project = project
        self.includes = outlined.get("includes", [])
        # By `at.file`: "" for the script, else the outline's includes[].file
        self.texts = {"": script}
        self.starts = {}
        self.entries = {}
        # Each section's body as (offset, end offset, first line, last line)
        self.sections = {}
        for section_id in _SECTION_KINDS:
            section = project.sections.get(section_id)
            if section is not None:
                start = section["start_position"] + section["sql_start"]
                end = section["end_position"]
                self.sections[section_id] = (start, end, self.line("", start), self.line("", end))
        # The objects of the project's schema (and of the part of the script
        # before its USE), in the order they run
        found = [
            (_run_order(item, self.includes), _Object(kind, item, (item.get("name") or "").lower()))
            for schema in outlined.get("schemas", [])
            if schema.get("name") is None or diagrams.same_name(schema["name"], schema_name)
            for kind in _SECTION_OF
            for item in schema.get(kind + "s", [])
            if "range" in item
        ]
        self.objects = [entry for _, entry in sorted(found, key=lambda pair: pair[0])]

    # -- Files

    def text(self, key: str) -> str:
        if key not in self.texts:
            path = source.checked_include_path(source.resolve_include_path(key, self.project.development))
            self.texts[key] = self.project.read(path)[1]
        return self.texts[key]

    def _starts(self, key: str) -> list:
        if key not in self.starts:
            self.starts[key] = source.line_starts(self.text(key))
        return self.starts[key]

    def offset(self, key: str, position: dict) -> int:
        return source.position_offset(self.text(key), self._starts(key), position)

    def line(self, key: str, offset: int) -> int:
        return bisect.bisect_right(self._starts(key), offset) - 1

    def file_key(self, item: dict) -> str:
        source_index = item.get("source")
        return self.includes[source_index]["file"] if isinstance(source_index, int) else ""

    def _entries(self, key: str) -> list:
        if key not in self.entries:
            text = self.text(key)
            self.entries[key] = statements_lib.sql_entries(text, lsp.split(self.binary, text))
        return self.entries[key]

    # -- Sections

    def _run_line(self, item: dict) -> int:
        """The script line an object runs from: its own, or its SOURCE line's."""
        return _run_order(item, self.includes)[0]

    def _in_section(self, section_id: str, line: int) -> bool:
        bounds = self.sections.get(section_id)
        return bounds is not None and bounds[2] <= line <= bounds[3]

    def section_objects(self, section_id: str, kinds, file_key: Optional[str] = None) -> list:
        return [entry for entry in self.objects
                if entry.kind in kinds and self._in_section(section_id, self._run_line(entry.item))
                and (file_key is None or self.file_key(entry.item) == file_key)]

    def section_includes(self, section_id: str) -> list:
        """The includes of a section's SOURCE lines, in order."""
        return [
            include for include in self.includes
            if not include.get("error")
            and self._in_section(section_id, include["range"]["start"]["line"])
        ]

    def follow(self, edit: dict) -> None:
        """Takes in an edit that adds nothing: the binary takes no place in
        the text a drop removes, so the object goes; a rename gives it the
        name a later drop knows it by. Edits naming no single object of the
        outline are left to the binary."""
        action, _, kind = edit.get("op", "").partition("_")
        name = edit.get(kind)
        if action not in ("drop", "rename") or kind not in _SECTION_OF or not isinstance(name, str):
            return
        matches = [entry for entry in self.objects
                   if entry.kind == kind and entry.name == name.lower()
                   and (kind != "routine" or edit.get("kind") in (None, entry.item.get("kind")))]
        if len(matches) != 1:
            return
        if action == "drop":
            self.objects.remove(matches[0])
            if kind == "table":
                # drop_table removes the table's triggers too
                self.objects = [entry for entry in self.objects
                                if entry.kind != "trigger"
                                or (entry.item.get("table") or "").lower() != matches[0].name]
        elif isinstance(edit.get("new_name"), str):
            matches[0].name = edit["new_name"].lower()

    # -- Places

    def after(self, item: dict) -> dict:
        return {"file": self.file_key(item), "after": dict(item["range"]["end"])}

    def _drop_before(self, kind: str, item: dict) -> Optional[int]:
        """Where the DROP ... IF EXISTS of an object right before its CREATE
        starts, or None."""
        key = self.file_key(item)
        entries = self._entries(key)
        begin = self.offset(key, item["range"]["start"])
        index = bisect.bisect_right(entries, begin, key=lambda entry: entry[0]) - 1
        if index < 1 or begin >= entries[index][1]:
            return None
        previous = entries[index - 1]
        dropped = statements_lib.dropped_if_exists(self.text(key)[previous[0]:previous[1]])
        kinds = ("PROCEDURE", "FUNCTION") if kind == "routine" else (kind.upper(),)
        if dropped and dropped[0] in kinds and diagrams.same_name(dropped[1], item.get("name") or ""):
            return previous[0]
        return None

    def above(self, section_id: str, kind: str, item: dict) -> dict:
        """At the first line of an object's statement: its comment lines
        right above it and the DROP ... IF EXISTS of it before it included."""
        key = self.file_key(item)
        begin = self._drop_before(kind, item)
        if begin is None:
            begin = self.offset(key, item["range"]["start"])
        text, starts = self.text(key), self._starts(key)

        def line_text(number):
            return text[starts[number]:starts[number + 1] if number + 1 < len(starts) else len(text)]

        first = line = self.line(key, begin)
        floor = self._floor(section_id, item)
        while line - 1 > floor and statements_lib._is_comment_line(line_text(line - 1)):
            line -= 1
        while line < first and not line_text(line).strip():
            line += 1
        return {"file": key, "line": line}

    def _floor(self, section_id: str, item: dict) -> int:
        """The last line above an object that is not a statement's own: the
        section's banner in the script, the header its SOURCE line's slice
        leaves out of an include."""
        source_index = item.get("source")
        if not isinstance(source_index, int):
            return self.sections[section_id][2]
        include = self.includes[source_index]
        key = include["file"]
        bound = (include.get("slice") or "[]").strip()[1:-1].partition(":")[0].strip()
        start = source.get_source_slice_offset(self.text(key), bound, False)
        return -1 if start is None else self.line(key, start) - 1

    def section_end(self, section_id: str) -> dict:
        """At the end of a section's statements: the line below its last
        one, else the line below its banner. Not below the text after them:
        a binary before lsp-server's fix of 2026-11 takes no line after a
        block comment (a template's example) up to the next comment line,
        the next section's banner."""
        start, end, banner, _ = self.sections[section_id]
        ends = [entry_end for begin, entry_end, _ in self._entries("") if start <= begin < end]
        return {"file": "", "line": self.line("", max(ends)) + 1 if ends else banner + 1}

    def file_end(self, include: dict) -> dict:
        return {"file": include["file"], "line": len(self._starts(include["file"])) - 1}

    # -- The rules

    def place(self, kind: str) -> tuple:
        """(at, form) of a new object of that kind."""
        section_id = _SECTION_OF[kind]
        if section_id not in self.sections:
            raise ValueError(f"The development script has no MSM Section {section_id}.")
        kinds = _SECTION_KINDS[section_id]
        position = kinds.index(kind)
        form = "create" if section_id == "140" else "or_replace"

        same = self.section_objects(section_id, (kind,))
        if same:
            last = same[-1].item
            if section_id == "150" and self._drop_before(kind, last) is not None:
                form = "drop_first"
            return self.after(last), form

        in_file = None
        includes = self.section_includes(section_id)
        if includes:
            own, later = _part_files(section_id, kind)
            # By their file's name, the first SOURCE line of a name kept
            by_name = {}
            for include in includes:
                by_name.setdefault(include["file"].rsplit("/", 1)[-1], include)
            in_file = by_name.get(own)
            if in_file is None:
                following = next((by_name[name] for name in later if name in by_name), None)
                if following is not None:
                    return {"file": "", "line": following["range"]["start"]["line"]}, form
                last_line = includes[-1]["range"]["end"]["line"]
                return {"file": "", "line": last_line + 1}, form
        file_key = in_file["file"] if in_file is not None else None

        before = self.section_objects(section_id, kinds[:position], file_key)
        if before:
            return self.after(before[-1].item), form
        later_objects = self.section_objects(section_id, kinds[position + 1:], file_key)
        if later_objects:
            return self.above(section_id, later_objects[0].kind, later_objects[0].item), form
        if in_file is not None:
            return self.file_end(in_file), form
        return self.section_end(section_id), form


def run_rank(op: str) -> tuple:
    """Where the kind of an add_* or replace_* op runs: Section 140's kinds
    before 150's, each section's in their order."""
    kind = op.partition("_")[2]
    section_id = _SECTION_OF.get(kind)
    if section_id is None:
        return (len(_SECTION_KINDS), 0)
    return (list(_SECTION_KINDS).index(section_id), _SECTION_KINDS[section_id].index(kind))


def check_request(binary: str, edits: list) -> None:
    """Refuses, before anything is written, edits that place statements
    themselves (msm does), and add ops a binary without placement
    (lsp-server #49) would put where it likes."""
    for index, edit in enumerate(edits):
        given = [member for member in _PLACEMENT_MEMBERS if member in edit]
        if given:
            raise ValueError(
                f"Edit {index + 1} ({edit['op']}) gives "
                + " and ".join(f"`{member}`" for member in given)
                + ": msm places the statements of new objects itself, where the project "
                "keeps objects of their kind. Leave them out."
            )
    if any(edit["op"] in _ADD_OPS for edit in edits) and not lsp.places_statements(binary):
        raise lsp.LspError(
            f"The MariaDB SQL language server's binary `{binary}` cannot put a new object's "
            "statement where msm keeps it (lsp-server #49). Update "
            f"{lsp.LSP_BINARY_NAME}, or the MariaDB Shell, which comes with it."
        )


def placed(project, binary: str, script: str, edits: list, schema_name: str) -> list:
    """The edits with `at` and `form` on each add op.

    Args:
        project: The modeling._Project.
        binary: The mariadb-lsp binary; check_request passed it.
        script: The development script as the request sends it.
        edits: The edits.
        schema_name: The project's schema.

    Returns:
        A copy of the edits, the add ops placed; the edits themselves where
        none adds an object.
    """
    if not any(edit["op"] in _ADD_OPS for edit in edits):
        return edits
    # The positions are those of the files before the request, as the
    # binary takes every `at`
    placer = _Placer(project, binary, script, lsp.outline(binary, script, project.development),
                     schema_name)
    result = []
    for edit in edits:
        if edit["op"] in _ADD_OPS:
            at, form = placer.place(_ADD_OPS[edit["op"]])
            edit = {**edit, "at": at, "form": form}
        else:
            placer.follow(edit)
        result.append(edit)
    return result
