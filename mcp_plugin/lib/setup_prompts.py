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

"""Interactive prompt primitives shared by the mcp.setup flows.

Both :mod:`mcp_plugin.lib.setup` and :mod:`mcp_plugin.lib.setup_migrator` ask
the user questions, so the primitives live here rather than in either of them:
`setup` imports `setup_migrator` to build its menu, so anything `setup_migrator`
needed back from `setup` would be an import cycle.

Everything goes through :func:`shell`, which is looked up per call rather than
held in a module global - that is the single seam the tests replace to script a
whole setup run's answers.
"""

# cSpell:ignore mysqlsh MariaDB

import mysqlsh


def shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def ask(message: str, options: dict = None) -> str:
    """Prompts the user for input, returning the entered (stripped) string."""
    return shell().prompt(message, options if options is not None else {}).strip()


def password(message: str) -> str:
    """Prompts the user for a password without echoing it."""
    return shell().prompt(message, {"type": "password"})


def yes_no(message: str, default: bool = True) -> bool:
    """Prompts the user for a yes/no answer.

    Args:
        message (str): The question to ask.
        default (bool): The answer to use when the user just presses Enter.

    Returns:
        The user's answer as a boolean.
    """
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = ask(message + suffix).lower()
    if answer == "":
        return default
    return answer in ("y", "yes")


def select_index(message: str, count: int) -> int:
    """Prompts the user to pick an item number in the range 1..count.

    Args:
        message (str): The prompt message.
        count (int): The number of items to choose from.

    Returns:
        The selected zero-based index, or -1 if the user cancelled.
    """
    while True:
        answer = ask(message + " (or leave empty to cancel): ")
        if answer == "":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        print(f"Please enter a number between 1 and {count}.")
