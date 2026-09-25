# Result view

The webview docked in the bottom panel: its layout, whose state it shows,
the two pickers, the result grids, the SQL preview and the code both sides
share. The actions grid, and what the server had to report for it, is in
[actions-grid.md](actions-grid.md).

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). What fills it is in
[running-sql.md](running-sql.md), and everything other than a run that
fills it - the connections a URI has open, and what is reported on them -
is in [connections.md](connections.md).

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
| error message, when there is one              |  very top
+-----------------------------------------------+
| content: actions grid, result grid, or preview|  flex
| ......................................        |
| status ............ [read only][tools]        |  the result set's own
+-----------------------------------------------+
| [Actions][Result #1 ][< >]     [conn][All Ses]|  what is on show
+-----------------------------------------------+
```

The last row is the **contentSelectionBar**, and it holds only what
picks what is on show: the tabs, and the two pickers at the far right.
What a result set came to, and what can be done with it, is a
**statusBar of its own along the bottom of that result set**
(`ResultStatusBar`), inside the tab's content. Every button there -
Preview SQL, + Row, Revert, Apply, Refresh - acts on one result set, so
it belongs to the result set and not to the view; keeping them apart is
also what leaves the row of tabs the room to be a row of tabs. The
Actions tab has no such bar: there is no result set for one to be
about.

The error bar leads, above what it is about: it is read before the eye
has gone looking for what went wrong. It steps through every error of
the last run rather than showing one - see below.

The tabs are **the editor's, not the activity bar's**: flat, with the
one on show marked by a line under it in `textLink.foreground` rather
than by a filled surface. The panel's own container tabs sit right
above them and are selected by surface, so two selections in one corner
of the screen cannot be read as one.

Only the **result** tabs scroll. The Actions tab stays outside the
strip, because it is what a result tab is gone back to, and the strip
carries a paging button on each side - VS Code chevrons - that appear
only while the tabs do not all fit. `pagingOf()` is the whole of the
decision: it reads the strip's `scrollWidth`, `clientWidth` and
`scrollLeft` and says whether it overflows and whether it is at either
end, so the buttons can be disabled where there is nothing that way. A
`ResizeObserver` on the strip and the strip's own `scroll` event are
what ask it again. The scrollbar itself is hidden: the buttons are the
control, and a bar under the tabs would cost a row of the panel.

A button pages by most of the strip's width, as a smooth scroll, and
two things are careful not to cut that scroll short. The tab switched
to is brought into view from an effect **keyed on the active tab**, not
from the tab's `ref` - Preact runs an inline `ref` on every render, so
scrolling there meant the first frame of a page dragged the tab it
started from straight back, and the strip moved a few pixels and
stopped. And `measureTabs()` keeps the paging state it already has when
nothing about it changed, so the scroll events a page fires do not each
cost a render.

Paddings are a couple of pixels throughout: the panel area is short, and
every row spent on chrome is a row of data not shown.

## State is per connection, and the host owns it

`ResultViewProvider` keeps a `Map<connectionUri, IConnectionResults>` and
the webview mirrors one entry of it at a time. The host has to own it
because VS Code discards a hidden view's DOM, so the frontend cannot be
trusted to still be holding anything.

The two halves behave differently on purpose:

- **Actions accumulate, newest first.** They are the log of everything
  that has happened on that connection, and what has just happened
  goes to the **top** of it - which is where the view already is, so
  nothing has to be scrolled to and a reader looking through the older
  rows is not taken away from them. The list is capped at
  `MAX_ACTION_ROWS` (2000) per connection, the oldest going first from
  the end, because a window can stay open for days. A run counts as
  its own row plus everything under it - its statements **and their
  warnings**, counted through the whole tree rather than one level of
  it - and is dropped whole: the tree cannot keep half of one.
- **Result sets are replaced.** The tabs stand for the *last* run, so
  starting a run clears them along with the apply context they were
  written back through. They are kept **per open connection**: a run on
  one does not throw away the tabs of another, and picking that other one
  brings them back.

The actions are **one list per URI**, not one per open connection, because
that is what the view shows by default: each row carries the label of the
connection it happened on (`IActionRow.connectionLabel`), and picking one
filters the list rather than switching to another. Which connection a
report's tabs belong to is read off the run's own row, so a report cannot
say one thing and the rows it carries another.

## Everything that happens, not only what is run

`appendEvent` is what puts up a row for anything other than a run: a
connection opened or closed, and each `db.*` call made on one. They are
`role: "event"` rows - no twistie, nothing under them - with the call in
the Information column where a statement row has its SQL, and they come
from the reporter the `ConnectionManager` is built with.

Two things they deliberately do not do:

- **An event does not reveal the view.** Browsing the schema tree is no
  reason to throw the panel open over what the user is reading; the rows
  are simply there when they next look. A run still reveals it.
- **An event does not reset the page.** State now arrives at any moment,
  so the frontend rebuilds its editing state and picks a tab only when
  the result set ids actually changed - otherwise a schema listed in the
  tree would throw away a grid's pending edits.

State is sent for every row that appears, which is why the configured
connection list is cached for `CONNECTION_LIST_TTL_MS`: listing it costs
two tool calls, and it only changes when the user edits a connection.

A run goes into the actions **when it starts**, not when it finishes.
`startRun()` puts up the row `pendingRunRow()` built - marked `pending`,
with an empty child array - before the connection has even been opened,
which is what the shell may have to be started for. `showResults()` then
replaces it, matching on the run's id, so the finished run takes the
pending row's place rather than being appended beside it. That is why
`IExecutionReport.actions` is one row and why `execute()` is given the
same `runId` the pending row went up with.

There is no "running" message and no placeholder over the view: the run
is a row like any other, so what the connection did before it stays
readable while it runs.

## Picking the connection, and clearing

The two pickers are `<select>`s at the far **right** of the view's own
bottom bar: the connection URI, and which of the connections open on it.
They sit after everything else, held there by `margin-left: auto` on the
first of them, so they stay put whether or not the status text beside
them has anything to say. They are not in the panel toolbar because they
cannot be - checked against `@types/vscode` 1.138:

- `WebviewView` exposes `title`, `description`, `badge` and `show()`,
  and `TreeView` little more; neither has a control of any kind.
- `TreeViewOptions` has no filter option. The filter boxes in Problems
  and Output are built-in workbench UI.
- `contributes.submenus` *does* put a real dropdown menu in `view/title`,
  but its items are commands declared in `package.json` with fixed
  titles, so it cannot list connections discovered at runtime.

**General Actions** is listed FIRST in the connection picker, and only once
something is filed under it (see `mariadb.actions.logAllCalls` in
[connections.md](connections.md)). It is not a connection, but where
nothing is on show yet the first general action IS what the view comes
up on - with the logging on it is usually first, and the panel used to
stay empty. `#generalByDefault` marks General Actions picked that way, and
the first CONNECTION event takes the view over from it; General Actions
picked by the user (`selectConnection`) stays.

The second picker offers **All Sessions** first, which is what the view
opens on and the only case in which the actions name a connection per
row. It lists the connections open on the URI and any that only the
gathered actions still remember, which say `(closed)`: the log outlives
the connection it was gathered on. A filter that would hide a run just
starting is dropped rather than left in force - putting a run up the
moment it starts is pointless if it cannot be seen - and picking another
URI goes back to all of them, since one URI's connections are not
another's.

A view title can hold buttons, so **Clear Actions** does live there
(`mariadb.clearResultView`, `$(clear-all)`). It empties what is on show:
with All picked, the connection's whole log *and* every one of its
result set tabs, along with the apply contexts those were written back
through; with one connection picked, only its rows and its tabs. Other
URIs are untouched.

The connection on show is also named beside the view's title through
`WebviewView.description`, as the Output panel names its channel.

Because the tabs are replaced but the actions are not, a statement row can
outlive the result set it produced. That is why result set ids carry the
run that made them (`run3-result-0`): without it a later run's
`result-0` would make an old row's jump arrow point at the wrong tab.
The grid only draws the arrow when the id is still among the open tabs.

## Fonts and surfaces

The view is UI, not an editor, so it uses `--vscode-font-family` at
`--vscode-font-size` throughout - column headers, messages, counts and
grid cells alike. `--vscode-editor-font-family` is reserved for SQL: the
actions grid's Information column on a statement row - not on a run's,
which holds a summary - and the generated statements in the SQL
preview. Times and counts get `font-variant-numeric: tabular-nums`, so
they line up without leaving the UI font.

The panel itself is drawn on `--vscode-panel-background`, so the bars
around the content - the row of tabs, a result set's own bar - read as
part of the panel. The **content** of the tab on show is the one thing
set into it, on `--vscode-sideBar-background`, and every row of every
grid shares that one surface; only borders separate them. What a row *means* is carried by a
marker icon instead, drawn with the codicon font in the Problems panel's
own colours (`--vscode-problemsInfoIcon-foreground` and its siblings).
A run's own row is set apart by its twistie and by the indent of the
statements under it - not by weight, and not by colour: every row is
the same font in the same size on the same ground.

`@vscode/codicons` supplies the glyph font. Two things make it work:
the webview build sets `base: "./"`, because the stylesheet is loaded
through `asWebviewUri` and an absolute `/codicon.ttf` would resolve
against the webview origin's root and 404 silently; and the font keeps
its own filename while the stylesheet's is pinned to `main.css`, which
the extension references from the HTML it builds.
`src/test/webview/assets.test.ts` guards both.

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

The **Preview SQL** button in the result set's own bar swaps the grid
for the list of statements the pending changes would run. The
statements come from `createQueryBuilder()`, the same factory the
extension executes with, so
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
