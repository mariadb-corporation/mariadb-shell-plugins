# The shell and the MCP server

Finding, installing and launching the MariaDB Shell, the MCP session it
hosts, what `--gui` changes about that server, and how its answers are
decoded.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The connection editor,
which is what most of GUI mode exists for, is in
[connections.md](connections.md).

## `src/shell/`

| File | Purpose |
| --- | --- |
| `constants.ts` | Minimum shell version, MCP server arguments, installer URLs. |
| `version.ts` | Version parsing and comparison, including the `mariadb-shell --version` line. |
| `locator.ts` | Finds a usable shell on the PATH or among the managed installations. |
| `installer.ts` | Builds and runs the platform installer, reporting its progress. |
| `bootstrap.ts` | `ensureShell()` — locate, else install, else fail. |
| `mcpServer.ts` | Builds the command that makes a shell host the MCP server. |
| `lineReader.ts` | Reassembles whole lines from chunked child process output. |
| `nodeRuntime.ts` | The real `child_process` / `fs` implementations of the interfaces above. |
| `vscodeProgress.ts` | The `vscode.window.withProgress` implementation of `ProgressHost`. |

## `src/mcp/`

| File | Purpose |
| --- | --- |
| `types.ts` | The `db.*` result shapes and the `IMariaDbApi` interface. |
| `protocol.ts` | Decodes MCP tool results into the values the Python tools returned. |
| `mariaDbApi.ts` | The `db.*` tools as typed calls. |
| `session.ts` | `McpSession` — starts the server once and hands out the API. |
| `sdkConnector.ts` | The real connector, on `@modelcontextprotocol/sdk`'s stdio transport. |

## Startup behaviour

The extension activates on `onStartupFinished` and on `onLanguage:sql`, but
starts **nothing** at activation: the MCP server comes up the first time
something actually needs it, so an editor that never touches MariaDB never
pays for a shell process. `ConnectionManager.api()` is the single place
that decides to start it, and `ensureShell()` in `src/shell/bootstrap.ts`
drives the shell lookup:

1. **PATH** — run `mariadb-shell --version` (`mariadb-shell.exe` on
   Windows) and accept it if it reports at least `MINIMUM_SHELL_VERSION`
   (currently **26.9.3**, and a HARD floor rather than a preference since
   that release: it is the first shell whose parser accepts `mariadb://`,
   and the MCP plugin stores connection URIs WITH their scheme from there
   on, so an older shell cannot parse what it is handed).
2. **Local installation** — look under the prefix the installer uses,
   `~/.local/share/mariadb-shell/<version>` on macOS and Linux and
   `%LOCALAPPDATA%\Programs\mariadb-shell\<version>` on Windows, honouring
   `MARIADB_SHELL_PREFIX`. Version directories are tried newest first; the
   binary at `<version>/bin/mariadb-shell` is probed, and what it reports
   wins over what the directory is called.
3. **Install** — if neither has a new enough shell, run the official
   installer for the platform (curl-into-bash, or PowerShell on Windows)
   with `MARIADB_SHELL_TAG` pinned. It runs inside
   `vscode.window.withProgress()` at `ProgressLocation.Notification`; both
   installer scripts prefix their progress with `==> `, so `Downloading`,
   `Verifying checksum` and `Unpacking into ...` become the notification's
   message as they arrive.

`McpSession` then starts
`<shell> -- mcp start-server --transport=stdio --gui` through the MCP SDK's
stdio transport, which owns the process. Its stdin and stdout carry the
protocol; stderr goes to the **MariaDB** output channel. Concurrent callers
share one start, so the tree, the toolbar and the panel cannot each spawn a
server.

## GUI mode (`--gui`)

`--gui` tells the MCP server its client is this extension rather than an
autonomous agent, and that changes two things about the server:

- **Every local path is accessible.** Without it the server keeps an
  allowed-path list (`mcp.setup`) and asks, by MCP elicitation, before
  touching anything outside it. The extension names paths the user just
  picked in VS Code's own dialogs, so there is nothing left to confirm.
- **The connection list is writable**, through `db.add_connection`,
  `db.update_connection`, `db.delete_connection` and `db.test_connection`,
  which a server serves in this mode only. The last two exist for the
  editor: nothing can read a stored password back, so `update` is what
  re-keys a connection without one having to come out, and `test` is the
  only way to try credentials before the connection exists (`db.connect`
  opens configured connections only, and `add` stores on success).
  `db.test_connection` is GUI-only for a reason beyond symmetry: it opens a
  session to any host and credentials it is handed, which given to an
  autonomous client is a way to try passwords against any reachable server.
  There are then **two** lists, told apart by a `kind`:

  | kind | who owns it | secret prefix |
  | --- | --- | --- |
  | `mcp` (the default) | `mcp.setup`; every MCP client can open these | `MCP:Connection:` |
  | `gui` | this extension, via the tools above | `GUI:Connection:` |

  `db.list_connections` reports **one kind per call**, so the extension
  asks twice to see both and always knows which list an entry is in — which
  it needs, since a connection is deleted from the list it is in. The same
  server may be in both under different credentials; `db.connect` then
  opens the `gui` one.

  What it reports is `scheme://user@host:port` from shell 26.9.3 on. A
  connection configured before that is stored WITHOUT a scheme and is
  reported with `mariadb://` filled in — the key is left alone, so nothing
  had to migrate, and every tool resolves either spelling back to it. The
  one place the difference shows in the extension is a URI IT wrote down
  earlier: see `withDefaultScheme` in
  [`connections.md`](connections.md).

`MCP_SERVER_ARGS` in `src/shell/constants.ts` is where the flag is passed.
A shell whose MCP plugin predates it **ignores it rather than failing** (the
plugin function takes its options as a dictionary and reads only the ones it
knows), so such a server simply comes up without GUI mode.
`MariaDbApi.listConnections`/`addConnection`/`deleteConnection` leave `kind`
and `verify` out of the call entirely when they were not given, for the same
reason.

## The MCP wire format

The server is a FastMCP server, so a tool's return value is rendered into
the `content` array rather than into one JSON document. `src/mcp/protocol.ts`
decodes all four shapes, which were read off the running server:

| Tool returns | `content` |
| --- | --- |
| a list | one text item per element (JSON for dicts, bare text for strings) |
| a dict | one text item holding its JSON |
| a scalar | one text item, plus `structuredContent.result` |
| an error | `isError: true`, the message as text |
