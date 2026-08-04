# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. GPLv2,
"MariaDB plc". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`), **MCP SDK 2.0.0**, Python 3.14, pytest
9.1.1, `mariadbd` at `/opt/homebrew/bin` (MariaDB 12.3.2). Full suite: **17 tests pass
(~28s), 93% total coverage**. Run it with `mariadb-shell --py -f run_tests.py` FROM the
mcp_plugin dir and with `/opt/homebrew/bin` on PATH (mariadbd is not on the default
PATH).

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
  configured URIs; opens via `_open_session()` = `parse_uri`+password ->
  `shell.open_session` (independent of the shell's global session). `_sessions` maps the
  UUID to a **`_Connection`** record (uri, `client_address`, `session`, `last_used`,
  `lock`), NOT to a bare session; `_sessions_lock` guards the dict, each `_Connection` has
  an `RLock` of its own.
- **HTTP-only connection safeguards** (branch `wip/AIPL-16`): gated on
  `general.is_http_transport()`, which reads the `_active_transport` module global that
  `lib/server.start()` sets from its `transport` arg BEFORE serving. Over stdio (one
  client, the parent process, for the server's whole life) NEITHER applies:
  - **Address binding**: a connection may only be used from the IP that opened it.
    `general.get_client_address(ctx)` = `ctx.request_context.request.client.host` — the
    TCP peer of the request, deliberately NOT `X-Forwarded-For` or any other header
    (client-supplied, forgeable). `request` is a starlette Request on HTTP and None on
    stdio, so the `try/except` + `getattr` chain returns None there. A mismatch raises the
    BYTE-IDENTICAL error an unknown UUID raises, so probing cannot tell a real UUID from a
    guessed one — keep those two messages the same. In HTTP mode `db.connect` with no
    determinable address FAILS CLOSED.
  - **30-minute idle timeout** (`general.SESSION_IDLE_TIMEOUT = 1800`, raised from the
    10 minutes it shipped with in 0bba1318): a daemon reaper
    thread (`_reap_idle_sessions`, started ONCE by the first `db.connect` via
    `_start_idle_reaper`, wakes every `_IDLE_CHECK_INTERVAL = 30`s) closes the SESSION of
    every idle connection but KEEPS the `_Connection`, so the UUID stays valid and the next
    tool call reopens transparently. `db.close` is the documented exception: it drops the
    entry and closes only an already-open session, it never reopens one to close it.
  - The reopened session is a NEW server session: temp tables, session vars, current
    schema and open transactions do NOT survive an idle period. Documented in the
    `db.connect` tool description (which clients see), the module docstring and the README.
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
- **Two deliberate FIXES to the user's supplied SQL** (both flagged to and left standing
  by the user):
  - constraints query needed `AND tc.TABLE_NAME = kcu.TABLE_NAME` — constraint names are
    unique per TABLE, not per schema, so joining on schema alone makes every table's
    `PRIMARY` match every other table's PK columns. Test pins this (two PK tables in the
    schema, `items` must report exactly one constraint row).
  - dropped the columns query's dead `LEFT OUTER JOIN` on KEY_COLUMN_USAGE: nothing was
    selected from it and it can duplicate a column that sits in two FKs. Restore with
    DISTINCT if fields from it are ever needed.
  - also added `ORDER BY tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION` for determinism.
- **REST SQL** (commit 0e97c9e9): `db.execute_sql` can run MRS REST SQL (e.g.
  `CONFIGURE REST METADATA`) because `mrs_plugin` registers a shell SQL handler
  (`@sql_handler("MRS", prefixes=...)`, prefixes incl. "CONFIGURE REST ") that intercepts
  `session.run_sql`. Requires mrs_plugin loaded in the server subprocess (symlinked by
  run_tests). antlr4 (MRS parser dep) is bundled in mariadb-shell.

## Current state

- **THIS session (branch `wip/AIPL-16`, off `main`): the two HTTP-only
  connection safeguards** described above — address binding + 10-minute idle timeout.
  Touched `lib/general.py` (transport global, `SESSION_IDLE_TIMEOUT`,
  `set_active_transport`/`is_http_transport`/`get_client_address`), `lib/db_functions.py`
  (the `_Connection` record, `_open_session`, `_get_connection`, `use_session`,
  `_close_idle_sessions`/`_reap_idle_sessions`/`_start_idle_reaper`, `ctx: Context` on all
  8 db tools), `lib/server.py` (`set_active_transport(transport)` in `start()`),
  `lib/msm_functions.py` (`deploy_schema` -> `use_session`), README and tests. NOTE: the
  cross-IP rejection is proven in-process only — driving two genuinely different source
  addresses at a local server needs a loopback alias (root on macOS), so the suite does not.
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
- Shell fns: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer`.
- Tools: db.* (**8**: `list_connections`, `connect`, `list_schemas`, `list_objects`,
  `get_object_details`, `execute_sql`, `execute_sql_script`, `close`),
  msm.* (**12**, path-guarded, async — the 12th is `deploy_schema`, gated on the db group),
  sandbox.* (7, `sandbox_dir`-guarded, async, port required).
- Tests (tests/unit/, no `__init__`): `test_sandbox` (deploy FIRST, shutdown LAST +
  path-reject), `test_config` (6), `test_msm` (5: create_project, elicit-accept,
  elicit-decline, deploy-needs-db-group, lifecycle), `test_db_sql`, `test_rest_sql`,
  `test_transport_http` (2: list_connections + a full connect/execute/close db flow over
  HTTP), `test_db_sessions` (7, the connection safeguards). **25 pass, ~29s.**
- Coverage after latest run: lib/msm_functions 100, lib/server 100, lib/general 98,
  lib/config 96, lib/db_functions 95, lib/sandbox_functions 93, server.py 85, lib/setup 84,
  general.py 73. TOTAL 93% (605 statements, 40 missed). What is left uncovered in
  db_functions is defensive only: the `session.close()` swallow, the reaper thread's own
  loop body (it sleeps 30s), `_start_idle_reaper`'s not-http return, the JSON-parse
  fallback and the `_serialize_result` `str()` guard.
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
  the transport global (`set_active_transport`/`is_http_transport`), `get_client_address`
  and `SESSION_IDLE_TIMEOUT`.
- lib/config.py -> connections (secrets) + allowed paths (settings.json) + `add_allowed_path`.
- lib/db_functions.py -> db.* tools; the `_Connection` cache (`_sessions` + `use_session` +
  the idle reaper); `_serialize_result`;
  the introspection SQL constants (`_LIST_SCHEMAS_SQL`, `_LIST_OBJECTS_SQL`,
  `_OBJECT_BASIC_SQL`, `_OBJECT_DETAILS_SQL`, `_ROUTINE_PARAMETERS_SQL`,
  `_OBJECT_COLUMNS_SQL`, `_OBJECT_CONSTRAINTS_SQL`, `_OBJECT_REFERENCES_SQL`).
- lib/msm_functions.py, lib/sandbox_functions.py -> async tools w/ `ctx: Context`;
  msm_functions also holds the db-group-gated `msm.deploy_schema`.
- lib/server.py -> build/serve; `_serve_stdio` hardening; passes function_groups to the
  registrars.
- tests/conftest.py -> ordering hook, fixtures (sandbox session, allowed_temp_dir,
  clean_config, stored_connections, non_interactive_shell).
- tests/unit/helpers.py -> `call_tool` (has `elicitation_callback`), `mcp_session`,
  `list_tool_names` (what the server ADVERTISES, used for the group gate), `tool_payload`,
  `find_free_port`, `server_binary_available`, `shell_binary`, plus streamable-http
  helpers.
- tests/unit/test_db_sql.py -> single `_db_flow` coroutine over ONE stdio session:
  connect -> execute_sql (incl. a DECIMAL/DATETIME serialization check) ->
  execute_sql_script (inline + file + denied) -> list_schemas -> creates one object of
  EVERY type in a throwaway schema (incl. a system-versioned table, a sequence, a
  trigger, an event, and an `orders` table with an FK to `items`) -> list_objects (all 7
  types + default + case-insensitivity + bad type + unknown schema) ->
  get_object_details (table both FK directions, view, function, procedure, sequence,
  trigger, event, plus not-found errors) -> DROP SCHEMA -> close.
- tests/unit/test_db_sessions.py -> the connection safeguards, driven IN-PROCESS with a
  `_StubSession` and a `_ToolRecorder` (a fake server whose `.tool(name=)` decorator just
  collects the tool functions, so they can be called directly, `ctx` positionally). Its
  `_context(address)` builds the `request_context.request.client.host` chain the HTTP
  transport supplies, and `http_transport`/`stdio_transport` fixtures flip
  `general.set_active_transport` and clear `_sessions`. No time is ever waited out —
  `connection.last_used -= SESSION_IDLE_TIMEOUT + 1` then `_close_idle_sessions()` directly.
- run_tests.py -> symlinks mcp_plugin + msm_plugin + mrs_plugin into a temp config home
  (`dot_mariadb_shell` under a `mcp_dot_mariadb_shell_*` temp dir), `pip install -r
  requirements.txt` (was an inline `pytest pytest-cov mcp` list — driving it off
  requirements.txt is what makes the `mcp < 3.0.0` pin actually bind), then runs pytest.
  Exits early if the install fails. `-k/--only` to filter, `-s/--shell`, `-u/--userhome`.
  .coveragerc omits msm/mrs/shell/site-pkgs.

## Next steps

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
   for: the 10-minute timeout is a constant, not a `mcp.startServer` option; the binding is
   to the raw peer address only, so a reverse proxy in front of the server would collapse
   every client onto one address (no `X-Forwarded-For` support — that would need an
   explicit trusted-proxy setting, never a blind header read); and the session is not
   additionally bound to the MCP session id. A cross-IP END-TO-END test also needs a
   loopback alias (`ifconfig lo0 alias 127.0.0.2`, root on macOS) or binding 0.0.0.0 and
   dialing the LAN IP.
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
  strings with the ids masked out and will fail if they drift apart.
- **The idle reaper is a module-global daemon thread started once** (`_idle_reaper`). The
  in-process test that calls `db.connect` really does start it, and it then lives for the
  rest of the pytest run, waking every 30s. That is harmless (it only ever touches
  `_sessions`, which the fixtures clear), but don't be surprised by it, and don't reset
  `_idle_reaper` to None in a fixture — that would start a second one.
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
  and skip themselves if it didn't. Run the full suite to validate.
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

- Branch: **`wip/AIPL-16`**, cut from `main` (which is at 8da59831 and tracks
  `mariadb`) and pushed to `mariadb/wip/AIPL-16`. Remote `mariadb` =
  mariadb-corporation/mariadb-shell-plugins; `origin` is mysql/mysql-shell-plugins and is
  NOT the push target. There is also a `local_office` remote (a NAS mirror) — not a push
  target either.
- THIS session is one commit on that branch: the HTTP-only connection safeguards
  (lib/general, lib/db_functions, lib/server, lib/msm_functions, README,
  tests/unit/test_db_sessions.py, tests/unit/test_transport_http.py).
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
