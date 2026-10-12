# Dump and Load

The Dump and Load submenu on connection, schema and table rows, its dialog,
the background tasks and the Tasks view.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The server side (the
`util.*` tools and the task registry) is in the MCP plugin's own
`context/util.md`. Branch `wip/mcp_dump_load` (2026-10-10). The server
runs each task in a MariaDB Shell process of its own (2026-10-12; it
first needed mariadb-shell #73, which was dropped for this).

| File | Purpose |
| --- | --- |
| `src/mcp/utilApi.ts` | `IUtilApi` / `UtilApi`: the 12 `util.*` tools, typed, and their task state. Start calls get a 2 min timeout (they check the connection first). `getTask` asks the server to wait `WAIT_MS` (2 s) and gives the call 30 s more than that. |
| `src/util/utilFields.ts` | Per operation: the dialog's title, path or target, and the utility's options with the shell's own defaults. `buildUtilOptions` sends only what differs from a default, so the shell stays the one deciding defaults. Pure, shared with the webview. |
| `src/util/utilProtocol.ts`, `utilDialogPanel.ts` | The dialog's messages and panel. Browse opens the folder or file picker. Start checks the values again, calls the host's `start` and closes once the task runs; a failed start stays in the dialog with the reason. One dialog at a time; a new one replaces it. |
| `webview/src/UtilDialog.tsx`, `util.tsx`, `utilStyles.css`, `vite.util.config.ts` | The dialog: path with Browse, or the copy's target; Basic and Advanced tabs; a progress bar and disabled buttons while the start call runs. Its own bundle (`build:webview` builds four now). |
| `src/util/taskMonitor.ts` | `TaskMonitor.follow`: polls `getTask` until the task ends, writes its messages to the *MariaDB Tasks* channel, and drives a cancellable notification (Cancel calls `util.cancel_task`). The server answers as soon as a task changes, and a dump reports progress many times a second, so the monitor waits out the rest of `POLL_INTERVAL_MS` (1 s) after a quicker answer: asking again at once redrew the Tasks view so often that a click on a row's Cancel was lost (found by the UI test). A task keeps its dialog's title; the server's names the path. The bar only moves for stages that carry `items` (bytes or rows); counting stages show just their name. A task the server forgot (restart) ends as failed, "Lost track of the task". |
| `src/util/tasksTreeProvider.ts` | The Tasks view (`mariadb.tasks`, third in the MariaDB container): newest first, state or stage plus percent, the stages in the tooltip; Cancel inline on running rows, Resume on resumable ones, Remove on finished ones, Show Output and Clear Finished on the title bar; a click (and Show Log in the menu) opens the task's own log. `showRunningTasks` sets the view's message ("Closing or reloading this window stops the running tasks...") and a badge with the count while tasks run. |
| `src/util/taskActions.ts` | `TaskActionLog`: each task as a row of its connection's Actions in the panel (`ResultViewProvider.putAction`, which replaces a row by id and does not reveal the panel). A `run` row, filed under `UI Backend`: the dialog's title, the stage and percent (or how it ended) as its summary, `pending` while it runs. Under it only what it came to: every warning and error as a row of its own, and the totals the shell ends with (`resultText`: total duration, schemas/tables dumped, rows written, data size, "... were loaded in", "N warnings were reported", "Total rows affected") gathered into ONE row with a line each, `multiline` - shown in full, the row as tall as its lines, like a failed statement's error (`.fullRow` in the grid); the steps along the way and the `util.*` call are left to the log, after the user found the panel far too verbose. A failure ends with its error as a child, pointed at by `jumpToRowId`, as a failed SQL run does. Times are the extension's own (the server's have whole seconds). Fed by `TaskMonitor.onDidReport` (`first` on its start), which strips the terminal colour codes a copy puts on its SRC:/TGT: prefixes. |
| `src/util/taskLogs.ts` | `TaskLogs`: a log file per task in `<globalStorage>/task-logs/<task id>.log` - a header (title, connection, call, start), each message as it comes, and how it ended. Survives a reload, where the output channel and the server's messages do not; an open editor follows the file as it grows. Deleted when the task leaves the Tasks view (`prune` on every change of the list, and on activation). The failure notification's button is Show Log (`TaskMonitor.openLog`). |
| `src/util/utilCommands.ts` | The nine commands and the four task commands. The connection id is the tree's own session (`UI_BACKEND_SESSION`). The server runs each task in a shell process of its own, so the tree keeps working while it runs. Multi-select: the schemas or tables of the selection on the same connection (and schema); the selection is matched by row, not object. Load, import and copy refresh the tree once done, unless they failed. |

Notes:
- Start checks a dump folder first (`dumpFolderProblem`): one that is not
  empty, or a file, keeps the dialog open with the reason, where the dump
  used to start and fail after the dialog closed. Copy targets are shown
  by address (`IUtilTarget`), sent by URI.
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
- Not done: copying between connections from the palette.
- **UI tests live on `wip/mrs_code_ext`** (main has no `ui-test/`), which
  has this branch merged in: `ui-test/tests/06-dump-load.test.ts`. Fixes
  they find are made here and merged there again. They cover dump, load
  into another schema, export, import, cancelling a slow dump from the
  Tasks view, a dump into a folder that is not empty, a copy to a second
  sandbox, and Clear Finished Tasks. The MRS tests of the same run need a
  shell with mariadb-shell #71.
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

## A task that does not start (2026-10-11)

When the start call itself fails - no task id to follow - the failure is
a row of the connection's Actions of its own (`TaskActionLog.startFailed`:
the task's title, summary "Did not start", the call and the error as the
row under it), and the panel comes up on it (`IUtilCommandsHost.showActions`
via the dialog's `onFailed`). A dialog closed meanwhile notifies it too;
one still open shows it as before. Closing the dialog never stops a task:
the dialog closes itself once the task runs, and the task runs on in the
server's thread until it ends or is cancelled.

