# code_ext — Project Context

The **MariaDB** VS Code extension. Written in TypeScript, it talks to the
MariaDB Shell backend through the [`mcp_plugin`](../../mcp_plugin), which
exposes the backend functions over the Model Context Protocol (MCP).

This file is the map: what the project is, where the code lives, and which
context file covers which area. It is the one to read first, and the detail
is a link away. Keep it and the file the change belongs to up to date as the
extension grows.

## Layout

| Path | Purpose |
| --- | --- |
| `src/extension.ts` | Activation entry point. Wires everything together and registers the commands. |
| `src/shell/` | Finding, installing and launching the MariaDB Shell. |
| `src/mcp/` | The MCP client: the one-at-a-time server startup, session lifecycle, wire decoding, typed `db.*` and `sandbox.*` APIs. |
| `src/errorMessages.ts` | The error notification every failure uses, with its Show Log button. |
| `src/connections/` | Which connections are open on which URI and which is the default, what happens on each one, plus the connection editor: URI building, the store, the panel and its protocol. |
| `src/tree/` | The Connections view: its data model and its tree items; the Sandboxes view. |
| `src/sandboxes/` | The New Sandbox dialog: its fields, protocol and panel. |
| `src/sql/` | The statement scanner, statement splitting, single-table detection, the edit query builder and the execution service. |
| `src/editor/` | The SQL editor toolbar, status bar entry and run command. |
| `src/webview/` | The result view host, its message protocol and the edit-collection logic. |
| `webview/src/` | The three Preact frontends: the result view, the connection editor and New Sandbox. |
| `src/test/` | The extension-side test suite, mirroring the source layout. |
| `webview/test/` | The frontend test suite, run under jsdom. |
| `images/` | Icons: `light/` and `dark/` variants, the activity bar seal, and `marketplace-icon.png`. |

## Context files

The detail lives in [`context/`](context/), one file per area, so a task
that touches one of them does not have to read the rest. Keep the file the
change belongs to up to date, and this table with it.

| File | What is in it |
| --- | --- |
| [`context/toolchain.md`](context/toolchain.md) | The two Vite builds and two Vitest projects, the npm scripts, the pinned Node and the lockfile churn, the Marketplace icon, CI. |
| [`context/shell-and-mcp.md`](context/shell-and-mcp.md) | `src/shell/` and `src/mcp/` file by file, the lazy startup and shell lookup, GUI mode (`--gui`) and the two connection lists, the MCP wire format. |
| [`context/connection-editor.md`](context/connection-editor.md) | The connection editor: its files and tabs, what it deliberately leaves out, the URI box and its rules, its own vite build. |
| [`context/connections.md`](context/connections.md) | The several connections one URI can have open and the one the Connections view keeps, what is reported on them (General Actions), the cached connection list, the tree, folders, opening, the default connection. |
| [`context/running-sql.md`](context/running-sql.md) | The two run commands and stop on error, which connection a file runs on, the statement scanner, splitting agreeing with the server, what makes a result set editable, the gutter markers. |
| [`context/sandboxes.md`](context/sandboxes.md) | The Sandboxes view, its Start / Stop / Delete, the New Sandbox dialog, the per-call timeouts, and what it needs from the shell. |
| [`context/result-view.md`](context/result-view.md) | The panel webview: layout, per-connection state, the two pickers (and when General Actions is shown), fonts and surfaces, the result grids, the SQL preview, the shared code. |
| [`context/actions-grid.md`](context/actions-grid.md) | The actions grid: run rows and their statements, errors and popups, scrolling, the jump arrows, and what the server had to report for it. |
| [`context/commands.md`](context/commands.md) | Every contributed command and where it appears. |
| [`context/testing-and-debugging.md`](context/testing-and-debugging.md) | The interfaces everything external sits behind, and the F5 launch and watch task. |

## Known gaps

- `MINIMUM_SHELL_VERSION` is 26.9.4. 26.9.3 was the floor for the
  connection URI (the first parser to accept `mariadb://`, which the MCP
  plugin now stores); 26.9.4 is the first release whose bundled
  `mcp_plugin` carries `db.test_connection` and the per-statement
  `db.execute_sql_script` results (`statement_index`, `execution_time`,
  per-statement `error`, `stop_on_error`, `warnings`) - checked by
  installing it and diffing its plugin against this repo's. The readers
  of those fields still fall back when they are missing, but no shell
  the extension accepts lacks them any more.
- **Connection folders need a shell that is not released yet.** No shell
  the extension accepts bundles an `mcp_plugin` with folder support, and an
  older plugin SILENTLY ignores `path` / `new_path` (the MCP SDK drops
  arguments a tool does not declare) - a folder change "saves" and does
  nothing. Decided: no runtime detection; raise `MINIMUM_SHELL_VERSION` to
  the first release that includes it once that is out. To try folders
  before then, the shell in use has to load this repo's plugin, not its
  bundled one (the installed 26.9.4 loads
  `lib/mariadb-shell/plugins/mcp_plugin`, not `~/.mariadb-shell/plugins`).
- **The Sandboxes view needs an unreleased shell too.** It lists through
  `sandbox.list_instances`, which only this repo's `mcp_plugin` has; on
  any released shell the view shows "could not be listed". Same decision
  as folders: raise `MINIMUM_SHELL_VERSION` once a release carries it. See
  [`context/sandboxes.md`](context/sandboxes.md).
- The installer is run with `MARIADB_SHELL_TAG` pinned, so a release
  marked **prerelease** on GitHub installs fine (every 26.9.x is one);
  only an unpinned `install.sh` would skip it.
- The Connections view keeps a connection of its own per URI, so a URI
  being browsed and run on costs two of the server's
  `MAX_CONNECTIONS_PER_CLIENT` (16). Nine connections open at once is
  therefore the ceiling, and nothing closes the tree's one on its own -
  only Disconnect does, or the server's 12 hour lifetime.
- The connection editor has no file pickers: the SSL certificate paths, the
  SSH identity and config files and the socket are typed, where the MySQL
  Shell's editor offers a browse button for each.
- The SSH tab has no password or passphrase field, because a URI cannot
  carry one: the shell keeps `ssh-password` and
  `ssh-identity-file-password` out of `ssh_uri_query_attributes` on
  purpose. A tunnel needing one therefore cannot be configured here - use
  a key the agent has already unlocked.
- The result grid edits every value as text; there is no type-aware editor
  (date picker, NULL toggle, BLOB viewer) yet, and no cell context menu.
- There is no paging. The MySQL Shell's result view pages through a result
  set; `db.execute_sql_script` returns the whole thing at once, so there is
  nothing to page through and the next/previous buttons were left out.
- Tabulator measures the DOM to lay itself out, and jsdom reports every
  element as zero sized, so it never finishes building under test. The
  grid's mapping, formatters and cell callbacks are tested directly
  (`webview/test/ResultGrid.test.tsx`); its rendering is not.

## Git state

Checked at this checkpoint (2026-09-25, second of the day):

```
$ git -C code_ext branch --show-current
wip/result-set-fixes-and-expansion

$ git -C code_ext status --short   (one repository: mcp_plugin's lines too)
?? images/dark/maximize.svg
?? images/dark/minimize.svg
?? images/light/maximize.svg
?? images/light/minimize.svg
```

- **The branch is `wip/result-set-fixes-and-expansion`**, pushed and in sync.
  The branches stack, each on the one before:
  `wip/connection-update` <- `wip/connection-folders` (PR #28) <-
  `wip/ext-sandbox-support` (PR #29, based on `wip/connection-folders`) <-
  `wip/result-set-fixes-and-expansion` (no PR yet; one would target
  `wip/ext-sandbox-support`).
- Its commits past #29's `0842eed9` (the Sandboxes view and dialog, see
  [`context/sandboxes.md`](context/sandboxes.md)):
  - `32f0c23f` every result set of a CALL (a tab each, "N result sets" with a
    child row per set), a view read without an error row (looked up as a
    table, the not-found answer logged as INFO), the error bar's copy button,
    and a Copy menu on actions cells - see
    [`context/running-sql.md`](context/running-sql.md) and
    [`context/actions-grid.md`](context/actions-grid.md);
  - `77a072a4` mcp_plugin only: the test run kept off the developer's secret
    store.
- **The four untracked icons are NOT this session's** - they appeared in the
  working tree during it and are the user's; left out of every commit.
- Suite at this checkpoint: **1106 pass across 52 files**, `npm run pretest`
  (typecheck + eslint) and `npm run build` clean.
- NOT clicked through in a running VS Code: the Sandboxes view, the New
  Sandbox dialog and the actions Copy menu. The server paths were verified
  instead against the dev shell (`/Users/mzinner/git/mariadb-shell/build/bin`,
  loading this repo's plugin via `~/.mariadb-shell/plugins`) through the real
  SDK connector: sandbox list / deploy / stop / start / delete, `mcp_access`,
  a view read (one call, logged as info) and a two-set CALL.

## Conventions

- Four space indentation, double quotes, trailing semicolons, 80 columns.
- Arrow function consts for exported functions; JSDoc with `@param` and
  `@returns` on everything exported.
- Comments explain *why*, not *what*.
- Relative imports carry the `.js` extension.
