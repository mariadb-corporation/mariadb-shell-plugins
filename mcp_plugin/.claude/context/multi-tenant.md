# Multi-tenant mode and OAuth2: implementation plan

The plan for serving several users from one MCP server: users managed by
`mcp.setup`, an API key per user, every user's secrets in a shell **secret
group** of their own (phase 1), then OAuth2 through Keycloak or through a built-in
authorization server that logs users in against MariaDB (phase 2).
**Nothing here is built yet.** Branch `wip/mcp-multi-tenant`, plan only, reviewed
with the user on 2026-10-06. Decisions marked **(user)** were the user's call.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Builds on the connection
machinery in [connections.md](connections.md) and the server in
[server.md](server.md). The secret-group API this rests on is described in
[environment.md](environment.md) ("Shell secret groups").

## Inputs

- **Shell secret groups**: one group per user, named by the user's UUID. See
  environment.md for the API and its limits. The limit that shapes the design:
  **groups partition, they do not protect**, so every tenant's isolation is
  enforced by the plugin, and the server should run under an OS account of its own.
- **Customer input on OAuth2:** Snowflake is the reference. The database is the
  OAuth authorization server, scopes mirror database roles, tools follow from the
  user's grants, there are no shared secrets, and connections are bound to the
  user. Details from Arcade's Snowflake page:
  - Clients request `session:role:all`.
  - The session runs as the user's `DEFAULT_ROLE`, capped by `ALLOWED_ROLES_LIST`.
  - There is no dynamic client registration.
  - Clients discover the authorization server through RFC 9728 Protected Resource
    Metadata.
  - "Access to the MCP server doesn't imply access to what it exposes."
- **MariaDB Server will NOT get an OAuth/JWT authentication plugin (user).** No
  token can log in to MariaDB, so the "pass the token through to the database"
  option is out. The MCP spec forbids passing tokens through anyway (below).
  Phase 2 considers **A (Keycloak)** and **C (built-in authorization server)**
  only **(user)**.
- **MCP authorization spec, revision 2026-07-28** (read 2026-10-06):
  - The server is an OAuth 2.1 resource server and **MUST** serve RFC 9728 PRM.
  - It **MUST** validate that a token was issued for it (audience, RFC 8707) and
    answer an invalid or expired token with 401.
  - It **MUST NOT accept or pass on any other token.**
  - It SHOULD put `scope` in the `WWW-Authenticate` challenge, and SHOULD answer a
    token with too few scopes with 403 `insufficient_scope` and the full set of
    scopes needed.
  - Client registration, in order of preference: **Client ID Metadata Documents
    (CIMD, SHOULD)**, pre-registration, then Dynamic Client Registration
    (**deprecated**, kept for compatibility).
  - Authorization servers SHOULD send `iss` in the authorization response
    (RFC 9207) and advertise `authorization_response_iss_parameter_supported`.
  - PKCE is required.
  - stdio "SHOULD NOT" use this flow, which fits stdio being refused in
    multi-tenant mode.
- **Keycloak** ([MCP guide](https://www.keycloak.org/securing-apps/mcp-authz-server),
  documented against nightly 26.8):
  - MCP 2025-03-26 is supported; later revisions are experimental.
  - RFC 8707 resource indicators are experimental (`--features=resource-indicators`).
    Without them, the audience comes from an **`Audience` mapper ("Included Custom
    Audience" = the MCP server URL) on Optional client scopes**.
  - Dynamic client registration is supported through anonymous-registration
    policies.
  - CIMD is experimental (`--features=cimd`), and ChatGPT's CIMD is NOT supported.
  - PKCE is required for public clients (VS Code, Claude Code, Claude Desktop).
  - Issuer `https://<host>/realms/<realm>`. Access tokens are RS256 JWTs with a
    5-minute lifespan by default.
- **Bundled Python** (checked 2026-10-06): PyJWT 2.15 (`PyJWKClient` for JWKS),
  cryptography 50, python-multipart, starlette and httpx are all present as MCP
  SDK dependencies. **No new dependency is needed**, but `requirements.txt` should
  name `pyjwt[crypto]` explicitly once the plugin imports it. The shell reports
  **mcp 2.2.0**, with 2.0.0, 2.1.1 and 2.2.0 `dist-info` directories side by side;
  environment.md still says 2.1.1, so re-check that before trusting either.
- **MCP SDK auth hooks**: read in the bundled tree on 2026-10-06, before the user
  moved the shell to the latest `mcp` release. **Re-check every point below
  against the new SDK** (phase 0):
  - `MCPServer(auth=AuthSettings(...), token_verifier=... | auth_server_provider=...)`.
    `streamable_http_app()`, which `_serve_streamable_http` already calls, then
    installs `BearerAuthBackend` + `AuthContextMiddleware`, wraps `/mcp` in
    `RequireAuthMiddleware` (401 with `WWW-Authenticate`, or 403 when
    `required_scopes` are missing), and serves PRM when `resource_server_url` is
    set.
  - `TokenVerifier.verify_token(token) -> AccessToken(client_id, scopes,
    expires_at, resource, subject, claims)`.
  - With an authenticated user, the **session-owner check** in
    `streamable_http_manager` becomes active. It is inert today (see
    connections.md) and binds an MCP session id to `(client_id, iss, sub)`.
  - The authorization-server side ships `/authorize`, `/token`, optional
    `/register` and `/revoke`, AS metadata, and the PKCE S256 check.
    `OAuthMetadata` has `client_id_metadata_document_supported` and
    `authorization_response_iss_parameter_supported` fields, but **`build_metadata`
    sets neither**, so they need our own metadata route. **Custom routes are added
    AFTER the SDK's**, so the override has to be inserted ahead of them.
  - `AuthSettings.issuer_url` is REQUIRED and must be https unless it is loopback.
  - `tools/list` is `MCPServer._handle_list_tools(ctx, params)`. Filtering it per
    caller means overriding that private method in a subclass, which needs a test
    pinning it against SDK upgrades.

## Decisions

1. **The mode is a persisted setting.** `mcp setup --multiTenant=true|false` writes
   `"multiTenant": true` to `settings.json`, and `mcp.startServer` reads it.
2. **`users.json` is keyed by the `mcp_user_id` UUID (user), and a user holds any
   number of identities (user).** It lives next to `settings.json` and is written
   `.tmp` + `os.replace`. It holds nothing secret:

   ```json
   {"version": 1,
    "users": {
      "3f2a8c1e-0b6d-4e7a-9c1f-5d2e8b7a4c60": {
        "name": "Ada Lovelace",
        "identities": [
          {"type": "email",    "value": "ada@example.com"},
          {"type": "userId",   "value": "ada"},
          {"type": "oauth",    "issuer": "https://kc.example.com/realms/mariadb", "subject": "6b1f…"},
          {"type": "mariadb",  "server": "mariadb://db1.example.com:3306", "account": "ada@%"}],
        "scopes": ["mcp:db", "mcp:msm"],
        "defaultRole": "analyst",
        "allowedPaths": ["/srv/projects/ada"],
        "tokenEpoch": 0,
        "disabled": false,
        "created": "2026-10-06T12:00:00Z",
        "apiKeyCreated": "2026-10-06T12:00:00Z"}}}
   ```

   - The key is a **lower-case canonical uuid4**: the `mcp_user_id` and the secret
     group. It is never reused.
   - **Each identity is unique across all users.**
     - `email` is compared case-insensitively.
     - `oauth` is the `(issuer, subject)` pair, because `sub` is unique only per
       issuer.
     - `mariadb` is `(normalized server URI, CURRENT_USER())`, written by the
       built-in authorization server in phase 2c.
   - `--user=` takes the UUID or any identity value.
   - **Several identities of every type, including several MariaDB accounts,
     may belong to one user (user).**
   - `scopes`, `defaultRole` and `tokenEpoch` are used from phase 2. Phase 1
     writes the defaults (`scopes` = all tool scopes, no `defaultRole`, epoch 0),
     so the file format does not change later. There is deliberately no
     `allowedRoles` field until role scopes are built (see "Later" in
     [oauth.md](oauth.md)).
3. **Inside a user's group:**
   - `MCP:CONN:<uri>` is the connection password. It is the same key as today,
     and the 256-byte rule is unchanged because the group is not part of the key.
   - **`MCP:API_KEY` holds the API key in PLAIN TEXT (user)**, so an admin can show
     it again with `--showApiKey`. Groups partition rather than protect, so this
     is exactly as safe as the connection passwords beside it, and the README says
     so.
   - From phase 2c, a login's grant lives here too: `MCP:OAUTH:GRANT:<grant id>`
     (the grant record, with refresh tokens hashed) and `MCP:OAUTH:CONN:<grant id>`
     (the login connection, `{uri, password}`). Both are deleted when the grant
     ends. With `loginConnectionStore: "memory"` neither is written. See 2c in
     [oauth-builtin.md](oauth-builtin.md).
4. **API key format:** `mdbmcp_<uuid without dashes>_<43-char base64url of 32 random
   bytes>`.
   - The UUID inside the key lets the verifier read ONE group. UUIDs are not
     secret.
   - Keys are compared with `hmac.compare_digest`.
   - One key per user. `--rotateApiKey` replaces it.
5. **The msm tools stay in multi-tenant mode (user)**, limited to the caller's
   `allowedPaths`. **`sandbox` and `migrator` are not served**, and asking for
   them explicitly is an error. **`--gui` is refused.** The default groups become
   `db,msm`.
6. **stdio is refused in multi-tenant mode (user).**
7. **No TLS on a non-loopback bind in multi-tenant mode only WARNS (user).** The
   warning names the leak: bearer tokens and API keys sent in clear.
8. **Admin view across users (user):** `mcp setup --show --allUsers` (and `--json`)
   uses `list_secrets({"allGroups": True})`.
   - It shows each user's identities, connections (URIs only), allowed paths, and
     whether an API key exists.
   - It also reports **orphan groups**: groups with secrets but no `users.json`
     entry, left by an interrupted removal. `--purgeOrphanGroups` cleans them up.
   - Secrets are never shown, except a single user's API key with `--showApiKey`.
9. **Generic-group (single-tenant) connections are invisible in multi-tenant mode.**
   There is no shared list and no migration when the mode is switched.
10. **Path elicitation must not self-grant in multi-tenant mode**, because the
    client is the party being restricted. It refuses with "ask the administrator
    to run mcp setup".
11. **One principal type for every way of authenticating.**
    `Principal(mcp_user_id, scopes, role, auth_method)`, built by whichever
    verifier accepted the token. Tool code never looks at `auth_method`.

## Phase 0: verify before building (spikes, no plugin code)

Done on 2026-10-06 for phase 1:

- [x] **The SDK update:** the shell now has MCP SDK **2.3.0** (see environment.md for
      how that was established; `importlib.metadata` says 2.2.0 and is wrong). Every
      auth hook listed under Inputs is unchanged in 2.3.0, and the suite passed
      unchanged on it (465 / 2 skipped).
- [x] **The group API, live** (keychain on macOS, and the plaintext helper the test
      runner uses): one key in two groups holds two values; `generic` does not see
      them; `delete_all_secrets({"group": a})` leaves group b; `allGroups` lists
      `{group, key}` entries (shell `Dict`s, indexable by key). An invalid group raises
      **`ValueError`** ("Option 'group' must be 'generic' or a UUID"). An upper-case UUID
      is accepted and lower-cased. A missing secret raises **`RuntimeError`** ("Could
      not find the secret").
- [x] **A read from a worker thread works** (a `threading.Thread`, the same as the
      `anyio` workers the verifier uses).
- [x] **Cost:** about **30 ms per keychain read**, about 35 ms for an `allGroups`
      listing, hence the key cache in `auth.UserDirectory`.
- [ ] Linux: login-path and secret-service with groups, and a headless macOS keychain.
      Not checked; the README's advice (an OS account of its own) does not depend on
      it.
- [x] **The feature gate** is a probe (`tenants.secret_groups_supported()` lists the
      nil-UUID group). It was not tried against an older shell; a test stands in a
      shell whose `list_secrets` refuses the argument.
- [ ] Phase 2 spikes:
  - [ ] Confirm `SET ROLE` behaviour on MariaDB 12.3 for an account whose own
        grants are minimal.
  - [ ] Does `SET SESSION TRANSACTION READ ONLY` stop DDL as well as DML?
  - [ ] Keycloak 26.x in a container: an Audience mapper on Optional client scopes
        puts our URL in `aud`; anonymous dynamic client registration works for
        Claude Code; check the `typ` claim of access tokens versus ID tokens.
  - [ ] Can the SDK's own `mcp.client.auth` OAuth client drive a full flow against
        a test server? It would be the conformance harness for 2b and 2c.

## Phase 1: multi-tenant mode with API keys

**Status: BUILT on 2026-10-06** (branch `wip/mcp-multi-tenant`). Suite **512 passed, 2
skipped, 96%** (was 465 / 98%). New modules: `lib/tenants.py` (users, identities,
groups, API keys; shell plugin code, `mysqlsh.Error`) and `lib/auth.py`
(`ApiKeyVerifier`, `UserDirectory`, `AuthFailureThrottle`, `build_auth`). New tests:
`tests/unit/test_multi_tenant.py` (47, one of them a real HTTP server with two users),
plus the `tenant_config` fixture in conftest.py (settings.json and users.json backed up as
BYTES; every group the test created purged). Verified by hand through the real shell
CLI: `--multiTenant=true/false`, `--addUser`, `--user=… --addConnection`, `--show
--allUsers`, the stdio refusal, and an HTTPS server (self-signed) answering no token with
401 + `WWW-Authenticate` and a valid key with 200.

**As built, where it differs from or adds to the plan below:**

- **Users removed or disabled lose their connections without a reaper change.**
  `auth.UserDirectory` stats `users.json` on every token check, and when it changed it
  calls `db_functions.drop_connections_of_inactive_users(active_ids)`. Their requests are
  refused by the SDK (401) from that moment anyway. The reaper is untouched.
- **The caller check is in the `tool_registrar` wrapper** (`_check_caller`), which finds
  `ctx` by binding the call to the tool's signature. That is why the non-GUI
  `db.list_connections` now takes `ctx` (the SDK hides it from clients). The check is a
  no-op unless `general.is_multi_tenant()`.
- **`ClientIdentity` has a third field, `user`**, filled from `general.get_principal(ctx)`
  inside `get_client_identity`. So all 9 call sites carry it without a change, and
  `describe_client` writes `user=` only when there is one, so single-tenant log lines
  are unchanged.
- **`db.connect` now checks the identity BEFORE resolving the URI.** In multi-tenant mode
  it fails closed without a user, and the user is needed to know which group to resolve
  in.
- **`config` takes `mcp_user_id=None` on every connection function.** `secret_options()`
  is the one place the group is turned into the shell's options argument.
  `upgrade_connection_keys` and the `connections.json` details are generic-group only,
  and `store_connection` refuses details with a user.
- **`config.save_settings` is now atomic** (`.tmp` + `os.replace`), since settings.json
  holds the mode.
- **The interactive menu does NOT toggle the mode.** `mcp setup --multiTenant` is the
  only switch, because adding a menu entry would renumber the single-tenant menu that
  tests script by number. In multi-tenant mode the menu is the user menu
  (`setup._tenant_menu`).
- **Extra `mcp setup` options:** `--setScopes` (with `--user`) and `--showApiKey`.
  `--json` also applies to `--addUser`/`--rotateApiKey`/`--showApiKey` when those are the
  only actions; `--show --user=X` shows one user.
- **`AuthSettings.issuer_url` is `http://localhost`** for the API-key-only server: the SDK
  insists on one, and with a verifier but no provider and no `resource_server_url` it
  serves no route that uses it.
- **NOT done in phase 1, moved to 2a:** `defaultRole` → `SET ROLE` on session open, and
  per-caller `tools/list` filtering. Scope gating at CALL time is done.
- **Revert probes run:**
  - comparing only `ClientIdentity[:2]` in `is_accessible_from` fails
    `test_a_connection_is_bound_to_its_user`
  - removing `_check_caller` fails the no-user and missing-scope tests
  - dropping the user from the identity fails 5 tests

### 1a. Config layer: `config.py` and a new `tenants.py`

- **`lib/tenants.py`** is shell plugin code, so it raises `mysqlsh.Error`. It holds:
  - `users.json` I/O: `list_users`, `get_user(uuid)`,
    `find_user(identifier)` (UUID, email, userId, `issuer|subject`), `add_user`,
    `remove_user`, `add_identity`, `remove_identity`, `set_user_*`
  - `is_multi_tenant()`
  - `issue_api_key(uuid)`, which stores the key in plain text in the group,
    stamps `apiKeyCreated` and returns it
  - `get_api_key(uuid)`
  - `verify_api_key(token) -> user | None`
  - `secret_groups_supported()`
  - `list_groups()` (all groups, for the admin view and orphan detection)
- **`config.py` gets a `group` threaded through every secret call.** The ~10 calls
  go through one `_secret_options(mcp_user_id)` helper. Every connection function
  gains `mcp_user_id=None`, with None meaning the `generic` group, exactly as
  today.
  - `kind` picks the key prefix and `mcp_user_id` picks the group. They are
    independent, and in multi-tenant mode only `kind="mcp"` exists.
  - `upgrade_connection_keys` stays generic-only.
  - No `connections.json` details are written for tenants (those are GUI-only).
  - `is_path_allowed(path, mcp_user_id=None)` reads the user's `allowedPaths`.
- **User removal order:** `delete_all_secrets({"group": id})` first, then the
  `users.json` entry. If it is interrupted in between, the entry is still there to
  be removed again. The reverse order would leave an orphan group, which the admin
  view reports anyway.

### 1b. `mcp.setup`

- **Mode and users:**
  - `--multiTenant=true|false`
  - `--addUser` with `--email`, `--userId` and `--name` (at least one identity),
    which prints the UUID and the API key (`--json` for scripts)
  - `--removeUser=<id>`
  - `--addIdentity` / `--removeIdentity` with `--user=`
  - `--rotateApiKey=<id>`
  - `--showApiKey=<id>`
  - `--disableUser` / `--enableUser`
- **Per-user resources:** `--user=<id>` is **required** with `--addConnection`,
  `--deleteConnections`, `--addPaths` and `--deletePaths` in multi-tenant mode, and
  **refused** in single-tenant mode.
- **Admin view:** `--show --allUsers [--json]`, plus `--purgeOrphanGroups`.
- **Order inside `apply()`:**
  1. mode
  2. user removals
  3. user additions
  4. identity changes
  5. enable/disable
  6. key rotation
  7. connection deletions, then additions
  8. path changes
  9. migrator (refused in multi-tenant mode)

  It stays fail-fast, as today.
- **Interactive menu:** when multi-tenant is on, "Manage users" comes first, and
  the connection and path items ask for the user first (`prompts.select`).
  Prompts reuse `setup_prompts`, never hand-rolled ones.

### 1c. Server: authentication and the request principal

- **`start()` checks**, in multi-tenant mode:
  - refuse stdio, `--gui`, `sandbox` and `migrator`
  - require secret-group support and at least one enabled user
  - warn about missing TLS on a non-loopback bind
- **The server is built with an `ApiKeyVerifier`:**
  `build_mcp_server(function_groups, auth=...)` →
  `MCPServer(auth=AuthSettings(issuer_url=<own base URL>,
  resource_server_url=None), token_verifier=...)`. The verifier returns an
  `AccessToken` with:
  - `client_id="mcp-api-key"`
  - `subject=<uuid>`
  - `scopes=user.scopes`
  - `claims={"iss": "mariadb-mcp:api-key", "mcp_user_id": uuid}`
- **The verifier chain is built now, even though it has one member.** Dispatch is on
  the token's shape: `mdbmcp_` means an API key; a three-part JWT means OAuth
  (phase 2). API keys keep working next to OAuth.
- **Cache and revocation:**
  - The cache maps a user to `(sha256 of the key, read at)`. It is flushed
    whenever `users.json`'s mtime changes (one `stat` per request), so a removal,
    disable or rotation by `mcp setup` in another process applies to the next
    request.
  - A 60s TTL covers a secret changed without `users.json` being touched.
  - A miss re-reads, so a freshly rotated key works at once.
  - The secret read runs in `anyio.to_thread`.
  - Failed attempts are logged with no token text and rate-limited per
    `(peer, user named in the key)`, with only a high per-peer ceiling. A gateway
    such as Arcade sends every user's requests from a few addresses, so a pure
    per-peer limit would let one user lock out all the others (see 2d in
    [oauth-builtin.md](oauth-builtin.md)).
- **`general.get_principal(ctx)`** reads `ctx.request_context.request.user`, the
  same chain `get_client_identity` uses, which also works on a worker thread.
  **In multi-tenant mode, no principal means fail closed**, in the
  `tool_registrar` wrapper every tool already passes through.
- **`ClientIdentity` gains `user`.** The connection binding stays ONE tuple
  equality, and the unknown-id error stays byte-identical. Single-tenant mode
  passes `user=None` on both sides.
- **TLS:** new `ssl_certfile` / `ssl_keyfile` options go to `uvicorn.Config`.
  The other supported setup is a TLS-terminating reverse proxy plus
  `allowed_hosts`. Behind a proxy the address half of the binding collapses, and
  the user and session halves carry it.

### 1d. Tenant isolation in the tools

- **db:** every connection lookup takes `principal.mcp_user_id`:
  - `list_connections`, `connect`, the password read
  - `_open_session`'s re-validation
  - `find_connection` / `resolve_connection_uri`
- **Caps:** `MAX_CONNECTIONS_PER_USER` is added, and `MAX_CONNECTIONS_TOTAL`
  becomes a `startServer` option.
- **The reaper** drops the connections of removed or disabled users (the same
  `users.json` mtime signal).
- **Audit log:** `describe_client` adds `user=<uuid prefix>`, plus events for API
  key accepted/refused and user removed/disabled.
- **msm and `db.execute_sql_script`:** paths are checked against the caller's
  `allowedPaths`, and there is no self-granting elicitation.
- **Tool gating:** each tool group requires its scope (`mcp:db`, `mcp:msm`),
  checked in the `tool_registrar` wrapper. API key users have both by default.
  This is the check phase 2 relies on.

### 1e. Tests and documentation

- `clean_config` covers `users.json`. A `tenant` fixture creates users with random
  UUIDs and runs `delete_all_secrets` for each one in teardown, so the developer's
  real groups are never touched.
- Must-have tests, each with a revert probe:
  - User B cannot list, connect to or use user A's connection, even with A's MCP
    session id.
  - A removed or disabled user is refused on the next request, and their
    connections are dropped.
  - Rotation stops the old key and the new one works at once.
  - No token or a bad token gets 401, and no tool runs.
  - stdio, `--gui`, `sandbox` and `migrator` are refused.
  - Elicitation does not grant a path.
  - The admin view lists all users and reports an orphan group.
  - Single-tenant behaviour is unchanged, and the full existing suite passes.
- One real-shell, real-HTTP end-to-end test with two users.
- Add the new attack surface to [security-review.md](security-review.md) as new
  numbered entries.
- README:
  - multi-tenant setup
  - plain-text API keys and "groups partition, they do not protect" (run the
    server under its own OS account)
  - TLS
  - the "no authentication" section rewritten for this mode

## Phase 2: OAuth2

Planned in detail in [oauth.md](oauth.md): 2a shared groundwork, 2b Keycloak,
2c a built-in authorization server logging users in against MariaDB, and the
phase 2 decisions the user made on 2026-10-06.

## Next steps

1. Phase 0 spikes, with results recorded here. The first one is re-reading the
   SDK after the user's update.
2. Phase 1 (1a+1b, then 1c+1e as two stacked PRs), including the parts of 2a ([oauth.md](oauth.md))
   that phase 1 already needs:
   - the `Principal`
   - tool-scope gating
   - the verifier chain
   - the `users.json` fields
   - `defaultRole` applied on session open
3. 2a → 2b → 2c, a PR each.
