# Copyright (c) 2026, MariaDB plc.
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""Changing the REST definition of a project: MSM Section 180.

Clients (the extension's REST dialogs, agents) hand msm the statements of a
REST service, schema or object, or name one to drop; msm alone decides where
in the project they go and in what form:

- set_rest_statements takes CREATE statements. Section 180 runs on every
  deployment, so each is written as CREATE OR REPLACE. An item Section 180
  creates already has its CREATE replaced where it stands, and its other
  CREATEs, DROPs and ALTERs (but those that only link or unlink auth apps)
  removed, as they would override what was saved. A new item goes after
  the statements of its parent (a service after the whole definition),
  into the file they are in, a breakout file too.
- drop_rest_object removes every statement of an item and of what is on it, with
  the comments right above each. It never writes a DROP: Section 280 of the
  next release's update script drops what the release no longer defines.

Every statement of each item comes from mariadb-lsp's outline (feature
rest_statements) and its delimiter from the binary's splitter, so msm reads
no REST SQL itself, but for the words of a CREATE before its item's name
(OR REPLACE, IF NOT EXISTS). The result is checked by
outlining it again: the REST definition may differ only in the items named,
and no statement may stop being applied; otherwise every file is put back.
"""

import bisect
import collections
import os
import re
from typing import Optional

from msm_plugin.lib import diagrams, editing, lsp, management, modeling, rest_paths, rest_removal
from msm_plugin.lib import source
from msm_plugin.lib import statements as statements_lib
from msm_plugin.lib.project_changes import changes_project

REST_DEFINITION_SECTION_ID = rest_removal.REST_DEFINITION_SECTION_ID
STATEMENTS_FEATURE = "rest_statements"

# The REST object kinds of the outline, as the statements write them.
OBJECT_KINDS = ("view", "procedure", "function")
DROP_KINDS = ("service", "schema", "object")

# The start of a REST CREATE statement up to its item's name: whether it
# replaces, and whether it is IF NOT EXISTS.
_CREATE = re.compile(r"^CREATE\b(\s+OR\s+REPLACE\b)?", re.IGNORECASE)
_IF_NOT_EXISTS = re.compile(r"\bIF\s+NOT\s+EXISTS\s*$", re.IGNORECASE)
# The `file:line: ` a note on an included statement starts with.
_NOTE_PLACE = re.compile(r"^[^\s:]+:\d+: ")
# What an item's fingerprint leaves out: where it is written, and what is
# on it, which has fingerprints of its own.
_PLACE_MEMBERS = {"range", "selection_range", "source", "statements", "leading_comments"}
_CHILD_MEMBERS = {"schemas", "objects", "content_sets"}


def lists_statements(binary: str) -> bool:
    """Whether the binary's outline tells the REST definition as msm needs
    it and lists every statement of each REST item (rest_statements)."""
    return lsp.outlines_rest(binary) and STATEMENTS_FEATURE in lsp.outline_features(binary)


def require_lsp(lsp_path: Optional[str] = None) -> str:
    """The mariadb-lsp binary, where it lists_statements; raises otherwise."""
    binary = rest_removal.require_rest_lsp(lsp_path)
    if STATEMENTS_FEATURE not in lsp.outline_features(binary):
        raise lsp.LspError(
            f"The REST definition is changed with the MariaDB SQL language server, and the "
            f"installed one ({binary}) cannot list the statements of REST items yet. Update "
            f"{lsp.LSP_BINARY_NAME}, or the MariaDB Shell, which comes with it."
        )
    return binary


# -----------------------------------------------------------------------------
# The REST items of an outline


def _items(outlined: dict) -> dict:
    """The REST items of an outline by key, each {kind, node}.

    Keys: ("service", s), ("schema", s, p), ("object", s, p, o) and
    ("content_set", s, c), each path as rest_paths.path_key has it.
    """
    items = {}
    for service in (outlined.get("rest") or {}).get("services", []):
        if not service.get("path"):
            continue
        s = rest_paths.path_key(service["path"])
        items[("service", s)] = {"kind": "service", "node": service}
        for content_set in service.get("content_sets", []):
            name = content_set.get("name") or ""
            items[("content_set", s, rest_paths.path_key(name))] = {
                "kind": "content_set", "node": content_set,
            }
        for schema in service.get("schemas", []):
            if not schema.get("path"):
                continue
            p = rest_paths.path_key(schema["path"])
            items[("schema", s, p)] = {"kind": "schema", "node": schema}
            for item in schema.get("objects", []):
                if item.get("path"):
                    items[("object", s, p, rest_paths.path_key(item["path"]))] = {
                        "kind": item.get("kind"), "node": item,
                    }
    return items


def _in_subtree(key: tuple, root: tuple) -> bool:
    """Whether the item of key is root or on it."""
    if root[0] == "service":
        return key[1] == root[1]
    if root[0] == "schema":
        return key[0] in ("schema", "object") and key[1:3] == root[1:3]
    return key == root


def _without_place(value):
    if isinstance(value, dict):
        return {
            name: _without_place(member) for name, member in value.items()
            if name not in _PLACE_MEMBERS
        }
    if isinstance(value, list):
        return [_without_place(member) for member in value]
    return value


def _fingerprint(item: dict):
    """What a deployment makes of an item, without where it is written."""
    node = {name: member for name, member in item["node"].items() if name not in _CHILD_MEMBERS}
    return item["kind"], _without_place(node)


def _describe(key: tuple, items: dict) -> str:
    """An item for people: its kind and the paths down to it."""
    names, at = [], key
    while at is not None:
        item = items.get(at)
        names.append((item["node"].get("path") or item["node"].get("name")) if item else at[-1])
        at = _parent_key(at)
    label = {"service": "REST service", "schema": "REST schema", "object": "REST object",
             "content_set": "REST content set"}[key[0]]
    return f"{label} {' '.join(reversed(names))}"


def _parent_key(key: tuple) -> Optional[tuple]:
    """The key of the item an item is on."""
    if key[0] in ("schema", "content_set"):
        return ("service", key[1])
    if key[0] == "object":
        return ("schema", key[1], key[2])
    return None


def _unread(outlined: dict) -> collections.Counter:
    """The notes on statements the outline did not apply, without their places."""
    return collections.Counter(
        (note.get("kind"), _NOTE_PLACE.sub("", str(note.get("message", ""))))
        for note in diagrams.unread_statements(outlined)
    )


# -----------------------------------------------------------------------------
# The files the statements are in


class _Files:
    """The text of each file the outline's statements are in, and the spans
    of new text to write into each."""

    def __init__(self, project, binary: str, outlined: dict):
        self.project = project
        self.binary = binary
        self.includes = outlined.get("includes", [])
        self.texts = {}
        self.line_starts = {}
        self.entries = {}
        self.spans = collections.defaultdict(list)
        # The statements removed, by _place
        self.removed = set()

    def path(self, source_index) -> str:
        """The file of a statement: the development script, or an include."""
        if not isinstance(source_index, int):
            return self.project.dev_file
        name = self.includes[source_index]["file"]
        return source.checked_include_path(
            source.resolve_include_path(name, self.project.development)
        )

    def text(self, path: str) -> str:
        if path not in self.texts:
            text = self.project.script if path == self.project.dev_file else self.project.read(path)[1]
            self.texts[path] = text
            self.line_starts[path] = editing.line_starts(text)
        return self.texts[path]

    def offset(self, path: str, position: dict) -> int:
        return editing._offset(self.text(path), self.line_starts[path], position)

    def entry(self, path: str, offset: int) -> tuple:
        """The (begin, end after the delimiter, delimiter) of the statement
        at offset, as the shell splits the file."""
        if path not in self.entries:
            text = self.text(path)
            self.entries[path] = statements_lib.sql_entries(text, lsp.split(self.binary, text))
        entries = self.entries[path]
        index = bisect.bisect_right(entries, offset, key=lambda entry: entry[0]) - 1
        found = entries[index] if index >= 0 and offset < entries[index][1] else None
        if found is None:
            raise ValueError(
                f"The statement at character {offset} of `{path}` is not one the shell runs."
            )
        return found

    def statement(self, statement: dict) -> tuple:
        """(path, text, begin, end, delimiter) of a statement: end after its
        delimiter and a line comment after it on its line."""
        path = self.path(statement.get("source"))
        text = self.text(path)
        begin = self.offset(path, statement["range"]["start"])
        _, end, delimiter = self.entry(path, begin)
        return path, text, begin, statements_lib._comment_after(text, end), delimiter

    def removal(self, statement: dict) -> tuple:
        """(path, start, end) of a statement with its delimiter, the comments
        right above it and its lines where it has them to itself."""
        path, text, start, end, _ = self.statement(statement)
        if statement.get("leading_comments"):
            start = min(start, self.offset(path, statement["leading_comments"]["start"]))
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end < 0 else line_end
        if not text[line_start:start].strip() and not text[end:line_end].strip():
            start, end = line_start, min(line_end + 1, len(text))
            # Between two blank lines, one of them goes too
            blank_before = start == 0 or (start >= 2 and text[start - 2] == "\n")
            if blank_before and end < len(text) and text[end] == "\n":
                end += 1
        return path, start, end

    def remove(self, statement: dict) -> None:
        """Removes a statement (removal)."""
        self.add(*self.removal(statement), "")
        self.removed.add(_place(statement))

    def end_of(self, statement: dict) -> tuple:
        """(path, offset, delimiter) right after a statement's line."""
        # Every statement ends with its delimiter: one without, the last of
        # an include or of the script, would run into what follows it, and
        # the project's snapshot could not be made in the first place
        path, text, _, end, delimiter = self.statement(statement)
        line_end = text.find("\n", end)
        if line_end >= 0 and not text[end:line_end].strip():
            end = line_end
        return path, end, delimiter

    def section_end(self) -> tuple:
        """(path, offset, delimiter) at the end of Section 180's own text."""
        section = self.project.sections.get(REST_DEFINITION_SECTION_ID)
        if section is None:
            raise ValueError(f"The development script has no MSM Section {REST_DEFINITION_SECTION_ID}.")
        start = section["start_position"] + section["sql_start"]
        body = self.project.script[start:section["end_position"]]
        return self.project.dev_file, start + len(body.rstrip()), ";"

    def add(self, path: str, start: int, end: int, new_text: str) -> None:
        self.spans[path].append((start, end, new_text))

    def new_texts(self) -> dict:
        """{path: (old, new)} of every file with spans."""
        texts = {}
        for path, spans in self.spans.items():
            spans = editing.checked_spans(spans, f"msm's changes to `{path}` overlap.")
            old = self.text(path)
            texts[path] = (old, editing.apply_spans(old, spans))
        return texts


def _order(statement: dict, includes: list) -> tuple:
    """Where a statement runs, for ordering statements across files."""
    position = statement["range"]["start"]
    source_index = statement.get("source")
    if isinstance(source_index, int):
        directive = includes[source_index].get("range", {}).get("start", {})
        return (directive.get("line", 0), directive.get("character", 0),
                position["line"], position["character"])
    return position["line"], position["character"], 0, 0


def _place(statement: dict) -> tuple:
    """A statement's place, which tells it apart from the others."""
    start = statement["range"]["start"]
    return statement.get("source"), start["line"], start["character"]


def _subtree_statements(items: dict, root: tuple, includes: list) -> list:
    """Every statement of an item and of what is on it, once each, in the order they run."""
    seen, found = set(), []
    for key, item in items.items():
        if not _in_subtree(key, root):
            continue
        for statement in item["node"].get("statements", []):
            place = _place(statement)
            if place not in seen:
                seen.add(place)
                found.append(statement)
    return sorted(found, key=lambda statement: _order(statement, includes))


# -----------------------------------------------------------------------------
# A statement a client gives


def _read_statement(binary: str, sql, index: int) -> dict:
    """The REST item one statement a client gave creates.

    Returns:
        {key, kind, paths (the service's, the schema's, the object's, as far
        as the item goes), text}: text is the statement in the form Section
        180 needs, without its delimiter.
    """
    where = f"Statement {index + 1}"
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError(f"{where} is empty: pass one CREATE REST statement each.")
    if len([entry for entry in lsp.split(binary, sql) if entry.get("kind") == "sql"]) != 1:
        raise ValueError(f"{where} has to be one statement, a CREATE REST ... of one item.")
    outlined = lsp.outline(binary, sql)
    unread = diagrams.unread_statements(outlined)
    if unread:
        raise ValueError(
            f"{where} cannot be read: {unread[0].get('message')}. A REST schema names its "
            "service (ON SERVICE /svc), an object its service and schema (ON SERVICE /svc "
            "SCHEMA /schema)."
        )
    items = _items(outlined)
    created = [(key, item) for key, item in items.items() if _creates(item)]
    if len(created) != 1:
        raise ValueError(f"{where} has to be one CREATE REST SERVICE, SCHEMA, VIEW, PROCEDURE or "
                         "FUNCTION statement.")
    key, item = created[0]
    if key[0] == "content_set" or (key[0] == "object" and item["kind"] not in OBJECT_KINDS):
        raise ValueError(f"{where}: msm takes CREATE REST SERVICE, SCHEMA, VIEW, PROCEDURE and "
                         "FUNCTION statements only.")
    statement = _creates(item)[0]
    starts = editing.line_starts(sql)
    start = editing._offset(sql, starts, statement["range"]["start"])
    name = editing._offset(sql, starts, statement["selection_range"]["start"])
    end = editing._offset(sql, starts, statement["range"]["end"])
    text = sql[start:end]
    head = text[: name - start]
    if _IF_NOT_EXISTS.search(head):
        raise ValueError(
            f"{where} is CREATE ... IF NOT EXISTS, which would never update a deployed "
            f"endpoint: Section {REST_DEFINITION_SECTION_ID} runs on every deployment and "
            "writes CREATE OR REPLACE."
        )
    found = _CREATE.match(text)
    if found is None:
        raise ValueError(f"{where} has to start with CREATE.")
    if not found.group(1):
        text = text[: found.end()] + " OR REPLACE" + text[found.end():]
    paths, at = [], key
    while at is not None:
        paths.insert(0, items[at]["node"]["path"])
        at = _parent_key(at)
    return {"key": key, "kind": item["kind"], "paths": paths, "text": text.strip()}


def _result_entry(statement: dict, files: _Files, items: dict, replaced: bool) -> dict:
    """What set_rest_statements says about a statement it wrote."""
    paths = statement["paths"]
    entry = {
        "kind": statement["kind"],
        "service": paths[0],
        "schema": paths[1] if len(paths) > 1 else None,
        "object": paths[2] if len(paths) > 2 else None,
        "file": None,
        "line": None,
        "replaced": replaced,
    }
    creates = _creates(items.get(statement["key"]))
    if creates:
        path = files.path(creates[0].get("source"))
        entry["file"] = os.path.normpath(path)
        entry["line"] = creates[0]["range"]["start"]["line"] + 1
    return entry


# -----------------------------------------------------------------------------
# The check of the result


def _check_result(before: dict, after: dict, changing: set, gone: set, kept: set) -> None:
    """Refuses a result whose REST definition changed more than asked.

    Args:
        before: The items before (_items).
        after: The items after.
        changing: Keys of the items that may change, appear or go.
        gone: Keys that must be gone.
        kept: Keys that must be there, created by the result.
    """
    for key in sorted(set(before) | set(after)):
        if key in changing:
            continue
        old, new = before.get(key), after.get(key)
        if (old is None) != (new is None) or (old and _fingerprint(old) != _fingerprint(new)):
            raise ValueError(
                f"The change would also change the {_describe(key, before if old else after)}, "
                f"which it is not about: a statement of Section {REST_DEFINITION_SECTION_ID} "
                "that names no service or schema of its own may now take another one. Nothing "
                "was written."
            )
    for key in gone:
        if key in after:
            raise ValueError(
                f"The {_describe(key, after)} is still there afterwards (a statement of it msm "
                "could not remove, e.g. one a SOURCE line includes only in part). Nothing was "
                "written."
            )
    for key in kept:
        item = after.get(key)
        if not _creates(item):
            raise ValueError(
                f"The new statement of the {_describe(key, after) if item else key[-1]} does not "
                f"take effect where msm put it in Section {REST_DEFINITION_SECTION_ID}. Nothing "
                "was written."
            )


def _checking(binary: str, before_outline: dict, before: dict, changing: set, gone: set,
              kept: set):
    """The check write_texts runs on the files as written: returns their outline.

    Args:
        before_outline: The outline before.
        before: Its items (_items).
    """
    unread_before = _unread(before_outline)

    def check(project):
        outlined = _outline(project, binary)
        new_notes = _unread(outlined) - unread_before
        if new_notes:
            _, message = next(iter(new_notes))
            raise ValueError(
                f"A statement would no longer be applied afterwards ({message}): it may depend "
                "on a USE REST or a statement that went. Nothing was written."
            )
        _check_result(before, _items(outlined), changing, gone, kept)
        return outlined

    return check


def _outline(project, binary: str) -> dict:
    """The project's outline, as the snapshot is made from it."""
    script, _ = modeling._outline_input(project)
    return lsp.outline(binary, script, project.development)


def _start(schema_project_path: str, base_hash: str, lsp_path: Optional[str]) -> tuple:
    """(project, binary) once base_hash is checked."""
    project = modeling._Project.load(schema_project_path)
    editing.check_base_hash(project, base_hash)
    return project, require_lsp(lsp_path)


def _move_years(schema_project_path: str, project, binary: str, outlined: dict) -> tuple:
    """(project, outline) once the copyright years moved, after the request
    was checked: a refused call leaves them. As edit_schema does, a change
    of its own, not undone."""
    if management.update_copyright_years(schema_project_path):
        project = modeling._Project.load(schema_project_path)
        outlined = _outline(project, binary)
    return project, outlined


def _answer(rows: list, written: list, notes: list, **members) -> dict:
    return {"rows": rows, "files": written, "migration": [], "migration_file": None,
            "notes": notes, **members}


# -----------------------------------------------------------------------------
# Setting statements


def _check_replaces(replaces) -> Optional[tuple]:
    """The key of the REST object replaces names, or None."""
    if replaces is None:
        return None
    if not hasattr(replaces, "get"):
        raise ValueError("replaces has to be {kind, service, schema, object}.")
    kind = replaces.get("kind")
    if kind in ("service", "schema"):
        raise ValueError(
            f"A REST {kind} cannot be renamed here: the statements on it name its path. "
            "Drop it and create it under the new path instead."
        )
    if kind != "object" or not all(
        isinstance(replaces.get(member), str) and replaces.get(member)
        for member in ("service", "schema", "object")
    ):
        raise ValueError("replaces has to name a REST object: {kind: \"object\", service, schema, object}.")
    return ("object", rest_paths.path_key(replaces["service"]),
            rest_paths.path_key(replaces["schema"]), rest_paths.path_key(replaces["object"]))


def _creates(item: Optional[dict]) -> list:
    return [s for s in (item or {"node": {}})["node"].get("statements", []) if s["kind"] == "create"]


def _overridden(item: dict) -> list:
    """The statements of an item a new CREATE OR REPLACE of it makes
    pointless: every CREATE but the first, its DROPs, and its ALTERs but
    those that only link or unlink auth apps."""
    creates = _creates(item)
    statements = item["node"].get("statements", [])
    overridden = [
        statement for statement in statements
        if (statement["kind"] == "create" and statement is not creates[0])
        or statement["kind"] == "drop"
        or (statement["kind"] == "alter" and not statement.get("auth_apps_only"))
    ]
    # An ALTER that also links an auth app (the outline's link has the range
    # of the statement that last linked it) would take the link along
    linking = {
        (link.get("source"), str(link.get("range")))
        for link in item["node"].get("auth_apps", [])
    }
    for statement in overridden:
        if statement["kind"] == "alter" and (
            statement.get("source"), str(statement["range"])
        ) in linking:
            raise ValueError(
                f"An ALTER REST SERVICE of {item['node'].get('path')} links an auth app and "
                "changes more: msm would have to remove it, link and all. Split it into an "
                "ALTER that only links auth apps and one for the rest, then try again."
            )
    return overridden


def _replace(files: _Files, statement: dict, text: str) -> None:
    """Replaces a statement's text, its delimiter and comments kept."""
    path = files.path(statement.get("source"))
    files.add(path, files.offset(path, statement["range"]["start"]),
              files.offset(path, statement["range"]["end"]), text)


def _new_place(files: _Files, items: dict, includes: list, placed: dict, key: tuple) -> tuple:
    """(path, offset, delimiter) where a new item's statement goes: after
    the statements on its nearest parent that has some (or was placed by
    this call), else after the whole REST definition, else at the end of
    Section 180."""
    parent = _parent_key(key)
    while parent is not None:
        if parent in placed:
            return placed[parent]
        on_it = [s for s in _subtree_statements(items, parent, includes)
                 if _place(s) not in files.removed]
        if on_it:
            return files.end_of(on_it[-1])
        parent = _parent_key(parent)
    every = sorted(
        (s for root in items if root[0] == "service"
         for s in _subtree_statements(items, root, includes) if _place(s) not in files.removed),
        key=lambda s: _order(s, includes),
    )
    return files.end_of(every[-1]) if every else files.section_end()


@changes_project
def set_rest_statements(
    schema_project_path: str,
    statements: list,
    base_hash: str,
    replaces: dict = None,
    lsp_path: str = None,
) -> dict:
    """Writes CREATE statements of REST services, schemas and objects into
    MSM Section 180, where msm keeps them (see the module's docstring).

    Args:
        schema_project_path: The project folder.
        statements: The CREATE REST SERVICE / SCHEMA / VIEW / PROCEDURE /
            FUNCTION statements, one item each; a schema names its service
            (ON SERVICE), an object its service and schema (ON SERVICE /svc
            SCHEMA /schema). A new schema goes before its objects.
        base_hash: The source_hash of the snapshot the caller knows.
        replaces: {kind: "object", service, schema, object}: the REST
            object a statement renames, whose statements go; the renamed one
            takes its place where Section 180 does not create it already.
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        edit_schema's {rows, files, migration (none), migration_file (None),
        notes}, and statements: [{kind, service, schema, object, file,
        line (1-based), replaced}] for each statement written.

    Raises:
        StaleSchemaError: For a stale base_hash.
        ValueError: For a statement that is not one CREATE of a REST item
            naming its place, an IF NOT EXISTS, two statements of one item,
            a rename of a service or schema, and a result that would change
            more than the items given.
    """
    if not isinstance(statements, list) or not statements:
        raise ValueError("No statements given: pass a list of CREATE REST statements.")
    renamed = _check_replaces(replaces)
    project, binary = _start(schema_project_path, base_hash, lsp_path)
    given = [_read_statement(binary, sql, index) for index, sql in enumerate(statements)]
    keys = [statement["key"] for statement in given]
    if len(set(keys)) != len(keys):
        raise ValueError("Two statements create the same REST item: pass one statement each.")
    if renamed is not None:
        objects = [statement for statement in given if statement["key"][0] == "object"]
        if len(objects) != 1:
            raise ValueError("replaces goes with the statement of one REST object.")

    outlined = _outline(project, binary)
    # The REST object a statement renames, where it is another one
    old = None
    if renamed is not None and renamed not in keys:
        old = _items(outlined).get(renamed)
        if not _creates(old):
            raise ValueError(
                f"Section {REST_DEFINITION_SECTION_ID} creates no REST object "
                f"{replaces['object']} on {replaces['service']} {replaces['schema']} "
                "to rename."
            )

    project, outlined = _move_years(schema_project_path, project, binary, outlined)
    items = _items(outlined)
    if old is not None:
        old = items[renamed]
    files = _Files(project, binary, outlined)
    includes = files.includes
    # Where each new item went, for the items on it that follow
    placed = {}
    changing, replaced = set(keys) | ({renamed} if old is not None else set()), {}

    def replacing(statement):
        return bool(_creates(items.get(statement["key"]))) or (
            old is not None and statement["key"][0] == "object"
        )

    # Replacements first, so no new item is placed after a statement a
    # later replacement removes; each group in the order given
    for statement in sorted(given, key=lambda statement: not replacing(statement)):
        key, text = statement["key"], statement["text"]
        item = items.get(key)
        # The renamed object goes with the one object statement
        renaming = old if key[0] == "object" else None
        if _creates(item):
            # Replaced where it stands; the renamed one goes
            _replace(files, _creates(item)[0], text)
            for other in _overridden(item) + (renaming["node"].get("statements", []) if renaming else []):
                files.remove(other)
            replaced[key] = True
        elif renaming is not None:
            # Where the renamed one stood
            first = _creates(renaming)[0]
            _replace(files, first, text)
            for other in renaming["node"].get("statements", []):
                if other is not first:
                    files.remove(other)
            replaced[key] = True
        else:
            anchor = _new_place(files, items, includes, placed, key)
            path, offset, delimiter = anchor
            files.add(path, offset, offset, "\n\n" + text + delimiter)
            placed[key] = anchor
            replaced[key] = False
            # A parent that was not there appears with it
            parent = _parent_key(key)
            while parent is not None:
                if parent not in items:
                    changing.add(parent)
                parent = _parent_key(parent)

    gone = {renamed} if old is not None else set()
    rows, written, after_outline = editing.write_texts(
        project, files.new_texts(), lsp_path,
        check=_checking(binary, outlined, items, changing, gone, set(keys)),
    )
    after = _items(after_outline)
    # The project's files stay where they are; the includes are the new outline's
    after_files = _Files(project, binary, after_outline)
    return _answer(rows, written, [], statements=[
        _result_entry(statement, after_files, after, replaced[statement["key"]])
        for statement in given
    ])


# -----------------------------------------------------------------------------
# Dropping


def _drop_key(kind: str, service, schema, rest_object) -> tuple:
    if kind not in DROP_KINDS:
        raise ValueError(f"Unknown kind `{kind}`; the kinds are {', '.join(DROP_KINDS)}.")
    needed = {"service": (service,), "schema": (service, schema),
              "object": (service, schema, rest_object)}[kind]
    if not all(isinstance(path, str) and path for path in needed):
        raise ValueError(
            "Name the REST " + kind + " by "
            + {"service": "service", "schema": "service and schema",
               "object": "service, schema and object"}[kind] + "."
        )
    return (kind, *(rest_paths.path_key(path) for path in needed))


@changes_project
def drop_rest_object(
    schema_project_path: str,
    kind: str,
    service: str,
    base_hash: str,
    schema: str = None,
    rest_object: str = None,
    lsp_path: str = None,
) -> dict:
    """Removes a REST service, schema or object from MSM Section 180: every
    statement of it and of what is on it, with the comments right above
    each (see the module's docstring).

    Args:
        schema_project_path: The project folder.
        kind: service, schema or object.
        service: The service's path as Section 180 writes it (mike,anna@/svc).
        base_hash: The source_hash of the snapshot the caller knows.
        schema: The REST schema's path, for a schema or object.
        rest_object: The REST object's path, for an object.
        lsp_path: The mariadb-lsp binary or its folder.

    Returns:
        edit_schema's {rows, files, migration (none), migration_file (None),
        notes}, and removed: [{file, line (1-based, before the change)}],
        one per statement removed or changed.

    Raises:
        StaleSchemaError: For a stale base_hash.
        ValueError: For an item Section 180 neither creates nor names, and
            a result that would change more than the item.
    """
    key = _drop_key(kind, service, schema, rest_object)
    project, binary = _start(schema_project_path, base_hash, lsp_path)
    outlined = _outline(project, binary)
    if key not in _items(outlined):
        what = {"service": service, "schema": f"{schema} on {service}",
                "object": f"{rest_object} on {service} {schema}"}[kind]
        raise ValueError(f"Section {REST_DEFINITION_SECTION_ID} has no REST {kind} {what}.")
    project, outlined = _move_years(schema_project_path, project, binary, outlined)
    items = _items(outlined)
    item = items[key]

    files = _Files(project, binary, outlined)
    removed = []
    for statement in _subtree_statements(items, key, files.includes):
        path = files.path(statement.get("source"))
        removed.append({"file": os.path.normpath(path),
                        "line": statement["range"]["start"]["line"] + 1})
        if statement.get("shared") and key[0] == "schema":
            # USE REST SERVICE s SCHEMA p: the service stays in use
            service_use = next(
                (s for s in items[("service", key[1])]["node"].get("statements", [])
                 if s.get("shared") and s["range"] == statement["range"]
                 and s.get("source") == statement.get("source")),
                None,
            )
            if service_use is not None:
                end = files.offset(path, statement["range"]["end"])
                files.add(path, files.offset(path, service_use["selection_range"]["end"]), end, "")
                continue
        files.remove(statement)

    notes = []
    declared = item["node"].get("declared")
    if declared is False:
        notes.append(
            f"Section {REST_DEFINITION_SECTION_ID} does not create the REST {kind} "
            f"{item['node'].get('path')}, it only names it: what it put on it is removed, the "
            f"{kind} itself stays where it is created."
        )
    gone = {other for other in items if _in_subtree(other, key)}
    rows, written, _ = editing.write_texts(
        project, files.new_texts(), lsp_path,
        check=_checking(binary, outlined, items, gone, gone, set()),
    )
    return _answer(rows, written, notes, removed=removed)
