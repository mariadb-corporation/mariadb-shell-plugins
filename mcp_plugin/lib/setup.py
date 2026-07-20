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

"""Interactive setup for the MariaDB MCP Server Plugin.

Guides the user through configuring the MariaDB connections and the local
directories the MCP server is allowed to access. Connections are verified with
``shell.open_session`` before their password is stored (see
:mod:`mcp_plugin.lib.config`).
"""

# cSpell:ignore mysqlsh MariaDB

import os

import mysqlsh

from mcp_plugin.lib import config, general


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def _prompt(message: str, options: dict = None) -> str:
    """Prompts the user for input, returning the entered (stripped) string."""
    return _shell().prompt(message, options if options is not None else {}).strip()


def _prompt_password(message: str) -> str:
    """Prompts the user for a password without echoing it."""
    return _shell().prompt(message, {"type": "password"})


def _prompt_yes_no(message: str, default: bool = True) -> bool:
    """Prompts the user for a yes/no answer.

    Args:
        message (str): The question to ask.
        default (bool): The answer to use when the user just presses Enter.

    Returns:
        The user's answer as a boolean.
    """
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = _prompt(message + suffix).lower()
    if answer == "":
        return default
    return answer in ("y", "yes")


def _select_index(message: str, count: int) -> int:
    """Prompts the user to pick an item number in the range 1..count.

    Args:
        message (str): The prompt message.
        count (int): The number of items to choose from.

    Returns:
        The selected zero-based index, or -1 if the user cancelled.
    """
    while True:
        answer = _prompt(message + " (or leave empty to cancel): ")
        if answer == "":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer) - 1
        print(f"Please enter a number between 1 and {count}.")


# --- Connections -----------------------------------------------------------


def _print_connections() -> list:
    """Prints the configured connections and returns them."""
    connections = config.list_connection_uris()
    if connections:
        print("\nConfigured connections:")
        for index, uri in enumerate(connections, start=1):
            print(f"  {index}. {uri}")
    else:
        print("\nNo connections configured yet.")
    return connections


def _add_connection() -> None:
    """Prompts for a connection URI and password, verifies and stores it."""
    uri = _prompt(
        "Enter the MariaDB connection URI (e.g. user@host:3306): "
    )
    if uri == "":
        return

    password = _prompt_password(f"Enter the password for '{uri}': ")

    # Verify the credentials by opening (and immediately closing) a session.
    try:
        connection_data = _shell().parse_uri(uri)
        connection_data["password"] = password
        session = _shell().open_session(connection_data)
        session.close()
    except Exception as error:  # noqa: BLE001 - surface any connection failure
        print(f"Could not connect to '{uri}': {error}")
        print("The connection was not stored.")
        return

    config.store_connection(uri, password)
    print(f"Connection '{uri}' verified and stored.")


def _delete_connection() -> None:
    """Prompts the user to delete one of the configured connections."""
    connections = _print_connections()
    if not connections:
        return

    index = _select_index("Enter the number of the connection to delete", len(connections))
    if index < 0:
        return

    uri = connections[index]
    config.delete_connection(uri)
    print(f"Connection '{uri}' deleted.")


# --- Allowed paths ---------------------------------------------------------


def _print_paths() -> list:
    """Prints the configured allowed paths and returns them."""
    paths = config.get_allowed_paths()
    if paths:
        print("\nAllowed paths:")
        for index, path in enumerate(paths, start=1):
            print(f"  {index}. {path}")
    else:
        print("\nNo allowed paths configured yet.")
    return paths


def _add_path() -> None:
    """Prompts for a directory to allow, defaulting to the current directory."""
    default_path = os.path.abspath(os.getcwd())
    entered = _prompt(
        f"Enter a directory the MCP server may access (default: {default_path}): "
    )
    path = os.path.abspath(os.path.expanduser(entered)) if entered else default_path

    if not os.path.isdir(path):
        print(f"'{path}' is not an existing directory. It was not added.")
        return

    paths = config.get_allowed_paths()
    if path in paths:
        print(f"'{path}' is already allowed.")
        return

    paths.append(path)
    config.set_allowed_paths(paths)
    print(f"Allowed path '{path}' added.")


def _delete_path() -> None:
    """Prompts the user to delete one of the allowed paths."""
    paths = _print_paths()
    if not paths:
        return

    index = _select_index("Enter the number of the path to delete", len(paths))
    if index < 0:
        return

    path = paths.pop(index)
    config.set_allowed_paths(paths)
    print(f"Allowed path '{path}' deleted.")


# --- Entry points ----------------------------------------------------------


def _first_run() -> None:
    """Guided first-run configuration: add connections, then allowed paths."""
    print("Let's configure the MariaDB connections the MCP server may use.")
    while _prompt_yes_no("Add a connection?", default=True):
        _add_connection()

    print(
        "\nNow choose the local directories the MCP server is allowed to access."
    )
    while _prompt_yes_no("Add an allowed path?", default=True):
        _add_path()

    # Ensure a settings file exists so subsequent runs use the management menu.
    config.set_allowed_paths(config.get_allowed_paths())


def _menu() -> None:
    """Management menu: add/delete connections and allowed paths."""
    actions = {
        "1": _add_connection,
        "2": _delete_connection,
        "3": _add_path,
        "4": _delete_path,
    }
    while True:
        _print_connections()
        _print_paths()
        print(
            "\nWhat would you like to do?\n"
            "  1. Add a connection\n"
            "  2. Delete a connection\n"
            "  3. Add an allowed path\n"
            "  4. Delete an allowed path\n"
            "  5. Finish"
        )
        choice = _prompt("Enter your choice: ")
        if choice == "5" or choice == "":
            break
        action = actions.get(choice)
        if action is None:
            print("Please enter a number between 1 and 5.")
            continue
        action()


def run_setup() -> None:
    """Runs the interactive MCP server setup.

    Returns:
        None
    """
    if not _shell().options.useWizards:
        raise mysqlsh.Error(
            "mcp.setup must be run from an interactive shell session."
        )

    print("=== MariaDB MCP Server setup ===")
    print(f"Configuration is stored in: {general.get_mcp_plugin_data_path()}")

    if config.settings_file_exists() or config.list_connection_uris():
        _menu()
    else:
        _first_run()

    print("\nSetup complete.")
