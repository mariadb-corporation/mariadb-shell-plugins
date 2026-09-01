# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. GPLv2,
"MariaDB plc". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`), **MCP SDK 2.0.0**, Python 3.14, pytest
9.1.1, uvicorn 0.52.1, httpx2 2.9.1, `mariadbd` at `/opt/homebrew/bin` (MariaDB 12.3.2).
Full suite: **78 tests pass (~38s), 95% total coverage**. Run it with
`mariadb-shell --py -f run_tests.py` FROM the mcp_plugin dir and with `/opt/homebrew/bin`
on PATH (mariadbd is not on the default PATH).

The shell's bundled Python — the one that matters for every "which version does this
behave like" question — is
`/Users/mzinner/git/mariadb-shell/build/lib/mariadb-shell/lib/python3.14/site-packages`.
Read the SDK and uvicorn sources THERE, not upstream. (Ask the shell itself rather than
`find /`: `mariadb-shell --py -f <script printing module __file__>`.)
**There are TWO dependency trees** in the shell build: that site-packages one, which is
what actually runs, and `build/bundled-python-deps/`, a staging copy. As of this session
they hold the same versions (mcp 2.0.0, uvicorn 0.52.1) and the files that matter are
byte-identical — but only the site-packages one is authoritative, so always get the path
from the running shell rather than from a filesystem search. (`/System/Volumes/Data/...`
hits are the same files through the macOS firmlink, not a third copy.)

The shell's env vars are `MARIADB_SHELL`, `MARIADB_SHELL_USER_CONFIG_HOME` and
`MARIADB_SHELL_TERM_COLOR_MODE` — the pre-rename `MYSQLSH*` names are GONE from all
three plugins' runners and test helpers. `run_tests.py` exports `MARIADB_SHELL` and
`tests/unit/helpers.py shell_binary()` reads it; keep those two in sync or the suite
silently runs against whatever `mariadb-shell` is on PATH.

## Architecture / key decisions

- Repo's existing `*_plugin` layout (NOT create-shell-plugin's `python/plugins/`).
  `@plugin` / `@plugin_function` decorators. FQNs camelCase (`mcp.startServer`) ->
  snake_case in Python, kebab-case in CLI. MCP tool names use dots per user request.
- MCP server built with **MCPServer** (`mcp.server.mcpserver`, the MCP Python SDK 2.x
  successor of 1.x's `mcp.server.fastmcp.FastMCP` — NOT the standalone `fastmcp` v2).
  `requirements.txt` pins `mcp >= 2.0.0, < 3.0.0`; on the 1.x API every server-side import
  here fails. `mcp`, `msm_plugin`, `mrs_plugin`, and the `sandbox` global are imported
  lazily so plugin load never hard-fails.
- **SDK 2.x API notes**: `MCPServer(name)` takes NO `host`/`port` — they are transport
  options passed to `run(transport=..., host=..., port=...)`, so `build_mcp_server()` takes
  only `function_groups`. The low-level server is `_lowlevel_server` (1.x: `_mcp_server`).
  Client side, `mcp.client.streamable_http.streamable_http_client` (1.x:
  `streamablehttp_client`) yields a 2-tuple `(read, write)` — the third `get_session_id`
  element is gone. The result models moved to **snake_case attributes with camelCase wire
  aliases**: `result.is_error` / `result.structured_content` (1.x: `isError` /
  `structuredContent`). The wire format did NOT change, so this is attribute access only —
  but `getattr(result, "structuredContent", None)` silently returns None instead of
  raising, which is exactly how `tool_payload` degraded unnoticed to its text-block
  fallback. `stdio_client`, `StdioServerParameters`, `ClientSession`,
  `server.tool(name=...)`, `stdio_server(stdout=)` and `ctx.elicit(message=, schema=)` are
  unchanged, as is `types.ElicitResult(action=, content=)`.
- Server runs **foreground on the main thread**; `start()` sets `useWizards=False`.
- **streamable-http is served on OUR OWN uvicorn server** (`_serve_streamable_http`), NOT
  via `mcp_server.run(transport=...)`. Verified on the bundled SDK: 2.0.0's
  `run_streamable_http_async` builds `uvicorn.Config(app, host=, port=, log_level=)`
  internally and forwards NOTHING else, so `proxy_headers` cannot be reached through
  `run()`. `_serve_streamable_http` does exactly what the SDK does (same
  `streamable_http_app`, same `settings.log_level`) plus TWO deliberate differences, both
  security fixes and neither a style choice — see Gotchas:
  - **`proxy_headers=False`** on the `uvicorn.Config` (S1).
  - **an explicit `transport_security=`** built by `_transport_security_settings` (S4),
    instead of letting `streamable_http_app` decide.
- **DNS-rebinding / Host+Origin validation is configured HERE, never left to the SDK**
  (`_transport_security_settings` + `_dialable_host_names`). Verified on the bundled SDK:
  `TransportSecuritySettings.enable_dns_rebinding_protection` defaults to **True**, but
  `TransportSecurityMiddleware.__init__` does
  `settings or TransportSecuritySettings(enable_dns_rebinding_protection=False)` — so
  passing None means OFF. 2.0 did NOT drop the 1.x auto-enable, it moved it into
  `lowlevel/server.py streamable_http_app()`:
  `if transport_security is None and host in ("127.0.0.1", "localhost", "::1")`. `host` DOES
  reach it, so the plugin's DEFAULT bind was already protected before this change — the
  defect is the CONDITION: a case-sensitive test over three literal strings, so `LOCALHOST`,
  `[::1]`, `127.0.0.2` (still loopback!) and EVERY non-loopback bind served with no
  validation at all. Now always enabled, allow list derived from the real bind host:
  loopback bind -> all of `general.LOOPBACK_HOST_NAMES`; a single address/name -> only that
  one (loopback deliberately NOT added, it is genuinely unreachable then), bare IPv6
  bracketed; wildcard (`general.is_wildcard_host`) -> loopback + `socket.gethostname()` /
  `getfqdn()` / its resolved addresses. Every name is allowed BARE and with `:{port}` — the
  SDK's own list had only `host:*`, which 421s a Host without a port (i.e. `--port=80`).
  Origins mirror the hosts over http+https with `:*`.
- **`allowed_hosts` option on `mcp.startServer`** (list or comma-separated string): extra
  Host values, for a server reached under a name that cannot be derived from the bind
  address — a reverse proxy, a port forward, a DNS alias. NEEDED, not decoration: without
  it, enabling the validation on a wildcard bind would 421 legitimate remote clients, which
  is worse than the status quo it replaced.
- **There is NO authentication.** Anyone who can reach the port can `db.list_connections` +
  `db.connect` and get a session on the stored credentials; the connection binding stops
  takeover, not unauthorized use. Stated outright in the README (its own section, placed
  BEFORE the connection-handling one), and `_warn_if_reachable_from_the_network` prints a
  stderr warning naming that risk when `--host` is not loopback
  (`general.is_loopback_host`).
- Transports: `streamable-http` (default) and `stdio`. **stdio hardened** (`_serve_stdio`):
  real stdout (fd 1) dup'd for the transport, then fd 1 AND `sys.stdout` redirected to
  stderr so tool/shell/C output can't corrupt JSON-RPC. Uses low-level
  `mcp_server._lowlevel_server.run(...)`.
- **Function groups** (`function_groups`): `db`, `msm`, `sandbox`; `_FUNCTION_GROUP_REGISTRARS`.
  Accepted as a LIST or as a comma-separated STRING (split+stripped in `server.py`);
  omitting it loads ALL THREE (`DEFAULT_FUNCTION_GROUPS = SUPPORTED_FUNCTION_GROUPS`).
- **Cross-group dependency mechanism**: `build_mcp_server` passes the full enabled-group
  list to EVERY registrar, so `register_db_tools`/`register_msm_tools`/
  `register_sandbox_tools` all take `(server, function_groups=())`. db and sandbox ignore
  it. msm uses it to register `msm.deploy_schema` ONLY when `db` is also served — that tool
  needs a `connection_id` from `db.connect`, so with msm alone it is left UNADVERTISED
  rather than exposed as something that cannot succeed. It is therefore registered LAST in
  the registrar, inside an `if` (see Gotchas — an early `return` there silently drops the
  tools defined after it).
- **`db_functions.use_session(connection_id, client_address=None)`** is the PUBLIC accessor
  (a `@contextmanager`, NOT the old plain `get_session` — that name is GONE), so other tool
  modules (msm) can resolve a `db.connect` session without reaching into another module's
  privates or keeping a second cache. It authorizes the caller, reopens a session that was
  closed for being idle, holds the connection's lock for the whole `with` block and stamps
  `last_used` on exit. `msm.deploy_schema` goes through it too — otherwise it would be a
  bypass of the address check.
- **Connections**: shell secrets keyed `MCP:Connection:<uri>`. `db.connect` only allows
  configured URIs, but NOT by string equality: `config.resolve_connection_uri()` maps what
  the client sent to the spelling it is stored under, and everything from there on uses the
  configured one (the password key, `_Connection.uri`, the log line, the re-validation on
  reopen). `config.normalize_connection_uri()` is the comparison form: a `mariadb://` or
  `mysql://` prefix stripped (`parse_uri` REJECTS `mariadb://` outright — that was the
  bug), then `parse_uri` -> drop the password, lowercase the host, spell out port 3306 when
  the URI left it out -> `unparse_uri` (which also fixes option order, percent-encoding and
  a trailing slash). It is a fixed point, so stored and incoming URIs go through the same
  function. Everything ELSE in the URI is kept and must match — a schema (`/db`) or an
  option (`?ssl-mode=REQUIRED`) the configured connection does not have makes it a
  different connection, refused rather than silently answered with a session that does not
  do that; `mysqlx://` likewise stays distinct. `mcp.setup` stores the normalized URI, so
  one connection has one key; the same connection configured under two spellings is the one
  case resolution cannot settle and it raises instead of guessing. Opens via
  `_open_session()` = `parse_uri`+password ->
  `shell.open_session` (independent of the shell's global session). `_sessions` maps the
  UUID to a **`_Connection`** record (uri, `client_address`, `session`, `last_used`,
  `lock`), NOT to a bare session; `_sessions_lock` guards the dict, each `_Connection` has
  an `RLock` of its own.
- **Connection safeguards.** `general.is_http_transport()` reads the `_active_transport`
  module global that `lib/server.start()` sets from its `transport` arg BEFORE serving.
  Since T6 it gates EXACTLY ONE THING and nothing else (grep it — there is one call site in
  `lib/`): `db.connect`'s fail-closed branch. The reaper used to be the second reader; it is
  now started and stopped by `server.start()`, which knows the transport from its own argument
  rather than from a global that outlives the server.
  - **Client binding**: a connection may only be used by the client that opened it —
    **BOTH the peer address AND the MCP session id**, held together in
    `general.ClientIdentity(address, session_id)` (a NamedTuple) and compared as ONE tuple
    equality. **`_Connection.is_accessible_from` is a PLAIN EQUALITY, NOT gated on the
    transport** — see Gotchas, re-gating it is a fail-open. Over stdio a request has
    neither part, so a connection is opened with an empty identity and every later call
    presents an empty one: the single client matches itself and the check needs no
    knowledge of the transport. A mismatch raises the BYTE-IDENTICAL error an unknown UUID
    raises, so probing cannot tell a real UUID from a guessed one — keep those two
    messages the same. In HTTP mode `db.connect` FAILS CLOSED when EITHER part is missing.
    One `general.get_client_identity(ctx)` feeds all 9 call sites (8 db tools +
    `msm.deploy_schema`); a single value rather than two parallel args precisely so a
    caller cannot pass one and forget the other.
  - **Why both parts.** The address is `ctx.request_context.request.client.host` — the TCP
    peer, deliberately NOT `X-Forwarded-For` or any other header (client-supplied,
    forgeable). But it is not a secret and it is SHARED: by everything behind one NAT or
    reverse proxy, and by every process on the machine on the default loopback bind (more
    so now that all loopback forms normalize to one token). The **MCP session id** is the
    half that actually separates clients: `mcp-session-id` header,
    `general.get_client_session_id`, a server-generated `uuid4().hex` the client must have
    been told. `request` is a starlette Request on HTTP and None on stdio, so the
    `try/except` + `getattr` chains return None there for both parts.
  - **The SDK has its own session-owner check and it is INERT here.**
    `streamable_http_manager.py:262` rejects a request whose session was created under a
    different credential, but `requestor = authorization_context(user) if isinstance(user,
    AuthenticatedUser)` — with no auth configured it is always None, `_session_owners` stays
    empty, and it never fires. So our binding is NOT redundant with it. Also useful: an
    UNKNOWN session id is 404'd by the manager before any tool runs, while a STOLEN one
    routes to that client's transport — i.e. the session id genuinely is the credential.
  - **Normalization** (`general.normalize_client_address` /
    `normalize_client_identity`, applied on produce, on store AND on compare — all three,
    so no caller can forget; both idempotent): the equality would otherwise inherit
    spelling false-negatives. IPv4-mapped `::ffff:a.b.c.d` -> `a.b.c.d`; EVERY loopback
    form -> the single `general.LOOPBACK_ADDRESS = "loopback"` token (deliberately not a
    valid IP literal, so it cannot collide with a real client address); IPv6 canonicalized
    via `str(ipaddress.ip_address(...))`; a non-IP string (unix socket path) passed through
    unchanged to compare only with itself; `None`/blank -> None. The session id is compared
    EXACTLY as issued (lowercase hex; header values are case-sensitive).
  - **30-minute idle timeout** (`general.SESSION_IDLE_TIMEOUT = 1800`, raised from the
    10 minutes it shipped with in 0bba1318): a daemon reaper
    thread (`_reap_connections`, started by `server.start()` before it serves over HTTP and
    stopped in its `finally`, waits `_REAP_INTERVAL = 30`s per pass) closes the SESSION of
    every idle connection but KEEPS the `_Connection`, so the UUID stays valid and the next
    tool call reopens transparently. `db.close` is the documented exception: it drops the
    entry and closes only an already-open session, it never reopens one to close it.
  - The reopened session is a NEW server session: temp tables, session vars, current
    schema and open transactions do NOT survive an idle period. Documented in the
    `db.connect` tool description (which clients see), the module docstring and the README.
  - **12-hour hard TTL** (`general.CONNECTION_MAX_LIFETIME = 43200`, S6 — the user chose 12h
    over the 4h first proposed): counted from `_Connection.opened_at`, NOT reset by use.
    `has_expired(max_lifetime)`. Enforced in TWO places on purpose: in `_get_connection`
    (so it holds in EVERY transport — the reaper is HTTP-only, and `sandbox.delete` can
    revoke a connection over stdio) and by the reaper's `_drop_expired_connections` pass (so
    it also reaches connections nobody comes back to). Both go through `_drop_connection(id,
    reason)` — pop under `_sessions_lock`, log, THEN `close_session()` (which WAITS for a
    running statement, unlike the idle pass's non-blocking acquire). An expired connection
    gets the byte-identical unknown-UUID error, so it still tells a guesser nothing.
  - **URI re-validation on EVERY open** (S6): `_open_session` itself checks
    `config.list_connection_uris()` before reading the password, so a reopen after an idle
    period cannot come back on a connection removed with `mcp.setup` or `sandbox.delete`.
    `db.connect` keeps its own check for the nicer first-time message. Error text differs
    deliberately ("is no longer a configured connection" vs "is not a configured
    connection") — the reopen only ever happens for the connection's own owner, after the
    identity check, so a distinct message leaks nothing.
  - **Closing is FINAL, and a flag is what makes it so** (T1): `_Connection.closed`, set
    under `connection.lock` by `_Connection.close()`, checked by `open_session()` which
    raises the module-private `_ConnectionClosed`; `use_session` translates that into
    `_no_such_connection(connection_id)`. The TERMINAL paths (`db.close`, `_drop_connection`)
    call `close()`; the IDLE path still calls `close_session()` and must NOT raise the flag,
    or an idle connection could never be reopened.
  - **Connection caps** (S7): `general.MAX_CONNECTIONS_PER_CLIENT = 16`,
    `MAX_CONNECTIONS_TOTAL = 64`. `_claim_connection_slot(id, connection)` counts AND
    inserts under ONE hold of `_sessions_lock`, and is called BEFORE
    `connection.open_session()`; a failed open pops the entry again. Expired connections are
    not counted. Over stdio every request has the same empty identity, so the per-client cap
    is the one that binds there.
  - **stderr audit trail** (S5): `general.log_event` (+ `describe_client`, `log_id_prefix`,
    `LOG_ID_PREFIX_LENGTH = 8`). Records: connection opened, a use REFUSED, a `db.connect`
    refused as unidentifiable or over a cap, an idle session closed, a connection dropped, a
    failing `session.close()`, a failing reaper pass. Connection UUIDs and MCP session ids
    are TRUNCATED to 8 chars — both are credentials.
- **All db.\* tools now take a leading `ctx: Context`** (same `from mcp.server.mcpserver
  import Context` inside the registrar as msm/sandbox; the server strips it from the
  client-facing schema) purely to reach the client address — they are still SYNC and still
  do not elicit.
- **Allowed paths**: `settings.json` under `general.get_plugin_data_path()`.
  `config.is_path_allowed()` via `os.path.commonpath` (reads disk fresh each call — NO
  in-memory cache); empty list => deny all.
- **Path enforcement + elicitation** (commit 6e12ad68): shared guard
  `general.require_allowed_path(ctx, path)` (ASYNC; skips when arg is None), used by msm.*
  (target/file/schema_project) and sandbox.* (sandbox_dir). When a path is NOT allowed it
  MCP-elicits (`ctx.elicit`, schema=one-bool `ConfirmTrustPath`) asking the user to trust
  it; on accept+trust it `config.add_allowed_path()` (persists to settings.json,
  abspath+expanduser, dedup) and proceeds; on decline/cancel/elicit-failure it raises the
  "not allowed" mysqlsh.Error. Because elicit is async, ALL msm (12) + sandbox (7) tools are
  `async def` with a leading `ctx: Context` param (`from mcp.server.mcpserver import Context`,
  imported inside the registrar; the server strips it from the client-facing schema).
  db.* tools stay SYNC — none of them elicit (`db.execute_sql_script` checks
  `config.is_path_allowed` directly and just errors out) — but they DO take `ctx` now, see
  the connection-safeguards bullet above.
- **SQL exec**: `db.execute_sql` = single statement (+ optional `?` params, one result
  dict). `db.execute_sql_script` = multi-statement via `mysqlsh.mysql.split_script()`,
  returns a LIST; accepts `sql_script` XOR `file_path` (file must be an allowed path).
  `sandbox.deploy` port REQUIRED int on all 7; `ssl=False` default.
- **Introspection tools** (committed in 3482634a; all sync, all built on the user's own
  SQL — the queries came from the user verbatim, only parameterized; do NOT "improve" them
  without asking):
  - `db.list_schemas(connection_id)` -> `_LIST_SCHEMAS_SQL` over I_S.SCHEMATA. Returns a
    bare LIST of `{schema_name, schema_type, schema_comment}` (lowercase aliases — the
    USER renamed them from upper case), not the full `_serialize_result` envelope, since
    a fixed SELECT has no useful affected-rows/warnings. `schema_type` is computed in SQL
    (System Schema / System Information Schema / User Schema) and is ALSO the first
    ORDER BY key, so rows sort by LABEL TEXT -> user schemas come LAST. Intended.
  - `db.list_objects(connection_id, schema_name, object_type="table")` -> one query per
    type in the `_LIST_OBJECTS_SQL` dict (table, view, function, procedure, sequence,
    trigger, event), each taking the schema as its single `?`. Columns: `name`+`comment`
    for table/view, `name`+`datatype` for sequence, `name` alone for the rest.
  - `db.get_object_details(connection_id, schema_name, object_name, object_type="table")`
    -> `{"basic": {schema, name, type, comment}}` for every type, plus per type:
    table -> `columns`+`constraints`+`references`; view -> `columns` only;
    function/procedure -> `parameters` (name, mode, datatype, parameter_default) +
    `returns`; sequence/trigger/event -> a single-row `details` dict.
  - Shared plumbing: `_query_rows` (run + return rows), `_normalize_object_type`
    (case-insensitive validation against the `_LIST_OBJECTS_SQL` keys, used by BOTH
    list_objects and get_object_details), `_parse_json_fields`.
- **Introspection SQL gotchas that are baked in on purpose**:
  - `table` listing uses `TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')` — system
    versioned tables have their OWN TABLE_TYPE and would otherwise vanish. Sequences and
    views have their own types, so they drop out by themselves.
  - `INFORMATION_SCHEMA.SEQUENCES` is **MariaDB 11.5+**. No fallback for older servers
    (would need I_S.TABLES `TABLE_TYPE='SEQUENCE'` + I_S.COLUMNS on
    `next_not_cached_value`).
  - A routine's RETURNS clause is the `ORDINAL_POSITION = 0` row of I_S.PARAMETERS (no
    name, no mode) -> split into `returns`, excluded from `parameters`. Procedures have
    no such row, so `returns` is None. Return type comes from PARAMETERS, NOT from
    `ROUTINES.DTD_IDENTIFIER` — one source, one documented rule.
  - Triggers have NO comment column anywhere in the information_schema, hence
    `SELECT NULL as comment` in `_OBJECT_BASIC_SQL["trigger"]`.
  - Existence check is "no `_OBJECT_BASIC_SQL` row" (works for all 7 types and catches a
    type mismatch, e.g. asking for a table as a sequence), NOT "no columns".
- **Deliberate changes to the user's supplied SQL** (each flagged to and left standing by the
  user):
  - constraints query needed `AND tc.TABLE_NAME = kcu.TABLE_NAME` — constraint names are
    unique per TABLE, not per schema, so joining on schema alone makes every table's
    `PRIMARY` match every other table's PK columns. Test pins this (two PK tables in the
    schema, `items` must report exactly one constraint row).
  - dropped the columns query's dead `LEFT OUTER JOIN` on KEY_COLUMN_USAGE: nothing was
    selected from it and it can duplicate a column that sits in two FKs. Restore with
    DISTINCT if fields from it are ever needed.
  - also added `ORDER BY tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION` for determinism.
  - **T9: the four aggregates in `_OBJECT_REFERENCES_SQL` are ordered by
    `k.ORDINAL_POSITION`** — the column's place in the FOREIGN KEY. The user asked for
    `c.ORDINAL_POSITION` (its place in the TABLE); that was flagged as the wrong ordinal, with
    the measurement below, and the user said commit. Do not switch it back.
- **REST SQL** (commit 0e97c9e9): `db.execute_sql` can run MRS REST SQL (e.g.
  `CONFIGURE REST METADATA`) because `mrs_plugin` registers a shell SQL handler
  (`@sql_handler("MRS", prefixes=...)`, prefixes incl. "CONFIGURE REST ") that intercepts
  `session.run_sql`. Requires mrs_plugin loaded in the server subprocess (symlinked by
  run_tests). antlr4 (MRS parser dep) is bundled in mariadb-shell.

## Current state

- **The user worked through a numbered security-review list (S1..S8) of the connection
  handling, one issue at a time.** Each was to be VERIFIED against the bundled SDK/uvicorn
  (or the real shell) rather than taken on faith, fixed, and pinned by a test PROVEN to fail
  without the fix (revert the one line, re-run, restore). **ALL OF S1..S8 ARE NOW DONE.**
  S1/S2/S8 in faa08b11, S3/S4 in 9c877f37, S5/S6/S7 in the commit this checkpoint describes.
  S8 (address normalization) was folded into the S2 commit at the user's instruction ("must
  land together with S8"); when the user later sent S8 as its own item it was VERIFIED as
  already in the tree and re-reported, not re-implemented — do the same if a list item
  arrives that is already built.
  - **S5 (MEDIUM, no audit trail)**: refused cross-address use, a failing `session.close()`
    and any reaper exception were all swallowed, so a hijack attempt left no trace. Added
    `general.log_event` and the call sites listed under Architecture. Verified BEFORE coding
    that stderr is the right stream in both transports: bundled `uvicorn/config.py:100` sends
    uvicorn's own diagnostics to `ext://sys.stderr` (only its ACCESS log goes to stdout, and
    HTTP mode does not care), and `mcp/client/stdio.py:345` passes the child `stderr=errlog`
    — an INHERITED fd, defaulting to the parent's stderr, not an undrained pipe. Deliberately
    NOT the `logging` module: under uvicorn an unconfigured logger drops INFO lines.
    `_get_connection` was split so ONLY the mismatch branch logs (a stale id is a client bug,
    a mismatch is an attempt), with both raising a shared `_no_such_connection()` factory so
    the two texts cannot drift. PROVEN twice: `log_event` neutered to `return` fails all 5
    log tests, each on its own message, while the 12 older tests still pass (so the
    `_get_connection` split is behaviour-preserving); `log_id_prefix` returning ids in full
    fails the two "no secrets in the log" assertions.
  - **S6 (MEDIUM, a UUID never expires and revocation does not reach it)**: report right on
    the substance, ONE detail over-stated and worth remembering — for a connection whose
    session had been idle-CLOSED, the reopen already failed before the fix, by accident,
    because `config.get_connection_password` is a `read_secret` on a deleted key. Confirmed
    against the real shell: `RuntimeError: Failed to read the secret: Could not find the
    secret`. So the pre-fix behaviour was "revocation reaches one narrow case, as an opaque
    secret-store error, and nothing else"; a connection with a live session was never
    re-checked at all. ALSO FOUND: `sandbox.delete` is a SECOND revocation path
    (`sandbox_functions.py:223`) and unlike `mcp.setup` it is reachable OVER MCP, including
    stdio — which is why the TTL is not left to the HTTP-only reaper. Fixed with the hard TTL
    plus the re-validation (see Architecture). PROVEN: `has_expired` → `return False` fails the
    3 lifetime tests (`DID NOT RAISE Error` x2, `assert 0 == 1`); disabling the
    `_open_session` check fails both revocation tests with that same secret-store
    `RuntimeError`.
  - **S7 (LOW, unbounded connections)**: no per-client or global cap, so a loop of
    `db.connect` calls cost the caller nothing and the server a real session each. Fixed with
    the two caps (see Architecture). PROVEN twice, and the second probe is the one that
    matters: caps removed → all 3 new tests fail with `DID NOT RAISE Error`; claiming the
    slot AFTER `open_session()` → all 3 fail on the session count (`assert 4 == 3`,
    `assert 3 == 2` x2), i.e. the refused call had already cost the database a connection.
- **A SECOND list started after S1..S8: T-numbered items. T1..T6, T8 and T9 are DONE** (no T7
  was sent) (T2 by
  verification alone: it needed no code change; T3 and T5 each needed something different from
  what was asked for - see each).
  - **T1 (HIGH, `db.close` racing a call leaks a session)**: PRE-EXISTING since 0bba1318, not
    introduced by S5..S7 — the report said so and it is true. `use_session` resolves the
    connection under `_sessions_lock`, RELEASES it, and only then takes `connection.lock`, so
    a whole `db.close` fits in between: it pops the record and closes the session, and
    `open_session()` then found `session is None` and opened a NEW one on a record no longer
    in `_sessions` — unreachable by `db.close` and by the reaper, so nothing would ever close
    it, while the caller went on working on a connection its client had been told was closed.
    Same shape a second time INSIDE `close_session()`, which takes the session out under the
    lock, releases, and only then calls `session.close()`. Fixed with the `closed` flag (see
    Architecture). **Holding the lock across `session.close()` would NOT have fixed it** — the
    racer would just open its session after the close finished; the report was right about
    that and `test_closing_a_connection_beats_a_call_that_races_it` would still fail under
    that "fix", since its closer thread is joined before the racing call takes the lock.
    PROVEN: with the flag check disabled, `..._beats_a_call_that_races_it` fails
    `DID NOT RAISE Error` (the racing `db.execute_sql` ran to completion on the leaked
    session) and `test_a_session_being_closed_is_not_replaced_underneath` fails
    `assert [<_StubSession object>] == ['refused']` — the racer was handed a second session
    while the first was being closed.
    - **The first version of both tests failed for the WRONG reason** under the probe (an
      `AttributeError: '_StubSession' object has no attribute 'run_sql'`, and an unrelated
      `mysqlsh.Error` from S6's URI check that `close_session`'s own `except` then swallowed).
      A probe that fails accidentally proves nothing, so `_StubSession` gained `run_sql` +
      `_StubResult` and the second test stubs `_open_session`. **Check WHY a revert probe
      fails, not just THAT it fails.**
  - **T2 (MEDIUM "verify", the reaper closes shell sessions from a foreign thread)**: asked
    for an authoritative answer on cross-thread `session.close()`, with a
    hand-it-back-to-the-event-loop mitigation if the answer was no or unobtainable. **The
    answer is YES, it is supported, and the mitigation was deliberately NOT built** — the user
    was told so with the evidence rather than being given a defensive change that would have
    made things worse. Do not "fix" this again without new evidence:
    - **`mysql_thread_init()` and `mysql_thread_end()` are EMPTY FUNCTIONS** in the MariaDB
      Connector/C this shell statically links
      (`/Users/mzinner/git/mariadb-server/libmariadb/libmariadb/mariadb_lib.c:4515`; the
      library is `MARIADB_CLIENT_LIBRARY` in `build/CMakeCache.txt` ->
      `mariadb-server/bld/libmariadb/libmariadb/libmariadbclient.a`). There is no per-thread
      client state to be missing. The shell's `Mysql_thread` RAII helper and its
      "initialization ... when connecting from threads" comment
      (`mysqlshdk/include/shellcore/shell_init.h:52`) are libmysqlclient heritage from the
      MySQL Shell this is ported from; its ONLY in-tree user is `kill_query`.
    - `Session_impl::close()` (`mysqlshdk/libs/db/mysql/session.cc:628`) = reset the previous
      result, `mysql_close()`, null the pointer. **No `thread_local` anywhere under
      `mysqlshdk/libs/db/`.**
    - **The report's premise that sessions are created on the event-loop thread is WRONG**,
      and this matters more than the rest: SDK 2.0 dispatches a sync tool body through
      `anyio.to_thread.run_sync` (`mcp/server/mcpserver/resolve.py:556`). MEASURED in this
      runtime: the loop runs on `MainThread`, every sync tool body on an `AnyIO worker
      thread`. So EVERY `db.connect`/`db.execute_sql`/`db.close` already opens, uses and
      closes sessions off the loop thread, on threads the shell did not create. The reaper is
      doing nothing the tools do not do on every call.
    - Empirically: 60 rounds of open-on-one-thread / query-on-a-second / close-on-a-brand-new
      daemon thread named `mcp-db-connection-reaper`, against a real 12.3.2 sandbox — no
      crash, clean `RuntimeError` on use-after-close, server connection ids incrementing one
      per round.
    - **Why the proposed mitigation would have been worse**: it would move ~1 close per 30
      minutes onto the loop thread while leaving every tool call on a worker thread (fixing
      the least-exposed instance); it would put a blocking `mysql_close()` network round trip
      ON the event loop, stalling the whole server; and it needs a loop reference captured at
      serve time plus a fallback for stdio, embedders using `build_mcp_server`, and the
      in-process tests — new machinery and a new failure mode for no reduction in exposure.
    - Built instead: **`tests/unit/test_db_threading.py`** (one test, uses the shared
      sandbox), which pins the finding against a real server and checks
      `information_schema.PROCESSLIST` so a close that silently did nothing would fail it. A
      future shell build that made sessions thread-affine breaks a test run instead of a
      production server. The finding is also written into `_reap_connections`'s docstring.
    - The invariant that IS load-bearing is unchanged: **one thread at a time per session**,
      via `_Connection.lock`, pinned by `test_a_session_in_use_is_not_closed`. Connector/C is
      thread-safe for distinct connections, not for concurrent use of one.
  - **T3 (MEDIUM, a dead session is never detected)**: real bug, real fix, but HALF the
    proposed fix was verified USELESS and deliberately left out:
    - **`session.is_open()` is NOT a liveness check.** It exists (`ClassicSession` exposes
      `isOpen` -> `is_open()` in Python, `modules/mod_mysql_session.cc:92`) but it is
      `return _mysql ? true : false` (`mysqlshdk/libs/db/mysql/session.h:220`) - a check that
      a client-side handle exists. MEASURED against a real server: it returns **True** after
      the connection is KILLed, True after the statement that failed on it, and True after
      the whole server has been stopped. So `if not session.is_open(): reopen` would catch
      NOTHING in any of the three cases T3 names, while reading as a safety net. Not added.
      Do not add it later either.
    - What works is the error: a lost connection arrives as **`mysqlsh.DBError` with `.code`
      2013** (CR_SERVER_LOST, "Lost connection to server during query"), and **2006**
      (CR_SERVER_GONE_ERROR, "Server has gone away") on every call after it. An ordinary SQL
      error is the SAME exception type with a server-side code (e.g. 1146), so the code is
      the only thing that tells them apart -> `_CONNECTION_LOST_ERRORS = (2006, 2013, 2055)`
      and `_is_connection_lost(error)`, applied around the `yield` in `use_session` (the
      choke point all 9 call sites go through). The session is discarded via
      `close_session()`, so the connection lives on and the NEXT call opens a new session.
    - The failed call is NOT retried: it may have run in part, and any transaction died with
      the connection. The client gets the DBError and decides. Stated in the README.
    - The code list is deliberately SHORT, and that is safe because a lost connection that
      first surfaces under some other code is caught on the next call, which is always 2006.
    - PROVEN with TWO probes, because one direction is not enough here:
      `_is_connection_lost` -> `return False` fails the two positive tests (`assert
      <shell.Object ...> is None` - the corpse still cached - and `assert False is True` on
      `_StubSession.closed`); `-> return True` fails
      `test_an_ordinary_sql_error_keeps_the_session`, which the first probe CANNOT fail by
      construction. A negative-control test needs its own probe in the opposite direction.
    - NOT built, mentioned to the user: a pre-flight `SELECT 1`/ping on the first call after
      a long idle period, which would turn the one failed call into a silent reconnect at the
      cost of a round trip. The error-driven discard already makes it self-healing.
  - **T4 (MEDIUM, silent state loss with no runtime signal)**: a reopen discards temp tables,
    session vars, the current schema and - the dangerous one - an open transaction, so a
    `COMMIT` on the new session succeeds having committed NOTHING. Documented but invisible at
    runtime. Now the call that opens the session reports `session_restarted: true` in its
    result metadata: `_Connection.session_restarted` (assigned on the way into `use_session`,
    cleared in its `finally`), read via `_session_was_restarted(connection_id)` inside the
    block, and rendered by `_serialize_result(result, session_restarted=...)`.
    - Present ONLY when true - a field that is nearly always false is one clients learn to
      skip - and on `db.execute_sql_script` only on the FIRST entry (`restarted and not
      results`), since one session is opened once, before the script starts. The
      introspection tools return bare row lists with no envelope and do not carry it; they do
      not depend on session state.
    - Covers T3's discard too: session dropped -> next call reopens -> that call reports it.
    - The flag is PER CALL, which is the whole point (the report said so). TWO mechanisms,
      deliberately redundant: the entry does `connection.session_restarted = restarted` (an
      assignment, so a later call cannot inherit a True), and the `finally` clears it (so a
      reader OUTSIDE a call - there is none today - cannot find a stale True). Either alone
      covers the sequential case.
    - **PROVEN, and the second probe exposed a WEAK TEST rather than a weak fix**: with the
      field suppressed, all 3 new tests fail with `KeyError: 'session_restarted'`. But the
      first version of the per-call assertion (checking the flag after making ANOTHER call)
      PASSED the per-connection probe, because the next call's entry assignment had already
      reset it. The test now asserts `connection.session_restarted is False` IMMEDIATELY after
      the reopening call, and the probe then fails `assert True is False`. Same lesson as T3,
      earned again: assert the invariant where it can actually be violated.
  - **T5 (LOW "latent deadlock", the lock is held across the yield)**: the CONCERN is real, the
    REPORTED MECHANISM is not. Both halves measured on this runtime:
    - **An RLock held across an await does NOT block the next coroutine - it lets it in.** The
      lock is owned by a THREAD and every coroutine on the server shares one. Measured: A
      acquires and awaits; B's `acquire(timeout=1)` returns `True`. So the failure mode would
      not be a deadlock but the silent loss of the mutual exclusion the lock exists for - two
      tool calls on ONE shell session at once, i.e. interleaved statements on one connection.
    - **A present-day problem the report did not name**: `msm.deploy_schema` is `async def`, so
      its body ran on the event-loop thread, where it both waited for the connection's lock and
      then ran a WHOLE DEPLOYMENT. Measured: a coroutine blocking on a lock a worker thread held
      for 0.6s let **zero** other tasks run - the entire server, every transport, frozen for
      the duration of somebody else's slow statement. No future code change needed for that.
    - Fixed at both ends: `msm.deploy_schema` runs its `use_session` block on a worker thread
      (`anyio.to_thread.run_sync`, with the client identity read BEFORE the hand-off - a worker
      thread has no request context to read it from), and `use_session` begins with
      `_refuse_on_the_event_loop()`, which raises when an asyncio loop is running on the calling
      thread. That makes the constraint executable instead of a comment, and makes the
      await-inside-the-block hazard unreachable for the only async caller.
    - NOT done: the timeout-and-"connection busy" option. Waiting for a connection another call
      is using IS the documented behaviour of the lock ("serializes the tool calls that share a
      connection"); a timeout would turn a legitimately slow statement into a spurious error.
      With the work off the loop thread a blocked wait costs one worker thread, not the server.
    - The guard sees only an **asyncio** loop (this build has no `sniffio`); nothing here uses
      trio. Residual: many queued calls on one connection still tie up worker threads (anyio's
      default limiter is 40).
    - PROVEN with two probes: guard disabled ->
      `test_a_session_cannot_be_taken_on_the_event_loop_thread` fails `DID NOT RAISE
      RuntimeError`; the msm deploy put back inline -> `test_stdio_msm_project_lifecycle` fails
      END TO END with the guard's own message. The second one is the one that matters: the
      guard catches the real caller, not just a synthetic one.
  - **T8 (LOW, duplicate column labels silently drop data)**: confirmed against a real server
    before touching anything - `SELECT 1 AS id, 2 AS id, 3 AS other` reported
    `columns: ['id','id','other']` and produced the row `{'id': 1, 'other': 3}`: **3 columns
    in, 2 out**, and the value 2 never even read, because `row.get_field('id')` answers with
    the FIRST match every time. `columns` still listed both, so the loss looked like the
    client's bug. Ordinary SQL reaches this through `db.execute_sql` (`SELECT a.id, b.id FROM a
    JOIN b`).
    - Fixed in `_serialize_result` with `_unique_column_labels`: first column of a label keeps
      it, later ones become `label_2`, `_3`, ... checked against every label already emitted so
      an invented key cannot land on a column genuinely named `id_2`. `columns` reports the
      keys actually used, so the two can no longer disagree.
    - **Values are now read BY POSITION (`row[index]`)**, which is the half that actually
      recovers the data - a label cannot address the second of two columns that share it.
      Verified the shell's Row supports it: `row[0..n]`, `len(row)`, `row.get_length()`.
    - A qualified name (`a.id`) was considered and rejected: `get_table_name()`,
      `get_table_label()` and `get_column_name()` all return `''` for aliased or computed
      columns (measured), so the rule would hold for some queries and not others.
    - PROVEN: restoring raw labels + `get_field(label)` fails four tests - the three unit tests
      (one via a stub row whose `get_field` raises "values must be read by position") and
      `test_db_connect_execute_and_close` END TO END with
      `assert ['id', 'id', 'other'] == ['id', 'id_2', 'other']`.
  - **T9 (LOW, non-deterministic column order in the FK reference mapping)**: real, and the
    prescribed fix would not have fixed it. `GROUP_CONCAT` and `JSON_ARRAYAGG` in both halves of
    `_OBJECT_REFERENCES_SQL` had no ORDER BY, so a composite key's column sequence was whatever
    the plan produced.
    - **Which ordinal is the point.** `c.ORDINAL_POSITION` is the column's place in the TABLE;
      `k.ORDINAL_POSITION` is its place in the FOREIGN KEY, and they differ whenever a key is
      not declared in table order. MEASURED on a real 12.3.2 with `child (id, a, b)` and
      `FOREIGN KEY (b, a) REFERENCES parent (x, y)`: unordered -> `'a, b'` /
      `[{a->y},{b->x}]`; `ORDER BY c.ORDINAL_POSITION` -> **the same**; `ORDER BY
      k.ORDINAL_POSITION` -> `'b, a'` / `[{b->x},{a->y}]`, which is the declared key. So the
      prescribed ordinal would have added a guarantee to the WRONG sequence, and on this plan
      changed nothing at all.
    - `JSON_ARRAYAGG(... ORDER BY ...)` is invalid in MySQL and VALID in MariaDB; checked on
      this build before relying on it. `GROUP_CONCAT` takes `ORDER BY` before `SEPARATOR`.
    - The TWO OTHER unordered `JSON_ARRAYAGG`s, in the second half's PK subqueries, are left
      alone ON PURPOSE: they are only compared with `JSON_CONTAINS`, which ignores array order
      (verified: `'["a","b"]'` contains `'["b","a"]'` -> 1). The reasoning is a comment in the
      SQL so it is not re-opened as an unexamined worry.
    - PROVEN with two probes, and the second is the interesting one: unordered ->
      `test_db_connect_execute_and_close` fails `assert 'second, first' == 'first, second'`;
      `ORDER BY c.ORDINAL_POSITION` -> fails IDENTICALLY. The test discriminates against the
      status quo AND against the prescribed variant.
    - The test creates `pairs` + `pair_refs` in the shared flow (`pair_refs (id, second, first)`
      with `FOREIGN KEY (first, second)`) and asserts the mapping from BOTH directions of the
      UNION. It forced two incidental updates: the object script is 11 statements now, not 9,
      and the table listing order is `... orders, pairs, pair_refs, versioned` — the server's
      own collation puts `pairs` FIRST, which is not what sorting the underscore first would do.
  - **T6 (LOW, reaper lifecycle)**: all three complaints were accurate - started lazily by the
    first `db.connect`, never stopped, and spawned while holding `_sessions_lock` (the very lock
    the new thread then wants). Now `lib/server.start()` owns it: `start_connection_reaper()`
    before serving over HTTP and `stop_connection_reaper()` in a `finally` after, both PUBLIC
    (no leading underscore) because `server.py` calls them. `_start_connection_reaper` is GONE
    and `db.connect` starts nothing.
    - The thread is spawned under `_reaper_lock`, a lock of its own, and asked to finish through
      `_reaper_stop` (a `threading.Event`): `_reap_connections` now does
      `while not _reaper_stop.wait(_REAP_INTERVAL)` instead of `time.sleep`, so stopping does
      not have to wait out the interval it is in. The event is cleared both after a join and
      before a start, so a reaper that once outlived its join cannot leave the next one
      pre-stopped.
    - **`general.is_http_transport()` now has EXACTLY ONE reader**: `db.connect`'s fail-closed
      branch. The reaper's start knows the transport from `server.start`'s own argument instead
      of from a global that outlives the server. Update the S2 gotcha if that changes again.
    - Not a regression for embedders: an embedder serving `build_mcp_server` itself got no
      reaper before either (the old lazy start required `is_http_transport()`, which is only set
      by `server.start`), and the maximum lifetime is applied when a connection is USED, in
      every transport, so nothing safety-relevant depends on the thread.
    - The "first sweep is 30s late" point is real and immaterial: the interval is 30s against
      timeouts of 1800s and 43200s. Loop order left as wait-then-sweep; sweeping at t=0 would
      find nothing.
    - PROVEN: `test_the_reaper_can_be_started_and_stopped` (start, idempotent re-start, stop,
      a NEW thread for the next server) and `test_serving_over_http_owns_the_connection_reaper`
      in test_server_binding (asserts the exact order `["start", "served", "stop"]`, that a
      serve which RAISES still stops it, and that stdio starts nothing).
  - **S1 (HIGH, header-derived peer address)**: uvicorn 0.52.1 has `proxy_headers=True`
    and `forwarded_allow_ips="127.0.0.1"` by DEFAULT (config.py:220/357), and
    `ProxyHeadersMiddleware` (config.py:526) rewrites `scope["client"]` from
    `X-Forwarded-For` when the peer is trusted. The plugin's `DEFAULT_HOST` is
    `127.0.0.1`, so EVERY request qualified and any client could pick the address the
    binding compared against. Fixed with `_serve_streamable_http` +
    `proxy_headers=False`. PROVEN: with `mcp_server.run(...)` restored, the new test fails
    with "No open connection found for id ..." — i.e. the header really did move the
    address.
  - **S2 (HIGH, fail-open when the transport global is unset)**: `is_accessible_from`
    short-circuited to True unless `is_http_transport()`, so any embedding that serves via
    the public `build_mcp_server` without `lib.server.start()` — and any moment after a
    server stops, since the global is never reset — silently disabled the binding. Now a
    plain normalized equality. PROVEN: restoring the gate fails
    `test_a_connection_stays_bound_without_an_active_transport` and
    `test_a_connection_is_usable_over_stdio` (8 others still pass).
  - **S3 (MEDIUM, IP binding is the only access control, no authentication)**: three parts,
    all built — the README says it plainly in its own section; a non-loopback `--host`
    prints a stderr warning naming the risk; and the connection is now bound to the
    **`Mcp-Session-Id`** as well as the address. The user re-decided this against the old
    "deliberate non-goal" note and was right to: the session id is the only half that
    separates clients sharing an address. PROVEN: reverting to address-only fails
    `test_a_connection_is_bound_to_its_mcp_session`,
    `test_the_tools_pass_the_client_identity_on` and — end to end, a real takeover —
    `test_streamable_http_binds_a_connection_to_its_mcp_session`, whose second client's
    `SELECT 1` came back with rows. The S1 test still passed under that revert, correctly.
  - **S4 (MEDIUM, DNS-rebinding / Origin validation)**: the report's premise about the
    middleware defaulting to disabled is right; its assumption that the PLUGIN was therefore
    unprotected is NOT — 2.0 kept the auto-enable, moved into `streamable_http_app`, and
    `host` reaches it, so the default `127.0.0.1` bind was already protected both before and
    after S1. The real defect is the case-sensitive three-string condition (see
    Architecture). Now configured explicitly. PROVEN: reverting to
    `streamable_http_app(host=host)` makes `test_streamable_http_rejects_a_foreign_host_header`
    fail with `assert 200 == 421` — a forged `Host: evil.example.com` was SERVED a real MCP
    initialize response. The test binds `--host=LOCALHOST` on purpose: still loopback, but
    outside the SDK's three strings, so it isolates our settings from the SDK's guess.
  - Touched by S5/S6/S7: `lib/general.py` (`log_event`, `describe_client`,
    `log_id_prefix`, `LOG_ID_PREFIX_LENGTH`, `CONNECTION_MAX_LIFETIME`,
    `MAX_CONNECTIONS_TOTAL`, `MAX_CONNECTIONS_PER_CLIENT`, `import sys`/`time`),
    `lib/db_functions.py` (`_Connection.opened_at`/`has_expired`, `_no_such_connection`,
    `_drop_connection`, `_drop_expired_connections`, `_claim_connection_slot`, and T1's
    `_ConnectionClosed`/`closed`/`close()`, the URI check
    in `_open_session`, the reaper renames, log calls at 8 sites, module + `db.connect` +
    `use_session` docstrings), `README.md` (**What the server logs** and **Removing a
    connection revokes it** sections, the lifetime and cap bullets),
    `tests/unit/test_db_sessions.py` (14 new tests, `_UnclosableSession`, `_age`,
    `_registered_tools`, `_register_connection` gained a `connection_id` arg).
  - Touched by S1..S4: `lib/server.py` (`_serve_streamable_http`,
    `_warn_if_reachable_from_the_network`, `_transport_security_settings`,
    `_dialable_host_names`, `allowed_hosts` through `start`), `lib/general.py`
    (`normalize_client_address`, `LOOPBACK_ADDRESS`, `ClientIdentity`,
    `normalize_client_identity`, `get_client_session_id`, `get_client_identity`,
    `MCP_SESSION_ID_HEADER`, `is_loopback_host`, `is_wildcard_host`, `LOOPBACK_HOST_NAMES`,
    `import ipaddress`), `lib/db_functions.py` (`_Connection.__init__` +
    `is_accessible_from` + `_get_connection`/`use_session` take an identity),
    `lib/msm_functions.py` (one call site), `server.py` (the `allowed_hosts` option),
    `README.md`, `tests/unit/helpers.py`, `tests/unit/test_transport_http.py`,
    `tests/unit/test_db_sessions.py`, NEW `tests/unit/test_server_binding.py`.
  - **RESIDUALS flagged to the user, none yet decided**:
    - `db.connect`'s fail-closed branch is still gated on `is_http_transport()` (S2 said to
      keep it that way), so an embedder serving over HTTP without `start()` whose transport
      attaches no peer address would bind to an empty identity and let those clients share
      connections. Strictly better than before (where ALL connections were unbound in that
      case) but it is the one path where the global can still soften something. Making it
      unconditional would need an explicit stdio exemption.
    - **`stateless_http` would now BREAK `db.connect` over HTTP** rather than weaken it: no
      session ids are issued in that mode, so the fail-closed branch refuses. We never enable
      it and `mcp.startServer` does not expose it — but anyone adding that option must deal
      with this first. The safe direction, but a hard failure.
    - A reverse proxy still collapses the ADDRESS half onto one value for everyone, so behind
      a proxy the binding rests on the session id alone. Acceptable (it is the strong half)
      and now stated in the README.
    - **A connection in continuous use is NOT re-validated per statement** — that would be a
      secret-store (macOS Keychain) lookup per SQL statement. So revoking a busy connection
      takes effect only when its session is next reopened or when its 12h TTL runs out.
      Stated in the README, with "restart the server if a removal has to take effect at once".
    - **The three limits and the caps are CONSTANTS, not `mcp.startServer` options**, which is
      the same call as for the idle timeout. The user picked 12h for the TTL (over the 4h
      proposed); 16/64 for the caps are MY numbers and were flagged as such.
    - S6's long TTL and S7's per-client cap COMPOSE: a client that never calls `db.close`
      keeps countable records for 12h, so it hits the wall after 16 `db.connect` calls in a
      day rather than 16 concurrent ones. Sized generously for exactly that, and the refusal
      error names `db.close` so a leaking client is nudged, not cut off.
    - Over stdio, expired records are only dropped when their UUID is used again (no reaper
      there), so a client that abandons them leaves them in the dict. Bounded by the caps,
      and the client owns the process anyway.
- Previous session on this branch: the two connection safeguards as originally built
  (0bba1318 + 6067fd8c) — address binding + the idle timeout, then raised to 30 minutes.
  Touched `lib/general.py` (transport global, `SESSION_IDLE_TIMEOUT`,
  `set_active_transport`/`is_http_transport`/`get_client_address`), `lib/db_functions.py`
  (the `_Connection` record, `_open_session`, `_get_connection`, `use_session`,
  `_close_idle_sessions`/`_reap_idle_sessions`/`_start_idle_reaper`, `ctx: Context` on all
  8 db tools), `lib/server.py` (`set_active_transport(transport)` in `start()`),
  `lib/msm_functions.py` (`deploy_schema` -> `use_session`), README and tests. NOTE: the
  cross-IP rejection is proven in-process only — driving two genuinely different source
  addresses at a local server needs a loopback alias (root on macOS), so the suite does not.
  The S1 test gets at the same thing from the other side: two clients on the SAME real peer
  address, one of them lying about it in a header.
- Previous session: **migrated the plugin from MCP SDK 1.x to 2.0.0** (the shell's bundled
  Python now ships 2.0.0; `requirements.txt` had `mcp >= 1.2.0` with no upper bound, so the
  major bump was picked up silently and 8 of 17 tests failed —
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` killed the server subprocess
  at import, which surfaced as `MCPError(-32000, 'Connection closed')`). Changes:
  `mcp.server.fastmcp.FastMCP` -> `mcp.server.mcpserver.MCPServer` (server.py, plus the
  `Context` import in msm_functions/sandbox_functions), host/port off the constructor and
  onto `run()`, `_mcp_server` -> `_lowlevel_server`, `streamablehttp_client` ->
  `streamable_http_client` (2-tuple), and `.isError`/`structuredContent` ->
  `.is_error`/`structured_content` at 31 assertion sites + `tool_payload`.
- Everything from the previous checkpoint's "Next steps" list is DONE and committed:
  REST SQL work (0e97c9e9), the sibling mrs_plugin management-session fix (ca47b8c8 then
  reworked in 82e18c4c), msm lifecycle + streamable-http transport tests (09fa116c).
- The three introspection tools (`db.list_schemas`, `db.list_objects`,
  `db.get_object_details`) are COMMITTED (3482634a, pushed).
- Shell fns: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer` (options: `host`,
  `port`, `transport`, `function_groups`, **`allowed_hosts`**).
- Tools: db.* (**8**: `list_connections`, `connect`, `list_schemas`, `list_objects`,
  `get_object_details`, `execute_sql`, `execute_sql_script`, `close`),
  msm.* (**12**, path-guarded, async — the 12th is `deploy_schema`, gated on the db group),
  sandbox.* (7, `sandbox_dir`-guarded, async, port required).
- Tests (tests/unit/, no `__init__`): `test_sandbox` (deploy FIRST, shutdown LAST +
  path-reject), `test_config` (9), `test_msm` (5: create_project, elicit-accept,
  elicit-decline, deploy-needs-db-group, lifecycle), `test_db_sql`, `test_rest_sql`,
  `test_transport_http` (**5**: list_connections, a full connect/execute/close db flow over
  HTTP, `..._ignores_a_forwarded_for_header` for S1,
  `..._binds_a_connection_to_its_mcp_session` for S3, and
  `..._rejects_a_foreign_host_header` for S4 — the last one talks raw `httpx2` rather than
  the MCP client, because it has to forge Host/Origin and assert HTTP status codes),
  `test_db_sessions` (**41** — the connection lifecycle and result serialization, in ten
  sections: the client
  identity, the binding (S2/S3/S8: `..._stays_bound_without_an_active_transport`,
  `..._is_usable_over_stdio`, `test_equivalent_spellings_of_an_address_are_the_same_client`,
  `..._is_reachable_over_either_ip_stack`, `..._is_bound_to_its_mcp_session`,
  `test_client_identity_carries_the_session_id_too`), the idle timeout, then S5's
  `test_opening_a_connection_is_logged`, `..._refused_connection_use_is_logged`,
  `test_closing_an_idle_session_is_logged`, `..._session_that_fails_to_close_is_logged`,
  `test_a_failed_reaper_pass_is_logged`, `test_logging_never_breaks_its_caller`; S6's
  `test_a_connection_does_not_live_for_ever`, `test_the_lifetime_holds_without_a_reaper`,
  `test_the_reaper_drops_a_connection_nobody_comes_back_to`,
  `test_removing_a_connection_revokes_it`, `test_a_first_open_is_validated_too`; S7's
  `test_one_client_cannot_open_connections_without_end`,
  `test_the_server_as_a_whole_has_a_limit_too`,
  `test_an_expired_connection_does_not_hold_a_slot`,
  `test_a_connection_that_fails_to_open_gives_its_slot_back`,
  `test_db_connect_refuses_a_uri_that_is_not_configured`; the URI-spelling pair
  `test_db_connect_takes_a_uri_however_the_client_spelled_it` and
  `test_db_connect_refuses_a_uri_asking_for_more_than_is_configured`; T1's
  `test_closing_a_connection_beats_a_call_that_races_it` and
  `test_a_session_being_closed_is_not_replaced_underneath`), `test_server_binding` (**6**:
  loopback vs reachable vs wildcard host classification, the no-auth warning, the default
  staying quiet, the derived Host/Origin allow lists, and that serving over HTTP starts and
  stops the connection reaper, and that `start()` refuses a configuration it cannot serve),
  NEW `test_db_threading` (**1**,
  T2: a real session opened, used and closed across three threads), NEW `test_db_recovery`
  (**1**, T3: a real session KILLed from a second session and replaced on the next call).
  **78 pass, ~38s.**
- **Coverage: TOTAL 95% (936 statements, 47 missed) — measured on a run with `.coverage`
  DELETED first.** Per module: lib/msm_functions 100, lib/db_functions 98, lib/general 98,
  lib/config 98, lib/server 97, lib/tool_registrar 93, lib/sandbox_functions 88,
  lib/setup 85, server.py 81, general.py 73.
  **CORRECTION, and a trap to avoid repeating**: earlier figures in this file and in the
  T-series commit messages (up to "db_functions 100%, TOTAL 97%") were INFLATED.
  `run_tests.py` passes `--cov-append`, so `.coverage` ACCUMULATES across runs - including the
  revert-probe runs, which execute the suite against DELIBERATELY MODIFIED code and so light up
  branches the real code never takes. Pass counts were never affected; only coverage.
  **Delete `.coverage` before any run whose number you intend to write down** (plain
  `.coverage`, never the `.coverage*` glob - that matches `.coveragerc`).
  The two gaps that mattered are now CLOSED (they were: `db.connect`'s slot giveback when the
  first open fails, added in S7 with no test, and its "not a configured connection" raise; plus
  `server.start`'s three validation raises) - see the three tests named below. What is left is
  defensive and small: the bytes->hex branch, the JSON-parse fallback, `_drop_connection`'s
  already-gone return, `execute_sql_script`'s "exactly one of sql_script/file_path" raise, a
  blank statement in a script, and `_dialable_host_names`' unresolvable-hostname except.
  Note that subprocess coverage IS captured (`.coveragerc` sets `parallel`/`sigterm` and
  `run_tests.py` sets `COVERAGE_PROCESS_START`), so the stdio- and HTTP-driven tests DO count -
  the figure is not under-reporting for that reason. pytest-cov combines and removes the
  `.coverage.*` data files, so deleting plain `.coverage` really does give a clean run.
- **Sibling `msm_plugin` was changed in an EARLIER session** (own commit, own suite: 9 pass
  — see the invocation note in Gotchas):
  - MySQL -> MariaDB rebrand of all PROSE/branding. Legal notices were NOT word-substituted;
    the USER instead ADDED a second line `Copyright (c) 2026, MariaDB plc.` under the
    existing Oracle line, leaving Oracle's notice and the "authors of MySQL hereby grant"
    FOSS exception intact. **Follow that additive pattern**; do not rewrite Oracle's LICENSE
    or the exception paragraph.
  - **The MariaDB notice is exactly `Copyright (c) 2026, MariaDB plc.`** — NOT "MariaDB plc
    and/or its affiliates." (that phrasing is Oracle's and is wrong for MariaDB) and not a
    bare "MariaDB". Both wrong forms existed and were normalized across 55 occurrences in 50
    files. Only the MariaDB line is ever rewritten; Oracle's "and/or its affiliates" stays.
  - `tests/conftest.py` now uses `from mysqlsh.globals import sandbox` +
    `sandbox.deploy/kill` instead of `mysqlsh.globals.dba.deploy_sandbox_instance` (this
    build of mariadb-shell has NO `dba` global), with `ssl: False` and an `int()` port.
  - `run_tests.py` prefers `mariadb-shell` over `mysqlsh`.
  - `lib/management.py deploy_schema` gained `backup: bool = False` (see Gotchas).
- **Sibling plugins touched again THIS session** (rebranding cherry-pick from
  `mariadb/rennox/rebranding` — 759c375d/7f119fd6/f11a3897 — plus follow-up fixes):
  - **`msm_plugin`: 9 pass.** `run_tests.py` fully de-`MYSQLSH`'d, `dot_mariadb_shell`,
    `pip install -r requirements.txt` + a return-code check. A `pip install ... msm` line
    was removed: `msm` is an unrelated PyPI package (a Minecraft server manager), and this
    plugin's own code comes from the runner's symlink.
  - **`mrs_plugin`: 228 pass, 20 fail, 0 errors** (was 72 pass / 15 fail / 161 errors).
    `run_tests.py` gained the missing `pip install -r requirements.txt` step (it had NONE,
    so the declared `pytest-mock` was never installed -> 81 `fixture 'mocker' not found`
    errors), `pytest-asyncio` added to requirements (`asyncio_mode = auto` was already set
    in both ini files, so the plugin was the only thing missing -> 9 async failures), and
    the stale `"name": "mrs"` service expectation fixed in 12 places (see Gotchas).
    `--mysqlsh` renamed to `--shell-options`; `MYSQLSH_FLAGS` -> `SHELL_FLAGS`.
  - **All three `requirements.txt` had `pytest >= 6.1.2, <= 7.0`**, which resolves to
    exactly 7.0.0 — and 7.0.0 crashes on Python 3.14 with
    `AttributeError: module 'ast' has no attribute 'Str'` (`ast.Str` was removed in 3.12).
    Now `pytest >= 7.4`, no upper bound. Verified by bisecting on a real suite: 7.0.0
    crashes, 7.4.4 / 8.4.2 / 9.1.1 all pass. The inline package lists in the runners
    existed to dodge this pin; fixing the pin is what let them switch to `-r`.

## Files that matter

- lib/general.py -> plugin data path, async `require_allowed_path`/`_confirm_trust_path`,
  the transport global (`set_active_transport`/`is_http_transport`), the client identity
  (`ClientIdentity`, `get_client_identity`, `get_client_address`, `get_client_session_id`,
  `normalize_client_address`/`normalize_client_identity`, `LOOPBACK_ADDRESS`,
  `MCP_SESSION_ID_HEADER`), the bind-address helpers (`is_loopback_host`,
  `is_wildcard_host`, `LOOPBACK_HOST_NAMES`), the stderr audit log (`log_event`,
  `describe_client`, `log_id_prefix`, `LOG_ID_PREFIX_LENGTH`) and every connection limit
  (`SESSION_IDLE_TIMEOUT`, `CONNECTION_MAX_LIFETIME`, `MAX_CONNECTIONS_TOTAL`,
  `MAX_CONNECTIONS_PER_CLIENT`). **100% covered — keep it that way.**
- lib/config.py -> connections (secrets) + allowed paths (settings.json) + `add_allowed_path`.
- lib/db_functions.py -> db.* tools; the `_Connection` cache (`_sessions` + `use_session` +
  the reaper + `_claim_connection_slot`/`_drop_connection`/`_no_such_connection` +
  `_ConnectionClosed` and the `closed` flag + `_CONNECTION_LOST_ERRORS`/
  `_is_connection_lost`); `_serialize_result` + `_unique_column_labels`;
  `_serialize_result`;
  the introspection SQL constants (`_LIST_SCHEMAS_SQL`, `_LIST_OBJECTS_SQL`,
  `_OBJECT_BASIC_SQL`, `_OBJECT_DETAILS_SQL`, `_ROUTINE_PARAMETERS_SQL`,
  `_OBJECT_COLUMNS_SQL`, `_OBJECT_CONSTRAINTS_SQL`, `_OBJECT_REFERENCES_SQL`).
- lib/msm_functions.py, lib/sandbox_functions.py -> async tools w/ `ctx: Context`;
  msm_functions also holds the db-group-gated `msm.deploy_schema`.
- lib/server.py -> build/serve; `_serve_stdio` hardening; `_serve_streamable_http`
  (own uvicorn, `proxy_headers=False`, explicit `transport_security`);
  `_transport_security_settings` + `_dialable_host_names` (the Host/Origin allow list);
  `_warn_if_reachable_from_the_network`; passes function_groups to the registrars.
- tests/unit/test_db_recovery.py -> T3: a real session, KILLed from a second session, is
  discarded and replaced on the next call. Needs the shared sandbox (and relies on
  sandbox.deploy having registered its URI, since every open re-validates it). Cannot be done
  with a stub: only the real client library produces the 2013/2006 codes the fix reads.
- tests/unit/test_db_threading.py -> T2: proves a shell session can be opened on one
  thread, used from another and closed from a third (named after the reaper), and that the
  server really drops the connection. Needs the shared sandbox. Its module docstring carries
  the Connector/C and Session_impl evidence - read it before believing any future claim that
  cross-thread session use is unsafe here.
- tests/unit/test_server_binding.py -> the bind address: loopback/wildcard classification,
  the no-authentication warning, and the derived Host/Origin allow lists. Pure in-process,
  no server started, so it is fast and needs no sandbox.
- tests/conftest.py -> ordering hook, fixtures (sandbox session, allowed_temp_dir,
  clean_config, stored_connections, non_interactive_shell).
- tests/unit/helpers.py -> `call_tool` (has `elicitation_callback`), `mcp_session`,
  `list_tool_names` (what the server ADVERTISES, used for the group gate), `tool_payload`,
  `find_free_port`, `server_binary_available`, `shell_binary`, plus the streamable-http
  helpers: **`http_server` (subprocess + URL) and `http_client_session(url, headers=)` are
  now separate**, so one server can be driven by several clients and a client can send
  forged headers. `http_session` is a thin wrapper over both and is unchanged for callers.
  Extra headers reach the transport only via a pre-built client
  (`mcp_http_client(headers=...)` -> `create_mcp_http_client`, passed as `http_client=`) —
  SDK 2.0's `streamable_http_client` takes no `headers` argument. Passing the client in also
  lets a test mutate `client.headers` MID-SESSION (httpx merges them per request), which is
  how the S1 test forges a header without opening a second MCP session.
  `http_server(bind_host=...)` starts the server with a different `--host` while the yielded
  URL always dials 127.0.0.1 — only useful for hosts that still bind loopback, which is
  exactly what the S4 test needs (`LOCALHOST`).
- tests/unit/test_db_sql.py -> single `_db_flow` coroutine over ONE stdio session:
  connect -> execute_sql (incl. a DECIMAL/DATETIME serialization check and T8's two
  columns sharing one label) -> execute_sql_script (inline + file + denied) ->
  list_schemas -> creates one object of EVERY type in a throwaway schema (incl. a
  system-versioned table, a sequence, a trigger, an event, an `orders` table with an FK
  to `items`, and T9's `pairs`/`pair_refs` whose composite FK is declared out of table
  order) -> list_objects (all 7 types + default + case-insensitivity + bad type +
  unknown schema) -> get_object_details (table both FK directions incl. the composite
  key's column order from both sides, view, function, procedure, sequence, trigger,
  event, plus not-found errors) -> DROP SCHEMA -> close. **The object script's statement
  count and the table-listing order are asserted, so adding a table to the flow means
  updating both.**
- tests/unit/test_db_sessions.py -> the connection safeguards, driven IN-PROCESS with a
  `_StubSession` and a `_ToolRecorder` (a fake server whose `.tool(name=)` decorator just
  collects the tool functions, so they can be called directly, `ctx` positionally). Its
  `_context(address)` builds the `request_context.request.client.host` chain the HTTP
  transport supplies, and `http_transport`/`stdio_transport` fixtures flip
  `general.set_active_transport` and clear `_sessions`. No time is ever waited out —
  `connection.last_used -= SESSION_IDLE_TIMEOUT + 1` then `_close_idle_sessions()` directly,
  and `_age(connection, seconds)` moves BOTH clocks back for the TTL tests.
  `CLIENT_ADDRESS`/`OTHER_ADDRESS` are TEST-NET-1 (`192.0.2.x`) on purpose: they must NOT
  be loopback, or normalization would collapse them onto the same token and the
  binding tests would assert nothing. The log assertions use `capsys` (`log_event` prints to
  `sys.stderr`, resolved at call time). `_registered_tools(monkeypatch, opened)` registers
  the tools with a stubbed `_open_session` and an `opened` list — asserting on its LENGTH is
  what proves a refused `db.connect` cost the database nothing.
- run_tests.py -> symlinks mcp_plugin + msm_plugin + mrs_plugin into a temp config home
  (`dot_mariadb_shell` under a `mcp_dot_mariadb_shell_*` temp dir), `pip install -r
  requirements.txt` (was an inline `pytest pytest-cov mcp` list — driving it off
  requirements.txt is what makes the `mcp < 3.0.0` pin actually bind), then runs pytest.
  Exits early if the install fails. `-k/--only` to filter, `-s/--shell`, `-u/--userhome`.
  .coveragerc omits msm/mrs/shell/site-pkgs.

## Next steps

0. **The security-review list S1..S8 is COMPLETE, committed and pushed; a T-numbered list
   started after it; T1..T6, T8 and T9 are all done, committed and pushed.** If the user sends
   more items, follow the working agreement that has held for all sixteen: (a) VERIFY the
   claim against the bundled SDK/uvicorn source under
   `/Users/mzinner/git/mariadb-shell/build/lib/mariadb-shell/lib/python3.14/site-packages`
   or against the real shell, never against upstream docs or memory — and say plainly when
   the report's premise is partly wrong (S4, S6) instead of letting a fix take credit it has
   not earned; (b) fix it in code, not via an env var; (c) add a test and PROVE it
   discriminates by reverting the fix, re-running, and restoring — for S7 the SECOND probe
   (the ORDERING, not the existence of the cap) was the one that mattered, and for T1 the
   first probe failed for the wrong reason and the tests had to be fixed before it proved
   anything; (d) correct any
   docstring/README claim the bug had made false. Also still open: the residuals listed at
   the end of the S1..S8 block in Current state.
1. **`mysqlsh.globals.util.dump_schemas` / `load_dump` do NOT exist in this mariadb-shell
   build**, so `msm.deploy_schema` / `msm_plugin` `deploy_schema` with `backup=True` raise
   `AttributeError: unknown attribute: dump_schemas`. The backup feature is unusable (and
   untested) until the dump/restore is reimplemented — e.g. `mariadb-dump` as a subprocess.
   `backup=False` is the default precisely because of this. STILL REPRODUCING: it is one of
   the 20 remaining mrs_plugin failures (`lib/test_services.py::test_service_as_project`).
2. **`mrs_plugin`'s 20 remaining failures**, all pre-existing and independent of this
   session's work. Grouped: 3x `REGEXP_LIKE does not exist` (see Gotchas — a genuine
   MariaDB portability bug in `mysql_tasks`' SQL); 5x `test_downstream_converter` in
   `sdk/python/tests/test_mrs_base_classes.py` returning strings instead of
   `int`/`datetime`/`date`/`timedelta`; 2x `request_path is already used` (test isolation);
   1x `dump_schemas` (item 1); several `CREATE OR REPLACE REST ...` statement-text
   mismatches; a few object-count assertions (`assert 2 == 1`, `assert 2 == 4`).
3. **`mrs_plugin/lib/general.py:221` and `:231` call `deploy_schema`** and silently lost
   their rollback dump when `backup` defaulted to False. Add `backup=True` there if that
   behaviour should be preserved (sibling plugin, deliberately untouched).
4. (Optional, connection handling) Open points deliberately NOT built, none of them asked
   for: the 30-minute timeout is a constant, not a `mcp.startServer` option; the binding is
   to the raw peer address only, so a **reverse proxy in front of the server collapses
   every client onto the proxy's address** — now stated outright in the README, since S1
   made `proxy_headers=False` deliberate. Restoring proxy support would need an explicit
   trusted-proxy setting feeding `forwarded_allow_ips`, NEVER a blind header read and never
   uvicorn's loopback default. The session is also not additionally bound to the MCP session
   id. A cross-IP END-TO-END test still needs a loopback alias
   (`ifconfig lo0 alias 127.0.0.2`, root on macOS) or binding 0.0.0.0 and dialing the LAN
   IP — note that with normalization in place, `127.0.0.2` folds onto the same loopback
   token as `127.0.0.1`, so a loopback alias no longer distinguishes two clients. Use two
   genuinely different interfaces.
5. (Optional, open questions raised with the user and NOT yet answered)
   - `object_type` is a plain `str` + `_normalize_object_type`, not `Literal[...]`; a
     Literal would publish the 7 values as a JSON-schema enum to clients but would reject
     `"Table"`.
   - the flag columns of `get_object_details` (`not_null`, `is_primary`, ...) and
     `to_many` inside `reference_mapping` stay 1/0 as the user's SQL produces them, not
     JSON booleans.
   - `interval_value`/`interval_field` of an event are raw, not composed into a readable
     schedule.
6. (Optional) Raise `lib/setup.py` (84%) and `general.py` (73%) coverage — now the two
   weakest modules by far.
7. (Optional) `test_db_sql.py`'s module docstring still says "connect / execute_sql /
   close"; the flow now covers far more.
8. (Optional) `gui/extension/package.json:1192` still labels the plugin "MySQL Schema
   Management" in the VS Code UI — outside msm_plugin, so left inconsistent by the rebrand.

## Gotchas / things not to repeat

- **`/checkpoint` needs its target folder** — invoked bare it must ask, but this session
  is non-interactive; target was inferred as `mcp_plugin` from the session's work.
- **NEVER serve streamable-http with `mcp_server.run(transport=...)` again.** It re-opens
  S1: the SDK builds its own `uvicorn.Config` with uvicorn's `proxy_headers=True` default,
  and `ProxyHeadersMiddleware` then rewrites the peer address from `X-Forwarded-For` for
  every request from a trusted peer — which, on the default `127.0.0.1` bind, is every
  request. `_serve_streamable_http` exists solely to pass `proxy_headers=False`. Equally:
  do NOT "simplify" it to the `FORWARDED_ALLOW_IPS` env var — what an empty trust list
  means is uvicorn-version-dependent (in 0.52.1 it happens to become `trusted_literals =
  {""}`, matching nothing, but that is incidental), whereas `proxy_headers=False` keeps the
  middleware from being installed at all.
- **NEVER let the SDK decide the DNS-rebinding protection.** That was S4: pass
  `transport_security=` explicitly. `streamable_http_app` enables it only for the exact,
  case-sensitive strings `"127.0.0.1"`, `"localhost"`, `"::1"`, so `LOCALHOST`, `[::1]`,
  `127.0.0.2` and every non-loopback bind serve with NO Host or Origin validation. Do not
  "simplify" `_transport_security_settings` away because the default bind happens to be
  covered by the SDK — the whole point is that the coverage is a string match.
  `test_streamable_http_rejects_a_foreign_host_header` binds `--host=LOCALHOST` precisely to
  sit outside that match; do not "fix" it to 127.0.0.1, it would then pass on the SDK's
  behaviour and stop testing ours.
- **NEVER re-gate `_Connection.is_accessible_from` on `general.is_http_transport()`.** That
  was S2: the global is only set by `lib.server.start()` and never reset, so the check
  failed open for any embedder using the public `build_mcp_server`, and after any server
  stopped. It is a plain equality and stdio falls out of it for free (empty identity ==
  empty identity). Since T6 the transport is consulted in exactly ONE place —
  `db.connect`'s fail-closed branch — and
  `test_a_connection_stays_bound_without_an_active_transport` will fail if that changes.
  The same reasoning is why S6's hard TTL is applied in `_get_connection` and not left to the
  reaper (which only runs over HTTP).
- **The connection binding is the WHOLE `ClientIdentity`, not the address.** Dropping the
  session id half (S3) leaves a binding that cannot separate clients sharing an address —
  everything behind a NAT or proxy, and every local process on the default loopback bind.
  Do not "simplify" `is_accessible_from` to compare `.address`; three tests fail, one of
  them by performing a real takeover. Also: `use_session`/`_get_connection` take an
  IDENTITY, not a string. A caller passing a bare address string is refused (fail-closed,
  loudly) rather than silently matching.
- **`CLIENT_ADDRESS`/`OTHER_ADDRESS` in the tests must stay non-loopback** (`192.0.2.x`,
  TEST-NET-1). Every loopback form normalizes to one token, so loopback addresses cannot
  stand in for two different clients — the binding tests would assert nothing.
- **Normalize BOTH sides of an address comparison, or don't compare at all.** With the
  strict equality, an unnormalized side reintroduces `::1` vs `127.0.0.1` and
  `::ffff:a.b.c.d` vs `a.b.c.d` false negatives — the same client locked out of its own
  connection. `normalize_client_address` is therefore applied in THREE places on purpose
  (`get_client_address`, `_Connection.__init__`, `is_accessible_from`); it is idempotent, so
  the redundancy costs nothing and means no future caller can forget. Consequence to keep
  in mind: **every loopback address folds onto ONE token**, so `127.0.0.2` is NOT a second
  client any more — a loopback alias can no longer stand in for a remote client in a test.
- **New db.* tools stay SYNC** unless they need elicitation; don't convert them to async
  "for consistency" with msm/sandbox. They do take `ctx: Context` — a new one MUST, or it
  silently skips the client-address check.
- **The `_Connection` lock is an `RLock` and that matters twice.** `use_session` holds it
  and calls `open_session()`, which takes it again; `close_session_if_idle` holds it and
  calls `close_session()`, same. But reentrancy also means the "a session in use is never
  reaped" guarantee only holds ACROSS THREADS — a same-thread `_close_idle_sessions()`
  inside a `use_session` block WOULD acquire the lock and close it. Production is safe (the
  reaper is its own thread) and `test_a_session_in_use_is_not_closed` therefore runs the
  reaper pass from a real `threading.Thread`; do not "simplify" it to a direct call, it
  would pass for the wrong reason and assert the opposite of the truth.
- **Lock order is `_sessions_lock` -> `connection.lock`, never the reverse** (`db.close`
  takes and releases the dict lock, THEN closes). Keep it that way.
- **Don't make the wrong-client error more helpful.** It is byte-identical to the
  unknown-UUID error on purpose, so a probing client cannot tell a live connection id from
  a made-up one. `test_a_connection_is_bound_to_its_client_over_http` compares the two
  strings with the ids masked out and will fail if they drift apart. Since S5/S6 there is one
  factory, `_no_such_connection(connection_id)`, and THREE callers (unknown id, wrong client,
  expired) — build the error there, never inline, or the three drift.
- **Never log a connection UUID or an MCP session id in full.** Both are credentials: with
  either, a client can use a connection. Everything goes through
  `general.log_id_prefix` (8 chars + `...`) and `general.describe_client`. Two tests assert
  the full values are ABSENT from the log; writing them out fails them.
- **`general.log_event` must never raise.** The idle reaper calls it from inside its own
  `except` block, where an exception would end the thread and silently stop the idle timeout
  and the TTL from being applied. Its `try/except Exception: pass` around the `print` is that
  guarantee, and `test_logging_never_breaks_its_caller` pins it. Do not "clean up" the bare
  except, and do not switch to the `logging` module: under uvicorn an unconfigured logger
  drops INFO lines, which is how this would quietly stop recording anything.
- **`_claim_connection_slot` counts AND inserts under one hold of `_sessions_lock`, and runs
  BEFORE `open_session()`.** Both properties are load-bearing. Releasing the lock between
  count and insert lets a burst of concurrent `db.connect` calls each see room and overshoot
  the caps (real: SDK 2.0 runs sync tools via `anyio.to_thread`). Claiming the slot after the
  session is open means a refused call has already cost the database a connection — that is
  the revert probe the three S7 tests catch (`assert 4 == 3`). A failed open must pop the
  entry again.
- **The hard TTL is enforced in `_get_connection`, not only by the reaper** — the reaper is
  HTTP-only, and `sandbox.delete` can revoke a connection over stdio. Same lesson as S2: a
  safeguard that only runs when the transport global says so is one that can be off.
  `test_the_lifetime_holds_without_a_reaper` runs under `stdio_transport` for exactly this.
- **Expired connections must NOT count against the caps** and must not be revived by
  anything. `_claim_connection_slot` skips them; `_get_connection` drops them.
- **`_drop_connection` pops under `_sessions_lock`, then closes the session with the lock
  RELEASED** (and `close_session` then WAITS for any running statement, deliberately, unlike
  the idle pass's non-blocking acquire). Do not "optimize" the close inside the dict lock —
  that inverts the documented lock order and blocks every other tool call behind a long
  statement.
- **Do not stub `_open_session` in a test meant to exercise the revoked-URI check** — that
  takes the check under test with it. Patch `config.list_connection_uris` instead and let the
  real `_open_session` run; it consults the list before touching the secret store or the
  network, so no server is needed. (First draft of `test_removing_a_connection_revokes_it`
  got this wrong and passed for the wrong reason.)
- **After `close_session()` the `_Connection.session` is None**, so a test cannot assert
  `connection.session.closed` afterwards — hold the `_StubSession` reference itself
  (`_register_connection(client, session)`). Three tests were written wrong this way first
  and failed with `AttributeError: 'NoneType' object has no attribute 'closed'`.
- **`_Connection.closed` is a one-way flag and `close_session()` must not touch it.** Setting
  it there would make an IDLE close terminal, so an idle connection could never be reopened —
  `test_an_idle_session_is_closed_and_opened_again` asserts `connection.closed is False` after
  an idle pass for exactly that. Terminal paths (`db.close`, `_drop_connection`) use
  `close()`; the idle path uses `close_session()`. And do not "simplify" the flag away in
  favour of holding `connection.lock` across `session.close()`: the racing caller would just
  open its new session once the close finished. That is T1, and it leaked a server-side
  connection every time under ordinary concurrent use.
- **`session.is_open()` tells you NOTHING about whether the connection is alive.** It is
  `_mysql ? true : false` — a local handle check that stays True after a KILL, after the
  failing statement, and after the server has been stopped (all three measured). Never write
  a liveness check on it. A dead session is recognized ONLY from the DBError code
  (`_CONNECTION_LOST_ERRORS`: 2013 first, then 2006 on every later call), and an ordinary SQL
  error is the same exception type with a server-side code — so match on the code, and keep
  the list to the codes that really mean it.
- **Never take a database session on the event-loop thread**, and never `await` inside a
  `use_session` block. `use_session` refuses the first outright
  (`_refuse_on_the_event_loop`), because the connection's lock is a THREAD lock held for the
  whole block: waiting for it on the loop thread freezes the entire server (measured: zero
  other tasks ran during a 0.6s wait), and running the work there does the same. Async tool
  code hands the block to `anyio.to_thread.run_sync` — `msm.deploy_schema` is the pattern, and
  it reads the client identity BEFORE the hand-off since a worker thread has no request
  context. The second rule is the mirror image: an RLock is owned by a thread, not a task, so
  awaiting inside the block would NOT keep another coroutine out — measured, it walks straight
  in reentrantly and two calls then share one session. Anything that awaits (an elicitation)
  goes before the block.
- **`session_restarted` must never outlive the call it describes.** It is per-CALL state kept
  on the `_Connection` only because the tools have no other channel: `use_session` assigns it
  on entry and clears it in `finally`. A flag left standing tells the next caller its
  transaction was lost when it was not, which is worse than saying nothing. Assert it right
  after the reopening call, not after the following one - the following call's entry assignment
  hides the bug.
- **A failed statement is never retried behind the client's back.** It may have run in part
  and any transaction died with the connection; the session is discarded so the NEXT call
  works, and the client gets the error. Do not "improve" this into an automatic retry.
- **A revert probe has to fail for the RIGHT reason** — read the failure text, never just the
  pass/fail count. Both T1 tests first failed under the probe on an `AttributeError:
  '_StubSession' object has no attribute 'run_sql'` and on an unrelated `mysqlsh.Error` that
  `close_session`'s own `except` swallowed, which would have "proven" the fix while testing
  nothing; `_StubSession` therefore has `run_sql` + `_StubResult`, so a call that is NOT
  refused runs to completion and the probe fails with `DID NOT RAISE`. And a
  **negative-control test needs its own probe in the OPPOSITE direction**:
  `test_an_ordinary_sql_error_keeps_the_session` cannot fail when T3's fix is removed, only
  when its predicate is made too broad — so that one took two probes.
- **The reaper belongs to the SERVER, not to the process or to a tool call.**
  `lib/server.start()` calls `db_functions.start_connection_reaper()` before serving over HTTP
  and `stop_connection_reaper()` in a `finally`. Do not put the start back into `db.connect`
  (T6: nothing then stopped it, a second server in one process kept the first one's thread, and
  it spawned a thread while holding `_sessions_lock` — the lock that thread wants). The handle
  is guarded by `_reaper_lock`, never `_sessions_lock`, and the thread waits on `_reaper_stop`
  rather than sleeping, so a stop does not have to wait out an interval.
- **Reaper naming history** (so the old names in older commits still make sense): S6 renamed
  `_idle_reaper` -> `_reaper`, `_reap_idle_sessions` -> `_reap_connections`,
  `_start_idle_reaper` -> `_start_connection_reaper` and `_IDLE_CHECK_INTERVAL` ->
  `_REAP_INTERVAL` (it does two jobs now, so the old names lied); T6 then replaced
  `_start_connection_reaper` with the public `start_connection_reaper` /
  `stop_connection_reaper` pair. **No test leaves a reaper running any more** - only
  `test_the_reaper_can_be_started_and_stopped` starts one, and it stops it again - so the old
  warning about a thread living for the rest of the pytest run no longer applies.
- **The shell already returns DECIMAL and DATETIME as STRINGS.** Verified: a
  `CAST(2 AS DECIMAL(10,2))` arrives as `'2.00'`, and I_S.SEQUENCES `START_VALUE` /
  `INCREMENT` as `'1'`. So `_serialize_result` needs NO numeric conversion — a
  `numbers.Number -> int/float` branch was written, proven unreachable, and REMOVED. Do
  not re-add it. What is left is bytes -> hex plus a `str()` guard for anything exotic
  (that guard is the single uncovered line in db_functions.py; it is insurance against a
  serialization crash killing a tool call, not a code path the tests exercise). Making
  decimals into JSON numbers would need CASTs in the individual queries (and
  `CAST(... AS SIGNED)` would mangle an UNSIGNED sequence past 2^63) or numeric-string
  sniffing, which would silently reinterpret genuine VARCHAR data. Don't.
- **`helpers.tool_payload` collapses a single-element list into the bare element** (and an
  empty list into None) — a one-row listing is NOT a list. Normalize in the test before
  iterating; this bit the list_objects assertions AND, one commit later, the deploy
  assertions. It WILL bite again.
- **Never guard a tool registration with an early `return` in a registrar** — the tools
  defined AFTER it silently stop being registered. `msm.deploy_schema`'s gate first used
  `if db not in function_groups: return` placed mid-registrar, which would have dropped
  `msm.get_deployment_script_versions` from every msm-only server. Register conditional
  tools LAST, inside an `if`, and assert the neighbouring tools still exist in both
  configurations (`test_stdio_deploy_schema_requires_the_db_group` does).
- **Run the suite from the mcp_plugin dir with `/opt/homebrew/bin` on PATH.** Two runs this
  session died instantly on `can't open file '.../run_tests.py'` because the cwd was the
  repo root; without homebrew on PATH the sandbox deploy finds no `mariadbd`.
- **msm_plugin / mrs_plugin suites**: run each as `mariadb-shell --py -f run_tests.py` from
  ITS OWN plugin dir, with `/opt/homebrew/bin` on PATH for `mariadbd`. `-s <path>` is no
  longer needed now that the runners default from `MARIADB_SHELL` or
  `shutil.which("mariadb-shell")`.
- **Filtering tests with `-k` breaks the db/msm/sandbox tests** — they depend on
  `test_sandbox_deploy` running first (conftest ordering hook + `sandbox.deployed` flag),
  and skip themselves if it didn't. Run the full suite to validate. For a targeted run that
  still needs the sandbox, `--only="'sandbox_deploy or <pattern>'"` works (the ordering hook
  puts the deploy first), but `test_sandbox_shutdown` is then deselected and the session
  fixture's best-effort finalizer does the teardown.
- **`run_tests.py` interpolates `-k` into a `shell=True` STRING** (`pattern = f"-k
  {args.only}"`, run_tests.py:130), so a pattern containing spaces is split by the shell and
  pytest dies with `file or directory not found: or`. The pattern needs its OWN quotes:
  `--only="'a or b'"`. Worth fixing to a proper argv list; not done yet.
- **A non-matching glob ABORTS the whole command under zsh** (`no matches found:
  .coverage.*`) — the shell here is zsh, not bash, so it does not pass the pattern through.
  A run "silently doing nothing" with only that message is this, not a test failure. The
  file to remove after a partial run is plain `.coverage` (still: never `.coverage*`, which
  matches `.coveragerc`).
- **`| tail -N` on a backgrounded run hides ALL output until it exits**, so polling the task
  output file mid-run shows an empty file and looks like a hang. Wait for the notification,
  or drop the pipe.
- **OBSOLETE AS OF SDK 2.0 — do not act on the old note.** Under mcp 1.28.x sync tools ran
  directly on the event loop (func_metadata: `else: return fn(...)`), which is why a
  sync-tool `anyio.from_thread.run` elicitation bridge was impossible and async tools were
  the only route. In **2.0.0 that changed**: `func_metadata.py` now does
  `await anyio.to_thread.run_sync(functools.partial(fn, ...))` for the non-async branch, so
  sync tools DO run in a worker thread and such a bridge is now technically possible. The
  existing async msm/sandbox tools work fine and there is no reason to rewrite them — but
  the stated impossibility is no longer a valid argument.
- **Elicitation is async**: `await ctx.elicit(message, schema=BaseModel)` ->
  ElicitationResult with `.action` ("accept"/"decline"/"cancel") and `.data`. Tools must be
  `async def` and declare `ctx: Context`.
- **No-hang for elicit**: a ClientSession without an elicitation_callback does NOT advertise
  the capability; server's elicit sends anyway and the client's default callback returns
  `ErrorData("Elicitation not supported")` -> server raises McpError -> guard `except` ->
  returns False -> "not allowed" error. In tests, pass `elicitation_callback` to answer
  (`types.ElicitResult(action="accept", content={"trust": True})`).
- **REST SQL / MRS session duplication**: MRS runs metadata DDL on a SECOND session so
  `USE x` etc. don't disturb the caller. `mrs_plugin/lib/general.py` originally did
  `if "shell.Object" in str(type(session))` -> no-arg `shell.open_session()` = duplicate the
  GLOBAL session (carries password). The MCP db layer uses `shell.open_session(connection_data)`
  and sets NO global session -> that path raised "An open session is required when
  duplicating sessions". A native shell session has NO `.connection_options` attr (GUI-only)
  — only `get_uri()` (no password), so `open_session(session.connection_options)` fails with
  "unknown attribute: connection_options" and URI-reopen can't carry credentials. Landed as
  ca47b8c8 (reuse caller's session) then 82e18c4c (original code restored + a different
  workaround until `session.clone()` exists). Alternative never taken:
  `shell.set_session(session)` in db.connect.
- **test client is plain `ClientSession(read,write)`** — happy-path tests pre-register their
  paths (via fixtures) so no elicitation fires.
- **tool_payload returns None for an empty list** (zero content blocks). Guard `or []`
  before `in`/iteration (bit test_sandbox_shutdown).
- **Don't delete `.coverage*` with a glob** — matches `.coveragerc`. Use `.coverage.*`.
- **pytest-dependency does not build in this env** — ordering is native (conftest hook +
  `sandbox.deployed` flag). Long runs: Bash `run_in_background: true` so a hang is stoppable;
  chained `sleep` polling is blocked by the harness — wait for the task notification.
- **run_sql rejects multi-statement** (1064) — split via `mysqlsh.mysql.split_script`, and
  don't leave a trailing `;` on single-statement SQL constants.
- **Sandbox port required** — never None/omit ("Argument #1 is expected to be an integer").
- **stdio needs clean stdout** — don't add prints to the stdio path.
- Don't reintroduce bg-thread+SIGTERM serving nor the interactive guard in `start()`.
  Plain GPLv2+MariaDB header. tests/ has no `__init__` (namespace pkg + `PYTHONPATH=..`).
- **A stale `.pyc` makes a rename look like it didn't apply.** After renaming the runtime
  home `dot_mysqlsh` -> `dot_mariadb_shell`, pytest kept printing the OLD path — the same
  temp dir across two runs, which is the tell (a fresh `TemporaryDirectory()` differs every
  run). The old path was baked into `co_filename` in `tests/unit/__pycache__/*.pyc`. Clear
  `__pycache__` and `.pytest_cache` before concluding an edit didn't take.
- **`mrs_plugin`'s suite leaks its sandbox on port 3388** whenever a run errors out, and the
  NEXT run then fails ~80 tests with `Port '3388' is already in use`. Two measurements were
  wasted on that echo. Kill the listener (`lsof -tnP -iTCP:3388 -sTCP:LISTEN`) and confirm
  the port is free BEFORE trusting any mrs_plugin pass/fail count.
- **`service.name` is a DB DEFAULT EXPRESSION**, not a trigger and not set by
  `add_service`: `regexp_replace(url_context_root, '[^0-9a-zA-Z ]', '')`. Reading the table
  DDL and the triggers does NOT reveal it — only
  `INFORMATION_SCHEMA.COLUMNS.COLUMN_DEFAULT` does. So a service at `/test` is named
  `test`, and `/service_to_delete` becomes `servicetodelete` (`_` is stripped too).
  `"name": "mrs"` was only ever right for the table's default `/mrs` context root, which
  `add_service` now REJECTS as reserved. It was stale in 12 places across
  `tests/unit/helpers.py`, `test_core.py`, `test_services.py`, `lib/test_services.py` and
  blocked 80 tests in fixture setup. When a whole suite errors in one fixture, fix the
  fixture first and re-measure — the failure count went UP (6 -> 25) because unblocked
  tests then failed on their own merits.
- **`REGEXP_LIKE` does not exist in MariaDB** (MySQL 8 only). `mysql_tasks`' SQL calls it
  (e.g. `mysql_tasks_3.0.2.sql:1809`), so 3 mrs_plugin tests die with
  `MySQL Error (1305): FUNCTION ... REGEXP_LIKE does not exist`. MariaDB form is the
  `REGEXP` operator / `REGEXP_REPLACE`. (The pattern `';[:space:]*$'` there also looks like
  a typo for `';[[:space:]]*$'`.) A real portability bug, not a test issue.
- **This file goes stale fast** — the previous checkpoint listed 3 "next steps" that were
  all already committed, claimed the introspection tools were UNCOMMITTED when they were in
  3482634a, and carried an SDK-1.x threading gotcha that 2.0 reversed. Re-check `git log`
  and the installed SDK against it before trusting it.

## Git state

- **A NEW branch `wip/MCP-CONN-HANDLING` was cut from `main` (1d6a9c37) after the AIPL-16
  work below was merged**, for one reported bug: `db.connect` compared the URI it was given
  with the configured ones as STRINGS, so `mariadb://root@127.0.0.1:PORT` — the form a
  client naturally writes, and one `shell.parse_uri` rejects as an invalid scheme — never
  matched the stored `root@127.0.0.1:PORT`. Fixed with URI normalization/resolution in
  `lib/config.py` (see the Connections bullet under Architecture), applied in `db.connect`
  and in `mcp.setup`'s add-connection flow, documented in the README's new "Which URI names
  which connection" section, and pinned by 5 tests. Both fixes were PROVEN to discriminate
  by reverting them: without the `db.connect` one the test fails with the reported error
  verbatim, without the `mcp.setup` one `not a uri` is stored as a connection. NOT COMMITTED
  as of this checkpoint.
- Branch: **`wip/AIPL-16`**, cut from `main` (which is at 8da59831 and tracks
  `mariadb`) and pushed to `mariadb/wip/AIPL-16`. Remote `mariadb` =
  mariadb-corporation/mariadb-shell-plugins; `origin` is mysql/mysql-shell-plugins and is
  NOT the push target. There is also a `local_office` remote (a NAS mirror) — not a push
  target either.
- **The session started in DETACHED HEAD** at `mariadb/wip/AIPL-16` (ecc6bc3c) with no
  local branch — `git checkout -b wip/AIPL-16` was needed before committing. Check
  `git branch --show-current` before assuming there is a branch to commit onto.
- The security work is eight commits on that branch:
  1. **S1 + S2/S8** (faa08b11) — the proxy-header fix and the transport-independent,
     normalized binding.
  2. the `sandbox.deploy` `sandbox_dir` docstring note (5b94d940) — a pre-existing edit the
     session inherited unstaged, committed separately on the user's instruction because it
     is not part of the security work.
  3. **S3 + S4** (9c877f37) — the session-id binding, the no-authentication README section
     and warning, and the explicit Host/Origin validation.
  4. **S5 + S6 + S7** (b86b0541) — the audit trail, the hard TTL + URI re-validation, and the
     caps. ONE commit, not three: S6 and S7 both call S5's `log_event` and all three interleave
     inside the same functions and docstrings, so splitting them would have meant committing
     intermediate states that were never run green. The user asked for the commit without
     specifying granularity after that reasoning was put to them, and the same reasoning (and
     the same answer) applied to every commit after it.
  5. **T1 + T2 + T3 + T4** (f80673ce) — the close/use race, the cross-thread verification and
     its regression test, dead-session detection, and the session_restarted signal. ONE commit
     again, for the same reason: T3 and T4 both build on paths T1 changed, and all four touch
     `use_session`, `_Connection` and the same docstrings, so any split would have committed
     states that were never run green. **Its coverage claim (100% / 97%) is WRONG** — see the
     Coverage bullet under Current state.
  6. **T5 + T6** (542c0d79) — session work off the event-loop thread, and the reaper's lifetime
     moved to `server.start()`. Together because T6's wiring test asserts the order
     `start`/`served`/`stop` around the same `server.start()` branch T5 left alone, and both
     land in `use_session`/`server.py`. Also carries the README change from
     `mariadb-shell --py -e "mcp.setup()"` to `mariadb-shell -- mcp setup` (the user's own
     wording) and the corrected coverage figure.
  7. **the three untested paths** (17a3408d) — `db.connect`'s slot giveback and its
     unconfigured-URI refusal, and `server.start`'s three validation raises. Tests only.
  8. **T8 + T9** (HEAD when this was written) — duplicate column labels keyed apart and read by
     position, and the FK reference mapping ordered by the key's own ordinal. Together because
     both are `_serialize_result`/introspection output and both are pinned in the same
     `test_db_sql` flow.
- **The remote branch moved mid-session and the push was rejected.** `mariadb/wip/AIPL-16`
  had gained e9122f6d (a merge of `main`, bringing 962165d7, a CI job-name change touching
  only `.github/workflows/shell-plugins-ci.yml`). Rebased rather than merged (one commit, no
  file overlap) and then **re-ran the full suite on the new base before pushing** — do that
  again rather than pushing a commit that was only ever green on the old base. Note
  `git stash push -- <path>` was needed first: the inherited unstaged edit blocked the
  rebase.
- Earlier commits on the branch: 0bba1318 (connection safeguards as first built),
  6067fd8c (idle timeout 10 -> 30 min), ecc6bc3c (branch rename recorded here).
- `.claude/skills/create-shell-plugin/SKILL.md` is still deliberately left UNSTAGED (not
  this work).
- The `wip/AIPL-5` history below predates it and is already in `main`:
- Session history: 3482634a (db introspection tools) -> the msm_plugin MariaDB/sandbox/
  backup commit -> the mcp_plugin `msm.deploy_schema` + db-group-gate commit -> 72c07ef8
  (doc the new db/msm tools + `get_mcp_plugin_data_path` -> `get_plugin_data_path`).
- Then THIS session: the three rebranding commits from `mariadb/rennox/rebranding`
  (759c375d msm, 7f119fd6 mrs, f11a3897 mcp) cherry-picked with `-n` onto `wip/AIPL-5`,
  reviewed by the user, and committed together with the MCP SDK 2.0 migration and the
  test-suite fixes.
- `mariadb/rennox/rebranding` itself is NOT merged — its commits were replayed, so the
  branch will look unmerged and a future merge would conflict. Rebase or drop it.
- One unrelated pre-existing edit was left UNSTAGED on purpose:
  `.claude/skills/create-shell-plugin/SKILL.md` (not this session's work).
