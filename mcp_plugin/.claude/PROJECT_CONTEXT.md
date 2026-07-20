# mcp_plugin — Project Context

## Project

`mcp_plugin` is a MariaDB/MySQL Shell plugin ("MariaDB MCP Server Plugin") that hosts
a Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell. Licensed
GPLv2, copyright "MariaDB plc and/or its affiliates". Top-level plugin folder in the
mysql-shell-plugins repo (sibling to `msm_plugin`, `mrs_plugin`, etc.).

## Architecture / key decisions

- Follows the repo's existing `*_plugin` layout (top-level folder: `__init__.py`,
  `init.py`, `general.py`, `server.py`, `lib/`, `package.json`, `requirements.txt`,
  `README.md`, `LICENSE`, `run_tests.py`, `pytest-coverage.ini`, `.coveragerc`,
  `python.env`), NOT the `create-shell-plugin` skill's upstream `python/plugins/` layout.
- `@plugin` / `@plugin_function` decorators from `mysqlsh.plugin_manager`.
- Plugin-function FQNs are camelCase (`mcp.startServer`) — shell auto-exposes snake_case
  in Python (`mcp.start_server`) and kebab-case in CLI (`mcp start-server`).
- MCP server built with **FastMCP** (`from mcp.server.fastmcp import FastMCP`), imported
  lazily; `mcp` + sibling `msm_plugin` imported lazily so plugin load never hard-fails.
- Server runs in the **foreground on the main thread**. `start()` sets
  `mysqlsh.globals.shell.options.useWizards = False` so wrapped `msm` functions run
  non-interactively (return values, don't prompt). Both transports go through one
  `mcp_server.run(transport=...)` call. (Replaced two earlier designs — see Gotchas.)
- Two transports via `transport` option: `streamable-http` (default, host/port) and
  `stdio`. Tools split into **function groups** loadable independently via
  `function_groups` option (`db`, `msm`); registry `_FUNCTION_GROUP_REGISTRARS` in
  lib/server.py maps group -> registrar callback.
- **Connections** stored as shell secrets keyed `MCP:Connection:<uri>` via
  `shell.store_secret`/`list_secrets`/`read_secret`/`delete_secret`. `db.connect` only
  allows configured URIs, reads password back, opens via `shell.parse_uri`+password ->
  `shell.open_session` (independent of global session).
- **Allowed paths** stored in `settings.json` under
  `lib.general.get_mcp_plugin_data_path()` (= `<shell user dir>/plugin_data/mcp_plugin`).
  `config.is_path_allowed()` uses `os.path.commonpath`; empty list => deny all.
- **`mcp.setup`** interactive command configures connections (verified via
  `open_session` before storing) and allowed paths; first-run guided, later runs a menu.
- **Path enforcement**: every `msm.*` tool with a path arg calls `_require_allowed_path`;
  when the path arg is None the check is SKIPPED (msm falls back to shell working dir).

## Current state

Implemented, byte-compiles clean (`python3 -m py_compile`). NOT yet run in a live shell.
- Shell functions: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer`.
- Server lifecycle with transport + function-group selection.
- `db.*` tools: list_connections, connect, execute_sql, close (results -> dict: columns,
  rows, affected_items_count, warnings_count; bytes->hex).
- 11 `msm.*` tools wrapping `../msm_plugin/management.py`, all path-guarded.
- `mcp.setup` full interactive flow.
- **Pytest suite** (tests/): test 1 stores 2 connections + reads them back over stdio
  server; test 2 creates an msm project via stdio server + verifies; fixtures clean up
  secrets/settings/temp dirs. `run_tests.py` symlinks mcp_plugin+msm_plugin into a config
  home and runs `mysqlsh --pym pytest`.

Open / unverified:
- Tests never executed here — see Gotchas for the likely-fragile bits (stdout cleanliness,
  secret store availability, `delete_secret` API).
- Allowed-path check skipped when path arg is None (potential bypass; by design for now).
- No enforcement wired into `db.*` (connections are the gate there, not paths).

## Files that matter

- `general.py` -> plugin fns `mcp.info`/`mcp.version`/`mcp.setup` (thin; call lib).
- `server.py` -> `mcp.startServer` plugin fn; reads host/port/transport/function_groups.
- `init.py` -> `@plugin class mcp`; imports general + server submodules.
- `lib/general.py` -> VERSION, host/port/transport/function-group constants,
  `get_mcp_plugin_data_path()`.
- `lib/server.py` -> `build_mcp_server()`, `start()`, `_FUNCTION_GROUP_REGISTRARS`.
- `lib/db_functions.py` -> `register_db_tools()` (`db.*`) + result serializer + `_sessions`.
- `lib/msm_functions.py` -> `register_msm_tools()` (11 `msm.*`) + `_require_allowed_path`.
- `lib/config.py` -> connection-secret helpers, settings.json, `is_path_allowed()`.
- `lib/setup.py` -> interactive `run_setup()` + prompt/menu helpers.
- `lib/__init__.py` -> imports submodules in dep order: general, config, db_functions,
  msm_functions, setup, server.
- `tests/conftest.py` -> fixtures: non_interactive_shell, stored_connections, allowed_temp_dir.
- `tests/unit/helpers.py` -> stdio MCP client harness (`call_tool`, `tool_payload`).
- `tests/unit/test_server.py` -> the 2 tests (importorskip "mcp").
- `run_tests.py` -> test runner (symlinks plugins, runs pytest via shell).
- `README.md` -> user docs (kept in sync).

## Next steps

1. Run the suite in a real shell: `cd mcp_plugin && python3 run_tests.py` (needs mysqlsh
   or mariadb-shell in PATH + `mcp` SDK installed via requirements.txt). Fix whatever the
   stdio round-trip surfaces (most likely #1 in Gotchas).
2. Verify `shell.delete_secret` exists; adjust `config.delete_connection` if the real API
   differs.
3. Decide whether None path args should be validated/rejected rather than skipped.
4. Load-test end-to-end in a real MCP client (streamable-http too), exercise `mcp.setup`.
5. Consider committing the folder (currently fully untracked).

## Gotchas / things not to repeat

- **MCP-over-stdio needs clean stdout** (JSON-RPC only). Tests launch
  `mysqlsh -- mcp start-server --transport=stdio` with `--quiet-start=2` to suppress the
  banner. If the CLI still writes anything to stdout, the handshake breaks — this is the
  most likely first-run failure. Don't add prints to the stdio serving path.
- **Secret delete API assumed**: user gave store/list/read secret only.
  `config.delete_connection()` uses `shell.delete_secret(key)` — VERIFY.
- **Don't reintroduce** the background-thread + SIGTERM-signal serving design NOR the
  interactive-session guard in `start()` — both were explicitly reverted by the user in
  favor of foreground + `useWizards=False`.
- `useWizards` left `False` after `start()` returns — fine for a dedicated server process.
- MCP tool names use dots (`db.connect`, `msm.create_project`) per user request; some MCP
  clients want `[a-zA-Z0-9_-]` only — left as-is intentionally.
- License header is plain GPLv2 + MariaDB attribution — do NOT copy the sibling plugins'
  Oracle/MySQL header with the OpenSSL FOSS exception.
- LICENSE file added manually by the user — don't regenerate.
- tests/ and tests/unit/ intentionally have NO `__init__.py` (mirrors msm_plugin; imported
  as `mcp_plugin.tests.unit.helpers` via namespace packages + PYTHONPATH=..).
- Storing connections in tests needs a working shell credential/secret store in the env.
- Repo default branch `main`; work on `wip/AIPL-5`.

## Git state

- Branch: `wip/AIPL-5` (default branch `main`).
- `git -C mcp_plugin status --short`: the entire `mcp_plugin/` folder is still
  **untracked** (`?? ./`) — nothing committed yet. Other repo-level changes exist outside
  this folder (workspace file, debug tooling, `.claude/`, `.vscode/`, `mysqlsh.log`).
