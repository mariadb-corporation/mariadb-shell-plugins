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

"""MCP server lifecycle.

The MCP server is meant to be launched from the command line, e.g.

    mariadb-shell -- mcp start-server --port=8080

It builds a FastMCP server, registers the requested function groups on it - the
database tools (see :mod:`mcp_plugin.lib.db_functions`) and/or the MariaDB
Schema Management tools (see :mod:`mcp_plugin.lib.msm_functions`), which can be
loaded independently - and serves it in the foreground using one of two
transports:

* ``streamable-http`` (default): served over HTTP on the configured host/port.
* ``stdio``: communicates over stdin/stdout; its lifetime is driven by the
  client. The real stdout is reserved for the JSON-RPC protocol and all other
  output is redirected to stderr (see :func:`_serve_stdio`).

The shell's interactive mode is disabled before serving, so the wrapped ``msm``
plugin functions return their results instead of prompting for input.
"""

# cSpell:ignore mysqlsh MariaDB fastmcp streamable fdopen dup2

import os
import sys

import mysqlsh

from mcp_plugin.lib import db_functions, general, msm_functions, sandbox_functions


# Maps a function group name to the callback that registers its tools.
_FUNCTION_GROUP_REGISTRARS = {
    general.FUNCTION_GROUP_DB: db_functions.register_db_tools,
    general.FUNCTION_GROUP_MSM: msm_functions.register_msm_tools,
    general.FUNCTION_GROUP_SANDBOX: sandbox_functions.register_sandbox_tools,
}


def build_mcp_server(host: str, port: int, function_groups):
    """Builds and configures the MariaDB MCP server.

    Args:
        host (str): The host address to bind the server to.
        port (int): The TCP port to listen on.
        function_groups (list): The function groups whose tools should be
            registered on the server.

    Returns:
        The configured FastMCP server instance.
    """
    # Imported lazily so that the plugin can be loaded even when the optional
    # `mcp` dependency is not available.
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("MariaDB MCP Server", host=host, port=port)
    # The full list of enabled groups is handed to every registrar, so a group
    # can leave out the tools that depend on another group not being served.
    for group in function_groups:
        _FUNCTION_GROUP_REGISTRARS[group](server, function_groups)

    return server


def start(host: str, port: int, transport: str, function_groups) -> None:
    """Builds and serves the MCP server using the given transport.

    Disables the shell's interactive mode and then serves the MCP server in the
    foreground, blocking for the lifetime of the server.

    Args:
        host (str): The host address to bind to (streamable-http only).
        port (int): The TCP port to listen on (streamable-http only).
        transport (str): The MCP transport to use, either "streamable-http" or
            "stdio".
        function_groups (list): The function groups whose tools should be
            exposed by the server.

    Returns:
        None
    """
    if transport not in general.SUPPORTED_TRANSPORTS:
        raise mysqlsh.Error(
            f"Unsupported transport '{transport}'. Supported transports are: "
            f"{', '.join(general.SUPPORTED_TRANSPORTS)}."
        )

    if not function_groups:
        raise mysqlsh.Error(
            "At least one function group must be enabled. Supported function "
            f"groups are: {', '.join(general.SUPPORTED_FUNCTION_GROUPS)}."
        )

    unknown_groups = [
        group for group in function_groups if group not in _FUNCTION_GROUP_REGISTRARS
    ]
    if unknown_groups:
        raise mysqlsh.Error(
            f"Unknown function group(s): {', '.join(unknown_groups)}. Supported "
            f"function groups are: {', '.join(general.SUPPORTED_FUNCTION_GROUPS)}."
        )

    # Disable interactive mode so the wrapped msm functions return their
    # results instead of prompting for input.
    mysqlsh.globals.shell.options.useWizards = False

    mcp_server = build_mcp_server(
        host=host, port=port, function_groups=function_groups
    )

    if transport == general.TRANSPORT_STDIO:
        _serve_stdio(mcp_server)
    else:
        mcp_server.run(transport=transport)


def _serve_stdio(mcp_server) -> None:
    """Serves the MCP server over stdio, protecting the JSON-RPC stream.

    In stdio mode the JSON-RPC messages are exchanged over the process stdout.
    Any other output produced while a tool runs (shell progress messages,
    Python prints, or C-level writes to file descriptor 1) would corrupt that
    stream. To prevent this, the real stdout is duplicated and handed to the
    MCP transport, then file descriptor 1 and Python's ``sys.stdout`` are
    redirected to stderr for the lifetime of the server so stray output can
    never reach the client.

    Args:
        mcp_server: The FastMCP server instance to serve.

    Returns:
        None
    """
    import io

    import anyio
    from mcp.server.stdio import stdio_server

    # Reserve the real stdout (fd 1) for the protocol.
    protocol_fd = os.dup(1)
    protocol_stream = io.TextIOWrapper(
        os.fdopen(protocol_fd, "wb"), encoding="utf-8"
    )

    # Redirect fd 1 (C-level writes) and Python's sys.stdout to stderr so that
    # any output produced while serving cannot corrupt the protocol stream.
    saved_sys_stdout = sys.stdout
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    async def _run():
        # stdio_server does not close the stream we pass in.
        async with stdio_server(stdout=anyio.wrap_file(protocol_stream)) as (
            read_stream,
            write_stream,
        ):
            await mcp_server._mcp_server.run(
                read_stream,
                write_stream,
                mcp_server._mcp_server.create_initialization_options(),
            )

    try:
        anyio.run(_run)
    finally:
        # Restore fd 1 from the reserved copy, then restore sys.stdout and
        # release the reserved stream (which closes protocol_fd).
        os.dup2(protocol_fd, 1)
        sys.stdout = saved_sys_stdout
        protocol_stream.close()
