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

"""MCP server management functions for the MariaDB MCP Server Plugin"""

# cSpell:ignore mysqlsh MariaDB

from mysqlsh.plugin_manager import plugin_function
import mcp_plugin.lib as lib


@plugin_function("mcp.startServer", shell=True, cli=True, web=True)
def start_server(**options) -> None:
    """Starts the MariaDB MCP server.

    Starts the Model Context Protocol server that exposes the MariaDB AI
    Plugin capabilities to MCP-compatible clients. This is meant to be launched
    from the command line; it serves in the foreground and blocks for the
    lifetime of the server.

    With the default "streamable-http" transport it serves over HTTP on the
    given host and port. With the "stdio" transport it communicates over
    stdin/stdout and exits when stdin is closed.

    The shell's interactive mode is disabled while the server runs.

    Args:
        **options (dict): Options controlling how the server is started.

    Keyword Args:
        host (str): The host address to bind the server to. Only used by the
            streamable-http transport. Defaults to 127.0.0.1.
        port (int): The TCP port to listen on. Only used by the streamable-http
            transport. Defaults to 8080.
        transport (str): The MCP transport to use, either "streamable-http" or
            "stdio". Defaults to streamable-http.
        function_groups (list): The function groups to expose, allowing them to
            be loaded independently. Supported groups are "db", "sandbox" and
            "msm". Defaults to all groups.

    Returns:
        None
    """
    host = options.get("host", lib.general.DEFAULT_HOST)
    port = int(options.get("port", lib.general.DEFAULT_PORT))
    transport = options.get("transport", lib.general.DEFAULT_TRANSPORT)

    function_groups = options.get("function_groups", None)
    if function_groups is None:
        function_groups = list(lib.general.DEFAULT_FUNCTION_GROUPS)
    elif isinstance(function_groups, str):
        function_groups = [
            group.strip() for group in function_groups.split(",") if group.strip()
        ]

    lib.server.start(
        host=host,
        port=port,
        transport=transport,
        function_groups=function_groups,
    )
