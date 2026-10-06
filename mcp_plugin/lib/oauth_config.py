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

"""The OAuth2 configuration of a multi-tenant MCP server.

A multi-tenant server (see :mod:`mcp_plugin.lib.tenants`) always accepts its
users' API keys. On top of that it can take OAuth2 access tokens, in one of two
modes, set with ``mcp setup --oauthMode``:

* ``keycloak``: Keycloak (or another OpenID Connect provider working the same
  way) is the authorization server. This server only checks the tokens it
  issued (see :mod:`mcp_plugin.lib.oauth_keycloak`).
* ``builtin``: this server IS the authorization server, and signs users in with
  their MariaDB account (see :mod:`mcp_plugin.lib.oauth_builtin`).

What this module keeps:

* In ``settings.json``: the server's **public URL** - the canonical resource
  identifier tokens are issued for, which cannot be derived from the bind
  address behind a proxy - and the ``oauth`` section with the mode and its
  settings.
* In ``oauth_clients.json``: the OAuth clients of the built-in authorization
  server, registered by an administrator or dynamically. Nothing secret.
* In the shell's ``generic`` secret group: a confidential client's secret, the
  key the built-in server signs its access tokens with, and the secret of the
  client Keycloak introspection uses. The client secrets are stored as they are,
  so that ``mcp setup --showOAuthClientSecret`` can show one again - the
  counterpart of Snowflake's ``SYSTEM$SHOW_OAUTH_CLIENT_SECRETS``.

Shell plugin code: errors are raised as ``mysqlsh.Error``.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak urlsafe cimd

import copy
import datetime
import ipaddress
import json
import os
import secrets
import uuid
from typing import Optional
from urllib.parse import urlsplit

import mysqlsh

from mcp_plugin.lib import config, general, tenants

# The OAuth modes.
OAUTH_MODE_NONE = "none"
OAUTH_MODE_KEYCLOAK = "keycloak"
OAUTH_MODE_BUILTIN = "builtin"
OAUTH_MODES = (OAUTH_MODE_NONE, OAUTH_MODE_KEYCLOAK, OAUTH_MODE_BUILTIN)

# Where the built-in authorization server keeps what a sign-in produced.
LOGIN_CONNECTION_STORE_SECRET_STORE = "secret-store"
LOGIN_CONNECTION_STORE_MEMORY = "memory"
LOGIN_CONNECTION_STORES = (
    LOGIN_CONNECTION_STORE_SECRET_STORE,
    LOGIN_CONNECTION_STORE_MEMORY,
)

# How a Keycloak token is checked: its signature alone, or by asking Keycloak.
VERIFICATION_JWT = "jwt"
VERIFICATION_INTROSPECTION = "introspection"
VERIFICATIONS = (VERIFICATION_JWT, VERIFICATION_INTROSPECTION)

# 90 days: the default validity of a Snowflake OAuth refresh token
# (OAUTH_REFRESH_TOKEN_VALIDITY), which is what a setup like the one Arcade
# documents for Snowflake expects.
DEFAULT_GRANT_MAX_LIFETIME = 90 * 24 * 3600

# The settings of each mode, with their defaults.
_DEFAULTS = {
    "mode": OAUTH_MODE_NONE,
    "keycloak": {
        "issuer": "",
        "verification": VERIFICATION_JWT,
        "introspectionClientId": "",
        "clientIds": [],
        "linkByVerifiedEmail": True,
        "autoProvision": {
            "enabled": True,
            "requiredRealmRole": "mcp-user",
            "defaultScopes": list(tenants.DEFAULT_SCOPES),
        },
    },
    "builtin": {
        "loginServers": [],
        "requiredRole": "",
        "autoProvision": {
            "enabled": True,
            "defaultScopes": list(tenants.DEFAULT_SCOPES),
        },
        "accessTokenLifetime": 3600,
        "grantMaxLifetime": DEFAULT_GRANT_MAX_LIFETIME,
        "grantIdleTimeout": None,
        "refreshGracePeriod": 30,
        "loginConnectionStore": LOGIN_CONNECTION_STORE_SECRET_STORE,
        "allowedClientNetworks": [],
        "dynamicClientRegistration": True,
        "cimd": True,
    },
}

# The keys in settings.json.
_PUBLIC_URL_SETTING = "publicUrl"
_OAUTH_SETTING = "oauth"

# Name of the file inside the plugin data directory that holds the clients.
CLIENTS_FILE_NAME = "oauth_clients.json"
_CLIENTS_FILE_VERSION = 1

# The generic-group secrets.
_CLIENT_SECRET_PREFIX = "MCP:OAUTH:CLIENT:"
SIGNING_KEY_SECRET = "MCP:OAUTH:SIGNING_KEY"
INTROSPECTION_SECRET = "MCP:OAUTH:INTROSPECTION_SECRET"

# How a client came to be registered.
CLIENT_REGISTERED_BY_ADMIN = "admin"
CLIENT_REGISTERED_DYNAMICALLY = "dynamic"


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def _now() -> str:
    """Returns the current time as an ISO 8601 UTC timestamp."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --- The public URL ---------------------------------------------------------------


def normalize_public_url(url) -> str:
    """Returns a public URL in its canonical form, checked.

    Args:
        url (str): The URL the MCP endpoint is reached at by clients, e.g.
            ``https://mcp.example.com/mcp``.

    Returns:
        The URL with a lower-case scheme and host and no trailing slash - the
        form the MCP specification asks resource identifiers to be compared in.

    Raises:
        mysqlsh.Error: If it is not an absolute http(s) URL, has a query or a
            fragment, or is plain http anywhere but on loopback.
    """
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise mysqlsh.Error(f"'{text}' is not an absolute http or https URL.")
    if parts.query or parts.fragment:
        raise mysqlsh.Error(f"The public URL '{text}' must have no query or fragment.")
    if parts.scheme.lower() == "http" and not general.is_loopback_host(parts.hostname):
        raise mysqlsh.Error(
            f"The public URL '{text}' must be https: tokens are sent to it. Plain "
            "http is only accepted on loopback, for testing."
        )

    netloc = parts.hostname.lower()
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"

    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}"


def get_public_url() -> str:
    """Returns the configured public URL, or ``""`` if there is none."""
    return str(config.get_settings().get(_PUBLIC_URL_SETTING, "") or "")


def set_public_url(url) -> str:
    """Sets the public URL (see :func:`normalize_public_url`); ``""`` clears it."""
    value = normalize_public_url(url) if str(url or "").strip() else ""
    settings = config.get_settings()
    settings[_PUBLIC_URL_SETTING] = value
    config.save_settings(settings)

    return value


def public_url_origin(url) -> str:
    """Returns the scheme and authority of a URL: the built-in issuer."""
    parts = urlsplit(url)

    return f"{parts.scheme}://{parts.netloc}"


def public_url_host(url) -> str:
    """Returns the Host header value clients send for a URL."""
    return urlsplit(url).netloc


# --- The oauth settings -----------------------------------------------------------


def _merged(defaults, stored):
    """Returns the defaults with what is stored laid over them, recursively."""
    if not isinstance(defaults, dict) or not isinstance(stored, dict):
        return copy.deepcopy(stored if stored is not None else defaults)

    merged = copy.deepcopy(defaults)
    for key, value in stored.items():
        merged[key] = _merged(defaults.get(key), value) if key in defaults else value

    return merged


def get_oauth_settings() -> dict:
    """Returns the oauth settings, every value filled in with its default."""
    return _merged(_DEFAULTS, config.get_settings().get(_OAUTH_SETTING) or {})


def get_mode() -> str:
    """Returns the OAuth mode."""
    mode = get_oauth_settings().get("mode")

    return mode if mode in OAUTH_MODES else OAUTH_MODE_NONE


def update_oauth_settings(change) -> dict:
    """Applies a change to the oauth settings and persists them.

    Args:
        change: A function taking the settings dict (defaults filled in) and
            changing it in place.

    Returns:
        The settings as they now are.
    """
    settings = config.get_settings()
    oauth = get_oauth_settings()
    change(oauth)
    settings[_OAUTH_SETTING] = oauth
    config.save_settings(settings)

    return oauth


def set_mode(mode) -> None:
    """Sets the OAuth mode.

    Raises:
        mysqlsh.Error: If the mode is unknown.
    """
    mode = str(mode or "").strip().lower()
    if mode not in OAUTH_MODES:
        raise mysqlsh.Error(
            f"'{mode}' is not an OAuth mode. Use one of: {', '.join(OAUTH_MODES)}."
        )

    def change(oauth):
        oauth["mode"] = mode

    update_oauth_settings(change)


def check_ready(mode=None) -> None:
    """Refuses to serve an OAuth mode that is not configured enough to work.

    Args:
        mode: The mode to check, or None for the configured one.

    Raises:
        mysqlsh.Error: If something the mode needs is missing.
    """
    mode = mode or get_mode()
    if mode == OAUTH_MODE_NONE:
        return

    if not get_public_url():
        raise mysqlsh.Error(
            f"OAuth mode '{mode}' needs the server's public URL, which tokens are "
            "issued for. Set it with mcp setup --publicUrl=https://<host>/mcp."
        )

    oauth = get_oauth_settings()
    if mode == OAUTH_MODE_KEYCLOAK and not oauth["keycloak"]["issuer"]:
        raise mysqlsh.Error(
            "OAuth mode 'keycloak' needs the realm's issuer URL. Set it with mcp "
            "setup --oauthIssuer=https://<keycloak>/realms/<realm>."
        )
    if (
        mode == OAUTH_MODE_KEYCLOAK
        and oauth["keycloak"]["verification"] == VERIFICATION_INTROSPECTION
        and not oauth["keycloak"]["introspectionClientId"]
    ):
        raise mysqlsh.Error(
            "Keycloak introspection needs a client to introspect with. Set it "
            "with mcp setup --oauthIntrospectionClientId and its secret with "
            "--oauthIntrospectionSecretEnv."
        )
    if mode == OAUTH_MODE_BUILTIN and not oauth["builtin"]["loginServers"]:
        raise mysqlsh.Error(
            "OAuth mode 'builtin' signs users in against a MariaDB server, and "
            "none is configured. Add one with mcp setup --addLoginServer."
        )


def normalize_networks(networks) -> list:
    """Returns a list of CIDR networks, checked.

    Raises:
        mysqlsh.Error: If one is not a network.
    """
    if isinstance(networks, str):
        networks = [item.strip() for item in networks.split(",")]

    normalized = []
    for network in networks or []:
        network = str(network).strip()
        if not network:
            continue
        try:
            normalized.append(str(ipaddress.ip_network(network, strict=False)))
        except ValueError:
            raise mysqlsh.Error(f"'{network}' is not a network (e.g. 192.0.2.0/24).")

    return normalized


def address_allowed(address, networks) -> bool:
    """Returns whether a peer address is in one of the given networks.

    An empty list allows everyone. A loopback peer arrives normalized to
    :data:`mcp_plugin.lib.general.LOOPBACK_ADDRESS` and matches a loopback
    network.
    """
    if not networks:
        return True
    if not address:
        return False

    if address == general.LOOPBACK_ADDRESS:
        candidates = [ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")]
    else:
        try:
            candidates = [ipaddress.ip_address(address)]
        except ValueError:
            return False

    for network in networks:
        parsed = ipaddress.ip_network(network, strict=False)
        if any(candidate in parsed for candidate in candidates if candidate.version == parsed.version):
            return True

    return False


def normalize_login_server(uri) -> str:
    """Returns a login server URI: a connection URI with no user or password.

    Raises:
        mysqlsh.Error: If it is not a connection URI, or names a user.
    """
    parsed = config.parse_connection_uri(uri)
    if parsed is None:
        raise mysqlsh.Error(f"'{uri}' is not a valid connection URI.")
    if parsed.get("user") or parsed.get("password"):
        raise mysqlsh.Error(
            f"The login server '{uri}' names a user: users sign in with their "
            "own account, so give the server only (mariadb://host:3306)."
        )

    normalized = config.normalize_connection_uri(uri)
    if normalized is None:
        raise mysqlsh.Error(f"'{uri}' is not a valid connection URI.")

    return normalized


# --- Clients ------------------------------------------------------------------------


def get_clients_file_path() -> str:
    """Returns the full path of the oauth_clients.json file."""
    return os.path.join(general.get_plugin_data_path(), CLIENTS_FILE_NAME)


def read_clients() -> dict:
    """Returns every registered client, by client id.

    Raises:
        mysqlsh.Error: If the file cannot be read.
    """
    path = get_clients_file_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as clients_file:
            content = json.load(clients_file)
    except (OSError, ValueError) as error:
        raise mysqlsh.Error(f"Could not read the OAuth clients file '{path}': {error}")

    clients = content.get("clients") if isinstance(content, dict) else None

    return {
        str(client_id): record
        for client_id, record in (clients or {}).items()
        if isinstance(record, dict)
    }


def _write_clients(clients: dict) -> None:
    """Persists every client, replacing the file whole and atomically."""
    path = get_clients_file_path()
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as clients_file:
        json.dump(
            {"version": _CLIENTS_FILE_VERSION, "clients": dict(sorted(clients.items()))},
            clients_file,
            indent=4,
        )
    os.replace(temporary, path)


def _change_clients(change):
    """Applies a change to the clients and persists them, under the file lock.

    ``mcp setup`` and a running server both change the clients - the server
    registers dynamic clients and records their use - and without the lock one
    could write back what it read before the other's change: a removed client
    would come back.
    """
    with config.file_lock(get_clients_file_path()):
        clients = read_clients()
        result = change(clients)
        _write_clients(clients)

    return result


def normalize_redirect_uris(uris) -> list:
    """Returns a list of redirect URIs, checked.

    Raises:
        mysqlsh.Error: If one is not an absolute URL, or carries a fragment.
    """
    if isinstance(uris, str):
        uris = [item.strip() for item in uris.split(",")]

    normalized = []
    for uri in uris or []:
        uri = str(uri).strip()
        if not uri:
            continue
        parts = urlsplit(uri)
        if not parts.scheme or not parts.netloc or parts.fragment:
            raise mysqlsh.Error(f"'{uri}' is not a valid redirect URI.")
        normalized.append(uri)

    return normalized


def client_secret_key(client_id) -> str:
    """Returns the generic-group secret key a client's secret is stored under."""
    return f"{_CLIENT_SECRET_PREFIX}{client_id}"


def add_client(
    name,
    confidential=False,
    redirect_uris=(),
    registered=CLIENT_REGISTERED_BY_ADMIN,
    client_id=None,
    client_secret=None,
    metadata=None,
) -> tuple:
    """Registers a client.

    Args:
        name (str): A name to show for it.
        confidential (bool): Whether it authenticates with a secret.
        redirect_uris: Where it may be redirected after sign-in. May be left
            empty for now: Arcade, for one, only shows its redirect URI after
            the client is set up (see :func:`set_redirect_uris`).
        registered (str): Who registered it.
        client_id (str): The id to give it, or None for a new one.
        client_secret (str): The secret to give a confidential client, or None
            for a new one.
        metadata (dict): Further registration metadata to keep.

    Returns:
        A ``(client_id, client_secret)`` tuple; the secret is None for a
        public client.
    """
    client_id = client_id or str(uuid.uuid4())
    uris = normalize_redirect_uris(redirect_uris)
    secret = None
    if confidential:
        secret = client_secret or secrets.token_urlsafe(32)
        _shell().store_secret(client_secret_key(client_id), secret)

    def change(clients):
        if client_id in clients:
            raise mysqlsh.Error(f"There is already an OAuth client '{client_id}'.")
        record = {
            "name": str(name or "").strip(),
            "confidential": bool(confidential),
            "redirectUris": uris,
            "allowedRoles": [],
            "registered": registered,
            "created": _now(),
        }
        if metadata:
            record["metadata"] = metadata
        clients[client_id] = record

    _change_clients(change)

    return client_id, secret


def resolve_client(identifier) -> str:
    """Returns the client an identifier names: its id, or its unique name.

    Raises:
        mysqlsh.Error: If no client, or more than one, is named.
    """
    clients = read_clients()
    if identifier in clients:
        return identifier

    named = [client_id for client_id, record in clients.items() if record.get("name") == identifier]
    if len(named) == 1:
        return named[0]
    if not named:
        raise mysqlsh.Error(
            f"'{identifier}' is not a registered OAuth client. Use "
            "--listOAuthClients to list them."
        )

    raise mysqlsh.Error(
        f"More than one OAuth client is named '{identifier}': give its id "
        f"({', '.join(sorted(named))})."
    )


def _change_client(client_id, change) -> dict:
    """Applies a change to one client's record and persists it."""

    def apply(clients):
        record = clients.get(client_id)
        if record is None:
            raise mysqlsh.Error(f"There is no OAuth client '{client_id}'.")
        change(record)

        return record

    return _change_clients(apply)


def set_redirect_uris(client_id, uris) -> list:
    """Sets the redirect URIs a client may use."""
    normalized = normalize_redirect_uris(uris)

    def change(record):
        record["redirectUris"] = normalized

    _change_client(client_id, change)

    return normalized


def set_allowed_roles(client_id, roles) -> list:
    """Sets the MariaDB roles a session signed in through a client may run as.

    The counterpart of Snowflake's ``ALLOWED_ROLES_LIST``: a sign-in through
    the client is refused unless the role the session would run under is one
    of these. Empty allows any.
    """
    if isinstance(roles, str):
        roles = [item.strip() for item in roles.split(",")]
    normalized = [str(role).strip() for role in roles or [] if str(role).strip()]

    def change(record):
        record["allowedRoles"] = normalized

    _change_client(client_id, change)

    return normalized


def get_client_secret(client_id) -> Optional[str]:
    """Returns a confidential client's secret, or None."""
    try:
        return _shell().read_secret(client_secret_key(client_id))
    except Exception:  # noqa: BLE001 - no secret is the shell's missing-secret error
        return None


def rotate_client_secret(client_id) -> str:
    """Gives a confidential client a new secret; the old one stops working.

    Raises:
        mysqlsh.Error: If the client is public.
    """
    record = read_clients().get(client_id)
    if record is None:
        raise mysqlsh.Error(f"There is no OAuth client '{client_id}'.")
    if not record.get("confidential"):
        raise mysqlsh.Error(f"The OAuth client '{client_id}' is public and has no secret.")

    secret = secrets.token_urlsafe(32)
    _shell().store_secret(client_secret_key(client_id), secret)

    return secret


def remove_client(client_id) -> None:
    """Removes a client and its secret.

    The grants issued to it are ended by the caller (see
    :func:`mcp_plugin.lib.oauth_builtin.end_grants_of_client`), which knows
    where grants are kept.
    """

    def change(clients):
        if clients.pop(client_id, None) is None:
            raise mysqlsh.Error(f"There is no OAuth client '{client_id}'.")

    _change_clients(change)
    try:
        _shell().delete_secret(client_secret_key(client_id))
    except Exception:  # noqa: BLE001 - a public client has none
        pass


def record_client_use(used: dict) -> None:
    """Records when clients were last used, so unused dynamic ones can expire.

    Called by the built-in server's sweeper with what it collected since its
    last pass, so the file is written at most once a pass rather than on every
    token issued. A client removed in the meantime stays removed.

    Args:
        used (dict): Client id to the ISO 8601 time it was last used.
    """
    if not used:
        return

    def change(clients):
        for client_id, stamp in used.items():
            record = clients.get(client_id)
            if record is not None:
                record["lastUsed"] = stamp

    try:
        _change_clients(change)
    except Exception as error:  # noqa: BLE001 - bookkeeping must not fail a sweep
        general.log_event(f"oauth: could not record the use of clients: {error}")


def now() -> str:
    """Returns the current time as it is recorded in the files."""
    return _now()


def expire_unused_dynamic_clients(max_age_seconds: float) -> list:
    """Removes the dynamically registered clients unused for too long.

    Returns:
        The ids of the clients removed.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    def last_activity(record):
        stamp = record.get("lastUsed") or record.get("created") or ""
        try:
            return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return now

    stale = [
        client_id
        for client_id, record in read_clients().items()
        if record.get("registered") == CLIENT_REGISTERED_DYNAMICALLY
        and (now - last_activity(record)).total_seconds() > max_age_seconds
    ]
    for client_id in stale:
        remove_client(client_id)

    return stale


# --- Keys ---------------------------------------------------------------------------


def _generate_signing_key() -> dict:
    """Returns a new ES256 signing key, as it is stored."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")

    return {"kid": secrets.token_hex(8), "pem": pem}


def get_signing_key() -> dict:
    """Returns the key the built-in server signs access tokens with.

    Generated and stored the first time it is needed.

    Returns:
        A dict with ``kid`` and the private key as ``pem``.
    """
    try:
        return json.loads(_shell().read_secret(SIGNING_KEY_SECRET))
    except Exception:  # noqa: BLE001 - none yet, or unreadable: make one
        key = _generate_signing_key()
        _shell().store_secret(SIGNING_KEY_SECRET, json.dumps(key))

        return key


def rotate_signing_key(keep_previous_for=None) -> str:
    """Replaces the signing key, keeping the previous one to verify with for a while.

    New access tokens are signed with the new key at once. The previous key is
    kept beside it, for VERIFYING only, for ``keep_previous_for`` seconds -
    by default one access token lifetime - so the tokens issued just before the
    rotation stay valid until they expire instead of every client being sent
    back to sign in. A key that was ever kept that way and is older is dropped.
    For a key that may have leaked, follow with
    :func:`drop_previous_signing_key`, which ends the overlap at once.

    A running server notices the rotation within a minute (its sweeper reloads
    the keys), or at once when it is shown a token signed with a key it does not
    know.

    Args:
        keep_previous_for (int): Seconds the previous key stays valid for
            verification, or None for the configured access token lifetime.

    Returns:
        The new key id.
    """
    if keep_previous_for is None:
        keep_previous_for = int(get_oauth_settings()["builtin"]["accessTokenLifetime"])

    current = get_signing_key()
    key = _generate_signing_key()
    if keep_previous_for > 0:
        key["previous"] = {
            "kid": current["kid"],
            "pem": current["pem"],
            "validUntil": int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            + int(keep_previous_for),
        }
    _shell().store_secret(SIGNING_KEY_SECRET, json.dumps(key))

    return key["kid"]


def drop_previous_signing_key() -> bool:
    """Stops accepting tokens signed with the key before the current one.

    For a signing key that may have leaked: rotate, then drop the previous key,
    and every token it signed is refused from the next check on.

    Returns:
        True if there was a previous key to drop.
    """
    key = get_signing_key()
    if "previous" not in key:
        return False

    del key["previous"]
    _shell().store_secret(SIGNING_KEY_SECRET, json.dumps(key))

    return True


def set_introspection_secret(secret) -> None:
    """Stores the secret of the client Keycloak introspection authenticates as."""
    _shell().store_secret(INTROSPECTION_SECRET, str(secret))


def get_introspection_secret() -> Optional[str]:
    """Returns the introspection client's secret, or None."""
    try:
        return _shell().read_secret(INTROSPECTION_SECRET)
    except Exception:  # noqa: BLE001 - not configured
        return None
