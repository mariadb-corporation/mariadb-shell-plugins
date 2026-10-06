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
Standard suite: **464 tests pass, 3 SKIPPED (~65s), 98% total coverage** (2381 statements,
51 missed, on `wip/mysqlsh-error-usage-fix` at the 2026-09-29 checkpoint; 447 / 2235 / 48
at the 2026-09-28 one before the rebase; re-measured then on a run with `.coverage`
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
- **Exceptions: tool code raises `ToolError` through `tool_registrar.tool_error()`, never
  `mysqlsh.Error`, and never imports `ToolError` at module scope.** `mysqlsh.Error` is
  for shell plugin code only (`server.py`, `setup*.py`, and `config.py`, which
  `mcp.setup` shares). The full rule and the reasons are in
  [`context/server.md`](context/server.md) (Architecture); reviewers have flagged it.

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

Checked at this checkpoint (2026-10-06):

```
$ git -C mcp_plugin branch --show-current
main

$ git -C mcp_plugin status --short
(clean)
```

- **On `main`, nothing open for mcp_plugin.** PRs #28, #29, #30 (`8f392c81`) and
  #31 (`4dd56d3b`, the `ToolError` rule) are all merged. Since then `main` gained
  `caef7471` (update_version derives the release versions and sandbox index),
  `f35a21be` (version 26.9.5), `e891a44d` (MRS/MSM use the shell's dump and load
  utilities) and #36 (`4bdf0ece`, MSM/MRS fixes), all of which touch mcp_plugin
  files. Their effect on mcp_plugin was NOT checked in the 2026-10-06 session, and
  the suite has not been re-run since the 2026-09-29 figure above (464 pass /
  3 skipped / 98%, measured on #31).
- **Over the ~400-line split threshold and NOT split** (untouched, so not read for
  a seam): `context/connections.md` (499), `context/history.md` (466, an archive),
  `context/security-review.md` (402), `context/migrator.md` (401).
- [`context/history.md`](context/history.md) is deliberately NOT updated, as by
  every session since `wip/sandbox-binaries`; this section is the current record.
