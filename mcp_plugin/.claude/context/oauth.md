# OAuth2 (phase 2 of multi-tenant mode): implementation plan

Phase 2 of the multi-tenant plan: OAuth2 through Keycloak (option A), then a
built-in authorization server that logs users in against MariaDB (option C).
Planned with the user and **built** on 2026-10-06 (see Status).

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Builds on phase 1 in
[multi-tenant.md](multi-tenant.md). The facts this plan rests on - the customer's
Snowflake reference, the MCP authorization spec 2026-07-28, Keycloak's MCP support,
and the SDK's auth hooks - are in [multi-tenant-inputs.md](multi-tenant-inputs.md).

## Status

**BUILT on 2026-10-06** (2a, 2b, 2c and 2d), on branch `wip/mcp-multi-tenant` after
phase 1, in **PR #37**. Suite at the end of the session: **577 passed, 3 skipped (all
opt-in), 93%**; **585 passed, 3 skipped** after the Arcade round below; **584 passed,
4 skipped** after the simplification pass (`176aee35`, see
[multi-tenant.md](multi-tenant.md) "Next steps").

**Evening round (2026-10-06), after the PR was opened:**
- **CI fix:** the code and two test modules imported `httpx`, which the shell no longer
  ships (SDK 2.3 uses `httpx2`); now `httpx2` everywhere (`850626ee`). See
  working-practices.md.
- **Sign-in page restyled** after mrs_plugin's
  `default_static_content/index.html` (colours, welcome header, joined fields, round next
  button, error bubble, separator, footer), without its script (the CSP allows none).
  The MariaDB seal is `lib/assets/mariadb-seal.svg`, inlined with its `style`
  attributes stripped (CSP: nonce'd `<style>` only). The button's chevron is an inline
  SVG (a CSS-border chevron became a diamond in a restyled Firefox), the button has
  `appearance:none` (Safari), and the inputs `flex:1 1 250px` so the button sits at the
  field's end. Commits `0ec99ee9`, `8f38d498`. The user's screenshot is in the docs.
- **Arcade, tested for real** (see oauth-builtin.md 2d): dotless tool names
  (`--toolNameSeparator`) and the user + grant binding (M23), `72a64f89`.

**Last round of the session (after the PR was opened):**
- **Signing-key overlap (M19).** `rotate_signing_key` keeps `previous`
  (`{kid, pem, validUntil}`) for one access-token lifetime, and
  `--dropPreviousSigningKey` ends it. The provider verifies by the token's `kid`
  (`_verifying_key`) and reloads the keys every sweep and on an unknown kid (at most
  every 10s, `_KEY_RELOAD_MIN_INTERVAL`). Before this, a rotation by `mcp setup-oauth`
  only reached a running server at restart.
- **The sign-in page's checkboxes were huge:** `input[name]{width:100%}` caught them.
  Only `input:not([type])`, `input[type=password]` and `select` are full width now.
- **HTTPS messages** say `--sslCertfile`/`--sslKeyfile`, not the Python names.
- **Real clients:**
  - Claude Code 2.1.287 with an API key header (`claude -p --mcp-config`) worked after
    the M22 fix: `ada@%, mcp_access, 24.99`.
  - The user's interactive Claude Code OAuth sign-in to the built-in server worked.
    Claude Code identifies by **CIMD** (`client_id=https://claude.ai/oauth/claude-code-client-metadata`)
    with a `localhost:<port>/callback` redirect.
  - VS Code was not tested (no Copilot).
- **Reference docs** for all of this were written in
  `../mariadb-shell/docs-ref/content/mariadb-shell/mcp-server/`:
  - new pages `multi-tenant-mode.md` and `oauth-authentication.md`
  - updates to README, starting-the-mcp-server, configuring-access, automated-setup,
    security-and-session-handling, connecting-mcp-clients and SUMMARY.md

  Committed and pushed on that repo's `wip/docs-ref` (the user's `5ae870fa5`, then
  `42ea44ca4` for tool names, binding and Arcade).

- New modules: `lib/oauth_config.py` (settings, public URL, clients, keys),
  `lib/oauth_keycloak.py` (`KeycloakVerifier`), `lib/oauth_builtin.py` (the provider,
  grants, sign-in page, CIMD) and `lib/setup_oauth.py` (the `mcp setup` options).
- Tests: `tests/unit/test_oauth.py` (51). One is an opt-in live test against a real
  realm (`run_tests.py --keycloak`, `KEYCLOAK_*` variables).
- Script: `scripts/keycloak_realm_setup.py`, the realm preparation (stdlib only,
  admin REST API).

**Later the same day:** the OAuth options moved out of `mcp setup` into a command of
their own, **`mcp setup-oauth`** (`mcp.setupOauth`, `lib/setup_oauth.py`). It has its
own validation and `--show`/`--json`, plus an interactive menu without options, and drops
the `oauth` prefix: `--mode`, `--issuer`, `--addClient`, `--showClientSecret`,
`--setClientRedirectUris`, `--requiredRole`, `--revokeTokens`, …

- `--setDefaultRole` stays in `mcp setup`: it applies to API-key users too.
- The realm script became **`mcp setup-keycloak-realm`** (`mcp.setupKeycloakRealm`,
  `lib/setup_keycloak.py`). It asks for whatever is not given (the admin password with
  the shell's password prompt), adds `--grantRealmRoleTo`, and points this server at the
  realm unless `--configureServer=false`. It is tested against an in-process stand-in
  for the admin REST API; it has NOT run against a real Keycloak (no admin credentials).
- `oauth_clients.json` writes are now under `config.file_lock` (M8 in
  [security-review-multi-tenant.md](security-review-multi-tenant.md)).

Where the notes below say `mcp setup --oauth…` or `--…OauthClient…`, read the
`setup-oauth` names.

**Option spellings:** the shell builds camelCase from snake_case by capitalizing
each word, so it is **`--addOauthClient`, `--showOauthClientSecret`,
`--setOauthClientRedirectUris`** and so on, never `OAuth`. This file and
oauth-builtin.md were corrected to match.

**As built, where it differs from or adds to the plan below:**

- **2a**
  - `general.Principal` gained `grant_id` (claim `grant`).
  - `auth.AuthBundle` carries settings + verifier or provider.
  - `auth.customize_app` puts the PRM route ahead of the SDK's, listing
    `scopes_supported`. It reuses `bundle.settings.issuer_url` /
    `resource_server_url` AS PARSED by AuthSettings, which preserves an empty
    path: a bare origin parsed anywhere else gains a trailing slash, and a client
    compares issuers byte for byte.
  - `InsufficientScopeMiddleware` is appended to `starlette_app.user_middleware`,
    which makes it the innermost middleware, after the SDK's authentication.
  - `auth.scoped_server_class()` overrides `_handle_list_tools`.
  - Default role: `db_functions._apply_default_role` runs ``SET ROLE `role` `` on
    every open, including reopens.
- **2b, from a live probe of the user's realm** (`http://192.168.10.252:8080`,
  realm `keycloak`):
  - discovery, `check_issuer` and the JWKS (one RS256 key) all work
  - the user's account `dba@zinner.org` cannot get a token ("Account is not fully
    set up": pending required actions)
  - anonymous DCR is refused by the "Trusted Hosts" policy

  So the live token test needs the realm prepared with the script by an admin.
  **Update, same day:** the user configured the realm by hand: client `mariadb-mcp`
  with direct access grants, client scopes `mcp:db`/`mcp:msm` with Audience
  `http://127.0.0.1:8080/mcp`, and `dba@zinner.org` (email verified, NO `mcp-user`
  role). Results:
  - `run_tests.py --keycloak` (KEYCLOAK_ISSUER=…/realms/keycloak,
    CLIENT_ID=mariadb-mcp) **passes**. The test now pre-adds a user by email when the
    account lacks `mcp-user`, so it checks linking.
  - **Verified end to end through a real server** in Keycloak mode on
    127.0.0.1:8080:
    - PRM names the realm, and no token gets 401
    - the sign-in was linked to `--addUser=dba@zinner.org` by its verified email
    - `db.connect` + `SELECT CURRENT_USER()` gave `dba@%` on a throwaway mariadbd

    The Keycloak server is remote while the audience is `127.0.0.1`: that is fine,
    since Keycloak never calls the MCP server.
  Nothing was created on that server. PyJWT's `PyJWKClient` refetches for an unknown
  `kid` at most every **30s** (a cooldown); the test ages
  `_last_successful_fetch` rather than disabling it.
- **2c**
  - `BuiltinAuthProvider.load_access_token` checks API keys too: the SDK refuses
    `auth_server_provider` together with `token_verifier`.
  - **Basic client auth:** the SDK checks a secret only where the client's
    `token_endpoint_auth_method` says, so `auth.BasicClientAuthMiddleware` rewrites
    a Basic header on `/token` and `/revoke` into form fields, and every
    confidential client is registered as `client_secret_post`.
  - **CSP `form-action` must include the client's redirect origin:** Chrome applies
    form-action to the redirect after the POST.
  - **Roles:** `APPLICABLE_ROLES` lists nested roles too. `requiredRole` counts
    nested ones (`roles["all"]`), but a configured `defaultRole` must be granted
    directly (`roles["direct"]`), since only those can be `SET ROLE`d. The required
    role is checked BEFORE the account is mapped, so an account without it is
    never provisioned.
  - **Refresh tokens:**
    - A token that matches neither the current nor the previous one is just
      rejected, and only true reuse of the previous one ends the grant; otherwise
      knowing a grant id would let anyone end it.
    - The grace check is strict (`<`).
    - `_issue_tokens` only rotates the CURRENT token.
  - Refresh token format: `mdbrt_<userhex>_<grantid>_<random>`. Grant record and
    login connection: `MCP:OAUTH:GRANT:<id>` and `MCP:OAUTH:CONN:<id>` in the user's
    group, cached 30s.
  - `mcp setup --removeOauthClient` / `--revokeTokens` end grants OFFLINE
    (`oauth_builtin.end_grants_of_client/_of_user` over `tenants.list_groups()`).
  - The provider is reached by `db_functions` through `oauth_builtin._provider`,
    set by `server.start` (`activate`).
  - The grant sweeper is the provider's own thread; the connection reaper is
    untouched.
- **2d:** two end-to-end tests against the sandbox with real accounts and roles:
  - an Arcade-shaped one (confidential client in two steps, PRM discovery, Basic
    auth, `oauth_bob` without the role refused, refresh race, revoke → 401)
  - the MCP SDK's own `OAuthClientProvider` (DCR, PKCE, `iss`, `tools/list` filtered
    to `db`)
- **Verified by hand** through the real shell against a throwaway `mariadbd`
  (scratchpad, port 33099): the whole flow, including `CURRENT_ROLE()` =
  `mcp_access` in the tool session.

## Overview

Order: **2a groundwork → 2b Keycloak (A) → 2c built-in authorization server (C).**
2b is smaller and builds the resource-server side both options share, against a
mature authorization server. 2c is closest to the customer's Snowflake picture,
but makes the plugin a security-critical authorization server, so it comes second
and reuses everything 2b proved. A server runs in ONE OAuth mode
(`oauth.mode`: `none` | `keycloak` | `builtin`) next to API keys, which stay on.

**Phase 2 decisions (user, 2026-10-06):**

1. **Link by verified email: yes.** A Keycloak login whose `email_verified` email
   matches an existing user's `email` identity is linked to that user.
2. **Auto-provisioning: yes**, in both 2b and 2c, behind a policy setting.
3. **2c keeps the DB password only as long as the grant that needs it.** The
   connection from a login is stored for the user, in their secret group (the
   default) or in memory, and deleted the moment the grant ends. It is never kept
   beyond that, and never logged. The connection reaper is NOT changed: login
   connections are idle-closed and reopened like any other (see 2c in [oauth-builtin.md](oauth-builtin.md)).
   (Clarified by the user on 2026-10-06, replacing a first reading that allowed
   no storage at all.)
4. **One user may hold several MariaDB accounts** (and several identities of any
   kind).
5. **No role scopes for now: the default role applies.** That is the user's
   `defaultRole` if an admin set one, otherwise the account's own `DEFAULT ROLE`
   on the server. `mariadb:role:<r>` scopes are deferred (see "Later").
6. **Dynamic client registration stays available for now.** In 2c it is
   implemented and enabled by default, and an admin can turn it off. In 2b it is
   Keycloak's job (anonymous-registration policy).
7. **Grants last 90 days by default (user)**, matching Snowflake's default
   refresh-token validity (`OAUTH_REFRESH_TOKEN_VALIDITY` = 7776000 s). There is no
   idle timeout by default; both are configurable.
8. **2c must support a Snowflake-style setup through Arcade (user).** The gaps
   found against Arcade's Snowflake guide are built into 2c, and the setup is
   described in "2d. Arcade" in [oauth-builtin.md](oauth-builtin.md).

## 2a. Shared groundwork

- **Public URL.** `settings.json` gets `"publicUrl": "https://mcp.example.com/mcp"`
  (`mcp setup --publicUrl`), with a `startServer` option to override it.
  - It is the canonical resource identifier: PRM `resource`, the `aud` check, and
    the `WWW-Authenticate` metadata URL.
  - It cannot be derived from the bind address behind a proxy.
  - It must be https unless it is loopback.
  - Its host is added to the Host/Origin allow list automatically.
- **Scope vocabulary:**

  | Scope | Grants |
  | --- | --- |
  | `mcp:db` | the db tools (connect, browse, run SQL) |

  `mcp:msm` existed until 2026-10-09, when the msm group left multi-tenant mode (see
  decision 5 in [multi-tenant.md](multi-tenant.md)). Older mentions of it below
  describe the state at the time.

  - **There are no read/write/admin scopes on purpose.** What a user can do is
    decided by **the database's own privileges for the account and its default
    role**, which is Snowflake's "access to the server is not access to what it
    exposes". A tool scope only decides whether a tool group is offered.
  - PRM `scopes_supported` = `["mcp:db"]`. `offline_access` is never
    listed (spec).
- **Default role.** When a user has a `defaultRole`, `_open_session` runs
  `SET ROLE <role>` right after opening, and again on every transparent reopen.
  Without one, nothing is run, and the server applies the account's own
  `DEFAULT ROLE` at login. If the role is not granted to the connection's account,
  the session fails with a clear error. This applies equally to API-key users, so
  it can land in phase 1.
  - **Documented requirement:** least privilege only holds when the DB account has
    minimal direct grants, because MariaDB adds the account's own privileges to
    the active role's.
- **Per-caller tool lists.** A `MCPServer` subclass overrides `_handle_list_tools`
  to show only the tools the principal's scopes allow, with a test pinning the
  private hook.
- **Insufficient scope.**
  - At the HTTP layer, a token with none of the tool scopes gets 403
    `insufficient_scope` with `scope="mcp:db mcp:msm"`.
  - Inside `tools/call` a JSON-RPC error is the only channel. The `ToolError`
    names the missing scope.
  - Optional later: a small ASGI check that peeks at `tools/call` and answers
    403 with `scope=` for proper step-up.
- **Verifier chain:** `CompositeVerifier([ApiKeyVerifier, <oauth verifier>])`,
  dispatched on token shape. Every verifier produces the same `Principal`.
- **Revocation by epoch:** bumping `tokenEpoch` in `users.json` invalidates every
  OAuth token of that user at once (2c tokens carry the epoch; for 2b the plugin
  records the epoch and the time it was bumped, and refuses tokens issued before
  it). `mcp setup --revokeTokens=<user>`, which disabling or removing a user also
  does.
- **Auto-provisioned users** get `scopes` from the mode's `defaultScopes`, no
  `defaultRole`, no `allowedPaths` and no stored connections, and are logged.
  Everything beyond that is granted by an admin through `mcp setup`.

## 2b. Option A: Keycloak as the authorization server

The MCP server is a **pure resource server**. Keycloak authenticates the user; the
MCP server checks the token, maps it to a user, and connects with that user's
stored DB credentials. Those are still shared secrets, because the spec forbids
passing the token on and MariaDB cannot use it anyway.

- **Settings:**

  ```json
  "oauth": {"mode": "keycloak",
            "issuer": "https://kc.example.com/realms/mariadb",
            "verification": "jwt",
            "clientIds": [],
            "linkByVerifiedEmail": true,
            "autoProvision": {"enabled": true, "requiredRealmRole": "mcp-user",
                              "defaultScopes": ["mcp:db", "mcp:msm"]}}
  ```

  `mcp setup` options: `--oauthMode`, `--oauthIssuer`, `--oauthAutoProvision`,
  `--oauthRequiredRealmRole`, `--oauthLinkByVerifiedEmail`. The issuer is checked
  at setup time by fetching its `/.well-known/openid-configuration`.
- **SDK wiring:** `AuthSettings(issuer_url=<keycloak issuer>,
  resource_server_url=<publicUrl>, validate_token_resource=True)` plus
  `KeycloakVerifier`. PRM then points clients at Keycloak.
- **`KeycloakVerifier.verify_token`:**
  1. JWKS comes from the discovered `jwks_uri` through `PyJWKClient`, cached, and
     refetched once on an unknown `kid`. Fetches run in a worker thread.
  2. Algorithms: only the asymmetric ones the JWKS offers (RS256/ES256). `none`
     and HS* are refused.
  3. `iss` must equal the configured issuer exactly. `exp`/`nbf` are checked with
     30s leeway. `typ` must be `Bearer` (an ID token is refused).
  4. **`aud` must contain `publicUrl`.** This works with Keycloak's Audience mapper
     today and with `--features=resource-indicators` later. `AccessToken.resource`
     is set to `publicUrl` when it matches, so the SDK's own check agrees.
  5. Optional `azp` allow list (`clientIds`).
  6. Scopes come from the `scope` claim and are intersected with the user's
     `scopes` in `users.json`. A token can never grant more than the admin allowed.
  7. **User mapping, in this order:**
     1. `(iss, sub)` among the `oauth` identities.
     2. **Link by verified email:** with `email_verified=true`, a user whose
        `email` identity matches (case-insensitively) gets the `oauth` identity
        added. It is logged, and refused if two users share that email.
     3. **Auto-provision:** when the token carries `requiredRealmRole`, a new user
        is created with the `oauth` identity and, when verified, the `email`
        identity.
     4. Otherwise 403, with a log line naming the unknown subject.

     **Concurrency:** linking and provisioning write `users.json` from the server
     process while `mcp setup` may write it from another. Both re-read and write
     under an advisory lock file (`users.json.lock`). `connections.json` gets away
     without a lock only because its drift is cosmetic, and identities are not.
  8. A disabled user, or a token issued before the user's epoch bump, is refused.
  9. Optional `verification: "introspection"` calls Keycloak's RFC 7662 endpoint
     (with a client of our own), cached ~30s, so a user disabled in Keycloak is cut
     off immediately rather than at token expiry (5 min by default).
- **DB credentials** remain admin-managed per user (`mcp setup --user=… --addConnection`).
  An auto-provisioned user can therefore authenticate but has no connections until
  an admin adds them, and `db.list_connections` says so. Optional later: a
  self-service page, reached through URL-mode elicitation and authenticated by the
  same Keycloak login, where users add their own connection passwords. The spec
  forbids eliciting passwords in-band.
- **Keycloak realm setup**, written into the README and as a script
  (`kcadm.sh`) used by the tests:
  - client scopes `mcp:db` and `mcp:msm` (Optional, each with an Audience mapper
    whose Included Custom Audience is `publicUrl`)
  - realm role `mcp-user` (the auto-provisioning gate)
  - email as a required, verified attribute
  - an anonymous dynamic-registration policy (Trusted Hosts, Allowed Client
    Scopes, a client limit) and/or pre-registered public clients with PKCE S256
    and loopback redirect URIs for Claude Code and VS Code
  - CIMD left off until Keycloak's support is stable
- **Tests:**
  - **Unit:** locally generated RSA/EC keys and an in-process JWKS. Wrong `iss`,
    wrong `aud`, expired, `alg=none`, HS256 signed with the public key, an ID
    token, an unknown `kid` followed by rotation, scope intersection.
  - **Every mapping path:** link by verified email; an unverified email that is
    NOT linked; a duplicate email that is refused; auto-provisioning with and
    without the realm role; a disabled user; the epoch.
  - **Opt-in integration** (`--keycloak`, like `--e2e`): a Keycloak container set
    up by the script, and a full flow with the SDK's OAuth client.

## 2c and 2d. Built-in authorization server, and Arcade

Planned in [oauth-builtin.md](oauth-builtin.md):
- **2c:** the MCP server as its own authorization server, logging users in
  against MariaDB.
- **2d:** the Snowflake-style setup through Arcade that 2c must support.

## Later (not planned in detail)

- **Role scopes**, `mariadb:role:<r>`, the Snowflake `session:role:` analogue. They
  would add `allowedRoles` to `users.json` (2c: the account's
  `APPLICABLE_ROLES`), a role on the `Principal` and the `_Connection`, a consent
  checkbox per role, and per-role client scopes in Keycloak.
- `mariadb:read-only` (`SET SESSION TRANSACTION READ ONLY`), once the phase-0
  spike shows what it stops.
- Self-service connection management for 2b users.
- HTTP-level step-up for per-tool scopes.
