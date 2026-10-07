# Multi-tenant mode and OAuth2: implementation plan

The plan for serving several users from one MCP server: users managed by
`mcp.setup`, an API key per user, every user's secrets in a shell **secret
group** of their own (phase 1), then OAuth2 through Keycloak or through a built-in
authorization server that logs users in against MariaDB (phase 2).
Planned with the user and **built** on 2026-10-06, on branch `wip/mcp-multi-tenant`
(PR #37); see "Next steps" for where it stands. Decisions marked **(user)** were the user's call.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Builds on the connection
machinery in [connections.md](connections.md) and the server in
[server.md](server.md). The secret-group API this rests on is described in
[environment.md](environment.md) ("Shell secret groups").

## Inputs

The external facts this plan rests on - secret groups, the customer's input, the MCP
authorization spec, Keycloak, the bundled Python and the SDK's auth hooks - are in
[multi-tenant-inputs.md](multi-tenant-inputs.md).

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

**As built, where it differs from or adds to the plan ([multi-tenant-phase1-plan.md](multi-tenant-phase1-plan.md)):**

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

The plan, sections 1a-1e (config layer, `mcp.setup`, server authentication,
tenant isolation in the tools, tests and docs), is in
[multi-tenant-phase1-plan.md](multi-tenant-phase1-plan.md).

## Phase 2: OAuth2

Planned in detail in [oauth.md](oauth.md): 2a shared groundwork, 2b Keycloak,
2c a built-in authorization server logging users in against MariaDB, and the
phase 2 decisions the user made on 2026-10-06.

## Next steps

**Status on 2026-10-07: phases 1 and 2 are built and in PR #37**
(`wip/mcp-multi-tenant` → `main`), reviewed by Rene once; his 11 comments are fixed in
`fe4dbfe6`, pushed, and each thread answered (see below). The as-built notes are in the
"Status" sections here and in [oauth.md](oauth.md), and the security review is
[security-review-multi-tenant.md](security-review-multi-tenant.md) (M1..M26).
Later commits: `850626ee` (httpx2), `0ec99ee9` and `8f38d498` (sign-in page),
`046d9321` (context), `72a64f89` (Arcade: tool names, binding), `176aee35` (the
simplification pass).

**Simplification pass (2026-10-06, late evening, `176aee35`):** a `/simplify` review
(reuse, simplification, efficiency, altitude) of the new Python, applied in one commit;
the full list of what was fixed and what was deliberately skipped is in PR #37's
description ("Simplification pass over the new code"). The names that changed, for
reading the code and the context files above:

- `config.write_json_file` (the one atomic tmp+`os.replace` writer),
  `general.utc_timestamp()` (the one "now"; `oauth_config.now()` and both `_now()` are
  gone), `general.principal_from_request()` (the `_RequestContext` adapter is gone).
- `auth.FailureCounter` is the sliding-window counter behind `AuthFailureThrottle` AND
  the sign-in page's limiter (`_LoginLimiter` is gone; the provider has
  `_login_failures` and `_sign_in_blocked`). It sweeps stale keys past 1024 entries.
- `tenants.scopes_of(record)` is the one reading of a user's scopes;
  `tenants.find_user_by_identity` / `users_with_email` take an optional `users` dict
  (the Keycloak verifier passes the directory's cache).
- `config.get_allowed_paths(mcp_user_id=None)` / `set_allowed_paths(paths,
  mcp_user_id=None)` delegate to `tenants` for a user; `setup_cli._allowed_paths` /
  `_set_allowed_paths` are gone. `config._connection_store(mcp_user_id)` is the
  upgrade-then-`secret_options` step every connection function uses.
- `oauth_builtin.login_connection_uri()` (URI from the cached grant, no keychain read)
  next to `login_credentials()`; `BuiltinAuthProvider.live_grant_uri`;
  `_end_stored_grants(matches)` behind `end_grants_of_client/user`.
- `setup_cli._reject_unknown(options, known_options)` and
  `_actions(options, action_options, presence_options)` serve all three setup
  commands; `setup_prompts.select_action()` is the menu step; `setup_oauth._section`
  is gone.
- `_serve_streamable_http` has no `throttle_auth_failures` flag (`auth is not None`
  decides); `_Connection.mcp_user_id` is a property over `client.user`;
  `AuthBundle.mode` and `build_auth(verifier=)` are gone.
- Skipped on purpose (behaviour risk): a module-wide mtime cache for `read_clients()` /
  `get_default_role` / `get_allowed_paths` (the provider got its own for the clients
  in the review round below); `check_issuer` via `_same_url` (would be
  case-insensitive); `_is_loopback_redirect` via `is_loopback_host` (wider set);
  `KeycloakAdmin` on httpx2; splitting `GrantStore`.

**Review round (2026-10-07):** Rene's review of `28f39f8e` (11 inline comments from his
`/code-review`). Security findings are M24..M26 and M17's rebinding in
[security-review-multi-tenant.md](security-review-multi-tenant.md). The others, each
with a test and a revert probe (12 probes, all failing as they should):

- `check_ready(mode, public_url=None)`: `build_auth` passes the `start-server
  --publicUrl` override (`test_the_start_servers_public_url_counts_as_configured`).
- `server._users_are_created_at_sign_in()`: with an OAuth mode whose `autoProvision` is
  enabled, `_check_multi_tenant` no longer refuses a server with no users
  (`test_a_server_that_creates_users_at_sign_in_starts_without_any`).
- Error messages named options that do not exist (`mcp setup --publicUrl`,
  `--oauthIssuer`, `--listOAuthClients`, …); all now name `mcp setup-oauth` options,
  as do two module docstrings (`--oauthMode`, `--addOAuthClient`).
  `test_every_option_an_oauth_refusal_names_exists` maps each named option to
  `setup_oauth.KNOWN_OPTIONS`.
- `BuiltinAuthProvider._known_clients()`: the registered clients cached by
  `(st_mtime_ns, st_size)` of `oauth_clients.json`; `live_grant` used to read and parse
  the file on every token check
  (`test_a_token_check_reads_the_clients_file_only_when_it_changed`).
- The `no_verify` docstring in `general.py` lost its stale `oauth_issuer` sentence.

Then a second `/code-review` round (whole branch against `main`, the fixes included)
found three issues in those fixes: the CIMD fetch dialed only the first of the
string-sorted addresses (now resolver order with fallback, M17), ending a grant left
with no scopes (taken back, M24), and the pending-sign-in eviction (closed with
`auth.AuthorizeRateLimit`, 30 per address per minute, the user's choice; M26). That round read the uncommitted diff fully but only spot-checked the
committed branch.

Suite after it: **595 passed, 3 skipped, no warnings, 97%** (5309 statements).
Rene's review also: asks whether `Co-Authored-By:` belongs in commit messages (a
team policy question for the user), and suggests everyone run `/code-review`,
`/simplify` and `/security-review` before opening a PR.

**Decided by the user** (2026-10-06):
- multi-instance support (M20) is skipped for now
- a single-tenant server keeps refusing sessionless (MCP 2026-07-28) clients over
  HTTP, and recommends multi-tenant mode (M22)
- signing-key overlap (M19) is built
- tool names: an opt-in `mcp setup --toolNameSeparator=_`, dots stay the default
- multi-tenant connections are bound to the user + grant (or client), not the session
  or address (M23)

**Open, in this order:**

1. PR #37: Rene's next look at `fe4dbfe6` (CI green at `e18acff8`; a
   `/security-review` found nothing HIGH or MEDIUM). Still open for the user or the team:
   the `Co-Authored-By:` question from his review.
2. The reference docs in `../mariadb-shell` (`wip/docs-ref`) are committed and pushed;
   they ride on mariadb-shell PR #59 ("Add MariaDB Shell reference docs …", open).
3. Verification still missing (a real Arcade project was DONE on 2026-10-06):
   - VS Code: the user has no Copilot access
   - `mcp setup-keycloak-realm` against a real Keycloak, which needs admin credentials;
     it is tested against a stand-in for the admin REST API
   - the Linux secret helpers, and Windows
4. Open M items:
   - `ssl-mode=VERIFY_IDENTITY` as the default for login servers (LOW, from the
     `/security-review`; the user's call)
   - a rate limit on `/register` (M18)
   - per-tool step-up at the HTTP layer (M9)
