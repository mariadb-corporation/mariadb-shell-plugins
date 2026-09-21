# Result view

The webview docked in the bottom panel: its layout, whose state it shows,
the output grid, the result grids, the SQL preview and the code both sides
share.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). What fills it is in
[running-sql.md](running-sql.md).

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

## Layout

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

## State is per connection, and the host owns it

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

## Picking the connection, and clearing

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

## Fonts and surfaces

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

## The output grid

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

## What the server had to report for this

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

## The result grids

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

## SQL preview

The **Preview SQL** button in the toolbar swaps the grid for the list of
statements the pending changes would run. The statements come from
`createQueryBuilder()`, the same factory the extension executes with, so
what is previewed is what will be sent. Clicking a statement goes back to
the grid and scrolls to the row it was generated from - which is how a
`RowChange` came to carry its `rowIndex`.

A failed apply puts the error under the offending statement and opens the
preview, so the user lands on the statement that failed rather than on a
message about it.

## Shared code

`src/webview/protocol.ts` holds the message types and is imported by both
sides, so the shapes cannot drift. `src/webview/changes.ts` (edit
collection) and `src/sql/queryBuilder.ts` with
`src/sql/resultSetQueryBuilder.ts` (statement generation) live on the
extension side but are bundled into the frontend too - they are pure
TypeScript with no Node imports, and having one copy is what keeps the
preview and the execution identical.
