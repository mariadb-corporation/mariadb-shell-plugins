# code_ext — Project Context

The **MariaDB** VS Code extension. Written in TypeScript, it talks to the
MariaDB Shell backend through the [`mcp_plugin`](../../mcp_plugin), which
exposes the backend functions over the Model Context Protocol (MCP).

This file is the running description of the project. Keep it up to date as
the extension grows.

## Layout

| Path | Purpose |
| --- | --- |
| `src/extension.ts` | Activation entry point. Wires everything together and registers the commands. |
| `src/shell/` | Finding, installing and launching the MariaDB Shell. |
| `src/mcp/` | The MCP client: session lifecycle, wire decoding, typed `db.*` API. |
| `src/connections/` | Which connections are open, and which one is the default. |
| `src/tree/` | The Connections view: its data model and its tree items. |
| `src/sql/` | The statement scanner, statement splitting, single-table detection, the edit query builder and the execution service. |
| `src/editor/` | The SQL editor toolbar, status bar entry and run command. |
| `src/webview/` | The result view host, its message protocol and the edit-collection logic. |
| `webview/src/` | The Preact frontend rendered inside the result view. |
| `src/test/` | The extension-side test suite, mirroring the source layout. |
| `webview/test/` | The frontend test suite, run under jsdom. |
| `images/` | Icons: `light/` and `dark/` variants, plus the activity bar seal. |

### `src/shell/`

| File | Purpose |
| --- | --- |
| `constants.ts` | Minimum shell version, MCP server arguments, installer URLs. |
| `version.ts` | Version parsing and comparison, including the `mariadb-shell --version` line. |
| `locator.ts` | Finds a usable shell on the PATH or among the managed installations. |
| `installer.ts` | Builds and runs the platform installer, reporting its progress. |
| `bootstrap.ts` | `ensureShell()` — locate, else install, else fail. |
| `mcpServer.ts` | Builds the command that makes a shell host the MCP server. |
| `lineReader.ts` | Reassembles whole lines from chunked child process output. |
| `nodeRuntime.ts` | The real `child_process` / `fs` implementations of the interfaces above. |
| `vscodeProgress.ts` | The `vscode.window.withProgress` implementation of `ProgressHost`. |

### `src/mcp/`

| File | Purpose |
| --- | --- |
| `types.ts` | The `db.*` result shapes and the `IMariaDbApi` interface. |
| `protocol.ts` | Decodes MCP tool results into the values the Python tools returned. |
| `mariaDbApi.ts` | The `db.*` tools as typed calls. |
| `session.ts` | `McpSession` — starts the server once and hands out the API. |
| `sdkConnector.ts` | The real connector, on `@modelcontextprotocol/sdk`'s stdio transport. |

## Toolchain

- **Build**: [Vite](https://vite.dev), twice. `vite.config.ts` bundles
  `src/extension.ts` into a single CommonJS `dist/extension.js` for the
  extension host, with `vscode` and the Node built-ins external.
  `vite.webview.config.ts` builds the Preact frontend into
  `dist/webview/main.js` and `main.css` for the browser. The extension
  build has `emptyOutDir: false` on purpose: it runs again on every change
  in watch mode, and emptying `dist/` would delete the panel's assets out
  from under a running extension host. `npm run clean` wipes `dist/`, and
  `npm run build` runs it first.
- **Tests**: [Vitest](https://vitest.dev), in two projects, because the
  two halves run in different places. The **extension** project runs
  `src/test/` in plain Node with the `vscode` module aliased onto
  `src/test/mocks/vscode.ts`, so no extension host is needed. The
  **webview** project runs `webview/test/` under jsdom with the Preact
  plugin. Either can be run alone with `--project extension` or
  `--project webview`.
- **Type checking**: `tsc --noEmit` against two configs — `tsconfig.json`
  for the extension (Node) and `tsconfig.webview.json` for the frontend
  (DOM + Preact JSX).
- **Lint**: ESLint with `typescript-eslint`, over `src` and `webview`.

| Script | What it does |
| --- | --- |
| `npm run build` | Clean `dist/`, then bundle the extension and the webview. |
| `npm run clean` | Remove `dist/`. |
| `npm run watch` | Rebuild the extension on change. |
| `npm run watch:webview` | Rebuild the webview on change. |
| `npm test` | Run both Vitest projects once. |
| `npm run coverage` | Run the suite with a V8 coverage report. |
| `npm run typecheck` | Type check both configs. |
| `npm run lint` | Lint `src` and `webview`. |
| `npm run pretest` | `typecheck` + `lint`. |

## Startup behaviour

The extension activates on `onStartupFinished` and on `onLanguage:sql`, but
starts **nothing** at activation: the MCP server comes up the first time
something actually needs it, so an editor that never touches MariaDB never
pays for a shell process. `ConnectionManager.api()` is the single place
that decides to start it, and `ensureShell()` in `src/shell/bootstrap.ts`
drives the shell lookup:

1. **PATH** — run `mariadb-shell --version` (`mariadb-shell.exe` on
   Windows) and accept it if it reports at least `MINIMUM_SHELL_VERSION`
   (currently **26.9.2**).
2. **Local installation** — look under the prefix the installer uses,
   `~/.local/share/mariadb-shell/<version>` on macOS and Linux and
   `%LOCALAPPDATA%\Programs\mariadb-shell\<version>` on Windows, honouring
   `MARIADB_SHELL_PREFIX`. Version directories are tried newest first; the
   binary at `<version>/bin/mariadb-shell` is probed, and what it reports
   wins over what the directory is called.
3. **Install** — if neither has a new enough shell, run the official
   installer for the platform (curl-into-bash, or PowerShell on Windows)
   with `MARIADB_SHELL_TAG` pinned. It runs inside
   `vscode.window.withProgress()` at `ProgressLocation.Notification`; both
   installer scripts prefix their progress with `==> `, so `Downloading`,
   `Verifying checksum` and `Unpacking into ...` become the notification's
   message as they arrive.

`McpSession` then starts
`<shell> -- mcp start-server --transport=stdio --gui` through the MCP SDK's
stdio transport, which owns the process. Its stdin and stdout carry the
protocol; stderr goes to the **MariaDB** output channel. Concurrent callers
share one start, so the tree, the toolbar and the panel cannot each spawn a
server.

## GUI mode (`--gui`)

`--gui` tells the MCP server its client is this extension rather than an
autonomous agent, and that changes two things about the server:

- **Every local path is accessible.** Without it the server keeps an
  allowed-path list (`mcp.setup`) and asks, by MCP elicitation, before
  touching anything outside it. The extension names paths the user just
  picked in VS Code's own dialogs, so there is nothing left to confirm.
- **The connection list is writable**, through `db.add_connection` and
  `db.delete_connection`, which a server serves in this mode only. There
  are then **two** lists, told apart by a `kind`:

  | kind | who owns it | secret prefix |
  | --- | --- | --- |
  | `mcp` (the default) | `mcp.setup`; every MCP client can open these | `MCP:Connection:` |
  | `gui` | this extension, via the two tools above | `GUI:Connection:` |

  `db.list_connections` reports **one kind per call**, so the extension
  asks twice to see both and always knows which list an entry is in — which
  it needs, since a connection is deleted from the list it is in. The same
  server may be in both under different credentials; `db.connect` then
  opens the `gui` one.

`MCP_SERVER_ARGS` in `src/shell/constants.ts` is where the flag is passed.
A shell whose MCP plugin predates it **ignores it rather than failing** (the
plugin function takes its options as a dictionary and reads only the ones it
knows), so such a server simply comes up without GUI mode.
`MariaDbApi.listConnections`/`addConnection`/`deleteConnection` leave `kind`
and `verify` out of the call entirely when they were not given, for the same
reason.

## The MCP wire format

The server is a FastMCP server, so a tool's return value is rendered into
the `content` array rather than into one JSON document. `src/mcp/protocol.ts`
decodes all four shapes, which were read off the running server:

| Tool returns | `content` |
| --- | --- |
| a list | one text item per element (JSON for dicts, bare text for strings) |
| a dict | one text item holding its JSON |
| a scalar | one text item, plus `structuredContent.result` |
| an error | `isError: true`, the message as text |

## Connections view

Contributed into its own activity bar container, whose icon is the MariaDB
seal (`images/mariadb-seal.svg`, kept as a vector). The tree is:

```
dba@localhost:3310            connection (seal icon; "default" if default)
└── world                     schema
    ├── Tables                object group, one per supported type
    │   └── city              object
    ├── Views / Functions / Procedures / Sequences / Triggers / Events
```

- The shape of the tree lives in `src/tree/connectionsModel.ts`, which is
  pure data and has no VS Code import; `connectionsTreeProvider.ts` only
  maps its nodes onto items.
- A connection that is **not open has no children**: expanding a node must
  not open a database connection the user did not ask for.
- A schema lists all seven object folders without querying anything; the
  query happens when a folder is opened.
- Icons are the MySQL Shell extension's, copied into `images/light` and
  `images/dark`. The upstream set has no sequence icon, so sequences fall
  back to the `symbol-numeric` codicon. Upstream's `light/schemaPrcoedure.svg`
  typo was corrected to `schemaProcedure.svg` on copy.
- A connection item's `contextValue` is
  `mariadbConnection.<connected|disconnected>.<default|notDefault>`, which
  is what the context menu switches its entries on.

## Default connection

Stored in the `mariadb.defaultConnection` setting — in the workspace where
there is one, so a project can default to its own database, and in the user
settings otherwise. Set and cleared from a connection's context menu in the
tree, and honoured by every SQL editor that has not picked its own.

## Running SQL

A `.sql` editor gets two toolbar buttons — a connection picker and a run
button — plus a status bar entry showing the connection it will run on
(`Ctrl`/`Cmd`+`Enter` runs it). The selection runs if there is one, else
the whole file.

### The two run commands

| Command | Default | What runs |
| --- | --- | --- |
| `mariadb.runSqlFile` | `Cmd`/`Ctrl`+`Enter` | The whole file, or the selection. |
| `mariadb.runSqlStatement` | `Shift`+`Enter` | Just the statement the cursor is in, which is also selected so it is plain what went. |

A third toolbar button toggles **stop on error** for the file in front
of you. A file starts out doing whatever `mariadb.execute.stopOnError`
says and can be switched for the session without changing the setting;
closing the file forgets the switch.

That button is two commands, `mariadb.stopOnError.disable` and
`...enable`, shown one at a time on the `mariadb.stopOnError` context
key: VS Code cannot swap a command's icon, so a button with two states
has to be two commands with two icons and a `when` clause between them.

`statementAtOffset()` finds the statement at the cursor, over the same
scanner the gutter dots use. A caret just after a statement counts as
inside it; in the whitespace between two, the *following* statement is
taken, because that is the one being typed into.

**The shortcuts are settings-driven**, which `contributes.keybindings`
does not support directly - it is static. The way round it: contribute
one binding per (command, chord) pair, each gated on a `when` clause
naming a context key, and set those context keys from the settings
(`src/editor/keybindings.ts`). `mariadb.keybindings.runScript` and
`mariadb.keybindings.runStatementAtCursor` therefore move the commands
between a fixed set of chords. Anything outside that set is still the
Keyboard Shortcuts editor's job.

### Which connection a file runs on

In order: the connection explicitly picked for that document, else the
one its header names, else the default.

**New SQL Editor** on a connection in the tree opens an unsaved `.sql`
file bound to it. The binding is held in memory keyed by document URI,
*and* written into the file as a header:

```sql
-- MariaDB connection: dba@localhost:3310
```

The header exists because an untitled document's URI changes the moment
it is saved, which would silently drop an in-memory binding. Writing it
into the file keeps the association across a save, across reopening and
across sharing the file, and leaves it visible so it can be corrected by
editing the line. An explicit pick still wins over it, so the status bar
is always the truth.

Tab captions skip leading comments (`dropLeadingComments`), or the first
result of every generated file would be captioned with its header.

`ExecutionService.execute()` then:

1. splits the script client-side (`splitStatements.ts`) so each result can
   be paired with the statement that produced it — the server splits again
   on its own side, and the two have to line up,
2. runs it with `db.execute_sql_script`,
3. sends statements without a result set to the **Output** tab and each
   result set to a tab of its own,
4. works out whether a result set can be written back.

### The statement scanner

`src/sql/statementSpans.ts` is a port of the MySQL Shell's
`MySQLParsingServices.determineStatementRanges`. It scans character by
character and records offsets, never building substrings as it goes,
which is what lets it run over a large file: about 120 MB/s, and around
80 ms for a 9.6 MB, 100k statement script.

Three deliberate differences from the original:

- **It is a generator.** Spans are yielded as they are found, so the
  first one is available in microseconds and the gutter can show dots
  long before a large file has been scanned through.
- **No dollar-quoted strings.** That branch guards a MySQL 8.1 feature
  for JavaScript stored programs. MariaDB has no dollar-quoted strings,
  so the branch could only ever mis-scan a `$`.
- **One off-by-one is corrected.** In the original, a word starting with
  `d` that is not DELIMITER left `contentStart` on its *second*
  character, so `delete from t` reported `elete from t`. Every other
  branch marks content before advancing the tail; that one advanced
  first.

`hasContent(span)` tells a real statement from a run of comments:
`contentStart` is deliberately set below `span.start` when there is no
content. **Every** content-free span has to say so that way — the spans a
comment left open at the end of the input produces used to set
`contentStart` to `span.start` instead, which reads as content, so a
trailing `-- note` with no newline after it counted as a statement while
the same line with a newline did not. That fed `splitStatements()`,
`statementAtOffset()` and the gutter dots alike.

### Splitting has to agree with the server

The results come back as a flat list and are paired with the statements by
position, so a client split that disagrees with the server's shifts every
result after the disagreement, which silently gives a grid the wrong
statement and loses its editability.

One rule is not obvious and was read off the running server rather than
guessed: **a line comment that begins a statement is a statement of its
own.** `-- a note` followed by a query is two statements, not one. It
holds for `--` and `#`, for an indented comment, for a comment after a
semicolon and for each of several comment lines in a row. It does *not*
hold for block comments, which stay with the statement that follows, nor
for a line comment in the middle of a statement, which stays inside it.

**Being a statement is not the same as being run.** The MCP plugin does
not execute a comment — it carries no SQL, and the server would accept it
as a query and report a result for it — but it still *numbers* one, so the
indexes on either side of it are unchanged. `splitStatements()` therefore
keeps the comment as an entry (the pairing depends on it) and marks it
`executable: false`. `execute()` filters on that for the two things that
are about what is being run rather than what was split: the
`Running N statements` count, and the `stopped after N of M` on a failed
run. A run's own lines (the opening one, and the row for a call that
failed outright) point at the first **executable** statement, not at a
header comment standing in front of it.

`src/test/sql/splitStatements.test.ts` pins all of these, and
`splitStatements()` also drops DELIMITER commands, which the server
consumes without producing a result.

Agreement was checked against a running server across 21 scripts
(comments in every position, quoted and escaped delimiters, block
comments, DELIMITER blocks, empty statements); it is worth re-running
that comparison after any change here.

### What makes a result set editable

`findUpdatableTarget()` accepts only a plain `SELECT ... FROM <one table>`.
Joins, unions, `GROUP BY`, `HAVING`, `DISTINCT`, subqueries, derived tables
and aggregates in the select list are all rejected, because the result then
no longer maps row for row onto stored rows. Literals and comments are
stripped first so a keyword inside either cannot be mistaken for the real
thing.

For a statement that passes, the table's columns are fetched with
`db.get_object_details` — that is where the primary key, the data types and
the auto-increment flags come from. An unqualified table name is resolved
against `SELECT DATABASE()`, asked at most once per execution. Without a
primary key in the result the grid stays read only, since there would be no
way to address a row.

Edits are held in the grid until **Apply**. `QueryBuilder` then generates
`UPDATE`, `INSERT` and `DELETE` statements — modelled on the MySQL Shell's
`QueryBuilder` — addressing each row by the primary key it had *before* the
edit, leaving auto-increment and generated columns out of an `INSERT`, and
running deletes last so a row that was edited and then removed is not
updated after it is gone.

## Statement markers in the gutter

`src/editor/statementDecorations.ts` puts a blue dot on the first line of
every statement of a visible `.sql` editor.

The scan is **sliced**: it runs for a few milliseconds, pushes what it has
found to the editor, hands control back and carries on. On a large file
the first dots are therefore on screen almost at once instead of after
the whole file has been parsed, and typing stays responsive. A full
re-apply is throttled to at most every 150 ms, since `setDecorations`
takes the whole set each time and re-applying a growing array on every
slice would be quadratic.

A scan is abandoned when its document changes under it (the version is
re-checked at every statement), when a newer scan starts for the same
document, when the document is closed, or when the decorator is disposed.
Edits schedule a rescan 300 ms after they stop.

Comments get no dot: the dot goes on `contentStart`, not `span.start`.
DELIMITER commands get none either, so the dots line up with the
statements the result tabs come from.

The ranges come from the scanner **for now**. They are the same thing a
language server would report, so `StatementDecorator.decorate()` is the
seam to replace when one exists: everything else takes ranges and knows
nothing about where they came from.

## Result view

Docked in the **bottom panel**, beside Problems, Output, Debug Console and
Ports. That is why it is a `WebviewView` in a `contributes.viewsContainers.panel`
container and not a `WebviewPanel`: only a view can live in the panel area,
a panel would open as an editor tab. It is revealed with `view.show(true)`
when one exists, and otherwise by running the `mariadb.results.focus`
command VS Code registers for every contributed view.

It serves `dist/webview/` under a strict CSP with a per-load nonce, so the
view cannot reach the network. Messages are held back until the frontend
posts `ready`, and the current report is re-sent whenever a view is
resolved again - VS Code throws a hidden view's DOM away, so it comes back
empty. `retainContextWhenHidden` keeps pending grid edits alive while
another panel tab is in front.

### Layout

Vertically, and deliberately in this order:

```
+-----------------------------------------------+
| content: output grid, result grid, or preview |  flex
+-----------------------------------------------+
| [Output][Result #1]  status  [conn v][tools]  |  tabs at the bottom
+-----------------------------------------------+
| error message, when there is one              |  very bottom
+-----------------------------------------------+
```

Paddings are a couple of pixels throughout: the panel area is short, and
every row spent on chrome is a row of data not shown.

### State is per connection, and the host owns it

`ResultViewProvider` keeps a `Map<connectionUri, { output, resultSets }>`
and the webview mirrors one entry of it at a time. The host has to own it
because VS Code discards a hidden view's DOM, so the frontend cannot be
trusted to still be holding anything.

The two halves behave differently on purpose:

- **Output accumulates.** It is the log of everything run on that
  connection, so a run appends to it. It is capped at `MAX_OUTPUT_ROWS`
  (2000) per connection, oldest first, because a window can stay open
  for days.
- **Result sets are replaced.** The tabs stand for the *last* run.

### Picking the connection, and clearing

The connection picker is a `<select>` at the far left of the view's own
bottom bar. It is not in the panel toolbar because it cannot be -
checked against `@types/vscode` 1.138:

- `WebviewView` exposes `title`, `description`, `badge` and `show()`,
  and `TreeView` little more; neither has a control of any kind.
- `TreeViewOptions` has no filter option. The filter boxes in Problems
  and Output are built-in workbench UI.
- `contributes.submenus` *does* put a real dropdown menu in `view/title`,
  but its items are commands declared in `package.json` with fixed
  titles, so it cannot list connections discovered at runtime.

A view title can hold buttons, so **Clear Output** does live there
(`mariadb.clearResultView`, `$(clear-all)`). It empties the connection on
show completely: its gathered output *and* its result set tabs, along
with the apply context those tabs were written back through. Other
connections are untouched.

The connection on show is also named beside the view's title through
`WebviewView.description`, as the Output panel names its channel.

Because the tabs are replaced but the output is not, an output row can
outlive the result set it produced. That is why result set ids carry the
run that made them (`run3-result-0`): without it a later run's
`result-0` would make an old row's jump arrow point at the wrong tab.
The grid only draws the arrow when the id is still among the open tabs.

### Fonts and surfaces

The view is UI, not an editor, so it uses `--vscode-font-family` at
`--vscode-font-size` throughout - column headers, messages, counts and
grid cells alike. `--vscode-editor-font-family` is reserved for SQL: the
output grid's Statement column and the generated statements in the SQL
preview. Times and counts get `font-variant-numeric: tabular-nums`, so
they line up without leaving the UI font.

Every row shares one background, `--vscode-sideBar-background`, header
included; only borders separate them. What a row *means* is carried by a
marker icon instead, drawn with the codicon font in the Problems panel's
own colours (`--vscode-problemsInfoIcon-foreground` and its siblings).
The opening and closing lines of a run are set apart by weight alone.

`@vscode/codicons` supplies the glyph font. Two things make it work:
the webview build sets `base: "./"`, because the stylesheet is loaded
through `asWebviewUri` and an absolute `/codicon.ttf` would resolve
against the webview origin's root and 404 silently; and the font keeps
its own filename while the stylesheet's is pinned to `main.css`, which
the extension references from the HTML it builds.
`src/test/webview/assets.test.ts` guards both.

### The output grid

A Tabulator table:
`◆ | Output | Time | Elapsed | Rows | Statement`.

The marker and the message lead together, as they do in the Problems
panel; the details follow. The grid draws **no vertical rules**: it is a
log, not a spreadsheet, and the columns line up on their own. The result
grids keep theirs, so the override is scoped to `.outputGridHost`.

A run reads as a block of three parts, which is what `role` on a row
says: an opening line (`Running 5 statements on ...`), one line per
statement, and a closing line (`Finished ... successfully`, or
`Finished with 2 errors, stopped after 4 of 5`).

- `Time` is the run's start, to the millisecond.
- `Elapsed` on a statement line is **that statement's own** time, as the
  server measured it. Only the closing line carries the run's total.
- `Rows` is rows returned for a query, rows affected otherwise.
- The marker is `info`, `warning` or `error`. A statement that succeeded
  but raised warnings is a warning, and the closing line of a run takes
  the worst of what it saw.

The two jump arrows ride **inside the cells they belong to** rather than
in columns of their own: a column of arrows costs the Output column width
it can put to better use, and the panel area is short of it.

- `↗` sits in the Output cell's top right corner and puts the cursor on
  the statement in the file it came from. On the closing line of a failed
  run it also scrolls the output to that run's first error, so one
  control answers "what went wrong, and where". A message that has one
  gets `padding-right` so the text truncates before the arrow rather than
  running under it.
- `→` follows the row count in the Rows cell and jumps to the result set
  that statement produced, when that tab is still open. Its place is
  held by an empty slot where there is no arrow, so the counts stay in a
  column.

Both share their cell with the value beside them, so their `cellClick`
handlers check what the click actually landed on (`clickedOn()`) instead
of firing for anywhere in the cell.

### What the server had to report for this

Per-statement timing and error attribution are not something a client can
work out: `db.execute_sql_script` runs the whole script in one call. The
MCP plugin was changed for it (`mcp_plugin/lib/db_functions.py`):

- every entry carries `statement_index` and `execution_time`,
- a failing statement no longer raises. Its entry carries `error` and
  `statement` instead of a result set, so the entries for what already
  ran are still returned - which matters, because a script is not a
  transaction,
- `stop_on_error` (default true) chooses between ending at the first
  failure and running every statement and reporting each one,
- a statement that is nothing but a `--`/`#` line comment is **not run**
  and has no entry, but still takes up a `statement_index`. Keeping the
  numbering is what lets that ship on its own: an extension that has not
  been updated goes on pairing correctly and simply stops seeing the row.

All of these are read **defensively** on this side: they are optional in
`IStatementResult`, and a shell that predates them still works, falling
back to pairing by position and to no per-statement timing. The comment
change needs nothing defensive: a plugin that still runs comments sends
one more entry, which pairs with the comment entry that is still in the
split — the row comes back, and nothing is misattributed.

### The result grids

[Tabulator](https://tabulator.info) 6.3, as in the MySQL Shell extension.
Tabulator owns the DOM below its container, so `ResultGrid` holds it in a
ref and feeds it data rather than re-rendering it through Preact.

Two things about it are easy to break:

- **Nothing may touch the table before `tableBuilt` fires.** Tabulator
  builds itself asynchronously and every data call before that throws.
  Rows that arrive early are parked in a ref and applied by the
  `tableBuilt` handler.
- `replaceData` is used rather than `setData`, because `setData` throws
  away the scroll position and the column widths on every keystroke.

Its stylesheet is `tabulator_simple`, imported at the top of
`styles.css` and then overridden - surface by surface - with VS Code theme
variables, since Tabulator ships a light theme of its own.

**Every surface it paints has to be answered, not just the obvious
ones.** A missed one is invisible in a light theme and glaring in a dark
one, which is how each of these was found:

- `.tabulator-tableholder .tabulator-table` is `#fff` with `#333` text
  and sits *behind* the rows, so transparent rows still came out white.
- `.tabulator-placeholder span` is `#000`, so the "nothing here yet"
  text disappeared into a dark background.
- The sortable header's hover is `#e6e6e6` and its sort arrows `#bbb`.

The theme's only `!important` backgrounds are on calcs holders and the
footer, neither of which this view renders, so nothing can defeat an
override.

### SQL preview

The **Preview SQL** button in the toolbar swaps the grid for the list of
statements the pending changes would run. The statements come from
`createQueryBuilder()`, the same factory the extension executes with, so
what is previewed is what will be sent. Clicking a statement goes back to
the grid and scrolls to the row it was generated from - which is how a
`RowChange` came to carry its `rowIndex`.

A failed apply puts the error under the offending statement and opens the
preview, so the user lands on the statement that failed rather than on a
message about it.

### Shared code

`src/webview/protocol.ts` holds the message types and is imported by both
sides, so the shapes cannot drift. `src/webview/changes.ts` (edit
collection) and `src/sql/queryBuilder.ts` with
`src/sql/resultSetQueryBuilder.ts` (statement generation) live on the
extension side but are bundled into the frontend too - they are pure
TypeScript with no Node imports, and having one copy is what keeps the
preview and the execution identical.

## Commands

| Command | Title | Where |
| --- | --- | --- |
| `mariadb.refreshConnections` | Refresh | Connections view title |
| `mariadb.connect` | Connect | Connection context menu |
| `mariadb.disconnect` | Disconnect | Connection context menu |
| `mariadb.newSqlEditor` | New SQL Editor | Connection row, beside Connect |
| `mariadb.setDefaultConnection` | Set as Default Connection | Connection context menu |
| `mariadb.clearDefaultConnection` | Clear Default Connection | Connection context menu |
| `mariadb.clearResultView` | Clear Output | Result view toolbar |
| `mariadb.selectEditorConnection` | Select Connection for this SQL File | SQL editor toolbar, status bar |
| `mariadb.runSqlFile` | Run SQL Script | SQL editor toolbar, `Ctrl`/`Cmd`+`Enter` |
| `mariadb.runSqlStatement` | Run SQL Statement at Cursor | SQL editor toolbar, `Shift`+`Enter` |
| `mariadb.stopOnError.disable` / `.enable` | Stop on Error (on/off) | SQL editor toolbar, one shown at a time |
| `mariadb.restartMcpServer` | Restart MCP Server | Command palette |
| `mariadb.showMcpServerLog` | Show MCP Server Log | Command palette |

## Testing approach

Everything that touches the outside world sits behind a small interface —
`ShellEnvironment`, `ProcessRunner`, `ProgressHost`, `IMcpConnector`,
`IMariaDbApi`, `IConnectionSettings` — so the lookup, install, connection,
tree, execution and panel behaviour are all tested with fakes, for every
platform, from a single host. `src/test/shell/nodeRuntime.test.ts` covers
the real process adapter by spawning actual processes against a temporary
directory.

The MCP decoding fixtures in `src/test/mcp/protocol.test.ts` are the shapes
the running server actually answers with, so the mapping is pinned to the
server rather than to an idea of it.

## Debugging in VS Code

`code_ext/.vscode/launch.json` is folder scoped, so its `${workspaceFolder}`
resolves to `code_ext` both when that folder is opened on its own and when
the multi-root `MariaDBShellPlugins.code-workspace` is opened. (Opening the
repository root as a plain folder does not pick the file up at all, since
only the root's `.vscode/launch.json` is read then.)

Two things about the **watch** task are deliberate and easy to undo by
accident:

- It depends on a separate `build: webview` task rather than chaining the
  webview build into the watch script. The webview build prints
  `built in ...`, which would satisfy the watcher's `endsPattern` and let
  the extension host launch before `dist/extension.js` had been written.
- Its `endsPattern` matches the failure lines (`Transform failed`,
  `error during build`, `Could not resolve`) as well as `built in `. A
  failed rebuild never prints `built in `, so without them F5 would wait
  for a build that never finishes. It deliberately does **not** match
  `watching for file changes`, which vite prints *before* the first build.

`preLaunchTask` names the `watch` task rather than using
`${defaultBuildTask}`, because other folders of the multi-root workspace
declare a default build task of their own.

The problem matcher parses esbuild's `<abs path>:<line>:<col>: ERROR: msg`
form, which is uppercase and absolute - hence `fileLocation: "absolute"`.

## Known gaps

- `package.json` has **no Marketplace `icon`**: VS Code requires a PNG
  there, and the artwork is kept as a vector on request. Add a PNG before
  publishing.
- `MINIMUM_SHELL_VERSION` is 26.9.2, which is a **released** shell and
  therefore predates the `db.execute_sql_script` changes this extension
  asked for (`statement_index`, `execution_time`, per-statement `error`,
  `stop_on_error`). Everything that reads them is optional and falls
  back, so it works - but against such a shell there is no per-statement
  timing, a failing script reports one error for the whole call with no
  statement to jump to, and `stopOnError: false` is ignored. Raise the
  minimum once a shell carrying the new plugin ships.
- The `db.add_connection` / `db.delete_connection` tools are reachable
  through `MariaDbApi` but **nothing in the UI calls them yet**: there is no
  "Add Connection" command, no credentials dialog and no tree entry for the
  `gui` list. Adding and removing connections is still `mcp.setup`'s job
  from the user's point of view.
- The result grid edits every value as text; there is no type-aware editor
  (date picker, NULL toggle, BLOB viewer) yet, and no cell context menu.
- There is no paging. The MySQL Shell's result view pages through a result
  set; `db.execute_sql_script` returns the whole thing at once, so there is
  nothing to page through and the next/previous buttons were left out.
- Tabulator measures the DOM to lay itself out, and jsdom reports every
  element as zero sized, so it never finishes building under test. The
  grid's mapping, formatters and cell callbacks are tested directly
  (`webview/test/ResultGrid.test.tsx`); its rendering is not.

## Conventions

- Four space indentation, double quotes, trailing semicolons, 80 columns.
- Arrow function consts for exported functions; JSDoc with `@param` and
  `@returns` on everything exported.
- Comments explain *why*, not *what*.
- Relative imports carry the `.js` extension.
