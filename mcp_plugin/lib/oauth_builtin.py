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

"""The MCP server as its own OAuth2 authorization server, signing in to MariaDB.

In OAuth mode ``builtin`` (see :mod:`mcp_plugin.lib.oauth_config`) this server
does what Snowflake's MCP server does: the database account IS the identity. A
client sends the user's browser here; the user signs in with their MariaDB user
name and password on this server's own page (``/login``); the account is checked
by opening a session with it; and the client gets an access token for the user
that account belongs to. The session the tools then open runs as that account,
under its default role, so what the user can do is what MariaDB's own grants
let the account do.

The MCP SDK provides the protocol endpoints - authorization server metadata,
``/authorize``, ``/token``, ``/register``, ``/revoke``, PKCE - and calls the
:class:`BuiltinAuthProvider` here for everything that is a decision. On top it
adds:

* **Grants.** A successful sign-in and consent creates a grant: one user's
  authorization of one client. It holds the client, the scopes, the MariaDB
  account, and the refresh token, which is rotated on every use - a refresh
  token presented a second time ends the whole grant, as OAuth 2.1 asks, after a
  short grace period for a client that sent the same refresh twice in a race.
  A grant ends at the first of its maximum lifetime (90 days by default),
  an idle timeout (off by default), a revocation, the user being disabled or
  removed, or its client being removed.
* **The login connection.** The account and password a user signed in with
  become a connection of the grant, so ``db.connect`` works with no
  administrator step. It is kept in the user's secret group
  (``MCP:OAUTH:CONN:<grant>``) - or only in memory, as configured - for exactly
  as long as the grant lives, and deleted with it. It is keyed by grant, so it
  never touches a connection an administrator stored for the same account. The
  connection reaper treats it like any other connection; reopening one checks
  that its grant is still alive.
* **Access tokens** are JWTs this server signs (ES256, the key in the generic
  secret group), for this server's public URL alone, carrying the user, the
  grant and the user's token epoch - raising which revokes every token at once.
* **Clients** are registered by an administrator (``mcp setup-oauth
  --addClient``), dynamically, or - with a Client ID Metadata Document - by
  naming an https URL as the client id, which is fetched with a guard against
  being pointed at internal addresses.

The sign-in page is a password oracle against the database, so it is rate
limited per address and per account, answers every failure the same way, and
is protected against cross-site requests and framing.
"""

# cSpell:ignore mysqlsh MariaDB anyio starlette cimd urlsafe nosniff jwks httpx

import functools
import hashlib
import hmac
import html
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import uuid
from typing import Optional
from urllib.parse import urlsplit

from mcp_plugin.lib import auth, config, general, oauth_config, tenants

# How a user authenticated, as general.Principal.auth_method reports it.
AUTH_METHOD_BUILTIN = "mariadb"

# The secrets of a grant, in the user's group.
GRANT_SECRET_PREFIX = "MCP:OAUTH:GRANT:"
LOGIN_CONNECTION_SECRET_PREFIX = "MCP:OAUTH:CONN:"

# What every refresh token starts with.
REFRESH_TOKEN_PREFIX = "mdbrt_"

# How long a pending sign-in, and an authorization code, stay usable.
PENDING_LIFETIME = 600
CODE_LIFETIME = 60

# The most pending sign-ins, and cached Client ID Metadata Documents, kept at
# once. Both are created by requests nobody has authenticated yet, so without
# a bound a stream of them would grow the server's memory; past it the oldest
# are dropped.
_MAX_PENDING = 10000
_MAX_CIMD_CACHE = 1000

# How long a grant record read from the secret store is trusted, in seconds.
# What another process changes - mcp setup removing a client or revoking a
# user's tokens - is caught at once by other checks; this bounds the rest.
_GRANT_CACHE_TTL = 30

# Sign-in failures: how many per account, and per address, within the window
# before further attempts are refused until the window has passed.
_LOGIN_WINDOW = 900
_LOGIN_MAX_FAILURES_PER_ACCOUNT = 5
_LOGIN_MAX_FAILURES_PER_ADDRESS = 30

# The least time between two reloads of the signing keys for a token naming an
# unknown key id, in seconds.
_KEY_RELOAD_MIN_INTERVAL = 10

# How often the sweeper ends expired grants and forgets unused clients.
_SWEEP_INTERVAL = 60

# Dynamically registered clients unused for this long are removed.
_DYNAMIC_CLIENT_MAX_IDLE = 30 * 24 * 3600

# The most dynamically registered clients there may be at once.
_MAX_DYNAMIC_CLIENTS = 1000

# Client ID Metadata Documents: size limit, timeout and cache bounds.
_CIMD_MAX_BYTES = 5000
_CIMD_TIMEOUT = 5
_CIMD_MIN_CACHE = 300
_CIMD_MAX_CACHE = 86400

# The provider of the server being served, which login_credentials asks.
_provider = None
_provider_lock = threading.Lock()


def _digest(token: str) -> str:
    """Returns the SHA-256 hex digest a token is stored as."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Grants -----------------------------------------------------------------------


class GrantStore:
    """Where grants and their login connections are kept.

    In the user's secret group (``"secret-store"``), where they survive a
    restart, or in this process only (``"memory"``), where a restart ends them.
    Thread-safe.
    """

    def __init__(self, mode: str):
        self.mode = mode
        self._lock = threading.RLock()
        self._grants = {}
        self._connections = {}
        self._cache = {}

    @property
    def in_memory(self) -> bool:
        return self.mode == oauth_config.LOGIN_CONNECTION_STORE_MEMORY

    def create(self, record: dict, uri: str, password: str) -> None:
        """Stores a new grant and its login connection."""
        user, grant_id = record["user"], record["id"]
        if self.in_memory:
            with self._lock:
                self._grants[grant_id] = dict(record)
                self._connections[grant_id] = (uri, password)
            return

        options = config.secret_options(user)
        config._shell().store_secret(
            LOGIN_CONNECTION_SECRET_PREFIX + grant_id,
            json.dumps({"uri": uri, "password": password}),
            *options,
        )
        self.update(record)

    def update(self, record: dict) -> None:
        """Stores a changed grant record."""
        if self.in_memory:
            with self._lock:
                self._grants[record["id"]] = dict(record)
            return

        config._shell().store_secret(
            GRANT_SECRET_PREFIX + record["id"],
            json.dumps(record),
            *config.secret_options(record["user"]),
        )
        with self._lock:
            self._cache[record["id"]] = (dict(record), time.monotonic())

    def get(self, user: str, grant_id: str, fresh: bool = False) -> Optional[dict]:
        """Returns a grant record, or None if there is no such grant."""
        if self.in_memory:
            with self._lock:
                record = self._grants.get(grant_id)
            return dict(record) if record and record.get("user") == user else None

        with self._lock:
            cached = self._cache.get(grant_id)
        if not fresh and cached is not None and time.monotonic() - cached[1] < _GRANT_CACHE_TTL:
            record = cached[0]
        else:
            try:
                record = json.loads(
                    config._shell().read_secret(
                        GRANT_SECRET_PREFIX + grant_id, *config.secret_options(user)
                    )
                )
            except Exception:  # noqa: BLE001 - no such grant
                record = None
            with self._lock:
                if record is None:
                    self._cache.pop(grant_id, None)
                else:
                    self._cache[grant_id] = (record, time.monotonic())

        if record is None or record.get("user") != user:
            return None

        return dict(record)

    def credentials(self, user: str, grant_id: str) -> Optional[tuple]:
        """Returns a grant's login connection as ``(uri, password)``, or None."""
        if self.in_memory:
            with self._lock:
                record = self._grants.get(grant_id)
                connection = self._connections.get(grant_id)
            if record is None or record.get("user") != user:
                return None
            return connection

        if self.get(user, grant_id) is None:
            return None
        try:
            value = json.loads(
                config._shell().read_secret(
                    LOGIN_CONNECTION_SECRET_PREFIX + grant_id,
                    *config.secret_options(user),
                )
            )
        except Exception:  # noqa: BLE001 - gone with the grant
            return None

        return (value["uri"], value["password"])

    def delete(self, user: str, grant_id: str) -> None:
        """Deletes a grant and its login connection."""
        with self._lock:
            self._grants.pop(grant_id, None)
            self._connections.pop(grant_id, None)
            self._cache.pop(grant_id, None)

        if self.in_memory:
            return

        delete_grant_secrets(user, grant_id)

    def all(self) -> list:
        """Returns every grant record."""
        if self.in_memory:
            with self._lock:
                return [dict(record) for record in self._grants.values()]

        return [record for _, record in stored_grants()]


def delete_grant_secrets(user: str, grant_id: str) -> None:
    """Deletes the secrets of one grant from a user's group, as far as they exist."""
    for prefix in (GRANT_SECRET_PREFIX, LOGIN_CONNECTION_SECRET_PREFIX):
        try:
            config._shell().delete_secret(prefix + grant_id, *config.secret_options(user))
        except Exception:  # noqa: BLE001 - already gone
            pass


def stored_grants() -> list:
    """Returns every grant kept in the secret store, as ``(user, record)``.

    Works without a running server, which is how ``mcp setup`` ends the grants
    of a client it removes or a user whose tokens it revokes.
    """
    found = []
    for group, keys in tenants.list_groups().items():
        for key in keys:
            if not key.startswith(GRANT_SECRET_PREFIX):
                continue
            try:
                record = json.loads(
                    config._shell().read_secret(key, *config.secret_options(group))
                )
            except Exception:  # noqa: BLE001 - unreadable: skip it
                continue
            found.append((group, record))

    return found


def _end_stored_grants(matches) -> int:
    """Ends every stored grant ``matches(user, record)`` picks; returns how many."""
    ended = 0
    for user, record in stored_grants():
        if matches(user, record):
            delete_grant_secrets(user, record["id"])
            ended += 1

    return ended


def end_grants_of_client(client_id: str) -> int:
    """Ends every stored grant of a client; for ``mcp setup`` removing it."""
    return _end_stored_grants(lambda user, record: record.get("client") == client_id)


def end_grants_of_user(user: str) -> int:
    """Ends every stored grant of a user; for ``mcp setup --revokeTokens``."""
    return _end_stored_grants(lambda owner, record: owner == user)


def _active_provider():
    """Returns the provider of the server being served, or None."""
    with _provider_lock:
        return _provider


def login_connection_uri(mcp_user_id, grant_id) -> Optional[str]:
    """Returns the URI of the login connection of a live grant.

    What :mod:`mcp_plugin.lib.db_functions` lists and resolves against. Read
    from the grant record, which is cached, where :func:`login_credentials`
    also reads the password from the secret store.

    Returns:
        The URI, or None when there is no such live grant or no built-in
        authorization server is being served.
    """
    provider = _active_provider()
    if provider is None or not grant_id:
        return None

    return provider.live_grant_uri(mcp_user_id, grant_id)


def login_credentials(mcp_user_id, grant_id) -> Optional[tuple]:
    """Returns the login connection of a live grant as ``(uri, password)``.

    What :mod:`mcp_plugin.lib.db_functions` opens a login connection with,
    asked of the provider of the server being served.

    Returns:
        The URI and password, or None when there is no such live grant or no
        built-in authorization server is being served.
    """
    provider = _active_provider()
    if provider is None or not grant_id:
        return None

    return provider.live_credentials(mcp_user_id, grant_id)


# --- Clients -------------------------------------------------------------------------


def _is_loopback_redirect(url: str) -> bool:
    """Returns whether a redirect URI is on loopback, where any port is fine."""
    parts = urlsplit(url)

    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


@functools.lru_cache(maxsize=None)
def _client_class():
    """Returns the SDK client information model, with this server's leniency.

    Built once: the SDK is imported lazily (see
    :mod:`mcp_plugin.lib.tool_registrar`), and defining a pydantic model is not
    free, so it is not done on every request to the token endpoint.
    """
    from mcp.shared.auth import OAuthClientInformationFull

    class Client(OAuthClientInformationFull):
        """A client as the SDK's handlers see it.

        Two departures from the SDK's own checks. A loopback redirect URI may
        use any port, as OAuth 2.1 allows native apps - Claude Code and VS Code
        pick a free port for each sign-in. And a scope this server does not
        define is dropped rather than refused, since clients ask for whatever
        their configuration says; no known scope means the user's own.
        """

        def validate_redirect_uri(self, redirect_uri):
            if redirect_uri is not None and _is_loopback_redirect(str(redirect_uri)):
                wanted = urlsplit(str(redirect_uri))
                for registered in self.redirect_uris or []:
                    have = urlsplit(str(registered))
                    if (
                        _is_loopback_redirect(str(registered))
                        and have.hostname == wanted.hostname
                        and have.path == wanted.path
                    ):
                        return redirect_uri

            return super().validate_redirect_uri(redirect_uri)

        def validate_scope(self, requested_scope):
            if requested_scope is None:
                return None
            known = [s for s in requested_scope.split() if s in tenants.SUPPORTED_SCOPES]

            return known or None

    return Client


def _client_from_record(client_id: str, record: dict, secret: Optional[str]):
    """Returns the SDK client for a registered client's record."""
    Client = _client_class()

    return Client(
        client_id=client_id,
        client_secret=secret if record.get("confidential") else None,
        client_name=record.get("name") or None,
        redirect_uris=record.get("redirectUris") or None,
        # A Basic header is turned into the form by
        # auth.BasicClientAuthMiddleware, so every confidential client is
        # checked the form's way, whichever it uses.
        token_endpoint_auth_method="client_secret_post" if record.get("confidential") else "none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(tenants.SUPPORTED_SCOPES),
    )


def _is_public_address(address: str) -> bool:
    """Returns whether an address is one the CIMD fetch may connect to."""
    parsed = ipaddress.ip_address(address)
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped

    return parsed.is_global and not parsed.is_multicast


def fetch_client_metadata(url: str, resolve=None, http_get=None) -> dict:
    """Fetches a Client ID Metadata Document, refusing internal addresses.

    Args:
        url (str): The client id, an https URL with a path.
        resolve: ``(host) -> [addresses]``, for tests.
        http_get: ``(url, addresses, max_bytes, timeout) -> (status, headers,
            body)``, for tests.

    Returns:
        A dict with the document as ``metadata`` and how long it may be cached
        as ``max_age``.

    Raises:
        ValueError: If the URL, the address or the document is not acceptable.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.path in ("", "/"):
        raise ValueError("a client id URL must be https with a path")

    addresses = (resolve or _resolve)(parts.hostname)
    if not addresses or not all(_is_public_address(a) for a in addresses):
        raise ValueError(f"'{parts.hostname}' resolves to an address that is not public")

    # Connected to the addresses checked above, never resolved again: a name
    # answering a public address to the check and an internal one to the
    # connect (DNS rebinding) would otherwise get the fetch past the check.
    status, headers, body = (http_get or _bounded_get)(
        url, addresses, _CIMD_MAX_BYTES, _CIMD_TIMEOUT
    )
    if status != 200:
        raise ValueError(f"fetching the document answered {status}")
    if len(body) > _CIMD_MAX_BYTES:
        raise ValueError("the document is too large")

    metadata = json.loads(body)
    if metadata.get("client_id") != url:
        raise ValueError("the document's client_id is not its URL")
    if not metadata.get("redirect_uris"):
        raise ValueError("the document names no redirect_uris")
    if metadata.get("token_endpoint_auth_method", "none") != "none":
        raise ValueError("a metadata-document client authenticates with no secret")

    max_age = _CIMD_MIN_CACHE
    for directive in str(headers.get("cache-control", "")).split(","):
        name, _, value = directive.strip().partition("=")
        if name.lower() == "max-age" and value.isdigit():
            max_age = int(value)
    max_age = max(_CIMD_MIN_CACHE, min(_CIMD_MAX_CACHE, max_age))

    return {"metadata": metadata, "max_age": max_age}


def _resolve(host: str) -> list:
    """Returns the addresses a host name resolves to, in the resolver's order.

    That order is the one to try them in (RFC 6724): on a host without IPv6
    the IPv4 addresses come first.
    """
    return list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, 443)))


def _bounded_get(url: str, addresses: list, max_bytes: int, timeout: float) -> tuple:
    """GETs a URL from one of the given addresses of its host.

    The addresses are tried in turn until one connects, all within
    ``timeout``. The host name is still what the request names (``Host``) and
    what TLS checks the certificate against (``sni_hostname``). Redirects are
    not followed, and at most ``max_bytes + 1`` bytes are read.
    """
    import httpx2

    parts = urlsplit(url)
    host_header = parts.netloc.rpartition("@")[2]
    deadline = time.monotonic() + timeout
    error = ValueError("no address to connect to")
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ip = ipaddress.ip_address(address)
        netloc = f"[{ip}]" if ip.version == 6 else str(ip)
        if parts.port is not None:
            netloc += f":{parts.port}"
        request = {
            "url": parts._replace(netloc=netloc).geturl(),
            "headers": {"Host": host_header},
            "extensions": {"sni_hostname": parts.hostname},
        }
        try:
            with httpx2.Client(timeout=remaining, follow_redirects=False) as client, client.stream(
                "GET", **request
            ) as response:
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        break

                return response.status_code, response.headers, body
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as connect_error:
            # This address is unreachable from here (an IPv6 one on a host
            # without IPv6, say); the next one may not be.
            error = connect_error

    raise error


# --- The provider -------------------------------------------------------------------


class SignInError(Exception):
    """A sign-in that is refused, with what the user is told."""


class BuiltinAuthProvider:
    """The SDK's authorization server provider for the built-in server."""

    def __init__(self, settings: dict, public_url: str, api_keys, open_session=None,
                 cimd_fetch=None):
        """Creates the provider.

        Args:
            settings (dict): The ``builtin`` section of the oauth settings.
            public_url (str): The server's public URL.
            api_keys: The :class:`mcp_plugin.lib.auth.ApiKeyVerifier` API keys
                are still checked with.
            open_session: ``(connection_data) -> session``, for tests.
            cimd_fetch: :func:`fetch_client_metadata`'s replacement, for tests.
        """
        self.settings = settings
        self.public_url = public_url
        self.issuer = oauth_config.public_url_origin(public_url)
        self.api_keys = api_keys
        self.directory = api_keys.directory
        self.store = GrantStore(settings["loginConnectionStore"])
        self._open_session = open_session or (lambda data: config._shell().open_session(data))
        self._cimd_fetch = cimd_fetch or fetch_client_metadata
        self._lock = threading.RLock()
        self._pending = {}
        self._codes = {}
        self._recent_refreshes = {}
        self._cimd_cache = {}
        self._client_uses = {}
        self._clients = ({}, None)
        # Failed sign-ins, counted per account and per address.
        self._login_failures = auth.FailureCounter(_LOGIN_WINDOW)
        self._sweeper = None
        self._sweeper_stop = threading.Event()
        self._keys_loaded_at = 0.0
        self._load_signing_keys()

    # --- signing keys ---------------------------------------------------------------

    def _load_signing_keys(self) -> None:
        """Reads the signing key, and the previous one while it is still valid.

        Tokens are signed with the current key; both verify. Read again by every
        sweep and when a token names a key id not known here, so a rotation
        with ``mcp setup-oauth`` in another process reaches a running server.
        """
        key = oauth_config.get_signing_key()
        verifying = {key["kid"]: (_public_key_of(key["pem"]), None)}
        previous = key.get("previous")
        if previous and int(previous.get("validUntil", 0)) > time.time():
            verifying[previous["kid"]] = (
                _public_key_of(previous["pem"]),
                int(previous["validUntil"]),
            )

        with self._lock:
            self._kid = key["kid"]
            self._private_key = key["pem"]
            self._verifying_keys = verifying
            self._keys_loaded_at = time.monotonic()

    def _verifying_key(self, kid):
        """Returns the public key a token's key id names, if it may still verify."""
        with self._lock:
            entry = self._verifying_keys.get(kid)
            stale = time.monotonic() - self._keys_loaded_at > _KEY_RELOAD_MIN_INTERVAL
        if entry is None and stale:
            # A key rotated in another process: reload, but not for every
            # token with a made-up kid.
            self._load_signing_keys()
            with self._lock:
                entry = self._verifying_keys.get(kid)
        if entry is None:
            return None

        public_key, valid_until = entry
        if valid_until is not None and valid_until <= time.time():
            return None

        return public_key

    # --- lifecycle ------------------------------------------------------------------

    def activate(self) -> None:
        """Makes this the provider login connections are looked up in."""
        global _provider

        with _provider_lock:
            _provider = self

    def deactivate(self) -> None:
        """Stops being the provider login connections are looked up in."""
        global _provider

        with _provider_lock:
            if _provider is self:
                _provider = None

    def start_sweeper(self) -> None:
        """Starts the thread that ends expired grants."""
        if self._sweeper is not None:
            return
        self._sweeper_stop.clear()
        self._sweeper = threading.Thread(
            target=self._sweep_loop, name="mcp-oauth-grant-sweeper", daemon=True
        )
        self._sweeper.start()

    def stop_sweeper(self) -> None:
        """Stops the sweeper thread."""
        sweeper, self._sweeper = self._sweeper, None
        if sweeper is None:
            return
        self._sweeper_stop.set()
        sweeper.join(timeout=_SWEEP_INTERVAL)

    def _sweep_loop(self) -> None:
        self.sweep()
        while not self._sweeper_stop.wait(_SWEEP_INTERVAL):
            self.sweep()

    def sweep(self) -> int:
        """Ends every grant that has expired or lost its user or client.

        Returns:
            How many grants were ended.
        """
        ended = 0
        try:
            self._load_signing_keys()
            now = int(time.time())
            clients = oauth_config.read_clients()
            for record in self.store.all():
                reason = self._grant_end_reason(record, now, clients)
                if reason:
                    self.end_grant(record["user"], record["id"], reason)
                    ended += 1
            # Collected under the lock, ended outside it: ending a grant
            # deletes secrets and closes sessions, and every token check waits
            # on this lock meanwhile.
            with self._lock:
                wall = time.time()
                never_redeemed = [
                    entry for entry in self._codes.values() if entry["expires_at"] < wall
                ]
                self._codes = {
                    code: entry
                    for code, entry in self._codes.items()
                    if entry["expires_at"] >= wall
                }
                for request_id, entry in list(self._pending.items()):
                    if entry["created"] + PENDING_LIFETIME < time.time():
                        del self._pending[request_id]
                grace = int(self.settings["refreshGracePeriod"])
                for digest, (_, issued) in list(self._recent_refreshes.items()):
                    if time.monotonic() - issued > grace:
                        del self._recent_refreshes[digest]
                used, self._client_uses = self._client_uses, {}
            for entry in never_redeemed:
                self.end_grant(entry["user"], entry["grant"], "its code was never redeemed")
                ended += 1
            oauth_config.record_client_use(used)
            oauth_config.expire_unused_dynamic_clients(_DYNAMIC_CLIENT_MAX_IDLE)
        except Exception as error:  # noqa: BLE001 - the sweeper must not die
            general.log_event(f"oauth: a grant sweep failed, the sweeper keeps running: {error}")

        return ended

    def _grant_end_reason(self, record, now, clients=None) -> str:
        """Returns why a grant is over, or ``""`` while it lives."""
        if now >= record.get("created", 0) + int(self.settings["grantMaxLifetime"]):
            return "it reached its maximum lifetime"
        idle = self.settings.get("grantIdleTimeout")
        if idle and now >= record.get("lastRefreshed", record.get("created", 0)) + int(idle):
            return "it was not used for its idle timeout"
        if clients is not None and record.get("client") not in clients and not str(
            record.get("client", "")
        ).startswith("https://"):
            return "its client was removed"
        user = self.directory.active_user(record.get("user"))
        if user is None:
            return "its user was removed or disabled"
        if int(user.get("tokenEpoch", 0)) != int(record.get("epoch", 0)):
            return "the user's tokens were revoked"

        return ""

    def end_grant(self, user, grant_id, reason) -> None:
        """Ends a grant: its secrets deleted, its open connections closed."""
        from mcp_plugin.lib import db_functions

        self.store.delete(user, grant_id)
        db_functions.drop_connections_of_grant(grant_id)
        general.log_event(
            f"oauth: ended grant {general.log_id_prefix(grant_id)} of "
            f"user={general.log_id_prefix(user)} - {reason}"
        )

    def live_grant(self, user, grant_id) -> Optional[dict]:
        """Returns a grant record if the grant is alive, ending it if it is not."""
        record = self.store.get(user, grant_id)
        if record is None:
            return None

        reason = self._grant_end_reason(record, int(time.time()), self._known_clients())
        if reason:
            self.end_grant(user, grant_id, reason)
            return None

        return record

    def _known_clients(self) -> dict:
        """Returns the registered clients, read again only when their file changed.

        Every built-in token check asks whether its grant's client still
        exists, so this is a ``stat`` per request rather than a read and a
        parse, the way :class:`mcp_plugin.lib.auth.UserDirectory` treats
        ``users.json``.
        """
        try:
            stat = os.stat(oauth_config.get_clients_file_path())
            version = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            version = None

        with self._lock:
            clients, read_version = self._clients
        if version is None or version != read_version:
            clients = oauth_config.read_clients()
            with self._lock:
                self._clients = (clients, version)

        return clients

    def live_grant_uri(self, user, grant_id) -> Optional[str]:
        """Returns the URI of a live grant's login connection, or None."""
        record = self.live_grant(user, grant_id)

        return None if record is None else record.get("uri")

    def live_credentials(self, user, grant_id) -> Optional[tuple]:
        """Returns a live grant's login connection, or None."""
        if self.live_grant(user, grant_id) is None:
            return None

        return self.store.credentials(user, grant_id)

    # --- routes ---------------------------------------------------------------------

    def routes(self) -> list:
        """Returns the routes the SDK does not serve: metadata and sign-in."""
        from mcp.server.auth.handlers.metadata import MetadataHandler
        from mcp.server.auth.routes import build_metadata, cors_middleware
        from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
        from pydantic import AnyHttpUrl
        from starlette.routing import Route

        metadata = build_metadata(
            AnyHttpUrl(self.issuer),
            None,
            ClientRegistrationOptions(enabled=bool(self.settings["dynamicClientRegistration"])),
            RevocationOptions(enabled=True),
        )
        metadata.client_id_metadata_document_supported = bool(self.settings["cimd"])
        metadata.authorization_response_iss_parameter_supported = True
        metadata.scopes_supported = list(tenants.SUPPORTED_SCOPES)
        metadata.issuer = self.issuer

        return [
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            ),
            Route("/login", endpoint=self.login_page, methods=["GET", "POST"]),
        ]

    # --- clients --------------------------------------------------------------------

    async def get_client(self, client_id: str):
        import anyio

        return await anyio.to_thread.run_sync(self.get_client_sync, client_id)

    def get_client_sync(self, client_id: str):
        """Returns a client by id: registered, or a metadata document's."""
        if client_id.startswith("https://"):
            if not self.settings["cimd"]:
                return None
            return self._metadata_client(client_id)

        record = oauth_config.read_clients().get(client_id)
        if record is None:
            return None

        secret = oauth_config.get_client_secret(client_id) if record.get("confidential") else None

        return _client_from_record(client_id, record, secret)

    def _metadata_client(self, url: str):
        """Returns the client a Client ID Metadata Document describes, cached."""
        now = time.monotonic()
        with self._lock:
            cached = self._cimd_cache.get(url)
            if cached is not None and cached[1] > now:
                return cached[0]

        try:
            fetched = self._cimd_fetch(url)
        except Exception as error:  # noqa: BLE001 - not a usable client
            general.log_event(f"oauth: REFUSED the client metadata document '{url}': {error}")
            return None

        metadata = fetched["metadata"]
        client = _client_from_record(
            url,
            {
                "name": metadata.get("client_name") or urlsplit(url).hostname,
                "redirectUris": metadata.get("redirect_uris"),
                "confidential": False,
            },
            None,
        )
        with self._lock:
            _put_bounded(self._cimd_cache, url, (client, now + fetched["max_age"]), _MAX_CIMD_CACHE)

        return client

    async def register_client(self, client_info) -> None:
        import anyio

        # File and secret-store writes: off the event loop, like every other
        # provider method's work.
        await anyio.to_thread.run_sync(self._register_client_sync, client_info)

    def _register_client_sync(self, client_info) -> None:
        from mcp.server.auth.provider import RegistrationError

        if len([
            record for record in oauth_config.read_clients().values()
            if record.get("registered") == oauth_config.CLIENT_REGISTERED_DYNAMICALLY
        ]) >= _MAX_DYNAMIC_CLIENTS:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="No more clients can be registered.",
            )

        oauth_config.add_client(
            client_info.client_name or "",
            confidential=bool(client_info.client_secret),
            redirect_uris=[str(uri) for uri in client_info.redirect_uris or []],
            registered=oauth_config.CLIENT_REGISTERED_DYNAMICALLY,
            client_id=client_info.client_id,
            client_secret=client_info.client_secret,
        )
        general.log_event(
            f"oauth: registered the client {client_info.client_id} "
            f"('{client_info.client_name or ''}') dynamically"
        )

    # --- authorization --------------------------------------------------------------

    async def authorize(self, client, params) -> str:
        from mcp.server.auth.provider import AuthorizeError

        if params.resource and params.resource.rstrip("/") != self.public_url.rstrip("/"):
            raise AuthorizeError(
                error="invalid_target",
                error_description=f"This server issues tokens for {self.public_url} only.",
            )

        request_id = secrets.token_urlsafe(32)
        entry = {
            "client_id": client.client_id,
            "client_name": client.client_name or client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "explicit": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "scopes": params.scopes or list(tenants.SUPPORTED_SCOPES),
            "state": params.state,
            "csrf": secrets.token_urlsafe(32),
            "created": time.time(),
        }
        with self._lock:
            _put_bounded(self._pending, request_id, entry, _MAX_PENDING)

        return f"{self.issuer}/login?req={request_id}"

    def _pending_request(self, request_id) -> Optional[dict]:
        with self._lock:
            entry = self._pending.get(request_id or "")
            if entry is None or entry["created"] + PENDING_LIFETIME < time.time():
                self._pending.pop(request_id or "", None)
                return None

            return dict(entry)

    async def login_page(self, request):
        """Serves the sign-in and consent page, and takes its answer."""
        from starlette.responses import HTMLResponse, RedirectResponse

        request_id = request.query_params.get("req")
        pending = self._pending_request(request_id)
        if pending is None:
            return _page(HTMLResponse, "This sign-in has expired. Start again from your client.",
                         status=400)

        if request.method == "GET":
            return self._form(HTMLResponse, request_id, pending)

        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf", "")), pending["csrf"]):
            return _page(HTMLResponse, "This sign-in could not be verified. Start again.",
                         status=400)

        address = general.normalize_client_address(getattr(request.client, "host", None))
        servers = self.settings["loginServers"]
        server = str(form.get("server") or (servers[0] if servers else ""))
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        consented = [scope for scope in form.getlist("scope") if scope in pending["scopes"]]

        import anyio

        try:
            location = await anyio.to_thread.run_sync(
                self.sign_in, pending, request_id, address, server, username, password,
                consented,
            )
        except SignInError as error:
            return self._form(HTMLResponse, request_id, pending, error=str(error),
                              username=username, status=400)

        return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})

    def _form(self, response_class, request_id, pending, error="", username="", status=200):
        """Renders the sign-in and consent form."""
        servers = self.settings["loginServers"]
        server_field = (
            f'<input type="hidden" name="server" value="{html.escape(servers[0])}">'
            if len(servers) == 1
            else '<div class="mrsLoginField"><select name="server" aria-label="Server">'
            + "".join(f'<option>{html.escape(s)}</option>' for s in servers)
            + "</select></div>"
        )
        scopes = "".join(
            f'<label><input type="checkbox" name="scope" value="{html.escape(scope)}" checked> '
            f"{html.escape(_SCOPE_DESCRIPTIONS.get(scope, scope))}</label>"
            for scope in pending["scopes"]
        )
        redirect_host = urlsplit(pending["redirect_uri"]).netloc
        error_box = (
            f'<div class="mrsLoginError" role="alert"><p>{html.escape(error)}</p></div>'
            if error else ""
        )
        body = f"""
<div class="mrsLogin">
<p>Sign in to MariaDB</p>
<div class="mrsLoginIntro"><strong>{html.escape(pending['client_name'])}</strong> wants to use
this server on your behalf, and will be sent back to
<strong>{html.escape(redirect_host)}</strong>.</div>
<form class="mrsLoginFields" method="post" action="/login?req={html.escape(request_id)}">
<input type="hidden" name="csrf" value="{html.escape(pending['csrf'])}">
<fieldset class="mrsLoginScopes"><legend>Allow it to</legend>{scopes}</fieldset>
{server_field}
<div class="mrsLoginField"><input type="text" name="username" placeholder="User Name"
 aria-label="User name" autocomplete="username" value="{html.escape(username)}" required
 {'' if username else 'autofocus'}></div>
<div class="mrsLoginField"><input type="password" name="password" placeholder="Password"
 aria-label="Password" autocomplete="current-password" required {'autofocus' if username else ''}>
<button type="submit" class="mrsLoginBtnNext" aria-label="Sign in and allow"
 title="Sign in and allow"><svg viewBox="0 0 24 24" aria-hidden="true">
<path d="M9 7.5l4.5 4.5L9 16.5"/></svg></button></div>
</form>
{error_box}
<div class="mrsLoginSeparator"></div>
</div>"""

        # The browser is sent on to the client after the form is posted, and
        # browsers apply form-action to that redirect too.
        target = urlsplit(pending["redirect_uri"])

        return _page(response_class, body, status=status, raw=True,
                     form_target=f"{target.scheme}://{target.netloc}")

    def sign_in(self, pending, request_id, address, server, username, password, consented) -> str:
        """Checks a sign-in and, if it holds, issues a code; on a worker thread.

        Returns:
            Where to send the browser: the client's redirect URI with the code.

        Raises:
            SignInError: With what the user is told.
        """
        from mcp.server.auth.provider import construct_redirect_uri

        servers = self.settings["loginServers"]
        if server not in servers:
            raise SignInError("Choose one of the listed servers.")
        account_key = f"{server}|{username}"
        if not username or self._sign_in_blocked(address, account_key):
            general.log_event(
                f"oauth: REFUSED a sign-in for '{username}' from address={address or '-'}"
                " - too many failed attempts"
            )
            raise SignInError("Too many failed attempts. Try again later.")
        if not consented:
            raise SignInError("Allow at least one of the listed uses.")

        try:
            account, roles, default_role, uri = self._check_account(server, username, password)
        except Exception as error:  # noqa: BLE001 - every failure reads the same
            self._login_failures.record(("account", account_key), ("address", address))
            general.log_event(
                f"oauth: REFUSED a sign-in for '{username}' on {server} from "
                f"address={address or '-'}: {type(error).__name__}"
            )
            raise SignInError("The user name or password is not correct.") from None

        # Before the account is mapped to a user, so one without the role is
        # never created as a user in the first place.
        required = str(self.settings.get("requiredRole") or "")
        if required and required not in roles["all"]:
            general.log_event(
                f"oauth: REFUSED a sign-in as {account} on {server}: the account does "
                f"not hold the role {required}"
            )
            raise SignInError("Your account is not allowed to use this server.")

        identity = {"type": tenants.IDENTITY_MARIADB, "server": server, "account": account}
        mcp_user_id = self._user_for(identity, username)
        record = self.directory.active_user(mcp_user_id)
        if record is None:
            raise SignInError("Your account is not allowed to use this server.")

        self._check_roles(pending["client_id"], record, roles, default_role)

        granted = [scope for scope in consented if scope in tenants.scopes_of(record)]
        if not granted:
            raise SignInError("Your account may not be granted any of these uses.")

        now = int(time.time())
        grant_id = uuid.uuid4().hex
        grant = {
            "id": grant_id,
            "user": mcp_user_id,
            "client": pending["client_id"],
            "scopes": granted,
            "server": server,
            "account": account,
            "uri": uri,
            "created": now,
            "lastRefreshed": now,
            "epoch": int(record.get("tokenEpoch", 0)),
            "refresh": "",
            "previous": "",
            "rotatedAt": 0,
        }
        self.store.create(grant, uri, password)

        code = secrets.token_urlsafe(32)
        with self._lock:
            self._pending.pop(request_id, None)
            self._codes[code] = {
                "client_id": pending["client_id"],
                "grant": grant_id,
                "user": mcp_user_id,
                "scopes": granted,
                "redirect_uri": pending["redirect_uri"],
                "explicit": pending["explicit"],
                "code_challenge": pending["code_challenge"],
                "expires_at": time.time() + CODE_LIFETIME,
            }

        general.log_event(
            f"oauth: user={general.log_id_prefix(mcp_user_id)} signed in as {account} on "
            f"{server} for the client {pending['client_id']} from address={address or '-'}, "
            f"granting {' '.join(granted)}"
        )

        return construct_redirect_uri(
            pending["redirect_uri"], code=code, state=pending["state"], iss=self.issuer
        )

    def _sign_in_blocked(self, address, account_key) -> bool:
        """Returns whether an account or an address has failed too often."""
        return (
            self._login_failures.count(("account", account_key))
            >= _LOGIN_MAX_FAILURES_PER_ACCOUNT
            or self._login_failures.count(("address", address))
            >= _LOGIN_MAX_FAILURES_PER_ADDRESS
        )

    def _check_account(self, server, username, password) -> tuple:
        """Opens a session as the user, reads who they are, and closes it again.

        Returns:
            ``(CURRENT_USER(), roles, default role, connection URI)``. The roles
            are a dict with ``all`` - every role the account can use, nested
            ones included, which is what "holds a role" means - and ``direct``,
            the ones granted to the account itself, which are all ``SET ROLE``
            can activate.
        """
        connection_data = dict(config._shell().parse_uri(server))
        connection_data["user"] = username
        host = connection_data.get("host", "")
        if (
            not general.is_loopback_host(host)
            and "socket" not in connection_data
            and str(connection_data.get("ssl-mode", "")).upper()
            not in ("REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY")
        ):
            # The password crosses the network: never in clear.
            connection_data["ssl-mode"] = "REQUIRED"
        uri = config.normalize_connection_uri(config._shell().unparse_uri(connection_data))

        connection_data["password"] = password
        session = self._open_session(connection_data)
        try:
            account = str(session.run_sql("SELECT CURRENT_USER()").fetch_one()[0])
            rows = session.run_sql(
                "SELECT GRANTEE, ROLE_NAME, IS_DEFAULT "
                "FROM information_schema.APPLICABLE_ROLES"
            ).fetch_all()
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - it was only for checking
                pass

        roles = {
            "all": sorted({str(row[1]) for row in rows}),
            "direct": sorted({str(row[1]) for row in rows if str(row[0]) == account}),
        }
        default_role = next(
            (str(row[1]) for row in rows if str(row[0]) == account and str(row[2]).upper() == "YES"),
            "",
        )

        return account, roles, default_role, uri

    def _user_for(self, identity, username) -> Optional[str]:
        """Returns the user an account belongs to, creating one if allowed."""
        mcp_user_id = tenants.find_user_by_identity(identity)
        if mcp_user_id is not None:
            return mcp_user_id

        provision = self.settings.get("autoProvision") or {}
        if not provision.get("enabled"):
            general.log_event(
                f"oauth: REFUSED a sign-in as {identity['account']}: no user has it, and "
                "users are not created automatically"
            )
            raise SignInError("Your account is not allowed to use this server.")

        mcp_user_id = tenants.add_user(
            [identity], name=username, scopes=provision.get("defaultScopes")
        )
        general.log_event(
            f"oauth: created user={general.log_id_prefix(mcp_user_id)} for "
            f"{identity['account']} on {identity['server']}"
        )

        return mcp_user_id

    def _check_roles(self, client_id, record, roles, default_role) -> None:
        """Refuses a sign-in the role rules do not allow."""
        configured = str(record.get("defaultRole") or "")
        if configured and configured not in roles["direct"]:
            raise SignInError(
                f"Your sessions run under the role '{configured}', which your account "
                "does not have."
            )

        client = oauth_config.read_clients().get(client_id) or {}
        allowed = client.get("allowedRoles") or []
        effective = configured or default_role
        if allowed and effective not in allowed:
            raise SignInError(
                "This client may only be used with the roles "
                f"{', '.join(allowed)}, and your sessions run under "
                f"{effective or 'no role'}."
            )

    async def load_authorization_code(self, client, authorization_code: str):
        from mcp.server.auth.provider import AuthorizationCode

        with self._lock:
            entry = self._codes.get(authorization_code)
        if entry is None or entry["client_id"] != client.client_id:
            return None

        return AuthorizationCode(
            code=authorization_code,
            scopes=entry["scopes"],
            expires_at=entry["expires_at"],
            client_id=entry["client_id"],
            code_challenge=entry["code_challenge"],
            redirect_uri=entry["redirect_uri"],
            redirect_uri_provided_explicitly=entry["explicit"],
            resource=self.public_url,
            subject=entry["user"],
        )

    async def exchange_authorization_code(self, client, authorization_code):
        import anyio

        with self._lock:
            entry = self._codes.pop(authorization_code.code, None)
        if entry is None:
            from mcp.server.auth.provider import TokenError

            raise TokenError(error="invalid_grant", error_description="The code was used already.")

        return await anyio.to_thread.run_sync(self._issue_tokens, entry["user"], entry["grant"], None)

    def _issue_tokens(self, user, grant_id, previous_refresh):
        """Issues an access token and a new refresh token for a live grant."""
        from mcp.server.auth.provider import TokenError
        from mcp.shared.auth import OAuthToken

        record = self.live_grant(user, grant_id)
        if record is None:
            raise TokenError(error="invalid_grant", error_description="The authorization has ended.")
        if previous_refresh and not hmac.compare_digest(
            _digest(previous_refresh), record.get("refresh", "")
        ):
            # Only the current refresh token is rotated. One accepted for its
            # grace period is answered from the remembered pair, never here.
            raise TokenError(error="invalid_grant", error_description="The refresh token was replaced.")

        refresh = f"{REFRESH_TOKEN_PREFIX}{uuid.UUID(user).hex}_{grant_id}_{secrets.token_urlsafe(32)}"
        now = int(time.time())
        record["previous"] = _digest(previous_refresh) if previous_refresh else ""
        record["rotatedAt"] = now
        record["refresh"] = _digest(refresh)
        record["lastRefreshed"] = now
        self.store.update(record)
        with self._lock:
            self._client_uses[record["client"]] = general.utc_timestamp()

        grant_end = record["created"] + int(self.settings["grantMaxLifetime"])
        lifetime = max(1, min(int(self.settings["accessTokenLifetime"]), grant_end - now))
        # Narrowed to what the user may have NOW, so mcp setup --setScopes
        # reaches a signed-in user at their next refresh at the latest.
        scopes = _grant_scopes(record, self.directory.active_user(user))
        token = OAuthToken(
            access_token=self._access_token(record, scopes, now, now + lifetime),
            token_type="Bearer",
            expires_in=lifetime,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )
        if previous_refresh:
            with self._lock:
                self._recent_refreshes[_digest(previous_refresh)] = (token, time.monotonic())

        return token

    def _access_token(self, record, scopes, issued_at, expires_at) -> str:
        """Signs an access token for a grant, carrying the given scopes."""
        import jwt

        claims = {
            "iss": self.issuer,
            "aud": self.public_url,
            "sub": record["user"],
            "client_id": record["client"],
            "scope": " ".join(scopes),
            "iat": issued_at,
            "exp": expires_at,
            "jti": secrets.token_hex(16),
            "epoch": record["epoch"],
            "grant": record["id"],
        }

        with self._lock:
            kid, private_key = self._kid, self._private_key

        return jwt.encode(
            claims, private_key, algorithm="ES256", headers={"kid": kid, "typ": "at+jwt"}
        )

    # --- refresh --------------------------------------------------------------------

    def _parse_refresh(self, token) -> Optional[tuple]:
        """Returns the ``(user, grant_id)`` a refresh token names, or None."""
        if not isinstance(token, str) or not token.startswith(REFRESH_TOKEN_PREFIX):
            return None
        parts = token[len(REFRESH_TOKEN_PREFIX):].split("_", 2)
        if len(parts) != 3:
            return None
        try:
            return str(uuid.UUID(hex=parts[0])), parts[1]
        except ValueError:
            return None

    async def load_refresh_token(self, client, refresh_token: str):
        import anyio

        return await anyio.to_thread.run_sync(self._load_refresh_sync, client.client_id, refresh_token)

    def _load_refresh_sync(self, client_id, token):
        from mcp.server.auth.provider import RefreshToken

        named = self._parse_refresh(token)
        if named is None:
            return None
        user, grant_id = named
        record = self.live_grant(user, grant_id)
        if record is None or record["client"] != client_id:
            return None

        presented = _digest(token)
        if not hmac.compare_digest(presented, record.get("refresh", "")):
            is_previous = bool(record.get("previous")) and hmac.compare_digest(
                presented, record["previous"]
            )
            if not is_previous:
                # Not this grant's token at all: refused, and nothing more. Only
                # a REUSED token - the one just replaced - ends the grant, or
                # anyone who learned a grant id could end it.
                return None

            within_grace = int(time.time()) - int(record.get("rotatedAt", 0)) < int(
                self.settings["refreshGracePeriod"]
            )
            with self._lock:
                remembered = self._recent_refreshes.get(presented)
            if not (within_grace and remembered is not None):
                self.end_grant(user, grant_id, "a refresh token was used twice")
                return None

        return RefreshToken(
            token=token,
            client_id=client_id,
            scopes=record["scopes"],
            expires_at=record["created"] + int(self.settings["grantMaxLifetime"]),
            resource=self.public_url,
            subject=user,
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        import anyio

        presented = _digest(refresh_token.token)
        with self._lock:
            remembered = self._recent_refreshes.get(presented)
        if remembered is not None and time.monotonic() - remembered[1] < int(
            self.settings["refreshGracePeriod"]
        ):
            # The same refresh twice in a race: the same answer, not a reuse.
            return remembered[0]

        user, grant_id = self._parse_refresh(refresh_token.token)

        return await anyio.to_thread.run_sync(
            self._issue_tokens, user, grant_id, refresh_token.token
        )

    # --- access tokens --------------------------------------------------------------

    async def load_access_token(self, token: str):
        import anyio

        if tenants.is_api_key(token):
            return await self.api_keys.verify_token(token)

        return await anyio.to_thread.run_sync(self.verify_access_token, token)

    def verify_access_token(self, token: str):
        """Returns the SDK access token a JWT of this server stands for, or None."""
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            public_key = self._verifying_key(jwt.get_unverified_header(token).get("kid"))
            if public_key is None:
                return None
            claims = jwt.decode(
                token,
                public_key,
                algorithms=["ES256"],
                audience=self.public_url,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "grant"]},
            )
        except Exception:  # noqa: BLE001 - not one of ours, or not valid
            return None

        user, grant_id = claims["sub"], claims["grant"]
        record = self.live_grant(user, grant_id)
        if record is None:
            return None
        active = self.directory.active_user(user)
        if active is None or int(active.get("tokenEpoch", 0)) != int(claims.get("epoch", -1)):
            return None

        # The user's current scopes bound the token's, so a narrowing by mcp
        # setup --setScopes applies to the next request, not the next sign-in.
        allowed = tenants.scopes_of(active)

        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id")),
            scopes=[scope for scope in str(claims.get("scope", "")).split() if scope in allowed],
            expires_at=int(claims["exp"]),
            resource=self.public_url,
            subject=user,
            claims={
                "iss": self.issuer,
                general.MCP_USER_ID_CLAIM: user,
                general.AUTH_METHOD_CLAIM: AUTH_METHOD_BUILTIN,
                general.GRANT_CLAIM: grant_id,
            },
        )

    async def revoke_token(self, token) -> None:
        import anyio

        claims = getattr(token, "claims", None) or {}
        if claims.get(general.GRANT_CLAIM):
            user, grant_id = claims[general.MCP_USER_ID_CLAIM], claims[general.GRANT_CLAIM]
        else:
            named = self._parse_refresh(getattr(token, "token", None))
            if named is None:
                return
            user, grant_id = named

        await anyio.to_thread.run_sync(self.end_grant, user, grant_id, "it was revoked")


def _put_bounded(table: dict, key, value, limit: int) -> None:
    """Stores a value, dropping the oldest entries to keep at most ``limit``."""
    table.pop(key, None)
    while len(table) >= limit:
        del table[next(iter(table))]
    table[key] = value


def _grant_scopes(record, user) -> list:
    """Returns the scopes of a grant its user may still be granted.

    A grant keeps the scopes consented to at sign-in; the user's record says
    what they may have now (see :func:`mcp_plugin.lib.tenants.scopes_of`).
    """
    if user is None:
        return []
    allowed = tenants.scopes_of(user)

    return [scope for scope in record.get("scopes") or [] if scope in allowed]


_SCOPE_DESCRIPTIONS = {
    tenants.SCOPE_DB: "work with your databases (connect, browse, run SQL)",
    tenants.SCOPE_MSM: "manage schema projects (MariaDB Schema Management)",
}

# The page follows the MySQL REST Service's sign-in page (mrs_plugin's
# default_static_content/index.html), without its script: same colours, same
# welcome header, same joined fields with the round "next" button.
_PAGE_STYLE = """
:root{--body-background:hsl(240,5%,91%);--body-text-color:hsl(240,5%,12%);
--icon-color:hsl(200,65%,40%);--textLink-foreground:hsl(200,65%,34%);
--primary-text-color:#333;--secondary-text-color:#adadad;--focus-color:hsl(200,65%,70%);
--error-color:hsl(0,100%,70%);--error-text-color:#500}
@media (prefers-color-scheme:dark){:root{--body-background:hsl(0,0%,17%);
--body-text-color:hsl(0,0%,75%);--textLink-foreground:hsl(200,65%,54%);
--primary-text-color:#c4c4c4;--secondary-text-color:#595959;--focus-color:hsl(200,65%,30%);
--error-color:hsl(0,100%,20%);--error-text-color:#e00}}
body,html{width:100%;min-height:100%;height:100%}
*{margin:0;padding:0}
body{background-color:var(--body-background);
font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-size:12px;
color:var(--body-text-color)}
h2{margin:20px 0;font-weight:100;font-size:33px}
p{line-height:19px;font-weight:200;font-size:15px}
#root{display:flex;box-sizing:border-box;min-height:100%;padding:0 16px 40px;
flex-direction:column;align-items:center;justify-content:center;position:relative}
.welcomeLogo{margin-top:20px;width:160px;height:104px;min-height:104px}
.welcomeLogo svg{width:100%;height:100%}
.welcomeLogo path{fill:var(--icon-color)}
.welcomeText{display:flex;flex-direction:column;align-items:center;justify-content:center}
.welcomeText p{text-align:center;max-width:400px}
.welcomeSpacer{height:80px}
.footer{position:absolute;bottom:0;line-height:12pt;font-weight:200;font-size:10px;margin:5px 0}
.mrsLogin{display:flex;flex-direction:column;padding-top:20px;padding-bottom:20px;gap:12px;
align-items:center}
.mrsLogin>p{font-size:20px;font-weight:400;margin-top:38px}
.mrsLoginIntro{max-width:300px;text-align:center;line-height:19px;font-weight:200;
font-size:15px}
.mrsLoginIntro strong{font-weight:500}
.mrsLoginFields{display:flex;flex-direction:column}
.mrsLoginScopes{border:none;margin:0 0 16px;font-size:14px;font-weight:200}
.mrsLoginScopes legend{font-weight:400;margin-bottom:6px}
.mrsLoginScopes label{display:flex;align-items:center;gap:8px;margin:4px 0}
input[type=checkbox]{margin:0}
.mrsLoginFields input[type=password],.mrsLoginFields input[type=text],.mrsLoginFields select
{border:none;outline:0;background-color:transparent;color:var(--primary-text-color);
font-size:17px;font-weight:300;width:250px;flex:1 1 250px;min-width:0;
font-family:inherit}
.mrsLoginField{display:flex;flex-direction:row;border:1px solid var(--secondary-text-color);
padding:8px 8px 8px 16px}
.mrsLoginField:first-of-type{border-top-left-radius:5px;border-top-right-radius:5px}
.mrsLoginField:last-of-type:not(:only-of-type){border-top:0}
.mrsLoginField:last-of-type{border-bottom-left-radius:5px;border-bottom-right-radius:5px;
margin-bottom:16px}
.mrsLoginField input,.mrsLoginField select{height:26px}
.mrsLoginField:focus-within{border-color:var(--focus-color)}
.mrsLoginBtnNext{border:1px solid var(--secondary-text-color);border-radius:50%;width:24px;
height:24px;min-width:24px;margin-left:12px;padding:0;background:transparent;
cursor:pointer;display:block;line-height:0;text-align:left;align-self:center;box-sizing:content-box;
-webkit-appearance:none;appearance:none;font:inherit;color:inherit;box-shadow:none}
.mrsLoginBtnNext svg{display:block;width:24px;height:24px;fill:none;
stroke:var(--secondary-text-color);stroke-width:3;stroke-linejoin:miter}
.mrsLoginField:has(input:not(:placeholder-shown)) .mrsLoginBtnNext{
border:1px solid var(--primary-text-color)}
.mrsLoginField:has(input:not(:placeholder-shown)) .mrsLoginBtnNext svg{
stroke:var(--primary-text-color)}
.mrsLoginBtnNext:focus-visible{outline:2px solid var(--focus-color);outline-offset:2px}
.mrsLoginError{background-color:var(--error-color);box-shadow:rgb(0 0 0 / 10%) 0 5px 10px 2px;
width:220px;padding:8px 20px;border:1px solid var(--error-text-color);border-radius:6px;
overflow-wrap:break-word;position:relative}
.mrsLoginError p{color:var(--error-text-color);font-size:14px;text-align:center}
.mrsLoginError:before{width:15px;height:15px;background-color:var(--error-color);content:"";
position:absolute;left:50%;top:0;margin-top:-9px;margin-left:-8px;
transform:rotate(135deg) skewX(5deg) skewY(5deg);border-left:1px solid var(--error-text-color);
border-bottom:1px solid var(--error-text-color)}
.mrsLoginSeparator{background:linear-gradient(to right,rgba(200,200,200,0),#c8c8c8,#c8c8c8,
rgba(200,200,200,0));width:400px;max-width:100%;height:1px;margin-top:20px}
"""


@functools.lru_cache(maxsize=None)
def _seal_svg() -> str:
    """Returns the MariaDB seal as inline SVG, coloured by the page's CSS.

    Inline because the page's CSP loads nothing; its style attributes are
    removed because the CSP allows only the nonce'd style element. Read once.
    """
    import os
    import re

    path = os.path.join(os.path.dirname(__file__), "assets", "mariadb-seal.svg")
    with open(path, encoding="utf-8") as file:
        svg = file.read()
    svg = svg[svg.index("<svg"):]
    svg = re.sub(r'\s(style|width|height|xml:space|xmlns:\w+|serif:\w+)="[^"]*"', "", svg)

    return svg.replace("<svg", '<svg role="img" aria-label="MariaDB"', 1)


def _page(response_class, body: str, status: int = 200, raw: bool = False,
          form_target: str = ""):
    """Returns an HTML page with the headers a sign-in page needs.

    Args:
        response_class: Starlette's ``HTMLResponse``.
        body (str): The page's content: text, or HTML when raw.
        status (int): The HTTP status.
        raw (bool): Whether body is HTML already.
        form_target (str): An origin the form may also lead to - the client's
            redirect - which browsers check form-action against after a POST.
    """
    nonce = secrets.token_urlsafe(16)
    content = body if raw else f'<div class="welcomeText"><p>{html.escape(body)}</p></div>'
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>MariaDB MCP Server</title><style nonce="{nonce}">{_PAGE_STYLE}</style>'
        f'</head><body><div id="root"><div class="welcomeLogo">{_seal_svg()}</div>'
        '<div class="welcomeText"><h2>MariaDB MCP Server</h2>'
        "<p>Welcome to the MariaDB MCP Server.</p></div>"
        f"{content}"
        '<div class="welcomeSpacer"></div>'
        '<div class="footer">Copyright (c) 2026, MariaDB plc.</div>'
        "</div></body></html>"
    )

    return response_class(
        document,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; style-src 'nonce-{nonce}'; "
                f"form-action 'self'{' ' + form_target if form_target else ''}; "
                "frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


def _public_key_of(pem: str):
    """Returns the public key of a PEM private key."""
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(pem.encode("ascii"), password=None).public_key()
