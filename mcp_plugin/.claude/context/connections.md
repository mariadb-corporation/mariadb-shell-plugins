# Connections and sessions

The two connection lists, how a URI names one, and everything that guards a
session once it is open: the client binding, the idle timeout, the hard TTL,
the caps, the audit log and the locking rules.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The numbered review that
produced most of this is in [security-review.md](security-review.md).

## Architecture / key decisions

- **Two connection lists, told apart by a `kind`** (`lib/config.py`): `mcp`
  (`MCP:Connection:`, curated with `mcp.setup`, openable by any client) and `gui`
  (`GUI:Connection:`, the extension's own). Neither prefix is a prefix of the other, so
  the listings cannot bleed. Every connection function takes `kind` and **defaults it to
  `mcp`**, which is what every caller written before this means — `mcp.setup`,
  `sandbox.deploy` and the migrator tools were therefore not touched and stay MCP-only.
  - `normalize_connection_kind()` refuses an unknown kind rather than defaulting it: a
    misspelling would otherwise act on the other list.
  - `usable_connection_kinds()` is `(gui, mcp)` in GUI mode and `(mcp,)` otherwise, so
    the GUI list is **unreachable** without `--gui`.
  - `find_connection(uri, kinds)` returns `(uri, kind)`, first list wins — a URI in both
    resolves rather than being refused. (Two spellings WITHIN one list are still refused.)
    `resolve_connection_uri(uri, kind)` is the one-list form the old callers still use.
  - `_Connection.kind` is kept for the connection's life, and `_open_session(uri, kind)`
    re-validates in THAT list only: deleting from the GUI list revokes even where the MCP
    list names the same server.
  - `db.delete_connection` also drops every open connection on it (`_drop_connections_on`),
    so a deletion takes effect at once instead of at the next session reopen.
  - `db.add_connection` verifies through `setup_cli.verify_connection` — the same function
    `mcp.setup` uses, so a connection is accepted on identical terms either way.
  - `db.update_connection` re-keys a connection (new URI, other list, new password) and is
    what lets an editor change one WITHOUT the password: it reads the stored secret and
    writes it straight back, and nothing returns it. Writes the new key before deleting the
    old, so a failure leaves the connection configured somewhere rather than nowhere, and
    drops what was open on the old key.
  - `db.test_connection` opens a session and closes it, storing nothing — the question an
    editor's Test button asks, which `db.connect` cannot answer (configured connections
    only) and `db.add_connection` cannot either (stores on success). Its password is
    optional: left out it uses the stored one, so editing does not mean retyping.
    **GUI-only for a reason of its own** — it opens a session to any host and credentials
    it is handed, so an autonomous client could try passwords against any reachable server.

- **The scheme is part of the URI, since MariaDB Shell 26.9.3** (`lib/config.py`). Until
  then `parse_uri` REJECTED `mariadb://` outright, so `parse_connection_uri` STRIPPED a
  `mariadb://`/`mysql://` prefix and connections were stored with no scheme at all. 26.9.3
  takes the whole family, and `mariadb+ssh://` is the ONLY way to ask for an SSH tunnel —
  so the scheme now has to survive into the stored key. `DEFAULT_CONNECTION_SCHEME` is
  `mariadb` and is filled in where a URI names none.
  - **Nothing is migrated, and nothing has to be.** `list_stored_connection_uris()` is the
    KEY list (secret-store keys, some of them scheme-less); `list_connection_uris()` is what
    is REPORTED and fills the default scheme in with `with_default_scheme()` — a purely
    TEXTUAL helper, deliberately not a re-normalization, so a key this shell can no longer
    parse still reports as itself. `_resolve_in_kind` and `_open_session`'s re-validation
    read the STORED list; everything user-facing reads the reported one. Getting that
    backwards is how a password lookup breaks, since the reported spelling is not the key.
  - **`store_connection` is a plain write; `drop_superseded_spellings(uri, kind)` is what
    keeps one connection to one key.** It runs AFTER the write (so a failure in between
    leaves the connection configured under one of the two, never neither) and is called by
    the three paths that CONFIGURE a connection: `db.add_connection`, `db.update_connection`
    and `setup_cli._add_connection`/`setup._add_connection`. Without it, re-adding a
    connection stored under the old spelling leaves BOTH keys and the pair then resolves to
    NEITHER — `_resolve_in_kind` refuses two spellings of one connection as ambiguous. It
    was deliberately NOT folded into `store_connection`: the ambiguity behaviour is a tested
    property of `resolve_connection_uri`, and its test builds that state by storing twice.
  - `db.update_connection` must check `configured_uri not in superseded` before deleting the
    old key — `delete_secret` RAISES on a missing key, and storing the new spelling of the
    same connection may already have taken it.
  - `sandbox.deploy` registers `mariadb://root@127.0.0.1:<port>` and `sandbox.delete`
    RESOLVES rather than comparing, so an instance deployed before the change is still
    cleaned up.

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
  reopen). `config.normalize_connection_uri()` is the comparison form: lowercase the
  `scheme://` prefix (the shell's parser is case-sensitive on it), then `parse_uri` ->
  default the scheme to `mariadb` when there is none -> drop the password, lowercase the
  host, spell out port 3306 when the URI left it out (only for `PROTOCOL_SCHEMES` =
  mariadb/mysql with or without `+ssh`, and NOT when the host is really a socket path —
  an absolute socket comes back as `host`, not `socket`) -> `unparse_uri` (which also fixes
  option order, percent-encoding and a trailing slash). It is a fixed point, so stored and
  incoming URIs go through the same function. Everything ELSE in the URI is kept and must
  match — a schema (`/db`), an option (`?ssl-mode=REQUIRED`) or a different scheme the
  configured connection does not have makes it a different connection, refused rather than
  silently answered with a session that does not do that; `mysql://`, `mysqlx://` and
  `mariadb+ssh://` all stay distinct from `mariadb://`. `mcp.setup` stores the normalized
  URI, so one connection has one key; the same connection configured under two spellings is
  the one case resolution cannot settle and it raises instead of guessing. Opens via
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

## Files that matter

- lib/general.py -> plugin data path, the migrator path helpers (`get_data_home`,
  `get_migrator_root`, `get_migrator_path`, `MIGRATOR_DIR_NAME`, `MIGRATOR_VERSION`),
  async `require_allowed_path`/`_confirm_trust_path`,
  the transport global (`set_active_transport`/`is_http_transport`), the client identity
  (`ClientIdentity`, `get_client_identity`, `get_client_address`, `get_client_session_id`,
  `normalize_client_address`/`normalize_client_identity`, `LOOPBACK_ADDRESS`,
  `MCP_SESSION_ID_HEADER`), the bind-address helpers (`is_loopback_host`,
  `is_wildcard_host`, `LOOPBACK_HOST_NAMES`), the stderr audit log (`log_event`,
  `describe_client`, `log_id_prefix`, `LOG_ID_PREFIX_LENGTH`) and every connection limit
  (`SESSION_IDLE_TIMEOUT`, `CONNECTION_MAX_LIFETIME`, `MAX_CONNECTIONS_TOTAL`,
  `MAX_CONNECTIONS_PER_CLIENT`). **100% covered — keep it that way.**

- lib/config.py -> connections (secrets) + allowed paths (settings.json) + `add_allowed_path`.
  Also the two connection lists: `CONNECTION_SECRET_PREFIX` / `GUI_CONNECTION_SECRET_PREFIX`,
  `CONNECTION_KIND_MCP` / `CONNECTION_KIND_GUI` / `SUPPORTED_CONNECTION_KINDS` /
  `DEFAULT_CONNECTION_KIND`, `normalize_connection_kind`, `connection_secret_prefix`,
  `usable_connection_kinds`, `_resolve_in_kind` and `find_connection`; and the scheme:
  `DEFAULT_CONNECTION_SCHEME`, `PROTOCOL_SCHEMES`, `_SCHEME_PREFIX`,
  `with_default_scheme`, `list_stored_connection_uris` vs `list_connection_uris`, and
  `drop_superseded_spellings`. `is_path_allowed` is the single GUI-mode path chokepoint.

- lib/db_functions.py -> db.* tools; the `_Connection` cache (`_sessions` + `use_session` +
  the reaper + `_claim_connection_slot`/`_drop_connection`/`_no_such_connection` +
  `_ConnectionClosed` and the `closed` flag + `_CONNECTION_LOST_ERRORS`/
  `_is_connection_lost`); `_serialize_result` + `_unique_column_labels`;
  `_serialize_result`;
  the introspection SQL constants (`_LIST_SCHEMAS_SQL`, `_LIST_OBJECTS_SQL`,
  `_OBJECT_BASIC_SQL`, `_OBJECT_DETAILS_SQL`, `_ROUTINE_PARAMETERS_SQL`,
  `_OBJECT_COLUMNS_SQL`, `_OBJECT_CONSTRAINTS_SQL`, `_OBJECT_REFERENCES_SQL`).

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

- tests/unit/test_db_recovery.py -> T3: a real session, KILLed from a second session, is
  discarded and replaced on the next call. Needs the shared sandbox (and relies on
  sandbox.deploy having registered its URI, since every open re-validates it). Cannot be done
  with a stub: only the real client library produces the 2013/2006 codes the fix reads.

- tests/unit/test_db_threading.py -> T2: proves a shell session can be opened on one
  thread, used from another and closed from a third (named after the reaper), and that the
  server really drops the connection. Needs the shared sandbox. Its module docstring carries
  the Connector/C and Session_impl evidence - read it before believing any future claim that
  cross-thread session use is unsafe here.

## Gotchas / things not to repeat

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

## Next steps

5. (Optional, connection handling) Open points deliberately NOT built, none of them asked
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
