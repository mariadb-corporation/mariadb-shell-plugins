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

    Multi-tenant mode is not an option here but part of the configuration,
    turned on with mcp setup --multiTenant=true. Such a server serves its users
    over HTTP only, each authenticated with their API key as a bearer token,
    each with their own connections and allowed paths.

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
            be loaded independently. Supported groups are "db", "msm",
            "sandbox", "migrator" and "util". Defaults to all groups, or to
            "db" in multi-tenant mode, which serves no other. The "migrator"
            group registers its tools only where the MySQL-to-MariaDB
            migration tooling is installed (see mcp.setup), and the "util"
            group only alongside "db", whose connections it works on.
        gui (bool): Serve for the MariaDB VS Code extension. The extension is
            a user interface the user drives directly on this machine, not an
            autonomous client, so two things the allow-lists exist to ask such
            a client about are turned on: every local path is accessible
            without being added to the allowed paths with mcp.setup, and the
            configured connections can be managed over the protocol with the
            db.add_connection and db.delete_connection tools that only this
            mode serves. Those tools, and db.list_connections, then take a
            "kind" naming which list of connections to work on: "mcp", the
            shared one mcp.setup curates, or "gui", the one the extension
            manages for itself. db.connect opens a connection from either,
            preferring the "gui" one where both name the same server.
        allowed_hosts (list): Additional values of the HTTP Host header to
            accept, for a server reached under a name that cannot be derived
            from the host it binds to - through a reverse proxy, a port forward
            or a DNS alias. Only used by the streamable-http transport. The
            names the bind address itself implies are always accepted, so this
            is only needed for those extra names; a request whose Host is none
            of them is refused, which is what protects an unauthenticated
            server from being driven by a page in a browser.
        ssl_certfile (str): A PEM certificate (or certificate chain) file to
            serve HTTPS with. Requires ssl_keyfile. Only used by the
            streamable-http transport.
        ssl_keyfile (str): The PEM private key file of ssl_certfile.
        max_connections (int): The most database connections the server holds
            open at once, for all clients together. Defaults to 64.
        public_url (str): The URL clients reach the MCP endpoint at, such as
            https://mcp.example.com/mcp, which OAuth tokens are issued for.
            Defaults to the one set with mcp setup --publicUrl.

    Returns:
        None
    """
    host = options.get("host", lib.general.DEFAULT_HOST)
    port = int(options.get("port", lib.general.DEFAULT_PORT))
    transport = options.get("transport", lib.general.DEFAULT_TRANSPORT)
    gui = bool(options.get("gui", False))

    # Left None when not given: the default depends on whether the server is
    # multi-tenant, which lib.server.start reads from the configuration.
    function_groups = options.get("function_groups", None)
    if isinstance(function_groups, str):
        function_groups = [
            group.strip() for group in function_groups.split(",") if group.strip()
        ]

    # Accepted as a list or, as the CLI passes it, a comma-separated string.
    allowed_hosts = options.get("allowed_hosts", None) or []
    if isinstance(allowed_hosts, str):
        allowed_hosts = [
            name.strip() for name in allowed_hosts.split(",") if name.strip()
        ]

    lib.server.start(
        host=host,
        port=port,
        transport=transport,
        function_groups=function_groups,
        allowed_hosts=allowed_hosts,
        gui=gui,
        ssl_certfile=options.get("ssl_certfile", None),
        ssl_keyfile=options.get("ssl_keyfile", None),
        max_connections=options.get("max_connections", None),
        public_url=options.get("public_url", None),
    )
