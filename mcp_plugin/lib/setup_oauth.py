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

"""The OAuth2 options of the non-interactive ``mcp setup``.

Part of :mod:`mcp_plugin.lib.setup_cli`, which dispatches to :func:`apply`
after the user options and before the connection ones. Kept apart because
there are many of them and they configure one thing: how a multi-tenant
server takes OAuth2 tokens (see :mod:`mcp_plugin.lib.oauth_config`).

Several options mean something even when false or empty - ``--publicUrl=""``
clears the URL, ``--oauthAutoProvision=false`` turns it off - so for those it
is being GIVEN that makes them an action (see :data:`PRESENCE_OPTIONS`).
"""

# cSpell:ignore mysqlsh MariaDB Keycloak cimd

import os

import mysqlsh

from mcp_plugin.lib import oauth_config, tenants

# Options that change something.
ACTION_OPTIONS = (
    "public_url",
    "oauth_mode",
    "oauth_issuer",
    "oauth_verification",
    "oauth_introspection_client_id",
    "oauth_introspection_secret_env",
    "oauth_client_ids",
    "oauth_link_by_verified_email",
    "oauth_auto_provision",
    "oauth_required_realm_role",
    "oauth_default_scopes",
    "add_login_server",
    "remove_login_server",
    "oauth_required_role",
    "oauth_grant_max_lifetime",
    "oauth_grant_idle_timeout",
    "oauth_access_token_lifetime",
    "oauth_refresh_grace_period",
    "oauth_login_connection_store",
    "oauth_allowed_client_networks",
    "oauth_dynamic_client_registration",
    "oauth_cimd",
    "add_oauth_client",
    "set_oauth_client_redirect_uris",
    "set_oauth_client_allowed_roles",
    "show_oauth_client_secret",
    "rotate_oauth_client_secret",
    "remove_oauth_client",
    "list_oauth_clients",
    "rotate_signing_key",
    "revoke_tokens",
    "set_default_role",
)

# Options that only qualify the ones above.
MODIFIER_OPTIONS = ("confidential", "redirect_uris", "roles")

# Actions that are actions whenever they are given, whatever their value.
PRESENCE_OPTIONS = (
    "public_url",
    "oauth_link_by_verified_email",
    "oauth_auto_provision",
    "oauth_required_realm_role",
    "oauth_required_role",
    "oauth_grant_idle_timeout",
    "oauth_allowed_client_networks",
    "oauth_dynamic_client_registration",
    "oauth_cimd",
    "oauth_client_ids",
    "set_default_role",
)

# Actions on one user, named by --user.
USER_TARGETED_OPTIONS = ("set_default_role",)

# Actions whose result --json can report.
JSON_ACTION_OPTIONS = (
    "add_oauth_client",
    "show_oauth_client_secret",
    "rotate_oauth_client_secret",
    "list_oauth_clients",
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


def check_combination(options: dict, mode_after: str) -> None:
    """Refuses OAuth options that do not go together.

    Args:
        options (dict): The options mcp.setup was called with.
        mode_after (str): The OAuth mode once the options are applied.

    Raises:
        mysqlsh.Error: If the options cannot all be honoured.
    """
    for name, modifier_of in (
        ("confidential", ("add_oauth_client",)),
        ("redirect_uris", ("add_oauth_client", "set_oauth_client_redirect_uris")),
        ("roles", ("set_oauth_client_allowed_roles",)),
    ):
        if name in options and not any(options.get(action) for action in modifier_of):
            raise mysqlsh.Error(
                f"{_cli_name(name)} only applies to "
                f"{', '.join(_cli_name(action) for action in modifier_of)}."
            )

    if options.get("set_oauth_client_redirect_uris") and "redirect_uris" not in options:
        raise mysqlsh.Error(
            f"{_cli_name('set_oauth_client_redirect_uris')} needs "
            f"{_cli_name('redirect_uris')}."
        )
    if options.get("set_oauth_client_allowed_roles") and "roles" not in options:
        raise mysqlsh.Error(
            f"{_cli_name('set_oauth_client_allowed_roles')} needs {_cli_name('roles')}."
        )

    if any(name in options for name in ("oauth_auto_provision", "oauth_default_scopes")) and (
        mode_after == oauth_config.OAUTH_MODE_NONE
    ):
        raise mysqlsh.Error(
            "Auto-provisioning belongs to an OAuth mode: give --oauthMode=keycloak or "
            "--oauthMode=builtin first."
        )


def _section(mode):
    """Returns the oauth settings section a mode keeps its settings in."""
    if mode == oauth_config.OAUTH_MODE_NONE:
        raise mysqlsh.Error(
            "That setting belongs to an OAuth mode: give --oauthMode=keycloak or "
            "--oauthMode=builtin first."
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


def apply(options: dict, report: dict, mcp_user_id=None) -> None:
    """Carries out the OAuth options, in a fixed order.

    The mode first, so the settings after it can belong to it; then the
    settings; then the clients, keys and revocations.

    Args:
        options (dict): The options mcp.setup was called with.
        report (dict): What --json reports, added to.
        mcp_user_id: The user --user named, for set_default_role.
    """
    if "public_url" in options:
        url = oauth_config.set_public_url(options["public_url"])
        print(f"Public URL {'set to ' + url if url else 'cleared'}.")

    if options.get("oauth_mode"):
        oauth_config.set_mode(options["oauth_mode"])
        print(f"OAuth mode: {oauth_config.get_mode()}.")

    mode = oauth_config.get_mode()
    _apply_keycloak(options)
    _apply_builtin(options)

    if "oauth_auto_provision" in options:
        enabled = _as_bool(options["oauth_auto_provision"])
        _set([_section(mode), "autoProvision", "enabled"], enabled)
        print(f"Creating users at their first sign-in: {'on' if enabled else 'off'}.")
    if options.get("oauth_default_scopes"):
        scopes = tenants.normalize_scopes(options["oauth_default_scopes"])
        _set([_section(mode), "autoProvision", "defaultScopes"], scopes)
        print(f"Users created at sign-in may be granted: {', '.join(scopes)}.")

    _apply_clients(options, report)

    if options.get("rotate_signing_key"):
        kid = oauth_config.rotate_signing_key()
        print(f"New token signing key {kid}: every access token issued so far stops working.")

    for identifier in _as_list(options.get("revoke_tokens")):
        from mcp_plugin.lib import oauth_builtin

        user = tenants.resolve_user(identifier)
        tenants.revoke_tokens(user)
        ended = oauth_builtin.end_grants_of_user(user)
        print(
            f"Every OAuth token of {tenants.describe_user(user)} revoked"
            f"{f', and {ended} sign-in(s) ended' if ended else ''}."
        )

    if "set_default_role" in options:
        tenants.set_default_role(mcp_user_id, options["set_default_role"])
        role = tenants.get_default_role(mcp_user_id)
        print(
            f"Sessions of {tenants.describe_user(mcp_user_id)} run under "
            f"{f'the role {role}' if role else 'the account default role'}."
        )


def _apply_keycloak(options: dict) -> None:
    """Applies the Keycloak settings."""
    if options.get("oauth_issuer"):
        issuer = str(options["oauth_issuer"]).strip().rstrip("/")
        if not options.get("no_verify"):
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

    if options.get("oauth_verification"):
        verification = str(options["oauth_verification"]).strip().lower()
        if verification not in oauth_config.VERIFICATIONS:
            raise mysqlsh.Error(
                f"'{verification}' is not a verification. Use one of: "
                f"{', '.join(oauth_config.VERIFICATIONS)}."
            )
        _set(["keycloak", "verification"], verification)
        print(f"Keycloak tokens are checked by: {verification}.")

    if options.get("oauth_introspection_client_id"):
        _set(["keycloak", "introspectionClientId"], str(options["oauth_introspection_client_id"]))
        print("Introspection client set.")

    if options.get("oauth_introspection_secret_env"):
        name = str(options["oauth_introspection_secret_env"])
        if name not in os.environ:
            raise mysqlsh.Error(f"--oauthIntrospectionSecretEnv names '{name}', which is not set.")
        oauth_config.set_introspection_secret(os.environ[name])
        print("Introspection client secret stored.")

    if "oauth_client_ids" in options:
        clients = _as_list(options["oauth_client_ids"])
        _set(["keycloak", "clientIds"], clients)
        print(f"Keycloak clients accepted: {', '.join(clients) or 'any'}.")

    if "oauth_link_by_verified_email" in options:
        enabled = _as_bool(options["oauth_link_by_verified_email"])
        _set(["keycloak", "linkByVerifiedEmail"], enabled)
        print(f"Linking sign-ins to users by verified email: {'on' if enabled else 'off'}.")

    if "oauth_required_realm_role" in options:
        role = str(options["oauth_required_realm_role"] or "").strip()
        _set(["keycloak", "autoProvision", "requiredRealmRole"], role)
        print(f"Realm role needed to be created at sign-in: {role or 'none'}.")


def _apply_builtin(options: dict) -> None:
    """Applies the built-in authorization server's settings."""
    servers = oauth_config.get_oauth_settings()["builtin"]["loginServers"]
    for uri in _as_list(options.get("remove_login_server")):
        normalized = oauth_config.normalize_login_server(uri)
        if normalized not in servers:
            raise mysqlsh.Error(
                f"'{normalized}' is not a login server. Login servers: "
                f"{', '.join(servers) or 'none'}."
            )
        servers = [server for server in servers if server != normalized]
        _set(["builtin", "loginServers"], servers)
        print(f"Login server '{normalized}' removed.")
    for uri in _as_list(options.get("add_login_server")):
        normalized = oauth_config.normalize_login_server(uri)
        if normalized not in servers:
            servers = servers + [normalized]
            _set(["builtin", "loginServers"], servers)
        print(f"Login server '{normalized}' added.")

    if "oauth_required_role" in options:
        role = str(options["oauth_required_role"] or "").strip()
        _set(["builtin", "requiredRole"], role)
        print(f"Role an account needs to sign in: {role or 'none'}.")

    for name, key in (
        ("oauth_grant_max_lifetime", "grantMaxLifetime"),
        ("oauth_access_token_lifetime", "accessTokenLifetime"),
        ("oauth_refresh_grace_period", "refreshGracePeriod"),
    ):
        if options.get(name) is not None and name in options:
            seconds = _seconds(name, options[name], allow_zero=name == "oauth_refresh_grace_period")
            _set(["builtin", key], seconds)
            print(f"{_cli_name(name)[2:]}: {seconds}s.")

    if "oauth_grant_idle_timeout" in options:
        seconds = _seconds("oauth_grant_idle_timeout", options["oauth_grant_idle_timeout"],
                           allow_zero=True)
        _set(["builtin", "grantIdleTimeout"], seconds or None)
        print(f"Grant idle timeout: {f'{seconds}s' if seconds else 'off'}.")

    if options.get("oauth_login_connection_store"):
        store = str(options["oauth_login_connection_store"]).strip().lower()
        if store not in oauth_config.LOGIN_CONNECTION_STORES:
            raise mysqlsh.Error(
                f"'{store}' is not a store. Use one of: "
                f"{', '.join(oauth_config.LOGIN_CONNECTION_STORES)}."
            )
        _set(["builtin", "loginConnectionStore"], store)
        print(f"Sign-in connections are kept in: {store}.")

    if "oauth_allowed_client_networks" in options:
        networks = oauth_config.normalize_networks(options["oauth_allowed_client_networks"])
        _set(["builtin", "allowedClientNetworks"], networks)
        print(f"Allowed client networks: {', '.join(networks) or 'any'}.")

    for name, key in (
        ("oauth_dynamic_client_registration", "dynamicClientRegistration"),
        ("oauth_cimd", "cimd"),
    ):
        if name in options:
            enabled = _as_bool(options[name])
            _set(["builtin", key], enabled)
            print(f"{_cli_name(name)[2:]}: {'on' if enabled else 'off'}.")


def _apply_clients(options: dict, report: dict) -> None:
    """Applies the client options."""
    as_json = report.get("json")

    if options.get("add_oauth_client"):
        confidential = _as_bool(options.get("confidential", False))
        client_id, secret = oauth_config.add_client(
            options["add_oauth_client"],
            confidential=confidential,
            redirect_uris=options.get("redirect_uris") or [],
        )
        report.setdefault("clients", []).append(
            {"clientId": client_id, "clientSecret": secret}
        )
        if not as_json:
            print(f"OAuth client '{options['add_oauth_client']}' registered.")
            print(f"Client ID:     {client_id}")
            if secret:
                print(f"Client secret: {secret}")
            if not options.get("redirect_uris"):
                print(
                    "It has no redirect URI yet: set it with "
                    f"--setOAuthClientRedirectUris={client_id} --redirectUris=<uri>."
                )

    if options.get("set_oauth_client_redirect_uris"):
        client_id = oauth_config.resolve_client(options["set_oauth_client_redirect_uris"])
        uris = oauth_config.set_redirect_uris(client_id, options["redirect_uris"])
        print(f"Redirect URIs of {client_id}: {', '.join(uris) or 'none'}.")

    if options.get("set_oauth_client_allowed_roles"):
        client_id = oauth_config.resolve_client(options["set_oauth_client_allowed_roles"])
        roles = oauth_config.set_allowed_roles(client_id, options["roles"])
        print(f"Roles sessions of {client_id} may run under: {', '.join(roles) or 'any'}.")

    if options.get("rotate_oauth_client_secret"):
        client_id = oauth_config.resolve_client(options["rotate_oauth_client_secret"])
        secret = oauth_config.rotate_client_secret(client_id)
        report.setdefault("clients", []).append({"clientId": client_id, "clientSecret": secret})
        if not as_json:
            print(f"New secret of {client_id}: {secret}\nThe previous secret no longer works.")

    if options.get("remove_oauth_client"):
        from mcp_plugin.lib import oauth_builtin

        client_id = oauth_config.resolve_client(options["remove_oauth_client"])
        oauth_config.remove_client(client_id)
        ended = oauth_builtin.end_grants_of_client(client_id)
        print(
            f"OAuth client {client_id} removed"
            f"{f', and {ended} sign-in(s) through it ended' if ended else ''}."
        )

    if options.get("show_oauth_client_secret"):
        client_id = oauth_config.resolve_client(options["show_oauth_client_secret"])
        secret = oauth_config.get_client_secret(client_id)
        report.setdefault("clients", []).append({"clientId": client_id, "clientSecret": secret})
        if not as_json:
            print(f"Secret of {client_id}: {secret or '(none - a public client)'}")

    if options.get("list_oauth_clients"):
        clients = describe_clients()
        report["clients"] = report.get("clients", []) + clients
        if not as_json:
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


def describe_clients() -> list:
    """Returns every client as --show and --listOAuthClients report it."""
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


def configuration() -> dict:
    """Returns the OAuth part of what --show reports. Nothing secret."""
    oauth = oauth_config.get_oauth_settings()

    return {
        "public_url": oauth_config.get_public_url(),
        "oauth": oauth,
        "oauth_clients": describe_clients(),
    }
