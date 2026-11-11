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

"""The REST endpoints a release removed: MSM Section 280 of an update script.

Section 180 of every deployment script creates the REST definition of the
deployed version with CREATE OR REPLACE, which updates a REST service, schema
or object in place and keeps its id; it never removes what a version no
longer defines. So msm.prepareRelease writes into the update script's Section
280 a DROP ... IF EXISTS of every REST object, schema and service the
previous release's Section 180 created and this one's no longer does, and a
REMOVE AUTH APP ... IF EXISTS of every auth app link it no longer names:
objects first, then schemas, links and services. What a removed service or
schema held is listed too, so a later version that defines the service
again does not keep the rest of it. A REST schema that takes its objects from
another database is dropped with FOR SCHEMA <the old one>: only where the
server still has it there, as its objects cannot move along.

Every later deployment script runs those sections (its Section 380) before
its own Section 180, on a fresh install too. A statement there that drops
what the deployed version defines again is commented out, so the endpoint
keeps its id: msm.generateDeploymentScript outlines the version's Section 180
with and without each statement.

The REST definition is read with mariadb-lsp's outline, which resolves the
service and schema of each statement as the shell does. A service or schema
the section only names (ON SERVICE of one it never creates) is never
dropped; its objects are. Paths are compared without their quotes and
without regard to case, as the server compares them.
"""

import re
from typing import Optional

from msm_plugin.lib import editing, lsp, management
from msm_plugin.lib import statements as statements_lib

REST_DEFINITION_SECTION_ID = "180"
REST_REMOVALS_SECTION_ID = "280"

# The outline's REST object kinds, as DROP REST names them
_DROP_KINDS = {"view": "VIEW", "procedure": "PROCEDURE", "function": "FUNCTION"}

# The outline's notes about a REST statement it could not apply, as the
# shell could not run it: no service or schema in use, or a service the
# file does not create
_UNRESOLVED_NOTES = ("rest-none-in-use", "unknown-rest-service")

_QUOTES = ("`", "'", '"')


def _key(path: str) -> str:
    """A path as the server compares it: unquoted, case ignored."""
    if len(path) >= 2 and path[0] == path[-1] and path[0] in _QUOTES:
        path = path[1:-1]
    return path.lower()


def _quote_text(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _line(note: dict) -> str:
    """Where a note's statement is: a line of Section 180, not of the file."""
    line = (note.get("start") or {}).get("line")
    if not isinstance(line, int):
        return ""
    return f"Section {REST_DEFINITION_SECTION_ID} line {line + 1}: "


def require_rest_lsp(lsp_path: Optional[str] = None) -> str:
    """The mariadb-lsp binary, where it outlines the REST definition as msm
    needs it; raises otherwise."""
    return _checked_rest_lsp(lsp.require_lsp(lsp_path))


def _checked_rest_lsp(binary: str) -> str:
    if not lsp.outlines_rest(binary):
        raise lsp.LspError(
            f"The REST endpoints a release removes are worked out with the MariaDB SQL "
            f"language server, and the installed one ({binary}) cannot read REST "
            f"definitions well enough yet. Update {lsp.LSP_BINARY_NAME}, or the MariaDB "
            "Shell, which comes with it."
        )
    return binary


# The database a CREATE REST SCHEMA takes its objects from, as written:
# msm's substitution placeholders kept, which the outline leaves out
_PLACEHOLDER = management.MSM_SUBSTITUTION_REGEX.replace("(", "(?:", 1)
_FROM_DATABASE = re.compile(
    r"\bREST\s+(?:SCHEMA|DATABASE)\b.*?(?<![/\w])FROM\s+"
    rf"((?:{_PLACEHOLDER}|`(?:[^`]|``)*`|[\w$]+)+)",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED = re.compile(r"`((?:[^`]|``)*)`")


def _database_text(sql: str, line_starts: list, schema: dict) -> Optional[str]:
    """The database of a REST schema as its CREATE statement (the outline's
    range of it) writes it; None where msm cannot read it."""
    span = schema.get("range") or {}
    try:
        statement = sql[
            editing._offset(sql, line_starts, span["start"]):
            editing._offset(sql, line_starts, span["end"])
        ]
    except (KeyError, TypeError, ValueError):
        return None
    found = _FROM_DATABASE.search(statement)
    return found.group(1) if found else None


def _written(schema: dict) -> Optional[str]:
    """A REST schema's database as written, quotes left out: what tells two
    of them apart, placeholders included."""
    text = schema["database_sql"]
    if text is None:
        return schema["database"]
    return _QUOTED.sub(lambda quoted: quoted.group(1).replace("``", "`"), text)


def definition(outlined: dict, sql: Optional[str] = None) -> tuple:
    """The REST definition of an outline, and what msm could not place.

    Args:
        outlined: The outline.
        sql: The SQL it outlines, to tell each REST schema's database as
            written.

    Returns:
        ({service key: {path, declared, auth_apps: {key: name}, schemas:
        {key: {path, declared, database, database_sql, objects: {key: {kind,
        path}}}}}}, warnings)
    """
    services, warnings = {}, []
    line_starts = [0] + [m.end() for m in re.finditer("\n", sql)] if sql else None
    for note in outlined.get("notes", []):
        if note.get("kind") in _UNRESOLVED_NOTES:
            warnings.append(_line(note) + str(note.get("message", "")))
    for service in (outlined.get("rest") or {}).get("services", []):
        path = service.get("path") or ""
        if not path:
            continue
        entry = services.setdefault(_key(path), {
            "path": path, "declared": False, "auth_apps": {}, "schemas": {},
        })
        entry["declared"] = entry["declared"] or service.get("declared") is True
        for app in service.get("auth_apps", []):
            name = app.get("name")
            if isinstance(name, str) and name:
                entry["auth_apps"][name.lower()] = name
        for schema in service.get("schemas", []):
            schema_path = schema.get("path") or ""
            if not schema_path:
                continue
            schema_entry = entry["schemas"].setdefault(_key(schema_path), {
                "path": schema_path, "declared": False, "database": None,
                "database_sql": None, "objects": {},
            })
            if schema.get("declared") is True:
                schema_entry["declared"] = True
                schema_entry["database"] = schema.get("schema")
                schema_entry["database_sql"] = (
                    _database_text(sql, line_starts, schema) if sql else None
                )
            for item in schema.get("objects", []):
                item_path = item.get("path") or ""
                if item_path:
                    schema_entry["objects"][_key(item_path)] = {
                        "kind": item.get("kind"), "path": item_path,
                    }
    return services, warnings


def _shape(services: dict) -> dict:
    """What a deployment of a REST definition leaves on the server, for
    comparing two of them."""
    return {
        key: (
            frozenset(service["auth_apps"]),
            {
                schema_key: {item_key: item["kind"] for item_key, item in schema["objects"].items()}
                for schema_key, schema in service["schemas"].items()
            },
        )
        for key, service in services.items()
    }


def removals(previous: dict, current: dict) -> tuple:
    """The statements that remove what the previous REST definition created
    and the current one does not.

    Returns:
        (statements, warnings): objects first, then schemas, auth app links
        and services.
    """
    objects, schemas, links, services, warnings = [], [], [], [], []
    for service_key, service in previous.items():
        kept_service = current.get(service_key)
        on_service = f"FROM SERVICE {service['path']}"
        for schema_key, schema in service["schemas"].items():
            kept_schema = kept_service["schemas"].get(schema_key) if kept_service else None
            for item_key, item in schema["objects"].items():
                kept = kept_schema["objects"].get(item_key) if kept_schema else None
                if kept is not None and kept["kind"] == item["kind"]:
                    continue
                kind = _DROP_KINDS.get(item["kind"])
                if kind is None:
                    warnings.append(
                        f"The REST {item['kind']} {item['path']} of {service['path']} "
                        f"{schema['path']} is not dropped: msm does not know how."
                    )
                    continue
                objects.append(
                    f"DROP REST {kind} IF EXISTS {item['path']} {on_service} "
                    f"SCHEMA {schema['path']};"
                )
            if kept_schema is None and schema["declared"]:
                schemas.append(f"DROP REST SCHEMA IF EXISTS {schema['path']} {on_service};")
            elif kept_schema is not None and schema["database"] and kept_schema["database"]:
                # A schema on another database: its objects cannot move along
                # on the server, so it is dropped where it still has the old
                # one, and Section 180 creates it again
                old, new = _written(schema), _written(kept_schema)
                where = f"The REST schema {schema['path']} of {service['path']}"
                if old == new:
                    pass
                elif schema["database_sql"] is None:
                    warnings.append(
                        f"{where} moves to another database, but msm could not read "
                        "its old one: drop the REST schema by hand."
                    )
                elif old.lower() == new.lower():
                    warnings.append(
                        f"{where} changes its database only in case; on a server with "
                        "lower_case_table_names=0 drop the REST schema by hand."
                    )
                else:
                    schemas.append(
                        f"DROP REST SCHEMA IF EXISTS {schema['path']} {on_service} "
                        f"FOR SCHEMA {schema['database_sql']};"
                    )
        kept_links = kept_service["auth_apps"] if kept_service else {}
        for app_key, name in service["auth_apps"].items():
            if app_key not in kept_links:
                links.append(
                    f"ALTER REST SERVICE {service['path']} REMOVE AUTH APP "
                    f"{_quote_text(name)} IF EXISTS;"
                )
        if kept_service is None and service["declared"]:
            services.append(f"DROP REST SERVICE IF EXISTS {service['path']};")
    return objects + schemas + links + services, warnings


def _has_statement(sql: Optional[str]) -> bool:
    return bool(sql) and not management.sql_content_has_no_statement(sql)


def release_removals(
    previous_sql: Optional[str],
    current_sql: Optional[str],
    previous_version: str,
    lsp_path: Optional[str] = None,
) -> Optional[str]:
    """The body of a release's Section 280: what the previous release's
    Section 180 created and this release's does not; None where there is
    nothing to drop.

    Args:
        previous_sql: The previous release's Section 180.
        current_sql: This release's Section 180.
        previous_version: The previous release's version.
        lsp_path: The mariadb-lsp binary or its folder; None looks for it.

    Raises:
        lsp.LspError: Where there is no binary, or the previous release has a
            REST definition and the binary cannot read it as msm needs.
    """
    if not _has_statement(previous_sql):
        return None
    binary = lsp.require_lsp(lsp_path)
    # Any binary tells whether there is a REST statement at all
    previous, previous_warnings = definition(lsp.outline(binary, previous_sql), previous_sql)
    if not previous and not previous_warnings:
        return None
    _checked_rest_lsp(binary)
    current, current_warnings = (
        definition(lsp.outline(binary, current_sql), current_sql)
        if _has_statement(current_sql)
        else ({}, [])
    )
    statements, warnings = removals(previous, current)

    lines = []
    if previous_warnings or current_warnings or warnings:
        lines.append(
            "-- msm could not place every REST statement; check that nothing it created "
            "needs dropping:"
        )
        lines += [f"--   version {previous_version}, {w}" for w in previous_warnings]
        lines += [f"--   this version, {w}" for w in current_warnings]
        lines += [f"--   {w}" for w in warnings]
        lines.append("")
    if statements:
        lines.append(
            f"-- What the Section 180 of version {previous_version} created and this "
            "version's no longer does"
        )
        lines += statements
    return "\n".join(lines) if lines else None


def _commented(statement: str, version: str) -> str:
    lines = statement.rstrip("\n").split("\n")
    return "\n".join(
        [f"-- msm: version {version} defines this again, so it is kept"]
        + [f"-- {line}" if line else "--" for line in lines]
    )


class _Target:
    """The target version's REST definition, outlined once, and whether a
    removal changes it."""

    def __init__(self, binary: str, sql: str):
        self.binary = binary
        self.sql = sql.rstrip("\n") + "\n"
        self.shape = _shape(definition(lsp.outline(binary, self.sql))[0])

    def removed_by(self, sql: str) -> bool:
        outlined = lsp.outline(self.binary, self.sql + sql)
        return _shape(definition(outlined)[0]) != self.shape


def _keep_redefined(target: _Target, removals_sql: str, version: str) -> str:
    """An update script's Section 280 with each statement commented out that
    removes what the target defines again; the one given where none does."""
    # One outline where the whole section removes nothing the target has
    if not target.removed_by(removals_sql):
        return removals_sql
    parts, at = [], 0
    for begin, end, _ in statements_lib.sql_entries(
        removals_sql, lsp.split(target.binary, removals_sql)
    ):
        statement = removals_sql[begin:end]
        if target.removed_by(statement + "\n"):
            parts += [removals_sql[at:begin], _commented(statement, version)]
            at = end
    parts.append(removals_sql[at:])
    return "".join(parts)


def keep_redefined(
    updates: list, target_sql: Optional[str], version: str, lsp_path: Optional[str] = None
) -> None:
    """Comments out each statement of the update scripts' Sections 280 that
    removes what the target version's Section 180 defines again, so the
    endpoint keeps its id.

    Args:
        updates: The sections of each update script (get_file_sections);
            their Section 280's sql_content is changed in place.
        target_sql: The target version's Section 180.
        version: The target version.
        lsp_path: The mariadb-lsp binary or its folder; None looks for it.
            Needed only where the target and a Section 280 have a statement.

    Raises:
        lsp.LspError: Where it is needed and there is no binary that reads
            REST definitions as msm needs.
    """
    removals = [
        sections[REST_REMOVALS_SECTION_ID]
        for sections in updates
        if _has_statement(sections.get(REST_REMOVALS_SECTION_ID, {}).get("sql_content"))
    ]
    if not removals or not _has_statement(target_sql):
        return
    target = _Target(require_rest_lsp(lsp_path), target_sql)
    for section in removals:
        section["sql_content"] = _keep_redefined(target, section["sql_content"], version)
