# Connections

The several connections one URI can have open, what is reported on them,
the cached connection list, the Connections view in the activity bar, its
folders, and the default connection. The connection editor itself is in
[connection-editor.md](connection-editor.md).

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The two connection
lists these work over, and the `--gui` tools that write them, are in
[shell-and-mcp.md](shell-and-mcp.md). What the result view does with the
activity reported here is in [result-view.md](result-view.md).

## Several connections on one URI

`db.connect` hands out a UUID per call, so one configured connection can
have several open at once, and `ConnectionManager` keeps them in a list of
`IOpenConnection` - the URI, the UUID, and a **label** saying which one it
is. The label is what the result view's second picker offers and what its
Conn column shows; the UUID never leaves the manager.

There are two producers, and they deliberately do not share:

| Label | Who opens it | Why it is its own |
| --- | --- | --- |
| `1`, `2`, … | An editor running SQL | The user's session: its temporary tables, its current schema, its open transaction. |
| `UI Backend` | The Connections view | Walking the tree neither waits behind whatever an editor is running nor disturbs its session state. |

- `connect(uri)` reuses the lowest numbered connection open on the URI,
  so an editor goes on running on the one it has been running on, and
  `connect(uri, name)` reuses the named one. `UI_BACKEND_SESSION` is that
  name, and the tree, the `mariadb.connect` command and
  `ConnectionsTreeProvider.expanded` all pass it.
- **`labelFor(uri)` answers before anything is opened.** The result view
  puts a run up the moment the user asks for it, which is before the
  connection it runs on has been opened - so the label has to be knowable
  in advance, and it is the one `connect` will then pick.
- `disconnect(uri)` closes **every** connection open on the URI, which is
  what Disconnect in the tree means; `disconnect(uri, name)` closes one.
- `isConnected(uri)` asks whether any is open, `isConnected(uri, name)`
  about one. The tree's rows use the first - what a row says, and what
  disconnecting it closes, is the whole of what is open on the connection
  - and its children the second, since that is what it browses on.

## What happens on a connection is reported

Everything done on an open connection becomes a row of that connection's
actions, not only the SQL an editor runs on it. `connectionActivity.ts`
holds the shape (`IActivityEvent`), the row it becomes (`activityRow`) and
the wrapper that produces them:

- `ConnectionManager` reports the two ends itself - `db.connect` before
  there is a UUID to look a connection up by, `db.close` after there is no
  longer a connection to look up.
- `createLoggingApi` wraps the calls that browse one: `db.list_schemas`,
  `db.list_objects` and `db.get_object_details`. It times each, says what
  came back (`Listed 12 tables in world`) and reports a failure as the row
  instead. A UUID it cannot resolve - one closed while the call was in
  flight - is reported as nothing at all.
- **General Actions** (`mariadb.actions.logAllCalls`, off by default):
  the calls made on NO open connection - `db.list_connections` and the
  add/update/delete/test tools - are reported too, under the pseudo
  connection `GENERAL_ACTIONS` (`"General Actions"`, empty label), which is
  how they get an entry of the panel's connection picker. The setting is
  `IConnectionSettings.logAllCalls?()`, read on EVERY call through
  `createLoggingApi`'s 4th argument, so turning it on needs no restart. Off
  by default because the list is read constantly (every tree refresh, the
  picker's own listing). The call text names the arguments given; a
  password is never in it - `update_connection` shows `password=***` only
  to say one was given. Calls ON a connection still go under it alone, so
  nothing is logged twice. The `sandbox.*` calls are logged here too (see
  [sandboxes.md](sandboxes.md)); `createGeneralWatcher` is shared by both
  wrappers.
- **General Actions shows until a connection reports.** With the logging on
  it is usually the first thing logged, and the panel used to stay empty
  because only a connection's event picked what the view shows. Now the
  first event of ANY kind does; General Actions picked that way
  (`#generalByDefault`) gives way to the first connection that reports
  something, while General Actions picked by the user stays.
- `ConnectionManager.api()` is what hands the wrapped API out, so
  everything that goes through the manager is reported and nothing has to
  remember to do it. The list tools are passed straight through: they work
  on the configured list, not on anything open.
- **Running SQL reports itself.** A run is a whole tree of rows - the
  statements, their times, what each came to - which no wrapper could
  build from a call and its return value, so `db.execute_sql_script` is
  passed through. The two uses of it that are not a run are the schema
  probe behind an unqualified table name, which is silent, and writing a
  grid's edits back, which the result view reports as an event of its own.

## The connection list is read once

`ConnectionManager.listStoredConnections()` caches the list (`#stored`, the
in-flight promise, so concurrent callers share one read) and asks the server
- ONE `db.list_connections(kind="all")`, whose entries carry their `kind`;
a released shell refuses `"all"`, and `connectionStore.listConnections` then
falls back to one call per list (also when an answer's entries name no
kind - without the fallback the view would break on every shell out
today) - ONLY:

- the first time anything needs it;
- after `invalidateStoredConnections()`, which the view's **Refresh** button
  calls, and which every `addConnection` / `updateConnection` /
  `deleteConnection` made through `api()` calls on its own (the manager wraps
  the API with `#invalidating`, success or failure - a failed update may have
  changed something). One reload after a batch, not one per row.

Before this every tree redraw re-listed, and the tree redraws on every
connection opened or closed; with General Actions logging on, the list
events redrew the panel, which re-listed for its picker, which logged more.
Everything - tree roots and folders, the panel picker (its own 5 s TTL on
top is now redundant but harmless), the editor's folder list
(`IConnectionEditorHost.listStored`), SQL-file connection choice, a folder
move - reads the cache. A read that FAILS is not cached, so the next call
retries. Known cost: a connection another client adds (`mcp.setup`, an
agent's `sandbox.deploy`) shows up at the next Refresh.

## Connections view

Contributed into its own activity bar container, whose icon is the MariaDB
seal (`images/mariadb-seal.svg`, kept as a vector). The tree is:

```
dba@localhost:3310/world      connection (icon by scheme; "default" if default)
└── world                     schema
    ├── Tables                object group, one per supported type
    │   └── city              object
    ├── Views / Functions / Procedures / Sequences / Triggers / Events
```

- The shape of the tree lives in `src/tree/connectionsModel.ts`, which is
  pure data and has no VS Code import; `connectionsTreeProvider.ts` only
  maps its nodes onto items.
- A connection the tree has **not opened its own connection on has no
  children**, even where an editor has one open. `getChildren` never opens
  one, not even in the connect-on-open mode below: the tree asks a node
  for its children again on every refresh, so a connection that opened
  itself there could never be disconnected - the refresh that
  disconnecting fires would open it straight back up.
- A schema lists all seven object folders without querying anything; the
  query happens when a folder is opened.
- **A connection row is labelled `connectionLabel(uri)`**: the URI without
  its scheme and without its `?options`, so `user@host:port/schema`. The
  scheme is shown by the icon instead - `connectionMariaDB`,
  `connectionMariaDBSSH`, `connectionMySQL`, `connectionMySQLSSH` (`mysqlx`
  borrows the MySQL one) - and the whole URI is the tooltip and what
  Copy Connection URI puts on the clipboard. Two connections differing only
  in scheme or options can therefore share a label; it is a caption, never
  a key - everything still goes by `node.uri`. `mariadbConnection.svg` is
  now only the view container's icon in `package.json`.
- Icons are the MySQL Shell extension's, copied into `images/light` and
  `images/dark`. The upstream set has no sequence icon, so sequences fall
  back to the `symbol-numeric` codicon. Upstream's `light/schemaPrcoedure.svg`
  typo was corrected to `schemaProcedure.svg` on copy.
- A connection item's `contextValue` is
  `mariadbConnection.<connected|disconnected>.<default|notDefault>`, which
  is what the context menu switches its entries on. The connection's LIST is
  deliberately **not** a fourth segment: the `when` clauses anchor on the
  third (`/notDefault$/`, `/\.default$/`) and would break. Edit and delete
  are handed the node itself, which carries `connectionKind`.
- A connection in the shared MCP list is described `MCP` in the tree, and
  `MCP, default` when it is both. The view lists **both** lists: the checkbox
  says who else may open a connection, not whether this extension can.
- A schema is described by its type **only where that is not `User
  Schema`**, which is what `db.list_schemas` calls everything that is not
  the server's own. Nearly every row is one, and a column of the same
  word beside the schemas the user came for says nothing; `System Schema`
  and `System Information Schema` are the ones worth marking. The
  tooltip still names the type, so it is there to be read.
- The view's welcome content is **four messages**, because an empty tree
  means different things. The list comes from the MCP server, which has
  to be found - or downloaded - and started first. What picks between
  them is the `mariadb.connectionsView` context key
  (`CONNECTIONS_VIEW_STATE_CONTEXT_KEY`), which `ConnectionsTreeProvider`
  sets from its own listing and from the `ServerStarter`'s phase:

  | state | when | says |
  | --- | --- | --- |
  | `looking` (and unset) | server being found or started | `Looking for MariaDB connections...` |
  | `installing` | the installer is running | downloading and installing, + Show Log |
  | `failed` | listing failed, or the server did not start | the log says why, + Show Log and Retry |
  | `listed` | the roots came back | nothing configured, + New Connection… and New Folder… buttons |

  `failed` rather than `listed` on a failure: the view used to claim
  nothing was configured when the shell could not even be installed.
  A new start attempt (phase `locating`) clears a listing failure back
  to `looking`. The view also shows its busy bar for the whole startup
  (`withProgress` at `{ viewId }`, in `showStartupInView`). It cannot
  show the failure's text: setting `TreeView.message` hides welcome
  content (VS Code's `shouldShowWelcome` requires it empty), so the
  reason goes to the notification and the log instead. It used to be
  one message sending the user to `mariadb-shell -- mcp setup` in a
  terminal, which the connection editor has since made unnecessary.
  `SqlEditorBinding.selectConnection` points at the same button when a
  SQL file is asked what to run on and nothing is configured, naming it
  in words rather than the codicon, a notification drawing none.

## Folders

A connection can be filed in a folder - `/Sandboxes`, `/Sandboxes/note_app` -
which the MCP server stores in the connection's key and reports from the GUI
`db.list_connections` as `{ uri, path }` (a server that predates folders
answers bare URIs, read as `/`). The folder is presentation only: everything
still goes by URI and kind.

- `connectionFolders.ts` mirrors the server's `normalize_connection_path`
  (`normalizeFolder`, `folderProblem` for the `:` it refuses, `allFolders`
  for every folder a set of paths implies, parents included).
- `IMariaDbApi.listConnections` still returns URIs, for its many callers;
  `listConnectionEntries` is the same tool with the folders.
  `addConnection(..., path)` and `updateConnection(..., newPath)` send the
  folder ONLY when there is one to send - a top-level add sends none, an edit
  sends `new_path` only when it changed - so an older server keeps working
  wherever folders are not used.
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
  folder (a connection's, or the folder itself; the toolbar means the top);
  a `/` in the name nests.
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
  none. The folder is added to the created set first, so it survives a move
  that fails, then `ConnectionsTreeProvider.fileInFolder` moves them.
- The editor's **Folder** field (Basic tab) offers every folder in use through
  a `<datalist>`; the panel lists them on `ready`, and a failed listing just
  leaves the list empty. A `:` marks the field invalid at once, and
  `saveConnection` refuses it before anything is sent.

## Opening a connection

Two modes, set by `mariadb.connections.connectMode`:

- **`onOpen`**, the default. A closed connection is drawn with a twistie
  and expanding the row opens it. The trigger is the tree view's
  `onDidExpandElement`, wired in `extension.ts` to
  `ConnectionsTreeProvider.expanded` - an expand the *user* performed, as
  against `getChildren`, which the tree also calls on every refresh. That
  method opens the connection and returns nothing: the children arrive
  with the refresh the manager fires when the connection opens.
- **`explicit`**. What it did before: a closed connection has no twistie
  and only the Connect button or command opens it. A connection something
  else already has open is the exception: expanding it opens the tree's
  own connection in this mode too. What the mode is about is not
  connecting to a server behind the user's back, and the server has been
  connected to; all this adds is the connection the tree browses on,
  without which the row would expand to nothing and stay that way.

The mode reaches the menus as the `mariadb.connectOnOpen` context key,
published by `publishConnectMode` on activation and on every change to the
setting. Its one job is to drop the inline Connect button from a row in
the `onOpen` mode, where it duplicates the twistie; the context menu
entry stays in both modes, since the palette and the keyboard need it.
Disconnect is untouched - nothing implicit closes a connection.

**Opening shows itself under the row.** `ConnectionsTreeProvider.#open`
(behind both `expanded` and `retry`) records an `IOpenAttempt` per URI and
redraws the row at once, and while the tree's own connection is not open
`ConnectionsModel` answers the row's children with ONE
`IConnectionStatusNode` instead of `[]`: `Connecting...` with the
`loading~spin` codicon, then - on failure - the reason's first line with
the `error` codicon (whole text in the tooltip) and an inline Retry
(`mariadb.retryConnection`, on `viewItem == mariadbConnectionStatus.failed`).
A failed open is logged but NOT notified any more: the user is looking at
the row. A second expand while one is connecting is ignored. The status
node carries its `parent` row because VS Code knows elements by identity,
and a retry has to redraw the very object the tree holds. Success deletes
the attempt; the manager's own change event has already listed the schemas.
The Connect command does not use this - its row is collapsed, so there is
nothing to show a spinner in.

Whether a node is drawn with a twistie is `IConnectionNode.expandable`,
which the model works out (`connected || connectOnOpen()`), not something
`treeItems.ts` decides: the shape of the tree stays in one place, and the
model is where it can be tested without VS Code.

## Default connection

Stored in the `mariadb.defaultConnection` setting — in the workspace where
there is one, so a project can default to its own database, and in the user
settings otherwise. Set and cleared from a connection's context menu in the
tree, and honoured by every SQL editor that has not picked its own.

**It is the one URI the extension writes down itself, which is why it needs
`withDefaultScheme`.** A setting written before shell 26.9.3 holds a
scheme-less URI, and `db.list_connections` now reports every connection with a
scheme, so a plain `===` would stop matching and the tree's default marker
would quietly disappear — nothing fails, the star is just gone.
`ConnectionsModel.getRoots` puts BOTH sides through the fill-in, which also
covers the mirror case: a setting written now, against a connection still
stored without one. Everything else that reads the setting hands it to
`db.connect`, which resolves it server-side, so it needs nothing.
