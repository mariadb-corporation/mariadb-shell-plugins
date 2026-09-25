# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. `mcp.setup`
additionally installs the MySQL-to-MariaDB migration tooling (AIPL-21), and
`sandbox.deploy` can be asked for a MariaDB server VERSION, downloading it if the machine
has none (see [`context/sandbox.md`](context/sandbox.md); that work merged as
PR #21). `sandbox.list_instances` lists the sandboxes in the default
sandbox path (all, or one by `port`), and a `--gui` server's `sandbox.deploy`
takes `mcp_access` - both for the VS Code extension's Sandboxes view. GPLv2,
"MariaDB plc". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`, shell **26.9.4** at the 2026-09-25
checkpoint — checked with `--version`; 26.9.3, 26.9.1 and 26.9.0 in earlier sessions. 26.9.3 is what the connection
URI now REQUIRES: it is the first shell whose parser accepts `mariadb://`, and
connections are stored with their scheme from here on — see
[`context/connections.md`](context/connections.md)), **MCP SDK 2.1.1** (2.0.0 before the
SDK-bump session — that jump is what broke CI, see the SDK-error gotcha in
[`context/environment.md`](context/environment.md)),
Python 3.14, pytest
9.1.1, uvicorn 0.52.1, httpx2 2.9.1, `mariadbd` at `/opt/homebrew/bin` (MariaDB 12.3.2).
Standard suite: **446 tests pass, 3 SKIPPED (~67s), 98% total coverage** (2231 statements,
49 missed; re-measured at the third 2026-09-25 checkpoint on a run with `.coverage`
DELETED first - ~65s since the run keeps its secrets in a plaintext file instead of the
macOS keychain, see the isolation gotcha in `context/testing.md` — see the coverage trap in [`context/testing.md`](context/testing.md)). Of the
three skipped, two are the OPT-IN end-to-end tests (`test_migration_e2e`,
`test_a_sandbox_really_runs_a_downloaded_server`) and the third is environmental:
`test_a_refusal_reaches_the_client_with_its_own_words` skips because the migration
tooling (v1.5.0) is not installed on this machine at the moment, so the migrator group
registers no tools. Before this session it was two skipped; with `--e2e` the run was
**355 pass** at the same coverage, since everything they touch is already covered
by the unit tests. Run it with
`mariadb-shell --py -f run_tests.py` FROM the mcp_plugin dir and with `/opt/homebrew/bin`
on PATH (mariadbd, mariadb-dump and pv are not on the default PATH).

## Layout

- Repo's existing `*_plugin` layout (NOT create-shell-plugin's `python/plugins/`).
  `@plugin` / `@plugin_function` decorators. FQNs camelCase (`mcp.startServer`) ->
  snake_case in Python, kebab-case in CLI. MCP tool names use dots per user request.

## Context files

The detail lives in [`context/`](context/), one file per area, so a task that
touches one of them does not have to read the rest. Each file keeps the
section headings this file used to carry — Architecture, Current state, Files
that matter, Gotchas, Next steps — so a cross-reference like "see Gotchas"
means that file's own. Keep the file the change belongs to up to date, and
this table with it.

| File | What is in it |
| --- | --- |
| [`context/environment.md`](context/environment.md) | The shell's bundled Python (the tree that actually runs), the MCP SDK version and its 2.x API, and the traps in both. |
| [`context/server.md`](context/server.md) | `mcp.startServer`: GUI mode, function groups, the two transports, the plugin's own uvicorn server, Host/Origin validation, the path guard and elicitation. |
| [`context/connections.md`](context/connections.md) | The two connection lists, URI resolution, `use_session`, and every session safeguard: client binding, idle timeout, hard TTL, caps, audit log, locking. |
| [`context/security-review.md`](context/security-review.md) | The numbered review behind most of that: S1..S8 and T1..T9, one entry each, with what was verified and which revert probe pinned it. |
| [`context/db-tools.md`](context/db-tools.md) | `db.execute_sql` / `execute_sql_script` including the per-statement script results, the three introspection tools and their SQL. |
| [`context/migrator.md`](context/migrator.md) | The migration tooling: the naming rule, the four `migrator.*` tools, the install, the Windows gate and the opt-in end-to-end test. |
| [`context/setup.md`](context/setup.md) | `mcp.setup`: the three interactive modules, the shell's own prompt types, and the command-line option surface. |
| [`context/sandbox.md`](context/sandbox.md) | `sandbox.deploy` on a requested server version: the search order, the pinned index, checksums and extraction. |
| [`context/testing.md`](context/testing.md) | The suite's contents, how to run it, what the coverage number means, and the fixture and harness traps. |
| [`context/siblings.md`](context/siblings.md) | `msm_plugin` and `mrs_plugin`: what was changed there, how to run their suites, and their own failures. |
| [`context/working-practices.md`](context/working-practices.md) | Keeping this context current, what auto mode may not do, and the Windows VM / codex quirks that look like plugin bugs. |
| [`context/history.md`](context/history.md) | The session-by-session record and the branch and commit history. Background, not current truth. |

## Git state

Checked at this checkpoint (2026-09-25, third of the day):

```
$ git -C mcp_plugin branch --show-current
wip/result-set-fixes-and-expansion

$ git -C mcp_plugin status --short   (mcp_plugin's own lines; the rest is code_ext)
 M .claude/context/db-tools.md
 M lib/db_functions.py
 M tests/unit/test_db_script.py
 M tests/unit/test_db_sql.py
?? tests/unit/test_db_paging.py
```

- **The branch is `wip/result-set-fixes-and-expansion`**, shared with
  [`code_ext`](../../code_ext), stacked on `wip/ext-sandbox-support` (PR #29) <-
  `wip/connection-folders` (PR #28). This session's work is committed as
  `daafbaf1` (this plugin) and `b58f02de` (code_ext), with the context checkpoint
  after them, and opened as a PR against `wip/ext-sandbox-support`. The status
  above is from before those commits.
- This session, both for the extension (see
  [`context/db-tools.md`](context/db-tools.md)):
  - **paging**: `db.execute_sql_script(limit=)` and `db.execute_sql(limit=, offset=)`.
    A SELECT that can take one gets `\nLIMIT limit+1 [OFFSET n]`; the extra row is
    dropped and `has_more_pages` (true/false, present only when the limit was
    applied) says whether it came back. A 1064 on the limited form re-runs it as
    written. The SELECT forms it skips (own LIMIT/FETCH/OFFSET, INTO, PROCEDURE,
    FOR UPDATE/SHARE, LOCK IN SHARE MODE) were verified against a real server;
  - **`column_types`** on every result set (`_column_type`: the shell's `Type` name,
    `BLOB` for `BYTES` + the BLOB flag). Measured: VECTOR is indistinguishable from
    VARBINARY in the shell's metadata.
- Earlier commits on this branch: `32f0c23f` (`additional_result_sets`),
  `77a072a4` (the test run kept off the developer's secret store).
- Suite at this checkpoint: **446 pass, 3 skipped, ~67s, 98% (2231 statements, 49
  missed)**, `.coverage` deleted first. The third skip is still the environmental
  one: the migration tooling is not installed on this machine.
- **Over the ~400-line split threshold and NOT split** (untouched this session, so
  not read for a seam): `context/connections.md` (458), `context/history.md` (466,
  an archive), `context/security-review.md` (402), `context/migrator.md` (401).
- [`context/history.md`](context/history.md) is deliberately NOT updated, as by
  every session since `wip/sandbox-binaries`; this section is the current record.
