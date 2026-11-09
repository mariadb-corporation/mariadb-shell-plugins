# Online ALTER TABLE

Back to [`../PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md).

The design below is agreed with the user; the points marked **open** are
not. Built: the escalation (`lib/online_alter.py`), the setting, and
`deploySchema` running an update's steps itself (`lib/update_steps.py`),
Next steps 2 to 4, and the msm side of 5 (the live-table task and the
settings tools). Not built: the extension's tab, step 6, step 7.

## Goal

Run the `ALTER TABLE` statements of a schema change with the least
disruptive method the server accepts, in two places:

- `msm.deploySchema`, for the ALTERs of the update steps (Section 240);
- a new `msm.onlineAlterTable`, which the VS Code extension's Table Editor
  calls for a live table (a new **Online Alter Table** tab there).

## Architecture / key decisions

- **msm drives it, in Python; no stored procedure.** An earlier draft had a
  `msm_online_alter` procedure, created in Section 230, called from 240 and
  dropped in 290. It was dropped in favour of this design, and its template
  files with it. Reasons: the scripts stay clean, one implementation serves
  deployments and the live Table Editor, progress and cancelling are
  possible, and a hand-off to an external tool can happen at all (a
  procedure cannot start a process).
- **The SQL scripts keep plain ALTERs.** The language server writes them,
  `msm.edit_schema` keeps them in the pending update script, and
  `prepareRelease` moves them into Section 240 unchanged. A deployment
  script run without msm (`mariadb` client, CI) runs them as plain,
  possibly blocking ALTERs. That is wanted: it shows what MSM adds.
- **On by default.** `msm.project.json`'s `onlineAlter` (user's decision:
  default True, so a user never has to think about it):
  `{enabled?, maxCopyBytes?, galeraMaxBytes?, nboLock?, lockWait?,
  maxRetries?, tables?: {<table>: {enabled?, <the same option keys>}}}`;
  `true` / `false` stand for `{enabled: ...}`; missing = enabled with the
  defaults. New projects get `"onlineAlter": {"enabled": true}` written
  (`create_schema_project_folder`), so it can be found. The per-table
  entries are what the Table Editor's tab writes for a project's table.
- **A deployment's own setting.** `deploy_schema(online_alter=)` (shell
  `msm.deploySchema`, keyword `online_alter`) takes the same format, for
  the limits that depend on the environment. Precedence, most specific
  wins and the call at the same level (Claude's choice, not discussed with
  the user): project general → call general → project's table entry →
  call's table entry; `enabled` follows the same order. So a table the
  project marks `maxCopyBytes: 0` stays so unless the call names that
  table. Table names match exactly. `online_alter.deployment_settings`
  checks both before anything else of the deployment (a bad key or value
  is a ValueError naming where it is written: `msm.project.json,
  onlineAlter of table ...` or `the deployment, ...`), and
  `DeploymentSettings.options_for(table)` gives the Options, None = off.
  The mcp tool `msm.deploy_schema` takes it as `online_alter`.
- **How deploySchema reaches the ALTERs** (built: `lib/update_steps.py`,
  shell-free; `management._update_steps_handler` hooks it into
  `execute_msm_sql_script(statement_handler=)`, which lets a handler run a
  statement itself). In the generated deployment script they sit inside
  `CREATE PROCEDURE msm_update_<from>_to_<to>` and run only through
  `CALL msm_create_or_update()`. So:
  - the generator puts marker comments around each version's Section 240
    body (as it already uses `MSM-LOOP` markers), and msm reads the steps
    from the deployment script itself, so what deploys is the generated
    file;
  - the script runs as now up to `CALL msm_create_or_update()`; a fresh
    install keeps the CALL (nothing to alter);
  - an upgrade with online alter on replaces that CALL: msm sets
    `msm_schema_version` to 0, 0, 0, sets `@msm_schema_init = 0` and
    `@msm_current_version` (Section 370's `msm_auth` reads both), then runs
    each step in version order;
  - in a step, each top-level `ALTER TABLE` goes through the online-alter
    code; each run of other statements between them (DML, DROPs,
    DECLAREs, IFs) runs as one `BEGIN NOT ATOMIC ... END`, so it behaves as
    inside the procedure; an ALTER nested in a compound statement runs as
    written, and the log says it did not run online;
  - an ALTER that names `ALGORITHM` or `LOCK` itself runs as written, and
    so does one with `ONLINE`, `IGNORE` or `IF EXISTS`
    (`statements.alter_table_parts` gives schema, table, spec, modifiers),
    and one of a table online ALTER TABLE is off for;
  - a step body with a top-level `DECLARE` (or one whose compound
    statements the splitter cannot balance) runs whole as one block, its
    ALTERs as written: split, a later part would lose the variable;
  - the rest of the script runs as written.
  - Markers, as generated: `    -- ### MSM-UPDATE-STEP-START <from> <to>`
    / `-END <from> <to>` around `${section_240_...}` in the template. A
    script without them (generated before) runs as written, and the log
    says so. The handler is not used for a new schema or where
    `DeploymentSettings.any_enabled()` is False.
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
  - Each online ALTER is logged to the update log: `ALTER TABLE `t`:
    COPY (0.21 s)`, or `runs as written (<why>)`.
  - Checked equal: an upgrade 1.0.0 → 1.0.2 through msm and the same
    through the procedure (`online_alter=False`) end with the same SHOW
    CREATE TABLE, rows, version and no msm routines left
    (`tests/unit/test_update_steps.py`).
- **The escalation** (`lib/online_alter.py`), taken from the dropped
  procedure. `alter_table(session, schema, table, spec, options, *, monitor,
  on_event, cancel, poll_interval, sleep) -> Result(method, reason,
  table_size, galera, steps)`; `Options` (dataclass; `from_dict` takes the
  camelCase keys `maxCopyBytes`, `galeraMaxBytes`, `nboLock`, `lockWait`,
  `maxRetries`, the rest kept from `base`); `plan(size, galera, options)`
  gives the allowed and skipped steps; `check_spec` refuses a spec with its
  own `ALGORITHM`/`LOCK` clause (`statements.locking_clause`, clause starts
  only, so names, strings and comments do not count). No shell imports: the
  error number is read from `error.code`. `on_event` gets `step`, `refused`,
  `skipped`, `retry` and `progress` events. Each step sends the ALTER with explicit clauses, so
  the server either runs it exactly so or refuses at once (1845 / 1846)
  without touching data; a refusal goes to the next step:
  1. `ALTER TABLE t <spec>, ALGORITHM=INSTANT` (on Galera under TOI).
  2. `<spec>, ALGORITHM=NOCOPY, LOCK=NONE`; skipped on Galera when the
     table is larger than `galera_max_bytes`.
  3. `<spec>, ALGORITHM=COPY, LOCK=NONE` (native online COPY, MariaDB 11.2+;
     older servers refuse with 1846); skipped above `max_copy_bytes`, and
     on Galera above `galera_max_bytes`.
  4. Galera only, when `nbo_lock` is set: `SET SESSION wsrep_OSU_method =
     'NBO'`, then `ALTER TABLE t LOCK=SHARED|EXCLUSIVE, <spec>` with no
     ALGORITHM clause. Community Server rejects NBO with 1231 (skip the
     step); 1235 = NBO refused this statement. NBO blocks writes to the
     table (`EXCLUSIVE` reads too).
  5. `SHARED_LOCK`: `<spec>, LOCK=SHARED`, then 6. `EXCLUSIVE_LOCK`:
     `<spec>, LOCK=EXCLUSIVE`, no ALGORITHM (the server takes the cheapest
     one it can run under that lock). For what no unlocked step can do: a
     column `DEFAULT (NEXT VALUE FOR seq)` is refused by INSTANT, NOCOPY
     and `COPY, LOCK=NONE` ("Try LOCK=SHARED"). Capped by
     `maxLockedBytes` (`max_locked_bytes`), **default no limit** (user's
     decision: nothing a plain ALTER TABLE runs may fail because online
     ALTER TABLE is on; production sets a cap); on Galera also by
     `galeraMaxBytes` (TOI). The reason says writes were blocked.
  7. Otherwise `NEEDS_EXTERNAL`: nothing changed.
  - Table size = `DATA_LENGTH + INDEX_LENGTH` of `information_schema.TABLES`
    (an estimate for InnoDB). Galera = session `wsrep_on` ON and
    `wsrep_provider` not `none`; a session on `RSU` is refused (it alters
    one node only); the session's `wsrep_OSU_method` and
    `lock_wait_timeout` are restored afterwards, also after an error.
  - Each attempt sets `lock_wait_timeout = lock_wait`; a timeout (1205) is
    retried with backoff 2, 4, 8 ... capped at 30 s, up to `max_retries`,
    then fails. `lock_wait` also covers the lock at the end of a long COPY:
    if that times out, the copy is lost and the step starts again.
  - Any other error stops it and is passed on.
  - Replicas must run the same MariaDB version: the binary log records the
    statement with its ALGORITHM clause.
- **Defaults** (the dropped procedure's examples): `max_copy_bytes` 10 GiB,
  `galera_max_bytes` 256 MiB, `nbo_lock` off (it blocks writes: choose it
  deliberately), `lock_wait` 5 s, `max_retries` 10 (about 5 minutes worst
  case).
- **Progress and cancelling.** With a `monitor` session (a second session
  on the same server), a thread polls `information_schema.PROCESSLIST`
  (`STAGE`, `MAX_STAGE`, `PROGRESS`, `STATE`) for the ALTER's connection id
  while each step runs, and once `cancel` is set it sends `KILL QUERY`; the
  ALTER then fails with 1317 and `OnlineAlterCancelled` is raised. Without
  a monitor, a cancel waits for the running step. A failing poll ends the
  watch, never the step. The task (Next steps 5) reports these events.
- **Live tables (Table Editor).** mcp `msm.online_alter_table(connection_id,
  schema, table, spec, options)` (served with msm + db) starts a task over
  `mysqlsh.task_manager` (type `msm`, kind `online_alter_table`; the task
  tools are served with it, `general.serves_tasks`), so it shows in the
  extension's Tasks view (`mariadb.tasks`, the `task.*` tools). The spec and
  options are checked before the task starts (a tool error). The task opens
  two sessions of its own from `db_functions.connection_data` (the ALTER, and
  the monitor for progress and KILL QUERY) and closes them;
  `online_alter.run_in_task` makes each step a stage, the server's progress
  the stage's (combined over the server's stages, never going back), and
  refusals, skips, lock retries and the server's STATE messages; a
  `task.cancel` reaches the ALTER through `set_canceller`. **NEEDS_EXTERNAL
  completes the task** (Claude's choice: nothing failed, nothing changed)
  with a warning message and `applied: false` in the result `{method,
  applied, reason, table_size, galera, steps}`. The shell function
  `msm.onlineAlterTable(schema, table, spec, options=, session=)` runs it
  synchronously on the given (or global) session: no task, no monitor. No
  temporary procedure, so no CREATE ROUTINE privilege is needed.
- **The tab's settings for a project table**: mcp
  `msm.get_online_alter_settings(table?, schema_project_path)` ({defaults,
  setting, table, enabled, options}) and `msm.set_online_alter_table(table,
  settings?, schema_project_path)` (writes `onlineAlter.tables.<table>`,
  None/{} removes the entry; reads the raw file, so the derived
  `copyrights` list is not written back; a project without the key gets
  `{"enabled": true, "tables": ...}`; unchanged = nothing written); shell
  `msm.getOnlineAlterSettings` / `msm.setOnlineAlterTable`; lib
  `management.get_online_alter_settings` / `set_online_alter_table`.
  `msm.get_online_alter_defaults()` (shell `msm.getOnlineAlterDefaults`,
  served with msm, no project) gives the same `defaults`, for a live
  table's tab, so the extension keeps no copy of them.
- **The extension's side** (decided by the extension's session with the
  user): a live table's settings live in VS Code's workspace state, per
  connection, schema and table (a rename moves them); online ALTER TABLE is
  on by default for live tables too. A project table's Save runs
  msm.edit_schema, then `msm.set_online_alter_table` under the table's
  (new) name; on a `rename_table` the extension moves the entry itself
  (set new, set old to null) until msm.edit_schema carries it over (step
  6: tell the extension's session then). Live Apply sends `merge_alters`
  where the binary has it; each ALTER of the table with a `spec` goes to
  `msm.online_alter_table` (the table's options without `enabled`), other
  statements run in order as before; NEEDS_EXTERNAL stops the Save with
  the reason, the statements before it stay run.
  The extension today applies a live table's statements one by one through
  `db.execute_sql` on the UI backend session (vscode-extension
  `src/tableEditor/tableSources.ts`, `LiveTableSource`); the ALTERs of an
  online-alter table go to the task instead.

## Next steps

1. (Done) The deployment template's dead `${section_260_...}` placeholders
   (no script has a Section 260; Section 290 reaches the script through
   `${section_290_removal_of_helpers}` in Section 390) are removed.
2. (Done) `lib/online_alter.py` and `tests/unit/test_online_alter.py`: the
   decisions against a fake session (Galera paths included), and on the
   sandbox INSTANT / NOCOPY / COPY, NEEDS_EXTERNAL leaving the table as it
   was, a held metadata lock timing out, a cancel stopping a blocked ALTER
   by KILL QUERY (fails without it: revert-probed), progress read during a
   COPY. Galera itself is only faked: no Galera or Enterprise Server in the
   suite.
3. (Done) The `onlineAlter` setting and `deploy_schema(online_alter=)`,
   checked up front; nothing uses the resolved settings until step 4.
   Still to do with step 5 or 6: a function the Table Editor writes a
   table's entry with, and `msm.edit_schema` carrying a table's entry over
   a `rename_table` (and dropping it with `drop_table`). The ai-plugins
   schema-management skills list the keys of `msm.project.json` and lack
   `onlineAlter` (re-run `sync-skills.sh` after editing).
4. (Done) Markers, `deploy_schema` running the upgrade steps itself, the
   locked steps, and the mcp tool `msm.deploy_schema(online_alter=)`. Not
   compared yet: roles and grants (the authorization part runs as before,
   from the same session variables).
5. (msm side done) `msm.online_alter_table` as a task, the settings tools.
   The extension's Online Alter Table tab and its Task are the
   vscode-extension's (asked of its session): for a project table, the tab
   reads `msm.get_online_alter_settings` and writes
   `msm.set_online_alter_table` on Save, and the deployment does the rest;
   for a live table, Apply starts `msm.online_alter_table` for each ALTER
   TABLE (the language server's `spec` with `merge_alters`, step 6) and
   follows the task.
6. **Merge the ALTERs of one table per Save** in `msm.edit_schema`: the
   language server writes several (e.g. DROP FK, MODIFY, ADD FK), and each
   would be its own escalation and maybe its own table copy. The binary
   does the merging (lsp-server #33): send `"merge_alters": true` at the top
   of the edit request, only where `edit --ops` prints `"merge_alters":
   true` (older binaries ignore the member). Every ALTER TABLE in the
   migration then also has `edits` (indexes), `table`, `spec` (the text
   after ``ALTER TABLE `t` ``) and `clauses: [{edit, spec, action, object,
   name, table, new_name?}]`; a merged one (2+ clauses) is tagged `alter`
   `table`, `edit` = its first clause's, and stands where that clause's
   statement stood. RENAME TABLE, CREATE, DROP get none of these. Order
   between tables holds (an ALTER moves past another table's only without a
   foreign key between them), so a modify_column of a column another
   table's FK references keeps that table's ADD CONSTRAINT between the
   change and later ALTERs of the same table. Pairs MariaDB refuses or
   reads differently in one statement stay separate (lsp-server
   `docs/edit-format.md`, "Merged ALTER TABLE statements"). msm's part:
   send the flag, keep the merged statement under one comment naming all
   its edits (`_describe` takes one edit today), and use `spec` where the
   Table Editor's live path hands an ALTER to `msm.onlineAlterTable`.
7. **Later, a separate step:** `NEEDS_EXTERNAL` and resuming. Prepare for
   it now (stop before any statement of the failing version runs; record
   the steps done so a re-run continues instead of failing with 32101 on
   version 0.0.0). Then msm runs the external tool itself: it downloads
   MariaDB's own builds of gh-ost as needed. gh-ost reads the binary log
   and does not work on Galera, nor with some foreign keys; there
   pt-online-schema-change is the tool.

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
