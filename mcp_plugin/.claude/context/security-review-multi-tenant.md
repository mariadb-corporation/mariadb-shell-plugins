# The M security review: multi-tenant mode and OAuth2

The attack surface multi-tenant mode and OAuth2 added (branch `wip/mcp-multi-tenant`),
numbered M1..M21 in the style of the S and T review in
[security-review.md](security-review.md). Each entry gives the threat, what is built
against it, the test that pins it, whether a REVERT PROBE has proved that test
discriminates, and what is left open. Written on 2026-10-06 from the code as built. Unlike
S and T, these were not reported from outside; they are the review the plan asked for.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). What was built is in
[multi-tenant.md](multi-tenant.md), [oauth.md](oauth.md) and
[oauth-builtin.md](oauth-builtin.md).

**Revert probes** were run on 2026-10-06 for every entry with a test: break the one line,
run the tests named, see them fail, restore. Each entry names its probe.

## Authentication and tenant isolation (phase 1)

- **M1 - An unauthenticated request reaches a tool.**
  - **Built:** in multi-tenant mode the SDK's bearer middleware answers every `/mcp`
    request without a valid token with 401, before any tool runs. Independently, the
    `tool_registrar` wrapper refuses a call with no principal (`_check_caller`). stdio,
    which has no request to carry a token, is refused at start.
  - **Tests:** `test_a_call_without_a_user_is_refused`, and the real-server test's
    401s.
  - **Probe run:** `_check_caller` removed fails the no-user and scope tests.
- **M2 - One user uses another's open connection** (a guessed or leaked connection id,
  even with the same MCP session id).
  - **Built:** `ClientIdentity.user` is part of the one tuple equality
    `_Connection.is_accessible_from` compares. The refusal is byte-identical to an
    unknown id.
  - **Test:** `test_a_connection_is_bound_to_its_user`.
  - **Probe run:** comparing `ClientIdentity[:2]` fails it.
- **M3 - One user lists or opens another's configured connection.**
  - **Built:** every connection call resolves in the caller's secret group
    (`mcp_user_id`), so another user's URI names nothing.
  - **Test:** `test_a_user_lists_and_opens_only_their_own_connections`.
  - **Probe run:** dropping the user from the identity fails it, along with 4 others.
- **M4 - A tenant reaches files outside their allowed paths.**
  - **Built:**
    - Per-user `allowedPaths`.
    - In multi-tenant mode the elicitation that let a CLIENT trust a new path is
      replaced by a refusal, since the client is the restricted party.
    - The msm tools' `None` (the server's working directory) is checked like any other
      path.
  - **Tests:** `test_paths_are_a_users_own`,
    `test_a_path_is_never_offered_to_the_client_to_trust`,
    `test_no_path_means_the_working_directory_and_is_checked`.
  - **Probe run:** the multi-tenant branch of `require_allowed_path` disabled fails the
    elicitation and working-directory tests.
  - **Open:** two users given overlapping paths share them, which is the
    administrator's call.
- **M5 - Secrets at rest.**
  - **Accepted risk.** API keys are stored in PLAIN TEXT (a user decision, so
    `--showApiKey` works), next to the connection passwords in the user's group.
    Groups partition and do not protect: any process of the server's OS user reads
    every group.
  - **Mitigation is operational,** in the README: run the server under its own OS
    account. On Linux the default `login-path` helper only obfuscates.
- **M6 - Guessing API keys.**
  - **Built:**
    - Keys carry 256 random bits and are compared as SHA-256 digests in constant time.
    - `AuthFailureThrottle` answers 429 after 10 refused tokens per (address, user named
      in the token) in 60s, with a 200 ceiling per address.
    - Keyed on the pair because a gateway sends all its users from a few addresses: one
      user's failures must not lock out the others.
  - **Since the user part is attacker-chosen,** the per-address ceiling is the
    effective bound, and the key length makes it moot anyway.
  - **Tests:** `test_failures_are_throttled_per_address_and_user`,
    `test_an_address_has_a_ceiling_of_its_own`, `test_the_throttle_answers_with_a_429`.
  - **Probe run:** the 429 branch disabled fails `test_the_throttle_answers_with_a_429`.
- **M7 - Revocation lag.**
  - **Built:** `users.json` is `stat`ed on every token check, and any change - a user
    removed, disabled, a key rotated - flushes the key cache and drops that user's
    connections on the next request. A key changed in the secret store BEHIND the
    plugin's back is trusted for up to 60s.
  - **Test:** `test_a_change_with_mcp_setup_applies_to_the_next_request`.
  - **Probe run:** the cache never refreshed once read fails it and
    `test_the_directory_reports_who_is_still_active`.
- **M8 - Lost updates between `mcp setup` and the server.**
  - **Built:** every read-modify-write of `users.json` and `oauth_clients.json` holds
    `config.file_lock` (fcntl, msvcrt on Windows).
  - **Found and fixed on 2026-10-06:** `oauth_clients.json` had no lock, and the server
    rewrote it on EVERY token issue to record client use, so a client removed with
    `mcp setup` could be written back. Client use is now collected in memory and written
    once per sweep (60s).
  - **Test:** `test_a_client_change_waits_for_the_lock`.
  - **Probe run:** the lock removed from `_change_clients` fails it.

## OAuth2 (phase 2)

- **M9 - Scopes.**
  - **Built:**
    - `tools/list` shows only the tools of the token's scopes
      (`auth.scoped_server_class`).
    - Each call checks its group's scope (`_check_caller`).
    - A token with no tool scope gets HTTP 403 `insufficient_scope` naming
      `mcp:db mcp:msm`.
  - **Tests:** `test_tools_are_listed_by_scope`, `test_a_tool_needs_the_scope_of_its_group`,
    `test_a_token_granting_no_tool_gets_a_403`.
  - **Probe run** for the call-time check (`_check_caller`).
  - **Open:** per-tool step-up at the HTTP layer (a missing scope for ONE tool is a
    JSON-RPC tool error, not a 403).
- **M10 - Token confusion (Keycloak).**
  - **Built:**
    - Only asymmetric algorithms (no `none`, no HMAC keyed with the public key).
    - The exact issuer, `exp`/`nbf` with 30s leeway, and `typ` Bearer (no ID tokens).
    - **`aud` must contain the public URL.**
    - An optional `azp` allow list.
  - **Tests:** `test_a_keycloak_token_that_is_not_for_this_server_is_refused` (6 cases),
    `test_only_asymmetric_signatures_are_accepted`; also live against the user's realm.
  - **Probe run:** removing the audience check fails the two audience cases.
- **M11 - Token passthrough.** By design, a Keycloak token never reaches the database:
  the database is reached with stored credentials, as the MCP specification requires.
  Nothing to test.
- **M12 - Account takeover through linking or auto-provisioning (Keycloak).**
  - **Built:**
    - Linking by email only when `email_verified` is true, and only to the one user
      with that email identity (identities are unique, so it cannot be ambiguous).
    - Auto-provisioning only with the realm role (`mcp-user` by default).
  - **Tests:** `test_a_keycloak_sign_in_is_linked_by_verified_email`,
    `test_keycloak_without_the_realm_role_creates_nobody`.
  - **Probe run:** linking without checking `email_verified` fails the linking test.
  - **Residual:** this trusts the realm's email verification; a realm that marks emails
    verified without checking them hands out other people's users.
- **M13 - The built-in sign-in page is a password oracle against the database.**
  - **Built:**
    - At most 5 failures per account and 30 per address in 15 minutes, with every
      failure answered the same way.
    - A CSRF token bound to the pending request.
    - `X-Frame-Options: DENY`, a CSP with a nonce, `form-action 'self'` plus the
      client's redirect origin, `no-store`.
    - TLS forced to a non-loopback database.
  - **Test:** `test_a_wrong_password_reads_the_same_and_is_rate_limited`.
  - **Probe run:** the limiter check removed fails it.
  - **Open:** the limits are per process (M20).
- **M14 - Authorization code interception and mix-up.**
  - **Built:**
    - PKCE S256 (the SDK).
    - Codes single-use, 60s, bound to client, redirect URI and grant.
    - Redirect URIs matched exactly, except the port of a loopback redirect, as OAuth
      2.1 allows native apps.
    - `iss` in the authorization response (RFC 9207), advertised in the metadata.
    - A `resource` other than the public URL refused.
  - **Tests:** `test_a_loopback_redirect_may_use_any_port`,
    `test_the_authorization_request_is_lenient_where_it_safely_can_be`,
    `test_an_unredeemed_code_ends_its_grant`, and the SDK-client interop test, which
    validates `iss` itself.
  - **Probe run:** accepting any redirect URI fails
    `test_a_loopback_redirect_may_use_any_port`.
- **M15 - Refresh token theft and reuse.**
  - **Built:**
    - Rotation on every use.
    - The previous token presented after the grace period ends the grant.
    - A token matching neither the current nor the previous one is just rejected, so
      knowing a grant id is not enough to end someone's grant.
    - Only the current token is ever rotated.
  - **Residual:** a thief racing the legitimate client within the grace period (30s)
    gets the same pair.
  - **Test:** `test_refresh_tokens_rotate_and_a_reuse_ends_the_grant`.
  - **Probe run:** removing the reuse ending fails it.
- **M16 - The database password from a sign-in.**
  - **Built:** kept only as the grant's login connection (user group or memory) and
    deleted with the grant on every way it ends. It is never logged.
  - **Test:** `test_the_password_lives_exactly_as_long_as_the_grant`, with a sentinel,
    in both stores.
  - **Probe run:** the store deletion removed from `end_grant` fails it and two other
    grant tests.
- **M17 - SSRF through a Client ID Metadata Document.**
  - **Built:** https with a path only, every resolved address public, no redirects, 5 KB,
    5s, and the document's `client_id` must equal its URL.
  - **Test:** `test_a_client_metadata_document_is_fetched_with_care` (9 refusals).
  - **Probe run:** the public-address check removed fails it.
  - **Open:** the address is checked with one DNS lookup and httpx connects with a
    second, so a name that changes its answer in between (DNS rebinding) can still reach
    an internal address. The fix is to connect to the checked address with SNI/Host
    set.
- **M18 - Dynamic client registration abuse.**
  - **Built:** at most 1000 dynamic clients; unused ones removed after 30 days. The
    setting can be turned off.
  - **Test:** `test_dynamic_registration_is_stored_and_can_be_turned_off`.
  - **Open:** `/register` itself has no per-address rate limit.
- **M19 - The signing key.**
  - Stored in the generic group (readable by the OS user, like M5).
  - `--rotateSigningKey` keeps the previous key for VERIFYING only, for one
    access-token lifetime, so issued tokens stay valid until they expire. For a key
    that may have leaked, `--dropPreviousSigningKey` ends that at once.
  - A running server reloads the keys every sweep (60s), and on a token naming an
    unknown key id (at most every 10s). Before this, a rotation by `mcp setup-oauth`
    only reached the server at its next restart.
  - **Tests:** `test_a_rotated_signing_key_keeps_issued_tokens_valid`,
    `test_dropping_the_previous_signing_key_refuses_its_tokens`.
- **M20 - Single instance.** Sign-in rate limits, the failure throttle, pending sign-ins,
  codes and the refresh grace cache are per process. Behind a load balancer each instance
  has its own limits, and codes and grace answers do not cross instances. Documented as a
  limit, not supported.
- **M21 - Network allow list.**
  - **Built:** `allowedClientNetworks` (built-in mode) restricts `/mcp`, `/token`,
    `/register` and `/revoke` by TCP peer address, and not `/login`, since browsers come
    from anywhere.
  - **Behind a reverse proxy** the peer is the proxy, so the list has to name it. Same
    reason as S1: `proxy_headers` stays off.

## Found with a real client

- **M22 - MCP 2026-07-28 has no sessions, so the S3 binding refused every modern
  client.** Found on 2026-10-06 by driving the server from **Claude Code 2.1.287**
  (`claude -p` with `--mcp-config` and the user's API key in a header).
  - **What happened:** Claude Code speaks protocol revision 2026-07-28. SDK 2.3.0 routes
    that to its "modern" handler (`streamable_http_manager._handle_request`: any
    `MCP-Protocol-Version` outside `HANDSHAKE_PROTOCOL_VERSIONS`), which has no
    `Mcp-Session-Id` at all. The S3 fail-closed rule (address AND session id over HTTP)
    therefore refused `db.connect` for EVERY such client. That holds on `main` too, for a
    single-tenant server.
  - **Built:** over HTTP, `db.connect` now needs the address and either the session id or
    an authenticated user. In multi-tenant mode the verified user is part of the
    binding, so a connection opened without a session is still bound to that user: a
    second user is refused it on the same address. The connection UUID (122 random
    bits) remains the handle.
  - **Test:** `test_a_sessionless_request_binds_to_its_user`.
  - **Verified by hand:** after the fix, Claude Code listed, connected and ran
    `SELECT CURRENT_USER(), CURRENT_ROLE(), …`: `ada@%, mcp_access, 24.99`.
  - **DECIDED by the user (2026-10-06): keep refusing, and recommend multi-tenant
    mode for HTTP.** An unauthenticated server refuses a sessionless client
    `db.connect` over HTTP, with an error that says so and names stdio and
    `mcp setup --multiTenant=true`. Binding to the address alone was rejected: every
    local process shares the loopback address (S3). The README has a section on it.
    - **Test:** `test_a_sessionless_client_of_an_unauthenticated_server_is_told_why`.
  - **Also affected:** the path-trust ELICITATION of a single-tenant server. In
    2026-07-28 servers do not send requests, so a modern client is never asked and the
    path is refused, which fails closed.

## Next steps

1. Close the open points named above: DNS-rebinding-proof CIMD fetch (M17), a rate limit
   on `/register` (M18), per-tool step-up (M9). M20 (several instances) was decided
   against for now (2026-10-06).
