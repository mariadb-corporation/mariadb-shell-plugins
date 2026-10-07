# Multi-tenant mode: the phase 1 plan (1a-1e)

The plan phase 1 was built from, section by section, as written before it was built.
Where the build differs, the "Status" notes in [multi-tenant.md](multi-tenant.md)
(the "As built" list under Phase 1) win. Moved out of
multi-tenant.md verbatim on 2026-10-07, when that file passed 400 lines.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## 1a. Config layer: `config.py` and a new `tenants.py`

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

## 1b. `mcp.setup`

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

## 1c. Server: authentication and the request principal

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

## 1d. Tenant isolation in the tools

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

## 1e. Tests and documentation

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
