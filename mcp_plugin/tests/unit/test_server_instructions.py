# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""The instructions the server hands its clients at initialize.

An agent that ran the mariadb client from a shell put the root password on
its command line - into the process list, the logs and the transcript. The
instructions are what a client like Claude Code puts in the system prompt, so
they are the one place every session reads before its first tool call.
"""

# cSpell:ignore mysqlsh MariaDB

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

from mcp_plugin.lib import general, server
import mcp_plugin.tests.unit.helpers as helpers


def test_a_client_receives_the_instructions_at_initialize():
    """Over a real stdio transport, not just from the builder."""
    instructions = helpers.server_instructions(["db", "sandbox"])

    assert instructions == server.server_instructions(["db", "sandbox"])


def test_the_instructions_rule_out_the_command_line_clients():
    """The clients an agent reaches for are named, and passwords are off limits."""
    instructions = server.server_instructions(
        [general.FUNCTION_GROUP_DB, general.FUNCTION_GROUP_SANDBOX]
    )

    for marker in (
        "mariadb,",
        "mysql,",
        "mariadb-shell",
        "mysqlsh",
        "Never ask the user for a database password",
        "mcp setup",
        "fine for what no tool here covers",
        "freshly generated random password",
    ):
        assert marker in instructions


def test_the_instructions_mention_only_the_groups_served():
    """A server without the sandbox group does not point at its tools."""
    db_only = server.server_instructions([general.FUNCTION_GROUP_DB])
    with_sandbox = server.server_instructions(
        [general.FUNCTION_GROUP_DB, general.FUNCTION_GROUP_SANDBOX]
    )

    assert "sandbox.deploy" not in db_only
    assert "sandbox.deploy" in with_sandbox
    assert "util." not in with_sandbox
    assert "util.*" in server.server_instructions(
        [general.FUNCTION_GROUP_DB, general.FUNCTION_GROUP_UTIL]
    )
    # util registers nothing without db.
    assert "util." not in server.server_instructions([general.FUNCTION_GROUP_UTIL])


def test_the_instructions_use_the_published_tool_names():
    """With --toolNameSeparator=_ the tools are db_connect, and so is the text."""
    instructions = server.server_instructions(
        [general.FUNCTION_GROUP_DB, general.FUNCTION_GROUP_SANDBOX], "_"
    )

    assert "db_execute_sql_script" in instructions
    assert "sandbox_deploy" in instructions
    assert "db.connect" not in instructions
