# Connection folders

How the Connections view files connections into folders: the folders the
server knows as a connection's path, the ones made with New Folder, moving
and renaming, and what is kept across a restart.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Split out of
[connections.md](connections.md) on 2026-10-11, which has the rest of the
Connections view.

A connection can be filed in a folder - `/Sandboxes`, `/Sandboxes/note_app` -
and given a caption and a color. The MCP server keeps all three in
`connections.json`, outside the secret store (the key is only prefix + URI),
and reports them from the GUI `db.list_connections` as `{ uri, path, kind,
caption, color }` (a server that predates folders answers bare URIs, read as
`/`; one that predates captions leaves them out). They are presentation only:
everything still goes by URI and kind.

- `connectionFolders.ts` mirrors the server's `normalize_connection_path`
  (`normalizeFolder`, `allFolders` for every folder a set of paths implies,
  parents included). Any folder name goes except the `/` separator, and a
  folder has no length limit.
- `connectionDetails.ts` mirrors the rest: `connectionKeyProblem(uri)` (see
  below), `captionProblem` / `normalizeCaption` (one line, <= 100 chars,
  trimmed) and `isConnectionColor`. The colors are `CONNECTION_COLORS` in
  `mcp/types.ts` - red, orange, yellow, green, blue, purple, the server's
  names.
- `IMariaDbApi.listConnections` still returns URIs, for its many callers;
  `listConnectionEntries` is the same tool with the details (a color it does
  not know is dropped). `addConnection(..., details)` and
  `updateConnection(..., newDetails)` take an `IConnectionDetails`
  `{ path?, caption?, color? }` and send each field ONLY when it is set - a
  top-level add with no caption sends none, an edit sends only what changed
  (`saveConnection` compares with `original`) - so an older server keeps
  working wherever they are not used. `""` clears a caption or color.
- **A row shows the caption as its label** with the address moved into the
  description (`address · MCP, default`), and the caption on the tooltip's
  first line. **The color** cannot tint the SVG connection icons, so a
  colored row gets a `resourceUri` of scheme `mariadb-connection`
  (`colorUriOf` in `tree/connectionColors.ts`), and
  `ConnectionColorDecorations`, registered in `activate` as a file
  decoration provider, colors its label with the theme's `charts.<color>`
  and adds a `●` badge. Rows are still in listing order, not sorted by
  caption.
- The editor's Basic tab has **Caption** (first field) and **Color** (a
  radiogroup of swatches drawn with `--vscode-charts-<color>`, plus None). The
  Color block is a `div.field`, not a `Field`: a `<label>` hands any click in
  it to its first button.
- `ConnectionsModel.getRoots` returns the top-level folders (sorted), then the
  top-level connections; a folder's children are its subfolders then its
  connections, of BOTH lists. A folder exists only as a path something is
  filed under - one holding only folders is shown, an empty one cannot be.
  Folder children come from the listing `getRoots` made (`#listing`), so a
  folder and its roots cannot disagree and opening one costs no server call.
- `FolderTreeItem` is drawn EXPANDED, so a connection that used to be in
  sight (a sandbox, now in `/Sandboxes`) does not vanish into a closed
  folder. Its icon is `folder-opened` or `folder` by state, which VS Code
  does not swap itself: the provider keeps the COLLAPSED paths and redraws
  the folder on a change (`onDidCollapseElement` -> `collapsed()`,
  `onDidExpandElement` -> `expanded()`, which drops that folder's own entry
  and not those of the folders inside it).
- **The collapsed folders persist**, because VS Code does not restore an
  extension tree's expanded rows across a restart. They are a `FolderSet`
  under `COLLAPSED_FOLDERS_KEY` in `globalState` - global, like the
  connections, so every window shows the same tree. Collapsed rather than
  open because open is the default: a new, moved or renamed folder comes up
  open with nothing to look up, and a stale entry changes nothing.
  `getTreeItem` reads it for both the state and the icon, so the first draw
  after a restart is right; `#refile` moves entries with a folder (rename
  and drop), Remove Folder drops them. Folder rows carry `id`
  `folder:<path>` (unique) so VS Code tells them apart across refreshes;
  connection rows get no id, since two can share a label. `contextValue` `mariadbFolder` puts Add
  Connection on it (inline and in the menu), which opens the editor with the
  folder filled in (`ConnectionEditorPanel.show(..., newIn)`).
- **Empty folders live in the extension.** The server knows a folder only as
  a connection's path, so one made with **New Folder…** is kept in
  `globalState` (`FolderSet`, key `mariadb.connections.folders`) and
  merged into the tree by the model's `customFolders` getter. `IFolderNode.
  empty` (nothing filed at or below it) makes the `contextValue`
  `mariadbFolder.empty`, which is the only row offering **Remove Folder** -
  a folder with connections in it is where they are filed, not a thing of
  its own. The menus match folders with `/^mariadbFolder/` for that reason.
  New Folder asks for a name with `showInputBox`, relative to the row's
  folder (a connection's, or the folder itself); the toolbar's New Folder
  and New Connection go by the view's SELECTION instead, the top level when
  nothing is selected - VS Code hands a toolbar command only the focused
  row (see `commands.md`). A `/` in the name nests.
- **Multi-select and drag and drop.** The view is created with
  `canSelectMany` and the provider as its `dragAndDropController`, on a MIME
  of its own (`CONNECTIONS_DRAG_MIME`, `application/vnd.mariadb.connections`)
  - NOT the tree's `application/vnd.code.tree.<id>`, which VS Code fills in
  itself with the handles of whatever is dragged: reading that back as
  connections failed with "dragged.map is not a function" on a folder drag.
  The drop also checks the payload is an array of rows.
  `handleDrag` carries CONNECTION and FOLDER rows (not what is under an open
  connection, which is its database). A folder moves WHOLE and keeps its
  name: every stored connection in or below it (listed fresh, so a closed
  folder's too) is re-filed with `rebase(path, from, to)`, then
  `FolderSet.move` carries the empty folders made inside it. Ignored: a
  folder dropped into itself, below itself or into its own parent; a folder
  inside another dragged folder, and a connection inside a dragged folder,
  both move with that folder rather than being flattened. Same-named folders
  at the target simply merge. `handleDrop` files into the folder dropped
  on, the folder of the connection dropped on, or `/` for empty space, and
  ignores a drop on anything else. `fileConnections` does one
  `db.update_connection(..., new_path)` per connection, skipping those
  already there, so a failure part way leaves each in one place; the tree
  is redrawn either way.
- **Rename Folder…** (any folder) asks for one name - a `/` is refused, since
  moving is what a drag does - and `ConnectionsTreeProvider.renameFolder`
  moves the folder to `<parent>/<name>` through the same `#refile` a folder
  drop uses: every connection in and below it re-filed, the empty folders
  inside it carried along, a namesake merged into.
- **There is no menu on the view's empty space, and none can be added**:
  VS Code's only view menus are `view/title` and `view/item/context`
  (checked in the workbench bundle). New Connection… and New Folder… are on
  every FOLDER row (`0_new`), in the toolbar, and - for a view with no rows
  at all - as buttons in the `listed` welcome content, which is exactly when
  a tree shows it. Connection rows do NOT carry them.
- **New Folder with Selection…** (connection rows) is handed the clicked row
  and the whole selection, as a menu on a `canSelectMany` tree is; it keeps
  the connections, asks for a name (`askForNewFolder`, shared with New
  Folder), and makes the folder INSIDE `commonFolder` of their paths - the
  folder they are in, or the deepest one a mixed selection shares, `/` if
  none. "The folder a row works in" is `folderOf(node)` in
  `connectionsModel.ts` (a folder row's path, a connection row's folder,
  else undefined), used by every folder command in `extension.ts` and by
  the provider's drop target; it was spelled out three ways before. The folder is added to the created set first, so it survives a move
  that fails, then `ConnectionsTreeProvider.fileInFolder` moves them.
- The editor's **Folder** field (Basic tab) offers every folder in use through
  a `<datalist>`; the panel lists them on `ready`, and a failed listing just
  leaves the list empty.

- **The stored key is at most 256 bytes**, which the server enforces (its
  Windows credential helper cannot store more; see mcp_plugin's
  `context/connections.md`). Only the URI counts: it gets
  `CONNECTION_KEY_BUDGET` (247, the prefixes being 9 bytes) in UTF-8 bytes,
  checked by `connectionKeyProblem(uri)`. The editor shows it live under the
  URI box and refuses Create/Save; `saveConnection` refuses it too. It
  measures the URI as BUILT, and the server normalizes (adds `:3306` when no
  port is given), so the server's refusal stays the last word. Moving,
  renaming and New Folder with Selection need no check at all any more - a
  folder is not in the key.
