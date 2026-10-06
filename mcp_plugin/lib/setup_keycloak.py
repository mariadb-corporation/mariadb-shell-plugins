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

"""``mcp setup-keycloak-realm``: prepares a Keycloak realm for this server.

Run once by a Keycloak administrator, from the shell. Anything not given as an
option is asked for - the admin password with the shell's own password prompt -
unless ``--nonInteractive`` forbids it. Everything it creates is left alone if
it exists already, so it is safe to run again. Through Keycloak's admin REST
API, in the given realm, it creates:

* the client scopes ``mcp:db`` and ``mcp:msm`` (included in the token's
  ``scope``), each with an ``Audience`` mapper that puts this server's public
  URL into the access token's ``aud`` - which the server requires, as the MCP
  specification forbids accepting tokens issued for anything else;
* the realm role ``mcp-user``, which a token must carry for its user to be
  created at their first sign-in, given to the users ``--grantRealmRoleTo``
  names;
* a public client for MCP clients that do not register themselves (Claude
  Code, VS Code, ...): PKCE S256 required, loopback redirect URIs on any port,
  the two scopes as optional scopes. ``--directGrant`` also allows the password
  grant, for the opt-in live test only - never on a production client.

Unless ``--configureServer=false``, it then points this server at the realm:
``mcp setup-oauth --mode=keycloak --issuer=<realm issuer>``.

Dynamic client registration is a realm policy decision and is NOT changed: by
default Keycloak refuses anonymous registration from hosts that are not trusted
(its "Trusted Hosts" policy).

Shell plugin code: errors are raised as ``mysqlsh.Error``.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak keycloak pkce

import json
import os
import urllib.error
import urllib.parse
import urllib.request

import mysqlsh

from mcp_plugin.lib import oauth_config, tenants
from mcp_plugin.lib import setup_prompts as prompts

KNOWN_OPTIONS = (
    "server",
    "realm",
    "admin_realm",
    "admin_user",
    "admin_password",
    "admin_password_env",
    "mcp_url",
    "client_id",
    "direct_grant",
    "grant_realm_role_to",
    "configure_server",
    "non_interactive",
)

# The realm role a token needs for its user to be created at first sign-in.
REALM_ROLE = "mcp-user"

# The client created for MCP clients, unless another id is given.
DEFAULT_CLIENT_ID = "mariadb-mcp"

# The HTTP timeout of every admin call, in seconds.
_TIMEOUT = 30


class KeycloakAdmin:
    """A minimal client of the Keycloak admin REST API."""

    def __init__(self, server, realm, token):
        self.server = server.rstrip("/")
        self.realm = realm
        self.token = token

    @classmethod
    def login(cls, server, realm, admin_realm, user, password):
        """Signs in to the admin API with the admin-cli client."""
        data = urllib.parse.urlencode(
            {"grant_type": "password", "client_id": "admin-cli", "username": user,
             "password": password}
        ).encode()
        url = f"{server.rstrip('/')}/realms/{admin_realm}/protocol/openid-connect/token"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data),
                                        timeout=_TIMEOUT) as response:
                token = json.load(response)["access_token"]
        except urllib.error.HTTPError as error:
            raise mysqlsh.Error(
                f"Keycloak refused the administrator sign-in to realm '{admin_realm}': "
                f"{error.code} {error.read().decode(errors='replace')}"
            ) from None
        except urllib.error.URLError as error:
            raise mysqlsh.Error(f"Could not reach Keycloak at '{server}': {error.reason}") from None

        return cls(server, realm, token)

    def call(self, method, path, body=None):
        """Calls the admin API; a conflict (it exists already) answers None."""
        url = f"{self.server}/admin/realms/{self.realm}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                text = response.read()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return None
            raise mysqlsh.Error(
                f"Keycloak refused {method} {path}: {error.code} "
                f"{error.read().decode(errors='replace')}"
            ) from None


def ensure_scope(admin, name, mcp_url) -> str:
    """Creates a client scope with an audience mapper; returns its id."""
    existing = {scope["name"]: scope for scope in admin.call("GET", "/client-scopes")}
    if name not in existing:
        admin.call("POST", "/client-scopes", {
            "name": name,
            "protocol": "openid-connect",
            "attributes": {"include.in.token.scope": "true",
                           "display.on.consent.screen": "true"},
        })
        existing = {scope["name"]: scope for scope in admin.call("GET", "/client-scopes")}
        print(f"Client scope {name} created.")
    scope_id = existing[name]["id"]

    mappers = admin.call("GET", f"/client-scopes/{scope_id}/protocol-mappers/models") or []
    audiences = [
        m.get("config", {}).get("included.custom.audience")
        for m in mappers
        if m.get("protocolMapper") == "oidc-audience-mapper"
    ]
    if mcp_url not in audiences:
        admin.call("POST", f"/client-scopes/{scope_id}/protocol-mappers/models", {
            "name": f"mcp-audience {mcp_url}"[:255],
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": mcp_url,
                       "access.token.claim": "true", "id.token.claim": "false"},
        })
        print(f"Audience {mcp_url} added to {name}.")

    return scope_id


def ensure_role(admin) -> dict:
    """Creates the realm role auto-provisioning requires; returns it."""
    roles = {role["name"]: role for role in admin.call("GET", "/roles")}
    if REALM_ROLE not in roles:
        admin.call("POST", "/roles", {"name": REALM_ROLE,
                                      "description": "May use the MariaDB MCP server"})
        roles = {role["name"]: role for role in admin.call("GET", "/roles")}
        print(f"Realm role {REALM_ROLE} created.")

    return roles[REALM_ROLE]


def grant_role(admin, role, username) -> None:
    """Gives a user the realm role, found by user name or by email."""
    quoted = urllib.parse.quote(username)
    users = admin.call("GET", f"/users?username={quoted}&exact=true") or admin.call(
        "GET", f"/users?email={quoted}&exact=true"
    )
    if not users:
        raise mysqlsh.Error(f"There is no user '{username}' in the realm.")

    admin.call("POST", f"/users/{users[0]['id']}/role-mappings/realm",
               [{"id": role["id"], "name": role["name"]}])
    print(f"Realm role {role['name']} given to {username}.")


def ensure_client(admin, client_id, scope_ids, direct_grant) -> None:
    """Creates the public PKCE client and attaches the scopes as optional."""
    path = f"/clients?clientId={urllib.parse.quote(client_id)}"
    found = admin.call("GET", path)
    if not found:
        admin.call("POST", "/clients", {
            "clientId": client_id,
            "name": "MariaDB MCP clients",
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": bool(direct_grant),
            "redirectUris": ["http://127.0.0.1/*", "http://localhost/*"],
            "attributes": {"pkce.code.challenge.method": "S256"},
        })
        found = admin.call("GET", path)
        print(f"Client {client_id} created.")
    elif bool(found[0].get("directAccessGrantsEnabled")) != bool(direct_grant):
        client = dict(found[0])
        client["directAccessGrantsEnabled"] = bool(direct_grant)
        admin.call("PUT", f"/clients/{client['id']}", client)
        print(f"Direct access grants on {client_id}: {'on' if direct_grant else 'off'}.")
    internal_id = found[0]["id"]

    for scope_id in scope_ids:
        admin.call("PUT", f"/clients/{internal_id}/optional-client-scopes/{scope_id}")
    print(f"Client {client_id} may request {', '.join(tenants.SUPPORTED_SCOPES)}.")


def _ask(options, name, message, secret=False):
    """Returns an option's value, asking for it if it was not given."""
    if options.get(name):
        return str(options[name])

    if options.get("non_interactive") or not prompts.shell().options.useWizards:
        from mcp_plugin.lib.setup_cli import _cli_name

        raise mysqlsh.Error(f"{_cli_name(name)} is needed, and this run cannot ask for it.")

    value = prompts.password(message) if secret else prompts.ask(message)
    if not value:
        raise mysqlsh.Error("Nothing was entered; the realm was not prepared.")

    return value


def _admin_password(options) -> str:
    """Returns the admin password: from the environment, the option, or a prompt."""
    if options.get("admin_password_env"):
        name = str(options["admin_password_env"])
        if name not in os.environ:
            raise mysqlsh.Error(f"--adminPasswordEnv names '{name}', which is not set.")
        return os.environ[name]

    return _ask(options, "admin_password", "Keycloak administrator password: ", secret=True)


def run_setup_keycloak_realm(**options) -> None:
    """Prepares a Keycloak realm, asking for whatever was not given.

    Raises:
        mysqlsh.Error: If an option is unknown, something needed is missing and
            cannot be asked for, or Keycloak refuses a step.
    """
    from mcp_plugin.lib.setup_cli import _as_bool, _as_list, _cli_name

    unknown = sorted(name for name in options if name not in KNOWN_OPTIONS)
    if unknown:
        raise mysqlsh.Error(
            f"Unknown option(s): {', '.join(unknown)}. Supported options are: "
            f"{', '.join(sorted(_cli_name(name) for name in KNOWN_OPTIONS))}."
        )

    server = _ask(options, "server", "Keycloak URL (e.g. https://kc.example.com): ").rstrip("/")
    realm = _ask(options, "realm", "Realm: ")
    admin_realm = str(options.get("admin_realm") or "master")
    admin_user = _ask(options, "admin_user", f"Administrator of realm '{admin_realm}': ")
    password = _admin_password(options)

    mcp_url = options.get("mcp_url") or oauth_config.get_public_url()
    mcp_url = oauth_config.normalize_public_url(
        mcp_url or _ask(options, "mcp_url", "This server's public URL (e.g. https://mcp.example.com/mcp): ")
    )
    client_id = str(options.get("client_id") or DEFAULT_CLIENT_ID)

    admin = KeycloakAdmin.login(server, realm, admin_realm, admin_user, password)
    scope_ids = [ensure_scope(admin, name, mcp_url) for name in tenants.SUPPORTED_SCOPES]
    role = ensure_role(admin)
    for username in _as_list(options.get("grant_realm_role_to")):
        grant_role(admin, role, username)
    ensure_client(admin, client_id, scope_ids, _as_bool(options.get("direct_grant", False)))

    issuer = f"{server}/realms/{realm}"
    print(f"Realm '{realm}' is prepared for {mcp_url}. Issuer: {issuer}")

    if _as_bool(options.get("configure_server", True)):
        from mcp_plugin.lib import setup_oauth

        if oauth_config.get_public_url() != mcp_url:
            setup_oauth.set_public_url(mcp_url)
        setup_oauth.set_mode(oauth_config.OAUTH_MODE_KEYCLOAK)
        setup_oauth.set_issuer(issuer)
