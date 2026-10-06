#!/usr/bin/env python3
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

"""Prepares a Keycloak realm to issue tokens for a MariaDB MCP server.

Run once by a Keycloak administrator, with any Python 3 (standard library
only). Everything it creates is left alone if it exists already, so it is safe
to run again. It creates, in the given realm:

* the client scopes ``mcp:db`` and ``mcp:msm`` (Optional, included in the
  token's ``scope``), each with an ``Audience`` mapper that puts the MCP
  server's public URL into the access token's ``aud`` - which the server
  requires, as the MCP specification forbids accepting tokens issued for
  anything else;
* the realm role ``mcp-user``, which a token must carry for its user to be
  created at their first sign-in (``mcp setup --oauthRequiredRealmRole``);
* a public client for MCP clients that do not register themselves (Claude Code,
  VS Code, ...): PKCE S256 required, loopback redirect URIs on any port, the two
  scopes as optional scopes. With ``--direct-grant`` it also allows the password
  grant, for the opt-in live test only - never on a production client.

Example::

    python3 keycloak_realm_setup.py --server http://keycloak:8080 --realm mariadb \\
        --admin-user admin --admin-password-env KC_ADMIN_PASSWORD \\
        --mcp-url https://mcp.example.com/mcp --client-id mariadb-mcp

Then configure the MCP server::

    mariadb-shell -- mcp setup --publicUrl=https://mcp.example.com/mcp \\
        --oauthMode=keycloak --oauthIssuer=http://keycloak:8080/realms/mariadb

and give users the realm role ``mcp-user`` (and a verified email, to be linked
to a user an administrator created by email).

Dynamic client registration (for clients that register themselves) is a realm
policy decision and is NOT changed by this script: by default Keycloak refuses
anonymous registration from hosts that are not trusted ("Trusted Hosts"
policy). See the README's Keycloak section.
"""

# cSpell:ignore Keycloak keycloak pkce

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOOL_SCOPES = ("mcp:db", "mcp:msm")
REALM_ROLE = "mcp-user"


class Admin:
    """A minimal client of the Keycloak admin REST API."""

    def __init__(self, server, realm, token):
        self.server = server.rstrip("/")
        self.realm = realm
        self.token = token

    @classmethod
    def login(cls, server, realm, admin_realm, user, password):
        data = urllib.parse.urlencode(
            {"grant_type": "password", "client_id": "admin-cli", "username": user,
             "password": password}
        ).encode()
        url = f"{server.rstrip('/')}/realms/{admin_realm}/protocol/openid-connect/token"
        with urllib.request.urlopen(urllib.request.Request(url, data=data)) as response:
            return cls(server, realm, json.load(response)["access_token"])

    def call(self, method, path, body=None):
        url = f"{self.server}/admin/realms/{self.realm}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request) as response:
                text = response.read()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as error:
            if error.code == 409:
                return None
            raise RuntimeError(f"{method} {path}: {error.code} {error.read().decode()}") from None


def ensure_scope(admin, name, mcp_url):
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
    if not any(m.get("name") == "mcp-audience" for m in mappers):
        admin.call("POST", f"/client-scopes/{scope_id}/protocol-mappers/models", {
            "name": "mcp-audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": mcp_url,
                       "access.token.claim": "true", "id.token.claim": "false"},
        })
        print(f"Audience mapper ({mcp_url}) added to {name}.")

    return scope_id


def ensure_role(admin):
    """Creates the realm role auto-provisioning requires."""
    roles = {role["name"] for role in admin.call("GET", "/roles")}
    if REALM_ROLE not in roles:
        admin.call("POST", "/roles", {"name": REALM_ROLE,
                                      "description": "May use the MariaDB MCP server"})
        print(f"Realm role {REALM_ROLE} created.")


def ensure_client(admin, client_id, scope_ids, direct_grant):
    """Creates the public PKCE client and attaches the scopes as optional."""
    found = admin.call("GET", f"/clients?clientId={urllib.parse.quote(client_id)}")
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
        found = admin.call("GET", f"/clients?clientId={urllib.parse.quote(client_id)}")
        print(f"Client {client_id} created.")
    internal_id = found[0]["id"]

    for scope_id in scope_ids:
        admin.call("PUT", f"/clients/{internal_id}/optional-client-scopes/{scope_id}")
    print(f"Client {client_id} may request {', '.join(TOOL_SCOPES)}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", required=True, help="Keycloak base URL")
    parser.add_argument("--realm", required=True, help="the realm to prepare")
    parser.add_argument("--admin-realm", default="master",
                        help="the realm the administrator signs in to (default: master)")
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--admin-password-env", required=True,
                        help="the NAME of an environment variable holding the password")
    parser.add_argument("--mcp-url", required=True,
                        help="the MCP server's public URL (its --publicUrl)")
    parser.add_argument("--client-id", default="mariadb-mcp")
    parser.add_argument("--direct-grant", action="store_true",
                        help="allow the password grant on the client - for tests only")
    args = parser.parse_args(argv)

    password = os.environ.get(args.admin_password_env)
    if password is None:
        parser.error(f"{args.admin_password_env} is not set")

    admin = Admin.login(args.server, args.realm, args.admin_realm, args.admin_user, password)
    scope_ids = [ensure_scope(admin, name, args.mcp_url) for name in TOOL_SCOPES]
    ensure_role(admin)
    ensure_client(admin, args.client_id, scope_ids, args.direct_grant)
    print(f"Issuer for mcp setup --oauthIssuer: {args.server.rstrip('/')}/realms/{args.realm}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
