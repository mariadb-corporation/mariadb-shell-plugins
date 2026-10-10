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

`src/test/mocks/vscode.ts` also stands in for file dialogs and files
(`fileDialogs` answers `showSaveDialog` / `showOpenDialog`, `files` backs
`workspace.fs`), records `vscode.open` calls (`openedWith`) and registered
file system providers (`fileSystemProviders`), and has `FileType`,
`FilePermission`, `FileChangeType`, `FileSystemError` and `Uri.from`; all
are reset by `resetVscodeMock`. Its `Disposable` is only an interface, so
code must return `{ dispose }` objects rather than `new vscode.Disposable`.

The MCP decoding fixtures in `src/test/mcp/protocol.test.ts` are the shapes
the running server actually answers with, so the mapping is pinned to the
server rather than to an idea of it. The shared fake API in
`src/test/helpers.ts` rejects a missing object with the real API's
`ObjectNotFoundError`, since the execution service and the logging
wrapper now tell it apart by type, not by message.

Two typing traps in the grid tests: Tabulator's `headerTooltip` typing
says the function returns a `string` while the runtime takes an element,
so a test calls it (`as unknown as () => HTMLElement`) and reads
`textContent`; and `formatValue` / `formatCell` return elements, so
their tests read `textContent` rather than comparing strings.

## UI tests (ExTester)

`MARIADB_SHELL=<path to mariadb-shell> npm run ui-test` drives a real
VS Code through WebDriver. It uses vscode-extension-tester 8.28.1 and
Mocha, on the newest VS Code (`--code_version max`). `UI_TEST_GLOB`
picks the files, e.g. `UI_TEST_GLOB=out/ui-test/tests/0[01]*.test.js`.
`mariadbd` must be on the PATH.

- `ui-test/run.ts` builds a temporary work dir, which is removed at the
  end, also after a failure. Sandboxes left running are pkill'ed first.
  - A shell home with this repo's plugins linked in and options.json
    `sandboxDir` + `credentialStore.helper: plaintext`. The keychain is
    the whole user's, so without the plaintext helper the test VS Code
    would show the user's own connections.
  - A `mariadb-shell` wrapper first on the PATH, setting
    `MARIADB_SHELL_USER_CONFIG_HOME`.
  - `VSCODE_CLI=1`. Without it, VS Code on macOS takes the login shell's
    PATH and finds the user's shell.
  - All `VSCODE_*` / `ELECTRON_*` variables are stripped. An inherited
    `ELECTRON_RUN_AS_NODE` makes the test VS Code exit at once.
- The files run in order and lean on each other:
  - `00-startup`
  - `01-sandbox`: New Sandbox dialog, test schema
  - `02-mrs`: MRS end to end
  - `03-connections`
  - `04-sql-results`
  - `05-lifecycle`: sandbox stop/start, MCP restart and log
  - `99-cleanup`: drops the sandbox

  `.mocharc.json` has `bail`.
- Helpers live in `ui-test/lib/ui.ts`. Timeouts are short on purpose
  (TIMEOUT 10 s, SERVER_TIMEOUT 15 s, DEPLOY_TIMEOUT 90 s), and dialogs
  fail at once on an in-dialog `.message.error` or an error
  notification. Screenshots go to `.vscode-test/extest/screenshots`.
- ExTester traps:
  - Tree rows are virtualized and their DOM is reused. Re-find a row by
    label and `aria-level` (`treeItem`); never hold on to one.
  - Context-menu clicks are sometimes lost when a tree refresh closes the
    menu. `menuAction`, `openDialog` and `menuConfirm` retry 3×.
  - A webview that has closed answers lookups with nothing, so
    `submitDialog` checks the editor tab from outside.
  - Palette commands do not reach a focused webview: bring an editor
    tab to the front first.
  - Tabulator rebuilds its cell editor right after the click. Type into
    `switchTo().activeElement()`, not into an element found earlier.
  - Untitled SQL editors would ask "Save changes?" when VS Code closes.
    Use "View: Revert and Close Editor".
  - Elements hidden until hover (the data mapping flags) need
    `driver().actions().move({origin})` first.
  - VS Code ignores a context-menu pick that comes right after the menu
    opened; `contextMenu` waits 200 ms before selecting.
  - `WebView.findElements` is Selenium's element method. It searches under
    the frame's element in the workbench and fails with "element not
    found" once inside the frame. Use `findWebElements`.
  - A connection closed while its row is expanded keeps the row expanded
    and empty. `treeItem` collapses and expands it again to reconnect.
  - Quick work can close a dialog before its progress bar is seen;
    `submitDialog` accepts that, but a dialog still open must show the bar.

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

## F5 hangs with "Extension host did not start in 10 seconds"

That line, in the development window's `renderer.log`, together with
`Could not connect to debug target at http://localhost:<port>` from
`ms-vscode.js-debug` in the launching window's, means the debugger never
attached to the `--inspect-brk` extension host. None of the extension ran:
there is no MariaDB output channel and no install progress. It is not the
shell download - check the logs under
`~/Library/Application Support/Code/logs/<session>/window*/renderer.log`
before looking at `src/shell/`.

Seen on **VS Code 1.139.0** (js-debug 1.117.0): a known regression,
[vscode-js-debug#2420](https://github.com/microsoft/vscode-js-debug/issues/2420).
js-debug probes `127.0.0.1` and `[::1]` in parallel; the inspector listens
on IPv4 only, the IPv6 refusal comes back first as an error type it does
not expect, and that cancels the IPv4 probe that would have worked. No
launch.json setting avoids it. The fix used on 2026-09-24 was going back
to 1.138.0 (`https://update.code.visualstudio.com/1.138.0/darwin-arm64/stable`)
with `"update.mode": "none"`; Run Without Debugging (Ctrl+F5) also works
on 1.139.0, since nothing waits for a debugger.
