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

"""``mcp setup-oauth``: how a multi-tenant server takes OAuth2 tokens.

A command of its own rather than more options of ``mcp setup``, which manages
the users and their connections: OAuth is one topic with many settings (see
:mod:`mcp_plugin.lib.oauth_config`). Like ``mcp setup`` it is declarative when
given options (:func:`apply`) and an interactive menu without
(:func:`run_setup_oauth`).

Several options mean something even when false or empty - ``--publicUrl=""``
clears the URL, ``--autoProvision=false`` turns it off - so for those it is
being GIVEN that makes them an action (see :data:`PRESENCE_OPTIONS`).

Shell plugin code: errors are raised as ``mysqlsh.Error``.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak cimd

import json as json_module
import os

import mysqlsh

from mcp_plugin.lib import oauth_config, tenants
from mcp_plugin.lib import setup_prompts as prompts

# Options that change something, in the order they are carried out.
ACTION_OPTIONS = (
    "public_url",
    "mode",
    "issuer",
    "verification",
    "introspection_client_id",
    "introspection_secret_env",
    "client_ids",
    "link_by_verified_email",
    "required_realm_role",
    "remove_login_server",
    "add_login_server",
    "required_role",
    "grant_max_lifetime",
    "grant_idle_timeout",
    "access_token_lifetime",
    "refresh_grace_period",
    "login_connection_store",
    "allowed_client_networks",
    "dynamic_client_registration",
    "cimd",
    "auto_provision",
    "default_scopes",
    "add_client",
    "set_client_redirect_uris",
    "set_client_allowed_roles",
    "rotate_client_secret",
    "remove_client",
    "show_client_secret",
    "list_clients",
    "rotate_signing_key",
    "drop_previous_signing_key",
    "revoke_tokens",
)

# Options that only qualify the ones above.
MODIFIER_OPTIONS = ("confidential", "redirect_uris", "roles", "no_verify", "show", "json")

KNOWN_OPTIONS = ACTION_OPTIONS + MODIFIER_OPTIONS

# Actions that are actions whenever they are given, whatever their value.
PRESENCE_OPTIONS = (
    "public_url",
    "link_by_verified_email",
    "auto_provision",
    "required_realm_role",
    "required_role",
    "grant_idle_timeout",
    "allowed_client_networks",
    "dynamic_client_registration",
    "cimd",
    "client_ids",
)

# Actions whose result --json can report, when they are the only ones.
JSON_ACTION_OPTIONS = (
    "add_client",
    "show_client_secret",
    "rotate_client_secret",
    "list_clients",
)

# The settings of the built-in server that are a number of seconds.
_SECONDS_OPTIONS = (
    ("grant_max_lifetime", "grantMaxLifetime"),
    ("access_token_lifetime", "accessTokenLifetime"),
    ("refresh_grace_period", "refreshGracePeriod"),
)


def _cli_name(option):
    from mcp_plugin.lib.setup_cli import _cli_name as name

    return name(option)


def _as_bool(value):
    from mcp_plugin.lib.setup_cli import _as_bool as as_bool

    return as_bool(value)


def _as_list(value):
    from mcp_plugin.lib.setup_cli import _as_list as as_list

    return as_list(value)


def _seconds(option, value, allow_zero=False) -> int:
    """Returns an option's value as a number of seconds."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise mysqlsh.Error(f"{_cli_name(option)} takes a number of seconds, got '{value}'.")
    if seconds < 0 or (seconds == 0 and not allow_zero):
        raise mysqlsh.Error(f"{_cli_name(option)} must be more than 0 seconds.")

    return seconds


def _actions(options: dict) -> list:
    """Returns the action options that were given."""
    return [
        name
        for name in ACTION_OPTIONS
        if options.get(name) or (name in PRESENCE_OPTIONS and name in options)
    ]


def check_combination(options: dict) -> None:
    """Refuses options that are unknown or do not go together.

    Raises:
        mysqlsh.Error: If the options cannot all be honoured.
    """
    unknown = sorted(name for name in options if name not in KNOWN_OPTIONS)
    if unknown:
        raise mysqlsh.Error(
            f"Unknown option(s): {', '.join(unknown)}. Supported options are: "
            f"{', '.join(sorted(_cli_name(name) for name in KNOWN_OPTIONS))}."
        )

    actions = _actions(options)
    if options.get("show"):
        if actions:
            raise mysqlsh.Error(
                "--show only reports the OAuth configuration, so it cannot be "
                f"combined with {', '.join(_cli_name(n) for n in actions)}."
            )
    elif options.get("json"):
        if not actions or any(name not in JSON_ACTION_OPTIONS for name in actions):
            raise mysqlsh.Error(
                "--json only applies to --show, and to "
                f"{', '.join(_cli_name(n) for n in JSON_ACTION_OPTIONS)} on their own."
            )

    for name, modifier_of in (
        ("confidential", ("add_client",)),
        ("redirect_uris", ("add_client", "set_client_redirect_uris")),
        ("roles", ("set_client_allowed_roles",)),
        ("no_verify", ("issuer",)),
    ):
        if name in options and not any(options.get(action) for action in modifier_of):
            raise mysqlsh.Error(
                f"{_cli_name(name)} only applies to "
                f"{', '.join(_cli_name(action) for action in modifier_of)}."
            )

    if not actions and not options.get("show") and not options.get("json"):
        raise mysqlsh.Error(
            "Nothing to do. Give an option, --show, or no options at all for the "
            "interactive setup - run 'mariadb-shell -- mcp setup-oauth --help'."
        )

    if options.get("set_client_redirect_uris") and "redirect_uris" not in options:
        raise mysqlsh.Error(
            f"{_cli_name('set_client_redirect_uris')} needs {_cli_name('redirect_uris')}."
        )
    if options.get("set_client_allowed_roles") and "roles" not in options:
        raise mysqlsh.Error(f"{_cli_name('set_client_allowed_roles')} needs {_cli_name('roles')}.")

    mode = str(options.get("mode") or oauth_config.get_mode()).strip().lower()
    if any(name in options for name in ("auto_provision", "default_scopes")) and (
        mode == oauth_config.OAUTH_MODE_NONE
    ):
        raise mysqlsh.Error(
            "Auto-provisioning belongs to an OAuth mode: give --mode=keycloak or "
            "--mode=builtin first."
        )


def _section(mode):
    """Returns the settings section a mode keeps its settings in."""
    if mode == oauth_config.OAUTH_MODE_NONE:
        raise mysqlsh.Error(
            "That setting belongs to an OAuth mode: give --mode=keycloak or "
            "--mode=builtin first."
        )

    return mode


def _set(path, value) -> None:
    """Sets one oauth setting, by its path in the settings."""

    def change(oauth):
        target = oauth
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    oauth_config.update_oauth_settings(change)


# --- Each setting, once -------------------------------------------------------------
#
# Shared by the options and the interactive menu, so both change a setting on
# the same terms and say the same about it.


def set_public_url(url) -> None:
    value = oauth_config.set_public_url(url)
    print(f"Public URL {'set to ' + value if value else 'cleared'}.")


def set_mode(mode) -> None:
    oauth_config.set_mode(mode)
    print(f"OAuth mode: {oauth_config.get_mode()}.")


def set_issuer(issuer, verify=True) -> None:
    issuer = str(issuer).strip().rstrip("/")
    if verify:
        from mcp_plugin.lib import oauth_keycloak

        try:
            oauth_keycloak.check_issuer(issuer)
        except Exception as error:  # noqa: BLE001 - surface what went wrong
            raise mysqlsh.Error(
                f"Could not read the OpenID configuration of '{issuer}': {error}. "
                "Pass --noVerify to save it anyway."
            ) from error
    _set(["keycloak", "issuer"], issuer)
    print(f"Keycloak issuer: {issuer}.")


def set_verification(verification) -> None:
    verification = str(verification).strip().lower()
    if verification not in oauth_config.VERIFICATIONS:
        raise mysqlsh.Error(
            f"'{verification}' is not a verification. Use one of: "
            f"{', '.join(oauth_config.VERIFICATIONS)}."
        )
    _set(["keycloak", "verification"], verification)
    print(f"Keycloak tokens are checked by: {verification}.")


def set_flag(path, enabled, label) -> None:
    _set(path, bool(enabled))
    print(f"{label}: {'on' if enabled else 'off'}.")


def set_text(path, value, label) -> None:
    value = str(value or "").strip()
    _set(path, value)
    print(f"{label}: {value or 'none'}.")


def add_login_server(uri) -> None:
    normalized = oauth_config.normalize_login_server(uri)
    servers = oauth_config.get_oauth_settings()["builtin"]["loginServers"]
    if normalized not in servers:
        _set(["builtin", "loginServers"], servers + [normalized])
    print(f"Login server '{normalized}' added.")


def remove_login_server(uri) -> None:
    normalized = oauth_config.normalize_login_server(uri)
    servers = oauth_config.get_oauth_settings()["builtin"]["loginServers"]
    if normalized not in servers:
        raise mysqlsh.Error(
            f"'{normalized}' is not a login server. Login servers: {', '.join(servers) or 'none'}."
        )
    _set(["builtin", "loginServers"], [server for server in servers if server != normalized])
    print(f"Login server '{normalized}' removed.")


def set_seconds(option, key, value) -> None:
    seconds = _seconds(option, value, allow_zero=option == "refresh_grace_period")
    _set(["builtin", key], seconds)
    print(f"{_cli_name(option)[2:]}: {seconds}s.")


def set_idle_timeout(value) -> None:
    seconds = _seconds("grant_idle_timeout", value, allow_zero=True)
    _set(["builtin", "grantIdleTimeout"], seconds or None)
    print(f"Sign-in idle timeout: {f'{seconds}s' if seconds else 'off'}.")


def set_login_connection_store(store) -> None:
    store = str(store).strip().lower()
    if store not in oauth_config.LOGIN_CONNECTION_STORES:
        raise mysqlsh.Error(
            f"'{store}' is not a store. Use one of: "
            f"{', '.join(oauth_config.LOGIN_CONNECTION_STORES)}."
        )
    _set(["builtin", "loginConnectionStore"], store)
    print(f"Sign-in connections are kept in: {store}.")


def set_allowed_client_networks(networks) -> None:
    normalized = oauth_config.normalize_networks(networks)
    _set(["builtin", "allowedClientNetworks"], normalized)
    print(f"Allowed client networks: {', '.join(normalized) or 'any'}.")


def add_client(name, confidential, redirect_uris, report) -> None:
    client_id, secret = oauth_config.add_client(
        name, confidential=confidential, redirect_uris=redirect_uris or []
    )
    report.setdefault("clients", []).append({"clientId": client_id, "clientSecret": secret})
    if report.get("json"):
        return

    print(f"OAuth client '{name}' registered.")
    print(f"Client ID:     {client_id}")
    if secret:
        print(f"Client secret: {secret}")
    if not redirect_uris:
        print(
            "It has no redirect URI yet: set it with "
            f"--setClientRedirectUris={client_id} --redirectUris=<uri>."
        )


def remove_client(identifier) -> None:
    from mcp_plugin.lib import oauth_builtin

    client_id = oauth_config.resolve_client(identifier)
    oauth_config.remove_client(client_id)
    ended = oauth_builtin.end_grants_of_client(client_id)
    print(
        f"OAuth client {client_id} removed"
        f"{f', and {ended} sign-in(s) through it ended' if ended else ''}."
    )


def revoke_tokens(identifier) -> None:
    from mcp_plugin.lib import oauth_builtin

    user = tenants.resolve_user(identifier)
    tenants.revoke_tokens(user)
    ended = oauth_builtin.end_grants_of_user(user)
    print(
        f"Every OAuth token of {tenants.describe_user(user)} revoked"
        f"{f', and {ended} sign-in(s) ended' if ended else ''}."
    )


def rotate_signing_key() -> None:
    kid = oauth_config.rotate_signing_key()
    lifetime = oauth_config.get_oauth_settings()["builtin"]["accessTokenLifetime"]
    print(
        f"New token signing key {kid}. Tokens signed with the previous key stay valid "
        f"until they expire, at most {lifetime}s; --dropPreviousSigningKey ends that now."
    )


def drop_previous_signing_key() -> None:
    if oauth_config.drop_previous_signing_key():
        print("The previous signing key was dropped: tokens it signed are refused from now on.")
    else:
        print("There is no previous signing key.")


# --- Options -------------------------------------------------------------------------


def apply(options: dict) -> None:
    """Carries out everything the given options ask for, or fails saying why.

    The order is fixed: the public URL and the mode first, so the settings
    after them can belong to the mode; then the settings; then the clients,
    the signing key and revocations.

    Args:
        options (dict): The options mcp.setupOauth was called with.

    Raises:
        mysqlsh.Error: If the options are unusable, or a step fails.
    """
    check_combination(options)

    if options.get("show"):
        _show(options)
        return

    report = {"json": bool(options.get("json"))}

    if "public_url" in options:
        set_public_url(options["public_url"])
    if options.get("mode"):
        set_mode(options["mode"])

    # Keycloak.
    if options.get("issuer"):
        set_issuer(options["issuer"], verify=not options.get("no_verify"))
    if options.get("verification"):
        set_verification(options["verification"])
    if options.get("introspection_client_id"):
        set_text(["keycloak", "introspectionClientId"], options["introspection_client_id"],
                 "Introspection client")
    if options.get("introspection_secret_env"):
        name = str(options["introspection_secret_env"])
        if name not in os.environ:
            raise mysqlsh.Error(f"--introspectionSecretEnv names '{name}', which is not set.")
        oauth_config.set_introspection_secret(os.environ[name])
        print("Introspection client secret stored.")
    if "client_ids" in options:
        clients = _as_list(options["client_ids"])
        _set(["keycloak", "clientIds"], clients)
        print(f"Keycloak clients accepted: {', '.join(clients) or 'any'}.")
    if "link_by_verified_email" in options:
        set_flag(["keycloak", "linkByVerifiedEmail"], _as_bool(options["link_by_verified_email"]),
                 "Linking sign-ins to users by verified email")
    if "required_realm_role" in options:
        set_text(["keycloak", "autoProvision", "requiredRealmRole"],
                 options["required_realm_role"], "Realm role needed to be created at sign-in")

    # The built-in server.
    for uri in _as_list(options.get("remove_login_server")):
        remove_login_server(uri)
    for uri in _as_list(options.get("add_login_server")):
        add_login_server(uri)
    if "required_role" in options:
        set_text(["builtin", "requiredRole"], options["required_role"],
                 "Role an account needs to sign in")
    for option, key in _SECONDS_OPTIONS:
        if option in options and options[option] is not None:
            set_seconds(option, key, options[option])
    if "grant_idle_timeout" in options:
        set_idle_timeout(options["grant_idle_timeout"])
    if options.get("login_connection_store"):
        set_login_connection_store(options["login_connection_store"])
    if "allowed_client_networks" in options:
        set_allowed_client_networks(options["allowed_client_networks"])
    if "dynamic_client_registration" in options:
        set_flag(["builtin", "dynamicClientRegistration"],
                 _as_bool(options["dynamic_client_registration"]), "Dynamic client registration")
    if "cimd" in options:
        set_flag(["builtin", "cimd"], _as_bool(options["cimd"]), "Client ID Metadata Documents")

    # Auto-provisioning, in whichever mode is now configured.
    mode = oauth_config.get_mode()
    if "auto_provision" in options:
        set_flag([_section(mode), "autoProvision", "enabled"], _as_bool(options["auto_provision"]),
                 "Creating users at their first sign-in")
    if options.get("default_scopes"):
        scopes = tenants.normalize_scopes(options["default_scopes"])
        _set([_section(mode), "autoProvision", "defaultScopes"], scopes)
        print(f"Users created at sign-in may be granted: {', '.join(scopes)}.")

    _apply_clients(options, report)

    if options.get("rotate_signing_key"):
        rotate_signing_key()
    if options.get("drop_previous_signing_key"):
        drop_previous_signing_key()
    for identifier in _as_list(options.get("revoke_tokens")):
        revoke_tokens(identifier)

    if report["json"]:
        print(json_module.dumps({"clients": report.get("clients", [])}, indent=2))


def _apply_clients(options: dict, report: dict) -> None:
    """Applies the client options."""
    if options.get("add_client"):
        add_client(options["add_client"], _as_bool(options.get("confidential", False)),
                   options.get("redirect_uris"), report)

    if options.get("set_client_redirect_uris"):
        client_id = oauth_config.resolve_client(options["set_client_redirect_uris"])
        uris = oauth_config.set_redirect_uris(client_id, options["redirect_uris"])
        print(f"Redirect URIs of {client_id}: {', '.join(uris) or 'none'}.")

    if options.get("set_client_allowed_roles"):
        client_id = oauth_config.resolve_client(options["set_client_allowed_roles"])
        roles = oauth_config.set_allowed_roles(client_id, options["roles"])
        print(f"Roles sessions of {client_id} may run under: {', '.join(roles) or 'any'}.")

    if options.get("rotate_client_secret"):
        client_id = oauth_config.resolve_client(options["rotate_client_secret"])
        secret = oauth_config.rotate_client_secret(client_id)
        report.setdefault("clients", []).append({"clientId": client_id, "clientSecret": secret})
        if not report.get("json"):
            print(f"New secret of {client_id}: {secret}\nThe previous secret no longer works.")

    if options.get("remove_client"):
        remove_client(options["remove_client"])

    if options.get("show_client_secret"):
        client_id = oauth_config.resolve_client(options["show_client_secret"])
        secret = oauth_config.get_client_secret(client_id)
        report.setdefault("clients", []).append({"clientId": client_id, "clientSecret": secret})
        if not report.get("json"):
            print(f"Secret of {client_id}: {secret or '(none - a public client)'}")

    if options.get("list_clients"):
        clients = describe_clients()
        report["clients"] = report.get("clients", []) + clients
        if not report.get("json"):
            _print_clients(clients)


def describe_clients() -> list:
    """Returns every client as --show and --listClients report it."""
    return [
        {
            "clientId": client_id,
            "name": record.get("name", ""),
            "confidential": bool(record.get("confidential")),
            "registered": record.get("registered", ""),
            "redirectUris": list(record.get("redirectUris", [])),
            "allowedRoles": list(record.get("allowedRoles", [])),
        }
        for client_id, record in sorted(oauth_config.read_clients().items())
    ]


def _print_clients(clients) -> None:
    print("OAuth clients:")
    for client in clients:
        print(
            f"  {client['clientId']}  {client['name'] or '-'}  "
            f"{'confidential' if client['confidential'] else 'public'}, "
            f"{client['registered']}; redirect URIs: "
            f"{', '.join(client['redirectUris']) or 'none'}; roles: "
            f"{', '.join(client['allowedRoles']) or 'any'}"
        )
    if not clients:
        print("  (none)")


def configuration() -> dict:
    """Returns the OAuth configuration as --show reports it. Nothing secret."""
    return {
        "public_url": oauth_config.get_public_url(),
        "oauth": oauth_config.get_oauth_settings(),
        "oauth_clients": describe_clients(),
    }


def _show(options: dict) -> None:
    """Prints the OAuth configuration, as JSON when --json was given."""
    current = configuration()
    if options.get("json"):
        print(json_module.dumps(current, indent=2))
        return

    _print_status(current)


def _print_status(current) -> None:
    """Prints the OAuth configuration as people read it."""
    oauth = current["oauth"]
    mode = oauth["mode"]
    print("=== MariaDB MCP Server OAuth configuration ===")
    print(f"Public URL:  {current['public_url'] or '(none)'}")
    print(f"OAuth mode:  {mode}")
    if mode == oauth_config.OAUTH_MODE_KEYCLOAK:
        keycloak = oauth["keycloak"]
        provision = keycloak["autoProvision"]
        print(f"Issuer:      {keycloak['issuer'] or '(none)'}")
        print(f"Checked by:  {keycloak['verification']}")
        print(f"Clients:     {', '.join(keycloak['clientIds']) or 'any'}")
        print(f"Link by verified email: {'on' if keycloak['linkByVerifiedEmail'] else 'off'}")
        print(
            f"Create users at sign-in: {'on' if provision['enabled'] else 'off'}"
            f" (realm role: {provision['requiredRealmRole'] or 'none'})"
        )
    elif mode == oauth_config.OAUTH_MODE_BUILTIN:
        builtin = oauth["builtin"]
        print(f"Login servers: {', '.join(builtin['loginServers']) or '(none)'}")
        print(f"Required role: {builtin['requiredRole'] or 'none'}")
        print(
            f"Sign-ins last: {builtin['grantMaxLifetime']}s; idle timeout: "
            f"{builtin['grantIdleTimeout'] or 'off'}; kept in: {builtin['loginConnectionStore']}"
        )
        print(
            f"Create users at sign-in: {'on' if builtin['autoProvision']['enabled'] else 'off'}; "
            f"dynamic registration: {'on' if builtin['dynamicClientRegistration'] else 'off'}; "
            f"CIMD: {'on' if builtin['cimd'] else 'off'}"
        )
        print(f"Allowed client networks: {', '.join(builtin['allowedClientNetworks']) or 'any'}")
        _print_clients(current["oauth_clients"])


# --- Interactive ----------------------------------------------------------------------

MENU_FINISH_LABEL = "Finish"


def _ask_yes_no(message, current) -> bool:
    return prompts.yes_no(message, default=bool(current))


def _menu_public_url() -> None:
    current = oauth_config.get_public_url()
    entered = prompts.ask(
        f"Public URL of the MCP endpoint (e.g. https://mcp.example.com/mcp)"
        f"{f' [{current}]' if current else ''}: "
    )
    if entered:
        set_public_url(entered)


def _menu_mode() -> None:
    labels = ["none - API keys only", "keycloak - Keycloak issues the tokens",
              "builtin - sign in with a MariaDB account"]
    choice = prompts.select("OAuth mode", labels)
    set_mode(oauth_config.OAUTH_MODES[choice])


def _menu_keycloak() -> None:
    keycloak = oauth_config.get_oauth_settings()["keycloak"]
    current = keycloak["issuer"]
    issuer = prompts.ask(
        "Realm issuer URL (e.g. https://kc.example.com/realms/mariadb)"
        f"{f' [{current}]' if current else ''}: "
    )
    if issuer:
        set_issuer(issuer)
    set_flag(["keycloak", "linkByVerifiedEmail"],
             _ask_yes_no("Link a sign-in to the user with its verified email?",
                         keycloak["linkByVerifiedEmail"]),
             "Linking sign-ins to users by verified email")
    enabled = _ask_yes_no("Create users at their first sign-in?",
                          keycloak["autoProvision"]["enabled"])
    set_flag(["keycloak", "autoProvision", "enabled"], enabled, "Creating users at sign-in")
    if enabled:
        role = prompts.ask(
            f"Realm role needed to be created [{keycloak['autoProvision']['requiredRealmRole']}]: "
        )
        if role:
            set_text(["keycloak", "autoProvision", "requiredRealmRole"], role,
                     "Realm role needed to be created at sign-in")


def _menu_keycloak_realm() -> None:
    from mcp_plugin.lib import setup_keycloak

    setup_keycloak.run_setup_keycloak_realm()


def _menu_builtin() -> None:
    builtin = oauth_config.get_oauth_settings()["builtin"]
    servers = builtin["loginServers"]
    print(f"Login servers: {', '.join(servers) or '(none)'}")
    entered = prompts.ask("Add a login server (e.g. mariadb://db.example.com:3306), empty for none: ")
    if entered:
        add_login_server(entered)
    if servers and prompts.yes_no("Remove a login server?", default=False):
        index = prompts.select_or_cancel("Select the login server to remove", servers)
        if index >= 0:
            remove_login_server(servers[index])
    role = prompts.ask(
        f"Role an account needs to sign in [{builtin['requiredRole'] or 'none'}] "
        "('-' for none): "
    )
    if role:
        set_text(["builtin", "requiredRole"], "" if role == "-" else role,
                 "Role an account needs to sign in")
    set_flag(["builtin", "autoProvision", "enabled"],
             _ask_yes_no("Create users at their first sign-in?", builtin["autoProvision"]["enabled"]),
             "Creating users at sign-in")
    days = prompts.ask(f"Days a sign-in lasts [{builtin['grantMaxLifetime'] // 86400}]: ")
    if days:
        set_seconds("grant_max_lifetime", "grantMaxLifetime", int(days) * 86400)
    set_flag(["builtin", "dynamicClientRegistration"],
             _ask_yes_no("Let clients register themselves?", builtin["dynamicClientRegistration"]),
             "Dynamic client registration")


def _menu_add_client() -> None:
    name = prompts.ask("Name of the client (e.g. arcade): ")
    if not name:
        return
    confidential = prompts.yes_no("Does it authenticate with a secret (a server-side client)?",
                                  default=True)
    uris = prompts.ask("Redirect URIs, comma-separated (empty to set them later): ")
    add_client(name, confidential, uris, {})


def _select_client(message):
    clients = describe_clients()
    if not clients:
        print("\nNo OAuth clients yet.")
        return None
    index = prompts.select_or_cancel(
        message, [f"{c['name'] or '-'} ({c['clientId']})" for c in clients]
    )

    return None if index < 0 else clients[index]["clientId"]


def _menu_client_redirects() -> None:
    client_id = _select_client("Select the client")
    if client_id:
        uris = oauth_config.set_redirect_uris(client_id, prompts.ask("Redirect URIs, comma-separated: "))
        print(f"Redirect URIs of {client_id}: {', '.join(uris) or 'none'}.")


def _menu_client_roles() -> None:
    client_id = _select_client("Select the client")
    if client_id:
        roles = oauth_config.set_allowed_roles(
            client_id, prompts.ask("Roles its sessions may run under, comma-separated (empty for any): ")
        )
        print(f"Roles sessions of {client_id} may run under: {', '.join(roles) or 'any'}.")


def _menu_client_secret() -> None:
    client_id = _select_client("Select the client whose secret to show")
    if client_id:
        print(f"Secret of {client_id}: {oauth_config.get_client_secret(client_id) or '(none - a public client)'}")


def _menu_remove_client() -> None:
    client_id = _select_client("Select the client to remove")
    if client_id and prompts.yes_no(f"Remove {client_id}, ending every sign-in through it?",
                                    default=False):
        remove_client(client_id)


def _menu_revoke() -> None:
    users = tenants.read_users()
    if not users:
        print("\nNo users yet.")
        return
    ids = sorted(users)
    index = prompts.select_or_cancel("Select the user whose tokens to revoke",
                                     [tenants.describe_user(u, users[u]) for u in ids])
    if index >= 0:
        revoke_tokens(ids[index])


def _menu_rotate_key() -> None:
    if prompts.yes_no("Rotate the token signing key?", default=False):
        rotate_signing_key()
        if prompts.yes_no(
            "Should the tokens signed with the previous key stop working at once "
            "(for a key that may have leaked)?",
            default=False,
        ):
            drop_previous_signing_key()


def _menu_entries() -> list:
    """Returns the menu's (label, action) pairs; what applies to the mode only."""
    entries = [
        ("Set the public URL", _menu_public_url),
        ("Choose the OAuth mode", _menu_mode),
    ]
    mode = oauth_config.get_mode()
    if mode == oauth_config.OAUTH_MODE_KEYCLOAK:
        entries += [
            ("Configure Keycloak", _menu_keycloak),
            ("Prepare a Keycloak realm for this server", _menu_keycloak_realm),
        ]
    if mode == oauth_config.OAUTH_MODE_BUILTIN:
        entries += [
            ("Configure the built-in sign-in", _menu_builtin),
            ("Register an OAuth client", _menu_add_client),
            ("Set a client's redirect URIs", _menu_client_redirects),
            ("Set a client's allowed roles", _menu_client_roles),
            ("Show a client's secret", _menu_client_secret),
            ("Remove a client", _menu_remove_client),
            ("Rotate the token signing key", _menu_rotate_key),
        ]
    if mode != oauth_config.OAUTH_MODE_NONE:
        entries.append(("Revoke a user's tokens", _menu_revoke))

    return entries


def menu() -> None:
    """The interactive OAuth setup."""
    while True:
        print()
        _print_status(configuration())
        entries = _menu_entries()
        labels = [label for label, _ in entries] + [MENU_FINISH_LABEL]
        choice = prompts.select("\nWhat would you like to do?", labels, default=len(entries))
        if choice == len(entries):
            break
        try:
            entries[choice][1]()
        except mysqlsh.Error as error:
            print(error)


def run_setup_oauth(**options) -> None:
    """Runs the OAuth setup, interactively or from the given options."""
    if options:
        apply(options)
        return

    if not prompts.shell().options.useWizards:
        raise mysqlsh.Error(
            "mcp.setupOauth must be run from an interactive shell session, or with "
            "options - run 'mariadb-shell -- mcp setup-oauth --help' for those."
        )

    menu()
    print("\nOAuth setup complete.")
