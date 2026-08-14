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

"""The MariaDB MCP Server Plugin"""

# cSpell:ignore mysqlsh MariaDB

from mysqlsh.plugin_manager import plugin_function
import mcp_plugin.lib as lib


@plugin_function("mcp.info", shell=True, cli=True, web=True)
def info() -> str:
    """Returns basic information about this plugin.

    Returns:
        str
    """
    return (
        f"MariaDB MCP Server Plugin Version {lib.general.VERSION} PREVIEW\n"
        "Warning! For testing purposes only!"
    )


@plugin_function("mcp.version", shell=True, cli=True, web=True)
def version() -> str:
    """Returns the version number of the plugin.

    Returns:
        str
    """
    return lib.general.VERSION


@plugin_function("mcp.setup", shell=True, cli=True, web=False)
def setup() -> None:
    """Interactively configures the MariaDB MCP server.

    Guides the user through configuring the MariaDB connections and the local
    directories the MCP server is allowed to access.

    On the first run it walks through adding connections and allowed paths. On
    subsequent runs it presents a menu to add or delete connections and paths.

    For each connection, the URI is entered, the password is prompted for and
    the connection is verified before the password is stored in the shell's
    secret store. The allowed directories are stored in a settings.json file in
    the plugin data directory.

    Returns:
        None
    """
    lib.setup.run_setup()
