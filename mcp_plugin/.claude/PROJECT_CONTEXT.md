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
Standard suite: **512 tests pass, 2 SKIPPED (~61s), 96% total coverage** (3211
statements, 128 missed) on `wip/mcp-multi-tenant` at the 2026-10-06 checkpoint, on MCP
SDK **2.3.0** (465 / 98% on `main` before multi-tenant mode; see the coverage trap in
[`context/testing.md`](context/testing.md)). The two skipped are the OPT-IN
end-to-end tests (`test_migration_e2e`, `test_a_sandbox_really_runs_a_downloaded_server`).
Run it with
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
| [`context/multi-tenant.md`](context/multi-tenant.md) | The plan (not built yet) for multi-tenant mode: its inputs and decisions, users in `users.json` keyed by UUID, one shell secret group per user, plain-text API keys, tenant isolation (phases 0 and 1). |
| [`context/oauth.md`](context/oauth.md) | Phase 2 of that plan (not built yet): the decisions, the shared OAuth groundwork (public URL, scopes, default role, verifier chain) and the Keycloak option. |
| [`context/oauth-builtin.md`](context/oauth-builtin.md) | Phase 2c/2d (not built yet): the built-in authorization server that logs users in against MariaDB, grants (90 days by default) and their login connections, and the Snowflake-style setup through Arcade. |
| [`context/history.md`](context/history.md) | The session-by-session record and the branch and commit history. Background, not current truth. |

## Git state

Checked at this checkpoint (2026-10-06):

```text
$ git -C mcp_plugin branch --show-current
wip/mcp-multi-tenant

$ git -C mcp_plugin status --short
(phase 1 of multi-tenant mode, committed and pushed on this branch)
```

- **Branch `wip/mcp-multi-tenant`** off `main` (`67253bae`): phase 1 of multi-tenant mode
  (API keys, users, secret groups, tenant isolation) is built. Phase 2 (OAuth2) is
  next. The plan and its status are in [`context/multi-tenant.md`](context/multi-tenant.md),
  [`context/oauth.md`](context/oauth.md) and
  [`context/oauth-builtin.md`](context/oauth-builtin.md).
- Rene's secret-groups design note was read and deleted (never committed). Its content
  is in [`context/environment.md`](context/environment.md) ("Shell secret groups").
- **Over the ~400-line split threshold and NOT split** (untouched, so not read for a
  seam): `context/connections.md` (499), `context/history.md` (466, an archive),
  `context/security-review.md` (402), `context/migrator.md` (401).
- [`context/history.md`](context/history.md) is deliberately NOT updated, as by
  every session since `wip/sandbox-binaries`; this section is the current record.
