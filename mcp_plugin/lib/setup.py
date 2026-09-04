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

"""Interactive setup for the MariaDB MCP Server Plugin.

Guides the user through configuring the MariaDB connections and the local
directories the MCP server is allowed to access. Connections are verified with
``shell.open_session`` before their password is stored (see
:mod:`mcp_plugin.lib.config`).

The management menu also offers the MySQL-to-MariaDB migration tooling, which
lives in :mod:`mcp_plugin.lib.setup_migration`: it is a menu-only step, never
part of the first run, and it is left out altogether on platforms the tooling
does not run on. The prompt primitives are in
:mod:`mcp_plugin.lib.setup_prompts`, shared with that module.
"""

# cSpell:ignore mysqlsh MariaDB

import os

import mysqlsh

from mcp_plugin.lib import config, general, setup_migration
from mcp_plugin.lib import setup_prompts as prompts


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
    """Prompts for a connection URI and password, verifies and stores it.

    What is stored is the normalized URI rather than what was typed: it is the
    key the connection is then looked up under, and one canonical spelling per
    connection is what keeps the same connection from being configured twice
    over (see :func:`mcp_plugin.lib.config.normalize_connection_uri`).
    """
    entered_uri = prompts.ask(
        "Enter the MariaDB connection URI (e.g. user@host:3306): "
    )
    if entered_uri == "":
        return

    uri = config.normalize_connection_uri(entered_uri)
    if uri is None:
        print(f"'{entered_uri}' is not a valid connection URI.")
        print("The connection was not stored.")
        return

    if uri != entered_uri:
        print(f"The connection will be stored as '{uri}'.")

    password = prompts.password(f"Enter the password for '{uri}': ")

    # Verify the credentials by opening (and immediately closing) a session.
    try:
        connection_data = prompts.shell().parse_uri(uri)
        connection_data["password"] = password
        session = prompts.shell().open_session(connection_data)
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

    index = prompts.select_index("Enter the number of the connection to delete", len(connections))
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
    entered = prompts.ask(
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

    index = prompts.select_index("Enter the number of the path to delete", len(paths))
    if index < 0:
        return

    path = paths.pop(index)
    config.set_allowed_paths(paths)
    print(f"Allowed path '{path}' deleted.")


# --- Entry points ----------------------------------------------------------


def _first_run() -> None:
    """Guided first-run configuration: add connections, then allowed paths.

    The migration tooling is deliberately NOT part of this: it is a download the
    plugin does not need in order to serve anything, so it stays a step the user
    goes and asks for from the menu rather than one the first run walks into.
    """
    print("Let's configure the MariaDB connections the MCP server may use.")
    while prompts.yes_no("Add a connection?", default=True):
        _add_connection()

    print(
        "\nNow choose the local directories the MCP server is allowed to access."
    )
    while prompts.yes_no("Add an allowed path?", default=True):
        _add_path()

    # Ensure a settings file exists so subsequent runs use the management menu.
    config.set_allowed_paths(config.get_allowed_paths())


def _menu_entries() -> list:
    """Returns the management menu's (label, action) pairs, in order.

    The migration tooling is appended only where it runs (see
    :func:`mcp_plugin.lib.setup_migration.is_supported`), which is why the menu
    is built rather than written out: on Windows the entry is absent and every
    number after it - "Finish" included - shifts up by one on its own.

    Returns:
        The entries to offer, without the trailing "Finish".
    """
    entries = [
        ("Add a connection", _add_connection),
        ("Delete a connection", _delete_connection),
        ("Add an allowed path", _add_path),
        ("Delete an allowed path", _delete_path),
    ]
    if setup_migration.is_supported():
        entries.append((setup_migration.menu_label(), setup_migration.manage))

    return entries


def _menu() -> None:
    """Management menu: connections, allowed paths and the migration tooling."""
    while True:
        _print_connections()
        _print_paths()
        if setup_migration.is_supported():
            setup_migration.print_status()

        # Rebuilt every round: the migration entry's label follows what is
        # installed, which the previous round may have just changed.
        entries = _menu_entries()
        finish = len(entries) + 1

        print("\nWhat would you like to do?")
        for number, (label, _) in enumerate(entries, start=1):
            print(f"  {number}. {label}")
        print(f"  {finish}. Finish")

        choice = prompts.ask("Enter your choice: ")
        if choice == "" or choice == str(finish):
            break
        if not (choice.isdigit() and 1 <= int(choice) <= len(entries)):
            print(f"Please enter a number between 1 and {finish}.")
            continue

        entries[int(choice) - 1][1]()


def run_setup() -> None:
    """Runs the interactive MCP server setup.

    Returns:
        None
    """
    if not prompts.shell().options.useWizards:
        raise mysqlsh.Error(
            "mcp.setup must be run from an interactive shell session."
        )

    print("=== MariaDB MCP Server setup ===")
    print(f"Configuration is stored in: {general.get_plugin_data_path()}")

    if config.settings_file_exists() or config.list_connection_uris():
        _menu()
    else:
        _first_run()

    print("\nSetup complete.")
