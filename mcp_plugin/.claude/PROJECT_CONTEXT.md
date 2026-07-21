# mcp_plugin — Project Context

## Project

`mcp_plugin` is a MariaDB/MySQL Shell plugin ("MariaDB MCP Server Plugin") that hosts
a Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell. Licensed
GPLv2, copyright "MariaDB plc and/or its affiliates". Top-level plugin folder in the
mysql-shell-plugins repo (sibling to `msm_plugin`, `mrs_plugin`, etc.).

Verified against a real `mariadb-shell` (build at `/Users/mzinner/git/mariadb-shell/
build/bin`), MCP SDK 1.28.1, Python 3.14, `mariadbd` at `/opt/homebrew/bin`. Full
suite: **11 tests pass (~14s), 85% coverage**.

## Architecture / key decisions

- Repo's existing `*_plugin` layout (NOT the create-shell-plugin skill's upstream
  `python/plugins/` layout). `@plugin` / `@plugin_function` decorators.
- Plugin-function FQNs camelCase (`mcp.startServer`) -> shell exposes snake_case in
  Python, kebab-case in CLI.
- MCP server built with **FastMCP**; `mcp`, `msm_plugin`, and the `sandbox` global are
  imported lazily so plugin load never hard-fails.
- Server runs **foreground on the main thread**; `start()` sets
  `shell.options.useWizards = False` so wrapped functions run non-interactively.
- Transports (`transport` option): `streamable-http` (default) and `stdio`.
  **stdio is hardened** (`_serve_stdio`): the real stdout (fd 1) is dup'd for the
  transport, then fd 1 AND `sys.stdout` are redirected to stderr so tool/shell/C-level
  output can't corrupt JSON-RPC. Uses low-level `mcp_server._mcp_server.run(...)`.
- **Function groups** (`function_groups` option): `db`, `msm`, `sandbox`.
  `_FUNCTION_GROUP_REGISTRARS` maps group -> registrar.
- **Connections**: shell secrets keyed `MCP:Connection:<uri>`. `db.connect` only allows
  configured URIs; reads password back, opens via `parse_uri`+password -> open_session.
- **Allowed paths**: `settings.json` under `lib.general.get_mcp_plugin_data_path()`.
  `config.is_path_allowed()` via `os.path.commonpath`; empty list => deny all.
- **Path enforcement** (`_require_allowed_path`, skips when arg is None): msm.* on
  target/file/schema_project paths; sandbox.* on sandbox_dir (all 7).
- **`mcp.setup`** interactive: configure connections (verified via open_session before
  storing) + allowed paths; first-run guided, later runs a menu.
- **Sandbox tools**: `port` REQUIRED int on all 7 (shell rejects non-int in port pos).
  `deploy` defaults `ssl=False` (avoids openssl dependency); pass ssl=True for TLS.
- **SQL exec**: `db.execute_sql` = single statement (+ optional `?` params, one result
  dict). `db.execute_sql_script` = multi-statement via `mysqlsh.mysql.split_script()`
  (run_sql itself rejects multi-statement, Error 1064), returns a LIST of result dicts.

## Current state

Implemented, compiles, all 11 tests pass live (85% coverage).
- Shell functions: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer`.
- Tools: `db.*` (list_connections, connect, execute_sql, execute_sql_script, close),
  `msm.*` (11, path-guarded), `sandbox.*` (7, sandbox_dir-guarded, port required).
- Test files (all under tests/unit/, no __init__):
  - test_sandbox.py: test_sandbox_deploy (ordered FIRST) + test_sandbox_shutdown
    (ordered LAST) + path-rejection.
  - test_config.py: stdio db.list_connections; direct lib/config.py tests
    (allowed paths + is_path_allowed, connection secrets); lib/setup.py flow tests
    (first-run, menu add/delete, non-interactive guard) via a scripted fake shell.
  - test_msm.py: msm.create_project over stdio.
  - test_db_sql.py: db connect/execute_sql/execute_sql_script/close end-to-end against
    the shared sandbox.
- Coverage: config 96, db_functions 95, general(lib) 100, msm_functions 61,
  sandbox_functions 84, server(lib) 90, setup 84, server.py 85, general.py 73.

Remaining coverage gaps (intentional/low value): msm_functions 61% (only create_project
exercised), setup.py 84% (cancel/invalid-input/empty branches).

## Test infrastructure (important)

- **Ordering is native** via `pytest_collection_modifyitems` in conftest.py (moves
  test_sandbox_deploy first, test_sandbox_shutdown last; everything else between).
  Do NOT use pytest-order / pytest-dependency — pytest-dependency FAILS TO BUILD in this
  env and aborted the whole pip install.
- **Shared sandbox**: session-scoped `sandbox` fixture (conftest) provides
  port/dir/uri/password/instance_dir + a `deployed` flag; registers the allowed path and
  stores the connection secret; safety-net stop/delete on teardown. Deploy is done by
  test_sandbox_deploy (sets `deployed=True`); db + shutdown tests `pytest.skip` when
  `not sandbox.deployed` (native dependency).
- **Subprocess coverage**: tools run in the `mariadb-shell -- mcp start-server` stdio
  SUBPROCESS, so coverage needs a bootstrap: `tests/_cov/sitecustomize.py` calls
  `coverage.process_startup()`; run_tests puts it on the subprocess PYTHONPATH and sets
  `MCP_COVERAGE_RC`; helpers sets `COVERAGE_PROCESS_START` on the SUBPROCESS ONLY (keeps
  the pytest process' pytest-cov clean). `.coveragerc` has `parallel/sigterm/
  relative_files=True` and a `[paths]` alias mapping `*/plugins/mcp_plugin/` (the symlink
  path the subprocess loads through) back to source; omits msm_plugin/mariadb-shell/
  site-packages/tests.
- **Persistent MCP session**: db tools are stateful across calls (session cached in the
  server process), so multi-step db flows use `helpers.mcp_session` (one subprocess),
  NOT repeated `helpers.call_tool` (fresh subprocess each time).
- `helpers.call_tool(...,timeout=)`, `tool_payload` (aggregates multi content-blocks ->
  list), `find_free_port`, `server_binary_available`, `mysqlsh_binary` (prefers
  mariadb-shell).
- Fixtures in conftest: non_interactive_shell (autouse), stored_connections (backup/
  clear/restore), allowed_temp_dir, clean_config (backup/restore connections+settings),
  sandbox (session).
- Runner: `mariadb-shell --log-level=debug3 --verbose=4 --py -f run_tests.py`
  (symlinks mcp_plugin+msm_plugin into a config home, pip-installs pytest/pytest-cov/mcp,
  runs pytest). run_tests only installs those 3 packages.

## Files that matter

- general.py / server.py / init.py -> plugin fns + registration.
- lib/general.py, lib/server.py, lib/db_functions.py, lib/msm_functions.py,
  lib/sandbox_functions.py, lib/config.py, lib/setup.py -> as described above.
- lib/__init__.py import order: general, config, db_functions, msm_functions,
  sandbox_functions, setup, server.
- tests/conftest.py, tests/unit/{test_config,test_msm,test_db_sql,test_sandbox}.py,
  tests/unit/helpers.py, tests/_cov/sitecustomize.py.
- run_tests.py, .coveragerc, pytest-coverage.ini, .gitignore.

## Next steps

1. (Optional) Raise msm_functions/setup coverage with more scripted variants.
2. (Optional) streamable-http transport test.
3. Commit: untracked lib/sandbox_functions.py, tests/_cov/, tests/unit/test_config.py,
   test_db_sql.py, test_msm.py; deleted tests/unit/test_server.py; plus modified files.

## Gotchas / things not to repeat

- **Fake-shell for setup tests must be ONE shared instance** (`fake=_FakeShell(answers);
  lambda: fake`). `lambda: _FakeShell(answers)` re-creates it each `_shell()` call, so
  every call re-pops the first answer -> infinite "Add a connection?" loop -> HANG. This
  bit once (test froze; had to pkill the stray mariadb-shell).
- **Do NOT delete `.coverage*` with a glob** — `.coverage*` also matches `.coveragerc`
  (config file). Use `.coverage.*` (dot) for parallel data files.
- **pytest-dependency does not build in this env** — abandoned; ordering/deps are native
  (collection hook + `sandbox.deployed` flag).
- **stdio needs clean stdout** — `_serve_stdio` handles it; don't add prints to the stdio
  path or revert to plain `mcp_server.run(transport="stdio")`.
- **run_sql rejects multi-statement input** (1064) — split via `mysqlsh.mysql.split_script`.
- **Sandbox port required** — never None/omit (shell: "Argument #1 is expected to be an
  integer"). No Optional port, no `_invoke` shim.
- **Probing stdio via `mariadb-shell -f script.py` fails** (fake stderr, fileno); works
  under `--pym pytest`. Use pytest to drive the MCP client.
- **Long test runs**: run via Bash `run_in_background: true` (tracked) so a real hang is
  visible and stoppable (TaskStop / pkill), rather than freezing the session.
- FastMCP emits one content block per list element -> tool_payload aggregates;
  structuredContent not populated in this SDK.
- Don't reintroduce bg-thread+SIGTERM serving nor the interactive guard in `start()`.
- MCP tool names use dots per user request. Plain GPLv2+MariaDB header (not Oracle/
  OpenSSL). LICENSE added manually. tests/ has no __init__ (namespace pkg + PYTHONPATH=..).

## Git state

- Branch: `wip/AIPL-5` (default `main`). `mcp_plugin/` tracked.
- `git -C mcp_plugin status --short`:
  - Modified: .claude/PROJECT_CONTEXT.md, .coveragerc, .gitignore, README.md, cspell.json,
    lib/__init__.py, lib/db_functions.py, lib/general.py, lib/server.py, run_tests.py,
    tests/conftest.py, tests/unit/helpers.py.
  - Deleted: tests/unit/test_server.py.
  - Untracked: lib/sandbox_functions.py, tests/_cov/, tests/unit/test_config.py,
    tests/unit/test_db_sql.py, tests/unit/test_msm.py, tests/unit/test_sandbox.py.
