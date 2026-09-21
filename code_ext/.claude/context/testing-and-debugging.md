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

### F5 builds once; the watcher is asked for

`preLaunchTask` is the **`build`** task - `npm run build`, which cleans
`dist/` and rebuilds the extension and both webviews in under two
seconds. It names the task rather than using `${defaultBuildTask}`,
because other folders of the multi-root workspace declare a default build
task of their own.

It is not the **`watch`** task, although a watcher is the faster way to
iterate, because **a background task's problems outlive the build that
reported them.** VS Code holds a background matcher's collection until it
sees the task's `beginsPattern` again, and a rebuild does not reliably
retrigger that: an esbuild error from a half written file - which is what
a watcher sees of any save made part way through an edit - can sit in the
Problems panel long after the file is correct, pointing at lines that no
longer exist. `Tasks: Restart Running Task` is what clears it. The one
shot task reports through the same matcher with **no `background` block**,
so every run clears what the last one said.

Both tasks report under `owner: "vite"`, which is deliberate: they share
one collection, so building also clears whatever a watcher left behind.

The watcher is still there - it is in the build group, just no longer the
default one, so `Tasks: Run Task` -> `watch` starts it when a debug
session is going to be reloaded (`Developer: Reload Window` in the
extension host) rather than relaunched. Two things about it are
deliberate and easy to undo by accident:

- It depends on a separate `build: webview` task rather than chaining the
  webview build into the watch script. The webview build prints
  `built in ...`, which would satisfy the watcher's `endsPattern` and let
  the extension host launch before `dist/extension.js` had been written.
  (F5 no longer launches behind it, but ending the task early would
  still report a build as finished before it was.)
- Its `endsPattern` matches the failure lines (`Transform failed`,
  `error during build`, `Could not resolve`) as well as `built in `. A
  failed rebuild never prints `built in `, so without them anything
  waiting on the task would wait for a build that never finishes. It
  deliberately does **not** match `watching for file changes`, which vite
  prints *before* the first build.

Note that the watcher rebuilds the **extension only**. The webviews are
built once by `build: webview`; `npm run watch:webview` watches those.

`webview/test/setup.ts` installs two sets of stubs, and the second is
easy to overlook: besides the `acquireVsCodeApi` bridge the frontend
takes at module load, it supplies `ResizeObserver` and the `scrollBy` /
`scrollIntoView` methods, which **jsdom does not implement at all**.
The page measures its tab strip and scrolls a tab into view; without
the stubs those calls throw inside an effect, where the failure is easy
to miss and every assertion after it is made against a page that never
finished rendering. They measure nothing - jsdom reports every element
as zero sized - so a test that needs a size states it itself, as
`App.test.tsx` does for the tab strip's paging.

The problem matcher parses esbuild's `<abs path>:<line>:<col>: ERROR: msg`
form, which is uppercase and absolute - hence `fileLocation: "absolute"`.
