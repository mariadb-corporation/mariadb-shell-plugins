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
The server is launched as a ``mariadb-shell`` subprocess running
``mcp start-server --transport=stdio`` and is driven with the MCP client SDK.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager

# Two connections used by the connection round-trip test. The URIs only need to
# be unique keys for the secret store; the round-trip test does not open them.
TEST_CONNECTION_URIS = [
    "mcp_pytest_a@127.0.0.1:33061",
    "mcp_pytest_b@127.0.0.1:33062",
]

TEST_CONNECTION_PASSWORD = "mcp_pytest_password"

# Time budget (seconds) for a single MCP stdio round-trip, including the time it
# takes to start the mariadb-shell subprocess and load the plugins.
_MCP_TIMEOUT = 90


def shell_binary() -> str:
    """Returns the path to the mariadb-shell binary to use.

    Falls back to the pre-rename ``mysqlsh`` name so the suite still runs
    against an older shell build that has not been renamed yet.
    """
    return (
        os.environ.get("MARIADB_SHELL")
        or shutil.which("mariadb-shell")
        or "mariadb-shell"
    )


def find_free_port() -> int:
    """Returns a currently-free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_binary_available() -> bool:
    """Returns whether a MariaDB/MySQL server binary is on the PATH.

    The stdio server subprocess inherits this process's PATH, so this is a
    reliable proxy for whether a sandbox can be deployed.
    """
    return bool(shutil.which("mariadbd") or shutil.which("mysqld"))


def _stdio_server_params(function_groups):
    """Builds the StdioServerParameters for the MCP server subprocess.

    Args:
        function_groups (list): The function groups to expose.

    Returns:
        A StdioServerParameters instance.
    """
    from mcp import StdioServerParameters

    env = os.environ.copy()
    # When the test runner enables subprocess coverage, point the subprocess at
    # the coverage config so its sitecustomize (on PYTHONPATH) starts coverage.
    coverage_rc = env.get("MCP_COVERAGE_RC")
    if coverage_rc:
        env["COVERAGE_PROCESS_START"] = coverage_rc

    return StdioServerParameters(
        command=shell_binary(),
        # --quiet-start=2 suppresses the shell banner so stdout carries only the
        # MCP JSON-RPC stream. The subprocess inherits
        # MARIADB_SHELL_USER_CONFIG_HOME from the environment, so it sees the
        # same secrets and settings.json.
        args=[
            "--quiet-start=2",
            "--",
            "mcp",
            "start-server",
            "--transport=stdio",
            f"--function-groups={','.join(function_groups)}",
        ],
        env=env,
    )


async def _acall_tool(
    function_groups, tool_name, arguments, timeout, elicitation_callback
):
    """Opens a stdio MCP session, calls one tool and returns the result."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_server_params(function_groups)

    async def _run():
        async with stdio_client(params) as (read, write):
            # Passing an elicitation_callback also makes the client advertise
            # the elicitation capability, so the server will actually send
            # elicitation/create requests instead of failing them.
            async with ClientSession(
                read, write, elicitation_callback=elicitation_callback
            ) as session:
                await session.initialize()
                return await session.call_tool(tool_name, arguments)

    return await asyncio.wait_for(_run(), timeout=timeout)


def call_tool(
    function_groups, tool_name, arguments=None, timeout=None, elicitation_callback=None
):
    """Calls an MCP tool over stdio and returns the CallToolResult.

    Args:
        function_groups (list): The function groups the server should expose.
        tool_name (str): The MCP tool to call.
        arguments (dict): The tool arguments.
        timeout (float): Round-trip timeout in seconds. Defaults to the module
            default; pass a larger value for slow operations like a deploy.
        elicitation_callback: Optional async ``(context, params)`` callback used
            to answer elicitation/create requests from the server. When omitted
            the client does not advertise the elicitation capability.

    Returns:
        The CallToolResult returned by the MCP client.
    """
    return asyncio.run(
        _acall_tool(
            function_groups,
            tool_name,
            arguments or {},
            timeout if timeout is not None else _MCP_TIMEOUT,
            elicitation_callback,
        )
    )


async def _alist_tool_names(function_groups, timeout):
    """Lists the tool names a server exposes for the given function groups."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_server_params(function_groups)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(session.list_tools(), timeout=timeout)

            return [tool.name for tool in result.tools]


def list_tool_names(function_groups, timeout=None):
    """Returns the names of the tools a server advertises over stdio.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Round-trip timeout in seconds.

    Returns:
        The list of tool names.
    """
    return asyncio.run(
        _alist_tool_names(
            function_groups, timeout if timeout is not None else _MCP_TIMEOUT
        )
    )


@asynccontextmanager
async def mcp_session(function_groups, timeout=None):
    """Opens a persistent stdio MCP session against a single server subprocess.

    Multiple tool calls made through the yielded ``call`` coroutine share the
    same server process, which is required for stateful tools such as
    db.connect / db.execute_sql / db.close (the open session is cached in the
    server process).

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Per-call timeout in seconds.

    Yields:
        An async ``call(tool_name, arguments=None)`` coroutine returning the
        CallToolResult.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    call_timeout = timeout if timeout is not None else _MCP_TIMEOUT
    params = _stdio_server_params(function_groups)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool_name, arguments=None):
                return await asyncio.wait_for(
                    session.call_tool(tool_name, arguments or {}),
                    timeout=call_timeout,
                )

            yield call


def _wait_for_port(host, port, timeout):
    """Blocks until a TCP port accepts connections or the timeout elapses.

    Args:
        host (str): The host to connect to.
        port (int): The port to probe.
        timeout (float): Maximum time to wait, in seconds.

    Returns:
        None

    Raises:
        TimeoutError: If the port does not accept a connection in time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"Server did not start listening on {host}:{port}")


@asynccontextmanager
async def http_session(function_groups, timeout=None):
    """Runs the server over streamable-http and yields a per-call coroutine.

    A ``mariadb-shell`` subprocess is launched with ``--transport=streamable-http`` on
    a free port; once it is listening, an MCP streamable-http client connects to
    ``http://127.0.0.1:<port>/mcp`` and initializes a session. Multiple tool
    calls made through the yielded ``call`` coroutine share the same server
    process. The subprocess is terminated on exit.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Per-call and startup timeout in seconds.

    Yields:
        An async ``call(tool_name, arguments=None)`` coroutine returning the
        CallToolResult.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    call_timeout = timeout if timeout is not None else _MCP_TIMEOUT
    host = "127.0.0.1"
    port = find_free_port()

    env = os.environ.copy()
    coverage_rc = env.get("MCP_COVERAGE_RC")
    if coverage_rc:
        env["COVERAGE_PROCESS_START"] = coverage_rc

    proc = subprocess.Popen(
        [
            shell_binary(),
            "--quiet-start=2",
            "--",
            "mcp",
            "start-server",
            "--transport=streamable-http",
            f"--host={host}",
            f"--port={port}",
            f"--function-groups={','.join(function_groups)}",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_port(host, port, call_timeout)

        url = f"http://{host}:{port}/mcp"
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(tool_name, arguments=None):
                    return await asyncio.wait_for(
                        session.call_tool(tool_name, arguments or {}),
                        timeout=call_timeout,
                    )

                yield call
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def tool_payload(result):
    """Extracts the Python payload returned by a tool from a CallToolResult.

    Prefers the structured content when present. Otherwise falls back to the
    text content blocks: the server emits one content block per element when a
    tool returns a list, so multiple blocks are aggregated back into a list
    while a single block is returned as a scalar.
    """
    structured = getattr(result, "structured_content", None)
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
