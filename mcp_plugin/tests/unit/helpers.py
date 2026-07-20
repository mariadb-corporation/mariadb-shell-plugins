# Copyright (c) 2026, MariaDB plc and/or its affiliates.
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

"""Test helpers for the MariaDB MCP Server Plugin.

Provides utilities to talk to the plugin's MCP server over the stdio transport.
The server is launched as a ``mysqlsh`` subprocess running
``mcp start-server --transport=stdio`` and is driven with the MCP client SDK.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp

import asyncio
import json
import os
import shutil

# Two connections used by the connection round-trip test. The URIs only need to
# be unique keys for the secret store; the round-trip test does not open them.
TEST_CONNECTION_URIS = [
    "mcp_pytest_a@127.0.0.1:33061",
    "mcp_pytest_b@127.0.0.1:33062",
]

TEST_CONNECTION_PASSWORD = "mcp_pytest_password"

# Time budget (seconds) for a single MCP stdio round-trip, including the time it
# takes to start the mysqlsh subprocess and load the plugins.
_MCP_TIMEOUT = 90


def mysqlsh_binary() -> str:
    """Returns the path to the mysqlsh/mariadb-shell binary to use."""
    return (
        os.environ.get("MYSQLSH")
        or shutil.which("mysqlsh")
        or shutil.which("mariadb-shell")
        or "mysqlsh"
    )


def _stdio_server_params(function_groups):
    """Builds the StdioServerParameters for the MCP server subprocess.

    Args:
        function_groups (list): The function groups to expose.

    Returns:
        A StdioServerParameters instance.
    """
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=mysqlsh_binary(),
        # --quiet-start=2 suppresses the shell banner so stdout carries only the
        # MCP JSON-RPC stream. The subprocess inherits MYSQLSH_USER_CONFIG_HOME
        # from the environment, so it sees the same secrets and settings.json.
        args=[
            "--quiet-start=2",
            "--",
            "mcp",
            "start-server",
            "--transport=stdio",
            f"--function-groups={','.join(function_groups)}",
        ],
        env=os.environ.copy(),
    )


async def _acall_tool(function_groups, tool_name, arguments):
    """Opens a stdio MCP session, calls one tool and returns the result."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_server_params(function_groups)

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    return await asyncio.wait_for(_run(), timeout=_MCP_TIMEOUT)


def call_tool(function_groups, tool_name, arguments=None):
    """Calls an MCP tool over stdio and returns the CallToolResult.

    Args:
        function_groups (list): The function groups the server should expose.
        tool_name (str): The MCP tool to call.
        arguments (dict): The tool arguments.

    Returns:
        The CallToolResult returned by the MCP client.
    """
    return asyncio.run(
        _acall_tool(function_groups, tool_name, arguments or {})
    )


def tool_payload(result):
    """Extracts the Python payload returned by a tool from a CallToolResult.

    Prefers the structured content when present. Otherwise falls back to the
    text content blocks: FastMCP emits one content block per element when a
    tool returns a list, so multiple blocks are aggregated back into a list
    while a single block is returned as a scalar.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]

    values = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            values.append(json.loads(text))
        except (ValueError, TypeError):
            values.append(text)

    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values
