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

"""A shell plugin offering MCP tools, for test_extension_tools.py.

Loaded only by the server subprocesses of that test, which put this folder's
parent on MARIADB_SHELL_PLUGINS_PATH. It imports neither mcp_plugin nor the
MCP SDK, as a plugin offering tools never has to.
"""

import mysqlsh
from mysqlsh.plugin_manager import plugin, plugin_function


@plugin
class mcpExtTest:
    """Functions offered as MCP tools of the ext_test group."""


@plugin_function("mcpExtTest.greet", shell=False, mcp={"group": "ext_test", "readOnly": True})
def greet(name, times=1, **options):
    """Greets someone.

    Says hello as often as asked.

    Args:
        name (str): Who to greet.
        times (int): How often.
        **options (dict): How to greet.

    Keyword Args:
        shout (bool): Upper case.
        greeting (str): The word to greet with.

    Returns:
        The greetings, as a list.
    """
    word = options.get("greeting", "hello")
    text = f"{word} {name}"
    if options.get("shout"):
        text = text.upper()

    return {"greetings": [text] * times}


@plugin_function("mcpExtTest.fail", shell=False, mcp={"group": "ext_test"})
def fail(reason):
    """Refuses, as a plugin does, with mysqlsh.Error.

    Args:
        reason (str): What to say.
    """
    raise mysqlsh.Error(f"Refused: {reason}")


@plugin_function("mcpExtTest.serverVersion", mcp={"group": "ext_test", "readOnly": True})
def server_version(session=None):
    """Returns the version of the server.

    Args:
        session (object): The session to use, the shell's own by default.
    """
    session = session or mysqlsh.globals.shell.get_session()
    return session.run_sql("SELECT VERSION() AS version")
