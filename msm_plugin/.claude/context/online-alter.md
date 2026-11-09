# Online ALTER TABLE

Back to [`../PROJECT_CONTEXT.md`](../PROJECT_CONTEXT.md).

The design below is agreed with the user; the points marked **open** are
not. Built: the escalation itself (`lib/online_alter.py`, Next steps 2).
Not built: everything that calls it.

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
  The mcp tool `msm.deploy_schema` gets the option with Next steps 4.
- **How deploySchema reaches the ALTERs.** In the generated deployment
  script they sit inside `CREATE PROCEDURE msm_update_<from>_to_<to>` and
  run only through `CALL msm_create_or_update()`. So:
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
  - an ALTER that names `ALGORITHM` or `LOCK` itself runs as written;
  - the rest of the script runs as written.
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
  5. Otherwise `NEEDS_EXTERNAL`: nothing changed.
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
- **Live tables (Table Editor).** `msm.onlineAlterTable` (plugin function
  and mcp tool) runs as a background task over `mysqlsh.task_manager`, so it
  shows in the extension's Tasks view (`mariadb.tasks`, the `task.*`
  tools). No temporary procedure, so no CREATE ROUTINE privilege is needed.
  The extension today applies a live table's statements one by one through
  `db.execute_sql` on the UI backend session (vscode-extension
  `src/tableEditor/tableSources.ts`, `LiveTableSource`); the ALTERs of an
  online-alter table go to the task instead. Where the extension keeps a
  live table's settings: **open**.

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
4. Markers around each 240 body in `generate_deployment_script`, and
   `deploy_schema` running the upgrade steps itself. Test that an upgrade
   through msm ends in the same schema, roles and version as one through
   `CALL msm_create_or_update()`.
5. `msm.onlineAlterTable` as a task, the mcp tool, then the extension's
   Online Alter Table tab and its Task.
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
