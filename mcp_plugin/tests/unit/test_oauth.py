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

"""OAuth2 for a multi-tenant server: groundwork, Keycloak and the built-in server.

* The groundwork (2a): tools listed by scope, the 403 for a token granting no
  tool, the default role, Protected Resource Metadata.
* Keycloak (2b), against a local stand-in for a realm: a tiny HTTP server
  publishing an OpenID configuration and a JWKS, and tokens signed with keys
  made here - so every refusal can be provoked, including a key rotation.
* The built-in authorization server (2c), in-process with a stand-in for the
  database session, and end to end (2d): a real server and real MariaDB
  accounts on the shared sandbox, driven the way Arcade drives one - a
  confidential client registered in two steps, discovery from Protected
  Resource Metadata alone, Basic client authentication - and once more with the
  MCP SDK's own OAuth client.
"""

# cSpell:ignore mysqlsh MariaDB Keycloak jwks kid cimd urlsafe httpx

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx2
import jwt
import mysqlsh
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.mcpserver.exceptions import ToolError

import mcp_plugin.tests.unit.helpers as helpers
from mcp_plugin.lib import (
    auth,
    config,
    db_functions,
    general,
    oauth_builtin,
    oauth_config,
    oauth_keycloak,
    server,
    setup_cli,
    setup_oauth,
    tenants,
)

PUBLIC_URL = "https://mcp.example.com/mcp"
LOGIN_SERVER = "mariadb://db.example.com:3306"


def _context(user, scopes=tenants.SUPPORTED_SCOPES, grant=""):
    """A tool's request context, authenticated as a user."""
    token = SimpleNamespace(
        scopes=list(scopes),
        claims={general.MCP_USER_ID_CLAIM: user, general.GRANT_CLAIM: grant},
    )
    request = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.10", port=1),
        headers={general.MCP_SESSION_ID_HEADER: "a" * 32},
        scope={"user": SimpleNamespace(access_token=token)},
    )

    return SimpleNamespace(request_context=SimpleNamespace(request=request))


# --- 2a: the groundwork --------------------------------------------------------------


def test_tools_are_listed_by_scope(tenant_config):
    """A token with mcp:db sees the db tools, one without it none - and no one, none.

    Pins the SDK's private _handle_list_tools hook the scoped server overrides.
    """
    from mcp_plugin.lib import server

    tenants.set_multi_tenant(True)
    general.set_multi_tenant(True)
    ada = tenants.add_user([tenants.parse_identity("ada")])
    mcp_server = server.build_mcp_server(["db"], auth=auth.build_auth(mode="none"))

    async def listed(scopes):
        ctx = _context(ada, scopes).request_context
        result = await mcp_server._handle_list_tools(ctx, None)
        return {tool.name.split(".")[0] for tool in result.tools}

    assert asyncio.run(listed(["mcp:db"])) == {"db"}
    assert asyncio.run(listed(["openid"])) == set()

    async def anonymous():
        request = SimpleNamespace(scope={}, headers={}, client=None)
        return (await mcp_server._handle_list_tools(
            SimpleNamespace(request=request), None)).tools

    assert asyncio.run(anonymous()) == []


def test_a_token_granting_no_tool_gets_a_403():
    """With the scopes it would need, so a client can go and ask for them."""
    sent = []

    async def app(scope, receive, send):
        sent.append("app")

    async def send(message):
        sent.append(message)

    middleware = auth.InsufficientScopeMiddleware(app, "https://x/.well-known/prm")
    no_tools = SimpleNamespace(access_token=SimpleNamespace(scopes=["openid"]))
    some = SimpleNamespace(access_token=SimpleNamespace(scopes=["mcp:db"]))

    asyncio.run(middleware({"type": "http", "user": no_tools}, None, send))
    start = sent[0]
    assert start["status"] == 403
    challenge = dict(start["headers"])[b"www-authenticate"].decode()
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="mcp:db"' in challenge
    assert "resource_metadata=" in challenge

    sent.clear()
    asyncio.run(middleware({"type": "http", "user": some}, None, send))
    assert sent == ["app"]


def test_a_users_default_role_is_set_on_every_session(tenant_config):
    """And a role that cannot be set ends in a clear error, the session closed."""
    ada = tenants.add_user([tenants.parse_identity("ada")])

    class Session:
        def __init__(self, fail=False):
            self.sql, self.closed, self.fail = [], False, fail

        def run_sql(self, sql):
            self.sql.append(sql)
            if self.fail:
                raise RuntimeError("ER_INVALID_ROLE")

        def close(self):
            self.closed = True

    session = Session()
    db_functions._apply_default_role(session, ada)
    assert session.sql == []

    tenants.set_default_role(ada, "ana`lyst")
    db_functions._apply_default_role(session, ada)
    assert session.sql == ["SET ROLE `ana``lyst`"]

    failing = Session(fail=True)
    with pytest.raises(ToolError, match="could not be set"):
        db_functions._apply_default_role(failing, ada)
    assert failing.closed


def test_the_verifier_chain_dispatches_on_the_tokens_shape():
    """An API key never reaches the OAuth verifier, nor a JWT the key verifier."""
    seen = []

    class Keys(auth.ApiKeyVerifier):
        def __init__(self):
            pass

        async def verify_token(self, token):
            seen.append(("keys", token))

    class Other:
        async def verify_token(self, token):
            seen.append(("other", token))

    chain = auth.CompositeVerifier([Keys(), Other()])
    key = f"{tenants.API_KEY_PREFIX}{uuid.uuid4().hex}_{'a' * 43}"
    asyncio.run(chain.verify_token(key))
    asyncio.run(chain.verify_token("eyJ.a.b"))

    assert seen == [("keys", key), ("other", "eyJ.a.b")]


def test_the_public_url_is_canonical_and_https(tenant_config):
    """Lower-case, no trailing slash, https unless loopback."""
    assert oauth_config.normalize_public_url("HTTPS://MCP.Example.com:8443/mcp/") == (
        "https://mcp.example.com:8443/mcp"
    )
    assert oauth_config.normalize_public_url("http://127.0.0.1:8080/mcp") == (
        "http://127.0.0.1:8080/mcp"
    )
    for bad in ("http://mcp.example.com/mcp", "mcp.example.com", "https://x/mcp?a=1"):
        with pytest.raises(mysqlsh.Error):
            oauth_config.normalize_public_url(bad)


def test_an_oauth_mode_needs_what_it_needs(tenant_config):
    """No public URL, no issuer, no login server: each is refused by name."""
    with pytest.raises(mysqlsh.Error, match="public URL"):
        oauth_config.check_ready(oauth_config.OAUTH_MODE_BUILTIN)

    oauth_config.set_public_url(PUBLIC_URL)
    with pytest.raises(mysqlsh.Error, match="--addLoginServer"):
        oauth_config.check_ready(oauth_config.OAUTH_MODE_BUILTIN)
    with pytest.raises(mysqlsh.Error, match="issuer"):
        oauth_config.check_ready(oauth_config.OAUTH_MODE_KEYCLOAK)


def test_the_start_servers_public_url_counts_as_configured(tenant_config):
    """mcp start-server --publicUrl stands in for one never set with setup-oauth."""
    with pytest.raises(mysqlsh.Error, match="--addLoginServer"):
        oauth_config.check_ready(oauth_config.OAUTH_MODE_BUILTIN, PUBLIC_URL)


def test_every_option_an_oauth_refusal_names_exists(tenant_config):
    """What a refusal tells the administrator to run is an option they can run."""
    def refusal(call, *args):
        with pytest.raises(mysqlsh.Error) as refused:
            call(*args)
        return str(refused.value)

    messages = [refusal(oauth_config.check_ready, oauth_config.OAUTH_MODE_BUILTIN)]
    oauth_config.set_public_url(PUBLIC_URL)
    messages.append(refusal(oauth_config.check_ready, oauth_config.OAUTH_MODE_BUILTIN))
    messages.append(refusal(oauth_config.check_ready, oauth_config.OAUTH_MODE_KEYCLOAK))
    oauth_config.update_oauth_settings(lambda oauth: oauth["keycloak"].update(
        issuer="https://kc.example.com/realms/r",
        verification=oauth_config.VERIFICATION_INTROSPECTION,
    ))
    messages.append(refusal(oauth_config.check_ready, oauth_config.OAUTH_MODE_KEYCLOAK))
    messages.append(refusal(oauth_config.resolve_client, "no-such-client"))

    named = set()
    for message in messages:
        assert "mcp setup-oauth --" in message, message
        assert "mcp setup --" not in message, message
        named.update(re.findall(r"--([a-zA-Z]+)", message))
    assert named == {"publicUrl", "addLoginServer", "issuer", "introspectionClientId",
                     "introspectionSecretEnv", "listClients"}
    for option in named:
        assert re.sub(r"[A-Z]", lambda m: "_" + m.group().lower(), option) in (
            setup_oauth.KNOWN_OPTIONS
        )


def test_a_server_that_creates_users_at_sign_in_starts_without_any(tenant_config):
    """The first sign-in adds the first user, and that needs the server running."""
    tenants.set_multi_tenant(True)
    oauth_config.set_public_url(PUBLIC_URL)
    oauth_config.set_mode(oauth_config.OAUTH_MODE_BUILTIN)

    server._check_multi_tenant("streamable-http", ["db"], False)

    oauth_config.update_oauth_settings(
        lambda oauth: oauth["builtin"]["autoProvision"].update(enabled=False)
    )
    with pytest.raises(mysqlsh.Error, match="no enabled user"):
        server._check_multi_tenant("streamable-http", ["db"], False)


def test_client_networks_are_checked_against_the_peer():
    """The listed networks only, loopback included where listed."""
    networks = oauth_config.normalize_networks("192.0.2.0/24, 127.0.0.0/8")

    assert oauth_config.address_allowed("192.0.2.77", networks)
    assert oauth_config.address_allowed(general.LOOPBACK_ADDRESS, networks)
    assert not oauth_config.address_allowed("198.51.100.1", networks)
    assert oauth_config.address_allowed("198.51.100.1", [])
    with pytest.raises(mysqlsh.Error):
        oauth_config.normalize_networks("not-a-network")


def test_basic_client_authentication_becomes_the_form():
    """Either way OAuth allows, the SDK sees the credentials in the form."""
    received = {}

    async def app(scope, receive, send):
        message = await receive()
        received["headers"] = dict(scope["headers"])
        received["body"] = message["body"]

    middleware = auth.BasicClientAuthMiddleware(app)
    basic = base64.b64encode(b"client%201:s3cret").decode()

    async def receive():
        return {"type": "http.request", "body": b"grant_type=refresh_token&refresh_token=x",
                "more_body": False}

    asyncio.run(middleware({"type": "http", "path": "/token", "headers": [
        (b"authorization", f"Basic {basic}".encode())]}, receive, None))

    assert b"authorization" not in received["headers"]
    form = dict(pair.split("=", 1) for pair in received["body"].decode().split("&"))
    assert form["client_id"] == "client+1" and form["client_secret"] == "s3cret"


# --- 2b: Keycloak ----------------------------------------------------------------------


class _Realm:
    """A stand-in for a Keycloak realm: discovery, JWKS and token signing."""

    def __init__(self):
        self.keys = [self._new_key()]
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.issuer = f"http://127.0.0.1:{self.server.server_port}/realms/test"
        self.jwks_fetches = 0
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def _new_key():
        return (secrets.token_hex(4), rsa.generate_private_key(public_exponent=65537, key_size=2048))

    def rotate(self):
        self.keys.append(self._new_key())

    def _handler(self):
        realm = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.endswith("/.well-known/openid-configuration"):
                    body = {"issuer": realm.issuer, "jwks_uri": realm.issuer + "/certs",
                            "introspection_endpoint": realm.issuer + "/introspect"}
                elif self.path.endswith("/certs"):
                    realm.jwks_fetches += 1
                    body = {"keys": [
                        {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
                         "kid": kid, "alg": "RS256", "use": "sig"}
                        for kid, key in realm.keys
                    ]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def token(self, key_index=-1, **overrides):
        kid, key = self.keys[key_index]
        now = int(time.time())
        claims = {
            "iss": self.issuer, "sub": "kc-sub-1", "aud": [PUBLIC_URL, "account"],
            "azp": "claude-code", "typ": "Bearer", "iat": now, "exp": now + 300,
            # mcp:msm, which a realm prepared before multi-tenant mode dropped
            # the msm group still grants, is a scope this server ignores.
            "scope": "openid mcp:db mcp:msm", "email": "ada@example.com",
            "email_verified": True, "realm_access": {"roles": ["mcp-user"]},
            "name": "Ada Lovelace",
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}

        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

    def close(self):
        self.server.shutdown()


@pytest.fixture
def realm():
    stand_in = _Realm()
    try:
        yield stand_in
    finally:
        stand_in.close()


def _keycloak(realm, **settings):
    merged = oauth_config.get_oauth_settings()["keycloak"]
    merged.update({"issuer": realm.issuer}, **settings)
    return oauth_keycloak.KeycloakVerifier(merged, PUBLIC_URL, auth.UserDirectory())


def test_a_keycloak_token_creates_its_user_at_first_sign_in(tenant_config, realm):
    """The realm role allows it; the verified email becomes an identity too."""
    verifier = _keycloak(realm)

    token = verifier.verify_token_sync(realm.token())

    ada = tenants.find_user("oauth:" + realm.issuer + "|kc-sub-1")
    assert token.claims[general.MCP_USER_ID_CLAIM] == ada
    assert tenants.find_user("ada@example.com") == ada
    assert token.scopes == ["mcp:db"]
    assert token.resource == PUBLIC_URL
    assert token.client_id == "claude-code"


def test_a_keycloak_sign_in_is_linked_by_verified_email(tenant_config, realm):
    """An administrator's user gets the identity; an unverified email does not."""
    ada = tenants.add_user([tenants.parse_identity("ada@example.com")], scopes="mcp:db")
    verifier = _keycloak(realm, autoProvision={"enabled": False})

    assert verifier.verify_token_sync(realm.token(email_verified=False)) is None
    assert tenants.find_user("oauth:" + realm.issuer + "|kc-sub-1") is None

    token = verifier.verify_token_sync(realm.token())
    assert token.claims[general.MCP_USER_ID_CLAIM] == ada
    assert token.scopes == ["mcp:db"]
    assert tenants.find_user("oauth:" + realm.issuer + "|kc-sub-1") == ada


def test_keycloak_without_the_realm_role_creates_nobody(tenant_config, realm):
    verifier = _keycloak(realm)

    assert verifier.verify_token_sync(realm.token(realm_access={"roles": []})) is None
    assert tenants.read_users() == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "http://elsewhere/realms/test"},
        {"aud": ["https://other.example.com/mcp"]},
        {"aud": None},
        {"exp": int(time.time()) - 3600},
        {"typ": "ID"},
        {"azp": "some-other-client"},
    ],
)
def test_a_keycloak_token_that_is_not_for_this_server_is_refused(tenant_config, realm,
                                                                 overrides):
    verifier = _keycloak(realm, clientIds=["claude-code"])

    assert verifier.verify_token_sync(realm.token(**overrides)) is None


def test_only_asymmetric_signatures_are_accepted(tenant_config, realm):
    """alg none, and HS256 keyed with the public key, are both forged tokens."""
    verifier = _keycloak(realm)
    claims = jwt.decode(realm.token(), options={"verify_signature": False})
    kid = realm.keys[0][0]

    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": kid})
    public_pem = realm.keys[0][1].public_key().public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).Encoding.PEM,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).PublicFormat.SubjectPublicKeyInfo,
    )
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    import hmac as hmac_module

    signature = base64.urlsafe_b64encode(
        hmac_module.new(public_pem, header + b"." + payload, hashlib.sha256).digest()
    ).rstrip(b"=")
    hs256 = (header + b"." + payload + b"." + signature).decode()

    assert verifier.verify_token_sync(unsigned) is None
    assert verifier.verify_token_sync(hs256) is None


def test_a_rotated_realm_key_is_followed(tenant_config, realm):
    """A token signed with a key the cache has not seen refetches the JWKS once."""
    verifier = _keycloak(realm)
    assert verifier.verify_token_sync(realm.token()) is not None
    fetches = realm.jwks_fetches

    realm.rotate()
    # Within PyJWT's 30s refetch cooldown an unknown kid is refused...
    assert verifier.verify_token_sync(realm.token(key_index=-1)) is None
    assert realm.jwks_fetches == fetches
    # ...after it, the keys are fetched again and the new one is found.
    verifier._jwk_client._last_successful_fetch -= 31
    assert verifier.verify_token_sync(realm.token(key_index=-1)) is not None
    assert realm.jwks_fetches == fetches + 1


def test_a_disabled_or_revoked_keycloak_user_is_refused(tenant_config, realm):
    verifier = _keycloak(realm)
    first = realm.token(iat=int(time.time()) - 10)
    assert verifier.verify_token_sync(first) is not None
    ada = tenants.find_user("ada@example.com")

    tenants.revoke_tokens(ada)
    assert verifier.verify_token_sync(first) is None

    tenants.set_disabled(ada, True)
    assert verifier.verify_token_sync(realm.token(iat=int(time.time()) + 5)) is None


def test_keycloak_introspection_asks_keycloak_and_caches(tenant_config, realm):
    """An inactive token is refused; one answer is reused for 30s."""
    calls = []

    def post(url, data, auth_pair):
        calls.append((url, auth_pair))
        return {"active": data["token"] == "good", "iss": realm.issuer, "sub": "kc-sub-1",
                "aud": PUBLIC_URL, "scope": "mcp:db", "exp": int(time.time()) + 60,
                "iat": int(time.time()), "email": "x@example.com", "email_verified": True,
                "realm_access": {"roles": ["mcp-user"]}}

    oauth_config.set_introspection_secret("isecret")
    settings = oauth_config.get_oauth_settings()["keycloak"]
    settings.update(issuer=realm.issuer, verification="introspection",
                    introspectionClientId="mcp-introspector")
    verifier = oauth_keycloak.KeycloakVerifier(settings, PUBLIC_URL, auth.UserDirectory(),
                                               http_post=post)

    assert verifier.verify_token_sync("good").scopes == ["mcp:db"]
    assert verifier.verify_token_sync("good") is not None
    assert verifier.verify_token_sync("bad") is None
    assert len(calls) == 2
    assert calls[0][1] == ("mcp-introspector", "isecret")


def test_setup_checks_a_keycloak_issuer(tenant_config, realm):
    """--issuer reads the OpenID configuration; --noVerify skips it."""
    setup_cli.apply({"multi_tenant": True})
    setup_oauth.apply({"public_url": PUBLIC_URL, "mode": "keycloak",
                     "issuer": realm.issuer})
    assert oauth_config.get_oauth_settings()["keycloak"]["issuer"] == realm.issuer

    with pytest.raises(mysqlsh.Error, match="Could not read the OpenID configuration"):
        setup_oauth.apply({"issuer": "http://127.0.0.1:9/realms/none"})

    bundle = auth.build_auth()
    assert isinstance(bundle.token_verifier, auth.CompositeVerifier)
    assert str(bundle.settings.issuer_url) == realm.issuer


# --- 2c: the built-in authorization server, in-process ---------------------------------


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetch_one(self):
        return self.rows[0]

    def fetch_all(self):
        return self.rows


class _Account:
    """A database session as a sign-in sees it: who, and which roles."""

    def __init__(self, account="ada@%", roles=(("ada@%", "mcp_access", "YES"),)):
        self.account, self.roles = account, list(roles)

    def run_sql(self, sql):
        if "CURRENT_USER" in sql:
            return _Rows([(self.account,)])
        return _Rows(self.roles)

    def close(self):
        pass


def _provider(open_session=None, **builtin):
    """A built-in provider over the tenant_config's settings."""
    oauth_config.set_public_url(PUBLIC_URL)
    settings = oauth_config.get_oauth_settings()["builtin"]
    settings.update({"loginServers": [LOGIN_SERVER]}, **builtin)

    def default_open(data):
        if data.get("password") != "pw":
            raise RuntimeError("Access denied")
        return _Account()

    return oauth_builtin.BuiltinAuthProvider(
        settings, PUBLIC_URL, auth.ApiKeyVerifier(resource=PUBLIC_URL),
        open_session=open_session or default_open,
    )


def _sign_in(provider, client_id, username="ada", password="pw", scopes=None,
             address="192.0.2.10"):
    params = SimpleNamespace(
        resource=PUBLIC_URL, redirect_uri="http://127.0.0.1:5555/callback",
        redirect_uri_provided_explicitly=True, code_challenge="c" * 43,
        scopes=scopes, state="st",
    )
    client = SimpleNamespace(client_id=client_id, client_name="Test")
    location = asyncio.run(provider.authorize(client, params))
    request_id = location.split("req=")[1]
    pending = provider._pending_request(request_id)

    return provider.sign_in(pending, request_id, address, LOGIN_SERVER, username, password,
                            pending["scopes"])


def _redeem(provider, client_id, location):
    code = dict(p.split("=", 1) for p in location.split("?", 1)[1].split("&"))["code"]
    client = SimpleNamespace(client_id=client_id)
    stored = asyncio.run(provider.load_authorization_code(client, code))

    return asyncio.run(provider.exchange_authorization_code(client, stored))


def _client(**kwargs):
    client_id, _ = oauth_config.add_client("test", redirect_uris=["http://127.0.0.1/callback"],
                                           **kwargs)
    return client_id


def test_a_sign_in_issues_tokens_for_the_accounts_user(tenant_config):
    """The account becomes a user; the token names them and the grant."""
    provider = _provider()
    client_id = _client()

    location = _sign_in(provider, client_id)
    assert "iss=https%3A%2F%2Fmcp.example.com" in location
    tokens = _redeem(provider, client_id, location)

    access = provider.verify_access_token(tokens.access_token)
    user = tenants.find_user(f"mariadb:{LOGIN_SERVER}|ada@%")
    assert access.claims[general.MCP_USER_ID_CLAIM] == user
    assert access.scopes == ["mcp:db"]
    grant = access.claims[general.GRANT_CLAIM]
    uri, password = provider.live_credentials(user, grant)
    assert password == "pw" and "ada@db.example.com" in uri and "ssl-mode=REQUIRED" in uri


def test_a_wrong_password_reads_the_same_and_is_rate_limited(tenant_config):
    provider = _provider()
    client_id = _client()

    for _ in range(oauth_builtin._LOGIN_MAX_FAILURES_PER_ACCOUNT):
        with pytest.raises(oauth_builtin.SignInError, match="not correct"):
            _sign_in(provider, client_id, password="nope")
    with pytest.raises(oauth_builtin.SignInError, match="Too many"):
        _sign_in(provider, client_id)
    # Another account on the same address still gets to try.
    with pytest.raises(oauth_builtin.SignInError, match="not correct"):
        _sign_in(provider, client_id, username="bob", password="nope")


def test_the_role_rules_hold_at_sign_in(tenant_config):
    """Required role (nested counts), a default role (direct only), client roles."""
    nested_only = [("mcp_access", "analyst", None), ("ada@%", "mcp_access", "YES")]
    provider = _provider(open_session=lambda data: _Account(roles=nested_only),
                         requiredRole="analyst")
    client_id = _client()
    _sign_in(provider, client_id)
    ada = tenants.find_user(f"mariadb:{LOGIN_SERVER}|ada@%")

    tenants.set_default_role(ada, "analyst")
    with pytest.raises(oauth_builtin.SignInError, match="does not have"):
        _sign_in(provider, client_id)
    tenants.set_default_role(ada, "")

    oauth_config.set_allowed_roles(client_id, "reporting")
    with pytest.raises(oauth_builtin.SignInError, match="only be used with the roles"):
        _sign_in(provider, client_id)

    lacking = _provider(open_session=lambda data: _Account(account="bob@%", roles=[]),
                        requiredRole="analyst")
    with pytest.raises(oauth_builtin.SignInError, match="not allowed"):
        _sign_in(lacking, client_id, username="bob")
    assert tenants.find_user(f"mariadb:{LOGIN_SERVER}|bob@%") is None


def test_several_accounts_belong_to_one_user_when_linked(tenant_config):
    """An administrator links them; a sign-in never does it by itself."""
    provider = _provider()
    client_id = _client()
    _sign_in(provider, client_id)
    ada = tenants.find_user(f"mariadb:{LOGIN_SERVER}|ada@%")
    tenants.add_identity(ada, tenants.parse_identity(f"mariadb:{LOGIN_SERVER}|ada_admin@%"))

    admin = _provider(open_session=lambda data: _Account(account="ada_admin@%"))
    tokens = _redeem(admin, client_id, _sign_in(admin, client_id, username="ada_admin"))

    assert admin.verify_access_token(tokens.access_token).claims[general.MCP_USER_ID_CLAIM] == ada


def test_refresh_tokens_rotate_and_a_reuse_ends_the_grant(tenant_config):
    """Within the grace period a race gets the same answer; after it, the end."""
    provider = _provider(refreshGracePeriod=30)
    client_id = _client()
    tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
    client = SimpleNamespace(client_id=client_id)

    def refresh(token):
        loaded = asyncio.run(provider.load_refresh_token(client, token))
        if loaded is None:
            return None
        return asyncio.run(provider.exchange_refresh_token(client, loaded, loaded.scopes))

    second = refresh(tokens.refresh_token)
    assert second.refresh_token != tokens.refresh_token
    assert refresh(tokens.refresh_token).refresh_token == second.refresh_token

    forged = second.refresh_token[:-4] + "AAAA"
    assert refresh(forged) is None
    assert provider.verify_access_token(second.access_token) is not None

    provider.settings["refreshGracePeriod"] = 0
    provider._recent_refreshes.clear()
    third = refresh(second.refresh_token)
    assert refresh(second.refresh_token) is None
    assert provider.verify_access_token(third.access_token) is None


def test_a_signed_in_user_has_the_scopes_they_may_have_now(tenant_config):
    """--setScopes reaches a live token at once and every refresh after it."""
    provider = _provider()
    client_id = _client()
    client = SimpleNamespace(client_id=client_id)
    tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
    ada = tenants.find_user(f"mariadb:{LOGIN_SERVER}|ada@%")

    tenants.set_scopes(ada, ["mcp:db"])
    assert provider.verify_access_token(tokens.access_token).scopes == ["mcp:db"]

    loaded = asyncio.run(provider.load_refresh_token(client, tokens.refresh_token))
    refreshed = asyncio.run(provider.exchange_refresh_token(client, loaded, loaded.scopes))
    assert refreshed.scope == "mcp:db"
    assert jwt.decode(refreshed.access_token, options={"verify_signature": False})[
        "scope"] == "mcp:db"

    # Widening the user again does not widen what was consented to.
    tenants.set_scopes(ada, list(tenants.SUPPORTED_SCOPES))
    assert provider.verify_access_token(refreshed.access_token).scopes == ["mcp:db"]

    # A grant left with none of its scopes lives on, good for no tool, until the
    # user may have one of them again: scopes filter, --revokeTokens revokes.
    only_db = _redeem(provider, client_id, _sign_in(provider, client_id, scopes=["mcp:db"]))
    grant = provider.verify_access_token(only_db.access_token).claims[general.GRANT_CLAIM]
    tenants.set_scopes(ada, [])
    assert provider.verify_access_token(only_db.access_token).scopes == []
    assert provider.live_grant(ada, grant) is not None
    loaded = asyncio.run(provider.load_refresh_token(client, only_db.refresh_token))
    empty = asyncio.run(provider.exchange_refresh_token(client, loaded, loaded.scopes))
    assert provider.verify_access_token(empty.access_token).scopes == []
    tenants.set_scopes(ada, ["mcp:db"])
    assert provider.verify_access_token(only_db.access_token).scopes == ["mcp:db"]


def test_a_token_check_reads_the_clients_file_only_when_it_changed(tenant_config,
                                                                    monkeypatch):
    """A stat per request; a removal by mcp setup-oauth is still seen at once."""
    provider = _provider()
    client_id = _client()
    tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
    reads = []
    read_clients = oauth_config.read_clients
    monkeypatch.setattr(oauth_config, "read_clients",
                        lambda: reads.append(1) or read_clients())

    for _ in range(3):
        assert provider.verify_access_token(tokens.access_token) is not None
    assert reads == []

    oauth_config.remove_client(client_id)
    assert provider.verify_access_token(tokens.access_token) is None


def test_the_password_lives_exactly_as_long_as_the_grant(tenant_config):
    """In both stores: found while the grant lives, gone after each way it ends."""
    for store in oauth_config.LOGIN_CONNECTION_STORES:
        provider = _provider(loginConnectionStore=store)
        client_id = _client()
        sentinel = f"pw-{store}"

        def open_with_sentinel(data, sentinel=sentinel):
            assert data["password"] == sentinel
            return _Account()

        provider._open_session = open_with_sentinel
        tokens = _redeem(provider, client_id,
                         _sign_in(provider, client_id, password=sentinel))
        access = provider.verify_access_token(tokens.access_token)
        user, grant = access.claims[general.MCP_USER_ID_CLAIM], access.claims[general.GRANT_CLAIM]

        def in_secret_store():
            keys = tenants.list_groups().get(user, [])
            return oauth_builtin.LOGIN_CONNECTION_SECRET_PREFIX + grant in keys

        assert provider.live_credentials(user, grant)[1] == sentinel
        assert in_secret_store() == (store == oauth_config.LOGIN_CONNECTION_STORE_SECRET_STORE)

        asyncio.run(provider.revoke_token(access))

        assert provider.live_credentials(user, grant) is None
        assert not in_secret_store()
        assert provider.verify_access_token(tokens.access_token) is None


def test_a_grant_ends_with_its_lifetime_its_user_or_its_client(tenant_config):
    provider = _provider(grantMaxLifetime=3600, grantIdleTimeout=600)
    client_id = _client()

    def new_grant():
        tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
        access = provider.verify_access_token(tokens.access_token)
        return access.claims[general.MCP_USER_ID_CLAIM], access.claims[general.GRANT_CLAIM]

    user, grant = new_grant()
    record = provider.store.get(user, grant)
    record["lastRefreshed"] -= 601
    provider.store.update(record)
    assert provider.sweep() == 1
    assert provider.live_grant(user, grant) is None

    user, grant = new_grant()
    record = provider.store.get(user, grant)
    record["created"] -= 3601
    provider.store.update(record)
    assert provider.live_grant(user, grant) is None

    user, grant = new_grant()
    tenants.revoke_tokens(user)
    assert provider.live_grant(user, grant) is None
    tenants.set_disabled(user, False)

    user, grant = new_grant()
    oauth_config.remove_client(client_id)
    assert provider.live_grant(user, grant) is None


def test_an_unredeemed_code_ends_its_grant(tenant_config):
    provider = _provider()
    client_id = _client()
    _sign_in(provider, client_id)
    assert len(provider.store.all()) == 1

    for entry in provider._codes.values():
        entry["expires_at"] = 0
    provider.sweep()

    assert provider.store.all() == []


def test_setup_ends_the_grants_of_a_removed_client_and_a_revoked_user(tenant_config):
    """Offline, from the secret store, as mcp setup does it in another process."""
    provider = _provider()
    client_id = _client()
    _sign_in(provider, client_id)
    _sign_in(provider, client_id)
    user = tenants.find_user(f"mariadb:{LOGIN_SERVER}|ada@%")

    assert oauth_builtin.end_grants_of_user(user) == 2
    _sign_in(provider, client_id)
    assert oauth_builtin.end_grants_of_client(client_id) == 1
    assert oauth_builtin.stored_grants() == []


def test_the_authorization_request_is_lenient_where_it_safely_can_be(tenant_config):
    """A missing resource is this server; another is refused; unknown scopes drop."""
    from mcp.server.auth.provider import AuthorizeError

    provider = _provider()
    client = provider.get_client_sync(_client())
    params = SimpleNamespace(resource=None, redirect_uri="http://127.0.0.1:1/callback",
                             redirect_uri_provided_explicitly=True, code_challenge="c",
                             scopes=None, state=None)

    assert "/login?req=" in asyncio.run(provider.authorize(client, params))
    params.resource = "https://other.example.com/mcp"
    with pytest.raises(AuthorizeError) as refused:
        asyncio.run(provider.authorize(client, params))
    assert refused.value.error == "invalid_target"

    assert client.validate_scope("session:role:all mcp:db openid") == ["mcp:db"]
    assert client.validate_scope("session:role:all") is None


def test_unauthenticated_requests_cannot_grow_the_server(tenant_config, monkeypatch):
    """Pending sign-ins and cached metadata documents are bounded; the oldest go."""
    monkeypatch.setattr(oauth_builtin, "_MAX_PENDING", 3)
    monkeypatch.setattr(oauth_builtin, "_MAX_CIMD_CACHE", 2)
    provider = _provider()
    client = provider.get_client_sync(_client())
    params = SimpleNamespace(
        resource=None, redirect_uri="http://127.0.0.1/callback",
        redirect_uri_provided_explicitly=True, code_challenge="c" * 43,
        scopes=None, state="st",
    )

    requests = [asyncio.run(provider.authorize(client, params)).split("req=")[1]
                for _ in range(5)]
    assert len(provider._pending) == 3
    assert provider._pending_request(requests[0]) is None
    assert provider._pending_request(requests[-1]) is not None

    provider._cimd_fetch = lambda url: {
        "metadata": {"client_id": url, "redirect_uris": ["http://127.0.0.1/cb"]},
        "max_age": 300,
    }
    for index in range(3):
        provider.get_client_sync(f"https://client{index}.example.com/c.json")
    assert list(provider._cimd_cache) == [
        "https://client1.example.com/c.json", "https://client2.example.com/c.json"]


def test_one_address_can_only_start_so_many_sign_ins():
    """Past the limit a 429; other addresses and other paths are not affected."""
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 302, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limit = auth.AuthorizeRateLimit(app, max_requests=3)

    async def status(path, address):
        sent = []

        async def send(message):
            sent.append(message)

        await limit({"type": "http", "path": path, "client": (address, 1), "headers": []},
                    None, send)
        return sent[0]["status"]

    async def scenario():
        assert [await status("/authorize", "192.0.2.1") for _ in range(4)] == [
            302, 302, 302, 429]
        assert await status("/authorize", "192.0.2.2") == 302
        assert await status("/login", "192.0.2.1") == 302
        # Refused requests do not count: the address is free again after the window.
        limit._requests.window = 0
        assert await status("/authorize", "192.0.2.1") == 302

    asyncio.run(scenario())
    assert seen.count("/authorize") == 5


def test_the_builtin_server_limits_sign_in_starts(tenant_config):
    """The limit is in front of the SDK's /authorize of a built-in server."""
    oauth_config.set_public_url(PUBLIC_URL)
    oauth_config.update_oauth_settings(
        lambda oauth: oauth["builtin"].update(loginServers=[LOGIN_SERVER])
    )
    bundle = auth.build_auth(mode=oauth_config.OAUTH_MODE_BUILTIN)
    mcp_server = server.build_mcp_server(["db"], auth=bundle)
    app = auth.customize_app(mcp_server.streamable_http_app(host="127.0.0.1"), bundle)
    client_id = _client()
    query = {"client_id": client_id, "response_type": "code",
             "redirect_uri": "http://127.0.0.1/callback", "code_challenge": "c" * 43,
             "code_challenge_method": "S256", "state": "st"}

    async def starts(address, count):
        transport = httpx2.ASGITransport(app=app, client=(address, 1234))
        async with httpx2.AsyncClient(transport=transport,
                                      base_url="https://mcp.example.com") as client:
            return [(await client.get("/authorize", params=query)).status_code
                    for _ in range(count)]

    first = asyncio.run(starts("192.0.2.1", 31))
    assert first == [302] * 30 + [429]
    assert asyncio.run(starts("192.0.2.2", 1)) == [302]


def test_a_loopback_redirect_may_use_any_port(tenant_config):
    """Native clients pick a free port per sign-in; other hosts match exactly."""
    from mcp.shared.auth import InvalidRedirectUriError
    from pydantic import AnyUrl

    provider = _provider()
    client = provider.get_client_sync(_client())

    assert client.validate_redirect_uri(AnyUrl("http://127.0.0.1:61234/callback"))
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl("http://127.0.0.1:61234/other"))
    with pytest.raises(InvalidRedirectUriError):
        client.validate_redirect_uri(AnyUrl("http://evil.example.com/callback"))


def test_a_client_metadata_document_is_fetched_with_care(tenant_config):
    """https, a public address, its own URL as client_id, small, no secret."""
    url = "https://client.example.com/oauth/client.json"
    document = {"client_id": url, "client_name": "Example",
                "redirect_uris": ["http://127.0.0.1/cb"]}

    def get(doc, status=200, headers=None):
        return lambda u, addresses, limit, timeout: (status, headers or {}, json.dumps(doc).encode())

    public = lambda host: ["93.184.216.34"]  # noqa: E731
    fetched = oauth_builtin.fetch_client_metadata(
        url, resolve=public, http_get=get(document, headers={"cache-control": "max-age=10"}))
    assert fetched["metadata"]["client_name"] == "Example"
    assert fetched["max_age"] == oauth_builtin._CIMD_MIN_CACHE

    # The fetch is told the addresses that were checked, so it never resolves again.
    connected = []
    oauth_builtin.fetch_client_metadata(
        url, resolve=public,
        http_get=lambda u, addresses, limit, timeout: connected.append(addresses) or (
            200, {}, json.dumps(document).encode()))
    assert connected == [["93.184.216.34"]]

    for bad_url, resolve, http_get in (
        ("http://client.example.com/c.json", public, get(document)),
        ("https://client.example.com/", public, get(document)),
        (url, lambda host: ["10.0.0.5"], get(document)),
        (url, lambda host: ["127.0.0.1"], get(document)),
        (url, lambda host: ["169.254.169.254"], get(document)),
        (url, public, get({**document, "client_id": "https://evil/c.json"})),
        (url, public, get({**document, "token_endpoint_auth_method": "client_secret_post"})),
        (url, public, get(document, status=302)),
        (url, public, lambda u, addresses, limit, timeout: (200, {}, b"x" * (limit + 1))),
    ):
        with pytest.raises(ValueError):
            oauth_builtin.fetch_client_metadata(bad_url, resolve=resolve, http_get=http_get)

    provider = _provider()
    provider._cimd_fetch = lambda u: {"metadata": document, "max_age": 300}
    client = provider.get_client_sync(url)
    assert client.client_name == "Example" and client.token_endpoint_auth_method == "none"


def test_the_metadata_fetch_connects_to_the_checked_address():
    """Not to what the name resolves to by then: the request names the host.

    An address that does not connect is passed over for the next one: here
    ::1, which nothing listens on, before the 127.0.0.1 the server is on.
    """
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the http.server name
            seen["host"], seen["path"] = self.headers["Host"], self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    listener = HTTPServer(("127.0.0.1", 0), Handler)
    port = listener.server_address[1]
    thread = threading.Thread(target=listener.handle_request, daemon=True)
    thread.start()
    try:
        # The name does not resolve (.invalid); only the pinned address works.
        status, _, body = oauth_builtin._bounded_get(
            f"http://client.invalid:{port}/c.json", ["::1", "127.0.0.1"], 100, 5)
    finally:
        thread.join(5)
        listener.server_close()

    assert (status, body) == (200, b"{}")
    assert seen == {"host": f"client.invalid:{port}", "path": "/c.json"}


def test_dynamic_registration_is_stored_and_can_be_turned_off(tenant_config):
    from mcp.shared.auth import OAuthClientInformationFull

    provider = _provider()
    info = OAuthClientInformationFull(client_id="dyn-1", client_name="Dyn",
                                      redirect_uris=["http://127.0.0.1/cb"],
                                      token_endpoint_auth_method="none")
    asyncio.run(provider.register_client(info))

    assert oauth_config.read_clients()["dyn-1"]["registered"] == "dynamic"
    assert oauth_config.expire_unused_dynamic_clients(10**9) == []
    assert oauth_config.expire_unused_dynamic_clients(-1) == ["dyn-1"]

    bundle_settings = _provider(dynamicClientRegistration=False).routes()
    metadata_route = bundle_settings[0]
    assert metadata_route.path == "/.well-known/oauth-authorization-server"


def test_a_login_connection_is_its_grants_alone(tenant_config, monkeypatch):
    """db.list_connections shows it to its grant; another grant does not see it."""
    tenants.set_multi_tenant(True)
    general.set_multi_tenant(True)
    provider = _provider()
    client_id = _client()
    provider.activate()
    try:
        tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
        access = provider.verify_access_token(tokens.access_token)
        user, grant = access.claims[general.MCP_USER_ID_CLAIM], access.claims[general.GRANT_CLAIM]

        recorder = SimpleNamespace(tools={})
        recorder.tool = lambda name: (lambda fn: recorder.tools.setdefault(name, fn))
        opened = []
        monkeypatch.setattr(db_functions, "_open_login_session",
                            lambda uri, u, g: opened.append((uri, u, g)) or SimpleNamespace(close=lambda: None))
        db_functions.register_db_tools(recorder)
        listed = recorder.tools["db.list_connections"](_context(user, grant=grant))
        assert listed and "ada@db.example.com" in listed[0]
        assert recorder.tools["db.list_connections"](_context(user, grant="other")) == []

        connection_id = recorder.tools["db.connect"](_context(user, grant=grant), listed[0])
        assert opened == [(listed[0], user, grant)]

        asyncio.run(provider.revoke_token(access))
        assert connection_id not in db_functions._sessions
    finally:
        provider.deactivate()
        db_functions._sessions.clear()


def test_setup_registers_a_client_in_two_steps(tenant_config, capsys):
    """Arcade shows its redirect URI only after the client exists."""
    setup_cli.apply({"multi_tenant": True})
    setup_oauth.apply({"public_url": PUBLIC_URL, "mode": "builtin",
                     "add_login_server": LOGIN_SERVER})
    capsys.readouterr()
    setup_oauth.apply({"add_client": "arcade", "confidential": True, "json": True})
    added = json.loads(capsys.readouterr().out)["clients"][0]

    setup_oauth.apply({"set_client_redirect_uris": "arcade",
                     "redirect_uris": "https://cloud.arcade.dev/api/v1/oauth/callback"})
    setup_oauth.apply({"set_client_allowed_roles": added["clientId"], "roles": "mcp_access"})
    capsys.readouterr()
    setup_oauth.apply({"show_client_secret": "arcade", "json": True})
    assert json.loads(capsys.readouterr().out)["clients"][0]["clientSecret"] == added["clientSecret"]

    record = oauth_config.read_clients()[added["clientId"]]
    assert record["redirectUris"] == ["https://cloud.arcade.dev/api/v1/oauth/callback"]
    assert record["allowedRoles"] == ["mcp_access"]
    assert record["confidential"] is True

    setup_oauth.apply({"rotate_client_secret": "arcade"})
    assert oauth_config.get_client_secret(added["clientId"]) != added["clientSecret"]
    setup_oauth.apply({"remove_client": "arcade"})
    assert oauth_config.read_clients() == {}


@pytest.mark.parametrize(
    "options, message",
    [
        ({"redirect_uris": "https://x/cb"}, "only applies to"),
        ({"set_client_redirect_uris": "x"}, "needs --redirectUris"),
        ({"auto_provision": False}, "belongs to an OAuth mode"),
        ({"login_connection_store": "disk", "mode": "builtin"}, "not a store"),
        ({"add_login_server": "mariadb://root@db:3306", "mode": "builtin"}, "names a user"),
        ({"grant_max_lifetime": -1, "mode": "builtin"}, "more than 0"),
    ],
)
def test_setup_refuses_oauth_options_that_cannot_work(tenant_config, options, message):
    with pytest.raises(mysqlsh.Error, match=message):
        setup_oauth.apply(options)


def test_setup_sets_every_builtin_setting(tenant_config):
    setup_oauth.apply({"mode": "builtin",
                     "add_login_server": f"{LOGIN_SERVER},mariadb://db2:3306"})
    setup_oauth.apply({
        "remove_login_server": LOGIN_SERVER,
        "required_role": "mcp_access", "grant_max_lifetime": 86400,
        "grant_idle_timeout": 0, "access_token_lifetime": 600,
        "refresh_grace_period": 0, "login_connection_store": "memory",
        "allowed_client_networks": "192.0.2.0/24",
        "dynamic_client_registration": False, "cimd": False,
        "auto_provision": False, "default_scopes": "mcp:db",
    })
    builtin = oauth_config.get_oauth_settings()["builtin"]

    assert builtin["loginServers"] == ["mariadb://db2:3306"]
    assert builtin["requiredRole"] == "mcp_access"
    assert (builtin["grantMaxLifetime"], builtin["grantIdleTimeout"]) == (86400, None)
    assert (builtin["accessTokenLifetime"], builtin["refreshGracePeriod"]) == (600, 0)
    assert builtin["loginConnectionStore"] == "memory"
    assert builtin["allowedClientNetworks"] == ["192.0.2.0/24"]
    assert builtin["dynamicClientRegistration"] is False and builtin["cimd"] is False
    assert builtin["autoProvision"] == {"enabled": False, "defaultScopes": ["mcp:db"]}


def test_ninety_days_is_the_default_grant_lifetime(tenant_config):
    assert oauth_config.get_oauth_settings()["builtin"]["grantMaxLifetime"] == 90 * 24 * 3600
    assert oauth_config.get_oauth_settings()["builtin"]["grantIdleTimeout"] is None


# --- 2d: end to end, the way Arcade drives a server -------------------------------------


@pytest.fixture
def mariadb_accounts(sandbox):
    """Roles and accounts on the shared sandbox, as Arcade's guide sets them up."""
    shell = mysqlsh.globals.shell
    root = dict(shell.parse_uri(sandbox.uri))
    root["password"] = sandbox.password
    session = shell.open_session(root)
    statements = [
        "CREATE ROLE IF NOT EXISTS mcp_access",
        "CREATE USER IF NOT EXISTS 'oauth_ada'@'%' IDENTIFIED BY 'ada_pw'",
        "GRANT mcp_access TO 'oauth_ada'@'%'",
        "SET DEFAULT ROLE mcp_access FOR 'oauth_ada'@'%'",
        "CREATE USER IF NOT EXISTS 'oauth_bob'@'%' IDENTIFIED BY 'bob_pw'",
    ]
    for statement in statements:
        session.run_sql(statement)
    try:
        yield SimpleNamespace(port=sandbox.port)
    finally:
        for statement in ("DROP USER IF EXISTS 'oauth_ada'@'%'",
                          "DROP USER IF EXISTS 'oauth_bob'@'%'", "DROP ROLE IF EXISTS mcp_access"):
            session.run_sql(statement)
        session.close()


def _start_server(port, timeout=90):
    env = os.environ.copy()
    if env.get("MCP_COVERAGE_RC"):
        env["COVERAGE_PROCESS_START"] = env["MCP_COVERAGE_RC"]

    proc = subprocess.Popen(
        [*helpers.shell_command(), "--quiet-start=2", "--", "mcp", "start-server",
         "--transport=streamable-http", "--host=127.0.0.1", f"--port={port}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    helpers._wait_for_port("127.0.0.1", port, timeout)

    return proc


def _authorization_code_flow(client, base, client_id, redirect, username, password,
                             auth_pair=None):
    """Runs discovery, authorize, sign-in and the code exchange as a gateway would."""
    prm = client.get(base + "/.well-known/oauth-protected-resource/mcp").json()
    metadata = client.get(prm["authorization_servers"][0]
                          + "/.well-known/oauth-authorization-server").json()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    login = client.get(metadata["authorization_endpoint"], params=dict(
        response_type="code", client_id=client_id, redirect_uri=redirect,
        code_challenge=challenge, code_challenge_method="S256", state="s",
        scope="mcp:db session:role:all",
    )).headers["location"]
    page = client.get(login)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    signed_in = client.post(login, data=dict(csrf=csrf, username=username, password=password,
                                             scope=["mcp:db"]))
    if signed_in.status_code != 302:
        return signed_in, None

    query = dict(p.split("=", 1) for p in signed_in.headers["location"].split("?", 1)[1].split("&"))
    token = client.post(metadata["token_endpoint"], auth=auth_pair, data=dict(
        grant_type="authorization_code", code=query["code"], redirect_uri=redirect,
        code_verifier=verifier, **({} if auth_pair else {"client_id": client_id}),
    ))

    return signed_in, token


def test_an_arcade_style_gateway_signs_users_in(tenant_config, mariadb_accounts, capsys):
    """Confidential client in two steps, PRM discovery, Basic auth, two users."""
    port = helpers.find_free_port()
    base = f"http://127.0.0.1:{port}"
    redirect = "https://cloud.arcade.dev/api/v1/oauth/callback"
    setup_cli.apply({"multi_tenant": True})
    setup_oauth.apply({
        "public_url": f"{base}/mcp", "mode": "builtin",
        "add_login_server": f"mariadb://127.0.0.1:{mariadb_accounts.port}",
        "required_role": "mcp_access",
    })
    capsys.readouterr()
    setup_oauth.apply({"add_client": "arcade", "confidential": True, "json": True})
    arcade = json.loads(capsys.readouterr().out)["clients"][0]
    setup_oauth.apply({"set_client_redirect_uris": arcade["clientId"], "redirect_uris": redirect})
    # The server refuses to start without an enabled user; the sign-in will add Ada.
    tenants.add_user([tenants.parse_identity("admin@example.com")])

    proc = _start_server(port)
    try:
        with httpx2.Client(follow_redirects=False, timeout=30) as client:
            unauthorized = client.post(base + "/mcp", json={},
                                       headers={"Accept": "application/json, text/event-stream"})
            assert unauthorized.status_code == 401
            assert "resource_metadata=" in unauthorized.headers["www-authenticate"]

            pair = (arcade["clientId"], arcade["clientSecret"])
            _, token = _authorization_code_flow(client, base, arcade["clientId"], redirect,
                                                "oauth_ada", "ada_pw", auth_pair=pair)
            assert token.status_code == 200, token.text
            tokens = token.json()

            refused, _ = _authorization_code_flow(client, base, arcade["clientId"], redirect,
                                                  "oauth_bob", "bob_pw", auth_pair=pair)
            assert refused.status_code == 400 and "not allowed" in refused.text

            async def use():
                async with helpers.http_client_session(
                    base + "/mcp", headers={"Authorization": f"Bearer {tokens['access_token']}"}
                ) as call:
                    listed = helpers.tool_payload(await call("db.list_connections"))
                    uri = listed[0] if isinstance(listed, list) else listed
                    connection_id = helpers.tool_payload(await call("db.connect", {"uri": uri}))
                    rows = helpers.tool_rows(await call("db.execute_sql", {
                        "connection_id": connection_id,
                        "sql": "SELECT CURRENT_USER() AS u, CURRENT_ROLE() AS r",
                    }))
                    return rows

            assert asyncio.run(use()) == [{"u": "oauth_ada@%", "r": "mcp_access"}]

            refreshed = client.post(base + "/token", data=dict(
                grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                client_id=pair[0], client_secret=pair[1]))
            again = client.post(base + "/token", data=dict(
                grant_type="refresh_token", refresh_token=tokens["refresh_token"],
                client_id=pair[0], client_secret=pair[1]))
            assert refreshed.status_code == 200 and again.status_code == 200
            assert again.json()["refresh_token"] == refreshed.json()["refresh_token"]

            revoked = client.post(base + "/revoke", data=dict(
                token=refreshed.json()["refresh_token"], client_id=pair[0], client_secret=pair[1]))
            assert revoked.status_code == 200
            after = client.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                headers={"Authorization": f"Bearer {refreshed.json()['access_token']}",
                                         "Accept": "application/json, text/event-stream"})
            assert after.status_code == 401
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_the_sdks_own_oauth_client_completes_the_flow(tenant_config, mariadb_accounts):
    """Interoperability: dynamic registration, PKCE, resource, iss - all the SDK's."""
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    from mcp.shared.auth import OAuthClientMetadata

    port = helpers.find_free_port()
    base = f"http://127.0.0.1:{port}"
    setup_cli.apply({"multi_tenant": True})
    setup_oauth.apply({
        "public_url": f"{base}/mcp", "mode": "builtin",
        "add_login_server": f"mariadb://127.0.0.1:{mariadb_accounts.port}",
    })
    tenants.add_user([tenants.parse_identity("admin@example.com")])

    class Storage:
        tokens = client_info = None

        async def get_tokens(self):
            return self.tokens

        async def set_tokens(self, tokens):
            self.tokens = tokens

        async def get_client_info(self):
            return self.client_info

        async def set_client_info(self, info):
            self.client_info = info

    callback = {}

    async def redirect_handler(url):
        def browse():
            with httpx2.Client(follow_redirects=False, timeout=30) as browser:
                login = browser.get(url).headers["location"]
                page = browser.get(login)
                csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
                answer = browser.post(login, data=dict(csrf=csrf, username="oauth_ada",
                                                       password="ada_pw", scope=["mcp:db"]))
                return dict(p.split("=", 1) for p in
                            answer.headers["location"].split("?", 1)[1].split("&"))
        from urllib.parse import unquote

        query = await asyncio.to_thread(browse)
        callback.update({key: unquote(value) for key, value in query.items()})

    async def callback_handler():
        from mcp.client.auth import AuthorizationCodeResult

        return AuthorizationCodeResult(code=callback["code"], state=callback.get("state"),
                                       iss=callback.get("iss"))

    proc = _start_server(port)
    try:
        async def run():
            provider = OAuthClientProvider(
                server_url=f"{base}/mcp",
                client_metadata=OAuthClientMetadata(
                    client_name="sdk-test", redirect_uris=["http://127.0.0.1:3030/callback"],
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"], token_endpoint_auth_method="none"),
                storage=Storage(), redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            async with create_mcp_http_client(auth=provider) as http_client:
                async with streamable_http_client(f"{base}/mcp", http_client=http_client) as (r, w):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        return sorted({tool.name.split(".")[0] for tool in tools.tools})

        assert asyncio.run(run()) == ["db"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --- the remaining setup options --------------------------------------------------------


def test_setup_sets_every_keycloak_setting(tenant_config, monkeypatch):
    monkeypatch.setenv("MCP_TEST_INTROSPECTION", "isecret")
    setup_oauth.apply({
        "mode": "keycloak", "issuer": "https://kc.example.com/realms/r",
        "no_verify": True, "verification": "introspection",
        "introspection_client_id": "mcp-introspector",
        "introspection_secret_env": "MCP_TEST_INTROSPECTION",
        "client_ids": "claude-code,vscode", "link_by_verified_email": False,
        "required_realm_role": "", "auto_provision": False,
        "default_scopes": "mcp:db",
    })
    keycloak = oauth_config.get_oauth_settings()["keycloak"]

    assert keycloak["issuer"] == "https://kc.example.com/realms/r"
    assert keycloak["verification"] == "introspection"
    assert keycloak["introspectionClientId"] == "mcp-introspector"
    assert oauth_config.get_introspection_secret() == "isecret"
    assert keycloak["clientIds"] == ["claude-code", "vscode"]
    assert keycloak["linkByVerifiedEmail"] is False
    assert keycloak["autoProvision"] == {"enabled": False, "requiredRealmRole": "",
                                         "defaultScopes": ["mcp:db"]}
    for bad in ({"verification": "guess"},
                {"introspection_secret_env": "MCP_TEST_UNSET_VARIABLE"}):
        with pytest.raises(mysqlsh.Error):
            setup_oauth.apply(bad)


def test_setup_revokes_rotates_lists_and_sets_roles(tenant_config, capsys):
    setup_cli.apply({"multi_tenant": True, "add_user": "ada"})
    ada = tenants.find_user("ada")
    first_kid = oauth_config.get_signing_key()["kid"]

    setup_oauth.apply({"revoke_tokens": "ada", "rotate_signing_key": True})
    assert tenants.get_user(ada)["tokenEpoch"] == 1
    assert oauth_config.get_signing_key()["kid"] != first_kid

    setup_cli.apply({"user": "ada", "set_default_role": "analyst"})
    assert tenants.get_default_role(ada) == "analyst"
    setup_cli.apply({"user": "ada", "set_default_role": ""})
    assert tenants.get_default_role(ada) == ""

    _client()
    capsys.readouterr()
    setup_oauth.apply({"list_clients": True, "json": True})
    assert json.loads(capsys.readouterr().out)["clients"][0]["name"] == "test"
    setup_oauth.apply({"list_clients": True})
    assert "public" in capsys.readouterr().out

    setup_oauth.apply({"public_url": PUBLIC_URL})
    setup_oauth.apply({"show": True})
    shown = capsys.readouterr().out
    assert PUBLIC_URL in shown and "OAuth mode:" in shown
    setup_oauth.apply({"public_url": ""})
    assert oauth_config.get_public_url() == ""


# --- a real Keycloak (opt-in) ------------------------------------------------------------


@pytest.mark.keycloak
def test_a_real_keycloak_token_is_accepted(tenant_config):
    """Against a realm prepared with mcp setup-keycloak-realm.

    Environment:
        KEYCLOAK_ISSUER: the realm's issuer, e.g. http://kc:8080/realms/mariadb
        KEYCLOAK_CLIENT_ID: a client allowing the password grant
            (mcp setup-keycloak-realm --directGrant)
        KEYCLOAK_USERNAME / KEYCLOAK_PASSWORD: a user with the realm role
            mcp-user and no pending required actions
        KEYCLOAK_MCP_URL: the --mcp-url the realm was prepared with
    """
    issuer = os.environ["KEYCLOAK_ISSUER"].rstrip("/")
    mcp_url = os.environ["KEYCLOAK_MCP_URL"]
    discovery = oauth_keycloak.check_issuer(issuer)
    answer = httpx2.post(discovery["token_endpoint"], data={
        "grant_type": "password", "client_id": os.environ["KEYCLOAK_CLIENT_ID"],
        "username": os.environ["KEYCLOAK_USERNAME"],
        "password": os.environ["KEYCLOAK_PASSWORD"], "scope": "openid mcp:db",
    }, timeout=30)
    assert answer.status_code == 200, answer.text
    token = answer.json()["access_token"]

    # A user is created at first sign-in when the account has the realm role
    # mcp-user; without it, the sign-in is linked by verified email to a user
    # an administrator added - which this test then adds first.
    claims = jwt.decode(token, options={"verify_signature": False})
    linked = None
    if "mcp-user" not in (claims.get("realm_access") or {}).get("roles", []):
        assert claims.get("email_verified") is True, (
            "the account needs the realm role mcp-user or a verified email"
        )
        linked = tenants.add_user([tenants.parse_identity(claims["email"])])

    settings = oauth_config.get_oauth_settings()["keycloak"]
    settings["issuer"] = issuer
    verifier = oauth_keycloak.KeycloakVerifier(settings, mcp_url, auth.UserDirectory())
    access = verifier.verify_token_sync(token)

    assert access is not None, "see the REFUSED line on stderr for why"
    assert access.scopes == ["mcp:db"]
    assert tenants.get_user(access.claims[general.MCP_USER_ID_CLAIM]) is not None
    if linked is not None:
        assert access.claims[general.MCP_USER_ID_CLAIM] == linked

    other = oauth_keycloak.KeycloakVerifier(settings, "https://not-this-server/mcp",
                                            auth.UserDirectory())
    assert other.verify_token_sync(token) is None


# --- mcp setup-keycloak-realm, against a stand-in for Keycloak's admin API ---------------


class _KeycloakAdminStandIn:
    """Just enough of Keycloak's admin REST API, and a realm's discovery."""

    def __init__(self, realm="mariadb"):
        self.realm = realm
        self.scopes, self.mappers, self.roles, self.clients = [], {}, [], []
        self.optional, self.role_mappings = {}, {}
        self.users = [{"id": "u1", "username": "dba", "email": "dba@example.com"}]
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                length = int(self.headers.get("content-length") or 0)
                return self.rfile.read(length) if length else b""

            def _route(self, method):
                from urllib.parse import parse_qs, urlsplit

                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                path = parts.path
                body = self._body()
                if path == "/realms/master/protocol/openid-connect/token":
                    form = dict(p.split("=", 1) for p in body.decode().split("&"))
                    if form.get("password") != "adminpw":
                        return self._answer(401, {"error": "invalid_grant"})
                    return self._answer(200, {"access_token": "admintok"})
                if path == f"/realms/{stand_in.realm}/.well-known/openid-configuration":
                    issuer = f"{stand_in.url}/realms/{stand_in.realm}"
                    return self._answer(200, {"issuer": issuer, "jwks_uri": issuer + "/certs"})
                if self.headers.get("authorization") != "Bearer admintok":
                    return self._answer(401)
                base = f"/admin/realms/{stand_in.realm}"
                data = json.loads(body) if body else None
                rest = path[len(base):]
                if rest == "/client-scopes":
                    if method == "GET":
                        return self._answer(200, stand_in.scopes)
                    if any(sc["name"] == data["name"] for sc in stand_in.scopes):
                        return self._answer(409)
                    stand_in.scopes.append({**data, "id": f"s{len(stand_in.scopes)}"})
                    return self._answer(201)
                if rest.startswith("/client-scopes/") and rest.endswith("/protocol-mappers/models"):
                    scope_id = rest.split("/")[2]
                    if method == "GET":
                        return self._answer(200, stand_in.mappers.get(scope_id, []))
                    stand_in.mappers.setdefault(scope_id, []).append(data)
                    return self._answer(201)
                if rest == "/roles":
                    if method == "GET":
                        return self._answer(200, stand_in.roles)
                    stand_in.roles.append({**data, "id": f"r{len(stand_in.roles)}"})
                    return self._answer(201)
                if rest == "/users":
                    key = "username" if "username" in query else "email"
                    return self._answer(200, [u for u in stand_in.users if u[key] == query[key]])
                if rest.endswith("/role-mappings/realm"):
                    stand_in.role_mappings.setdefault(rest.split("/")[2], []).extend(data)
                    return self._answer(204)
                if rest == "/clients":
                    if method == "GET":
                        return self._answer(200, [c for c in stand_in.clients
                                                  if c["clientId"] == query.get("clientId")])
                    stand_in.clients.append({**data, "id": f"c{len(stand_in.clients)}"})
                    return self._answer(201)
                if "/optional-client-scopes/" in rest:
                    _, _, client, _, scope_id = rest.split("/")
                    stand_in.optional.setdefault(client, set()).add(scope_id)
                    return self._answer(204)
                if rest.startswith("/clients/"):
                    for index, client in enumerate(stand_in.clients):
                        if client["id"] == rest.split("/")[2]:
                            stand_in.clients[index] = data
                    return self._answer(204)
                return self._answer(404)

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

        return Handler

    def close(self):
        self.server.shutdown()


@pytest.fixture
def keycloak_admin():
    stand_in = _KeycloakAdminStandIn()
    try:
        yield stand_in
    finally:
        stand_in.close()


def test_setup_keycloak_realm_prepares_the_realm_once(tenant_config, keycloak_admin,
                                                      monkeypatch):
    """Scopes with the audience, the role, the client; idempotent; server configured."""
    from mcp_plugin.lib import setup_keycloak

    monkeypatch.setenv("MCP_TEST_KC_ADMIN", "adminpw")
    options = {
        "server": keycloak_admin.url, "realm": "mariadb", "admin_user": "admin",
        "admin_password_env": "MCP_TEST_KC_ADMIN", "mcp_url": PUBLIC_URL,
        "grant_realm_role_to": "dba@example.com", "non_interactive": True,
    }
    setup_keycloak.run_setup_keycloak_realm(**options)
    setup_keycloak.run_setup_keycloak_realm(**options, direct_grant=True)

    assert sorted(sc["name"] for sc in keycloak_admin.scopes) == ["mcp:db"]
    for scope in keycloak_admin.scopes:
        (mapper,) = keycloak_admin.mappers[scope["id"]]
        assert mapper["config"]["included.custom.audience"] == PUBLIC_URL
        assert mapper["config"]["access.token.claim"] == "true"
    assert [role["name"] for role in keycloak_admin.roles] == ["mcp-user"]
    assert keycloak_admin.role_mappings["u1"][0]["name"] == "mcp-user"
    (client,) = keycloak_admin.clients
    assert client["clientId"] == "mariadb-mcp" and client["publicClient"] is True
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["directAccessGrantsEnabled"] is True
    assert keycloak_admin.optional["c0"] == {"s0"}

    assert oauth_config.get_mode() == "keycloak"
    assert oauth_config.get_public_url() == PUBLIC_URL
    assert oauth_config.get_oauth_settings()["keycloak"]["issuer"] == (
        f"{keycloak_admin.url}/realms/mariadb"
    )


def test_setup_keycloak_realm_asks_for_what_was_not_given(tenant_config, keycloak_admin,
                                                          monkeypatch):
    """The admin password with the shell's own password prompt."""
    from mcp_plugin.lib import setup_keycloak, setup_prompts
    from mcp_plugin.tests.unit.test_config import _FakeShell

    oauth_config.set_public_url(PUBLIC_URL)
    fake_shell = _FakeShell([keycloak_admin.url, "mariadb", "admin", "adminpw"])
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup_keycloak.run_setup_keycloak_realm(configure_server=False)

    types = [(asked or {}).get("type") for _, asked in fake_shell.prompts]
    assert types[-1] == "password"
    assert len(keycloak_admin.scopes) == 1
    assert oauth_config.get_mode() == "none"


def test_setup_keycloak_realm_says_what_went_wrong(tenant_config, keycloak_admin):
    from mcp_plugin.lib import setup_keycloak

    base = {"server": keycloak_admin.url, "realm": "mariadb", "admin_user": "admin",
            "mcp_url": PUBLIC_URL, "non_interactive": True}
    with pytest.raises(mysqlsh.Error, match="refused the administrator sign-in"):
        setup_keycloak.run_setup_keycloak_realm(**base, admin_password="wrong")
    with pytest.raises(mysqlsh.Error, match="--adminPassword is needed"):
        setup_keycloak.run_setup_keycloak_realm(**base)
    with pytest.raises(mysqlsh.Error, match="no user 'nobody'"):
        setup_keycloak.run_setup_keycloak_realm(**base, admin_password="adminpw",
                                                grant_realm_role_to="nobody")
    with pytest.raises(mysqlsh.Error, match="Unknown option"):
        setup_keycloak.run_setup_keycloak_realm(**base, realmz="x")


# --- mcp setup-oauth ------------------------------------------------------------------


@pytest.mark.parametrize(
    "options, message",
    [
        ({"oauth_mode": "builtin"}, "Unknown option"),
        ({}, "Nothing to do"),
        ({"show": True, "mode": "builtin"}, "cannot be"),
        ({"json": True, "mode": "builtin"}, "--json only applies"),
        ({"no_verify": True, "mode": "keycloak"}, "--noVerify only applies"),
    ],
)
def test_setup_oauth_refuses_what_it_cannot_do(tenant_config, options, message):
    with pytest.raises(mysqlsh.Error, match=message):
        setup_oauth.apply(options)


def test_the_interactive_oauth_setup(tenant_config, monkeypatch, capsys):
    """Mode, public URL, a login server and a confidential client, by menu."""
    from mcp_plugin.lib import setup_prompts
    from mcp_plugin.tests.unit.test_config import _FakeShell

    answers = [
        "1", PUBLIC_URL,                      # public URL
        "2", "3",                             # mode: builtin
        "3", LOGIN_SERVER, "mcp_access", "y", "", "y",  # built-in sign-in
        "4", "arcade", "y", "",               # register a confidential client
        "5", "1", "https://cloud.arcade.dev/api/v1/oauth/callback",  # its redirect URI
        "7", "1",                             # show its secret
        "",                                   # finish
    ]
    fake_shell = _FakeShell(answers)
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup_oauth.run_setup_oauth()

    assert oauth_config.get_public_url() == PUBLIC_URL
    assert oauth_config.get_mode() == "builtin"
    builtin = oauth_config.get_oauth_settings()["builtin"]
    assert builtin["loginServers"] == [LOGIN_SERVER] and builtin["requiredRole"] == "mcp_access"
    (client_id, record), = oauth_config.read_clients().items()
    assert record["name"] == "arcade" and record["confidential"] is True
    assert record["redirectUris"] == ["https://cloud.arcade.dev/api/v1/oauth/callback"]
    assert oauth_config.get_client_secret(client_id) in capsys.readouterr().out


def test_a_client_change_waits_for_the_lock(tenant_config):
    """No writer reads the clients while another is between its read and write.

    What was missing before: the server recording a client's use could write
    back a client mcp setup had just removed.
    """
    _client()
    path = oauth_config.get_clients_file_path()
    finished = threading.Event()

    def change():
        oauth_config.record_client_use({next(iter(oauth_config.read_clients())): "x"})
        finished.set()

    with config.file_lock(path):
        worker = threading.Thread(target=change)
        worker.start()
        time.sleep(0.3)
        assert not finished.is_set()
    worker.join(5)

    assert finished.is_set()
    assert next(iter(oauth_config.read_clients().values()))["lastUsed"] == "x"


# --- signing key rotation ------------------------------------------------------------------


def _issued_token(provider):
    client_id = _client()
    tokens = _redeem(provider, client_id, _sign_in(provider, client_id))
    return tokens.access_token


def test_a_rotated_signing_key_keeps_issued_tokens_valid(tenant_config):
    """Issued tokens live out their lifetime; new ones carry the new key."""
    provider = _provider()
    old = _issued_token(provider)
    old_kid = jwt.get_unverified_header(old)["kid"]

    new_kid = oauth_config.rotate_signing_key()
    # A token naming the new kid makes the server reload the keys at once.
    provider._keys_loaded_at = 0
    provider._load_signing_keys()

    assert provider.verify_access_token(old) is not None
    fresh = _issued_token(provider)
    assert jwt.get_unverified_header(fresh)["kid"] == new_kid != old_kid
    assert provider.verify_access_token(fresh) is not None

    # Once the previous key's window has passed, its tokens are refused.
    key = oauth_config.get_signing_key()
    key["previous"]["validUntil"] = int(time.time()) - 1
    mysqlsh.globals.shell.store_secret(oauth_config.SIGNING_KEY_SECRET, json.dumps(key))
    provider._load_signing_keys()
    assert provider.verify_access_token(old) is None
    assert provider.verify_access_token(fresh) is not None


def test_dropping_the_previous_signing_key_refuses_its_tokens(tenant_config, capsys):
    """For a key that may have leaked: rotate, then drop the previous one."""
    provider = _provider()
    old = _issued_token(provider)

    setup_oauth.apply({"rotate_signing_key": True})
    provider._load_signing_keys()
    assert provider.verify_access_token(old) is not None

    setup_oauth.apply({"drop_previous_signing_key": True})
    provider._load_signing_keys()
    assert provider.verify_access_token(old) is None
    assert "dropped" in capsys.readouterr().out
    setup_oauth.apply({"drop_previous_signing_key": True})
    assert "no previous" in capsys.readouterr().out


def test_a_server_notices_a_rotation_made_elsewhere(tenant_config):
    """A token with an unknown kid reloads the keys, at most every 10 seconds."""
    provider = _provider()
    oauth_config.rotate_signing_key()
    provider._keys_loaded_at = 0

    fresh_provider = _provider()
    token = _issued_token(fresh_provider)

    assert provider.verify_access_token(token) is not None


def test_the_sign_in_checkboxes_are_ordinary_checkboxes(tenant_config):
    """Only the text fields take the full width; a checkbox stays its own size."""
    from starlette.responses import HTMLResponse

    provider = _provider()
    pending = {"scopes": ["mcp:db"], "redirect_uri": "http://127.0.0.1:1/cb",
               "client_name": "Test", "csrf": "x"}
    page = provider._form(HTMLResponse, "req", pending).body.decode()

    assert "input[name]" not in page
    assert "input[type=checkbox]{margin:0}" in page
    assert page.count('type="checkbox"') == 1


def test_the_sign_in_page_shows_the_seal_inline(tenant_config):
    """The seal is inline SVG, and carries no style attribute the CSP would block."""
    from starlette.responses import HTMLResponse

    provider = _provider()
    pending = {"scopes": ["mcp:db"], "redirect_uri": "http://127.0.0.1:1/cb",
               "client_name": "Test", "csrf": "x"}
    response = provider._form(HTMLResponse, "req", pending, error="No.")
    page = response.body.decode()

    assert '<svg role="img" aria-label="MariaDB"' in page
    assert "<?xml" not in page and "<!DOCTYPE svg" not in page
    assert " style=" not in page
    assert "img-src" not in response.headers["Content-Security-Policy"]
    assert '<div class="mrsLoginError" role="alert"><p>No.</p></div>' in page
