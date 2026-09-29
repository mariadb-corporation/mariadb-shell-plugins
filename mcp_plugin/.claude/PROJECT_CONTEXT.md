# mcp_plugin — Project Context

## Project

`mcp_plugin` ("MariaDB MCP Server Plugin") is a MariaDB Shell plugin that hosts a
Model Context Protocol (MCP) server exposing MariaDB AI Plugin capabilities to
MCP-compatible clients. It registers the global `mcp` object in the shell and serves
`db.*`, `msm.*`, and `sandbox.*` tool groups over stdio or streamable-http. `mcp.setup`
additionally installs the MySQL-to-MariaDB migration tooling (AIPL-21), and
`sandbox.deploy` can be asked for a MariaDB server VERSION, downloading it if the machine
has none (see [`context/sandbox.md`](context/sandbox.md); that work merged as
PR #21). GPLv2,
"MariaDB plc". Top-level plugin folder in mysql-shell-plugins
(sibling to `msm_plugin`, `mrs_plugin`, etc.). Verified against a real `mariadb-shell`
(`/Users/mzinner/git/mariadb-shell/build/bin`, shell **26.9.3** — checked with
`--version`; 26.9.1 and 26.9.0 in earlier sessions. 26.9.3 is what the connection
URI now REQUIRES: it is the first shell whose parser accepts `mariadb://`, and
connections are stored with their scheme from here on — see
[`context/connections.md`](context/connections.md)), **MCP SDK 2.1.1** (2.0.0 before the
SDK-bump session — that jump is what broke CI, see the SDK-error gotcha in
[`context/environment.md`](context/environment.md)),
Python 3.14, pytest
9.1.1, uvicorn 0.52.1, httpx2 2.9.1, `mariadbd` at `/opt/homebrew/bin` (MariaDB 12.3.2).
Standard suite: **374 tests pass, 2 SKIPPED (~190s), 98% total coverage** (2132 statements,
46 missed; measured on a run with `.coverage` DELETED first — see the coverage trap in
[`context/testing.md`](context/testing.md)). These figures are from the session that
last measured them, not from this checkpoint. The two skipped are the OPT-IN
end-to-end tests: with `--e2e` the run is
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

Checked at this checkpoint (2026-09-28):

```
$ git branch --show-current
wip/connection-folders
```

- **The branch is `wip/connection-folders`**, PR #28 ("Connection folders,
  and reading the connection list once"). It builds on PR #27
  (`wip/connection-update`) and targets that branch, so its diff shows only
  the folder work; it moves to `main` once #27 is merged. Both this plugin
  and [`code_ext`](../../code_ext) change on it, one commit across the two
  per change. `wip/code-ext` is the older shared branch these grew out of.
- **`486f30ed`** is the folder work itself: a folder is part of the secret
  key (`MCP:CONN:/Sandboxes/note_app:<uri>`), GUI `db.list_connections`
  returns `{uri, path, kind}`, `kind="all"`, `sandbox.deploy` files under
  `/Sandboxes`. See [`context/connections.md`](context/connections.md).
- **The commit after it answers the PR #28 review** (ReneRamirez: the
  Windows credential manager caps a key at 256 bytes):
  `check_connection_key_length` refuses a key over
  `MAX_CONNECTION_KEY_BYTES` on every platform before anything is written,
  and the prefixes were shortened to `MCP:CONN:` / `GUI:CONN:`, with
  `upgrade_connection_keys()` moving legacy `MCP:Connection:` /
  `GUI:Connection:` keys once per process. No downgrade path, by decision.
  Both in [`context/connections.md`](context/connections.md). The reviewer
  was asked to look again.
- **The commit after that moves the details out of the key** (2026-09-28):
  folder, caption and color live in `connections.json` next to
  `settings.json`, keyed by list and stored URI; the key is `<prefix><uri>`
  again, so only the URI counts against the 256 bytes, a `:` is allowed in a
  folder name, and re-filing never touches the secret store. GUI
  `db.list_connections` entries gained `caption` and `color`;
  `db.add_connection` takes `caption`/`color`, `db.update_connection`
  `new_caption`/`new_color`. Non-GUI listing is still bare URIs. Keys in the
  folder-in-key format are migrated by `upgrade_connection_keys`. Suite:
  **380 pass, 3 skipped, 98%**. See
  [`context/connections.md`](context/connections.md).
- **Suite at this checkpoint**, shell 26.9.3, `.coverage` deleted first:
  **374 pass, 2 skipped, ~190s, 98% (2132 statements, 46 missed)**. The four
  uncovered lines in `lib/config.py` are still the unparse-failure and
  Windows-drive branches.
- [`context/history.md`](context/history.md) is deliberately NOT updated. Its
  "Current state" is the `wip/sandbox-binaries` era and has been left as an
  archive by every session since; this section is the current record.
