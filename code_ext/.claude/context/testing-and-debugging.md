# Testing and debugging

How the suite is kept host-free, and what is load bearing in the F5 launch
configuration.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The scripts and the
Vitest projects themselves are in [toolchain.md](toolchain.md).

## Testing approach

Everything that touches the outside world sits behind a small interface —
`ShellEnvironment`, `ProcessRunner`, `ProgressHost`, `IMcpConnector`,
`IMariaDbApi`, `IConnectionSettings` — so the lookup, install, connection,
tree, execution and panel behaviour are all tested with fakes, for every
platform, from a single host. `src/test/shell/nodeRuntime.test.ts` covers
the real process adapter by spawning actual processes against a temporary
directory.

The MCP decoding fixtures in `src/test/mcp/protocol.test.ts` are the shapes
the running server actually answers with, so the mapping is pinned to the
server rather than to an idea of it.

## Debugging in VS Code

`code_ext/.vscode/launch.json` is folder scoped, so its `${workspaceFolder}`
resolves to `code_ext` both when that folder is opened on its own and when
the multi-root `MariaDBShellPlugins.code-workspace` is opened. (Opening the
repository root as a plain folder does not pick the file up at all, since
only the root's `.vscode/launch.json` is read then.)

Two things about the **watch** task are deliberate and easy to undo by
accident:

- It depends on a separate `build: webview` task rather than chaining the
  webview build into the watch script. The webview build prints
  `built in ...`, which would satisfy the watcher's `endsPattern` and let
  the extension host launch before `dist/extension.js` had been written.
- Its `endsPattern` matches the failure lines (`Transform failed`,
  `error during build`, `Could not resolve`) as well as `built in `. A
  failed rebuild never prints `built in `, so without them F5 would wait
  for a build that never finishes. It deliberately does **not** match
  `watching for file changes`, which vite prints *before* the first build.

`preLaunchTask` names the `watch` task rather than using
`${defaultBuildTask}`, because other folders of the multi-root workspace
declare a default build task of their own.

The problem matcher parses esbuild's `<abs path>:<line>:<col>: ERROR: msg`
form, which is uppercase and absolute - hence `fileLocation: "absolute"`.
