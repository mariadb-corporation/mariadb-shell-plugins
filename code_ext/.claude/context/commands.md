# Commands

Every command the extension contributes, and where it appears.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

| Command | Title | Where |
| --- | --- | --- |
| `mariadb.refreshConnections` | Refresh | Connections view title |
| `mariadb.addConnection` | New Connection… | Connections view title (`+`) |
| `mariadb.editConnection` | Edit Connection | Connection context menu |
| `mariadb.deleteConnection` | Delete Connection | Connection context menu |
| `mariadb.addConnection` (on a folder) | New Connection… | Inline on, and context menu of, a folder: the new connection starts in it |
| `mariadb.newFolder` | New Folder… | View toolbar; context menu of folders. Prompts for a name, relative to that folder |
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
| `mariadb.clearResultView` | Clear Actions | Result view toolbar |
| `mariadb.selectEditorConnection` | Select Connection for this SQL File | SQL editor toolbar, status bar |
| `mariadb.runSqlFile` | Run SQL Script | SQL editor toolbar, `Ctrl`/`Cmd`+`Enter` |
| `mariadb.runSqlStatement` | Run SQL Statement at Cursor | SQL editor toolbar, `Shift`+`Enter` |
| `mariadb.stopOnError.disable` / `.enable` | Stop on Error (on/off) | SQL editor toolbar, one shown at a time |
| `mariadb.restartMcpServer` | Restart MCP Server | Command palette |
| `mariadb.showMcpServerLog` | Show MCP Server Log | Command palette |
