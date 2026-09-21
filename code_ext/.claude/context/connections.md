# Connections

The connection editor, the several connections one URI can have open, the
Connections view in the activity bar, and the default connection.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The two connection
lists these work over, and the `--gui` tools that write them, are in
[shell-and-mcp.md](shell-and-mcp.md). What the result view does with the
activity reported here is in [result-view.md](result-view.md).

## The connection editor

`src/connections/` holds it, split so that only the panel needs VS Code:

| File | Purpose |
| --- | --- |
| `connectionUri.ts` | Fields <-> URI, and the allow-list of URI options. Pure. |
| `connectionStore.ts` | Add / edit / delete / test, over `IMariaDbApi`. Pure. |
| `editorProtocol.ts` | The messages the panel and its webview exchange. |
| `connectionEditorPanel.ts` | The panel, its HTML and the message loop. |
| `webview/src/ConnectionEditor.tsx` | The dialog itself. |

It is modelled on the MySQL Shell extension's `ConnectionEditor`: the same
three tabs (Basic, SSL, Advanced) in the same order, with the same captions
where the setting is the same one. **What is missing is missing on purpose** -
a connection here is stored as a URI and nothing else, so a setting that
cannot be written into one cannot be offered. That rules out the SSH tunnel
tab (the shell keeps `ssh-*` in `ssh_uri_connection_attributes`, a separate
set that never reaches a URI), the OCI/MDS tabs, `sql-mode` and the HeatWave
check. `URI_OPTIONS` is `uri_connection_attributes` from the shell's
`mysqlshdk/libs/db/utils_connection.h`, and is what tells a typo in the
"Other Connection Options" table from a real option.

Four things about it are load bearing:

- **`buildConnectionUri` emits the shell's own canonical spelling.** Every
  expectation in `connectionUri.test.ts` was produced by running the fields
  through the real `shell.unparse_uri`, then feeding the result back through
  `parse_uri`/`unparse_uri`, which returned it unchanged. Options are sorted
  because the shell's encoder sorts them; two URIs naming one connection must
  come out identical or the server sees two connections.
- **A password is never in the URI and never leaves the server.** Nothing can
  read a stored password back - no tool returns one - so the host sends the
  webview `hasStoredPassword`, not a value. `password: undefined` means "keep
  what is stored" and is NOT the same as `""`, which is a real password for an
  account that has none.
- **Editing is a re-key, not an update in place.** A connection is keyed by
  its URI and by which list it is in, so changing the host or the MCP checkbox
  moves it. That is what `db.update_connection` is for: it moves the secret
  server-side, so an edit does not make the user retype the password.
- **Saving does not verify**, as in the original. Test Connection is its own
  button; a server that is down must not stop its connection being configured.

The editor is **its own vite build** (`vite.editor.config.ts`), not a second
entry beside the result view. One build with two entries makes Rollup hoist
what they share into a common chunk, which each entry pulls in with a static
`import` - and a webview's `script-src 'nonce-...'` does NOT extend to a
module the entry imports, so that chunk would be refused and the view would
come up blank. One entry per build has nothing to hoist.

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
- A connection the tree has **not opened its own connection on has no
  children**, even where an editor has one open. `getChildren` never opens
  one, not even in the connect-on-open mode below: the tree asks a node
  for its children again on every refresh, so a connection that opened
  itself there could never be disconnected - the refresh that
  disconnecting fires would open it straight back up.
- A schema lists all seven object folders without querying anything; the
  query happens when a folder is opened.
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

Whether a node is drawn with a twistie is `IConnectionNode.expandable`,
which the model works out (`connected || connectOnOpen()`), not something
`treeItems.ts` decides: the shape of the tree stays in one place, and the
model is where it can be tested without VS Code.

## Default connection

Stored in the `mariadb.defaultConnection` setting — in the workspace where
there is one, so a project can default to its own database, and in the user
settings otherwise. Set and cleared from a connection's context menu in the
tree, and honoured by every SQL editor that has not picked its own.
