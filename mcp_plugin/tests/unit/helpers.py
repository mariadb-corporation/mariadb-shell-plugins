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

"""Test helpers for the MariaDB MCP Server Plugin.

Provides utilities to talk to the plugin's MCP server over the stdio transport.
The server is launched as a ``mariadb-shell`` subprocess running
``mcp start-server --transport=stdio`` and is driven with the MCP client SDK.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver httpx

import asyncio
import json
import os
import shlex
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

from mcp_plugin.lib import config

# Two connections used by the connection round-trip test. The URIs only need to
# be unique keys for the secret store; the round-trip test does not open them.
# Spelled as they normalize, scheme included, so that what is stored is what
# db.list_connections reports back.
TEST_CONNECTION_URIS = [
    "mariadb://mcp_pytest_a@127.0.0.1:33061",
    "mariadb://mcp_pytest_b@127.0.0.1:33062",
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


def shell_options() -> list:
    """Returns the extra options every shell the tests start is given.

    Taken from MARIADB_SHELL_OPTIONS, which run_tests.py sets from its
    --shell-options, for example ``--log-level=debug3``.
    """
    return shlex.split(os.environ.get("MARIADB_SHELL_OPTIONS", ""))


def shell_command() -> list:
    """Returns the shell binary followed by the extra options to start it with."""
    return [shell_binary(), *shell_options()]


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


def _stdio_server_params(function_groups, gui=False):
    """Builds the StdioServerParameters for the MCP server subprocess.

    Args:
        function_groups (list): The function groups to expose.
        gui (bool): Whether to pass --gui, which serves the server as the
            MariaDB VS Code extension is served.

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
            *shell_options(),
            "--quiet-start=2",
            "--",
            "mcp",
            "start-server",
            "--transport=stdio",
            f"--function-groups={','.join(function_groups)}",
            # Spelled as the shell's own help lists it. This is the ONE place
            # the real option name is exercised, so a rename that only touched
            # the plugin would be caught here and nowhere else.
            *(["--gui"] if gui else []),
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


async def _alist_tools(function_groups, timeout, gui=False):
    """Lists the tools a server exposes for the given function groups."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_server_params(function_groups, gui)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(session.list_tools(), timeout=timeout)

            return result.tools


async def _alist_tool_names(function_groups, timeout, gui=False):
    """Lists the tool names a server exposes for the given function groups."""
    return [tool.name for tool in await _alist_tools(function_groups, timeout, gui)]


def list_tools(function_groups, timeout=None, gui=False):
    """Returns the tools a server advertises over stdio, schemas included.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Round-trip timeout in seconds.
        gui (bool): Whether to start the server with --gui.

    Returns:
        The tools, by name.
    """
    tools = asyncio.run(
        _alist_tools(
            function_groups,
            timeout if timeout is not None else _MCP_TIMEOUT,
            gui,
        )
    )
    return {tool.name: tool for tool in tools}


async def _ainstructions(function_groups):
    """The instructions a server answers initialize with."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_server_params(function_groups, False)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()

            return result.instructions


def server_instructions(function_groups):
    """Returns the instructions a server sends its clients over stdio.

    Args:
        function_groups (list): The function groups the server should expose.

    Returns:
        The instructions, or None when the server sends none.
    """
    return asyncio.run(_ainstructions(function_groups))


def list_tool_names(function_groups, timeout=None, gui=False):
    """Returns the names of the tools a server advertises over stdio.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Round-trip timeout in seconds.
        gui (bool): Whether to start the server with --gui.

    Returns:
        The list of tool names.
    """
    return asyncio.run(
        _alist_tool_names(
            function_groups,
            timeout if timeout is not None else _MCP_TIMEOUT,
            gui,
        )
    )


@asynccontextmanager
async def mcp_session(function_groups, timeout=None, gui=False):
    """Opens a persistent stdio MCP session against a single server subprocess.

    Multiple tool calls made through the yielded ``call`` coroutine share the
    same server process, which is required for stateful tools such as
    db.connect / db.execute_sql / db.close (the open session is cached in the
    server process).

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Per-call timeout in seconds.
        gui (bool): Serve as the VS Code extension's server (--gui), which
            adds db.add_connection and db.delete_connection.

    Yields:
        An async ``call(tool_name, arguments=None)`` coroutine returning the
        CallToolResult.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    call_timeout = timeout if timeout is not None else _MCP_TIMEOUT
    params = _stdio_server_params(function_groups, gui=gui)

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
async def http_server(function_groups, timeout=None, bind_host=None, extra_args=()):
    """Runs the MCP server over streamable-http and yields its endpoint URL.

    A ``mariadb-shell`` subprocess is launched with
    ``--transport=streamable-http`` on a free port; once it is listening, the
    ``http://127.0.0.1:<port>/mcp`` URL is yielded. Any number of client
    sessions can be opened against it with :func:`http_client_session`. The
    subprocess is terminated on exit.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Startup timeout in seconds.
        bind_host (str): The ``--host`` to start the server with. Defaults to
            127.0.0.1. The yielded URL always dials 127.0.0.1, so this is only
            useful for hosts that still bind loopback - which is what the
            DNS-rebinding test needs.
        extra_args: More ``start-server`` options, such as
            ``--trusted-proxies=127.0.0.1``.

    Yields:
        The MCP endpoint URL of the running server.
    """
    host = bind_host or "127.0.0.1"
    port = find_free_port()

    env = os.environ.copy()
    coverage_rc = env.get("MCP_COVERAGE_RC")
    if coverage_rc:
        env["COVERAGE_PROCESS_START"] = coverage_rc

    proc = subprocess.Popen(
        [
            *shell_command(),
            "--quiet-start=2",
            "--",
            "mcp",
            "start-server",
            "--transport=streamable-http",
            f"--host={host}",
            f"--port={port}",
            f"--function-groups={','.join(function_groups)}",
            *extra_args,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Always dialled over 127.0.0.1: bind_host only ever names something
        # that resolves to loopback anyway.
        _wait_for_port(
            "127.0.0.1", port, timeout if timeout is not None else _MCP_TIMEOUT
        )

        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def mcp_http_client(headers=None):
    """Builds the HTTP client the streamable-http transport would build itself.

    Handed to :func:`http_client_session` when a test needs to control the
    headers the transport sends - the transport takes no ``headers`` argument of
    its own, only a pre-configured client. The returned client is NOT entered
    yet; ``http_client_session`` does that. Its ``headers`` mapping may be
    changed at any point, including between calls on a live session, as httpx
    merges them into each request as it is built.

    Args:
        headers (dict): Headers to send with every request.

    Returns:
        An httpx AsyncClient with the MCP defaults.
    """
    from mcp.shared._httpx_utils import create_mcp_http_client

    return create_mcp_http_client(headers=headers)


@asynccontextmanager
async def http_client_session(url, timeout=None, headers=None, http_client=None):
    """Opens an MCP streamable-http client session against a running server.

    Args:
        url (str): The MCP endpoint URL, as yielded by :func:`http_server`.
        timeout (float): Per-call timeout in seconds.
        headers (dict): Extra HTTP headers to send with every request. Used to
            drive the header-forging tests; the transport's own headers are
            added on top of these. Ignored when ``http_client`` is given.
        http_client: A client from :func:`mcp_http_client` to use instead, for
            when the test has to keep a handle on it. It is entered and closed
            here.

    Yields:
        An async ``call(tool_name, arguments=None)`` coroutine returning the
        CallToolResult.
    """
    from contextlib import AsyncExitStack

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    call_timeout = timeout if timeout is not None else _MCP_TIMEOUT

    async with AsyncExitStack() as stack:
        if http_client is None and headers:
            http_client = mcp_http_client(headers=headers)

        if http_client is not None:
            http_client = await stack.enter_async_context(http_client)

        read, write = await stack.enter_async_context(
            streamable_http_client(url, http_client=http_client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        async def call(tool_name, arguments=None):
            return await asyncio.wait_for(
                session.call_tool(tool_name, arguments or {}),
                timeout=call_timeout,
            )

        yield call


@asynccontextmanager
async def http_session(function_groups, timeout=None, headers=None):
    """Runs the server over streamable-http and yields a per-call coroutine.

    Convenience wrapper around :func:`http_server` plus one
    :func:`http_client_session`, for tests that need only a single client.
    Multiple tool calls made through the yielded ``call`` coroutine share the
    same server process, which is required for stateful tools such as
    db.connect / db.execute_sql / db.close.

    Args:
        function_groups (list): The function groups the server should expose.
        timeout (float): Per-call and startup timeout in seconds.
        headers (dict): Extra HTTP headers to send with every request.

    Yields:
        An async ``call(tool_name, arguments=None)`` coroutine returning the
        CallToolResult.
    """
    async with http_server(function_groups, timeout=timeout) as url:
        async with http_client_session(url, timeout=timeout, headers=headers) as call:
            yield call


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


def result_set(entry, index=0):
    """One result set of a statement's result: the first by default."""
    return entry["result_sets"][index]


def tool_rows(result):
    """The rows of the first result set a db.execute_sql call returned."""
    return result_set(tool_payload(result))["rows"]


# --- the secret store around a test ------------------------------------------


def backup_connections() -> dict:
    """Returns every stored connection of every kind, with its password and details.

    Keyed by ``(kind, uri)`` because the two lists can hold the same URI under
    two different passwords, and a backup that collapsed them would restore the
    wrong one.

    The details - folder, caption, color - are part of what is backed up.
    Deleting a connection drops them, so a restore that re-stored only the
    password would file every connection at the top level with no caption -
    which, run against a developer's own store, emptied their /Sandboxes
    folder on every test run.

    Returns:
        A dict mapping ``(kind, uri)`` to ``(password, details)``.
    """
    return {
        (kind, uri): (
            config.get_connection_password(uri, kind),
            config.get_connection_details(uri, kind),
        )
        for kind in config.SUPPORTED_CONNECTION_KINDS
        for uri in config.list_stored_connection_uris(kind)
    }


def clear_connections() -> None:
    """Deletes every stored connection of every kind, best effort."""
    for kind in config.SUPPORTED_CONNECTION_KINDS:
        for uri in config.list_stored_connection_uris(kind):
            try:
                config.delete_connection(uri, kind)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


def restore_connections(connections: dict) -> None:
    """Re-stores what :func:`backup_connections` returned, best effort."""
    for (kind, uri), (password, details) in connections.items():
        try:
            config.store_connection(uri, password, kind, **details)
        except Exception:  # noqa: BLE001 - best-effort restore
            pass


# --- stubs of shell results, for the tests of the db tools' serialization -----


class StubType:
    """A mysql.Type constant: its name is its data."""

    def __init__(self, data):
        self.data = data


class MetadataColumn:
    """A column with the shell's Column getters the db tools read."""

    def __init__(self, label, kind="STRING", flags="", length=0,
                 fractional_digits=0):
        self.label = label
        self.kind = kind
        self.flags = flags
        self.length = length
        self.fractional_digits = fractional_digits

    def get_column_label(self):
        return self.label

    def get_type(self):
        return StubType(self.kind)

    def get_flags(self):
        return self.flags

    def get_length(self):
        return self.length

    def get_fractional_digits(self):
        return self.fractional_digits


class MultiSetResult:
    """A result over several result sets, walked as the shell walks them.

    Each entry of ``sets`` is ``(columns, rows)`` for a set with data, or
    None for one without - the status a CALL ends on. A column is a
    ``MetadataColumn``, or just its label for a plain STRING column.
    """

    affected_items_count = 0
    warnings_count = 0

    def __init__(self, sets):
        self._sets = sets
        self._at = 0

    def has_data(self):
        return self._sets[self._at] is not None

    def get_columns(self):
        return [
            MetadataColumn(column) if isinstance(column, str) else column
            for column in self._sets[self._at][0]
        ]

    def fetch_all(self):
        return self._sets[self._at][1]

    def next_result(self):
        self._at += 1
        return self._at < len(self._sets)

    def get_warnings(self):
        return []


class TableSession:
    """Answers every statement from a table of rows of one column.

    It honours the LIMIT and OFFSET the paging adds, on a line of their own,
    and records every statement it is asked to run.
    """

    def __init__(self, rows, column="n"):
        self.rows = list(rows)
        self.column = column
        self.statements = []

    def run_sql(self, sql, _params=None):
        self.statements.append(sql)
        rows = self.rows
        if "\nLIMIT " in sql:
            parts = sql.rsplit("\nLIMIT ", 1)[1].split(" OFFSET ")
            offset = int(parts[1]) if len(parts) > 1 else 0
            rows = rows[offset:offset + int(parts[0])]

        return MultiSetResult([([self.column], [[value] for value in rows])])

    def close(self):
        pass


# --- the db tools over a stub session, without a server -----------------------


class ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


@contextmanager
def stub_db_tools(monkeypatch, make_session):
    """Registers the db tools over stub sessions and connects one.

    The tools are called as an HTTP client would call them, from one address
    on one MCP session, on a connection to a stored URI whose sessions
    ``make_session`` builds.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        make_session: Builds a session; called once per session opened.

    Yields:
        A namespace with ``sql`` and ``script`` - db.execute_sql and
        db.execute_sql_script on that connection, taking the statement or
        script and keyword arguments - and ``opened``, the sessions opened,
        so a test can see what reached the server.
    """
    # Imported here: the db tools need the MCP SDK, which the tests that use
    # only the other helpers do not.
    from mcp_plugin.lib import db_functions, general

    uri = "root@127.0.0.1:3306"
    opened = []

    def _fake_open_session(_uri, kind=None, mcp_user_id=None):
        session = make_session()
        opened.append(session)

        return session

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)
    monkeypatch.setattr(
        db_functions.config,
        "list_stored_connection_uris",
        lambda kind=None, mcp_user_id=None: [uri],
    )

    recorder = ToolRecorder()
    db_functions.register_db_tools(recorder)

    db_functions._sessions.clear()
    general.set_active_transport(general.TRANSPORT_STREAMABLE_HTTP)
    context = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                client=SimpleNamespace(host="192.0.2.10", port=54321),
                headers={
                    general.MCP_SESSION_ID_HEADER: "0123456789abcdef0123456789abcdef"
                },
            )
        )
    )
    try:
        connection_id = recorder.tools["db.connect"](context, uri)
        yield SimpleNamespace(
            sql=lambda sql, **kwargs: recorder.tools["db.execute_sql"](
                context, connection_id, sql, **kwargs
            ),
            script=lambda script, **kwargs: recorder.tools[
                "db.execute_sql_script"
            ](context, connection_id, script, **kwargs),
            opened=opened,
        )
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()
