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

"""Multi-tenant mode: users, their secret groups, API keys and isolation.

Most of this runs in-process: the users and their secrets are real (in groups
the ``tenant_config`` fixture deletes again), the tools are called directly
with a request context carrying an authenticated user, and the sessions are
stubs. The last tests start a real server over HTTP and talk to it with real
API keys, which is the only way to see the SDK's own authentication answer.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver httpx

import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import httpx2
import mysqlsh
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import mcp_plugin.tests.unit.helpers as helpers
from mcp_plugin.lib import (
    auth,
    config,
    db_functions,
    general,
    server,
    setup_cli,
    tenants,
)

CLIENT_ADDRESS = "192.0.2.10"
SESSION_ID = "0123456789abcdef0123456789abcdef"
OTHER_SESSION_ID = "fedcba9876543210fedcba9876543210"

URI = "mariadb://app@db.example.com:3306"
PASSWORD = "tenant_pytest_password"


class _ToolRecorder:
    """Stands in for the MCPServer, collecting the registered tools by name."""

    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


class _StubSession:
    """An open session that accepts anything and records being closed."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _context(user=None, scopes=tenants.SUPPORTED_SCOPES, address=CLIENT_ADDRESS,
             session_id=SESSION_ID, grant="", client_id=auth.API_KEY_CLIENT_ID):
    """Builds a request context as the HTTP transport does, with a user.

    The SDK's bearer authentication puts an ``AuthenticatedUser`` into the
    request's scope; all the plugin reads of it is ``access_token``.
    """
    scope = {}
    if user is not None:
        token = SimpleNamespace(
            scopes=list(scopes),
            client_id=client_id,
            claims={
                general.MCP_USER_ID_CLAIM: user,
                general.AUTH_METHOD_CLAIM: auth.AUTH_METHOD_API_KEY,
                general.GRANT_CLAIM: grant,
            },
        )
        scope["user"] = SimpleNamespace(access_token=token)

    request = SimpleNamespace(
        client=SimpleNamespace(host=address, port=54321),
        headers={general.MCP_SESSION_ID_HEADER: session_id} if session_id else {},
        scope=scope,
    )

    return SimpleNamespace(request_context=SimpleNamespace(request=request))


@pytest.fixture
def multi_tenant(tenant_config):
    """Turns multi-tenant mode on, as a starting server would, for one test."""
    db_functions._sessions.clear()
    tenants.set_multi_tenant(True)
    general.set_multi_tenant(True)
    general.set_active_transport(general.TRANSPORT_STREAMABLE_HTTP)
    try:
        yield
    finally:
        general.set_active_transport(None)
        db_functions._sessions.clear()


def _add(identity, **kwargs):
    """Adds a user known by one identity and returns their id."""
    return tenants.add_user([tenants.parse_identity(identity)], **kwargs)


def _db_tools(monkeypatch, opened=None):
    """Registers the db tools with sessions that open without a server."""

    def _fake_open_session(uri, kind=None, mcp_user_id=None):
        if opened is not None:
            opened.append((uri, mcp_user_id))
        return _StubSession()

    monkeypatch.setattr(db_functions, "_open_session", _fake_open_session)
    recorder = _ToolRecorder()
    db_functions.register_db_tools(recorder)

    return recorder.tools


# --- identities ---------------------------------------------------------------


def test_an_identity_is_read_from_how_it_is_written():
    """A bare address is an email, anything else a user id; types are named."""
    assert tenants.parse_identity("ada@example.com") == {
        "type": "email",
        "value": "ada@example.com",
    }
    assert tenants.parse_identity("ada") == {"type": "userId", "value": "ada"}
    assert tenants.parse_identity("userId:ada@corp") == {
        "type": "userId",
        "value": "ada@corp",
    }
    assert tenants.parse_identity("oauth:https://kc/realms/r|6b1f") == {
        "type": "oauth",
        "issuer": "https://kc/realms/r",
        "subject": "6b1f",
    }
    assert tenants.parse_identity("mariadb:mariadb://db:3306|ada@%") == {
        "type": "mariadb",
        "server": "mariadb://db:3306",
        "account": "ada@%",
    }

    for bad in ("", "email:nobody", "oauth:only-an-issuer", "oauth:|sub"):
        with pytest.raises(mysqlsh.Error):
            tenants.parse_identity(bad)


def test_an_identity_belongs_to_one_user_only(tenant_config):
    """Emails compare without case; a second user cannot take an identity."""
    ada = _add("Ada@Example.com", name="Ada")

    with pytest.raises(mysqlsh.Error, match="already belongs"):
        _add("ada@example.COM")

    bob = _add("bob")
    with pytest.raises(mysqlsh.Error, match="already belongs"):
        tenants.add_identity(bob, tenants.parse_identity("ADA@example.com"))

    assert tenants.find_user("ada@EXAMPLE.com") == ada
    assert tenants.find_user(ada.upper()) == ada
    assert tenants.find_user("bob") == bob
    assert tenants.find_user("carol") is None


def test_a_user_holds_several_identities(tenant_config):
    """Any of them names the user; the last one cannot be taken away."""
    ada = _add("ada@example.com")
    tenants.add_identity(ada, tenants.parse_identity("userId:ada"))
    tenants.add_identity(ada, tenants.parse_identity("oauth:https://kc/r|42"))
    tenants.add_identity(ada, tenants.parse_identity("mariadb:mariadb://a:3306|ada@%"))
    tenants.add_identity(ada, tenants.parse_identity("mariadb:mariadb://b:3306|ada@%"))

    for identifier in (
        "ada@example.com",
        "userId:ada",
        "oauth:https://kc/r|42",
        "mariadb:mariadb://b:3306|ada@%",
    ):
        assert tenants.find_user(identifier) == ada

    tenants.remove_identity(ada, tenants.parse_identity("userId:ada"))
    assert tenants.find_user("userId:ada") is None

    for identifier in ("oauth:https://kc/r|42", "mariadb:mariadb://a:3306|ada@%",
                       "mariadb:mariadb://b:3306|ada@%"):
        tenants.remove_identity(ada, tenants.parse_identity(identifier))
    with pytest.raises(mysqlsh.Error, match="only identity"):
        tenants.remove_identity(ada, tenants.parse_identity("ada@example.com"))


def test_users_json_holds_nothing_secret(tenant_config):
    """The file is keyed by the UUID and carries no key or password."""
    ada = _add("ada@example.com", name="Ada")
    key = tenants.issue_api_key(ada)

    with open(tenants.get_users_file_path(), encoding="utf-8") as users_file:
        text = users_file.read()
    content = json.loads(text)

    assert list(content["users"]) == [ada]
    assert str(uuid.UUID(ada)) == ada
    assert key not in text
    record = content["users"][ada]
    assert record["scopes"] == list(tenants.SUPPORTED_SCOPES)
    assert record["allowedPaths"] == []
    assert record["tokenEpoch"] == 0
    assert "allowedRoles" not in record


# --- secret groups and API keys ---------------------------------------------------


def test_each_user_keeps_their_secrets_in_a_group_of_their_own(tenant_config):
    """The same URI holds a different password per user, and none generically."""
    ada = _add("ada")
    bob = _add("bob")

    config.store_connection(URI, "ada-pw", mcp_user_id=ada)
    config.store_connection(URI, "bob-pw", mcp_user_id=bob)

    assert config.get_connection_password(URI, mcp_user_id=ada) == "ada-pw"
    assert config.get_connection_password(URI, mcp_user_id=bob) == "bob-pw"
    assert URI not in config.list_stored_connection_uris()
    assert config.find_connection(URI, mcp_user_id=ada) == (URI, "mcp")
    assert config.find_connection(URI) is None

    config.delete_connection(URI, mcp_user_id=ada)
    assert config.list_connection_uris(mcp_user_id=ada) == []
    assert config.list_connection_uris(mcp_user_id=bob) == [URI]


def test_a_users_connection_has_no_details(tenant_config):
    """Folders and captions are for the extension, which tenants do not get."""
    ada = _add("ada")

    with pytest.raises(mysqlsh.Error, match="no folder"):
        config.store_connection(URI, PASSWORD, path="/x", mcp_user_id=ada)


def test_an_api_key_names_its_user_and_can_be_shown_again(tenant_config):
    """The key is stored as it is, so an administrator can show it again."""
    ada = _add("ada")
    key = tenants.issue_api_key(ada)

    assert key.startswith(tenants.API_KEY_PREFIX)
    assert tenants.user_of_api_key(key) == ada
    assert tenants.get_api_key(ada) == key
    assert tenants.is_api_key(key)
    assert not tenants.is_api_key("eyJhbGciOi.a.b")

    rotated = tenants.issue_api_key(ada)
    assert rotated != key
    assert tenants.get_api_key(ada) == rotated


def test_removing_a_user_removes_their_secrets(tenant_config):
    """Their API key and connections go with them, other users' stay."""
    ada = _add("ada")
    bob = _add("bob")
    tenants.issue_api_key(ada)
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    config.store_connection(URI, PASSWORD, mcp_user_id=bob)

    tenants.remove_user(ada)

    assert tenants.get_user(ada) is None
    assert ada not in tenants.list_groups()
    assert config.list_connection_uris(mcp_user_id=bob) == [URI]


def test_a_group_nobody_owns_is_reported_and_can_be_purged(tenant_config):
    """What an interrupted removal leaves behind is found, not guessed at."""
    orphan = str(uuid.uuid4())
    mysqlsh.globals.shell.store_secret("MCP:API_KEY", "x", {"group": orphan})

    assert orphan in tenants.orphan_groups()

    tenants.purge_group(orphan)
    assert orphan not in tenants.orphan_groups()


# --- authentication -----------------------------------------------------------------


def test_an_api_key_authenticates_its_user(tenant_config):
    """The verifier makes the key into a token carrying the user and scopes."""
    ada = _add("ada", scopes="mcp:db")
    key = tenants.issue_api_key(ada)
    verifier = auth.ApiKeyVerifier(auth.UserDirectory())

    token = verifier.verify_token_sync(key)

    assert token.subject == ada
    assert token.client_id == auth.API_KEY_CLIENT_ID
    assert token.scopes == ["mcp:db"]
    principal = general.principal_from_access_token(token)
    assert principal == general.Principal(
        ada, ("mcp:db",), auth.AUTH_METHOD_API_KEY, client_id=auth.API_KEY_CLIENT_ID
    )


def test_a_wrong_key_authenticates_nobody(tenant_config):
    """Another user's secret under one's own id, garbage and JWTs all fail."""
    ada = _add("ada")
    bob = _add("bob")
    ada_key = tenants.issue_api_key(ada)
    bob_key = tenants.issue_api_key(bob)
    verifier = auth.ApiKeyVerifier(auth.UserDirectory())

    forged = ada_key.rsplit("_", 1)[0] + "_" + bob_key.rsplit("_", 1)[1]
    for token in (forged, "", "Bearer x", "eyJhbGciOi.a.b", ada_key + "x"):
        assert verifier.verify_token_sync(token) is None


def test_a_change_with_mcp_setup_applies_to_the_next_request(tenant_config):
    """Rotation, disabling and removal are noticed without a restart."""
    ada = _add("ada")
    old = tenants.issue_api_key(ada)
    verifier = auth.ApiKeyVerifier(auth.UserDirectory())
    assert verifier.verify_token_sync(old) is not None

    new = tenants.issue_api_key(ada)
    assert verifier.verify_token_sync(old) is None
    assert verifier.verify_token_sync(new) is not None

    tenants.set_disabled(ada, True)
    assert verifier.verify_token_sync(new) is None
    tenants.set_disabled(ada, False)
    assert verifier.verify_token_sync(new) is not None

    tenants.remove_user(ada)
    assert verifier.verify_token_sync(new) is None


def test_the_directory_reports_who_is_still_active(tenant_config):
    """That is what closes the connections of a user who is gone."""
    ada = _add("ada")
    bob = _add("bob")
    seen = []
    directory = auth.UserDirectory(on_users_changed=seen.append)

    directory.users()
    tenants.set_disabled(bob, True)
    directory.users()
    directory.users()

    assert seen == [{ada, bob}, {ada}]


def test_oauth_tokens_are_throttled_per_user_they_name():
    """Behind one gateway, one user's expired tokens do not throttle the others."""
    import jwt

    key = "k" * 32

    def token(subject):
        return jwt.encode({"sub": subject, "exp": 1}, key, algorithm="HS256")

    assert auth._user_named_in(token("ada")) == "ada"
    assert auth._user_named_in(token("x" * 1000)) == "x" * auth._MAX_THROTTLE_SUBJECT
    assert auth._user_named_in("not-a-token") is None
    assert auth._user_named_in(jwt.encode({"aud": "x"}, key, algorithm="HS256")) is None

    ada = str(uuid.uuid4())
    api_key = f"{tenants.API_KEY_PREFIX}{uuid.UUID(ada).hex}_{'A' * 43}"
    assert auth._user_named_in(api_key) == ada

    async def refuse(scope, receive, send):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    throttle = auth.AuthFailureThrottle(refuse, max_failures=2)

    async def status(subject):
        sent = []

        async def send(message):
            sent.append(message)

        headers = [(b"authorization", f"Bearer {token(subject)}".encode())]
        await throttle({"type": "http", "path": "/mcp", "client": ("gateway", 1),
                        "headers": headers}, None, send)
        return sent[0]["status"]

    async def scenario():
        assert [await status("ada") for _ in range(3)] == [401, 401, 429]
        assert await status("bob") == 401

    asyncio.run(scenario())


def test_failures_are_throttled_per_address_and_user():
    """One user's bad tokens do not lock out another user on that address."""
    throttle = auth.AuthFailureThrottle(app=None, max_failures=3,
                                        max_failures_per_address=100)

    for _ in range(3):
        throttle.record_failure("gateway", "ada")

    assert throttle.is_throttled("gateway", "ada")
    assert not throttle.is_throttled("gateway", "bob")
    assert not throttle.is_throttled("elsewhere", "ada")


def test_an_address_has_a_ceiling_of_its_own():
    """Guessing across many users from one address is throttled too."""
    throttle = auth.AuthFailureThrottle(app=None, max_failures=100,
                                        max_failures_per_address=5)

    for index in range(5):
        throttle.record_failure("gateway", f"user{index}")

    assert throttle.is_throttled("gateway", "someone-new")


# --- the tools ------------------------------------------------------------------------


def test_a_user_lists_and_opens_only_their_own_connections(multi_tenant, monkeypatch):
    """Bob's configured URI names nothing for Ada, and the reverse."""
    ada = _add("ada")
    bob = _add("bob")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    opened = []
    tools = _db_tools(monkeypatch, opened)

    assert tools["db.list_connections"](_context(ada)) == [URI]
    assert tools["db.list_connections"](_context(bob)) == []

    connection_id = tools["db.connect"](_context(ada), URI)
    assert opened == [(URI, ada)]

    with pytest.raises(ToolError, match="is not a configured connection"):
        tools["db.connect"](_context(bob), URI)


def test_a_connection_is_bound_to_its_user(multi_tenant, monkeypatch):
    """Even on the very same address and MCP session, another user is refused."""
    ada = _add("ada")
    bob = _add("bob")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    tools = _db_tools(monkeypatch)
    connection_id = tools["db.connect"](_context(ada), URI)

    with pytest.raises(ToolError) as refused:
        tools["db.close"](_context(bob), connection_id)
    with pytest.raises(ToolError) as unknown:
        tools["db.close"](_context(bob), "no-such-id")

    # The refusal is byte-identical to an unknown id, ids masked out.
    assert str(refused.value).replace(connection_id, "ID") == str(
        unknown.value
    ).replace("no-such-id", "ID")

    tools["db.close"](_context(ada), connection_id)


def test_a_call_without_a_user_is_refused(multi_tenant, monkeypatch):
    """The registrar's check holds even if the SDK's somehow did not."""
    _add("ada")
    tools = _db_tools(monkeypatch)

    with pytest.raises(ToolError, match="authenticated users only"):
        tools["db.list_connections"](_context(None))


def test_a_tool_needs_the_scope_of_its_group(multi_tenant, monkeypatch):
    """A token without mcp:db cannot call a db tool."""
    ada = _add("ada")
    tools = _db_tools(monkeypatch)

    with pytest.raises(ToolError, match="mcp:db"):
        tools["db.list_connections"](_context(ada, scopes=["openid"]))


def test_a_user_has_a_connection_limit_of_their_own(multi_tenant, monkeypatch):
    """Spread over many MCP sessions, a user still cannot take every slot."""
    ada = _add("ada")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    tools = _db_tools(monkeypatch)
    monkeypatch.setattr(general, "MAX_CONNECTIONS_PER_USER", 2)

    tools["db.connect"](_context(ada, session_id="a" * 32), URI)
    tools["db.connect"](_context(ada, session_id="b" * 32), URI)
    with pytest.raises(ToolError, match="maximum of 2"):
        tools["db.connect"](_context(ada, session_id="c" * 32), URI)


def test_the_connections_of_an_inactive_user_are_dropped(multi_tenant, monkeypatch):
    """Only theirs: another user's, and a single-tenant one, stay open."""
    ada = _add("ada")
    bob = _add("bob")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    config.store_connection(URI, PASSWORD, mcp_user_id=bob)
    tools = _db_tools(monkeypatch)
    ada_id = tools["db.connect"](_context(ada), URI)
    bob_id = tools["db.connect"](_context(bob), URI)

    dropped = db_functions.drop_connections_of_inactive_users({bob})

    assert dropped == 1
    assert ada_id not in db_functions._sessions
    assert bob_id in db_functions._sessions


def test_a_removed_users_reopen_is_refused(multi_tenant):
    """Their group is gone, so their connection URI is no longer configured."""
    ada = _add("ada")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    tenants.remove_user(ada)

    with pytest.raises(ToolError, match="no longer a configured connection"):
        db_functions._open_session(URI, "mcp", ada)


def test_paths_are_a_users_own(multi_tenant, tmp_path):
    """Allowed for one user is not allowed for another, nor without a user."""
    ada = _add("ada")
    bob = _add("bob")
    tenants.set_allowed_paths(ada, [str(tmp_path)])

    inside = str(tmp_path / "project")
    assert config.is_path_allowed(inside, ada)
    assert not config.is_path_allowed(inside, bob)
    assert not config.is_path_allowed(inside)


def test_a_path_is_never_offered_to_the_client_to_trust(multi_tenant, tmp_path):
    """The client is who the list restricts, so nothing is elicited or saved."""
    ada = _add("ada")
    asked = []

    async def _elicit(**_kwargs):
        asked.append(True)
        return SimpleNamespace(action="accept", data=SimpleNamespace(trust=True))

    ctx = _context(ada)
    ctx.elicit = _elicit

    with pytest.raises(ToolError, match="Ask the administrator"):
        asyncio.run(general.require_allowed_path(ctx, str(tmp_path)))

    assert asked == []
    assert tenants.get_allowed_paths(ada) == []


def test_no_path_means_the_working_directory_and_is_checked(multi_tenant, tmp_path,
                                                            monkeypatch):
    """None is read as the cwd; a tenant may not get it for free."""
    ada = _add("ada")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolError, match=str(tmp_path.name)):
        asyncio.run(general.require_allowed_path(_context(ada), None))

    tenants.set_allowed_paths(ada, [str(tmp_path)])
    asyncio.run(general.require_allowed_path(_context(ada), None))


def test_a_single_tenant_server_is_unchanged(tenant_config, monkeypatch):
    """Without the mode nothing asks for a user, and None stays None."""
    general.set_multi_tenant(False)
    tools = _db_tools(monkeypatch)

    assert isinstance(tools["db.list_connections"](_context(None)), list)
    asyncio.run(general.require_allowed_path(_context(None), None))


# --- the server ---------------------------------------------------------------------


def test_a_multi_tenant_server_refuses_what_it_cannot_serve(tenant_config):
    """stdio, --gui, the msm, sandbox, migrator and util groups, and no users."""
    tenants.set_multi_tenant(True)

    with pytest.raises(mysqlsh.Error, match="no enabled user"):
        server._check_multi_tenant("streamable-http", ["db"], False)

    _add("ada")
    server._check_multi_tenant("streamable-http", ["db"], False)

    with pytest.raises(mysqlsh.Error, match="stdio"):
        server._check_multi_tenant("stdio", ["db"], False)
    with pytest.raises(mysqlsh.Error, match="--gui"):
        server._check_multi_tenant("streamable-http", ["db"], True)
    with pytest.raises(mysqlsh.Error, match="msm, sandbox, migrator, util"):
        server._check_multi_tenant(
            "streamable-http", ["db", "msm", "sandbox", "migrator", "util"], False
        )


def test_tokens_in_clear_are_warned_about_only_off_loopback(capsys):
    """TLS, or loopback, says nothing; plain HTTP on the network warns."""
    server._warn_if_tokens_travel_in_clear("127.0.0.1", 8080, tls=False)
    server._warn_if_tokens_travel_in_clear("0.0.0.0", 8080, tls=True)
    assert capsys.readouterr().err == ""

    server._warn_if_tokens_travel_in_clear("0.0.0.0", 8080, tls=False)
    assert "plain HTTP" in capsys.readouterr().err


# --- mcp setup ------------------------------------------------------------------------


def test_setup_adds_a_user_and_hands_out_their_key(tenant_config, capsys):
    """--addUser prints the key; --json makes it machine-readable."""
    setup_cli.apply({"multi_tenant": True})
    setup_cli.apply({"add_user": "ada@example.com", "name": "Ada", "json": True})

    report = json.loads(capsys.readouterr().out.split("Multi-tenant mode on.\n", 1)[1])
    added = report["users"][0]
    assert tenants.find_user("ada@example.com") == added["id"]
    assert tenants.get_api_key(added["id"]) == added["apiKey"]


def test_setup_needs_a_user_for_connections_in_multi_tenant_mode(tenant_config):
    """And refuses one outside it, where it would silently mean nothing."""
    with pytest.raises(mysqlsh.Error, match="only applies .* in multi-tenant mode"):
        setup_cli.apply({"add_paths": "/tmp", "user": "ada"})

    setup_cli.apply({"multi_tenant": True})
    with pytest.raises(mysqlsh.Error, match="belong to a user"):
        setup_cli.apply({"add_paths": "/tmp"})


def test_setup_manages_one_users_connections_and_paths(tenant_config, tmp_path):
    """--user puts them in that user's group and record, nowhere else."""
    setup_cli.apply({"multi_tenant": True, "add_user": "ada"})
    ada = tenants.find_user("ada")

    setup_cli.apply(
        {
            "user": "ada",
            "add_connection": URI,
            "password": PASSWORD,
            "no_verify": True,
            "add_paths": str(tmp_path),
        }
    )

    assert config.list_connection_uris(mcp_user_id=ada) == [URI]
    assert URI not in config.list_connection_uris()
    assert tenants.get_allowed_paths(ada) == [str(tmp_path)]
    assert str(tmp_path) not in config.get_allowed_paths()

    setup_cli.apply({"user": ada, "delete_connections": URI, "delete_paths": str(tmp_path)})
    assert config.list_connection_uris(mcp_user_id=ada) == []
    assert tenants.get_allowed_paths(ada) == []


def test_setup_shows_every_user_and_the_orphans(tenant_config, capsys):
    """--show --allUsers is the administrator's view across users."""
    setup_cli.apply({"multi_tenant": True, "add_user": "ada", "name": "Ada"})
    ada = tenants.find_user("ada")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    orphan = str(uuid.uuid4())
    mysqlsh.globals.shell.store_secret("MCP:API_KEY", "x", {"group": orphan})
    capsys.readouterr()

    setup_cli.apply({"show": True, "all_users": True, "json": True})
    shown = json.loads(capsys.readouterr().out)

    assert shown["multi_tenant"] is True
    (user,) = shown["users"]
    assert user["id"] == ada
    assert user["name"] == "Ada"
    assert user["connections"] == [URI]
    assert user["has_api_key"] is True
    assert orphan in shown["orphan_groups"]
    assert "apiKey" not in json.dumps(shown)

    setup_cli.apply({"purge_orphan_groups": True})
    assert orphan not in tenants.orphan_groups()


def test_setup_rotates_shows_disables_and_removes(tenant_config, capsys):
    """Each of the key and lifecycle options does what it says."""
    setup_cli.apply({"multi_tenant": True, "add_user": "ada"})
    ada = tenants.find_user("ada")
    first = tenants.get_api_key(ada)

    setup_cli.apply({"rotate_api_key": "ada"})
    assert tenants.get_api_key(ada) != first

    capsys.readouterr()
    setup_cli.apply({"show_api_key": "ada", "json": True})
    assert json.loads(capsys.readouterr().out)["users"][0]["apiKey"] == (
        tenants.get_api_key(ada)
    )

    setup_cli.apply({"disable_user": "ada"})
    assert not tenants.is_active_user(ada)
    setup_cli.apply({"enable_user": "ada"})
    assert tenants.is_active_user(ada)

    with pytest.raises(mysqlsh.Error, match="Unknown scope"):
        setup_cli.apply({"user": "ada", "set_scopes": "mcp:msm"})
    setup_cli.apply({"user": "ada", "set_scopes": "mcp:db"})
    assert tenants.get_scopes(ada) == ["mcp:db"]

    setup_cli.apply({"remove_user": "ada"})
    assert tenants.find_user("ada") is None


def test_setup_refuses_the_migrator_in_multi_tenant_mode(tenant_config):
    """The tools it installs are not served to tenants."""
    with pytest.raises(mysqlsh.Error, match="not available in multi-tenant"):
        setup_cli.apply({"multi_tenant": True, "install_migrator": True})


def test_switching_modes_moves_nothing(tenant_config, clean_config):
    """Each mode's connections stay where they are, for switching back."""
    config.store_connection(URI, PASSWORD)
    setup_cli.apply({"multi_tenant": True, "add_user": "ada"})
    ada = tenants.find_user("ada")

    setup_cli.apply({"multi_tenant": False})

    assert tenants.is_multi_tenant() is False
    assert URI in config.list_connection_uris()
    assert tenants.get_user(ada) is not None


# --- a real server --------------------------------------------------------------------


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_a_real_server_serves_each_user_their_own(tenant_config, sandbox):
    """Over HTTP with real keys: 401 without one, isolation with one."""
    setup_cli.apply({"multi_tenant": True})
    ada = _add("ada")
    bob = _add("bob")
    ada_key = tenants.issue_api_key(ada)
    bob_key = tenants.issue_api_key(bob)
    config.store_connection(sandbox.uri, sandbox.password, mcp_user_id=ada)

    async def scenario():
        async with helpers.http_server(["db"]) as url:
            async with httpx2.AsyncClient() as raw:
                for headers in ({}, _bearer("mdbmcp_nope"), _bearer(ada_key + "x")):
                    response = await raw.post(
                        url,
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        headers={"Accept": "application/json, text/event-stream",
                                 **headers},
                    )
                    assert response.status_code == 401

            async with helpers.http_client_session(url, headers=_bearer(ada_key)) as ada_call:
                listed = helpers.tool_payload(await ada_call("db.list_connections"))
                assert listed == [config.with_default_scheme(sandbox.uri)] or listed == (
                    config.with_default_scheme(sandbox.uri)
                )
                connection_id = helpers.tool_payload(
                    await ada_call("db.connect", {"uri": sandbox.uri})
                )
                rows = helpers.tool_rows(
                    await ada_call(
                        "db.execute_sql",
                        {"connection_id": connection_id, "sql": "SELECT 1 AS one"},
                    )
                )
                assert rows == [{"one": 1}]

                async with helpers.http_client_session(
                    url, headers=_bearer(bob_key)
                ) as bob_call:
                    assert helpers.tool_payload(
                        await bob_call("db.list_connections")
                    ) in ([], None)
                    refused = await bob_call(
                        "db.execute_sql",
                        {"connection_id": connection_id, "sql": "SELECT 1"},
                    )
                    assert refused.is_error
                    assert "No open connection found" in str(refused.content)

                tenants.set_disabled(ada, True)
                with pytest.raises(Exception):
                    await ada_call("db.list_connections")

    asyncio.run(scenario())


# --- the interactive setup ------------------------------------------------------------


def test_the_interactive_setup_manages_users(tenant_config, tmp_path, monkeypatch,
                                             capsys):
    """The multi-tenant menu walks a user through their whole life."""
    from mcp_plugin.lib import setup, setup_prompts
    from mcp_plugin.tests.unit.test_config import _FakeShell

    tenants.set_multi_tenant(True)
    path = str(tmp_path)
    answers = [
        "1", "ada@example.com", "Ada",       # add a user
        "3", "1",                            # manage Ada's resources
        "1", "app@db.example.com:3306", "pw",  # add a connection
        "3", path,                           # add an allowed path
        "",                                  # back (the default)
        "4", "1",                            # show Ada's key
        "5", "1",                            # issue a new key
        "6", "1",                            # disable Ada
    ]
    fake_shell = _FakeShell(answers + [""])  # finish (the default)
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    ada = tenants.find_user("ada@example.com")
    assert tenants.get_user(ada)["name"] == "Ada"
    assert config.list_connection_uris(mcp_user_id=ada) == [URI]
    assert tenants.get_allowed_paths(ada) == [path]
    assert not tenants.is_active_user(ada)
    assert URI not in config.list_connection_uris()
    output = capsys.readouterr().out
    assert tenants.get_api_key(ada) in output

    menus = [c for c in fake_shell.select_prompts() if c and c[-1] == "Finish"]
    assert menus[0] == [label for label, _ in setup._tenant_menu_entries()] + ["Finish"]

    # Then the deletions, and the user's removal.
    fake_shell = _FakeShell(
        [
            "3", "1", "4", "1", "2", "1", "",  # delete the path and the connection
            "2", "1", "y",                     # remove Ada
            "",                                # finish
        ]
    )
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    assert tenants.get_user(ada) is None
    assert ada not in tenants.list_groups()


def test_the_interactive_setup_says_when_there_is_nobody(tenant_config, monkeypatch,
                                                         capsys):
    """Every user action backs out cleanly with no users."""
    from mcp_plugin.lib import setup, setup_prompts
    from mcp_plugin.tests.unit.test_config import _FakeShell

    tenants.set_multi_tenant(True)
    fake_shell = _FakeShell(["2", "3", "4", "5", "6", "1", "", ""])
    monkeypatch.setattr(setup_prompts, "shell", lambda: fake_shell)

    setup.run_setup()

    assert capsys.readouterr().out.count("No users yet.") >= 6
    assert tenants.read_users() == {}


@pytest.mark.parametrize(
    "options, message",
    [
        ({"json": True, "add_paths": "/tmp"}, "--json only applies"),
        ({"all_users": True, "add_paths": "/tmp"}, "--allUsers only applies"),
        ({"name": "Ada", "add_paths": "/tmp"}, "--name only applies"),
        ({"add_identity": "bob"}, "needs --user"),
        ({"user": "ada"}, "names the user for"),
        ({"multi_tenant": "maybe"}, "is not true or false"),
    ],
)
def test_setup_refuses_options_that_do_not_go_together(tenant_config, options,
                                                       message):
    """Each refusal says which option is out of place."""
    with pytest.raises(mysqlsh.Error, match=message):
        setup_cli.apply(options)


def test_an_unreadable_users_file_refuses_everyone(tenant_config, capsys):
    """users.json is an access control: broken means nobody, said out loud."""
    ada = _add("ada")
    key = tenants.issue_api_key(ada)
    with open(tenants.get_users_file_path(), "w", encoding="utf-8") as users_file:
        users_file.write("{not json")

    with pytest.raises(mysqlsh.Error, match="Could not read the users file"):
        tenants.read_users()

    verifier = auth.ApiKeyVerifier(auth.UserDirectory())
    assert verifier.verify_token_sync(key) is None
    assert "refusing all" in capsys.readouterr().err


def test_a_shell_without_groups_cannot_go_multi_tenant(tenant_config, monkeypatch):
    """Without groups every user's secrets would land in one place."""

    class _OldShell:
        def list_secrets(self, *args):
            if args:
                raise TypeError("too many arguments")
            return []

    monkeypatch.setattr(config, "_shell", lambda: _OldShell())

    assert tenants.secret_groups_supported() is False
    with pytest.raises(mysqlsh.Error, match="keeps secrets in groups"):
        tenants.set_multi_tenant(True)


def test_the_throttle_answers_with_a_429(tenant_config):
    """Refused tokens from one address for one user end in Too Many Requests."""
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["path"])
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    throttle = auth.AuthFailureThrottle(app, max_failures=2)
    ada = _add("ada")
    key = tenants.issue_api_key(ada)

    async def request(headers):
        sent = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "path": "/mcp",
            "client": (CLIENT_ADDRESS, 1234),
            "headers": headers,
        }
        await throttle(scope, None, send)
        return sent[0]["status"]

    async def scenario():
        bearer = [(b"authorization", f"Bearer {key}x".encode())]
        # Without a token nothing is counted: that is every client's first try.
        assert await request([]) == 401
        assert await request([]) == 401
        assert await request(bearer) == 401
        assert await request(bearer) == 401
        assert await request(bearer) == 429
        # The lifespan and other non-HTTP traffic passes straight through.
        await throttle({"type": "lifespan", "path": "-"}, None, None)

    asyncio.run(scenario())
    assert calls == ["/mcp"] * 4 + ["-"]


def test_a_sessionless_request_binds_to_its_user(multi_tenant, monkeypatch):
    """MCP 2026-07-28 has no sessions: an authenticated user is the binding.

    Claude Code speaks that revision and never sends an MCP session id; without
    this, db.connect refused every one of its calls. The connection is still
    bound: another user is refused it on the very same address.
    """
    ada = _add("ada")
    bob = _add("bob")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    tools = _db_tools(monkeypatch)

    connection_id = tools["db.connect"](_context(ada, session_id=None), URI)

    with pytest.raises(ToolError, match="No open connection"):
        tools["db.close"](_context(bob, session_id=None), connection_id)
    tools["db.close"](_context(ada, session_id=None), connection_id)


def test_a_gateway_keeps_a_connection_across_sessions_and_addresses(multi_tenant,
                                                                    monkeypatch):
    """Bound to the user and their authorization, not the session or address.

    Arcade opens a new MCP session for every tool call, from whichever of its
    addresses: bound to the session, the connection db.connect opened was gone
    by the next call. The same user's other clients still cannot use it.
    """
    ada = _add("ada")
    config.store_connection(URI, PASSWORD, mcp_user_id=ada)
    tools = _db_tools(monkeypatch)

    connection_id = tools["db.connect"](
        _context(ada, session_id="s1", address="203.0.113.7", grant="g1"), URI
    )

    with pytest.raises(ToolError, match="No open connection"):
        tools["db.close"](_context(ada, session_id="s2", grant="g2"), connection_id)
    with pytest.raises(ToolError, match="No open connection"):
        tools["db.close"](_context(ada, session_id="s2", client_id="other"), connection_id)
    tools["db.close"](
        _context(ada, session_id="s2", address="203.0.113.8", grant="g1"), connection_id
    )


def test_a_sessionless_client_of_an_unauthenticated_server_is_told_why(tenant_config,
                                                                      monkeypatch):
    """Refused over HTTP (the user's decision), saying stdio or multi-tenant mode."""
    general.set_multi_tenant(False)
    general.set_active_transport(general.TRANSPORT_STREAMABLE_HTTP)
    try:
        tools = _db_tools(monkeypatch)
        with pytest.raises(ToolError, match="--multiTenant=true"):
            tools["db.connect"](_context(None, session_id=None), URI)
    finally:
        general.set_active_transport(None)
