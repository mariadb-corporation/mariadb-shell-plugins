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

"""Authentication for a multi-tenant MCP server.

A multi-tenant server (see :mod:`mcp_plugin.lib.tenants`) only answers requests
that carry a bearer token it can verify. The checking itself is the MCP SDK's:
given an ``AuthSettings`` and a token verifier, ``MCPServer`` installs its bearer
authentication middleware, answers a request without a valid token with a 401
before any tool runs, and binds every MCP session to the principal that created
it. What this module adds is the verifier, and what has to sit around it:

* :class:`ApiKeyVerifier` accepts a user's API key. Every key names the user it
  belongs to (see :func:`mcp_plugin.lib.tenants.user_of_api_key`), so one secret
  group is read per check, not every user's.
* :class:`UserDirectory` caches ``users.json`` and the keys read from the secret
  store - a read costs some 30ms on the macOS keychain, too much for every
  request. ``users.json`` is ``stat``-ed on every check and the whole cache
  dropped when it changed, so a user removed, disabled or given a new key with
  ``mcp setup`` in another process is refused from the next request on, not
  after a timeout. Noticing that is also when the connections of a user who is
  gone are closed.
* :class:`AuthFailureThrottle` slows down guessing: after too many refused
  tokens from one address FOR ONE USER, that pair is answered with a 429 for a
  while. Keyed on the pair and not the address alone, because a gateway such as
  Arcade sends every one of its users' requests from a handful of addresses:
  one user's bad tokens must not lock all the others out.

The verified token becomes an ``AccessToken`` whose claims carry the
``mcp_user_id`` (see :func:`mcp_plugin.lib.general.get_principal`), which is all
the tools ever look at.

The MCP SDK is imported only inside functions, as everywhere in this plugin
(see :mod:`mcp_plugin.lib.tool_registrar`).
"""

# cSpell:ignore mysqlsh MariaDB anyio starlette

import hashlib
import hmac
import threading
import time
from typing import Optional

from mcp_plugin.lib import general, tenants

# The client_id of every access token made from an API key. The SDK binds an
# MCP session to (client_id, issuer, subject), and the subject is the user.
API_KEY_CLIENT_ID = "mcp-api-key"

# The issuer claim of every access token made from an API key.
API_KEY_ISSUER = "mariadb-mcp:api-key"

# How the user authenticated, as general.Principal.auth_method reports it.
AUTH_METHOD_API_KEY = "api-key"

# How long a key read from the secret store is trusted without reading it
# again, in seconds. The cache is dropped as soon as users.json changes, which
# is what every change mcp.setup makes writes, so this only bounds a key changed
# in the secret store behind the plugin's back.
API_KEY_CACHE_TTL = 60

# The issuer URL handed to the SDK's AuthSettings, which insists on one. With
# only a token verifier and no authorization server, the SDK uses it for
# nothing: it serves no metadata and no authorization endpoints.
_API_KEY_ONLY_ISSUER_URL = "http://localhost"


class UserDirectory:
    """The users and API keys a server checks tokens against, cached.

    Thread-safe: tokens are checked on worker threads, several at a time.
    """

    def __init__(self, on_users_changed=None):
        """Creates an empty directory.

        Args:
            on_users_changed: Called with the set of ACTIVE user ids each time
                users.json is found to have changed, after the cache was
                refreshed - which is when the connections of users who are no
                longer active are closed. Never called with a lock held.
        """
        self._lock = threading.Lock()
        # A value users_file_version() never returns, so the first call reads.
        self._version = object()
        self._users = {}
        self._keys = {}
        self._on_users_changed = on_users_changed

    def users(self) -> dict:
        """Returns every user, re-read whenever users.json has changed.

        A file that cannot be read means no users, which refuses everyone:
        failing closed, and logged.

        Returns:
            A dict of ``mcp_user_id`` to the user's record.
        """
        version = tenants.users_file_version()
        with self._lock:
            if version == self._version:
                return self._users

        try:
            users = tenants.read_users()
        except Exception as error:  # noqa: BLE001 - refuse everyone, say why
            general.log_event(f"auth: could not read the users, refusing all: {error}")
            users = {}

        with self._lock:
            self._version = version
            self._users = users
            # Every key goes with the file: a rotation writes the file.
            self._keys = {}

        if self._on_users_changed is not None:
            try:
                self._on_users_changed(
                    {
                        user_id
                        for user_id, record in users.items()
                        if not record.get("disabled", False)
                    }
                )
            except Exception as error:  # noqa: BLE001 - checking must go on
                general.log_event(f"auth: acting on a change of the users failed: {error}")

        return users

    def active_user(self, mcp_user_id) -> Optional[dict]:
        """Returns a user's record if the user exists and is not disabled."""
        record = self.users().get(mcp_user_id)
        if record is None or record.get("disabled", False):
            return None

        return record

    def api_key_matches(self, mcp_user_id, token: str) -> bool:
        """Returns whether a token is the user's current API key.

        Compared as SHA-256 digests in constant time. The key is read from the
        secret store when the cache has none for the user, when the cached one
        is older than :data:`API_KEY_CACHE_TTL`, and when the token does not
        match the cached one - so a freshly issued key works at once.

        Args:
            mcp_user_id (str): The user the token claims to be for.
            token (str): The token.

        Returns:
            True if it is their key.
        """
        presented = hashlib.sha256(token.encode("utf-8")).digest()

        with self._lock:
            cached = self._keys.get(mcp_user_id)

        if (
            cached is not None
            and time.monotonic() - cached[1] < API_KEY_CACHE_TTL
            and hmac.compare_digest(cached[0], presented)
        ):
            return True

        key = tenants.get_api_key(mcp_user_id)
        if key is None:
            return False

        stored = hashlib.sha256(key.encode("utf-8")).digest()
        with self._lock:
            self._keys[mcp_user_id] = (stored, time.monotonic())

        return hmac.compare_digest(stored, presented)


def _drop_connections_of_inactive_users(active_user_ids) -> None:
    """Closes every connection of a user who was removed or disabled."""
    # Imported here: db_functions is a tool module and imports more than the
    # verifier needs at import time.
    from mcp_plugin.lib import db_functions

    db_functions.drop_connections_of_inactive_users(active_user_ids)


class ApiKeyVerifier:
    """The SDK token verifier that accepts users' API keys."""

    def __init__(self, directory: Optional[UserDirectory] = None):
        """Creates a verifier.

        Args:
            directory: Where users and keys are looked up. A new one, which
                closes the connections of users who are no longer active, by
                default.
        """
        self.directory = directory or UserDirectory(
            on_users_changed=_drop_connections_of_inactive_users
        )

    async def verify_token(self, token: str):
        """Returns the access token an API key stands for, or None.

        Called by the SDK for every request. The work - a stat, maybe a file and
        a secret-store read - runs on a worker thread, so a slow keychain never
        holds up the event loop the whole server answers on.

        Args:
            token (str): The bearer token the request carried.

        Returns:
            An ``AccessToken`` for the user, or None to have the request refused.
        """
        import anyio

        return await anyio.to_thread.run_sync(self.verify_token_sync, token)

    def verify_token_sync(self, token: str):
        """Does what :meth:`verify_token` does, on the calling thread."""
        from mcp.server.auth.provider import AccessToken

        mcp_user_id = tenants.user_of_api_key(token)
        if mcp_user_id is None:
            return None

        record = self.directory.active_user(mcp_user_id)
        if record is None or not self.directory.api_key_matches(mcp_user_id, token):
            return None

        scopes = record.get("scopes")
        if not isinstance(scopes, list):
            scopes = list(tenants.DEFAULT_SCOPES)

        return AccessToken(
            token=token,
            client_id=API_KEY_CLIENT_ID,
            scopes=[scope for scope in tenants.SUPPORTED_SCOPES if scope in scopes],
            subject=mcp_user_id,
            claims={
                "iss": API_KEY_ISSUER,
                general.MCP_USER_ID_CLAIM: mcp_user_id,
                general.AUTH_METHOD_CLAIM: AUTH_METHOD_API_KEY,
            },
        )


def build_auth(verifier=None) -> tuple:
    """Returns what ``MCPServer`` needs to authenticate requests.

    Args:
        verifier: The token verifier, or None for a new :class:`ApiKeyVerifier`.

    Returns:
        An ``(AuthSettings, verifier)`` tuple.
    """
    from mcp.server.auth.settings import AuthSettings

    settings = AuthSettings(
        issuer_url=_API_KEY_ONLY_ISSUER_URL,
        resource_server_url=None,
        required_scopes=None,
    )

    return settings, verifier or ApiKeyVerifier()


class AuthFailureThrottle:
    """ASGI middleware answering repeated authentication failures with a 429.

    A failure is a request that carried a bearer token and was answered with a
    401. They are counted per ``(peer address, user named in the token)`` over
    a sliding window, with a far higher ceiling for the address alone, so a
    stream of guesses is slowed down without one user's mistakes locking out
    everyone else who shares their address (see the module docstring). A
    request without a token is not counted: that is how every client starts.
    """

    def __init__(
        self,
        app,
        window: float = 60.0,
        max_failures: int = 10,
        max_failures_per_address: int = 200,
    ):
        """Wraps an ASGI app.

        Args:
            app: The ASGI app to protect.
            window (float): The time over which failures are counted, in
                seconds; also how long a throttled pair stays throttled.
            max_failures (int): Failures from one address for one user after
                which that pair is throttled.
            max_failures_per_address (int): Failures from one address, for any
                users, after which the address is throttled.
        """
        self.app = app
        self.window = window
        self.max_failures = max_failures
        self.max_failures_per_address = max_failures_per_address
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key, now) -> list:
        """Returns the failure times of one key within the window, pruned."""
        times = [t for t in self._failures.get(key, []) if now - t < self.window]
        if times:
            self._failures[key] = times
        else:
            self._failures.pop(key, None)

        return times

    def is_throttled(self, address, user) -> bool:
        """Returns whether requests from an address for a user are throttled."""
        now = time.monotonic()
        with self._lock:
            return (
                len(self._recent((address, user), now)) >= self.max_failures
                or len(self._recent((address, None), now))
                >= self.max_failures_per_address
            )

    def record_failure(self, address, user) -> None:
        """Counts one failure from an address for a user."""
        now = time.monotonic()
        with self._lock:
            self._failures.setdefault((address, user), []).append(now)
            self._failures.setdefault((address, None), []).append(now)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        token = _bearer_token(scope)
        if token is None:
            await self.app(scope, receive, send)
            return

        address = general.normalize_client_address((scope.get("client") or (None,))[0])
        user = tenants.user_of_api_key(token) or "-"

        if self.is_throttled(address, user):
            await _send_too_many_requests(send, self.window)
            return

        status = {}

        async def capture(message):
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status")
            await send(message)

        await self.app(scope, receive, capture)

        if status.get("code") == 401:
            self.record_failure(address, user)
            general.log_event(
                f"auth: REFUSED a bearer token from address={address or '-'} "
                f"for user={general.log_id_prefix(user if user != '-' else None)}"
            )


def _bearer_token(scope) -> Optional[str]:
    """Returns the bearer token of an ASGI HTTP request, if it carries one."""
    for name, value in scope.get("headers") or ():
        if name.lower() != b"authorization":
            continue

        text = value.decode("latin-1")
        if text[:7].lower() == "bearer ":
            return text[7:].strip()

    return None


async def _send_too_many_requests(send, retry_after: float) -> None:
    """Answers a request with a 429 and nothing else."""
    body = b'{"error": "too_many_requests"}'
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(int(retry_after)).encode("ascii")),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
