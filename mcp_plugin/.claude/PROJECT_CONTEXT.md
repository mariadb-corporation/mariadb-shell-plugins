# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB/MySQL Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. GPLv2,
"MariaDB plc and/or its affiliates". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`), MCP SDK 1.28.1, Python 3.14, `mariadbd`
at `/opt/homebrew/bin`. Full suite: **14 tests pass (~26s with REST test), 86% coverage**.

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
- **Connections**: shell secrets keyed `MCP:Connection:<uri>`. `db.connect` only allows
  configured URIs; opens via `parse_uri`+password -> `shell.open_session` (independent of
  the shell's global session).
- **Allowed paths**: `settings.json` under `get_mcp_plugin_data_path()`.
  `config.is_path_allowed()` via `os.path.commonpath` (reads disk fresh each call — NO
  in-memory cache); empty list => deny all.
- **Path enforcement + elicitation** (committed 6e12ad68): shared guard
  `general.require_allowed_path(ctx, path)` (ASYNC; skips when arg is None), used by msm.*
  (target/file/schema_project) and sandbox.* (sandbox_dir). When a path is NOT allowed it
  MCP-elicits (`ctx.elicit`, schema=one-bool `ConfirmTrustPath`) asking the user to trust
  it; on accept+trust it `config.add_allowed_path()` (persists to settings.json,
  abspath+expanduser, dedup) and proceeds; on decline/cancel/elicit-failure it raises the
  "not allowed" mysqlsh.Error. Because elicit is async, ALL msm (11) + sandbox (7) tools are
  `async def` with a leading `ctx: Context` param (`from mcp.server.fastmcp import Context`,
  imported inside the registrar; FastMCP strips it from the client-facing schema).
- **SQL exec**: `db.execute_sql` = single statement (+ optional `?` params, one result
  dict). `db.execute_sql_script` = multi-statement via `mysqlsh.mysql.split_script()`,
  returns a LIST. `sandbox.deploy` port REQUIRED int on all 7; `ssl=False` default.
- **REST SQL** (uncommitted): `db.execute_sql` can run MRS REST SQL (e.g.
  `CONFIGURE REST METADATA`) because `mrs_plugin` registers a shell SQL handler
  (`@sql_handler("MRS", prefixes=...)`, prefixes incl. "CONFIGURE REST ") that intercepts
  `session.run_sql`. Requires mrs_plugin loaded in the server subprocess (now symlinked by
  run_tests). antlr4 (MRS parser dep) is bundled in mariadb-shell.

## Current state

- Elicitation refactor: DONE and committed (6e12ad68). All lib/* + msm/sandbox tests in.
- REST SQL test: DONE, working, UNCOMMITTED. 14 tests pass, 86% coverage.
- Shell fns: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer`.
- Tools: db.* (list_connections, connect, execute_sql, execute_sql_script, close),
  msm.* (11, path-guarded, async), sandbox.* (7, sandbox_dir-guarded, async, port required).
- Tests (tests/unit/, no __init__): test_sandbox (deploy FIRST, shutdown LAST + path-reject),
  test_config, test_msm (create_project + elicit-accept + elicit-decline), test_db_sql,
  test_rest_sql (CONFIGURE REST METADATA).
- Coverage: config 96, db_functions 94, general(lib) 95, msm_functions 61,
  sandbox_functions 87, server(lib) 90, setup 84, server.py 85, general.py 73.

## Files that matter

- lib/general.py -> plugin data path + async `require_allowed_path`/`_confirm_trust_path`.
- lib/config.py -> connections (secrets) + allowed paths (settings.json) + `add_allowed_path`.
- lib/db_functions.py -> db.* tools; session cache keyed by UUID; `_serialize_result`.
- lib/msm_functions.py, lib/sandbox_functions.py -> async tools w/ `ctx: Context`.
- lib/server.py -> build/serve; `_serve_stdio` hardening.
- tests/conftest.py -> ordering hook, fixtures (sandbox session, allowed_temp_dir,
  clean_config, stored_connections, non_interactive_shell).
- tests/unit/helpers.py -> `call_tool` (now has `elicitation_callback`), `mcp_session`,
  `tool_payload`, `find_free_port`, `server_binary_available`, `mysqlsh_binary`.
- tests/unit/test_rest_sql.py -> REST SQL end-to-end (NEW, uncommitted).
- run_tests.py -> symlinks mcp_plugin + msm_plugin + mrs_plugin into a temp config home,
  pip-installs pytest/pytest-cov/mcp, runs pytest. .coveragerc omits msm/mrs/shell/site-pkgs.

## Next steps

1. Commit the REST SQL work: `run_tests.py`, `.coveragerc`, `tests/unit/test_rest_sql.py`.
2. Land the sibling `mrs_plugin/lib/general.py` fix SEPARATELY (see Gotchas) — it is NOT
   part of mcp_plugin and test_rest_sql depends on it.
3. (Optional) Raise msm_functions/setup coverage; streamable-http transport test.

## Gotchas / things not to repeat

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
  `USE x` etc. don't disturb the caller. `mrs_plugin/lib/general.py:203-207` chose that
  session: original `if "shell.Object" in str(type(session))` -> no-arg
  `shell.open_session()` = duplicate the GLOBAL session (carries password). The MCP db layer
  uses `shell.open_session(connection_data)` and sets NO global session -> original path
  raised "An open session is required when duplicating sessions". A native shell session has
  NO `.connection_options` attr (GUI-only) — only `get_uri()` (no password), so the
  `open_session(session.connection_options)` branch fails with "unknown attribute:
  connection_options" and URI-reopen can't carry credentials. USER applied an alternative
  fix in mrs_plugin/lib/general.py that makes it pass; that file is a SIBLING plugin, land
  it separately. (Alternative never taken: `shell.set_session(session)` in db.connect.)
- **test client is plain `ClientSession(read,write)`** — happy-path tests pre-register their
  paths (via fixtures) so no elicitation fires.
- **tool_payload returns None for an empty list** (zero content blocks). Guard `or []`
  before `in`/iteration (bit test_sandbox_shutdown).
- **Don't delete `.coverage*` with a glob** — matches `.coveragerc`. Use `.coverage.*`.
- **pytest-dependency does not build in this env** — ordering is native (conftest hook +
  `sandbox.deployed` flag). Long runs: Bash `run_in_background: true` so a hang is stoppable.
- **run_sql rejects multi-statement** (1064) — split via `mysqlsh.mysql.split_script`.
- **Sandbox port required** — never None/omit ("Argument #1 is expected to be an integer").
- **stdio needs clean stdout** — don't add prints to the stdio path.
- Don't reintroduce bg-thread+SIGTERM serving nor the interactive guard in `start()`.
  Plain GPLv2+MariaDB header. tests/ has no __init__ (namespace pkg + PYTHONPATH=..).

## Git state

- Branch: `wip/AIPL-5` (default `main`). Last commit 6e12ad68 (elicitation).
- `git -C mcp_plugin status --short`:
  - Modified: .coveragerc, run_tests.py, ../mrs_plugin/lib/general.py (SIBLING).
  - Untracked: tests/unit/test_rest_sql.py.
