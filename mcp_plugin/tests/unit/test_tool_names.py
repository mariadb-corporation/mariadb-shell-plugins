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

"""The names the tools are published under: ``mcp setup --toolNameSeparator``.

Arcade refuses a tool named ``db.list_connections`` - "tool name must only
contain ASCII letters, numbers, and the dash and underscore characters" -
although the MCP specification allows the dot, so a server can publish its
tools as ``db_list_connections`` instead, with every mention of one tool in
another's description or error rewritten to match.
"""

# cSpell:ignore mysqlsh MariaDB

import asyncio
import json
import re

import mysqlsh
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_plugin.lib import config, server, setup_cli, tool_registrar

# What Arcade accepts, quoted from its refusal.
ARCADE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@pytest.fixture(autouse=True)
def _dotted_names_afterwards():
    """The separator is the server's, held by the module: put the default back."""
    yield
    tool_registrar._separator = "."
    tool_registrar._registered.clear()


def _tools(separator):
    mcp_server = server.build_mcp_server(["db", "msm"], tool_name_separator=separator)
    return mcp_server, asyncio.run(mcp_server.list_tools())


def test_tools_keep_their_dotted_names_by_default(tenant_config):
    _, tools = _tools(".")
    names = {tool.name for tool in tools}

    assert "db.list_connections" in names
    assert "msm.create_project" in names
    assert any("db.connect" in (tool.description or "") for tool in tools)


def test_tool_names_with_a_separator(tenant_config):
    """Names, descriptions and parameter descriptions all use the separator.

    Pins the SDK's private tool manager, which finish_tool_names rewrites.
    """
    _, tools = _tools("_")
    names = {tool.name for tool in tools}
    text = json.dumps([[tool.description, tool.input_schema] for tool in tools])

    assert all(ARCADE_NAME.match(name) for name in names), names
    assert {"db_list_connections", "db_connect", "msm_create_project"} <= names
    assert "db_connect" in text
    assert not re.search(r"\b(db|msm)\.(connect|execute_sql|list_connections)\b", text)


def test_a_dotted_word_that_names_no_tool_is_left_alone(tenant_config):
    _tools("_")

    assert tool_registrar.translate_tool_names(
        "Call db.connect, then read sales.orders and db.nothing."
    ) == "Call db_connect, then read sales.orders and db.nothing."


def test_a_tools_error_names_the_published_tools(tenant_config):
    mcp_server, _ = _tools("_")

    @mcp_server.tool(name="db.refuse")
    def refuse() -> str:
        raise ToolError("Open one with db.connect first.")

    with pytest.raises(ToolError, match="Open one with db_connect first"):
        asyncio.run(mcp_server.call_tool("db_refuse", {}))


def test_the_group_is_read_from_either_name():
    assert tool_registrar.tool_group("db.list_connections") == "db"
    assert tool_registrar.tool_group("db_list_connections") == "db"
    assert tool_registrar.tool_group("msm-create_project") == "msm"


def test_mcp_setup_sets_the_separator(tenant_config, capsys):
    setup_cli.apply({"tool_name_separator": "_"})
    assert config.get_tool_name_separator() == "_"
    assert "db_list_connections" in capsys.readouterr().out

    setup_cli.apply({"tool_name_separator": "."})
    assert config.get_tool_name_separator() == "."
    assert "toolNameSeparator" not in config.get_settings()

    with pytest.raises(mysqlsh.Error, match="must be one of"):
        setup_cli.apply({"tool_name_separator": "/"})
