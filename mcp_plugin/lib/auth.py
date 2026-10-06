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

With OAuth2 turned on (see :mod:`mcp_plugin.lib.oauth_config`) API keys keep
working: :func:`build_auth` puts them in a :class:`CompositeVerifier` with the
mode's own verifier - Keycloak's (:mod:`mcp_plugin.lib.oauth_keycloak`) - or, for
the built-in authorization server, has its provider check both
(:mod:`mcp_plugin.lib.oauth_builtin`). What the SDK does not do is added around
its app by :func:`customize_app`: Protected Resource Metadata that lists the
scopes, a 403 ``insufficient_scope`` for a token granting no tool at all, the
built-in server's own authorization server metadata and sign-in page, a
network allow list, and ``client_secret_basic`` next to ``client_secret_post``.

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

    def __init__(self, directory: Optional[UserDirectory] = None, resource=None):
        """Creates a verifier.

        Args:
            directory: Where users and keys are looked up. A new one, which
                closes the connections of users who are no longer active, by
                default.
            resource (str): The server's public URL, reported as the resource
                every key is valid for - which the SDK checks wherever OAuth is
                on - or None.
        """
        self.directory = directory or UserDirectory(
            on_users_changed=_drop_connections_of_inactive_users
        )
        self.resource = resource

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
            resource=self.resource,
            subject=mcp_user_id,
            claims={
                "iss": API_KEY_ISSUER,
                general.MCP_USER_ID_CLAIM: mcp_user_id,
                general.AUTH_METHOD_CLAIM: AUTH_METHOD_API_KEY,
            },
        )


class CompositeVerifier:
    """A token verifier that asks each of several in turn.

    The first that recognizes a token answers for it: an API key is told apart
    from a JWT by its shape (see :func:`mcp_plugin.lib.tenants.is_api_key`), so
    each verifier only ever sees the tokens meant for it.
    """

    def __init__(self, verifiers):
        self.verifiers = list(verifiers)

    async def verify_token(self, token: str):
        for verifier in self.verifiers:
            if tenants.is_api_key(token) != isinstance(verifier, ApiKeyVerifier):
                continue
            return await verifier.verify_token(token)

        return None


class AuthBundle:
    """What a multi-tenant server needs to authenticate, for one OAuth mode.

    Attributes:
        settings: The SDK's ``AuthSettings``.
        token_verifier: The verifier, or None where a provider checks tokens.
        provider: The built-in authorization server's provider, or None.
        mode (str): The OAuth mode.
        public_url (str): The server's public URL, or ``""``.
    """

    def __init__(self, settings, token_verifier=None, provider=None, mode="none",
                 public_url=""):
        self.settings = settings
        self.token_verifier = token_verifier
        self.provider = provider
        self.mode = mode
        self.public_url = public_url

    def server_kwargs(self) -> dict:
        """Returns the keyword arguments ``MCPServer`` takes for this."""
        kwargs = {"auth": self.settings}
        if self.provider is not None:
            kwargs["auth_server_provider"] = self.provider
        else:
            kwargs["token_verifier"] = self.token_verifier

        return kwargs


def build_auth(verifier=None, mode=None, public_url=None):
    """Returns what ``MCPServer`` needs to authenticate requests.

    Args:
        verifier: The API key verifier, or None for a new
            :class:`ApiKeyVerifier`.
        mode (str): The OAuth mode, or None for the configured one.
        public_url (str): The server's public URL, or None for the configured
            one.

    Returns:
        An :class:`AuthBundle`.
    """
    from mcp.server.auth.settings import (
        AuthSettings,
        ClientRegistrationOptions,
        RevocationOptions,
    )

    from mcp_plugin.lib import oauth_config

    mode = mode or oauth_config.get_mode()
    if public_url is None:
        public_url = oauth_config.get_public_url()

    if mode == oauth_config.OAUTH_MODE_NONE:
        settings = AuthSettings(
            issuer_url=_API_KEY_ONLY_ISSUER_URL,
            resource_server_url=None,
            required_scopes=None,
        )

        return AuthBundle(
            settings, token_verifier=verifier or ApiKeyVerifier(), mode=mode
        )

    oauth_config.check_ready(mode)
    api_keys = verifier or ApiKeyVerifier(resource=public_url)
    oauth = oauth_config.get_oauth_settings()

    if mode == oauth_config.OAUTH_MODE_KEYCLOAK:
        from mcp_plugin.lib.oauth_keycloak import KeycloakVerifier

        keycloak = KeycloakVerifier(
            oauth["keycloak"], public_url, directory=api_keys.directory
        )
        settings = AuthSettings(
            issuer_url=oauth["keycloak"]["issuer"],
            resource_server_url=public_url,
            required_scopes=None,
            validate_token_resource=True,
        )

        return AuthBundle(
            settings,
            token_verifier=CompositeVerifier([api_keys, keycloak]),
            mode=mode,
            public_url=public_url,
        )

    from mcp_plugin.lib.oauth_builtin import BuiltinAuthProvider

    builtin = oauth["builtin"]
    provider = BuiltinAuthProvider(builtin, public_url, api_keys)
    settings = AuthSettings(
        issuer_url=oauth_config.public_url_origin(public_url),
        resource_server_url=public_url,
        required_scopes=None,
        validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(
            enabled=bool(builtin["dynamicClientRegistration"]),
            default_scopes=list(builtin["autoProvision"]["defaultScopes"]),
        ),
        revocation_options=RevocationOptions(enabled=True),
    )

    return AuthBundle(settings, provider=provider, mode=mode, public_url=public_url)


def filtered_tools(tools, principal):
    """Returns the tools a principal's scopes let them call.

    Args:
        tools: The SDK ``Tool`` objects a server serves.
        principal: The :class:`mcp_plugin.lib.general.Principal`, or None.

    Returns:
        The tools whose group the principal has the ``mcp:<group>`` scope of;
        none at all without a principal.
    """
    if principal is None:
        return []

    from mcp_plugin.lib import tool_registrar

    return [
        tool
        for tool in tools
        if f"mcp:{tool_registrar.tool_group(tool.name)}" in principal.scopes
    ]


def scoped_server_class():
    """Returns an ``MCPServer`` that lists each caller only their tools.

    The SDK lists every registered tool to everyone. A multi-tenant server
    only lists those the caller's token grants the scope of, so a client never
    offers its model a tool it cannot call. ``_handle_list_tools`` is a private
    hook of the SDK; ``test_tools_are_listed_by_scope`` pins it.
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ListToolsResult

    class ScopedMCPServer(MCPServer):
        async def _handle_list_tools(self, ctx, params):
            listed = await super()._handle_list_tools(ctx, params)
            principal = general.get_principal(_RequestContext(ctx))

            return ListToolsResult(tools=filtered_tools(listed.tools, principal))

    return ScopedMCPServer


class _RequestContext:
    """Presents an SDK request context the way the tools' ``ctx`` presents one."""

    def __init__(self, request_context):
        self.request_context = request_context


class InsufficientScopeMiddleware:
    """Answers an authenticated request whose token grants no tool with a 403.

    Installed INSIDE the SDK's authentication middleware, so the user it put in
    the scope is there to read. A token granting at least one tool scope goes
    through; which tools it may call is checked per call. One granting none
    could do nothing at all, and the MCP specification asks for a 403
    ``insufficient_scope`` naming the scopes needed, which lets a client go
    back to the authorization server for them.
    """

    def __init__(self, app, resource_metadata_url=None):
        self.app = app
        self.resource_metadata_url = resource_metadata_url

    async def __call__(self, scope, receive, send):
        user = scope.get("user") if scope.get("type") == "http" else None
        token = getattr(user, "access_token", None)
        if token is not None and not set(token.scopes) & set(tenants.SUPPORTED_SCOPES):
            challenge = (
                'Bearer error="insufficient_scope", '
                f'scope="{" ".join(tenants.SUPPORTED_SCOPES)}", '
                'error_description="The token grants no tool of this server"'
            )
            if self.resource_metadata_url:
                challenge += f', resource_metadata="{self.resource_metadata_url}"'
            await _send_json(send, 403, {"error": "insufficient_scope"},
                             [(b"www-authenticate", challenge.encode("latin-1"))])
            return

        await self.app(scope, receive, send)


class ClientNetworkMiddleware:
    """Refuses requests to the protected endpoints from outside some networks.

    The counterpart of a Snowflake network policy: only the listed networks
    (Arcade's egress addresses, say) may call the MCP endpoint and the
    authorization server's token, registration and revocation endpoints. The
    sign-in page is not restricted - the user's browser comes from anywhere.
    """

    _PROTECTED_PATHS = ("/token", "/register", "/revoke")

    def __init__(self, app, networks, mcp_path="/mcp"):
        self.app = app
        self.networks = list(networks)
        self.paths = self._PROTECTED_PATHS + (mcp_path,)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") in self.paths:
            from mcp_plugin.lib import oauth_config

            address = general.normalize_client_address((scope.get("client") or (None,))[0])
            if not oauth_config.address_allowed(address, self.networks):
                general.log_event(
                    f"auth: REFUSED a request to {scope.get('path')} from "
                    f"address={address or '-'}, outside the allowed client networks"
                )
                await _send_json(send, 403, {"error": "access_denied"})
                return

        await self.app(scope, receive, send)


class BasicClientAuthMiddleware:
    """Lets a client authenticate to the token endpoint either way OAuth allows.

    The SDK checks a client's secret where the client's registration says it
    sends it - the Basic header or the form - and a client registered by an
    administrator does not say. OAuth 2.1 has the server accept both. So a
    request to ``/token`` or ``/revoke`` that authenticates with a Basic header
    is turned into one that sends the same credentials in the form, which is
    what such clients are registered for (see
    :mod:`mcp_plugin.lib.oauth_builtin`).
    """

    _PATHS = ("/token", "/revoke")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self._PATHS:
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or ())
        basic = next(
            (value for name, value in headers
             if name.lower() == b"authorization" and value[:6].lower() == b"basic "),
            None,
        )
        if basic is None:
            await self.app(scope, receive, send)
            return

        import base64
        from urllib.parse import parse_qsl, unquote, urlencode

        try:
            decoded = base64.b64decode(basic[6:].strip()).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        form = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        form.setdefault("client_id", unquote(client_id))
        form.setdefault("client_secret", unquote(client_secret))
        new_body = urlencode(form).encode("utf-8")

        scope = dict(scope)
        scope["headers"] = [
            (name, value) for name, value in headers
            if name.lower() not in (b"authorization", b"content-length", b"content-type")
        ] + [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", str(len(new_body)).encode("ascii")),
        ]

        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": new_body, "more_body": False}

        await self.app(scope, replay, send)


def customize_app(starlette_app, bundle: AuthBundle, mcp_path: str = "/mcp"):
    """Adds what the SDK's app lacks for a multi-tenant server.

    Args:
        starlette_app: The app ``streamable_http_app()`` built.
        bundle (AuthBundle): How the server authenticates.
        mcp_path (str): The path the MCP endpoint is served at.

    Returns:
        The ASGI app to serve.
    """
    from starlette.middleware import Middleware

    from mcp_plugin.lib import oauth_config

    resource_metadata_url = None
    if bundle.public_url:
        from mcp.server.auth.routes import (
            build_resource_metadata_url,
            create_protected_resource_routes,
        )

        # The URLs exactly as AuthSettings parsed them, which keeps an empty
        # path empty: a client compares the issuer it is told here with the one
        # the authorization server's metadata states, character by character,
        # and a bare origin parsed anywhere else gains a trailing slash.
        resource = bundle.settings.resource_server_url
        resource_metadata_url = str(build_resource_metadata_url(resource))
        # Ahead of the SDK's own PRM route, which lists the required scopes -
        # none here, as a token needs only one of the tool scopes.
        starlette_app.router.routes[0:0] = create_protected_resource_routes(
            resource_url=resource,
            authorization_servers=[bundle.settings.issuer_url],
            scopes_supported=list(tenants.SUPPORTED_SCOPES),
        )

    if bundle.provider is not None:
        starlette_app.router.routes[0:0] = bundle.provider.routes()

    # Innermost, so the SDK's authentication has run by the time it does.
    starlette_app.user_middleware.append(
        Middleware(InsufficientScopeMiddleware, resource_metadata_url=resource_metadata_url)
    )

    app = starlette_app
    if bundle.provider is not None:
        app = BasicClientAuthMiddleware(app)
        networks = oauth_config.get_oauth_settings()["builtin"]["allowedClientNetworks"]
        if networks:
            app = ClientNetworkMiddleware(app, networks, mcp_path)

    return app


async def _send_json(send, status: int, payload: dict, headers=()) -> None:
    """Answers a request with a small JSON body."""
    import json

    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


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
