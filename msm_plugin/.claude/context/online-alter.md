# Online ALTER TABLE

Back to [`../PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md).

msm runs `ALTER TABLE` statements the least disruptive way the server
accepts, in two places:

- `msm.deploySchema`, for the ALTERs of the update steps (Section 240);
- `msm.online_alter_table` (mcp, a background task), which the VS Code
  extension's Table Editor calls for a live table from its **Online Alter
  Table** tab.

The design is agreed with the user; choices Claude made on its own are
marked as such. Built across mariadb-shell-plugins #95, lsp-server #33/#34
(merged ALTERs) and the vscode-extension's tab.

## Architecture / key decisions

- **msm drives it, in Python; no stored procedure.** An earlier draft had a
  `msm_online_alter` procedure, created in Section 230, called from 240 and
  dropped in 290; it was dropped, and its template files with it. Reasons:
  the scripts stay clean, one implementation serves deployments and the
  live Table Editor, progress and cancelling are possible, and a hand-off
  to an external tool can happen at all (a procedure cannot start a
  process).
- **The SQL scripts keep plain ALTERs.** The language server writes them,
  `msm.edit_schema` keeps them in the pending update script, and
  `prepareRelease` moves them into Section 240 unchanged. A deployment
  script run without msm (`mariadb` client, CI) runs them as plain,
  possibly blocking ALTERs. That is wanted: it shows what MSM adds.
- **On by default.** `msm.project.json`'s `onlineAlter` (user's decision:
  default True, so a user never has to think about it):
  `{enabled?, maxRebuildBytes?, galeraMaxBytes?, nboLock?, maxLockedBytes?,
  lockWait?, maxRetries?, tables?: {<table>: {enabled?, <the same option
  keys>}}}`; `true` / `false` stand for `{enabled: ...}`; missing = enabled
  with the defaults. New projects get `"onlineAlter": {"enabled": true}`
  written (`create_schema_project_folder`), so it can be found. The
  per-table entries are what the Table Editor's tab writes for a project's
  table.
- **A deployment's own setting.** `deploy_schema(online_alter=)` (shell
  `msm.deploySchema`, mcp `msm.deploy_schema`, keyword `online_alter`)
  takes the same format, for the limits that depend on the environment.
  Precedence, most specific wins and the call at the same level (Claude's
  choice): project general → call general → project's table entry → call's
  table entry; `enabled` follows the same order. So a table the project
  marks `maxRebuildBytes: 0` stays so unless the call names that table. Table
  names match exactly. The `tables` entries are the project's tables: an
  ALTER of a step that names another schema's table gets the general
  values only (`options_for(None)`; user's decision). `online_alter.deployment_settings` checks both
  before anything else of the deployment (a bad key or value is a
  ValueError naming where it is written: `msm.project.json, onlineAlter of
  table ...` or `the deployment, ...`), and
  `DeploymentSettings.options_for(table)` gives the Options, None = off.
- **How deploySchema reaches the ALTERs** (`lib/update_steps.py`,
  shell-free; `management._update_steps_handler` hooks it into
  `execute_msm_sql_script(statement_handler=)`, which lets a handler run a
  statement itself). In the generated deployment script the ALTERs sit
  inside `CREATE PROCEDURE msm_update_<from>_to_<to>` and run only through
  `CALL msm_create_or_update()`. So:
  - the generator puts markers around each version's Section 240 body
    (`    -- ### MSM-UPDATE-STEP-START <from> <to>` / `-END <from> <to>`
    around `${section_240_...}` in the template), and msm reads the steps
    from the deployment script itself, so what deploys is the generated
    file; a script without them (generated before) runs as written, and
    the log says so;
  - the script runs as written up to `CALL msm_create_or_update()`; a
    fresh install keeps the CALL (nothing to alter), and so does an
    upgrade where `DeploymentSettings.any_enabled()` is False;
  - otherwise an upgrade replaces that CALL: msm sets `msm_schema_version`
    to 0, 0, 0, sets `@msm_schema_init = 0` and `@msm_current_version`
    (Section 370's `msm_auth` reads both), then runs each step in version
    order;
  - in a step, each top-level `ALTER TABLE` goes through the escalation;
    each run of other statements between them (DML, DROPs, IFs) runs as
    one `BEGIN NOT ATOMIC ... END`, so it behaves as inside the procedure;
    an ALTER nested in a compound statement runs as written;
  - an ALTER that names `ALGORITHM` or `LOCK` itself runs as written, and
    so does one with `ONLINE`, `IGNORE` or `IF EXISTS`
    (`statements.alter_table_parts` gives schema, table, spec, modifiers),
    one that changes the partitioning (`statements.changes_partitioning`:
    the word PARTITION/PARTITIONS/PARTITIONING outside quotes and comments;
    the server takes no ALGORITHM/LOCK after partition options; the mcp
    tool and `check_spec` refuse such a spec), and one of a table online
    ALTER TABLE is off for;
  - a step body with a top-level `DECLARE` (or one whose compound
    statements the splitter cannot balance) runs whole as one block, its
    ALTERs as written: split, a later part would lose the variable;
  - the rest of the script runs as written.
  - `run_update` checks first that a step starts at the schema's version
    and raises a ValueError otherwise (the procedure's SIGNAL 32102): the
    guard in `deploy_schema` reads the project's update scripts, while a
    deployment script of an older target holds fewer steps, and without
    the check Section 910 would stamp the target version onto an unchanged
    schema. A statement of a step that fails is logged with its SQL (the
    caller sees only the CALL the steps stand in for). An ALTER TABLE of a
    table `information_schema.TABLES` does not list for the session (a
    temporary table the step made) runs as written
    (`online_alter.TableNotFoundError`, raised before anything ran).
  - **Top-level statements** (`statements.top_level_statements`): a
    tokenizer (quoted names, strings, comments skipped) with a stack. An
    `IF`/`BEGIN`/`CASE`/`LOOP`/`WHILE`/`REPEAT` opens a block only where a
    statement starts (start, after `;`, a label's `:`, `BEGIN`, `ATOMIC`,
    `DO`, `LOOP`, `REPEAT`, or `THEN`/`ELSE` outside a CASE expression); a
    CASE elsewhere is an expression closed by its own END; `END IF` /
    `END LOOP` ... close one. So `IF (cond) THEN` is a statement and
    `IF(a, b, c)` a function.
  - `NeedsExternalToolError` (update_steps; step, table, sql, result)
    stops the deployment: the statements before it ran, the version VIEW
    stays 0, 0, 0. `deploy_schema` keeps the type through its own wrapping
    (`_deployment_error`); every other failure is still a plain Exception.
  - Each online ALTER is logged to the update log: ``ALTER TABLE `t`:
    COPY (0.21 s)``, or `runs as written (<why>)`.
  - Checked equal (`tests/unit/test_update_steps.py`): an upgrade
    1.0.0 → 1.0.2 through msm and the same through the procedure
    (`online_alter=False`) end with the same SHOW CREATE TABLE, rows,
    version and no msm routines left. Roles and grants are not compared.
- **The escalation** (`lib/online_alter.py`, no shell imports: the error
  number is read from `error.code`). `alter_table(session, schema, table,
  spec, options, *, monitor, on_event, cancel, poll_interval, sleep) ->
  Result(method, reason, table_size, galera, steps)`; `Options` (dataclass;
  `from_dict` takes the camelCase keys `maxRebuildBytes`, `galeraMaxBytes`,
  `nboLock`, `maxLockedBytes`, `lockWait`, `maxRetries`, the rest kept from
  `base`; `to_dict` gives them back); `plan(size, galera, options)` gives
  the allowed and skipped steps; `check_spec` refuses a spec with its own
  `ALGORITHM`/`LOCK` clause or a partitioning change
  (`as_written_reason`, which `update_steps._alter` uses too, to run such
  an ALTER as written; `statements.locking_clause`: anywhere
  outside quotes, comments and parentheses, also after a table option
  without a comma; `LOCK` is reserved, `ALGORITHM` counts only before `=`
  or one of its values, as a column may be named algorithm, and not as
  the name in `CHANGE [COLUMN] algorithm copy ...`; a comment counts as a
  blank, so `ALGORITHM /* x */ = COPY` is found). `on_event` gets
  `step`, `applied`, `refused`, `skipped` (these three with `seconds` and
  `message`), `retry` and `progress` events, in `METHODS` order, none
  after the applied step. Each step
  sends the ALTER with explicit clauses, so the server either runs it
  exactly so or refuses at once (1845 / 1846) without touching data; a
  refusal goes to the next step:
  1. `ALTER TABLE t <spec>, ALGORITHM=INSTANT` (on Galera under TOI).
  2. `<spec>, ALGORITHM=NOCOPY, LOCK=NONE`; skipped on Galera when the
     table is larger than `galera_max_bytes`.
  3. `<spec>, ALGORITHM=INPLACE, LOCK=NONE`: InnoDB rebuilds the table in
     place, reads and writes go on (primary key added/dropped, NULL to NOT
     NULL, ROW_FORMAT, FORCE; user's decision).
     A column type change is refused here too. Skipped like COPY.
  4. `<spec>, ALGORITHM=COPY, LOCK=NONE` (native online COPY, MariaDB 11.2+;
     older servers refuse with 1846). INPLACE and COPY are `REBUILDS`,
     both skipped above `max_rebuild_bytes` (`maxRebuildBytes`, no other
     name accepted), and on Galera above
     `galera_max_bytes`.
  5. Galera only, when `nbo_lock` is set: `SET SESSION wsrep_OSU_method =
     'NBO'`, then `ALTER TABLE t LOCK=SHARED|EXCLUSIVE, <spec>` with no
     ALGORITHM clause. Community Server rejects NBO with 1231 (skip the
     step); 1235 = NBO refused this statement. NBO blocks writes to the
     table (`EXCLUSIVE` reads too).
  6. `SHARED_LOCK`: `<spec>, LOCK=SHARED`, then 7. `EXCLUSIVE_LOCK`:
     `<spec>, LOCK=EXCLUSIVE`, no ALGORITHM (the server takes the cheapest
     one it can run under that lock). For what no unlocked step can do: a
     column `DEFAULT (NEXT VALUE FOR seq)` is refused by INSTANT, NOCOPY,
     INPLACE and `COPY, LOCK=NONE` ("Try LOCK=SHARED"). Capped by
     `maxLockedBytes` (`max_locked_bytes`), **default no limit** (user's
     decision: nothing a plain ALTER TABLE runs may fail because online
     ALTER TABLE is on; production sets a cap); on Galera also by
     `galeraMaxBytes` (TOI). The reason says writes were blocked.
  8. Otherwise `NEEDS_EXTERNAL`: nothing changed. Its reason
     (`_needs_external_reason(steps)`) lists each distinct skip reason, then
     `the server refused <methods> (last: <its last refusal>)`, so it shows
     which limit would allow more and what the server refused anyway.
  - Table size = `DATA_LENGTH + INDEX_LENGTH` of `information_schema.TABLES`
    (an estimate for InnoDB). Galera = session `wsrep_on` ON and
    `wsrep_provider` not `none`; a session on `RSU` is refused (it alters
    one node only); the session's `wsrep_OSU_method` and
    `lock_wait_timeout` are restored afterwards, also after an error.
  - `lock_wait_timeout = lock_wait` is set once per ALTER; a timeout (1205) is
    retried with backoff 2, 4, 8 ... capped at 30 s, up to `max_retries`,
    then fails. `lock_wait` also covers the lock at the end of a long COPY:
    if that times out, the copy is lost and the step starts again.
  - Any other error stops it and is passed on.
  - Replicas must run the same MariaDB version: the binary log records the
    statement with its ALGORITHM clause.
- **Defaults** (`Options()`, from the dropped procedure's examples):
  `max_rebuild_bytes` 10 GiB, `galera_max_bytes` 256 MiB, `nbo_lock` off (it
  blocks writes: choose it deliberately), `max_locked_bytes` no limit,
  `lock_wait` 5 s, `max_retries` 10 (about 5 minutes worst case).
- **Galera differs from the procedure** (stated in the description of
  #95): with online ALTER on by default and `galera_max_bytes` 256 MiB, an
  update on Galera whose ALTER of a table over 256 MiB is not INSTANT
  stops with `NeedsExternalToolError` unless `nboLock` is set (or
  `galeraMaxBytes` raised, or the table / deployment turned off); the
  procedure ran it as written under TOI, pausing the cluster. The one exception to "nothing a
  plain ALTER runs fails because online ALTER is on" (`maxLockedBytes`).
- **Progress and cancelling.** With a `monitor` session (a second session
  on the same server), a thread polls `information_schema.PROCESSLIST`
  (`STAGE`, `MAX_STAGE`, `PROGRESS`, `STATE`) for the ALTER's connection id
  while each step runs, and once `cancel` is set it sends `KILL QUERY`; the
  ALTER then fails with 1317 and `OnlineAlterCancelled` is raised. Without
  a monitor, a cancel waits for the running step. A failing poll ends the
  watch, never the step.
- **Live tables (Table Editor).** mcp `msm.online_alter_table(connection_id,
  schema, table, spec, options)` (served with msm + db) starts a task over
  `mysqlsh.task_manager` (type `msm`, kind `online_alter_table`; the task
  tools are served with it, `general.serves_tasks`), so it shows in the
  extension's Tasks view (`mariadb.tasks`, the `task.*` tools). The spec and
  options are checked before the task starts (a tool error). The task opens
  two sessions of its own through `db_functions.session_opener` (the ALTER,
  and the monitor for progress and KILL QUERY) and closes them; that opens
  them as the connection's own session is opened (URI still configured or
  sign-in grant alive, the user's default role in multi-tenant mode), never
  with `shell.open_session` on the connection data; once the connection
  is closed (`db.close`, expiry) the opener refuses like an unknown id;
  `online_alter.run_in_task` makes each step a stage, in `METHODS` order
  up to the step that applies the change (`alter_table` records the skips
  where their steps would run, and none after the applied one): the
  applied step's stage ends `completed` with its seconds, a refused one
  `skipped` with its seconds, one skipped by a limit `skipped` without
  (`task.stage_finished(stage, seconds, status=...)`, the `SKIPPED` stage
  status of mariadb-shell's `mysqlsh.task_manager`; on a shell without it
  the stage ends `completed`, TypeError fallback). The server's progress
  is the stage's (combined over the server's stages, never going back),
  and refusals, skips, lock retries and the server's STATE are messages; a
  `task.cancel` reaches the ALTER through `set_canceller`. **NEEDS_EXTERNAL
  completes the task** (Claude's choice: nothing failed, nothing changed)
  with a warning message and `applied: false` in the result `{method,
  applied, reason, table_size, galera, steps}`. The shell function
  `msm.onlineAlterTable(schema, table, spec, options=, session=)` runs it
  synchronously on the given (or global) session: no task, no monitor. No
  temporary procedure, so no CREATE ROUTINE privilege is needed.
- **The tab's settings tools**: mcp `msm.get_online_alter_settings(table?,
  schema_project_path)` ({defaults, setting, table, enabled, options}; with
  no table `setting` holds the general level and `tables`,
  `enabled`/`options` are None) reads. Writing goes through
  `msm.set_project_settings(settings, schema_project_path)` (see
  PROJECT_CONTEXT "Project settings"): `{"onlineAlter": <the whole
  setting>}` replaces general values and tables at once, null removes it
  (msm's defaults, on); checked with `read_setting` before anything is
  written. The per-table and general-level tools (`set_online_alter_table`,
  `set_online_alter_defaults`, #101) were removed for it on 2026-11-10 (the
  user wants one tool for msm.project.json, not one per setting).
  `msm.get_online_alter_defaults()` (shell `msm.getOnlineAlterDefaults`,
  served with msm, no project) gives the same `defaults`, for a live
  table's tab, so the extension keeps no copy of them.
- **One ALTER TABLE per table and Save** (`msm.edit_schema`): each ALTER is
  its own escalation and maybe its own table copy, so the language server
  merges a table's ALTERs where asked: `lsp.merges_alters(binary)` (its
  `edit --ops` says `"merge_alters": true`, lsp-server #33) → the request
  gets `"merge_alters": true` (older binaries ignore it, so it is sent only
  then). Every ALTER TABLE in the migration then also has `edits`
  (indexes), `table`, `spec` (the text after ``ALTER TABLE `t` ``) and
  `clauses: [{edit, spec, action, object, name, table, new_name?}]`; a
  merged one (2+ clauses) is tagged `alter` `table`, `edit` = its first
  clause's, and stands where that clause's statement stood. RENAME TABLE,
  CREATE, DROP get none of these. Order between tables holds, so a
  modify_column of a column another table's FK references keeps that
  table's ADD CONSTRAINT between the change and later ALTERs of the same
  table. `migration_statements` keeps a merged statement once, under a
  `-- msm.editSchema:` line for each of its edits.
  - **The merge contract** msm needs (agreed with the lsp-server side,
    lsp-server #34; the pairs are listed in lsp-server
    `docs/edit-format.md`, "Merged ALTER TABLE statements"): a merged
    statement gives the separate statements' result with
    `foreign_key_checks` 1 (a live session) and 0 (every MSM deployment,
    Section 010), and under each clause msm adds it either applies or is
    refused with 1845/1846, never another error. `DROP FOREIGN KEY fk` and
    a later `ADD CONSTRAINT fk` (same name) stay two statements. Some
    merges are refused by a step the separate statements pass (1845 for
    `ADD COLUMN` + its FK under NOCOPY, checks off): one more step, no
    failure.
- **A table's settings follow it**: `editing.online_alter_settings_text`
  moves a table's `onlineAlter.tables` entry over a `rename_table` and
  drops it with a `drop_table`, in the edits' order, as one more text of
  `edit_schema`'s all-or-nothing write (msm.project.json is then in
  `files`).
- **The extension's side** (decided by the extension's session with the
  user): a live table's settings live in VS Code's workspace state, per
  connection, schema and table (a rename moves them); online ALTER TABLE is
  on by default for live tables too. A project table's Save runs
  msm.edit_schema, then writes the setting with the tab's entry changed
  through `msm.set_project_settings`, under the table's post-Save name (msm
  has moved the entry over a rename already). Its SQL preview asks mariadb-lsp
  for merged ALTERs too, to match what edit_schema keeps. Live Apply sends
  `merge_alters` where the binary has it; each ALTER of the table with a
  `spec` goes to `msm.online_alter_table` (the table's options without
  `enabled`) and is followed in the Tasks view; other statements run in
  order through `db.execute_sql` as before (vscode-extension
  `src/tableEditor/tableSources.ts`, `LiveTableSource`); NEEDS_EXTERNAL
  stops the Save with the reason, the statements before it stay run.

## Next steps

1. **NEEDS_EXTERNAL and resuming** (a separate step, user's decision). Stop
   before any statement of the failing version runs where that can be
   known; record the steps done so a re-run continues instead of failing
   with 32101 on version 0.0.0. Then msm runs the external tool itself: it
   downloads MariaDB's own builds of gh-ost as needed. gh-ost reads the
   binary log and does not work on Galera, nor with some foreign keys;
   there pt-online-schema-change is the tool.
2. Compare roles and grants after an upgrade run by msm with one run by the
   procedure (the authorization part runs as before, from the same session
   variables, but no test checks it).
3. Galera and NBO are tested against a fake session only: run the steps on
   a Galera cluster (Community: NBO refused with 1231; Enterprise: NBO).
4. The ai-plugins schema-management skills list the keys of
   `msm.project.json` and lack `onlineAlter`, and know nothing of online
   ALTER TABLE in deployments (re-run `sync-skills.sh` after editing).

5. Open from the reviews of #95: `_Watch.__exit__` joins the monitor
   thread without a timeout (a monitor session hanging in its PROCESSLIST
   read keeps the task running after the ALTER returned); "is this the
   project's schema" in `update_steps._alter` compares case-sensitively,
   which is wrong under `lower_case_table_names` 1/2 (needs the server's
   setting, not plain case folding).

## Gotchas / things not to repeat

- **A shell session works from a second Python thread while `run_sql`
  blocks in the first** (the shell releases the GIL): that is what the
  progress poll and `KILL QUERY` rely on. Checked on MariaDB 12.3.2.
- What 12.3.2 answers: a refused step is 1846 with the reason and a hint
  (`ALGORITHM=INSTANT is not supported. Reason: ADD INDEX. Try
  ALGORITHM=NOCOPY`); a column type change is refused by INSTANT, NOCOPY
  and INPLACE alike and runs with `COPY, LOCK=NONE`; `wsrep_OSU_method =
  'NBO'` without Galera is 1231; a killed ALTER is 1317. `PROGRESS` seems
  to be per stage (back to 0 at the next stage), so report stage x of y
  with it rather than one percentage.
- **`foreign_key_checks=0` changes what an ALTER does**: a `DROP FOREIGN
  KEY fk, ..., ADD CONSTRAINT fk ...` in one statement fails with 1826
  "Duplicate FOREIGN KEY constraint name" under checks off for every
  algorithm but COPY, the plain statement too, while it works with checks
  on. Anything about ALTERs that MSM deployments run must be checked with
  checks off as well (Section 010 sets them off).
- Do not treat errors other than 1845/1846/1235 as "try the next step":
  1826 and the like also mean a real mistake in the migration, which a
  later step would hide or fail on differently.
- A dry run on an empty `CREATE TABLE ... LIKE` copy usually, but not
  always, predicts the real table: whether InnoDB can change a table
  instantly can depend on its history (earlier instant changes, an old row
  format).
- 1235 means "not supported" in general; outside the NBO step it also moves
  on to the next step.
- Partition management (`ADD`/`DROP`/`REORGANIZE PARTITION`) under NBO needs
  a syntax the escalation does not build: no NBO for those.
- Section 010 sets `FOREIGN_KEY_CHECKS=0`, which lets an added foreign key
  run without a table copy in a deployment; a live Table Editor session
  does not have it.
- `deploy_schema` re-raises a failed deployment with its own message;
  `_deployment_error` keeps the `NeedsExternalToolError` type
  (`NeedsExternalToolError(message)`, which takes the message only).
- A spec that ends in a `--` / `#` comment gets a line break from
  `check_spec`, or the steps' `, ALGORITHM=...` would be commented out.
- Table size reads `TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')`:
  MariaDB lists a system-versioned table as the latter.
