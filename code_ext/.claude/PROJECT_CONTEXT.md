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
| `src/mcp/` | The MCP client: the one-at-a-time server startup, session lifecycle, wire decoding, typed `db.*` API. |
| `src/errorMessages.ts` | The error notification every failure uses, with its Show Log button. |
| `src/connections/` | Which connections are open on which URI and which is the default, what happens on each one, plus the connection editor: URI building, the store, the panel and its protocol. |
| `src/tree/` | The Connections view: its data model and its tree items. |
| `src/sql/` | The statement scanner, statement splitting, single-table detection, the edit query builder and the execution service. |
| `src/editor/` | The SQL editor toolbar, status bar entry and run command. |
| `src/webview/` | The result view host, its message protocol and the edit-collection logic. |
| `webview/src/` | The two Preact frontends: the result view and the connection editor. |
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
| [`context/connections.md`](context/connections.md) | The connection editor and what it deliberately leaves out, the several connections one URI can have open and the one the Connections view keeps, the tree, the default connection. |
| [`context/running-sql.md`](context/running-sql.md) | The two run commands and stop on error, which connection a file runs on, the statement scanner, splitting agreeing with the server, what makes a result set editable, the gutter markers. |
| [`context/result-view.md`](context/result-view.md) | The panel webview: layout, per-connection state, the two pickers, the actions grid and what the server had to report for it, fonts and surfaces, the result grids, the SQL preview, the shared code. |
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
- **Connection folders, captions and colors need a shell that is not
  released yet.** No shell the extension accepts bundles an `mcp_plugin`
  with them, and an older plugin SILENTLY ignores `path` / `caption` /
  `color` and their `new_` forms (the MCP SDK drops arguments a tool does
  not declare) - such a change "saves" and does nothing. Decided: no runtime detection; raise `MINIMUM_SHELL_VERSION` to
  the first release that includes it once that is out. To try folders
  before then, the shell in use has to load this repo's plugin, not its
  bundled one (the installed 26.9.4 loads
  `lib/mariadb-shell/plugins/mcp_plugin`, not `~/.mariadb-shell/plugins`).
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

Checked at this checkpoint (2026-09-28):

```
$ git branch --show-current
wip/connection-folders
```

- **The branch is `wip/connection-folders`**, PR #28, shared with
  [`mcp_plugin`](../../mcp_plugin): a change needing both lands as one commit
  across the two. It targets PR #27's `wip/connection-update` and moves to
  `main` once #27 is merged.
- **`486f30ed`** added folders to the Connections view (drag and drop,
  Rename / New / Remove Folder, New Folder with Selection, the editor's
  Folder field), the cached connection list and General Actions logging.
- **The commit after it answers the PR #28 review**: a stored key is at most
  256 bytes, so folder + `:` + URI get 247 (the prefixes are now 9 bytes).
  The editor flags it live and refuses to save; `fileConnections` checks a
  whole drop / rename / New Folder with Selection before moving anything;
  Rename Folder validates the subtree as the name is typed. See
  [`context/connections.md`](context/connections.md), "The stored key is at
  most 256 bytes".
- **The commit after that** (2026-09-28) follows the plugin moving folder,
  caption and color out of the key into `connections.json`: only the URI
  counts against the 247 bytes now, so the drop / rename / New Folder
  length checks and the `:` rule are gone (`filingProblem`, `folderProblem`,
  `renameProblem` removed; `connectionKeyProblem(uri)` moved to
  `connectionDetails.ts`). A connection has a **Caption** (the row's label)
  and a **Color** (the row's label color and a `●` badge, via a file
  decoration). `addConnection` / `updateConnection` take an
  `IConnectionDetails` object instead of a trailing path.
- Suite at this checkpoint: **961 pass across 45 files**, `npm run pretest`
  (typecheck + eslint) and `npm run build` clean. NOT clicked through in a
  running VS Code.

## Conventions

- Four space indentation, double quotes, trailing semicolons, 80 columns.
- Arrow function consts for exported functions; JSDoc with `@param` and
  `@returns` on everything exported.
- Comments explain *why*, not *what*.
- Relative imports carry the `.js` extension.
