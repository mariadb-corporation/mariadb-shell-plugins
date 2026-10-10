# The `util` function group: dumps and loads as background tasks

Branch `wip/mcp_dump_load` (2026-10-10, from `main` after #42). It needs
mariadb-shell PR #73 (`wip/util_any_session`), which gives the shell's
utilities the `session` and `progressCallback` options; with an older shell
every task fails with "This MariaDB Shell is too old for the util tools".

## Pieces

- `lib/tasks.py`: the registry.
  - A `Task` holds its status, stages, current progress, a deque of messages
    with sequence numbers and a `version` bumped on every change, and a
    `Condition` that waiting callers sleep on.
  - `on_event()` is the utility's `progressCallback`; it returns "cancel" once
    `cancel()` set the flag.
  - Tasks are bound to the client identity exactly as connections are
    (another client's id answers "no task").
  - At most `MAX_RUNNING_TASKS_PER_CLIENT` = 4 run per client. Finished tasks
    are kept 1 h, 100 at most, pruned on every lookup.
- `lib/util_functions.py`: the 12 tools.
  - The nine start tools are async: `require_allowed_path` elicits. They:
    1. refuse `session`/`progressCallback`/`showProgress` in `options`;
    2. check the local paths;
    3. open the task's own session with
       `db_functions.open_separate_session()` (and, for a copy, the target's
       `connection_data()` with its password) via `anyio.to_thread`, so a
       wrong connection fails the call itself;
    4. start the thread.
  - `get_task` / `list_tasks` / `cancel_task` are sync, so `wait_ms` (at most
    30 s) blocks a worker thread, not the loop.
  - The group registers only alongside `db`.
- `db_functions.open_separate_session()` opens a second session the way a
  reopened idle one is opened, with the same checks, and never takes the
  connection's lock. `connection_data()` refuses login (grant) connections.
- `general.FUNCTION_GROUP_UTIL`: supported and in the defaults, never in
  `MULTI_TENANT_FUNCTION_GROUPS` (the user's decision, 2026-10-10).

## Shell facts it rests on (see the shell's `.claude/context/util-any-session.md`)

- `util.*` from a Python thread works with `session=`: no global session is
  touched. The callback runs on the shell's progress thread and dump workers,
  with the GIL taken. Cancel works from any thread.
- Events: `message` / `stageStarted` / `progress` / `stageFinished`. `progress`
  has `throughput`, `etaSeconds`, `items` and `totalIsApproximate` for byte and
  row stages, or `totalKnown` for counting ones.

## Tests

`tests/unit/test_util.py` (13):
- the registry on its own: events, since/dropped messages, failure, cancel,
  client binding, the per-client cap, pruning, waking a waiter, percent;
- `_options` and `_local_paths`;
- the db-group gate;
- an end-to-end run over stdio on the shared sandbox: dump, load into another
  schema, export, import, a cancelled `maxRate` dump, a copy to the same server
  failing as a task, `list_tasks`, refusals.

`test_multi_tenant.py` checks that `util` is refused. Full suite: 609 passed,
3 skipped (2026-10-10, against the shell built from #73).
