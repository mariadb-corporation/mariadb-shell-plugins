# The actions grid

The log of everything done on a connection, in the result view's Actions
column: its rows, how an error and a long value show, what scrolls, the
jump arrows, and what the server had to report for it.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Split out of
[result-view.md](result-view.md), which has the view around it: layout,
state, the pickers, fonts, the result grids and the SQL preview.


A Tabulator table: `Actions | Time | Information | (Conn)`.

Three columns carry it, **and no header row**: `headerVisible` is
false, because the columns are self evident from what is in them and
the panel area is short enough that a row of titles is a row of the log
not shown. The titles stay in the column definitions - one option flips
the header back - and the actions grid's own header rules went with it.
The grid is given 2px of margin above it instead, and a height of
`calc(100% - 2px)` to pay for it: an element told to be 100% tall with
a margin on top overflows its parent by exactly that much.

What is *not* there is deliberate:

- **There is no column of icons.** Everything a row leads with is in
  the Actions cell: the twistie of a run or the branch of a statement,
  which Tabulator puts in front of the cell's own content, then the
  severity marker, then what happened. A column of its own for the
  marker cost width twice over - its own, and the gap between it and
  the message it belongs to.
- **Time, Information and Conn are the detail beside the message**, so
  they are quieter and smaller than it (`0.9em`, and `0.85em` for
  Conn), and `vertAlign: "middle"` centres them against a message that
  may be taller than they are.
- **Time holds both the start and the duration**, as
  `20:44:13.431 (3ms)`: the statement's own time, or the whole run's on
  its own row. One column of the pair would be dead space on every row
  that has no duration, and a run's start and its length read as one
  fact. Nothing in brackets while a run is still under way - a `(0ms)`
  would claim it had finished.
- **There is no Rows column.** The count is already in the Actions
  column, in the server's own words (`12 rows in set`,
  `Query OK, 3 rows affected`, `Listed 12 tables in world`), so a column
  for it repeated what the row already said. `IActionRow` carries no
  `rows` field for the same reason: nothing reads it.
- **Conn is there only while several connections are on show together.**
  With one picked, a column repeating its name the whole way down says
  nothing. It comes last, where it labels the row without standing
  between the marker and what it says, and is drawn smaller and
  quieter than what it labels. The columns close over whether it is there, so
  the table is rebuilt when that changes.

The marker and the message lead together, as they do in the Problems
panel; the details follow. The grid draws **no vertical rules**: it is a
log, not a spreadsheet, and the columns line up on their own. The result
grids keep theirs, so the override is scoped to `.actionsGridHost`.

**Every marker that stands on its own sits in one column.** A run
carries a 16px twistie with 2px of margin after it; an event, which is
a top level row with no children, gets neither twistie nor branch, so
`formatMessageCell` gives it a `.treeSpacer` of the same 16px *and the
same margin* - without them a log of runs and events would step in and
out as the eye runs down it. The room around the marker is given as
margins rather than as a flex `gap` for the same reason: a gap would
also fall between that spacer and the marker after it.

Everything in that cell except the message itself is `flex: 0 0 auto`,
Tabulator's branch element included. A flex line shares what it has to
give up in proportion to how wide each item wants to be, so a message
too long for its cell asks for hundreds of pixels beside the branch's
seven: left to shrink, the guide all but disappears and the marker and
the message slide left with it, out of the column.

A **statement** is meant to miss it: `dataTreeChildIndent` is **20**,
and Tabulator adds its branch element's 7px of width and 5px of margin,
which puts a statement's marker at 32 - one clear step in from its
run's 16. That step, and the twistie above it, are what say the row
belongs to the run.

## One row per run, opened to see its statements

It is a **tree**, up to three levels deep, which is what `role` on a row
says: one `run` row per execution, with a `statement` row per statement
under it, a `warning` row under a statement for each warning it
produced, and an `event` row of its own for everything else that
happened. A run is therefore one line in the log until it is asked
about, instead of a block of lines to be picked apart by eye.

- `Actions` on a run says what ran and where (`Ran 5 statements on ...`,
  `Running 5 statements on ...` while it is under way); on a statement,
  what the server said about it.
- `Information` - the column that used to be `Statement` - says what the
  run came to (`Finished 5 statements successfully`,
  `Finished with 2 errors, stopped after 4 of 5`, `Running…`) and, on a
  statement, which statement it was. A run's summary is the view
  speaking, not SQL, so it drops the editor font its children use.
- `Time` is the start, to the millisecond, with the duration after it in
  brackets - the whole run's on a run row and that statement's own, as
  the server measured it, on a statement.
- The marker is `pending`, `info`, `warning` or `error`. A statement
  that succeeded but raised warnings is a warning, and a run takes the
  worst of what its statements saw. `pending` is the spinning
  `codicon-loading`, as VS Code marks work in progress.
- A **warning** row puts the server's own level and code
  (`Warning 1292`, `Note 1051`) where a statement puts its SQL, leaving
  the message column to the text, which is the thing being read. It
  takes the statement's time, connection and source, so the arrow on it
  goes to the statement that warned; it carries no duration of its own.
  A warning whose level is `Error` - which the diagnostics area can
  hold - is marked as one.

The texts come from `db.execute_sql_script`, which gained a `warnings`
array per statement for this (`level`, `code`, `message`, as
`SHOW WARNINGS` reports them). `warnings_count` is the older field and
can arrive without it, so `warningRowsOf()` produces nothing against an
older shell: the count still shows in the statement's message and the
row simply has nothing to open. That same change was what fixed the
count, which used to come back **0 for every statement that returned a
result set** - it is `mysql_warning_count`, and the server does not
have it until the rows have been read.

## An error grows its row; everything else gets a popup

An **error message is not cut off**. A server error runs to a sentence
or two and is the thing the reader came for, so `.errorRow` wraps the
message (`white-space: pre-wrap`, `overflow-wrap: anywhere`) and the
row grows to hold it - Tabulator sizes a row to its tallest cell, so
nothing more than the wrap is needed. The marker is pulled to the top
of the block (`align-items: flex-start` on `.actionMessageCell`) to
stay in the column of markers. Time and Information stay centred
against the block whether or not that is wanted: Tabulator writes
`align-items` as an **inline** style for a column with a `vertAlign`,
which no rule in the stylesheet can outrank.

**Every other cut-off cell gets a hover popup** (`overflowPopup.ts`)
holding the whole text and a button that copies it - `title` would do
none of the three things wanted, being unselectable, uncopyable and
gone the moment the pointer moves. One popup is shared by the page,
placed against the *window* rather than inside the cell, which is why
it is dismissed on a captured `scroll`: what scrolls is the grid, and
the popup would be left pointing at nothing. Whether a cell is cut off
is asked on **each hover** (`isTruncated`), not once when it is built,
because the columns are resizable and the panel is not. Leaving the
cell starts a 150ms close that entering the popup cancels - without
that grace the copy button could never be reached. The copy goes
through the `copyToClipboard` message the host already had, so it is
`vscode.env.clipboard` rather than the webview's own, and the button
says so on itself rather than raising a notification.

Neither can be tested under jsdom, which lays nothing out. What is
tested directly is the arithmetic and the element building -
`isTruncated`, `popupPosition`, `buildOverflowPopup`, and the hover
wiring against stubbed `scrollWidth`/`clientWidth`. The **rendering**
of both was checked the way the tree's was: the grid built into a
throwaway page and screenshotted in headless Chrome, with VS Code's
theme variables declared on `:root` (without them the popup's
background chain ends in an undefined variable and the whole
declaration drops, which is what the first screenshot showed).

**Only the newest run is open, and inside it the statements that
warned.** `dataTreeStartExpanded` is asked for every row Tabulator
builds and answers `startsExpanded()` against the set `expandedIdsOf()`
returns: the newest run, plus each of its statements that has children.
A warning the reader has to go looking for is a warning nobody reads,
and a count with no text behind it is no better. Since a data change
rebuilds every row, a run that was open closes behind the one that
follows it. The newest run is **looked for** rather than taken from the
front of the list, because the row in front of it may be an event - a
run's row goes up before the connection it needs has been opened, so
the opening lands above it. A pending run carries `children: []` rather
than nothing, so it already has its twistie and its message does not
shift when the statements arrive.

Three things about the tree had to be told to Tabulator:

- `dataTreeChildField: "children"`, because the rows are the protocol's
  own shape rather than Tabulator's `_children`.
- `dataTreeElementColumn: "message"`, the one column there is to put it
  in. Tabulator inserts the control as that cell's first child, which
  is why the cell is `inline-flex` - `inline`, because a Tabulator cell
  is inline-level and laid out beside its neighbours, and a block one
  takes the whole row and the grid comes apart.
- `dataTreeExpandElement` / `dataTreeCollapseElement`, VS Code's
  chevrons. Tabulator's own control is a boxed `+`/`-` in hard coded
  greys; supplying the elements is what stops those rules applying at
  all. Its branch guide is the one surface left, and
  `--vscode-tree-indentGuidesStroke` answers it.

Rows are looked up by index among the **top level only**, so a statement
cannot be scrolled to by its own id: `openTo()` finds its run, opens it
and takes the child component from `getTreeChildren()`.

## Nothing scrolls itself

Rows arriving are not scrolled to, because they arrive at the **top**
and the view opens there. A reader who has not gone looking through the
older rows is already where the new ones appear, and one who has is not
taken away from what they are reading. That is what putting the newest
first buys, and it is why `replaceData` is now the whole of the data
effect.

The jump arrows still scroll where they are pointed: an explicit ask,
and the one thing in the grid that moves the view.

## The two jump arrows

They both ride **at the end of the Actions cell** rather than in columns
of their own: a column of arrows costs the Actions column width it can
put to better use, and the panel area is short of it.

- `→` jumps to the result set that statement produced, when that tab is
  still open.
- `↗` puts the cursor on the statement in the file it came from. On a
  failed run's row it also opens that run and scrolls to its first
  error, so one control answers "what went wrong, and where".

They are laid out **beside** the message rather than over it - the cell
is a flex row of the message and an `.actionArrows` span - so the text
truncates before them, a row can show both, and neither can cover the
other.

**The whole row is the target**, as a row of the Problems panel is: a
click anywhere along one - the time and the statement as much as the
message - takes the cursor to the statement it came from, and the row
says so with a pointer (`goesSomewhere`, set by the `rowFormatter` on
any row that has a `source`). `actionClick()` decides, off a table-level
`rowClick` rather than a column's `cellClick`, so that every column
counts. The jump arrow is the one exception, going to a result set
instead; the go-to arrow now does nothing its row does not already do
and stays only because it says so, and because a run's carries a title
of its own.

Two things make that safe, both checked in a browser rather than
reasoned about. Tabulator calls `stopPropagation()` on its
expand/collapse element, so opening a run is still only opening it -
and that holds for the **custom** chevrons this grid supplies, which
carry `treeToggle` rather than Tabulator's own class. And the overflow
popup hangs off the body rather than off the cell, so its copy button
is not a click on the row either.

### Right-click copies a cell

VS Code puts a Cut / Copy / Paste menu on every webview, and its Copy acts
on the text SELECTION - clicking a grid cell selects nothing, so it copied
nothing. Tabulator's `cellContext` now opens `contextMenu.ts`'s menu
instead: one item, **Copy**, for that cell's text as shown
(`cellCopyText`: the message, `timeOf`, `informationOf`, the connection
label), through the same `copyToClipboard` message. Its `preventDefault()`
is what keeps VS Code's menu away - the webview shows it only for an
unhandled event. One menu for the page, positioned at the pointer inside
the window (`menuPosition`), in the `--vscode-menu-*` colours; Escape, a
click elsewhere, a scroll, a resize or a blur closes it, and the grid's
teardown does too. An empty cell's Copy is disabled. The result grids
still get VS Code's own menu. Not yet seen in a running VS Code - jsdom
cannot build Tabulator, so the wiring is untested there; the menu and
`cellCopyText` are tested directly.

The error bar above the content follows the same reading: `errorsOf()`
looks at the **newest thing that happened** and reports what each of its
failing statements said, not the run's count of errors. A run that
worked clears it, and a run that failed before it could blame a
statement falls back to the run's own summary. An event that failed on
its own - a connection that could not be opened - has no children and
says it itself.

"Newest" is **not** the first row, and reading it that way was a bug
worth remembering: a run's row goes up before the connection it needs
has been opened, and opening one is itself logged, so on the **first**
execution on a connection an event sits in front of the run. The bar
stayed empty on that first run and appeared on the second - the
connection being open by then, with no event logged. `errorsOf()` steps
over everything that is neither a run nor a failure of its own, which is
the same rule `expandedIdsOf()` in the grid already followed.

The bar holds **one error at a time out of all of them**, and it opens
on the **first**, which is usually what caused the rest and is where
someone working through them starts. Two chevrons and an `n of m` step
between them, and a step is not only a change of text: it is the same
thing the arrow on a row does, putting the cursor on the statement and
opening the actions on the row that reported it, because stepping
through errors is for fixing them. Clicking the **message itself** does
the same, which is why it is a button drawn as text rather than text
with an arrow beside it - the whole message is the target.

An `×` closes it. What that has to survive is that state arrives
whenever *anything* happens on the connection, not only when a run
finishes: `showErrors()` therefore compares the new set against
`errorKey()` of the old one and does nothing when they match, so a
reader part way through the errors is not put back at the first, and a
bar they closed does not come back. A genuinely new set reopens it at
its first error. The controls are pinned to the top right of the bar
(`align-items: flex-start`), because a long message scrolls under them
and buttons that scrolled with it would be gone when they were wanted.

# What the server had to report for this

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
