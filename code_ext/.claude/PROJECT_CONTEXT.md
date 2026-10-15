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
| `src/errorMessages.ts` | The error notification every failure uses, with its Show Log button, and `reportError` (log it, then notify). |
| `src/text.ts` | `errorText`, `counted` and `stripAnsi`: text helpers shared with the webviews, so free of `vscode`. |
| `src/connections/` | Which connections are open on which URI and which is the default, what happens on each one, what each one's `sql_mode` says about double quotes (`sqlModes.ts`), plus the connection editor: URI building, the store, the panel and its protocol. |
| `src/tree/` | The Connections view: its data model and its tree items; the Sandboxes view; `viewState.ts`, the welcome-content state machine both share. |
| `src/mrs/` | The MariaDB REST Service: REST SQL builders, the data mapping model, the dialogs' fields, the API, the dialog panel and the commands, and what goes to the Actions tab (`mrsActions.ts`, `mrsActivity.ts`) (see [`context/mrs.md`](context/mrs.md)). |
| `src/util/` | Dump and Load: the operations' fields, the dialog, the commands, the task monitor, the Tasks view, a task's Actions row (`taskActions.ts`) and its log file (`taskLogs.ts`) (see [`context/dump-and-load.md`](context/dump-and-load.md)). |
| `src/sandboxes/` | The New Sandbox dialog: its fields, protocol and panel. |
| `src/rest/` | The MariaDB REST Daemon: a sandbox deployed with the REST Service, its daemon following it, the REST Daemons view's commands, and `ActionGroup`, one Actions row with a row per step (see [`context/rest.md`](context/rest.md)). |
| `src/modeler/` | The schema diagram viewer's extension side: the `msm_schema_diagrams` rows (`mwb-schema/1`, `mwb-diagram/1`) and their queries, the Diagram session's service, the page model (A4 sheets the diagram is printed on), and the panel (see [`context/modeler.md`](context/modeler.md)). |
| `src/sql/` | The statement scanner, statement splitting, single-table detection, the edit query builder and the execution service. |
| `src/editor/` | The SQL editor toolbar, status bar entry and run command; `sqlLanguage.ts`, which documents are SQL; `ansiQuotes.ts`, the semantic tokens that mark `"..."` as an identifier where the connection has ANSI_QUOTES. |
| `syntaxes/` | The MariaDB SQL TextMate grammar: the hand-written template, the generated `words.json`, and the built grammar (see [`context/sql-grammar.md`](context/sql-grammar.md)). |
| `tools/grammar.mts` | `grammar:sync` reads the word lists out of the mariadb-server and mariadb-shell checkouts; `grammar:build` makes the grammar from the template. |
| `language-configuration.json` | Comments, brackets and auto-closing pairs of the MariaDB SQL language. |
| `src/webview/` | The result view host, its message protocol and the edit-collection logic; a maximized result set's editor tab (`maximizedResult.ts`); grid values as files - saved / loaded (`valueFiles.ts`) and opened in editors (`valueDocuments.ts`); what every webview shares - the HTML, CSP and options (`html.ts`), the hold-until-`ready` queue (`readyQueue.ts`), the dialogs' common messages (`dialogProtocol.ts`). |
| `webview/src/` | The Preact frontends: the result view, the connection editor, New Sandbox, the Dump and Load dialog, the MRS dialogs and the schema model viewer. |
| `webview/src/canvas/` | The three.js canvas: 2D / 3D views, paper and grid, selection, moves, undo, the Navigator - generic, showing a figure set (see [`context/modeler.md`](context/modeler.md)). |
| `webview/src/modeler/` | The EER diagram as a figure set of the canvas (tables, layers, connections), and the model viewer. |
| `src/test/` | The extension-side test suite, mirroring the source layout; `grammar/` tokenizes with VS Code's own TextMate engine (word coverage, token classes, corpus snapshots). |
| `webview/test/` | The frontend test suite, run under jsdom. |
| `ui-test/` | The ExTester UI tests: the runner and its suites (`core`, `mrs`, `dump-load`, each with a sandbox of its own), the helpers, the test files (see [`context/testing-and-debugging.md`](context/testing-and-debugging.md)). |
| `images/` | Icons: `light/` and `dark/` variants, the activity bar icon `mariadb-code-ext.svg`, and `marketplace-icon.png`. |

## Context files

The detail lives in [`context/`](context/), one file per area, so a task
that touches one of them does not have to read the rest. Keep the file the
change belongs to up to date, and this table with it.

| File | What is in it |
| --- | --- |
| [`context/toolchain.md`](context/toolchain.md) | The two Vite builds and two Vitest projects, the npm scripts, the pinned Node and the lockfile churn, the Marketplace icon, CI. |
| [`context/shell-and-mcp.md`](context/shell-and-mcp.md) | `src/shell/` and `src/mcp/` file by file, the lazy startup and shell lookup, GUI mode (`--gui`) and the two connection lists, the MCP wire format. |
| [`context/connection-editor.md`](context/connection-editor.md) | The connection editor: its files and tabs, what it deliberately leaves out, the URI box and its rules, its own vite build. |
| [`context/connections.md`](context/connections.md) | The several connections one URI can have open and the one the Connections view keeps, what is reported on them (General Actions; browsing only on failure or with `logAllCalls`), the cached connection list, the tree, opening, the default connection. |
| [`context/connection-folders.md`](context/connection-folders.md) | Folders, captions and colors of connections: what the server keeps, the helpers mirroring its rules, New Folder, moving, collapsed folders kept across a restart. Split out of connections.md on 2026-10-11. |
| [`context/running-sql.md`](context/running-sql.md) | The two run commands and stop on error, which connection a file runs on, the MariaDB SQL language and its ANSI_QUOTES highlight, the statement scanner, splitting agreeing with the server, what makes a result set editable, the gutter markers. |
| [`context/sql-grammar.md`](context/sql-grammar.md) | The MariaDB SQL TextMate grammar: one language, ANSI_QUOTES as semantic tokens (and why not two languages), what a tokenizer can and cannot do and the policy for each, the generated word lists and the sync workflow, the three test layers. |
| [`context/dump-and-load.md`](context/dump-and-load.md) | The Dump and Load submenu, its dialog, the background tasks (`util.*` tools) with their notification and the Tasks view, each task as a row of the Actions tab, and a log file per task. |
| [`context/rest.md`](context/rest.md) | The MariaDB REST Daemon: New Sandbox's REST Service option and its steps, Set Up MariaDB REST Daemon, the REST Daemons view, the sub-rows and the log button. |
| [`context/sandboxes.md`](context/sandboxes.md) | The Sandboxes view, its Start / Stop / Delete, the New Sandbox dialog, the per-call timeouts, and what it needs from the shell. |
| [`context/result-view.md`](context/result-view.md) | The panel webview: layout, per-connection state, the two pickers (and when General Actions is shown), fonts and surfaces, the SQL preview, the shared code. |
| [`context/result-grid.md`](context/result-grid.md) | The result grid: Tabulator and its traps, value display (hex, data icons, NULL), the cell menu and Delete Row, freezing the primary key, saving / loading a BLOB, opening any value in a VS Code editor. |
| [`context/result-set.md`](context/result-set.md) | One result set: its toolbar (the MySQL Shell's layout and icons), paging, maximizing into an editor tab. |
| [`context/actions-grid.md`](context/actions-grid.md) | The actions grid: run rows and their statements, errors and popups, scrolling, the jump arrows, and what the server had to report for it. |
| [`context/commands.md`](context/commands.md) | Every contributed command and where it appears. |
| [`context/mrs.md`](context/mrs.md) | The MariaDB REST Service tree and dialogs: REST SQL FORMAT=JSON reads, the `mrs.*` tools, several metadata schemas, the files, what was left out of the MySQL Shell's support, the opt-in end-to-end test, and every MRS action in the Actions tab. |
| [`context/modeler.md`](context/modeler.md) | The schema model viewer (preview, `wip/canvas`): the split between the generic canvas and figure sets, the 2D / 3D views and orbiting, tables and layers (moves, elevation), the Navigator, the Workbench figure design and toolbar, in-place editing of names, the A4 page model, life-size 100%, the table catalog interface, why cards are canvas textures, the shortcuts and what is still missing. |
| [`context/testing-and-debugging.md`](context/testing-and-debugging.md) | The interfaces everything external sits behind, the UI tests (ExTester) and their traps, and the F5 launch and watch task. |

## Known gaps

- `MINIMUM_SHELL_VERSION` is **26.10.1**, set by the release tooling
  (`tools/update_version`, the shell repo's `main` version); the hard
  floor is 26.9.4 (`mariadb://` URIs from 26.9.3, `db.test_connection` and
  per-statement script results from 26.9.4). 26.10.0 is the first release
  whose bundled `mcp_plugin` has connection folders / captions / colors,
  `sandbox.list_instances` and `mcp_access`, paging (`has_more_pages`) and
  `column_metadata` - checked 2026-10-11 by unpacking the 26.10.0 and
  26.10.1 tarballs and grepping their plugin - so those are no longer
  gaps. **What 26.10.1 still lacks:** the MRS module (mariadb-shell #71)
  with the `mrs` MCP group: the REST Service tree needs a shell newer than
  any release. Raise the version once one carries it. Dump and Load works
  with any of them: since 2026-10-12 the server runs each task in a shell
  process of its own and no longer needs #73.
- The result grid edits every value as text; there is no type-aware editor
  (date picker, BLOB viewer) yet. Its cell menu has Save / Load Value and
  Set Field to Null only - none of the MySQL Shell's Copy items.
- A VECTOR is recognised only from the table's own column type (the
  server reports it as `BYTES`, like VARBINARY), so outside an editable
  single-table result it shows as hex.
- Tabulator measures the DOM to lay itself out, and jsdom reports every
  element as zero sized, so it never finishes building under test. The
  grid's mapping, formatters and cell callbacks are tested directly
  (`webview/test/ResultGrid.test.tsx`); its rendering is not.
- The webview CSP allows inline STYLES (`style-src 'unsafe-inline'`,
  `src/webview/html.ts`). Harmless now that every grid cell and header
  tooltip is built as an element (see [`context/result-grid.md`](context/result-grid.md)),
  and left as the 2026-10-07 security review's one defence-in-depth
  suggestion: dropping it needs a check that the bundled CSS does not rely
  on it.
- **CI cannot test what needs the unreleased shell.** The plugins CI uses
  the shell tarball someone put in `~/.cache/ci` on the GCE runner (the
  26.10.1 release), which does not have mariadb-shell #71 (MRS module):
  #41 and #44 fail there for that reason alone (checked 2026-10-11). #43
  failed for #73 too, until its tasks moved to a shell process of their
  own (2026-10-12). The UI tests are not in CI at
  all. Options weighed, none done: wait for a shell release; let a PR name
  the shell build it needs and download it (shell PR CI would have to
  upload its Linux package; `rocky-linux.yml` can build one on dispatch);
  skip tests whose feature the shell lacks.
- The UI tests need a shell with #71, which mariadb-shell `main` has since
  2026-10-13: any local build of `main` in `~/git/mariadb-shell`
  (`MARIADB_SHELL=...`). Before that it took the local-only merge
  `wip/ui_test_shell`, deleted on 2026-10-13.
- **Highlighting is lexical.** Non-reserved keywords used as names are
  coloured as keywords, data types have no scope of their own, and the
  ANSI_QUOTES identifiers are semantic tokens, so a theme with semantic
  highlighting off shows them as strings and they appear a moment after
  the grammar's colours. See [`context/sql-grammar.md`](context/sql-grammar.md).

## Git state

Checked at this checkpoint (2026-10-15):

```text
$ git -C code_ext branch --show-current
wip/canvas

$ git -C code_ext status --short
(clean before this checkpoint's own edits)
```

- **`wip/canvas`** (from `main` at `411b22be`, pushed, **no PR yet**):
  `39c889e1` "code_ext: schema diagram viewer on a three.js canvas" -
  the generic canvas (`webview/src/canvas/`), the EER figure set
  (`webview/src/modeler/`), the Diagrams folder in the Connections view
  and the "Diagram" session. See [`context/modeler.md`](context/modeler.md).
  This checkpoint's context edits are not committed.
- `main` has #44, #45, #50, #51 and #52 merged since the last checkpoint;
  the only other open PR is **#48** (`wip/vscode-copilot`).
- Suite: **2186 unit tests pass**, 8 skipped; type check, lint and build
  clean (2026-10-15). The UI tests were not run this session.

## Conventions

- Four space indentation, double quotes, trailing semicolons, 80 columns.
- Arrow function consts for exported functions; JSDoc with `@param` and
  `@returns` on everything exported.
- Comments explain *why*, not *what*.
- Relative imports carry the `.js` extension.
