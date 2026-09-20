# MariaDB for VS Code

The power of MariaDB Shell inside your favorite editor.

The extension drives the MariaDB Shell backend through its
[MCP plugin](../mcp_plugin), which exposes the backend functions over the
Model Context Protocol.

## Requirements

MariaDB Shell **26.9.2** or newer. The extension takes care of this on its
own: at startup it looks for a suitable shell and, if there is none,
installs one.

1. A `mariadb-shell` on your `PATH` is used if it is new enough.
2. Otherwise a local installation under
   `~/.local/share/mariadb-shell/<version>` (macOS, Linux) or
   `%LOCALAPPDATA%\Programs\mariadb-shell\<version>` (Windows) is used.
3. Otherwise the official installer is run, with the download and
   extraction shown in a progress notification.

Set `MARIADB_SHELL_PREFIX` to install and look somewhere else.

## Commands

| Command | Description |
| --- | --- |
| **MariaDB: Restart MCP Server** | Re-runs the shell lookup and restarts the MCP server. |
| **MariaDB: Show MCP Server Log** | Opens the *MariaDB* output channel. |

## Development

```bash
npm install     # install the toolchain
npm run watch   # rebuild on change, then press F5 to launch the host
npm test        # run the Vitest suite
npm run pretest # type check and lint
```

The extension is bundled with [Vite](https://vite.dev) and tested with
[Vitest](https://vitest.dev). See [.claude/PROJECT_CONTEXT.md](.claude/PROJECT_CONTEXT.md)
for the layout and the startup behaviour in detail.
