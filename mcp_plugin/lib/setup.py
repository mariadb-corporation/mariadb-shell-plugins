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
lives in :mod:`mcp_plugin.lib.setup_migrator`: it is a menu-only step, never
part of the first run, and it is left out altogether on platforms the tooling
does not run on. The prompt primitives are in
:mod:`mcp_plugin.lib.setup_prompts`, shared with that module.

Everything the menu offers can also be given as an option instead, which turns
the whole thing declarative and terminal-free; that lives in
:mod:`mcp_plugin.lib.setup_cli`.

In multi-tenant mode (turned on with ``mcp setup --multiTenant=true``, see
:mod:`mcp_plugin.lib.tenants`) the menu is about users instead: adding and
removing them, their API keys, and - once one is picked - their connections and
allowed paths, which are theirs alone.
"""

# cSpell:ignore mysqlsh MariaDB

import os

import mysqlsh

from mcp_plugin.lib import config, general, setup_cli, setup_migrator, tenants
from mcp_plugin.lib import setup_prompts as prompts

# The management menu's last choice, and its default: picking it (or replying
# with nothing) leaves the menu.
MENU_FINISH_LABEL = "Finish"


# --- Connections -----------------------------------------------------------


def _print_connections(mcp_user_id=None) -> list:
    """Prints the configured connections and returns them."""
    connections = config.list_connection_uris(mcp_user_id=mcp_user_id)
    if connections:
        print("\nConfigured connections:")
        for index, uri in enumerate(connections, start=1):
            print(f"  {index}. {uri}")
    else:
        print("\nNo connections configured yet.")
    return connections


def _add_connection(mcp_user_id=None) -> None:
    """Prompts for a connection URI and password, verifies and stores it.

    What is stored is the normalized URI rather than what was typed: it is the
    key the connection is then looked up under, and one canonical spelling per
    connection is what keeps the same connection from being configured twice
    over (see :func:`mcp_plugin.lib.config.normalize_connection_uri`).

    Args:
        mcp_user_id: The user to store it for, in multi-tenant mode.
    """
    entered_uri = prompts.ask(
        "Enter the MariaDB connection URI (e.g. mariadb://user@host:3306): "
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

    # Asked before the password, which would be wasted on a connection the
    # secret store cannot hold.
    try:
        config.check_connection_key_length(uri)
    except mysqlsh.Error as error:
        print(error)
        print("The connection was not stored.")
        return

    password = prompts.password(f"Enter the password for '{uri}': ")

    # Verify the credentials by opening (and immediately closing) a session.
    try:
        setup_cli.verify_connection(uri, password)
    except Exception as error:  # noqa: BLE001 - surface any connection failure
        print(f"Could not connect to '{uri}': {error}")
        print("The connection was not stored.")
        return

    config.store_connection(uri, password, mcp_user_id=mcp_user_id)
    superseded = config.drop_superseded_spellings(uri, mcp_user_id=mcp_user_id)
    print(f"Connection '{uri}' verified and stored{setup_cli._for_user(mcp_user_id)}.")
    for old_uri in superseded:
        print(f"It replaces '{old_uri}', which named the same connection.")


def _delete_connection(mcp_user_id=None) -> None:
    """Prompts the user to delete one of the configured connections.

    The list is NOT printed first: the select prompt below renders its own
    numbered list, so printing one here would show it twice.
    """
    connections = config.list_connection_uris(mcp_user_id=mcp_user_id)
    if not connections:
        print("\nNo connections configured yet.")
        return

    index = prompts.select_or_cancel(
        "Select the connection to delete", connections
    )
    if index < 0:
        return

    # The list is the reported one, whose spellings are not always the stored
    # keys - a connection configured before the scheme was kept is reported
    # with it - so the pick is resolved back to the key it is stored under.
    uri = config.resolve_connection_uri(connections[index], mcp_user_id=mcp_user_id)
    if uri is None:
        print(f"'{connections[index]}' is no longer configured.")
        return

    config.delete_connection(uri, mcp_user_id=mcp_user_id)
    print(f"Connection '{uri}' deleted{setup_cli._for_user(mcp_user_id)}.")


# --- Allowed paths ---------------------------------------------------------


def _print_paths(mcp_user_id=None) -> list:
    """Prints the configured allowed paths and returns them."""
    paths = config.get_allowed_paths(mcp_user_id)
    if paths:
        print("\nAllowed paths:")
        for index, path in enumerate(paths, start=1):
            print(f"  {index}. {path}")
    else:
        print("\nNo allowed paths configured yet.")
    return paths


def _add_path(mcp_user_id=None) -> None:
    """Prompts for a directory to allow, defaulting to the current directory."""
    default_path = os.path.abspath(os.getcwd())
    entered = prompts.ask(
        f"Enter a directory the MCP server may access (default: {default_path}): "
    )
    path = os.path.abspath(os.path.expanduser(entered)) if entered else default_path

    if not os.path.isdir(path):
        print(f"'{path}' is not an existing directory. It was not added.")
        return

    paths = config.get_allowed_paths(mcp_user_id)
    if path in paths:
        print(f"'{path}' is already allowed.")
        return

    paths.append(path)
    config.set_allowed_paths(paths, mcp_user_id)
    print(f"Allowed path '{path}' added{setup_cli._for_user(mcp_user_id)}.")


def _delete_path(mcp_user_id=None) -> None:
    """Prompts the user to delete one of the allowed paths.

    The list is NOT printed first: the select prompt below renders its own
    numbered list, so printing one here would show it twice.
    """
    paths = config.get_allowed_paths(mcp_user_id)
    if not paths:
        print("\nNo allowed paths configured yet.")
        return

    index = prompts.select_or_cancel("Select the allowed path to delete", paths)
    if index < 0:
        return

    path = paths.pop(index)
    config.set_allowed_paths(paths, mcp_user_id)
    print(f"Allowed path '{path}' deleted{setup_cli._for_user(mcp_user_id)}.")


# --- Users (multi-tenant mode) ------------------------------------------------


def _select_user(message: str):
    """Prompts for one of the users; returns their id, or None if cancelled."""
    users = tenants.read_users()
    if not users:
        print("\nNo users yet.")
        return None

    user_ids = sorted(users)
    labels = [tenants.describe_user(user_id, users[user_id]) for user_id in user_ids]
    index = prompts.select_or_cancel(message, labels)

    return None if index < 0 else user_ids[index]


def _print_users() -> None:
    """Prints the users."""
    users = tenants.read_users()
    if not users:
        print("\nNo users yet.")
        return

    print("\nUsers:")
    for index, user_id in enumerate(sorted(users), start=1):
        record = users[user_id]
        state = " (disabled)" if record.get("disabled") else ""
        print(f"  {index}. {tenants.describe_user(user_id, record)}{state}")


def _add_user() -> None:
    """Prompts for a new user's identity and name, adds them, shows their key."""
    entered = prompts.ask(
        "Enter the user's email address, or another identity "
        "(userId:<id>, oauth:<issuer>|<subject>): "
    )
    if entered == "":
        return

    try:
        identity = tenants.parse_identity(entered)
        name = prompts.ask("Enter a name to show for the user (optional): ")
        mcp_user_id = tenants.add_user([identity], name=name or None)
    except mysqlsh.Error as error:
        print(error)
        print("The user was not added.")
        return

    key = tenants.issue_api_key(mcp_user_id)
    print(f"User {tenants.describe_user(mcp_user_id)} added.")
    print(f"API key: {key}")


def _remove_user() -> None:
    """Prompts for a user to remove, with everything they have."""
    mcp_user_id = _select_user("Select the user to remove")
    if mcp_user_id is None:
        return

    label = tenants.describe_user(mcp_user_id)
    if not prompts.yes_no(
        f"Remove {label} with their API key and connections?", default=False
    ):
        return

    tenants.remove_user(mcp_user_id)
    print(f"User {label} removed.")


def _show_api_key() -> None:
    """Prompts for a user and shows their API key."""
    mcp_user_id = _select_user("Select the user whose API key to show")
    if mcp_user_id is None:
        return

    key = tenants.get_api_key(mcp_user_id)
    print(f"API key of {tenants.describe_user(mcp_user_id)}: {key or '(none)'}")


def _rotate_api_key() -> None:
    """Prompts for a user and issues them a new API key."""
    mcp_user_id = _select_user("Select the user to issue a new API key to")
    if mcp_user_id is None:
        return

    key = tenants.issue_api_key(mcp_user_id)
    print(f"New API key for {tenants.describe_user(mcp_user_id)}: {key}")
    print("The previous key no longer works.")


def _toggle_user() -> None:
    """Prompts for a user and disables or enables them."""
    mcp_user_id = _select_user("Select the user to disable or enable")
    if mcp_user_id is None:
        return

    disabled = not (tenants.get_user(mcp_user_id) or {}).get("disabled", False)
    tenants.set_disabled(mcp_user_id, disabled)
    print(
        f"User {tenants.describe_user(mcp_user_id)} "
        f"{'disabled' if disabled else 'enabled'}."
    )


def _manage_user_resources() -> None:
    """Prompts for a user, then manages their connections and allowed paths."""
    mcp_user_id = _select_user("Select the user whose connections and paths to manage")
    if mcp_user_id is None:
        return

    entries = [
        ("Add a connection", _add_connection),
        ("Delete a connection", _delete_connection),
        ("Add an allowed path", _add_path),
        ("Delete an allowed path", _delete_path),
    ]
    while True:
        print(f"\n--- {tenants.describe_user(mcp_user_id)} ---")
        _print_connections(mcp_user_id)
        _print_paths(mcp_user_id)

        action = prompts.select_action("\nWhat would you like to do?", entries, "Back")
        if action is None:
            return

        action(mcp_user_id)


def _tenant_menu_entries() -> list:
    """Returns the multi-tenant management menu's (label, action) pairs."""
    return [
        ("Add a user", _add_user),
        ("Remove a user", _remove_user),
        ("Manage a user's connections and allowed paths", _manage_user_resources),
        ("Show a user's API key", _show_api_key),
        ("Issue a user a new API key", _rotate_api_key),
        ("Disable or enable a user", _toggle_user),
    ]


def _tenant_menu() -> None:
    """Management menu of a multi-tenant server: its users."""
    print("Multi-tenant mode is on (mcp setup --multiTenant=false turns it off).")
    while True:
        _print_users()

        action = prompts.select_action(
            "\nWhat would you like to do?", _tenant_menu_entries(), MENU_FINISH_LABEL
        )
        if action is None:
            break

        action()


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
    :func:`mcp_plugin.lib.setup_migrator.is_supported`), which is why the menu
    is built rather than written out: on Windows the entry is simply absent, and
    the shell's select prompt numbers whatever it is given.

    Returns:
        The entries to offer, without the trailing "Finish".
    """
    entries = [
        ("Add a connection", _add_connection),
        ("Delete a connection", _delete_connection),
        ("Add an allowed path", _add_path),
        ("Delete an allowed path", _delete_path),
    ]
    if setup_migrator.is_supported():
        entries.append((setup_migrator.menu_label(), setup_migrator.manage))

    return entries


def _menu() -> None:
    """Management menu: connections, allowed paths and the migration tooling.

    A select prompt, so the shell numbers the choices, refuses anything that is
    not one of them and re-asks by itself. "Finish" is the last choice and the
    default, which is what makes an empty reply end the menu.
    """
    while True:
        _print_connections()
        _print_paths()
        if setup_migrator.is_supported():
            setup_migrator.print_status()

        # Rebuilt every round: the migration entry's label follows what is
        # installed, which the previous round may have just changed.
        action = prompts.select_action(
            "\nWhat would you like to do?", _menu_entries(), MENU_FINISH_LABEL
        )
        if action is None:
            break

        action()


def run_setup(**options) -> None:
    """Runs the MCP server setup, interactively or from the given options.

    With no options this is the walkthrough it has always been. With any option
    it is declarative instead and asks nothing it was not obliged to
    (:func:`mcp_plugin.lib.setup_cli.apply`), which is what lets it run where
    there is no terminal - the interactive-session requirement below applies
    only to the walkthrough, since that is the only part that cannot proceed
    without one.

    Args:
        **options (dict): See ``mcp.setup``.

    Returns:
        None
    """
    if setup_cli.has_options(options):
        setup_cli.apply(options)
        return

    if not prompts.shell().options.useWizards:
        raise mysqlsh.Error(
            "mcp.setup must be run from an interactive shell session, or with "
            "options - run 'mariadb-shell -- mcp setup --help' for those."
        )

    print("=== MariaDB MCP Server setup ===")
    print(f"Configuration is stored in: {general.get_plugin_data_path()}")

    if tenants.is_multi_tenant():
        _tenant_menu()
    elif config.settings_file_exists() or config.list_connection_uris():
        _menu()
    else:
        _first_run()

    print("\nSetup complete.")
