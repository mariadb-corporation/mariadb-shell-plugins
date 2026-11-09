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

"""The update steps of a deployment script, run by msm instead of the
server.

A deployment script brings an existing schema up to date with
``CALL msm_create_or_update()``, which calls ``msm_update_<from>_to_<to>()``
for each release on the way: Section 240 of each update script is the body
of one of these procedures. Run that way, every ALTER TABLE runs as
written. msm.deploySchema instead runs the update itself, in the order the
procedure would, with the ALTER TABLE statements at a step's top level going
through lib/online_alter.py; everything between them runs in
``BEGIN NOT ATOMIC ... END`` blocks, which behave as the procedure's body.

The generated script marks each step's body (MSM-UPDATE-STEP-START / -END),
so what runs is what the script holds. A script generated before the markers
has none and runs as written.

No shell imports: the session and a log function are passed in.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

from msm_plugin.lib import online_alter, statements

_STEP = re.compile(
    r"^[ \t]*-- ### MSM-UPDATE-STEP-START (\S+) (\S+)[ \t]*\n(.*?)"
    r"^[ \t]*-- ### MSM-UPDATE-STEP-END \1 \2[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_CALL_CREATE_OR_UPDATE = re.compile(
    r"^\s*CALL\s+`?msm_create_or_update`?\s*\(\s*\)\s*;?\s*$", re.IGNORECASE
)
_DECLARE = re.compile(r"^\s*DECLARE\b", re.IGNORECASE)


@dataclass
class UpdateStep:
    version_from: str
    version_to: str
    body: str


class NeedsExternalToolError(online_alter.OnlineAlterError):
    """No online step applied an ALTER TABLE of an update: nothing of that
    statement changed, and the deployment stops."""

    def __init__(self, step: UpdateStep, table: str, sql: str, result: online_alter.Result):
        super().__init__(
            f"The update from {step.version_from} to {step.version_to} needs an external "
            f"tool for an ALTER TABLE of `{table}`: {result.reason}. Nothing of that "
            "statement was changed; the statements before it ran. Statement: "
            f"{statements.first_line(sql)}"
        )
        self.step = step
        self.table = table
        self.sql = sql
        self.result = result

    def with_message(self, message: str) -> "NeedsExternalToolError":
        """The same error with another message."""
        # Not copy.copy: it rebuilds an exception from its args alone
        error = NeedsExternalToolError.__new__(NeedsExternalToolError)
        error.__dict__.update(self.__dict__)
        Exception.__init__(error, message)
        return error


def read_steps(script: str) -> list:
    """The marked update steps of a deployment script, in its order."""
    return [UpdateStep(match.group(1), match.group(2), match.group(3))
            for match in _STEP.finditer(script)]


def is_create_or_update_call(sql: str) -> bool:
    return bool(_CALL_CREATE_OR_UPDATE.match(statements.strip_statement(sql)))


@dataclass
class Action:
    """What runs for a part of a step: kind "alter" (one top-level ALTER
    TABLE, sql), or "block" (statements run as one BEGIN NOT ATOMIC block,
    sql = the block; note says why a whole step is one block)."""

    kind: str
    sql: str
    note: Optional[str] = None


def step_actions(body: str) -> list:
    """A step's body as the actions that run it, in order.

    A body that declares variables or handlers at its top level, or whose
    compound statements cannot be read, runs whole as one block: split up,
    a later part would lose what the DECLARE gave it.
    """
    try:
        parts = statements.top_level_statements(body)
    except ValueError as error:
        # As the procedure has it: its statements end with their own ;
        return [Action("block", f"BEGIN NOT ATOMIC\n{body.rstrip()}\nEND",
                       f"its compound statements could not be read ({error})")]
    if not parts:
        return []
    if any(_DECLARE.match(statements.strip_statement(part)) for part in parts):
        return [_block(parts, "it declares variables or handlers")]
    actions, run = [], []
    for part in parts:
        if statements.alter_table_parts(part) is not None:
            if run:
                actions.append(_block(run))
                run = []
            actions.append(Action("alter", part))
        else:
            run.append(part)
    if run:
        actions.append(_block(run))
    return actions


def _block(parts: list, note: str = None) -> Action:
    # The ; on a line of its own: a statement may end in a line comment
    return Action("block", "BEGIN NOT ATOMIC\n" + "".join(f"{part}\n;\n" for part in parts)
                  + "END", note)


def run_update(
    session,
    script: str,
    schema_name: str,
    current_version: str,
    settings: online_alter.DeploymentSettings,
    log: Callable[[str, str], None],
) -> None:
    """Does what ``CALL msm_create_or_update()`` does for an existing,
    managed schema on an older version: marks the update as running (the
    version VIEW at 0, 0, 0), sets @msm_schema_init and
    @msm_current_version for the authorization procedures that follow, and
    runs the steps from current_version on. The session is in the schema
    (the script's USE before the call).

    Args:
        session: The deployment's session.
        script: The deployment script, its substitutions applied.
        schema_name: The schema deployed; an ALTER TABLE naming no schema
            alters a table of it.
        current_version: The schema's version before the update.
        settings: The deployment's onlineAlter settings.
        log: log(level, message), the update log.

    Raises:
        NeedsExternalToolError: Where no online step applies an ALTER TABLE.
        Any error of a statement, unchanged.
    """
    session.run_sql("SET @msm_schema_init = 0")
    session.run_sql("SET @msm_current_version = ?", [current_version])
    session.run_sql(
        "CREATE OR REPLACE SQL SECURITY INVOKER VIEW `msm_schema_version` "
        "(`major`,`minor`,`patch`) AS SELECT 0, 0, 0"
    )
    version = current_version
    for step in read_steps(script):
        if step.version_from != version:
            continue
        log("INFO", f"Updating from {step.version_from} to {step.version_to} ...")
        for action in step_actions(step.body):
            if action.kind == "block":
                if action.note:
                    log("INFO", f"The update to {step.version_to} runs as one block: {action.note}; "
                                "its ALTER TABLE statements run as written.")
                session.run_sql(action.sql)
            else:
                _alter(session, step, action.sql, schema_name, settings, log)
        version = step.version_to


def _alter(session, step, sql, schema_name, settings, log) -> None:
    parts = statements.alter_table_parts(sql)
    options = settings.options_for(parts.table)
    why = None
    if options is None:
        why = "online ALTER TABLE is off for the table"
    elif parts.modifiers:
        why = f"it uses {' '.join(parts.modifiers)}"
    elif statements.locking_clause(parts.spec):
        why = f"it sets {statements.locking_clause(parts.spec)} itself"
    if why:
        log("INFO", f"ALTER TABLE `{parts.table}` runs as written ({why}).")
        session.run_sql(statements.strip_statement(sql))
        return
    result = online_alter.alter_table(
        session, parts.schema or schema_name, parts.table, parts.spec, options)
    if not result.applied:
        log("ERROR", f"ALTER TABLE `{parts.table}`: no online step applied it: {result.reason}")
        raise NeedsExternalToolError(step, parts.table, sql, result)
    seconds = sum(item["seconds"] for item in result.steps)
    log("INFO", f"ALTER TABLE `{parts.table}`: {result.method} ({seconds:.2f} s"
                + (f"; {result.reason}" if result.reason else "") + ").")
