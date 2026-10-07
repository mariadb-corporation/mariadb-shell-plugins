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
Standard suite: **595 tests pass, 3 SKIPPED (~78s), no warnings, 97% (5309 statements)**
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
| [`context/migrator.md`](context/migrator.md) | The migration tooling: the naming rule, the four `migrator.*` tools, the install, the Windows gate and the opt-in end-to-end test. |
| [`context/setup.md`](context/setup.md) | `mcp.setup`: the three interactive modules, the shell's own prompt types, and the command-line option surface. |
| [`context/sandbox.md`](context/sandbox.md) | `sandbox.deploy` on a requested server version: the search order, the pinned index, checksums and extraction. |
| [`context/testing.md`](context/testing.md) | The suite's contents, how to run it, what the coverage number means, and the fixture and harness traps. |
| [`context/siblings.md`](context/siblings.md) | `msm_plugin` and `mrs_plugin`: what was changed there, how to run their suites, and their own failures. |
| [`context/working-practices.md`](context/working-practices.md) | Keeping this context current, what auto mode may not do, and the Windows VM / codex quirks that look like plugin bugs. |
| [`context/multi-tenant.md`](context/multi-tenant.md) | Multi-tenant mode (BUILT, PR #37): the decisions, users in `users.json` keyed by UUID, one shell secret group per user, plain-text API keys, tenant isolation (phases 0 and 1, as built), Rene's review round, and the open next steps. |
| [`context/multi-tenant-phase1-plan.md`](context/multi-tenant-phase1-plan.md) | The phase 1 plan as written before building, sections 1a-1e (config layer, `mcp.setup`, server auth, tenant isolation, tests and docs). Background; the as-built notes in multi-tenant.md win. Split out of multi-tenant.md on 2026-10-07. |
| [`context/multi-tenant-inputs.md`](context/multi-tenant-inputs.md) | The external facts the multi-tenant and OAuth2 plans rest on: secret groups, the customer's Snowflake input, the MCP authorization spec 2026-07-28, Keycloak, the bundled Python, the SDK's auth hooks. Split out of multi-tenant.md on 2026-10-06. |
| [`context/oauth.md`](context/oauth.md) | Phase 2 (BUILT): the decisions, the shared OAuth groundwork (public URL, scopes, default role, verifier chain), the Keycloak option, `mcp setup-oauth` / `mcp setup-keycloak-realm`, and the as-built status including real-client tests. |
| [`context/oauth-builtin.md`](context/oauth-builtin.md) | Phase 2c/2d (BUILT): the built-in authorization server that logs users in against MariaDB, grants (90 days by default) and their login connections, and the Snowflake-style setup through Arcade. |
| [`context/security-review-multi-tenant.md`](context/security-review-multi-tenant.md) | The M review of multi-tenant mode and OAuth2: M1..M26 (M22: MCP 2026-07-28 has no sessions; M23: Arcade opens a session per call; M24..M26 and M17's rebinding: from Rene's review, fixed 2026-10-07), the threat, what is built, the test and its revert probe, and what is left open. |
| [`context/history.md`](context/history.md) | The session-by-session record and the branch and commit history. Background, not current truth. |

## Git state

Checked at this checkpoint (2026-10-07):

```text
$ git -C mcp_plugin branch --show-current
wip/mcp-multi-tenant

$ git -C mcp_plugin status --short
(clean after fc1cf5dd; then only this index and multi-tenant.md, committed next)
```

- **Rene's 11 PR #37 comments are fixed, pushed and answered** (2026-10-07):
  `fe4dbfe6` (the fixes, both `/code-review` rounds and the `/authorize` limit) and
  `fc1cf5dd` (context); each thread has a reply naming the commit and the test. What
  they are: "Review round" in [`context/multi-tenant.md`](context/multi-tenant.md) and
  M24..M26 in
  [`context/security-review-multi-tenant.md`](context/security-review-multi-tenant.md).
- **Branch `wip/mcp-multi-tenant`** off `main` (`67253bae`), pushed, **PR #37** to
  `main`, reviewed once by Rene (2026-10-06 21:33, "COMMENTED", on `28f39f8e`). CI green
  at `850626ee`; the run for `fc1cf5dd` was not checked. The PR description ends with a "Simplification pass over the new code"
  section. Commits:
  - `d1bf2a31` phase 1
  - `f388ea7b` phase 2
  - `512029e8` the live Keycloak test
  - `5d137af0` `setup-oauth`, `setup-keycloak-realm`, the clients lock, the M review
  - `b79866d1` the sessionless binding
  - `1bf93817` signing-key overlap, the sessionless refusal, the checkboxes
  - `c81bbd98` the HTTPS option names
  - `850626ee` `httpx2` instead of `httpx` (the CI failure)
  - `0ec99ee9`, `8f38d498` the sign-in page in the MRS style, with the MariaDB seal
  - `046d9321` a checkpoint
  - `72a64f89` Arcade: `--toolNameSeparator`, connections bound to user + grant (M23)
  - `abc58808` the 2026-10-06 evening checkpoint
  - `176aee35` the simplification pass (see
    [`context/multi-tenant.md`](context/multi-tenant.md) "Next steps" for the renamed
    helpers)
  - `28f39f8e` its checkpoint (made in another session; this session fast-forwarded to
    it, see the fetch gotcha in working-practices.md)
  - `fe4dbfe6` the review fixes, `fc1cf5dd` their context
- **Docs:** the MCP reference docs in `../mariadb-shell/docs-ref/` are committed and
  pushed on `wip/docs-ref` (`5ae870fa5`, `42ea44ca4`), mariadb-shell PR #59.
- **Left on the machine:** `cloudflared` (Homebrew). The user's Arcade dashboard still
  lists a `mariadb` server with a dead URL.
- What is open is in the Next steps of
  [`context/multi-tenant.md`](context/multi-tenant.md).
- **Split at this checkpoint:** `context/multi-tenant.md` (440 -> 299) lost the phase 1
  plan 1a-1e, verbatim, to
  [`context/multi-tenant-phase1-plan.md`](context/multi-tenant-phase1-plan.md) (153).
- **Over the ~400-line split threshold and NOT split:** `context/connections.md` (507,
  not touched this session), `context/history.md` (466, an archive),
  `context/security-review.md` (402), `context/migrator.md` (401).
- [`context/history.md`](context/history.md) is deliberately NOT updated, as by
  every session since `wip/sandbox-binaries`; this section is the current record.
