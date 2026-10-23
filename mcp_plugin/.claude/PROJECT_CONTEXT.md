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
(sibling to `msm_plugin`, `rest_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`, shell **26.9.4** at the 2026-09-25
checkpoint — checked with `--version`; 26.9.3, 26.9.1 and 26.9.0 in earlier sessions. 26.9.3 is what the connection
URI now REQUIRES: it is the first shell whose parser accepts `mariadb://`, and
connections are stored with their scheme from here on — see
[`context/connections.md`](context/connections.md)), **MCP SDK 2.1.1** (2.0.0 before the
SDK-bump session — that jump is what broke CI, see the SDK-error gotcha in
[`context/environment.md`](context/environment.md)),
Python 3.14, pytest
9.1.1, uvicorn 0.52.1, httpx2 2.9.1, `mariadbd` at `/opt/homebrew/bin` (MariaDB 12.3.2).
Standard suite: **937 pass, 6 skipped (~130s)** on `main` after #63 at the
2026-10-19 evening checkpoint (the 6 are opt-in: 5 `--e2e`, 1 `--keycloak`); 865 / 6 on PR #59's
tree earlier that day; 616 / 3 on PR #44's
tree at the 2026-10-11 checkpoint, against a local shell build of mariadb-shell #71 + #73; **595 tests pass, 3 SKIPPED (~78s), no warnings, 97% (5309 statements)**
on `wip/mcp-multi-tenant` at the 2026-10-07 checkpoint, with Rene's review fixes in the
working tree (584 / 4 after the simplification pass; 585 / 3 at the 2026-10-06 evening
checkpoint; 577 and 93%, 5200 statements, at the one before),
on MCP SDK **2.3.0** with **httpx2 2.13.1** (the local build's site-packages were cleaned
to match `build/bundled-python-deps`; see working-practices.md) (465 / 98% on `main` before multi-tenant mode; see the coverage trap in
[`context/testing.md`](context/testing.md)). The three skipped are OPT-IN: the
end-to-end tests (`--e2e`: `test_migration_e2e`,
`test_a_sandbox_really_runs_a_downloaded_server`) and the live Keycloak test
(`--keycloak`, see [`context/oauth.md`](context/oauth.md)).
Run it with
`mariadb-shell --py -f run_tests.py` FROM the mcp_plugin dir and with `/opt/homebrew/bin`
on PATH (mariadbd, mariadb-dump and pv are not on the default PATH).

## Layout

- Repo's existing `*_plugin` layout (NOT create-shell-plugin's `python/plugins/`).
  `@plugin` / `@plugin_function` decorators. FQNs camelCase (`mcp.startServer`) ->
  snake_case in Python, kebab-case in CLI. MCP tool names use dots per user request;
  `mcp setup --toolNameSeparator=_` publishes them with `_` for gateways that refuse dots
  (Arcade), see [`context/server.md`](context/server.md).
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
| [`context/source-includes.md`](context/source-includes.md) | MSM SOURCE directives in `db.execute_sql_script` and the `sql` tools: msm's shared `lib/source.py`, path resolution and allowed-path checks, line-break normalization, how statements and positions are placed back in the script or the included file. |
| [`context/migrator.md`](context/migrator.md) | The migration tooling: the naming rule, the four `migrator.*` tools, the install, the Windows gate and the opt-in end-to-end test. |
| [`context/setup.md`](context/setup.md) | `mcp.setup`: the three interactive modules, the shell's own prompt types, and the command-line option surface. |
| [`context/util.md`](context/util.md) | The `util` group: dumps, loads, copies, exports and imports as background tasks (`lib/tasks.py`), the 12 tools, the shell process each task runs in, how a crashed load is resumed, and what the extension does with the tasks. |
| [`context/restd.md`](context/restd.md) | The `restd` group: MariaDB REST Daemon deployments, how `mariadb-restd --setup` and the daemon are driven, the install, and the hooks that make a sandbox's daemon follow it. |
| [`context/sql.md`](context/sql.md) | The `sql` group: `sql.check`, `sql.outline`, `sql.format` over the `mariadb-lsp` binary, offered only where it is found; SOURCE includes checked before the binary reads them; positions made 1-based. |
| [`context/sandbox.md`](context/sandbox.md) | `sandbox.deploy` on a requested server version: the search order, the pinned index, checksums and extraction. |
| [`context/testing.md`](context/testing.md) | The suite's contents, how to run it, what the coverage number means, and the fixture and harness traps. |
| [`context/siblings.md`](context/siblings.md) | `msm_plugin` and `rest_plugin`: what was changed there, how to run their suites, and their own failures. |
| [`context/working-practices.md`](context/working-practices.md) | Keeping this context current, what auto mode may not do, and the Windows VM / codex quirks that look like plugin bugs. |
| [`context/multi-tenant.md`](context/multi-tenant.md) | Multi-tenant mode (BUILT, PR #37): the decisions, users in `users.json` keyed by UUID, one shell secret group per user, plain-text API keys, tenant isolation (phases 0 and 1, as built), Rene's review round, and the open next steps. |
| [`context/multi-tenant-phase1-plan.md`](context/multi-tenant-phase1-plan.md) | The phase 1 plan as written before building, sections 1a-1e (config layer, `mcp.setup`, server auth, tenant isolation, tests and docs). Background; the as-built notes in multi-tenant.md win. Split out of multi-tenant.md on 2026-10-07. |
| [`context/multi-tenant-inputs.md`](context/multi-tenant-inputs.md) | The external facts the multi-tenant and OAuth2 plans rest on: secret groups, the customer's Snowflake input, the MCP authorization spec 2026-07-28, Keycloak, the bundled Python, the SDK's auth hooks. Split out of multi-tenant.md on 2026-10-06. |
| [`context/oauth.md`](context/oauth.md) | Phase 2 (BUILT): the decisions, the shared OAuth groundwork (public URL, scopes, default role, verifier chain), the Keycloak option, `mcp setup-oauth` / `mcp setup-keycloak-realm`, and the as-built status including real-client tests. |
| [`context/oauth-builtin.md`](context/oauth-builtin.md) | Phase 2c/2d (BUILT): the built-in authorization server that logs users in against MariaDB, grants (90 days by default) and their login connections, and the Snowflake-style setup through Arcade. |
| [`context/security-review-multi-tenant.md`](context/security-review-multi-tenant.md) | The M review of multi-tenant mode and OAuth2: M1..M26 (M22: MCP 2026-07-28 has no sessions; M23: Arcade opens a session per call; M24..M26 and M17's rebinding: from Rene's review, fixed 2026-10-07), the threat, what is built, the test and its revert probe, and what is left open. |
| [`context/history.md`](context/history.md) | The session-by-session record and the branch and commit history. Background, not current truth. |

## Git state

Checked at this checkpoint (2026-10-23):

```text
$ git -C mcp_plugin branch --show-current
main

$ git -C mcp_plugin status --short   (one repository: every project's lines)
(clean)
```

- **Merged since 2026-10-19:** #65 (the `sql` tools find `mariadb-lsp` next to
  the running shell), #69 (`util.import_table` steering, the tool listing a
  quarter cheaper, see [`context/server.md`](context/server.md); its second
  commit keeps a parameter called `title` through the schema trim). #66 and
  #70 changed only the shared `LICENSE` copies. `wip/mcp-tool-listing-tokens`
  is deleted locally and on origin; no `wip/` branch or worktree is left.
- **Elsewhere, open (as of 2026-10-19, not rechecked):** mariadb-shell
  `wip/time-out-of-day-range` (`5f5883393`, pushed, NO PR yet - the user's
  call). lsp-server `wip/source-dir` (`8e98d91`): no longer needed by the
  `sql` tools.
- [`context/history.md`](context/history.md) is deliberately NOT updated,
  as by every session since `wip/sandbox-binaries`.
