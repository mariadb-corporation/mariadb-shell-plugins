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

Multi-tenant mode (``--multiTenant=true``, see :mod:`mcp_plugin.lib.tenants`)
adds the users: ``--addUser``, ``--removeUser`` and the options that change one,
each naming the user by UUID or by any of their identities. In that mode a
connection or an allowed path always belongs to a user, so ``--user`` is
required with the options that add or delete them - and refused outside it,
where it would silently mean nothing.
"""

# cSpell:ignore mysqlsh MariaDB

import json as json_module
import os
import sys

import mysqlsh

from mcp_plugin.lib import config, general, setup_migrator, tenants
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
    "multi_tenant",
    "add_user",
    "remove_user",
    "add_identity",
    "remove_identity",
    "disable_user",
    "enable_user",
    "set_scopes",
    "rotate_api_key",
    "show_api_key",
    "purge_orphan_groups",
    "set_default_role",
)

# Where the password for --add-connection may come from. Exactly one.
PASSWORD_OPTIONS = ("password", "password_env", "password_stdin")

# Options that only qualify what the ones above do.
MODIFIER_OPTIONS = (
    "no_verify",
    "non_interactive",
    "show",
    "json",
    "user",
    "all_users",
    "name",
    "scopes",
)

KNOWN_OPTIONS = ACTION_OPTIONS + PASSWORD_OPTIONS + MODIFIER_OPTIONS

# The actions that work on what belongs to one user in multi-tenant mode, and
# so need --user there.
PER_USER_OPTIONS = ("add_connection", "delete_connections", "add_paths", "delete_paths")

# The actions that change one user, named by --user.
USER_TARGETED_OPTIONS = (
    "add_identity",
    "remove_identity",
    "set_scopes",
    "set_default_role",
)

# The actions whose result --json can report: each hands out an API key.
JSON_ACTION_OPTIONS = ("add_user", "rotate_api_key", "show_api_key")

# The actions that are actions whenever they are given, false or empty too.
PRESENCE_OPTIONS = ("multi_tenant", "set_default_role")


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


def _as_bool(value) -> bool:
    """Returns an option's value as a boolean.

    The command line passes ``--multiTenant=false`` as the string, which is
    truthy, so the words are read for what they say.

    Raises:
        mysqlsh.Error: If it is neither a boolean nor a word for one.
    """
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False

    raise mysqlsh.Error(f"'{value}' is not true or false.")


def _actions(options: dict) -> list:
    """Returns the action options that were given.

    ``--multiTenant=false`` is an action even though its value is false, and
    so are the others in :data:`PRESENCE_OPTIONS`.
    """
    return [
        name
        for name in ACTION_OPTIONS
        if options.get(name) or (name in PRESENCE_OPTIONS and name in options)
    ]


def _multi_tenant_after(options: dict) -> bool:
    """Returns whether the server is multi-tenant once these options applied."""
    if "multi_tenant" in options:
        return _as_bool(options["multi_tenant"])

    return tenants.is_multi_tenant()


def _check_combination(options: dict) -> None:
    """Refuses combinations of options that contradict or do nothing.

    Args:
        options (dict): The options mcp.setup was called with.

    Returns:
        None

    Raises:
        mysqlsh.Error: If the options cannot all be honoured as given.
    """
    actions = _actions(options)

    if options.get("show"):
        if actions:
            raise mysqlsh.Error(
                "--show only reports the configuration, so it cannot be "
                f"combined with {', '.join(_cli_name(n) for n in actions)}."
            )
    elif options.get("json"):
        # Nothing else produces machine-readable output, so a caller passing
        # this with any other action is expecting something they would not get.
        if not actions or any(name not in JSON_ACTION_OPTIONS for name in actions):
            raise mysqlsh.Error(
                "--json only applies to --show, and to "
                f"{', '.join(_cli_name(n) for n in JSON_ACTION_OPTIONS)} on "
                "their own."
            )

    if options.get("all_users") and not options.get("show"):
        raise mysqlsh.Error("--allUsers only applies to --show.")

    if "multi_tenant" in options:
        _as_bool(options["multi_tenant"])

    for name in ("name", "scopes"):
        if options.get(name) and not (
            options.get("add_user") or (name == "scopes" and options.get("set_scopes"))
        ):
            raise mysqlsh.Error(
                f"{_cli_name(name)} only applies to {_cli_name('add_user')}."
            )

    targeted = [
        name
        for name in USER_TARGETED_OPTIONS
        if options.get(name) or (name in PRESENCE_OPTIONS and name in options)
    ]
    if targeted and not options.get("user"):
        raise mysqlsh.Error(
            f"{', '.join(_cli_name(n) for n in targeted)} needs "
            f"{_cli_name('user')} to say which user to change."
        )

    per_user = [name for name in PER_USER_OPTIONS if options.get(name)]
    if per_user and _multi_tenant_after(options) and not options.get("user"):
        raise mysqlsh.Error(
            "In multi-tenant mode connections and allowed paths belong to a "
            f"user: give {_cli_name('user')} with "
            f"{', '.join(_cli_name(n) for n in per_user)}."
        )
    if per_user and options.get("user") and not _multi_tenant_after(options):
        raise mysqlsh.Error(
            f"{_cli_name('user')} only applies to "
            f"{', '.join(_cli_name(n) for n in per_user)} in multi-tenant mode, "
            "which is off. Turn it on with --multiTenant=true."
        )
    if (
        options.get("user")
        and not per_user
        and not targeted
        and not options.get("show")
    ):
        raise mysqlsh.Error(
            f"{_cli_name('user')} names the user for "
            f"{', '.join(_cli_name(n) for n in PER_USER_OPTIONS + USER_TARGETED_OPTIONS)}"
            ", or for --show."
        )

    migrator = [
        name for name in ("install_migrator", "remove_migrator") if options.get(name)
    ]
    if migrator and _multi_tenant_after(options):
        raise mysqlsh.Error(
            "The migration tooling is not available in multi-tenant mode, which "
            "does not serve the migrator tools."
        )

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


def _add_connection(options: dict, mcp_user_id=None) -> None:
    """Verifies and stores the connection named by --add-connection.

    Args:
        options (dict): The options mcp.setup was called with.
        mcp_user_id: The user to store it for, in multi-tenant mode.
    """
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

    # Before the password is asked for or the connection verified, both wasted
    # on one the secret store cannot hold.
    config.check_connection_key_length(uri)

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
    # provisioning script has to be safe to run twice. Asked before storing,
    # since storing is what makes it true.
    replaced = uri in config.list_stored_connection_uris(mcp_user_id=mcp_user_id)
    config.store_connection(uri, password, mcp_user_id=mcp_user_id)
    superseded = config.drop_superseded_spellings(uri, mcp_user_id=mcp_user_id)
    print(
        f"Connection '{uri}' "
        f"{'updated' if replaced or superseded else 'stored'}"
        f"{'' if options.get('no_verify') else ' after verification'}"
        f"{_for_user(mcp_user_id)}."
    )
    for old_uri in superseded:
        print(f"It replaces '{old_uri}', which named the same connection.")


def _for_user(mcp_user_id) -> str:
    """Returns the words that say whose a connection or path is, if anyone's."""
    if mcp_user_id is None:
        return ""

    return f" for {tenants.describe_user(mcp_user_id)}"


def _delete_connections(options: dict, mcp_user_id=None) -> None:
    """Deletes the connections named by --delete-connections."""
    for entered_uri in _as_list(options["delete_connections"]):
        uri = config.resolve_connection_uri(entered_uri, mcp_user_id=mcp_user_id)
        if uri is None:
            configured = config.list_connection_uris(mcp_user_id=mcp_user_id)
            raise mysqlsh.Error(
                f"'{entered_uri}' is not a configured connection"
                f"{_for_user(mcp_user_id)}. Configured connections: "
                f"{', '.join(configured) or 'none'}."
            )

        config.delete_connection(uri, mcp_user_id=mcp_user_id)
        print(f"Connection '{uri}' deleted{_for_user(mcp_user_id)}.")


def _allowed_paths(mcp_user_id) -> list:
    """Returns the allowed paths: one user's, or the server-wide list."""
    if mcp_user_id is None:
        return config.get_allowed_paths()

    return tenants.get_allowed_paths(mcp_user_id)


def _set_allowed_paths(mcp_user_id, paths: list) -> None:
    """Persists the allowed paths: one user's, or the server-wide list."""
    if mcp_user_id is None:
        config.set_allowed_paths(paths)
    else:
        tenants.set_allowed_paths(mcp_user_id, paths)


def _add_paths(options: dict, mcp_user_id=None) -> None:
    """Adds the directories named by --add-paths."""
    for entered in _as_list(options["add_paths"]):
        path = os.path.abspath(os.path.expanduser(entered))
        if not os.path.isdir(path):
            raise mysqlsh.Error(f"'{path}' is not an existing directory.")

        paths = _allowed_paths(mcp_user_id)
        if path in paths:
            print(f"Allowed path '{path}' was already allowed{_for_user(mcp_user_id)}.")
            continue

        _set_allowed_paths(mcp_user_id, paths + [path])
        print(f"Allowed path '{path}' added{_for_user(mcp_user_id)}.")


def _delete_paths(options: dict, mcp_user_id=None) -> None:
    """Removes the directories named by --delete-paths."""
    for entered in _as_list(options["delete_paths"]):
        path = os.path.abspath(os.path.expanduser(entered))
        paths = _allowed_paths(mcp_user_id)
        if path not in paths:
            raise mysqlsh.Error(
                f"'{path}' is not an allowed path{_for_user(mcp_user_id)}. "
                f"Allowed paths: {', '.join(paths) or 'none'}."
            )

        paths.remove(path)
        _set_allowed_paths(mcp_user_id, paths)
        print(f"Allowed path '{path}' deleted{_for_user(mcp_user_id)}.")


# --- Users (multi-tenant mode) --------------------------------------------------


def _set_multi_tenant(options: dict) -> None:
    """Turns multi-tenant mode on or off, as --multiTenant says."""
    enabled = _as_bool(options["multi_tenant"])
    tenants.set_multi_tenant(enabled)
    print(f"Multi-tenant mode {'on' if enabled else 'off'}.")


def _add_user(options: dict, report: dict) -> None:
    """Adds the user named by --add-user and issues their API key."""
    identities = [tenants.parse_identity(text) for text in _as_list(options["add_user"])]
    mcp_user_id = tenants.add_user(
        identities, name=options.get("name"), scopes=options.get("scopes")
    )
    key = tenants.issue_api_key(mcp_user_id)

    report.setdefault("users", []).append({"id": mcp_user_id, "apiKey": key})
    if not report.get("json"):
        print(f"User {tenants.describe_user(mcp_user_id)} added.")
        print(f"API key: {key}")
        print("Hand it to the user; mcp setup --showApiKey shows it again.")


def _remove_users(options: dict) -> None:
    """Removes the users named by --remove-user, with all their secrets."""
    for identifier in _as_list(options["remove_user"]):
        mcp_user_id = tenants.resolve_user(identifier)
        label = tenants.describe_user(mcp_user_id)
        tenants.remove_user(mcp_user_id)
        print(f"User {label} removed, with their connections and API key.")


def _change_identities(options: dict, mcp_user_id) -> None:
    """Adds and removes the identities --add-identity and --remove-identity name."""
    for text in _as_list(options.get("add_identity")):
        identity = tenants.parse_identity(text)
        tenants.add_identity(mcp_user_id, identity)
        print(
            f"Identity '{tenants.describe_identity(identity)}' added to "
            f"{tenants.describe_user(mcp_user_id)}."
        )

    for text in _as_list(options.get("remove_identity")):
        identity = tenants.parse_identity(text)
        tenants.remove_identity(mcp_user_id, identity)
        print(
            f"Identity '{tenants.describe_identity(identity)}' removed from "
            f"{tenants.describe_user(mcp_user_id)}."
        )


def _set_disabled(options: dict) -> None:
    """Disables and enables the users --disable-user and --enable-user name."""
    for name, disabled in (("disable_user", True), ("enable_user", False)):
        for identifier in _as_list(options.get(name)):
            mcp_user_id = tenants.resolve_user(identifier)
            tenants.set_disabled(mcp_user_id, disabled)
            print(
                f"User {tenants.describe_user(mcp_user_id)} "
                f"{'disabled' if disabled else 'enabled'}."
            )


def _set_scopes(options: dict, mcp_user_id) -> None:
    """Sets the scopes of the user --user names, as --set-scopes lists them."""
    tenants.set_scopes(mcp_user_id, options["set_scopes"])
    print(
        f"Scopes of {tenants.describe_user(mcp_user_id)}: "
        f"{', '.join(tenants.get_scopes(mcp_user_id)) or 'none'}."
    )


def _rotate_api_keys(options: dict, report: dict) -> None:
    """Issues new API keys to the users --rotate-api-key names."""
    for identifier in _as_list(options["rotate_api_key"]):
        mcp_user_id = tenants.resolve_user(identifier)
        key = tenants.issue_api_key(mcp_user_id)
        report.setdefault("users", []).append({"id": mcp_user_id, "apiKey": key})
        if not report.get("json"):
            print(
                f"New API key for {tenants.describe_user(mcp_user_id)}: {key}\n"
                "The previous key no longer works."
            )


def _show_api_keys(options: dict, report: dict) -> None:
    """Prints the API keys of the users --show-api-key names."""
    for identifier in _as_list(options["show_api_key"]):
        mcp_user_id = tenants.resolve_user(identifier)
        key = tenants.get_api_key(mcp_user_id)
        report.setdefault("users", []).append({"id": mcp_user_id, "apiKey": key})
        if not report.get("json"):
            print(
                f"API key of {tenants.describe_user(mcp_user_id)}: "
                f"{key or '(none - issue one with --rotateApiKey)'}"
            )


def _set_default_role(options: dict, mcp_user_id) -> None:
    """Sets the role the sessions of the user --user names run under."""
    tenants.set_default_role(mcp_user_id, options["set_default_role"])
    role = tenants.get_default_role(mcp_user_id)
    print(
        f"Sessions of {tenants.describe_user(mcp_user_id)} run under "
        f"{f'the role {role}' if role else 'the account default role'}."
    )


def _purge_orphan_groups() -> None:
    """Deletes the secret groups that belong to no user."""
    groups = tenants.orphan_groups()
    for group in groups:
        tenants.purge_group(group)
        print(f"Secret group {group}, which belonged to no user, deleted.")
    if not groups:
        print("There are no secret groups that belong to no user.")


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


def _user_summary(mcp_user_id, record, keys) -> dict:
    """Returns one user as --show reports them.

    Args:
        mcp_user_id (str): The user.
        record (dict): Their record from users.json.
        keys (list): The secret keys in their group.

    Returns:
        A dict with everything about the user but their secrets.
    """
    prefix = config.connection_secret_prefix()

    return {
        "id": mcp_user_id,
        "name": record.get("name", ""),
        "identities": [
            tenants.describe_identity(identity)
            for identity in record.get("identities", [])
        ],
        "scopes": tenants.get_scopes(mcp_user_id),
        "disabled": bool(record.get("disabled", False)),
        "created": record.get("created", ""),
        "has_api_key": tenants.API_KEY_SECRET in keys,
        "connections": sorted(
            config.with_default_scheme(key[len(prefix):])
            for key in keys
            if key.startswith(prefix)
        ),
        "allowed_paths": list(record.get("allowedPaths", [])),
    }


def configuration(all_users: bool = False, mcp_user_id=None) -> dict:
    """Returns the whole configuration --show reports.

    Args:
        all_users (bool): Whether to report every user of a multi-tenant
            server, with what belongs to them, and the secret groups that
            belong to nobody.
        mcp_user_id: One user to report, for --show --user.

    Returns:
        The configured connections, the allowed paths and the state of the
        migration tooling, and the users where asked for.
    """
    current = {
        "config_path": general.get_plugin_data_path(),
        "multi_tenant": tenants.is_multi_tenant(),
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
    from mcp_plugin.lib import oauth_config

    current["public_url"] = oauth_config.get_public_url()
    current["oauth_mode"] = oauth_config.get_mode()

    if all_users or mcp_user_id is not None:
        # One listing of every group, rather than one per user: each costs a
        # round trip to the secret store.
        groups = tenants.list_groups()
        users = tenants.read_users()
        wanted = users if all_users else {mcp_user_id: users[mcp_user_id]}
        current["users"] = [
            _user_summary(user_id, record, groups.get(user_id, []))
            for user_id, record in sorted(wanted.items())
        ]
        if all_users:
            current["orphan_groups"] = [
                group for group in groups if group not in users
            ]

    return current


def _show(options: dict) -> None:
    """Prints the configuration, as JSON when --json was given."""
    current = configuration(
        all_users=bool(options.get("all_users")),
        mcp_user_id=(
            tenants.resolve_user(options["user"]) if options.get("user") else None
        ),
    )

    if options.get("json"):
        # Nothing else on stdout, so the whole of it parses as one document.
        print(json_module.dumps(current, indent=2, sort_keys=False))
        return

    print("=== MariaDB MCP Server configuration ===")
    print(f"Configuration is stored in: {current['config_path']}")
    print(f"Multi-tenant mode: {'on' if current['multi_tenant'] else 'off'}")
    if current["multi_tenant"]:
        print(f"Public URL:        {current['public_url'] or '(none)'}")
        print(f"OAuth mode:        {current['oauth_mode']} (mcp setup-oauth --show)")

    if "users" in current:
        _print_users(current)
        return

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


def _print_users(current: dict) -> None:
    """Prints the users part of --show."""
    print("\nUsers:")
    for user in current["users"]:
        state = " (disabled)" if user["disabled"] else ""
        print(f"  {user['name'] or user['id']}{state}")
        print(f"    id:            {user['id']}")
        print(f"    identities:    {', '.join(user['identities']) or 'none'}")
        print(f"    scopes:        {', '.join(user['scopes']) or 'none'}")
        print(f"    API key:       {'yes' if user['has_api_key'] else 'none'}")
        print(f"    connections:   {', '.join(user['connections']) or 'none'}")
        print(f"    allowed paths: {', '.join(user['allowed_paths']) or 'none'}")
    if not current["users"]:
        print("  (none)")

    if current.get("orphan_groups"):
        print("\nSecret groups that belong to no user (--purgeOrphanGroups):")
        for group in current["orphan_groups"]:
            print(f"  {group}")


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

    report = {"json": bool(options.get("json"))}

    if "multi_tenant" in options:
        _set_multi_tenant(options)
    if options.get("remove_user"):
        _remove_users(options)
    if options.get("add_user"):
        _add_user(options, report)

    # Resolved after the users were added, so a call can add a user and give
    # them a connection in one go, naming them by the identity just added.
    mcp_user_id = tenants.resolve_user(options["user"]) if options.get("user") else None

    if options.get("add_identity") or options.get("remove_identity"):
        _change_identities(options, mcp_user_id)
    if options.get("disable_user") or options.get("enable_user"):
        _set_disabled(options)
    if options.get("set_scopes"):
        _set_scopes(options, mcp_user_id)
    if options.get("rotate_api_key"):
        _rotate_api_keys(options, report)

    if "set_default_role" in options:
        _set_default_role(options, mcp_user_id)

    if options.get("delete_connections"):
        _delete_connections(options, mcp_user_id)
    if options.get("delete_paths"):
        _delete_paths(options, mcp_user_id)
    if options.get("add_connection"):
        _add_connection(options, mcp_user_id)
    if options.get("add_paths"):
        _add_paths(options, mcp_user_id)
    if options.get("remove_migrator"):
        _remove_migrator()
    if options.get("install_migrator"):
        _install_migrator()
    if options.get("purge_orphan_groups"):
        _purge_orphan_groups()
    if options.get("show_api_key"):
        _show_api_keys(options, report)

    if report["json"]:
        print(json_module.dumps({"users": report.get("users", [])}, indent=2))

    # A settings file has to exist for the next run to reach the management
    # menu rather than the first-run walkthrough, exactly as _first_run ensures.
    config.set_allowed_paths(config.get_allowed_paths())
