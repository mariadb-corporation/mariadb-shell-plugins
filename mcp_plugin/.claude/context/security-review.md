# The S and T security review

The numbered review of the connection handling: S1..S8, then T1..T9. One
entry per item — what was reported, what was actually true, what was built,
and the revert probe that proved the test discriminates.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). What it all built is
described in [connections.md](connections.md).

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

## Next steps

1. **The security-review list S1..S8 is COMPLETE, committed and pushed; a T-numbered list
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
