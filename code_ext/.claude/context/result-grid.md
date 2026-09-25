# The result grid

The grid a result set is shown in: Tabulator and its traps, how values
are shown, the cell menu, deleting rows, freezing the primary key, saving
and loading a BLOB, and opening any value in an editor.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Split out of
[result-view.md](result-view.md), which has the view around it; the
result set's toolbar, paging and maximizing are in
[result-set.md](result-set.md).

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

**Values are shown as the MySQL Shell shows them** (`formatValue` in
`ResultGrid.tsx`, by `IResultColumn.display`):

- NULL, and a BLOB, a spatial value or a VECTOR, are its data icons
  (`data-null`, `data-blob`, `data-geometry`, `data-vector`.svg, copied to
  `images/{light,dark}/`), drawn as a mask in `--vscode-badge-foreground`
  over the cell's own word ("NULL", "BLOB", ...), which stays for copying
  and screen readers. `data-json.svg` is copied too but unused: the MySQL
  Shell shows JSON as text, and so does this.
- BINARY / VARBINARY is `0x` and its hex, cut at 64 digits with an
  ellipsis (`BINARY_DIGITS_SHOWN`). The hex is the server's; editing takes
  it with or without the `0x`.
- `display` comes from `valueDisplayOf(serverType, columnType)`
  (`dataTypes.ts`): the table's COLUMN_TYPE where it was looked up - the
  only way to tell a VECTOR, which the server reports as `BYTES` like a
  VARBINARY - and otherwise the server's `column_types`, which every
  result has. So a VECTOR shows as its icon only in an editable
  single-table result; elsewhere it is hex.
- GEOMETRY and VECTOR cells get no text editor (`editableAsText`): their
  hex would be written back as a quoted string. BINARY and BLOB can, as
  hex (`literalKind` "binary").

**Rows are deleted from the cell menu** - its last item, Delete Row, or
Restore Row on a row already marked (disabled in a result that cannot be
edited). There is no row header column any more; a marked row shows only
by its red, struck-through cells.

**Primary key columns can be frozen** (`mariadb.resultSet.freezePrimaryKeyColumns`,
default true, `settings.ts` `freezePrimaryKeyColumns()`, sent as
`IViewState.freezeKeyColumns`). Each result set starts from the setting
when it is first shown and can switch it for itself with the action
menu's checkable "Freeze Primary Key Columns" (`IEditingState.freezeKeys`;
another page of it keeps its choice; off where no key is known, i.e. the
table was not looked up). `orderColumns` moves the key columns to the
FRONT when frozen - Tabulator freezes only at the left edge, and a frozen
column after an unfrozen one would be frozen at the right - and marks them
`frozen`; toggling rebuilds the table (`freezeKeys` is a dependency of the
build effect). Frozen cells get an opaque background: `--grid-surface`
underneath and the row's state (`--frozen-tint`: hover, selection, added,
deleted, changed) as a gradient layer over it; a deleted row's frozen
cells are dimmed by colour, not opacity, which would show through.

**A BLOB's value can be saved to and loaded from a file**, two ways:

- An **overlay** over a BLOB cell while the pointer is on it: Save and Load
  buttons (`blobCell`, the MySQL `toolbar-save` / `toolbar-load` icons as
  masks). They stop their own mousedown/click, or the cell would open its
  editor or select its row.
- A **cell menu** on right-click, on every result-grid cell (Tabulator's
  `cellContext` -> `openContextMenu`, the generalized form of the actions
  grid's Copy menu): Save Value to File..., Load Value from File...,
  separator, Set Field to Null. Items that do not apply are disabled.

`cellActionsOf` holds the MySQL Shell's rules for both: save and load are
for BLOB columns only (not BINARY/VARBINARY); save needs a value; load and
Set Field to Null need an editable result, a column that is not generated
and a row not marked for deletion, and Set Field to Null a nullable column
that is not NULL already. A loaded file and a NULL are ordinary pending
edits, written by Apply.

The file work is the host's (`src/webview/valueFiles.ts`), through VS
Code's save and open dialogs and `workspace.fs`: `saveValue` carries the
cell's value as the grid holds it (hex) and `bytesOf` turns it into the
BYTES it stands for - unlike the MySQL Shell, which saves its base64 text.
`extensionFor` guesses the file type from the content (png, jpg, gif,
pdf, zip, gz, webp, svg, txt, else bin) for the suggested name and the
filter; the name is `<table>-<column>-<row>`. `loadValue` is answered by
`valueLoaded` with the file as hex, or an error for the error bar; the page
keys pending loads by request, so an answer lands on the right result set
even after a tab switch. Not covered by a test: the grid click starting a
load (Tabulator does not build under jsdom); the answering half is.

**Any value opens in an ordinary VS Code editor** (Open Value in Editor:
first item of the cell menu; the first overlay button on a BLOB; the only
overlay button on a JSON value and on text with a line break or more than
`LONG_TEXT` (80) characters - `worthOpening` / `openableCell`). Not a
webview editor: a webview cannot reach VS Code's Monaco, and bundling
Monaco measured +5.0 MB (JSON only) to +14.1 MB, against 1.97 MB for
all of `dist/` today. The MySQL Shell's own field editor is a dialog of
plain textareas, and disabled.

- `ValueDocuments` (`src/webview/valueDocuments.ts`) is a
  `FileSystemProvider` for the `mariadb-value` scheme, registered in
  `extension.ts` and owned by the provider. `openValue` puts the value
  under `mariadb-value:/<resultId/row/column>/<table>-<column>-<row>.<ext>`
  and runs `vscode.open` - which picks the editor by type, so an image
  opens in VS Code's image preview. The extension is the language:
  `extensionOfValue` - JSON for a JSON column (`display: "json"`, from the
  server's `JSON` type or the column type) and for text that parses as
  JSON, XML for text that starts like it, the sniffed type of a binary
  value (`extensionFor`), else txt.
- **Where it opens**: from a maximized result set, `ViewColumn.Beside` -
  the group to its right, created if there is none; from the panel,
  `ViewColumn.Active`, as a tab of its own. (The secondary sidebar was
  asked about: it holds views, never editors.)
- **Saving writes back to the grid, not to disk**: `writeFile` turns the
  bytes into what the grid holds (hex for a binary kind, UTF-8 text
  otherwise) and sends `valueEdited` to the page it came from, then WAITS
  for `valueEditResult` (`VALUE_EDIT_TIMEOUT_MS`, 10 s). The page refuses
  when the result set is gone, the page (`pageKey`) changed, or the row is
  marked for deletion; the refusal fails the save with that reason. An
  accepted value is a pending edit, written by Apply. Text equal to what
  the cell already said (`"1"` for the number 1) is not an edit.
- **Read only** (`FilePermission.Readonly`, `cellActionsOf().openReadOnly`):
  a result that cannot be edited, a generated column, a deleted row, and
  GEOMETRY / VECTOR (their hex cannot go back as text).
- A document is forgotten when its text editor closes; an image editor
  does not fire that, so a picture's bytes stay until the view goes.
  Re-opening a cell replaces its document and reloads an open editor.
  An edit made in the grid after opening does not reach the editor.

The rest of the grid's styling is ours; the MySQL Shell's was tried and
reverted.

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
