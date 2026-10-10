# Dump and Load

The Dump and Load submenu on connection, schema and table rows, its dialog,
the background tasks and the Tasks view.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The server side (the
`util.*` tools and the task registry) is in the MCP plugin's own
`context/util.md`. Branch `wip/mcp_dump_load` (2026-10-10). It needs
mariadb-shell #73.

| File | Purpose |
| --- | --- |
| `src/mcp/utilApi.ts` | `IUtilApi` / `UtilApi`: the 12 `util.*` tools, typed, and their task state. Start calls get a 2 min timeout (they open a session first). `getTask` asks the server to wait `WAIT_MS` (2 s) and gives the call 30 s more than that. |
| `src/util/utilFields.ts` | Per operation: the dialog's title, path or target, and the utility's options with the shell's own defaults. `buildUtilOptions` sends only what differs from a default, so the shell stays the one deciding defaults. Pure, shared with the webview. |
| `src/util/utilProtocol.ts`, `utilDialogPanel.ts` | The dialog's messages and panel. Browse opens the folder or file picker. Start checks the values again, calls the host's `start` and closes once the task runs; a failed start stays in the dialog with the reason. One dialog at a time; a new one replaces it. |
| `webview/src/UtilDialog.tsx`, `util.tsx`, `utilStyles.css`, `vite.util.config.ts` | The dialog: path with Browse, or the copy's target; Basic and Advanced tabs; a progress bar and disabled buttons while the start call runs. Its own bundle (`build:webview` builds four now). |
| `src/util/taskMonitor.ts` | `TaskMonitor.follow`: polls `getTask` until the task ends, writes its messages to the *MariaDB Tasks* channel, and drives a cancellable notification (Cancel calls `util.cancel_task`). The bar only moves for stages that carry `items` (bytes or rows); counting stages show just their name. A task the server forgot (restart) ends as failed, "Lost track of the task". |
| `src/util/tasksTreeProvider.ts` | The Tasks view (`mariadb.tasks`, third in the MariaDB container): newest first, state or stage plus percent, the stages in the tooltip; Cancel inline on running rows, Resume on resumable ones, Remove on finished ones, Show Output and Clear Finished on the title bar. `showRunningTasks` sets the view's message ("Closing or reloading this window stops the running tasks...") and a badge with the count while tasks run. |
| `src/util/utilCommands.ts` | The nine commands and the four task commands. The connection id is the tree's own session (`UI_BACKEND_SESSION`). The server opens a separate session per task, so the tree keeps working while it runs. Multi-select: the schemas or tables of the selection on the same connection (and schema); the selection is matched by row, not object. Load, import and copy refresh the tree once done, unless they failed. |

Notes:
- Dump folders default to `<first workspace folder or home>/<name>-<YYYY-MM-DD-HHMM>`,
  an export to `<schema>.<table>.tsv` there. `exportTable` quotes names that
  need it.
- Copy targets are the configured connections other than the source; the
  target is connected (tree session) only when Start is pressed.
- **Tasks end with the window.**
  - The MCP server is a stdio child of the extension host, and a task is a
    thread in that server, so a reload or close stops every running task: a
    dump is left incomplete, a load partway (resumable), and a copy cannot
    resume.
  - The server's one-hour retention only helps a server that keeps running
    (HTTP).
  - VS Code gives no chance to warn before a window closes, so the view
    says it while tasks run.
- **The task list survives a reload.**
  - `TaskMonitor` keeps it in `workspaceState` (`TASKS_KEY`, the newest 50,
    without messages) on every change.
  - On activation, a task that had not ended is marked `interrupted` (icon
    `debug-disconnect`) and counts as over: Remove, Clear Finished.
- **Resume Load** (`mariadb.resumeTask`):
  - offered for a load that did not complete (interrupted, failed or
    cancelled; context `mariadbTask.finished.resumable`);
  - starts `util.load_dump` again with the same folder and options, minus
    `resetProgress`, so the shell resumes from the dump's progress file;
  - the new task takes the old row's place.
- Not done: copying between connections from the palette, and ExTester UI
  tests (main has no `ui-test/`; they are on `wip/mrs_code_ext`).
- Tests:
  - `src/test/util/` (fields, monitor + view, commands driven through the
    dialog panel);
  - `src/test/mcp/utilApi.test.ts`;
  - `webview/test/UtilDialog.test.tsx`;
  - a one-off run against a real `mcp start-server --gui` (dump, load, list)
    passed on 2026-10-10.
- The real-server run deployed its sandbox in `~/.mariadb-shell/sandboxes`
  and stored its password in the real secret store even with
  `MYSQLSH_USER_CONFIG_HOME` pointing elsewhere; both had to be removed by
  hand. The plugin's `run_tests.py` isolates the secret store, which a
  one-off run does not.
