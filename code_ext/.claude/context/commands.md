# Commands

Every command the extension contributes, and where it appears.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

| Command | Title | Where |
| --- | --- | --- |
| `mariadb.refreshConnections` | Refresh | Connections view title |
| `mariadb.toolbarAddConnection` | New Connection… | Connections view title (`+`). In the selection's folder, the top level when nothing is selected; hidden from the palette |
| `mariadb.editConnection` | Edit Connection | Connection context menu |
| `mariadb.deleteConnection` | Delete Connection | Connection context menu |
| `mariadb.addConnection` (on a folder) | New Connection… | Inline on, and context menu of, a folder: the new connection starts in it |
| `mariadb.newFolder` | New Folder… | Context menu of folders; the palette and the empty view's welcome link (top level). Prompts for a name, relative to that folder |
| `mariadb.toolbarNewFolder` | New Folder… | View toolbar. In the selection's folder, the top level when nothing is selected; hidden from the palette |
| `mariadb.newFolderWithSelection` | New Folder with Selection… | Context menu of connections. Prompts for a name and files the selected connections in the new folder, inside the folder they share |
| `mariadb.renameFolder` | Rename Folder… | Context menu of every folder. Re-files everything in and below it |
| `mariadb.removeFolder` | Remove Folder | Context menu of an EMPTY folder only |
| `mariadb.retryConnection` | Retry | Inline on, and context menu of, a connection's failed-to-open row |
| `mariadb.copyConnectionUri` | Copy Connection URI | Connection context menu (the whole URI, which the tree shortens) |
| `mariadb.connect` | Connect | Connection context menu; the inline button only in the explicit connect mode |
| `mariadb.disconnect` | Disconnect | Connection context menu |
| `mariadb.newSqlEditor` | New SQL Editor | Connection row, beside Connect |
| `mariadb.setDefaultConnection` | Set as Default Connection | Connection context menu |
| `mariadb.clearDefaultConnection` | Clear Default Connection | Connection context menu |
| `mariadb.addSandbox` | New Sandbox… | Sandboxes view title (`+`); the view's welcome content |
| `mariadb.refreshSandboxes` | Refresh | Sandboxes view title; Retry in its failed welcome content |
| `mariadb.startSandbox` | Start Sandbox | Inline on, and context menu of, a STOPPED sandbox |
| `mariadb.stopSandbox` | Stop Sandbox | Inline on, and context menu of, a RUNNING sandbox |
| `mariadb.deleteSandbox` | Delete Sandbox | Inline on, and context menu of, every sandbox not busy. Asks first; stops a running one before deleting |
| `mariadb.selectRows` | Select Rows | Inline on (`toolbar-execute.svg`), and context menu of, a table or view in the Connections view. Runs `SELECT * FROM \`schema\`.\`name\`` and opens the result maximized, in an editor tab titled `schema.name`. Hidden from the palette |
| `mariadb.clearResultView` | Clear Actions | Result view toolbar |
| `mariadb.selectEditorConnection` | Select Connection for this SQL File | SQL editor toolbar, status bar |
| `mariadb.runSqlFile` | Run SQL Script | SQL editor toolbar, `Ctrl`/`Cmd`+`Enter` |
| `mariadb.runSqlStatement` | Run SQL Statement at Cursor | SQL editor toolbar, `Shift`+`Enter` |
| `mariadb.stopOnError.disable` / `.enable` | Stop on Error (on/off) | SQL editor toolbar, one shown at a time |
| `mariadb.restartMcpServer` | Restart MCP Server | Command palette |
| `mariadb.showMcpServerLog` | Show MCP Server Log | Command palette |

**The Connections view's toolbar has commands of its own.** VS Code runs a
tree view's toolbar commands with the tree's FOCUSED row as their argument
(`getActionsContext` returns `$focusedTreeItem: true`; the extension host
turns it into `focusedElement`), and a row stays focused after its
selection is cleared. Sharing `addConnection` / `newFolder` with the row
menus therefore created in whatever folder last had focus - a new folder
landed inside the first one. `toolbarAddConnection` and
`toolbarNewFolder` ignore their argument and go by
`connectionsView.selection` instead (`selectedFolder` in `extension.ts`):
a folder row is its own folder, a connection its `path`, a schema or
object its connection's (looked up in the cached list), several rows
their `commonFolder`, nothing selected the top level. The extension host
is handed `$selectedTreeItems: true` too but never turns it into an
argument, so the selection is not there to be had any other way. (A first
fix that always used the top level was wrong: a selected folder has to
count.) A test pins the toolbar to these commands; the mock tree view's
`select` sets its `selection`.
