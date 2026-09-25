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
Standard suite: **375 tests pass, 3 SKIPPED (~200s), 98% total coverage** (2115 statements,
45 missed; re-measured at the 2026-09-25 checkpoint on a run with `.coverage` DELETED
first — see the coverage trap in [`context/testing.md`](context/testing.md)). Of the
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

Checked at this checkpoint (2026-09-25):

```
$ git -C mcp_plugin branch --show-current
wip/connection-folders

$ git -C mcp_plugin status --short   (mcp_plugin's own lines; the rest is code_ext)
 M .claude/PROJECT_CONTEXT.md
 M .claude/context/sandbox.md
 M .claude/context/testing.md
 M lib/sandbox_functions.py
 M tests/unit/helpers.py
 M tests/unit/test_sandbox.py
```

- **The branch is `wip/connection-folders`** (it was `wip/code-ext` at the last
  checkpoint), shared with [`code_ext`](../../code_ext); HEAD `486f30ed`.
- **This session's work is UNCOMMITTED**, and lands with the extension's in one
  commit when the user asks:
  - `sandbox.list_instances(port=None)`: the instances in the DEFAULT sandbox path
    only (the user's decision), `[{port, version, status}]` by port; with `port`,
    that one instance or `[]`.
  - `sandbox.deploy(..., mcp_access=True)`, advertised in `--gui` mode ONLY
    (`_register_deploy` trims it from `__signature__` otherwise); False files the
    sandbox's connection in the `gui` list. `sandbox.delete` now removes the
    connection from both lists.
  - `tests/unit/helpers.py` gained `list_tools` (schemas as advertised).
  - All of it is in [`context/sandbox.md`](context/sandbox.md).
- Suite at this checkpoint: **375 pass, 3 skipped, ~200s, 98% (2115 statements,
  45 missed)**, `.coverage` deleted first. `lib/sandbox_functions.py` is 94%: the
  6 missed lines are the `sandbox.start` / `sandbox.kill` bodies, uncovered before
  this session too.
- Two leftover test sandboxes (ports 49715/49740 under `$TMPDIR/mcp_sandbox_*`,
  started 2026-09-20 by interrupted suite runs) were killed and removed at the
  user's request. An interrupted run leaves its shared sandbox RUNNING - worth
  checking `ps` for `mcp_sandbox_` after one.
- [`context/history.md`](context/history.md) is deliberately NOT updated, as by
  every session since `wip/sandbox-binaries`; this section is the current record.
