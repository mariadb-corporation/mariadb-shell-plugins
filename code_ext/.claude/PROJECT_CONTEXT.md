# code_ext — Project Context

The **MariaDB** VS Code extension. Written in TypeScript, it talks to the
MariaDB Shell backend through the [`mcp_plugin`](../../mcp_plugin), which
exposes the backend functions over the Model Context Protocol (MCP).

This file is the running description of the project. Keep it up to date as
the extension grows.

## Layout

| Path | Purpose |
| --- | --- |
| `src/extension.ts` | Activation entry point. Wires the real runtime to the shell bootstrap and owns the MCP server for the extension's lifetime. |
| `src/shell/constants.ts` | Minimum shell version, MCP server arguments, installer URLs. |
| `src/shell/version.ts` | Version parsing and comparison, including the `mariadb-shell --version` line. |
| `src/shell/locator.ts` | Finds a usable shell on the PATH or among the managed installations. |
| `src/shell/installer.ts` | Builds and runs the platform installer, reporting its progress. |
| `src/shell/bootstrap.ts` | `ensureShell()` — locate, else install, else fail. |
| `src/shell/mcpServer.ts` | `McpServerController` — starts, restarts and stops the MCP server. |
| `src/shell/lineReader.ts` | Reassembles whole lines from chunked child process output. |
| `src/shell/nodeRuntime.ts` | The real `child_process` / `fs` implementations of the interfaces above. |
| `src/shell/vscodeProgress.ts` | The `vscode.window.withProgress` implementation of `ProgressHost`. |
| `src/test/mocks/vscode.ts` | Test double for the `vscode` module, aliased in by `vitest.config.ts`. |
| `src/test/helpers.ts` | Fake environment, process runner, MCP spawner and log. |

## Toolchain

- **Build**: [Vite](https://vite.dev) in library mode, bundling
  `src/extension.ts` into a single CommonJS file at `dist/extension.js`.
  `vscode` and the Node built-ins stay external; everything else is bundled.
  See `vite.config.ts`.
- **Tests**: [Vitest](https://vitest.dev), running in plain Node. Tests live
  next to the code they cover as `*.test.ts`. The `vscode` module is aliased
  onto `src/test/mocks/vscode.ts`, so no extension host is needed.
  See `vitest.config.ts`.
- **Type checking**: `tsc --noEmit`; Vite does the emitting.
- **Lint**: ESLint with `typescript-eslint`.

| Script | What it does |
| --- | --- |
| `npm run build` | Bundle into `dist/`. |
| `npm run watch` | Rebuild on change (the default build task). |
| `npm test` | Run the Vitest suite once. |
| `npm run test:watch` | Run Vitest in watch mode. |
| `npm run coverage` | Run the suite with a V8 coverage report. |
| `npm run typecheck` | Type check without emitting. |
| `npm run lint` | Lint `src/`. |
| `npm run pretest` | `typecheck` + `lint`. |

## Startup behaviour

The extension activates on `onStartupFinished` and brings up the MCP server.
`ensureShell()` in `src/shell/bootstrap.ts` drives the sequence:

1. **PATH** — run `mariadb-shell --version` (`mariadb-shell.exe` on Windows)
   and accept it if it reports at least `MINIMUM_SHELL_VERSION`
   (currently **26.9.2**).
2. **Local installation** — look under the prefix the installer uses,
   `~/.local/share/mariadb-shell/<version>` on macOS and Linux and
   `%LOCALAPPDATA%\Programs\mariadb-shell\<version>` on Windows, honouring
   `MARIADB_SHELL_PREFIX`. Version directories are tried newest first; the
   binary at `<version>/bin/mariadb-shell` is probed, and what it reports
   wins over what the directory is called.
3. **Install** — if neither has a new enough shell, run the official
   installer for the platform:

   ```bash
   # macOS and Linux
   curl -fsSL https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.sh \
       | MARIADB_SHELL_TAG=v26.9.2 bash
   ```

   ```powershell
   # Windows
   $env:MARIADB_SHELL_TAG = 'v26.9.2'
   irm https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.ps1 | iex
   ```

   The installer runs inside `vscode.window.withProgress()` at
   `ProgressLocation.Notification`. Both installer scripts prefix their
   progress lines with `==> `, so those lines - `Downloading`,
   `Verifying checksum`, `Unpacking into ...` - become the notification's
   message as they arrive. Step 1 and 2 are then repeated; if the shell is
   still missing the activation fails with an error notification.

Once a shell is found, `McpServerController` spawns:

```
<shell binary> -- mcp start-server --transport=stdio
```

Its stdin and stdout carry the MCP protocol; stderr is written to the
**MariaDB** output channel. The server is killed when the extension is
deactivated.

## Commands

| Command | Title |
| --- | --- |
| `mariadb.restartMcpServer` | MariaDB: Restart MCP Server |
| `mariadb.showMcpServerLog` | MariaDB: Show MCP Server Log |

## Testing approach

Everything that touches the outside world sits behind a small interface -
`ShellEnvironment`, `ProcessRunner`, `ProgressHost`, `McpServerSpawner` -
so the lookup, install and startup rules are unit tested with fakes, for
every platform, from a single host. `src/shell/nodeRuntime.test.ts` then
covers the real adapter by spawning actual processes (using
`process.execPath` as a stand-in binary) against a temporary directory.

## Conventions

- Four space indentation, double quotes, trailing semicolons.
- Arrow function consts for exported functions; JSDoc with `@param` and
  `@returns` on everything exported.
- Comments explain *why*, not *what*.
- Relative imports carry the `.js` extension.
