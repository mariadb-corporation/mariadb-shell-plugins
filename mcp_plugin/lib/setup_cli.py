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

"""Non-interactive mcp.setup: every menu item as a command-line option.

``mariadb-shell -- mcp setup`` with no options is the interactive walkthrough
(:mod:`mcp_plugin.lib.setup`). Given any option it becomes declarative instead,
which is what lets a provisioning script or a CI job configure the server
without a terminal - see :func:`apply`.

It also holds :func:`verify_connection`, which both paths use: the interactive
menu and the command line have to accept a connection on exactly the same
terms, and it goes through :mod:`mcp_plugin.lib.setup_prompts`'s shell handle
like the rest of the setup does.

Passwords are the one thing not simply passed as a value. Four sources are
accepted - stdin, a named environment variable, a prompt, and the option
itself - because the safe one differs by caller: a pipe from a secret manager,
a CI runner's injected variable, a person at a terminal, or a script whose
author accepts that a command line is visible in ``ps``. Exactly one may be
given at a time, so which was meant is never guessed at.
"""

# cSpell:ignore mysqlsh MariaDB

import json as json_module
import os
import sys

import mysqlsh

from mcp_plugin.lib import config, general, setup_migrator
from mcp_plugin.lib import setup_prompts as prompts

# Options that change something. Their presence is what switches mcp.setup from
# the interactive walkthrough to the declarative one.
ACTION_OPTIONS = (
    "add_connection",
    "delete_connections",
    "add_paths",
    "delete_paths",
    "install_migrator",
    "remove_migrator",
)

# Where the password for --add-connection may come from. Exactly one.
PASSWORD_OPTIONS = ("password", "password_env", "password_stdin")

# Options that only qualify what the ones above do.
MODIFIER_OPTIONS = ("no_verify", "non_interactive", "show", "json")

KNOWN_OPTIONS = ACTION_OPTIONS + PASSWORD_OPTIONS + MODIFIER_OPTIONS


def _cli_name(option: str) -> str:
    """Returns the option as the command line documents it.

    The shell accepts ``--add_paths``, ``--add-paths`` and ``--addPaths`` alike,
    but its generated help lists only the last, so that is the spelling a
    message about an option has to use - naming it any other way sends the
    reader looking for something the help does not mention.

    Args:
        option (str): The keyword name, in snake_case.

    Returns:
        The option as it appears in ``mcp setup --help``.
    """
    head, *rest = option.split("_")

    return "--" + head + "".join(word.capitalize() for word in rest)


def verify_connection(uri: str, password: str) -> None:
    """Checks that the given credentials open a session, and closes it again.

    Args:
        uri (str): The connection URI to open.
        password (str): The password to open it with.

    Returns:
        None

    Raises:
        Exception: Whatever the shell raises when the session cannot be opened.
            Its text is the useful part of the answer, so it is not replaced.
    """
    connection_data = dict(prompts.shell().parse_uri(uri))
    connection_data["password"] = password
    session = prompts.shell().open_session(connection_data)
    session.close()


def has_options(options: dict) -> bool:
    """Returns whether mcp.setup was given anything to do without asking.

    Args:
        options (dict): The options mcp.setup was called with.

    Returns:
        True when ANY option was given, recognized or not. A misspelled option
        has to reach :func:`_reject_unknown` and be refused there; treating it
        as "no options" would start the walkthrough instead, which in a script
        with no terminal is a confusing way to be told about a typo.
    """
    return bool(options)


def _as_list(value) -> list:
    """Returns an option's value as a list of non-empty strings.

    Accepted as a real list or, as the command line passes one, a
    comma-separated string. Repeating an option is deliberately NOT a supported
    way of building a list: comma-separated is the one form the plugin's other
    list options already take.

    Args:
        value: The option's value.

    Returns:
        The values, stripped, without the empty ones.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]

    return [str(item).strip() for item in value if str(item).strip()]


def _reject_unknown(options: dict) -> None:
    """Refuses an option that is not one of ours.

    A misspelled option in a provisioning script would otherwise be ignored in
    silence, and the run would report success having done less than it was
    asked to.

    Args:
        options (dict): The options mcp.setup was called with.

    Returns:
        None

    Raises:
        mysqlsh.Error: If any option is not recognized.
    """
    unknown = sorted(name for name in options if name not in KNOWN_OPTIONS)
    if unknown:
        raise mysqlsh.Error(
            f"Unknown option(s): {', '.join(unknown)}. Supported options are: "
            f"{', '.join(sorted(_cli_name(name) for name in KNOWN_OPTIONS))}."
        )


def _check_combination(options: dict) -> None:
    """Refuses combinations of options that contradict or do nothing.

    Args:
        options (dict): The options mcp.setup was called with.

    Returns:
        None

    Raises:
        mysqlsh.Error: If the options cannot all be honoured as given.
    """
    actions = [name for name in ACTION_OPTIONS if options.get(name)]

    if options.get("show"):
        if actions:
            raise mysqlsh.Error(
                "--show only reports the configuration, so it cannot be "
                f"combined with {', '.join(_cli_name(n) for n in actions)}."
            )
    elif options.get("json"):
        # Nothing else produces machine-readable output, so a caller passing
        # this with an action is expecting something they would not get.
        raise mysqlsh.Error("--json only applies to --show.")

    given_passwords = [name for name in PASSWORD_OPTIONS if name in options]
    if len(given_passwords) > 1:
        raise mysqlsh.Error(
            "Give exactly one of "
            f"{', '.join(_cli_name(n) for n in given_passwords)}: which "
            "password was meant is not something to guess at."
        )

    if not options.get("add_connection"):
        stray = given_passwords + (["no_verify"] if options.get("no_verify") else [])
        if stray:
            raise mysqlsh.Error(
                f"{', '.join(_cli_name(n) for n in stray)} only applies to "
                f"{_cli_name('add_connection')}."
            )

    if not actions and not options.get("show"):
        # --non-interactive on its own would otherwise fall through to the
        # walkthrough it exists to avoid.
        raise mysqlsh.Error(
            "Nothing to do. Give one of "
            f"{', '.join(_cli_name(n) for n in ACTION_OPTIONS)}, or --show, or "
            "no options at all for the interactive setup."
        )


def _password_from_stdin() -> str:
    """Returns the password read from stdin.

    Returns:
        The first line of stdin, without its line ending.

    Raises:
        mysqlsh.Error: If stdin is a terminal, where this would wait for input
            that nobody knows to type.
    """
    if sys.stdin.isatty():
        raise mysqlsh.Error(
            "--passwordStdin expects the password to be piped in, but stdin "
            "is a terminal. Pipe it, or leave the option out to be prompted."
        )

    # Only the first line: a password does not contain one, and a file with a
    # trailing newline is the normal case.
    return sys.stdin.readline().rstrip("\r\n")


def _password_from_env(name: str) -> str:
    """Returns the password held by the named environment variable.

    The variable's NAME is what the option takes, so the password itself never
    appears in the command line.

    Args:
        name (str): The environment variable to read.

    Returns:
        Its value, which may be empty - an empty password is a password.

    Raises:
        mysqlsh.Error: If the variable is not set at all.
    """
    if name not in os.environ:
        raise mysqlsh.Error(
            f"--passwordEnv names '{name}', which is not set in the "
            "environment."
        )

    return os.environ[name]


def _resolve_password(uri: str, options: dict) -> str:
    """Returns the password for a connection, from whichever source was given.

    Args:
        uri (str): The connection the password is for, for the prompt.
        options (dict): The options mcp.setup was called with.

    Returns:
        The password.

    Raises:
        mysqlsh.Error: If no source was given and none can be asked for.
    """
    if options.get("password_stdin"):
        return _password_from_stdin()
    if "password_env" in options:
        return _password_from_env(str(options["password_env"]))
    if "password" in options:
        return str(options["password"])

    if options.get("non_interactive"):
        raise mysqlsh.Error(
            "No password given and --nonInteractive forbids asking for one. "
            "Use --passwordStdin, --passwordEnv or --password."
        )
    if not prompts.shell().options.useWizards:
        raise mysqlsh.Error(
            "No password given and this session cannot prompt for one. Use "
            "--passwordStdin, --passwordEnv or --password."
        )

    return prompts.password(f"Enter the password for '{uri}': ")


def _add_connection(options: dict) -> None:
    """Verifies and stores the connection named by --add-connection."""
    entered_uri = str(options["add_connection"]).strip()

    parsed = config.parse_connection_uri(entered_uri)
    if parsed is None:
        raise mysqlsh.Error(f"'{entered_uri}' is not a valid connection URI.")
    if parsed.get("password"):
        # Refused rather than used or quietly dropped: normalization strips it,
        # so using it would mean storing a connection with no password at all,
        # and a password in a URI is as visible in `ps` as one in --password
        # without saying so.
        raise mysqlsh.Error(
            "The connection URI carries a password. Give the URI without it "
            "and pass the password with --passwordStdin, --passwordEnv or "
            "--password."
        )

    uri = config.normalize_connection_uri(entered_uri)
    if uri is None:
        raise mysqlsh.Error(f"'{entered_uri}' is not a valid connection URI.")

    password = _resolve_password(uri, options)

    if options.get("no_verify"):
        print(f"Storing '{uri}' without verifying it (--noVerify).")
    else:
        try:
            verify_connection(uri, password)
        except Exception as error:  # noqa: BLE001 - surface the shell's text
            raise mysqlsh.Error(
                f"Could not connect to '{uri}': {error}. The connection was "
                "not stored. Pass --noVerify to store it anyway."
            ) from error

    # Re-configuring a connection updates its password rather than failing: a
    # provisioning script has to be safe to run twice.
    replaced = uri in config.list_connection_uris()
    config.store_connection(uri, password)
    print(
        f"Connection '{uri}' {'updated' if replaced else 'stored'}"
        f"{'' if options.get('no_verify') else ' after verification'}."
    )


def _delete_connections(options: dict) -> None:
    """Deletes the connections named by --delete-connections."""
    for entered_uri in _as_list(options["delete_connections"]):
        uri = config.resolve_connection_uri(entered_uri)
        if uri is None:
            configured = config.list_connection_uris()
            raise mysqlsh.Error(
                f"'{entered_uri}' is not a configured connection. Configured "
                f"connections: {', '.join(configured) or 'none'}."
            )

        config.delete_connection(uri)
        print(f"Connection '{uri}' deleted.")


def _add_paths(options: dict) -> None:
    """Adds the directories named by --add-paths."""
    for entered in _as_list(options["add_paths"]):
        path = os.path.abspath(os.path.expanduser(entered))
        if not os.path.isdir(path):
            raise mysqlsh.Error(f"'{path}' is not an existing directory.")

        if path in config.get_allowed_paths():
            print(f"Allowed path '{path}' was already allowed.")
            continue

        config.add_allowed_path(path)
        print(f"Allowed path '{path}' added.")


def _delete_paths(options: dict) -> None:
    """Removes the directories named by --delete-paths."""
    for entered in _as_list(options["delete_paths"]):
        path = os.path.abspath(os.path.expanduser(entered))
        paths = config.get_allowed_paths()
        if path not in paths:
            raise mysqlsh.Error(
                f"'{path}' is not an allowed path. Allowed paths: "
                f"{', '.join(paths) or 'none'}."
            )

        paths.remove(path)
        config.set_allowed_paths(paths)
        print(f"Allowed path '{path}' deleted.")


def _remove_migrator() -> None:
    """Removes every installed release of the migration tooling."""
    removed_dir = setup_migrator.remove()
    print(f"Migration tooling removed from '{removed_dir}'.")


def _install_migrator() -> None:
    """Downloads, provisions and wraps the configured migration tooling release."""
    if not setup_migrator.is_supported():
        raise mysqlsh.Error(
            "The migration tooling is a POSIX shell program and does not run "
            "on this platform, so there is nothing to install."
        )

    print(f"Downloading {setup_migrator.archive_url()} ...")
    target_dir = setup_migrator.download()
    print(f"Migration tooling {general.MIGRATOR_VERSION} installed in '{target_dir}'.")

    print("Creating the virtual environment and installing dependencies ...")
    venv_dir = setup_migrator.provision(target_dir)
    print(f"Virtual environment ready in '{venv_dir}'.")

    wrapper = setup_migrator.install_wrapper(target_dir)
    print(f"'{general.MIGRATOR_DIR_NAME}' wrapper installed as '{wrapper}'.")


def configuration() -> dict:
    """Returns the whole configuration --show reports.

    Returns:
        The configured connections, the allowed paths and the state of the
        migration tooling.
    """
    return {
        "config_path": general.get_plugin_data_path(),
        "connections": config.list_connection_uris(),
        "allowed_paths": config.get_allowed_paths(),
        "migrator": {
            "supported": setup_migrator.is_supported(),
            "configured_release": general.MIGRATOR_VERSION,
            "installed_releases": setup_migrator.installed_versions(),
            "install_path": general.get_migrator_path(),
            "wrapper_path": setup_migrator.wrapper_path(),
        },
    }


def _show(options: dict) -> None:
    """Prints the configuration, as JSON when --json was given."""
    current = configuration()

    if options.get("json"):
        # Nothing else on stdout, so the whole of it parses as one document.
        print(json_module.dumps(current, indent=2, sort_keys=False))
        return

    print("=== MariaDB MCP Server configuration ===")
    print(f"Configuration is stored in: {current['config_path']}")

    print("\nConfigured connections:")
    for index, uri in enumerate(current["connections"], start=1):
        print(f"  {index}. {uri}")
    if not current["connections"]:
        print("  (none)")

    print("\nAllowed paths:")
    for index, path in enumerate(current["allowed_paths"], start=1):
        print(f"  {index}. {path}")
    if not current["allowed_paths"]:
        print("  (none)")

    tooling = current["migrator"]
    print("\nMigration tooling:")
    if not tooling["supported"]:
        print("  not supported on this platform")
        return

    print(f"  Configured release: {tooling['configured_release']}")
    print(f"  Installed releases: {', '.join(tooling['installed_releases']) or 'none'}")
    print(f"  Install path:       {tooling['install_path']}")
    print(f"  Wrapper:            {tooling['wrapper_path']}")


def apply(options: dict) -> None:
    """Carries out everything the given options ask for, or fails saying why.

    The order is fixed rather than the order the options happen to be in:
    deletions first, so that deleting and re-adding the same connection in one
    call ends up with it added, and the migration tooling last, since it is the
    step that reaches the network. Passing both --remove-migrator and
    --install-migrator is therefore how a release is reinstalled.

    Anything that fails stops the run, leaving what already succeeded in place
    and reported - a provisioning script has to be able to tell how far it got.

    Args:
        options (dict): The options mcp.setup was called with.

    Returns:
        None

    Raises:
        mysqlsh.Error: If the options are unusable, or a step fails.
    """
    _reject_unknown(options)
    _check_combination(options)

    if options.get("show"):
        _show(options)
        return

    if options.get("delete_connections"):
        _delete_connections(options)
    if options.get("delete_paths"):
        _delete_paths(options)
    if options.get("add_connection"):
        _add_connection(options)
    if options.get("add_paths"):
        _add_paths(options)
    if options.get("remove_migrator"):
        _remove_migrator()
    if options.get("install_migrator"):
        _install_migrator()

    # A settings file has to exist for the next run to reach the management
    # menu rather than the first-run walkthrough, exactly as _first_run ensures.
    config.set_allowed_paths(config.get_allowed_paths())
