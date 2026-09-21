# MariaDB for VS Code

The power of MariaDB Shell inside your favorite editor.

The extension drives the MariaDB Shell backend through its
[MCP plugin](https://github.com/mariadb-corporation/mariadb-shell-plugins/tree/main/mcp_plugin),
which exposes the backend functions over the Model Context Protocol.

## Features

- **Connections view** in the activity bar: browse your configured
  connections, their schemas, and the tables, views, functions,
  procedures, sequences, triggers and events in each.
- **Add, edit and delete connections** from the view's `+` button and each
  connection's context menu, in a dialog with the host, protocol, port,
  user, default schema, socket, SSL and compression settings a MariaDB
  Shell connection URI can carry. Passwords go to the operating system's
  secret store, never into the URI, and **Test Connection** tries the
  credentials before anything is saved.
- **MCP access** is a checkbox on each connection. Ticked, it goes in the
  shared MCP connection list, which any MCP client on this machine - an AI
  assistant, say - can open; the Connections view marks those `MCP`.
  Unticked, it belongs to this extension alone.
- **Run SQL** from any `.sql` file, with a connection picker and a run
  button in the editor toolbar (`Ctrl`/`Cmd`+`Enter`).
- **New SQL Editor**, beside Connect on a connection in the tree, opens an
  unsaved `.sql` file already bound to that connection.
- **Results** docked in the bottom panel, beside Problems and Output, with
  an output log and a tab per result set. A dropdown picks which
  connection's results are shown, the output accumulates until you clear
  it, and each output row can jump to the result set it produced.
- A result that comes from a single table with a primary key can be
  **edited in place** — change cells, add and remove rows — and
  **Preview SQL** shows exactly the statements Apply would run, with a
  click on any of them taking you to the row it came from.
- **Default connection**, set from a connection's context menu and
  remembered in your workspace settings.
- **Expanding a connection opens it**, so browsing a database takes one
  click. Set `mariadb.connections.connectMode` to `explicit` to go back to
  connecting with the button on the row.

## Requirements

MariaDB Shell **26.9.2** or newer, with at least one connection configured
via `mariadb-shell -- mcp setup`.

The extension takes care of the shell on its own. The first time it needs
the server it looks for a suitable shell and, if there is none, installs
one:

1. A `mariadb-shell` on your `PATH` is used if it is new enough.
2. Otherwise a local installation under
   `~/.local/share/mariadb-shell/<version>` (macOS, Linux) or
   `%LOCALAPPDATA%\Programs\mariadb-shell\<version>` (Windows) is used.
3. Otherwise the official installer is run, with the download and
   extraction shown in a progress notification.

Set `MARIADB_SHELL_PREFIX` to install and look somewhere else.

## Settings

| Setting | Description |
| --- | --- |
| `mariadb.defaultConnection` | The connection URI new SQL editors run on. |
| `mariadb.connections.connectMode` | Whether expanding a connection in the **Connections** view opens it (`onOpen`, the default) or only the Connect button does (`explicit`). |

## Commands

| Command | Description |
| --- | --- |
| **MariaDB: Add Connection** | Opens the connection editor on a new connection. |
| **MariaDB: Run SQL** | Runs the selection, or the whole `.sql` file. |
| **MariaDB: Select Connection for this SQL File** | Pins this file to a connection. |
| **MariaDB: Restart MCP Server** | Closes the connections and starts the server again. |
| **MariaDB: Show MCP Server Log** | Opens the *MariaDB* output channel. |

## Development

```bash
npm install            # install the toolchain
npm run build          # bundle the extension and the result panel
npm test               # run the Vitest suite
npm run pretest        # type check and lint
```

Press <kbd>F5</kbd> to launch an extension host. The **Run Extension**
configuration builds the result panel and then starts the extension
watcher, so nothing else is needed. To watch from a terminal instead, run
`npm run watch` for the extension and `npm run watch:webview` for the
result panel - `npm run watch` covers the extension only.

The extension is bundled with [Vite](https://vite.dev), the result panel is
[Preact](https://preactjs.com), and both are tested with
[Vitest](https://vitest.dev). See
[.claude/PROJECT_CONTEXT.md](https://github.com/mariadb-corporation/mariadb-shell-plugins/blob/main/code_ext/.claude/PROJECT_CONTEXT.md)
for the layout and the behaviour in detail.

## Licensing

The MariaDB for VS Code extension is a part of the MariaDB Shell and licensed
under the GPLv2. See
[LICENSE](https://github.com/mariadb-corporation/mariadb-shell-plugins/blob/main/code_ext/LICENSE)
for more details.
