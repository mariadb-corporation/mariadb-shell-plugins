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

"""Renaming a project's schema (msm.renameSchema).

The schema name is a project's identity: msm.project.json's schemaName and
schemaFileName, the development script's file names, the USE, CREATE SCHEMA
and every qualifier in its SQL, the banners and markdown files the templates
filled with it, the schema snapshot and, where it still has msm's name, the
project folder. A rename changes them all, or none:

- the work is done in a copy of the project folder next to it, and the copy
  takes the folder's place at the end; a refusal or failure before that
  leaves the project as it was, a dry run throws the copy away;
- the references in the SQL are the language server's to find (``mariadb-lsp
  edit``, op ``rename_schema``): its parser resolves each name, so a table or
  alias that only has the schema's spelling stays, and it knows msm's
  ``/*<msm:name>*/`` placeholders;
- the project lists follow the folder last; a list that cannot be changed is
  reported, not raised, as the folder has moved.

Only a project without a release can be renamed for now: databases deployed
from a release would have to be moved to the new name by a migration, which
the answer's ``migration`` (empty for now) is there for.

A rename is not marked @changes_project: the mark moves the copyright years
of the folder the call names, which is gone afterwards. The years move along
in the copy instead, before its edits.
"""

import os
import re
import shutil
import uuid
from string import Template
from typing import Optional

from msm_plugin.lib import editing, lsp, management, modeling, projects, source
from msm_plugin.lib import sections as sections_lib

# The language server's edit op that renames the schema
RENAME_OP = "rename_schema"

# Why a rename is refused, as the answer's `reason` gives it
REASON_RELEASED = "released"
REASON_INVALID_NAME = "invalid-name"
REASON_SAME_NAME = "same-name"
REASON_FOLDER_EXISTS = "folder-exists"
REASON_LSP_MISSING = "lsp-missing"
# Given by the MCP plugin, for a project LLMs may not work on
REASON_RESTRICTED = "restricted"

# The template placeholders the schema's name fills
_NAME_PLACEHOLDERS = ("schema_name", "schema_identifier")


class RenameRefused(ValueError):
    """A rename msm does not make; the message is for the user.

    Attributes:
        reason: One of the REASON_ values.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def answer(project_path: str) -> dict:
    """The answer of a rename that has not been worked out (yet)."""
    return {
        "allowed": True,
        "reason": None,
        "message": None,
        "project_path": project_path,
        "previous_project_path": project_path,
        "files": [],
        "migration": [],
        "warnings": [],
    }


def refused(project_path: str, reason: str, message: str) -> dict:
    """The answer of a dry run msm refuses."""
    return {**answer(project_path), "allowed": False, "reason": reason, "message": message}


# -----------------------------------------------------------------------------
# What is checked before anything is done


def _check_not_released(project_path: str) -> None:
    # Not listed_last_released_version: a releases folder that cannot be
    # read must not pass for one without a release
    if not os.path.isdir(os.path.join(project_path, "releases", "versions")):
        return
    try:
        last = management.get_last_released_version(project_path)
    except StopIteration:
        raise ValueError(
            f"The releases of the project {project_path} could not be read."
        ) from None
    if last is not None:
        version = "%d.%d.%d" % tuple(last)
        raise RenameRefused(
            REASON_RELEASED,
            f"The schema was released already (version {version}), so it cannot be renamed: "
            "the databases deployed from its releases would keep the old name. Only a project "
            "without a release can be renamed.",
        )


def _rename_capable_lsp(lsp_path: Optional[str]) -> str:
    binary = lsp.find_lsp(lsp_path)
    if binary is None:
        raise RenameRefused(
            REASON_LSP_MISSING,
            f"Renaming the schema needs the MariaDB SQL language server's binary "
            f"{lsp.LSP_BINARY_NAME}, which was not found. Install it, or update the MariaDB "
            "Shell, which comes with it.",
        )
    if RENAME_OP not in lsp.edit_ops(binary):
        raise RenameRefused(
            REASON_LSP_MISSING,
            f"The installed MariaDB SQL language server ({binary}) cannot rename schemas yet. "
            f"Update {lsp.LSP_BINARY_NAME}, or the MariaDB Shell, which comes with it.",
        )
    return binary


def _checked_name(
    new_schema_name, project_settings: dict, allow_special_chars: bool
) -> str:
    """The new name, surrounding whitespace dropped, or refused."""
    if not isinstance(new_schema_name, str) or not new_schema_name.strip():
        raise RenameRefused(REASON_INVALID_NAME, "A new schema name has to be given.")
    name = new_schema_name.strip()
    old_name = project_settings.get("schemaName")
    if name == old_name:
        raise RenameRefused(REASON_SAME_NAME, f"The schema is named `{name}` already.")

    try:
        # What a deployment makes of the name, with the defaults; it checks
        # the length
        effective = management.get_effective_schema_name(
            {**project_settings, "schemaName": name}
        )
        if not re.search(management.MSM_SUBSTITUTION_REGEX, name):
            management.check_mysql_identifier(
                name, must_be_usable_when_unquoted=not allow_special_chars
            )
        else:
            # The placeholders work outside of quotes only
            if re.search(management.MSM_SUBSTITUTION_REGEX, effective):
                raise ValueError(
                    "The name uses a substitution the project does not declare."
                )
            management.check_mysql_identifier(effective, must_be_usable_when_unquoted=True)
    except ValueError as error:
        raise RenameRefused(REASON_INVALID_NAME, str(error)) from None
    return name


def _new_project_path(project_path: str, old_name: str, new_name: str) -> str:
    """The folder the project gets: msm's name for the new schema where the
    folder has msm's name for the old one, else the folder it is in."""
    if os.path.basename(project_path) != management.project_folder_name(old_name):
        return project_path
    new_path = os.path.join(
        os.path.dirname(project_path), management.project_folder_name(new_name)
    )
    if new_path != project_path and os.path.lexists(new_path):
        try:
            same = os.path.samefile(new_path, project_path)
        except OSError:
            same = False
        # A change of case only, on a file system that ignores case
        if not same:
            raise RenameRefused(
                REASON_FOLDER_EXISTS,
                f"The project folder cannot be renamed to {new_path}: it exists already.",
            )
    return new_path


# -----------------------------------------------------------------------------
# The work, in the copy


def _rename_request(project_settings: dict, old_name: str, new_name: str) -> dict:
    return {
        "format": lsp.EDIT_FORMAT,
        "schema": management.get_effective_schema_name(project_settings),
        "edits": [{
            "op": RENAME_OP, "schema": old_name, "new_name": new_name, "comments": True,
        }],
    }


def _edited(binary: str, sql: str, source_dir: str, request: dict, new_name: str) -> dict:
    """The binary's answer; nothing to change where no statement names the
    schema, refused where the script has a schema of the new name."""
    try:
        return lsp.edit(binary, sql, source_dir, request)
    except lsp.LspError as error:
        if error.kind == "unknown-object":
            return {"files": [], "notes": []}
        if error.kind == "name-taken":
            raise RenameRefused(
                REASON_INVALID_NAME,
                f"The development script works with a schema named `{new_name}` already.",
            ) from None
        raise


def _within(path: str, folder: str) -> bool:
    return projects._is_within(projects._real_path(path), projects._real_path(folder))


def _notes(result: dict, files: dict) -> list:
    """The binary's notes, each (its file's path, its line or None, message)."""
    return [
        (files.get(note.get("file"), note.get("file")),
         (note.get("start") or {}).get("line"),
         note.get("message", ""))
        for note in result.get("notes", [])
    ]


def _note_warning(note: tuple, renamed: dict, project_path: str) -> str:
    """A note as a warning, naming the file as it is named after the rename."""
    path, line, message = note
    path = renamed.get(path, path)
    where = os.path.relpath(path, project_path) if _within(path, project_path) else path
    if line is not None:
        where = f"{where}, line {line + 1}"
    return f"{where}: {message}"


def _apply(result: dict, files: dict, read, project_path: str, warnings: list) -> list:
    """Writes the binary's text edits; returns the files written.

    Args:
        result: The binary's answer.
        files: The path of each of its file names it may edit.
        read: Reads a file's current text by its path.
        project_path: The folder edits have to stay in.
        warnings: Gets a warning for a file outside it, left as it is.
    """
    written = []
    for changed in result.get("files", []):
        path = files.get(changed.get("file"))
        if path is None:
            continue
        if not _within(path, project_path):
            warnings.append(
                f"{path} names the schema but is not part of the project; it was left as it is."
            )
            continue
        old = read(path)
        new = editing.apply_text_edits(old, changed.get("edits", []))
        if sections_lib.write_if_changed(path, new, old):
            written.append(path)
    return written


def _include_files(result: dict, development: str) -> dict:
    """The path of each included file the binary names, by its name."""
    paths = {}
    for changed in [*result.get("files", []), *result.get("notes", [])]:
        name = changed.get("file")
        if name and name != "-" and name not in paths:
            paths[name] = source.checked_include_path(
                source.resolve_include_path(name, development)
            )
    return paths


def _template_lines(template_path: str, old_name: str, new_name: str) -> dict:
    """{old line: new line} of the lines of a template file the schema's
    name fills."""
    def filled(template: Template, schema_name: str) -> str:
        return template.safe_substitute(
            schema_name=schema_name,
            schema_identifier=management.get_schema_identifier(schema_name),
        )

    with open(template_path) as f:
        templates = [Template(line) for line in f.read().splitlines()]
    return {
        filled(template, old_name): filled(template, new_name)
        for template in templates
        if set(_NAME_PLACEHOLDERS) & set(template.get_identifiers())
    }


def _replace_lines(text: str, replaced: dict) -> str:
    """The text with each line that is a key of replaced replaced by its
    value; line ends stay."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        content = line.rstrip("\r\n")
        lines[i] = replaced.get(content, content) + line[len(content):]
    return "".join(lines)


def _rename_markdown(project_path: str, old_name: str, new_name: str) -> list:
    """Renames the schema in the lines of the template's markdown files that
    still read as the template wrote them; a line the user changed stays."""
    written = []
    for name in management._TEMPLATE_MARKDOWN_FILES:
        path = os.path.join(project_path, name)
        text = sections_lib.read_if_exists(path)
        replaced = _template_lines(
            os.path.join(management._PROJECT_TEMPLATE, name), old_name, new_name
        )
        if text is None or not replaced:
            continue
        if sections_lib.write_if_changed(path, _replace_lines(text, replaced), text):
            written.append(path)
    return written


def _rename_template_examples(dev_file: str, old_name: str, new_name: str) -> list:
    """Renames the schema in the banners of the template's commented-out
    examples (Sections 140 and 150) that still read as the template wrote
    them. The binary leaves block comments alone, and the examples are one.

    Returns:
        [dev_file] where it was written, else [].
    """
    text = sections_lib.read_if_exists(dev_file)
    if text is None or sections_lib._TEMPLATE_EXAMPLE_MARK not in text:
        return []
    replaced = _template_lines(management.DEVELOPMENT_SCRIPT_TEMPLATE, old_name, new_name)
    new = sections_lib._TEMPLATE_EXAMPLE.sub(
        lambda match: _replace_lines(match.group(0), replaced), text
    )
    return [dev_file] if sections_lib.write_if_changed(dev_file, new, text) else []


def _rename_in(
    path: str, old_name: str, new_name: str, binary: str, move_years: bool
) -> tuple:
    """Renames the schema in a project folder (the copy).

    Returns:
        (the files written, at their new names; warnings)
    """
    if move_years:
        # Not listed: the call's own files are (see update_copyright_years)
        management.update_copyright_years(path)

    project = modeling._Project.load(path)
    settings = project.settings
    snapshot_was_current = modeling.snapshot_is_current(path)
    request = _rename_request(settings, old_name, new_name)
    warnings, written = [], []

    # The development script and the files it includes. Section 160 is left
    # out, as for the snapshot: its VIEW is msm's to write, and it is made
    # again below.
    script, _ = modeling._outline_input(project)
    result = _edited(binary, script, project.development, request, new_name)
    files = {"-": project.dev_file, **_include_files(result, project.development)}
    written += _apply(
        result, files,
        lambda file: project.script if file == project.dev_file else project.read(file)[1],
        path, warnings,
    )
    notes = _notes(result, files)
    for file in _rename_template_examples(project.dev_file, old_name, new_name):
        if file not in written:
            written.append(file)

    # The pending update script, where there is one
    pending = editing.pending_update_path(path, settings)
    pending_text = sections_lib.read_if_exists(pending)
    if pending_text is not None:
        result = _edited(binary, pending_text, project.development, request, new_name)
        files = {"-": pending}
        written += _apply(result, files, lambda file: pending_text, path, warnings)
        notes += _notes(result, files)

    # The file names
    new_file_name = management.convert_string_to_valid_filename(new_name)
    renamed = {}
    old_file_name = settings.get("schemaFileName")
    if new_file_name != old_file_name:
        new_settings = {**settings, "schemaFileName": new_file_name}
        for old_path, new_path in (
            (project.dev_file, os.path.join(path, "development", f"{new_file_name}_next.sql")),
            (pending, editing.pending_update_path(path, new_settings)),
        ):
            if os.path.exists(old_path):
                if os.path.lexists(new_path) and not os.path.samefile(old_path, new_path):
                    raise RenameRefused(
                        REASON_INVALID_NAME,
                        f"The project has a file {os.path.relpath(new_path, path)} already, "
                        "which the rename would replace.",
                    )
                os.rename(old_path, new_path)
                renamed[old_path] = new_path
    written = [renamed.get(file, file) for file in written]
    warnings += [_note_warning(note, renamed, path) for note in notes]
    written += renamed.values()

    raw = management._read_raw_project_settings(path)
    raw["schemaName"] = new_name
    raw["schemaFileName"] = new_file_name
    written.append(management.write_project_settings(path, raw))

    written += _rename_markdown(path, old_name, new_name)

    # The snapshot keeps the objects of its schema only, so one made of the
    # old name describes none: a current one is made again. One that was
    # stale stays so, for the next msm.updateSchemaDefinition.
    if snapshot_was_current:
        written += modeling.update_schema_definition(path, binary)["files"]

    return written, warnings


def _dependent_projects(
    project_path: str, old_name: str, owner, mcp_access_only: bool
) -> list:
    """Warnings for the other listed projects that depend on the schema;
    with mcp_access_only, only those an LLM may see."""
    warnings = []
    for listed in projects.list_projects(owner, mcp_access_only=mcp_access_only):
        other = listed["project_path"]
        if not listed["exists"] or other == project_path:
            continue
        dependencies = (projects.read_settings(other) or {}).get("schemaDependencies")
        if isinstance(dependencies, list) and any(isinstance(name, str) and name.lower() == old_name.lower()
               for name in dependencies):
            warnings.append(
                f"The project {other} lists `{old_name}` in its schemaDependencies; "
                "change it there if it depends on the renamed schema."
            )
    return warnings


def _sibling(path: str, purpose: str) -> str:
    """A hidden folder next to path that does not exist yet."""
    return os.path.join(
        os.path.dirname(path),
        f".{os.path.basename(path)}.{purpose}-{uuid.uuid4().hex[:8]}",
    )


def _swap(path: str, staging: str, new_path: str) -> str:
    """Puts the copy in the folder's place; returns where the folder went.

    Raises:
        ValueError: Where a move fails; the folder is then where it was.
    """
    backup = _sibling(path, "renamed")
    try:
        os.rename(path, backup)
    except OSError as error:
        raise ValueError(f"The project folder {path} could not be moved: {error}") from None
    try:
        os.rename(staging, new_path)
    except OSError as error:
        try:
            os.rename(backup, path)
        except OSError:
            raise ValueError(
                f"The project folder could not be moved to {new_path}: {error}; "
                f"nor back from {backup}, where it is now."
            ) from None
        raise ValueError(f"The project folder could not be moved to {new_path}: {error}") from None
    return backup


def rename_schema(
    schema_project_path: str,
    new_schema_name: Optional[str] = None,
    dry_run: bool = False,
    lsp_path: Optional[str] = None,
    allow_special_chars: bool = False,
    owner=None,
    mcp_access_only: bool = False,
) -> dict:
    """Renames the schema of a project that has no release yet.

    Args:
        schema_project_path: The project folder.
        new_schema_name: The new name. A name with substitution placeholders
            (/*<msm:name>*/) is written as is. A dry run may leave it out,
            to learn whether the project can be renamed at all.
        dry_run: Whether to work the rename out without changing anything.
        lsp_path: The mariadb-lsp binary or its folder; None looks for it.
        allow_special_chars: Whether the name may need quotes.
        owner: The user whose project list is searched for projects that
            depend on the schema (projects.list_projects); every list that
            has the project follows it.
        mcp_access_only: Whether to warn only about projects an LLM may
            work on, as the MCP server lists them for its agents.

    Returns:
        {allowed, reason, message, project_path, previous_project_path,
        files, migration, warnings}: allowed is False only for a dry run
        msm refuses, with the reason (a REASON_ value) and the message for
        the user; project_path is the folder afterwards, previous_project_path
        the folder before (the same where the folder keeps its name); files
        the files written, or for a dry run that would be, at their new paths;
        migration the statements a deployed schema would need (none, there is
        no release); warnings what the user has to look at.

    Raises:
        RenameRefused: For a rename msm does not make (not for a dry run),
            with the same message a dry run gives.
        ValueError: For a project msm cannot read.
    """
    path = projects.normalize_project_path(schema_project_path)
    try:
        settings = management.get_project_settings(path)
        old_name = settings.get("schemaName")
        _check_not_released(path)
        binary = _rename_capable_lsp(lsp_path)
        if new_schema_name is None and dry_run:
            return answer(path)
        new_name = _checked_name(new_schema_name, settings, allow_special_chars)
        new_path = _new_project_path(path, old_name, new_name)

        staging = _sibling(path, "renaming")
        # Gone once it took the folder's place; else thrown away
        try:
            shutil.copytree(path, staging, symlinks=True)
            written, warnings = _rename_in(staging, old_name, new_name, binary, not dry_run)
            result = {
                **answer(new_path),
                "previous_project_path": path,
                "files": sorted({
                    os.path.join(new_path, os.path.relpath(file, staging)) for file in written
                }),
            }
            warnings += _dependent_projects(path, old_name, owner, mcp_access_only)
            if dry_run:
                result["warnings"] = warnings
                return result
            backup = _swap(path, staging, new_path)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except RenameRefused as refusal:
        if dry_run:
            return refused(path, refusal.reason, str(refusal))
        raise

    try:
        shutil.rmtree(backup)
    except OSError as error:
        warnings.append(f"The project's old folder {backup} could not be removed: {error}")

    if new_path != path:
        warnings += projects.move_project(path, new_path)
    result["warnings"] = warnings
    return result
