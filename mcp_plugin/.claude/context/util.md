# The `util` function group: dumps and loads as background tasks

Every task runs the utility in a MariaDB Shell process of its own,
`mariadb-shell --json=raw --passwords-from-stdin --uri=<uri> -- util ...`, and
is followed with the `task.*` tools. Running it in the server's process on a
session of its own, through the `session` and `progressCallback` options of
mariadb-shell PR #73 (closed for this), let a crash of a utility take the
whole server down.

## Pieces

- `lib/tasks.py`: a thin layer over the shell's `mysqlsh.task_manager`, which
  holds the registry described below and is shared with the dba plugin's
  tasks; this module makes a task its client's and turns refusals into tool
  errors. The `task.get` / `task.list(task_type)` / `task.cancel` tools are in
  `lib/task_functions.py`, served with the util or dba group (see
  [dba.md](dba.md)).
  - A `Task` holds its status, stages, current progress, a deque of messages
    with sequence numbers and a `version` bumped on every change, and a
    `Condition` that waiting callers sleep on.
  - `start(work)` runs `work(task)` on a thread. That thread follows the shell
    process and feeds `on_event()`.
  - `cancel()` sets the flag and calls the canceller the work set with
    `set_canceller()`. A cancel that came before the canceller was set reaches
    it when it is set.
  - Tasks are bound to the client identity exactly as connections are
    (another client's id answers "no task").
  - At most `MAX_RUNNING_TASKS_PER_CLIENT` = 4 run per client. Finished tasks
    are kept 1 h, 100 at most, pruned on every lookup.
- `lib/util_process.py`: the shell process.
  - `shell_executable()`: `mysqlsh.executable`, which the shell sets to its
    own binary (`get_binary_path()`, in `python_context.cc`) when it starts
    its Python; since 2023, so every supported shell has it. Without it (a
    plain Python), `mariadb-shell` on PATH. `sys.executable` is the shell's
    bundled Python, not the shell.
  - `build_command()` follows `shell_cli_mapper.cc`; see "Command line" below.
  - `OutputParser` turns the JSON lines into the events `on_event()` takes;
    see "Output" below.
  - `ShellProcess` starts `<bundled python> -I util_watchdog.py <shell argv>`
    in a session of its own (`start_new_session`, or
    `CREATE_NEW_PROCESS_GROUP`). The environment has
    `MARIADB_SHELL_TERM_COLOR_MODE=nocolor`, so a copy's SRC:/TGT: prefixes
    carry no colour codes.
  - `run()` runs it, and on a crash starts it again where that resumes the
    work; see "Crashes" below.
- `lib/util_watchdog.py`: a script, not imported.
  - It starts the shell and writes the passwords to the shell's stdin.
  - It reads commands from its own stdin: `cancel` sends SIGINT (CTRL_BREAK
    on Windows).
  - An end of its stdin means the server is gone: SIGINT, then a kill 30 s
    later. `kill` ends the shell at once: killing the watchdog itself would
    leave the shell running.
  - Once the shell has ended it writes `{"mcpShellExit": {"returncode": n}}`,
    where n is negative for a signal.
  - Without it, a load would go on after a window reload beside the one the
    extension resumes. Where the shell has no Python, the shell runs
    directly, and `atexit` kills it.
- `lib/util_functions.py`: the 12 tools.
  - The nine start tools are async: `require_allowed_path` elicits. They:
    1. refuse `session`/`progressCallback`/`showProgress` in `options`;
    2. check the local paths (`_local_paths`): every URL with no scheme or a
       `file` one (any case, stripped as the shell's `strip_scheme` does),
       unless a bucket option makes the URLs prefixes, plus `progressFile`
       and the object storage config/credential files in every case. It
       skipped every `://` once, which let `file:///...` past the allow-list
       (found by `/security-review`, 2026-10-12);
    3. read the source's URI and password
       (`db_functions.connection_credentials`), and for a copy the target's
       connection data (`connection_data`), and build the command line once,
       via `anyio.to_thread`, so that a wrong connection or a value the
       command line cannot take fails the call itself;
    4. start the task.
  - `task.get` / `task.list` / `task.cancel` are sync, so `wait_ms` (at most
    30 s) blocks a worker thread, not the loop.
  - The group registers only alongside `db`.
  - Tool descriptions steer the agent (2026-10-21): `import_table` names
    the file formats it takes (CSV, TSV, JSON lines, every LOAD DATA
    format, local or object storage), its dialects and the mapping
    options, and says to use it instead of `LOAD DATA` through
    `db.execute_sql`, `mariadb-import` or generated INSERTs; `load_dump`
    says it takes only `util.dump_*` dumps; `export_table` says to use it
    instead of `SELECT ... INTO OUTFILE`. The server rule in
    `server.server_instructions` repeats that (an agent was loading CSV
    files through mariadb-dump and clients before). The nine start tools
    no longer say that `session`, `progressCallback` and `showProgress`
    are refused: the refusal's error says it, and the sentence cost nine
    times its tokens (the token trim of 2026-10-21, see server.md).
- `db_functions.connection_credentials()` makes the check every session open
  makes (the URI is still configured). It refuses login (grant) connections:
  a process of its own would outlive the grant and skip the default role.
  They exist only in multi-tenant mode, which never serves `util`.
- `general.FUNCTION_GROUP_UTIL`: supported and in the defaults, never in
  `MULTI_TENANT_FUNCTION_GROUPS` (the user's decision, 2026-10-10).

## Command line (shell facts, checked on 2026-10-12)

- A list parameter takes every unnamed argument after it. So what follows a
  list is named: `--outputUrl`, or a copy's target as its connection
  attributes (`--user`, `--host`, `--port`, ...).
- `schema` is both a copy option and a connection attribute. The mapper
  wants `--options-schema` and `--connection-data-schema`; `util copy-* --help`
  lists the ambiguous ones. The prefixed forms exist only for ambiguous names
  (`--options-threads` is an invalid option), so they can't fence anything off.
- **Every connection attribute is a named option of a copy's target, and the
  last one given wins** (`define_named_args` 'C', `set_named_option` is a plain
  overwrite). So a copy refuses every client option the shell takes as a
  connection attribute (`_option_argument`); otherwise `{"host": ...}` would
  send the copy, and the target's stored password from stdin, to a server the
  client chose (found by `/security-review`, 2026-10-12). The shell decides:
  `shell.unparse_uri({"host": ..., name: "1"})` fails with "Invalid values in
  connection options" only for a name that is no attribute; any other answer
  refuses the option. Names go through the mapper's `from_camel_case_to_dashes`
  first (`sslMode`, `ssl_mode`, `Host`), ported as `_cli_name`.
- Option names must match `^[A-Za-z][A-Za-z0-9_-]*$`: the mapper splits a named
  argument at its first `:` or `=`, so `{"host=evil": 1}` became
  `--host=evil:str=1`, which sets `host`.
- An unnamed value is parsed as JSON. It stays a string only if that gives a
  string, or a number written differently ("1.50"). So "2024", "true" or
  "[1]" are refused as unnamed values, and so is anything starting with "-".
- Typed named values: `:str=` for strings. `:json=` works only where the
  option has no scalar type (lists, dicts). `:int=` fails on a `uint` option,
  so numbers and booleans go untyped.
- Strings expected as strings are unescaped (`\,` `\"` `\'` `\=`), so a value
  containing one is refused.
- Lists go as one unnamed JSON list (`'["a","b,c"]'`).
- Without a TTY `showProgress` is off, so the tool passes `--showProgress=true`.
- The child loads the user's plugins at startup (about 0.2 s).

## Output

One JSON object per line on stdout:

- `{"<level>": "text\n"}` is a message (info, status, note, warning, error).
- `{"info": "Stage"}` / `{"info": "Stage - done"}` with no `\n` is a stage
  without a count.
- `numericProgressUpdate` has the stage name (`description`).
- `throughputProgressUpdate` / `throughputProgressConfig` belong to the data
  stage. It has no name, so it is named from `dumpProgress` / `loadProgress`
  and the kind ("Dumping data", "Loading data", "Exporting data",
  "Importing data").
- A copy prefixes the text with "SRC: " / "TGT: ", and its loader prints its
  stages as status messages ("X..." / "X - done").
- Errors: the line with `exceptionInfo` is skipped. The last error line
  becomes the task's error; earlier ones are messages. Prompts are ignored.
- The data stage ends with the run (`finish_all()`).

## Crashes

- A crash is a death by a signal, or a non-zero exit with no error line. A
  missing watchdog marker counts as one too.
- Only `load_dump` is restarted: up to `MAX_RESTARTS` = 3 times, unless
  `progressFile` is "" or the URL is http(s) with no `progressFile` (the
  user's decision, 2026-10-12).
- A restart drops `resetProgress`, but only after some run reached the
  "Scanning metadata" stage. `Dump_loader::execute_tasks` resets the
  progress file before that stage. A crash before it would otherwise resume
  from another load's progress file and load nothing.
- The crashed run's running stages become failed, and its stderr tail (the
  "Warning: Charset id" lines left out) becomes error messages.

## Tests

`tests/unit/test_util.py` (105 with the parametrized cases):

- the registry on its own: events, since/dropped messages, failure, cancel
  through the canceller (also one asked before it was set), client binding,
  the per-client cap, pruning, waking a waiter, percent;
- `build_command` for every utility, typed values, a copy's target, refusals,
  among them every spelling of a connection attribute in a copy's options and
  option names holding `=` or `:`;
- `OutputParser` on lines recorded from a real shell: dump, copy (SRC/TGT),
  errors, non-JSON output;
- `run()` against a fake shell script: passwords on stdin, a crash resumed,
  one that keeps crashing, no resume for a dump, a PAR load or
  `progressFile: ""`, `resetProgress` kept when the crash came early, a
  failure that is no crash, a shell deaf to ^C killed after a cancel (the
  shell, not only the watchdog);
- `_options` and `_local_paths`, `file://` URLs and the file options included;
- the db-group gate;
- an end-to-end run over stdio on the shared sandbox: dump, load into another
  schema, a load whose shell process is SIGKILLed and resumed, export,
  import, a cancelled `maxRate` dump, a copy to the same server failing as a
  task, `task.list`, refusals.

Full suite on 2026-10-12, with a local shell build (mariadb-shell #71 merged
in): 700 passed, 1 failed, 3 skipped. The failure is
`test_rest_sql.py::test_rest_configure_metadata` ("Invalid options ...
substitutions"), which this branch does not touch.

## The extension's side

`code_ext` (PRs #43 / #44) follows a task with `util.get_task`, which this
server no longer offers - the extension has to move to `task.get` - at most once a
second (the tool answers as soon as a task changes, which while data moves
is many times a second), puts each task in the Actions tab with only its
totals and warnings under it, and writes a log file per task. It still strips
terminal colour codes from a copy's SRC:/TGT: prefixes; the shell process
prints none now.
