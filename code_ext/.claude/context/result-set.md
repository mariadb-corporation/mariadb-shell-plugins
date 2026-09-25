# The result set

What one result set can do beyond its grid: the toolbar along its
bottom, paging through its rows, and moving it into an editor tab.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Split out of
[result-view.md](result-view.md); the grid itself is in
[result-grid.md](result-grid.md).

## The result set's toolbar

`ResultStatusBar` copies the layout of the MySQL Shell's
`ResultTabView` (`gui/frontend/src/components/ResultView/` in
mysql-shell-plugins) and its icons, copied to
`images/light/toolbar-*.svg`:

```
status text ...... View: [grid v] | Pages: [<][>] | Edit: [edit][add-row][sql_preview][commit][rollback][refresh] | [maximize] | [menu]
```

- The `|` are 1px lines, 16px high, 6px either side, in
  `--vscode-panel-border` (the MySQL Shell's are invisible spacers; lines
  were asked for). The section labels are 0.8em. Buttons are 20px,
  icons 18px.
- The icons are drawn as **CSS masks over `currentColor`**, as the MySQL
  Shell draws them, so they take the button's colour in every theme and
  only the **light** files are used: `styles.css` sets `--icon-<name>`
  on `body` and `.<name>Icon::before` uses it as `mask-image`. The webview
  build has `assetsInlineLimit: 0`, so they are served as files, under
  `dist/webview/icons/light/`.
- **Every** MySQL Shell toolbar icon (54) is copied to
  `images/light/toolbar-*.svg`, with a dark copy in `images/dark/` (the
  `<svg>` style gets `fill:white;`, and the inner `fill:rgb(34,34,36)`
  declarations, which would override it, are dropped). Most are unused
  for now. The four names that already existed here -
  `toolbar-execute`, `toolbar-execute_caret`,
  `toolbar-stop_on_error-active` / `-inactive` - differ from the MySQL
  ones and were NOT overwritten: they are the SQL editor toolbar's.
- **View** is a dropdown (`ToolbarMenu`) of Data Grid / Preview Changes,
  showing the icon of what is on show. Preview Changes is off while
  nothing is pending.
- **Edit**: Start Editing opens the first editable cell of the first row
  not marked deleted (`ResultGrid`'s `editRequest` counter); off on a
  read-only result set, whose `readOnlyReason` is its tooltip - there is
  no "read only" label any more. Preview, Apply and Rollback are off
  while nothing is pending; **Refresh is off while something is**, as in
  the MySQL Shell, since a reload would drop the edits.
- The status text says what is pending the MySQL Shell's way
  (`editingStatusOf`: "Editing, 1 row affected (1 field changed, ...)"),
  in place of the old badge and "Apply (n)".
- **Add New Row** is a toolbar button after Start Editing
  (`toolbar-add-row.svg`, the user's own); the preview icon is
  `toolbar-sql_preview.svg`. Section labels are 0.8em.
- The **action menu** holds Close Result Set
  (`closeResult`: drops the panel tab, or closes the editor tab without
  putting it back). The MySQL Shell's disabled Export / Import items are
  left out.
- **Pages** (between View and Edit, as in the MySQL Shell): Previous and
  Next, with the MySQL `toolbar-page_previous` / `-page_next` icons. See
  "Paging" below.
- `ToolbarMenu` opens UPWARDS and is `position: fixed`, placed from the
  button's rect: the bar is `overflow: hidden` and runs along the bottom.

## Paging

`mariadb.execute.pageSize` (default 200, `settings.ts` `pageSize()`, which
falls back to 200 for anything that is not a whole number >= 1) is passed
as `limit` to `db.execute_sql_script` on every run (`IExecutionOptions.
pageSize`). The **server** does the rest: it adds `LIMIT size+1` to each
SELECT that can take one, drops the extra row and reports
`has_more_pages` (see `mcp_plugin`'s db-tools.md). So the extension never
sees the extra row, and every count - the status, the actions' "N rows in
set" - is the page's own.

- A result set the server paged carries `IResultSet.page` (`index` from
  0, `size`, `hasMore`, `loads`). One without it - its own LIMIT, a CALL,
  an older shell - holds every row, and the Pages buttons are off.
- Status: `pageStatus()` - `200 rows in set (page 2)`; the page is named
  only when there is more than one.
- Next / Previous post `page`; the provider's `#fetchPage` calls
  `ExecutionService.fetchPage` -> `db.execute_sql` with `limit` and
  `offset = index * size`, on the result set's apply context, and
  replaces the result set IN PLACE (same id, so jump arrows keep working;
  `loads` + 1). It is logged as an event row ("Page 3: ..."). A failure
  keeps the page on show and sends `pageFailed`, shown in the error bar.
- Maximized tabs page the same way, in their own tab.
- The page's grid edits are rebuilt when `index` or `loads` change
  (`pageKeyOf` in App.tsx); paging is off while edits are pending, as
  Refresh is. After an apply, a paged result set reloads the PAGE it is
  on rather than re-running the statement back to page 1.

## Maximizing a result set

The toolbar's **Maximize** button (after Refresh, as in the MySQL Shell) Maximize moves the
result set out of the panel into an **editor tab of its own**
(`MaximizedResult` in `src/webview/maximizedResult.ts`, a `WebviewPanel`
of type `mariadb.maximizedResult`), and there the button is **Minimize**,
which puts it back.

- **Nothing is run again.** The host already holds every result set's
  rows, so it moves the `IResultSet` object across. The page posts its
  grid's `IEditableRow[]` with `maximize` / `minimize`, and the host hands
  them to the other side as `editing` on the next `state`, so pending
  edits travel too.
- It is the **same frontend**, told by `IViewState.maximized` to show its
  one result set with no tab row and no pickers. The error bar stays (a
  failed refresh or apply), but stepping to an error does not switch to
  an Actions tab it does not have.
- It **leaves the panel's tabs** while it is away and goes back **where
  it was** (`IMaximizedOrigin.position`), on its connection and on the
  connection open on it that produced it - the view is switched there if
  another one's tabs are on show.
- **Refresh** in the tab re-runs the statement as a normal run (it is in
  the actions), but `startRun(..., into)` / `#runInto` send its result to
  the tab instead of replacing the panel's tabs, and the panel is not
  revealed. The tab holds one result set, so a CALL's refresh keeps the
  first; a failed refresh leaves the old rows, with the error in the bar.
- **Apply** writes through the tab's own apply context and replies to the
  tab. `#applyChanges` takes the result set, context, label and reply
  function for that reason.
- The tab's **title** is the statement's start, cut at `TITLE_LENGTH`
  (30) with an ellipsis - not the panel's `Result #1`, which every run
  numbers from one. A tab opened with a title keeps it across refreshes.
- **Select Rows** on a table or view (`mariadb.selectRows`) opens a run
  straight into a tab: `runScript(..., into)` takes a `RunInto`, a
  maximized tab's key or `{ title }` for a new one. The run is logged and
  the panel's tabs are left alone; its first result set opens titled
  `schema.name` and minimizes to the FRONT of its connection's tabs. A
  run that produced no result set (it failed) opens nothing and brings
  the panel up on its error instead.
- **Closing the tab** with its X discards the result set, as closing a
  tab does; only Minimize brings it back. Disposing the provider closes
  every such tab.
- When the id set of the panel's tabs changes, the page now **keeps the
  edits of the tabs still there** and shows a tab that has just arrived
  (a returning one, or a run's first); before, any change rebuilt all of
  them.

Maximize and minimize use the user-supplied
`images/light/{maximize,minimize}.svg`, as masks like the others.
