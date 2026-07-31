# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB/MySQL Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. GPLv2,
"MariaDB plc and/or its affiliates". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`), MCP SDK 1.28.1, Python 3.14, `mariadbd`
at `/opt/homebrew/bin` (MariaDB 12.3.2). Full suite: **17 tests pass (~28s), 94% total
coverage**. Run it with `python3 run_tests.py` FROM the mcp_plugin dir and with
`/opt/homebrew/bin` on PATH (mariadbd is not on the default PATH).

## Architecture / key decisions

- Repo's existing `*_plugin` layout (NOT create-shell-plugin's `python/plugins/`).
  `@plugin` / `@plugin_function` decorators. FQNs camelCase (`mcp.startServer`) ->
  snake_case in Python, kebab-case in CLI. MCP tool names use dots per user request.
- MCP server built with **FastMCP** (`mcp.server.fastmcp`, the bundled one — NOT the
  standalone `fastmcp` v2). `mcp`, `msm_plugin`, `mrs_plugin`, and the `sandbox` global
  are imported lazily so plugin load never hard-fails.
- Server runs **foreground on the main thread**; `start()` sets `useWizards=False`.
- Transports: `streamable-http` (default) and `stdio`. **stdio hardened** (`_serve_stdio`):
  real stdout (fd 1) dup'd for the transport, then fd 1 AND `sys.stdout` redirected to
  stderr so tool/shell/C output can't corrupt JSON-RPC. Uses low-level
  `mcp_server._mcp_server.run(...)`.
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
- **`db_functions.get_session(connection_id)`** is the PUBLIC accessor over the private
  `_get_session`, so other tool modules (msm) can resolve a `db.connect` session without
  reaching into another module's privates or keeping a second cache.
- **Connections**: shell secrets keyed `MCP:Connection:<uri>`. `db.connect` only allows
  configured URIs; opens via `parse_uri`+password -> `shell.open_session` (independent of
  the shell's global session). Sessions cached in-process in `_sessions`, keyed by UUID.
- **Allowed paths**: `settings.json` under `get_mcp_plugin_data_path()`.
  `config.is_path_allowed()` via `os.path.commonpath` (reads disk fresh each call — NO
  in-memory cache); empty list => deny all.
- **Path enforcement + elicitation** (commit 6e12ad68): shared guard
  `general.require_allowed_path(ctx, path)` (ASYNC; skips when arg is None), used by msm.*
  (target/file/schema_project) and sandbox.* (sandbox_dir). When a path is NOT allowed it
  MCP-elicits (`ctx.elicit`, schema=one-bool `ConfirmTrustPath`) asking the user to trust
  it; on accept+trust it `config.add_allowed_path()` (persists to settings.json,
  abspath+expanduser, dedup) and proceeds; on decline/cancel/elicit-failure it raises the
  "not allowed" mysqlsh.Error. Because elicit is async, ALL msm (12) + sandbox (7) tools are
  `async def` with a leading `ctx: Context` param (`from mcp.server.fastmcp import Context`,
  imported inside the registrar; FastMCP strips it from the client-facing schema).
  db.* tools stay SYNC — none of them elicit (`db.execute_sql_script` checks
  `config.is_path_allowed` directly and just errors out).
- **SQL exec**: `db.execute_sql` = single statement (+ optional `?` params, one result
  dict). `db.execute_sql_script` = multi-statement via `mysqlsh.mysql.split_script()`,
  returns a LIST; accepts `sql_script` XOR `file_path` (file must be an allowed path).
  `sandbox.deploy` port REQUIRED int on all 7; `ssl=False` default.
- **Introspection tools** (all UNCOMMITTED, all sync, all built on the user's own SQL —
  the queries came from the user verbatim, only parameterized; do NOT "improve" them
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
  `test_transport_http`.
- Coverage after latest run: lib/msm_functions 100, lib/db_functions 99, lib/server 98,
  lib/config 96, lib/general 95, lib/sandbox_functions 87, server.py 85, lib/setup 84,
  general.py 73. TOTAL 94%.
- **Sibling `msm_plugin` was changed in the same session** (own commit, own suite: 9 pass
  via `python3 run_tests.py -s <mariadb-shell>` with /opt/homebrew/bin on PATH):
  - MySQL -> MariaDB rebrand of all PROSE/branding. Legal notices were NOT word-substituted;
    the USER instead ADDED a second line `Copyright (c) 2026, MariaDB plc and/or its
    affiliates.` under the existing Oracle line, leaving Oracle's notice and the "authors of
    MySQL hereby grant" FOSS exception intact. **Follow that additive pattern**; do not
    rewrite Oracle's LICENSE or the exception paragraph.
  - `tests/conftest.py` now uses `from mysqlsh.globals import sandbox` +
    `sandbox.deploy/kill` instead of `mysqlsh.globals.dba.deploy_sandbox_instance` (this
    build of mariadb-shell has NO `dba` global), with `ssl: False` and an `int()` port.
  - `run_tests.py` prefers `mariadb-shell` over `mysqlsh`.
  - `lib/management.py deploy_schema` gained `backup: bool = False` (see Gotchas).

## Files that matter

- lib/general.py -> plugin data path + async `require_allowed_path`/`_confirm_trust_path`.
- lib/config.py -> connections (secrets) + allowed paths (settings.json) + `add_allowed_path`.
- lib/db_functions.py -> db.* tools; `_sessions` UUID cache; `_serialize_result`;
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
- run_tests.py -> symlinks mcp_plugin + msm_plugin + mrs_plugin into a temp config home,
  pip-installs pytest/pytest-cov/mcp, runs pytest. `-k/--only` to filter, `-s/--shell`,
  `-u/--userhome`. .coveragerc omits msm/mrs/shell/site-pkgs.

## Next steps

1. **`mysqlsh.globals.util.dump_schemas` / `load_dump` do NOT exist in this mariadb-shell
   build**, so `msm.deploy_schema` / `msm_plugin` `deploy_schema` with `backup=True` raise
   `AttributeError: unknown attribute: dump_schemas`. The backup feature is unusable (and
   untested) until the dump/restore is reimplemented — e.g. `mariadb-dump` as a subprocess.
   `backup=False` is the default precisely because of this.
2. **`mrs_plugin/lib/general.py:221` and `:231` call `deploy_schema`** and silently lost
   their rollback dump when `backup` defaulted to False. Add `backup=True` there if that
   behaviour should be preserved (sibling plugin, deliberately untouched).
3. (Optional, open questions raised with the user and NOT yet answered)
   - `object_type` is a plain `str` + `_normalize_object_type`, not `Literal[...]`; a
     Literal would publish the 7 values as a JSON-schema enum to clients but would reject
     `"Table"`.
   - the flag columns of `get_object_details` (`not_null`, `is_primary`, ...) and
     `to_many` inside `reference_mapping` stay 1/0 as the user's SQL produces them, not
     JSON booleans.
   - `interval_value`/`interval_field` of an event are raw, not composed into a readable
     schedule.
4. (Optional) Raise `lib/setup.py` (84%) and `general.py` (73%) coverage — now the two
   weakest modules by far.
5. (Optional) `test_db_sql.py`'s module docstring still says "connect / execute_sql /
   close"; the flow now covers far more.
6. (Optional) `gui/extension/package.json:1192` still labels the plugin "MySQL Schema
   Management" in the VS Code UI — outside msm_plugin, so left inconsistent by the rebrand.

## Gotchas / things not to repeat

- **`/checkpoint` needs its target folder** — invoked bare it must ask, but this session
  is non-interactive; target was inferred as `mcp_plugin` from the session's work.
- **New db.* tools stay SYNC** unless they need elicitation; don't convert them to async
  "for consistency" with msm/sandbox.
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
- **msm_plugin's tests need `-s <path to mariadb-shell>`** (its `run_tests.py` auto-detect
  only finds a shell on PATH) and the same homebrew PATH for `mariadbd`.
- **Filtering tests with `-k` breaks the db/msm/sandbox tests** — they depend on
  `test_sandbox_deploy` running first (conftest ordering hook + `sandbox.deployed` flag),
  and skip themselves if it didn't. Run the full suite to validate.
- **mcp 1.28.x runs SYNC tools directly on the event loop** (func_metadata:
  `else: return fn(...)`, no to_thread). So a sync-tool `anyio.from_thread.run` bridge for
  elicitation is IMPOSSIBLE (not in a worker thread). Async tools were the only viable
  route — and introduce NO new blocking since sync tools already blocked the loop.
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
- **This file goes stale fast** — the previous checkpoint listed 3 "next steps" that were
  all already committed. Re-check `git log` against it before trusting it.

## Git state

- Branch: `wip/AIPL-5`, upstream `mariadb/wip/AIPL-5` (remote `mariadb` =
  mariadb-corporation/mariadb-shell-plugins; `origin` is mysql/mysql-shell-plugins and is
  NOT the push target). Default branch `main`.
- Session history: 3482634a (db introspection tools) -> the msm_plugin MariaDB/sandbox/
  backup commit -> the mcp_plugin `msm.deploy_schema` + db-group-gate commit.
- Working tree clean as of this checkpoint; everything pushed.
