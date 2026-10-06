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

"""Keycloak as the authorization server of a multi-tenant MCP server.

In OAuth mode ``keycloak`` (see :mod:`mcp_plugin.lib.oauth_config`) this server
is a pure OAuth resource server: Keycloak signs users in and issues the access
tokens, its Protected Resource Metadata (served by this server) sends clients
there, and :class:`KeycloakVerifier` checks every token a client brings back:

1. The signature, against the realm's keys (its ``jwks_uri``, found through
   OpenID Connect discovery and cached; an unknown ``kid`` refetches them once,
   which is how a key rotation is followed). Only asymmetric algorithms are
   accepted - never ``none``, never an HMAC one, which would let anyone holding
   the public key sign.
2. The issuer, exactly; the expiry, with 30s of leeway; and that it is an
   access token (``typ`` ``Bearer``) and not an ID token.
3. **The audience must include this server's public URL.** Keycloak puts it
   there through an ``Audience`` mapper on the client scopes ``mcp:db`` and
   ``mcp:msm``, or from the ``resource`` parameter once its resource indicators
   are turned on. A token issued for anything else is refused: the MCP
   specification forbids accepting tokens meant for another resource.
4. Optionally the client (``azp``), against an allow list.
5. The user. A token's ``(iss, sub)`` is an ``oauth`` identity (see
   :mod:`mcp_plugin.lib.tenants`). If no user has it, a user whose ``email``
   identity is the token's VERIFIED email gets it linked; failing that, a user
   is created when the token carries the realm role auto-provisioning requires.
   Anyone else is refused, and logged.
6. The scopes: those the token carries, narrowed to those the user may be
   granted. A disabled user, or a token issued before the user's tokens were
   revoked, is refused.

With ``verification: introspection`` the token is not decoded here at all but
sent to Keycloak's introspection endpoint (RFC 7662), so a session ended in
Keycloak is refused at once rather than when the token expires - at the price
of a round trip per token, cached for 30s.

The token itself is never passed on: the database is reached with the
credentials an administrator stored for the user, which is what the MCP
specification requires and all MariaDB could use anyway.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak jwks azp anyio httpx

import hashlib
import threading
import time
from typing import Optional

from mcp_plugin.lib import general, tenants

# How a user authenticated, as general.Principal.auth_method reports it.
AUTH_METHOD_KEYCLOAK = "keycloak"

# The algorithms a token may be signed with. Asymmetric only.
_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]

# Leeway for the expiry and not-before checks, in seconds.
_LEEWAY = 30

# How long an introspection answer is reused, in seconds.
_INTROSPECTION_CACHE_TTL = 30

# How long the discovered endpoints are reused, in seconds.
_DISCOVERY_TTL = 3600

# The HTTP timeout for discovery, keys and introspection, in seconds.
_HTTP_TIMEOUT = 10


def _audiences(claims) -> list:
    """Returns a token's audiences as a list."""
    audience = claims.get("aud")
    if audience is None:
        return []
    if isinstance(audience, str):
        return [audience]

    return [str(item) for item in audience]


def _same_url(first, second) -> bool:
    """Compares two URLs the way resource identifiers compare: no trailing slash."""
    return str(first).rstrip("/").lower() == str(second).rstrip("/").lower()


class KeycloakVerifier:
    """The SDK token verifier for access tokens Keycloak issued."""

    def __init__(self, settings: dict, public_url: str, directory=None, http_get=None,
                 http_post=None):
        """Creates a verifier.

        Args:
            settings (dict): The ``keycloak`` section of the oauth settings.
            public_url (str): This server's public URL, which tokens must be
                issued for.
            directory: The :class:`mcp_plugin.lib.auth.UserDirectory` to look
                users up in, or None for a fresh one.
            http_get: A function ``(url) -> dict`` fetching JSON, for tests.
            http_post: A function ``(url, data, auth) -> dict`` posting a form
                and returning JSON, for tests.
        """
        from mcp_plugin.lib import auth

        self.settings = settings
        self.issuer = str(settings["issuer"]).rstrip("/")
        self.public_url = public_url
        self.directory = directory or auth.UserDirectory()
        self._http_get = http_get or _http_get_json
        self._http_post = http_post or _http_post_json
        self._lock = threading.Lock()
        self._discovery = None
        self._discovered_at = 0.0
        self._jwk_client = None
        self._introspected = {}

    # --- what Keycloak publishes -------------------------------------------------

    def discovery(self) -> dict:
        """Returns the realm's OpenID Connect discovery document, cached."""
        with self._lock:
            if self._discovery is not None and time.monotonic() - self._discovered_at < _DISCOVERY_TTL:
                return self._discovery

        document = self._http_get(f"{self.issuer}/.well-known/openid-configuration")
        if str(document.get("issuer", "")).rstrip("/") != self.issuer:
            raise ValueError(
                f"The discovery document of '{self.issuer}' names another issuer, "
                f"'{document.get('issuer')}'."
            )

        with self._lock:
            self._discovery = document
            self._discovered_at = time.monotonic()

        return document

    def _signing_key(self, token):
        """Returns the realm key a token says it was signed with."""
        import jwt

        with self._lock:
            client = self._jwk_client
        if client is None:
            jwks_uri = self.discovery()["jwks_uri"]
            # PyJWKClient refetches the keys for an unknown kid at most every
            # 30s (its cooldown), which keeps tokens with made-up kids from
            # hammering Keycloak. Keycloak publishes a new key before signing
            # with it, so a real rotation is not held up by that.
            client = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=300,
                                     timeout=_HTTP_TIMEOUT)
            with self._lock:
                self._jwk_client = client

        return client.get_signing_key_from_jwt(token).key

    # --- checking a token ---------------------------------------------------------

    def decode(self, token: str) -> Optional[dict]:
        """Returns a token's claims if its signature, issuer and expiry hold.

        Returns:
            The claims, or None if the token is not valid.
        """
        import jwt

        try:
            return jwt.decode(
                token,
                self._signing_key(token),
                algorithms=_ALGORITHMS,
                issuer=self.issuer,
                leeway=_LEEWAY,
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except Exception as error:  # noqa: BLE001 - any failure refuses the token
            general.log_event(f"oauth: REFUSED a Keycloak token: {type(error).__name__}: {error}")
            return None

    def introspect(self, token: str) -> Optional[dict]:
        """Returns what Keycloak says about a token, if it is active.

        Returns:
            The introspection response, or None if the token is not active.
        """
        from mcp_plugin.lib import oauth_config

        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._lock:
            cached = self._introspected.get(digest)
            if cached is not None and now - cached[1] < _INTROSPECTION_CACHE_TTL:
                return cached[0]

        endpoint = self.discovery().get("introspection_endpoint")
        try:
            answer = self._http_post(
                endpoint,
                {"token": token, "token_type_hint": "access_token"},
                (
                    self.settings["introspectionClientId"],
                    oauth_config.get_introspection_secret() or "",
                ),
            )
        except Exception as error:  # noqa: BLE001 - refuse, and say why
            general.log_event(f"oauth: Keycloak introspection failed: {error}")
            return None

        claims = answer if answer.get("active") else None
        with self._lock:
            self._introspected = {
                key: value
                for key, value in self._introspected.items()
                if now - value[1] < _INTROSPECTION_CACHE_TTL
            }
            self._introspected[digest] = (claims, now)

        return claims

    def check_claims(self, claims: dict) -> bool:
        """Returns whether verified claims are an access token for this server."""
        if claims.get("typ") not in (None, "Bearer", "bearer", "at+jwt"):
            general.log_event(f"oauth: REFUSED a Keycloak token of type '{claims.get('typ')}'")
            return False

        if str(claims.get("iss", "")).rstrip("/") != self.issuer:
            return False

        if claims.get("exp") is not None and claims["exp"] + _LEEWAY < time.time():
            return False

        if not any(_same_url(audience, self.public_url) for audience in _audiences(claims)):
            general.log_event(
                "oauth: REFUSED a Keycloak token issued for "
                f"{_audiences(claims) or 'no audience'}, not for {self.public_url}"
            )
            return False

        allowed_clients = self.settings.get("clientIds") or []
        if allowed_clients and claims.get("azp") not in allowed_clients:
            general.log_event(
                f"oauth: REFUSED a Keycloak token of client '{claims.get('azp')}', "
                "which is not an allowed client"
            )
            return False

        return True

    def user_for(self, claims: dict) -> Optional[str]:
        """Returns the user a token's claims stand for, linking or creating one.

        Returns:
            The ``mcp_user_id``, or None if the token names nobody who may use
            this server.
        """
        identity = {
            "type": tenants.IDENTITY_OAUTH,
            "issuer": self.issuer,
            "subject": str(claims.get("sub", "")),
        }
        mcp_user_id = tenants.find_user_by_identity(identity)
        if mcp_user_id is not None:
            return mcp_user_id

        email = claims.get("email")
        verified = bool(email) and claims.get("email_verified") is True

        if verified and self.settings.get("linkByVerifiedEmail", True):
            owners = tenants.users_with_email(email)
            if len(owners) == 1:
                tenants.link_identity(owners[0], identity)
                general.log_event(
                    f"oauth: linked the Keycloak subject {general.log_id_prefix(identity['subject'])} "
                    f"to user={general.log_id_prefix(owners[0])} by their verified email"
                )
                return owners[0]
            if len(owners) > 1:
                general.log_event(
                    "oauth: REFUSED a Keycloak token: its verified email belongs to "
                    f"{len(owners)} users, so which one is meant is not guessed"
                )
                return None

        provision = self.settings.get("autoProvision") or {}
        required = provision.get("requiredRealmRole") or ""
        roles = ((claims.get("realm_access") or {}).get("roles")) or []
        if provision.get("enabled") and (not required or required in roles):
            identities = [identity]
            if verified and not tenants.users_with_email(email):
                identities.append({"type": tenants.IDENTITY_EMAIL, "value": email})
            mcp_user_id = tenants.add_user(
                identities,
                name=claims.get("name") or claims.get("preferred_username"),
                scopes=provision.get("defaultScopes"),
            )
            general.log_event(
                f"oauth: created user={general.log_id_prefix(mcp_user_id)} for the "
                f"Keycloak subject {general.log_id_prefix(identity['subject'])}"
            )
            return mcp_user_id

        general.log_event(
            f"oauth: REFUSED a Keycloak token: the subject "
            f"{general.log_id_prefix(identity['subject'])} is no user of this server"
        )
        return None

    def verify_token_sync(self, token: str):
        """Returns the access token a Keycloak token stands for, or None."""
        from mcp.server.auth.provider import AccessToken

        from mcp_plugin.lib import oauth_config

        if self.settings.get("verification") == oauth_config.VERIFICATION_INTROSPECTION:
            claims = self.introspect(token)
        else:
            claims = self.decode(token)
        if claims is None or not self.check_claims(claims):
            return None

        try:
            mcp_user_id = self.user_for(claims)
        except Exception as error:  # noqa: BLE001 - refuse, and say why
            general.log_event(f"oauth: could not map a Keycloak token to a user: {error}")
            return None
        if mcp_user_id is None:
            return None

        record = self.directory.active_user(mcp_user_id)
        if record is None:
            return None
        revoked_at = int(record.get("tokensRevokedAt", 0) or 0)
        if revoked_at and int(claims.get("iat", 0)) <= revoked_at:
            return None

        allowed = record.get("scopes")
        if not isinstance(allowed, list):
            allowed = list(tenants.DEFAULT_SCOPES)
        requested = str(claims.get("scope", "")).split()
        scopes = [scope for scope in tenants.SUPPORTED_SCOPES if scope in requested and scope in allowed]

        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or "keycloak"),
            scopes=scopes,
            expires_at=int(claims["exp"]) if claims.get("exp") else None,
            resource=self.public_url,
            subject=str(claims.get("sub")),
            claims={
                "iss": self.issuer,
                general.MCP_USER_ID_CLAIM: mcp_user_id,
                general.AUTH_METHOD_CLAIM: AUTH_METHOD_KEYCLOAK,
            },
        )

    async def verify_token(self, token: str):
        """Called by the SDK for every request; the work runs on a worker thread."""
        import anyio

        return await anyio.to_thread.run_sync(self.verify_token_sync, token)


def _http_get_json(url: str) -> dict:
    """Fetches a JSON document."""
    import httpx

    response = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=False)
    response.raise_for_status()

    return response.json()


def _http_post_json(url: str, data: dict, auth) -> dict:
    """Posts a form with Basic client authentication and returns the JSON answer."""
    import httpx

    response = httpx.post(url, data=data, auth=auth, timeout=_HTTP_TIMEOUT,
                          follow_redirects=False)
    response.raise_for_status()

    return response.json()


def check_issuer(issuer: str, http_get=None) -> dict:
    """Fetches an issuer's discovery document, to check it before saving it.

    Returns:
        The discovery document.

    Raises:
        Exception: If it cannot be fetched or names another issuer.
    """
    get = http_get or _http_get_json
    document = get(f"{str(issuer).rstrip('/')}/.well-known/openid-configuration")
    if str(document.get("issuer", "")).rstrip("/") != str(issuer).rstrip("/"):
        raise ValueError(f"'{issuer}' publishes the issuer '{document.get('issuer')}'.")
    if not document.get("jwks_uri"):
        raise ValueError(f"'{issuer}' publishes no jwks_uri.")

    return document
