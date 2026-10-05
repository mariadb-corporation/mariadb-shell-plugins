# The sibling plugins

`msm_plugin` and `mrs_plugin`: what was changed in them from here, how to run
their suites, and the failures that are theirs rather than ours.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Current state

- **Sibling `msm_plugin` was changed in an EARLIER session** (own commit, own suite: 9 pass
  — see the invocation note in Gotchas):
  - MySQL -> MariaDB rebrand of all PROSE/branding. Legal notices were NOT word-substituted;
    the USER instead ADDED a second line `Copyright (c) 2026, MariaDB plc.` under the
    existing Oracle line, leaving Oracle's notice and the "authors of MySQL hereby grant"
    FOSS exception intact. **Follow that additive pattern**; do not rewrite Oracle's LICENSE
    or the exception paragraph.
  - **The MariaDB notice is exactly `Copyright (c) 2026, MariaDB plc.`** — NOT "MariaDB plc
    and/or its affiliates." (that phrasing is Oracle's and is wrong for MariaDB) and not a
    bare "MariaDB". Both wrong forms existed and were normalized across 55 occurrences in 50
    files. Only the MariaDB line is ever rewritten; Oracle's "and/or its affiliates" stays.
  - `tests/conftest.py` now uses `from mysqlsh.globals import sandbox` +
    `sandbox.deploy/kill` instead of `mysqlsh.globals.dba.deploy_sandbox_instance` (this
    build of mariadb-shell has NO `dba` global), with `ssl: False` and an `int()` port.
  - `run_tests.py` prefers `mariadb-shell` over `mysqlsh`.
  - `lib/management.py deploy_schema` gained `backup: bool = False` (see Gotchas).

- **`mrs_plugin` queries made valid under MariaDB's `ONLY_FULL_GROUP_BY` (2026-10-05, from the msm_plugin session, `d1c82c8c` on `wip/msm_mrs_fixes`):** `lib/content_files.py` and `lib/db_objects.py` dropped a redundant outer `GROUP BY <pk>` (selecting `al.changed_at` instead of `MAX(...)`), `lib/roles.py` lists every selected column in its `GROUP BY`. MySQL 8 accepts columns that depend on a grouped primary key; MariaDB does not, so `CREATE REST VIEW` failed (1055) inside MSM deployment scripts, which set that mode. `tests/conftest.py` now uses `sql_mode='ONLY_FULL_GROUP_BY'` instead of `''`: 247 pass, 2 skipped. Details in `msm_plugin/.claude/PROJECT_CONTEXT.md` (issue 12).

- **Sibling plugins touched again THIS session** (rebranding cherry-pick from
  `mariadb/rennox/rebranding` — 759c375d/7f119fd6/f11a3897 — plus follow-up fixes):
  - **`msm_plugin`: 9 pass.** `run_tests.py` fully de-`MYSQLSH`'d, `dot_mariadb_shell`,
    `pip install -r requirements.txt` + a return-code check. A `pip install ... msm` line
    was removed: `msm` is an unrelated PyPI package (a Minecraft server manager), and this
    plugin's own code comes from the runner's symlink.
  - **`mrs_plugin`: 228 pass, 20 fail, 0 errors** (was 72 pass / 15 fail / 161 errors).
    `run_tests.py` gained the missing `pip install -r requirements.txt` step (it had NONE,
    so the declared `pytest-mock` was never installed -> 81 `fixture 'mocker' not found`
    errors), `pytest-asyncio` added to requirements (`asyncio_mode = auto` was already set
    in both ini files, so the plugin was the only thing missing -> 9 async failures), and
    the stale `"name": "mrs"` service expectation fixed in 12 places (see Gotchas).
    `--mysqlsh` renamed to `--shell-options`; `MYSQLSH_FLAGS` -> `SHELL_FLAGS`.
  - **All three `requirements.txt` had `pytest >= 6.1.2, <= 7.0`**, which resolves to
    exactly 7.0.0 — and 7.0.0 crashes on Python 3.14 with
    `AttributeError: module 'ast' has no attribute 'Str'` (`ast.Str` was removed in 3.12).
    Now `pytest >= 7.4`, no upper bound. Verified by bisecting on a real suite: 7.0.0
    crashes, 7.4.4 / 8.4.2 / 9.1.1 all pass. The inline package lists in the runners
    existed to dodge this pin; fixing the pin is what let them switch to `-r`.

## Gotchas / things not to repeat

- **msm_plugin / mrs_plugin suites**: run each as `mariadb-shell --py -f run_tests.py` from
  ITS OWN plugin dir, with `/opt/homebrew/bin` on PATH for `mariadbd`. `-s <path>` is no
  longer needed now that the runners default from `MARIADB_SHELL` or
  `shutil.which("mariadb-shell")`.

- **`mrs_plugin`'s suite leaks its sandbox on port 3388** whenever a run errors out, and the
  NEXT run then fails ~80 tests with `Port '3388' is already in use`. Two measurements were
  wasted on that echo. Kill the listener (`lsof -tnP -iTCP:3388 -sTCP:LISTEN`) and confirm
  the port is free BEFORE trusting any mrs_plugin pass/fail count.

- **`service.name` is a DB DEFAULT EXPRESSION**, not a trigger and not set by
  `add_service`: `regexp_replace(url_context_root, '[^0-9a-zA-Z ]', '')`. Reading the table
  DDL and the triggers does NOT reveal it — only
  `INFORMATION_SCHEMA.COLUMNS.COLUMN_DEFAULT` does. So a service at `/test` is named
  `test`, and `/service_to_delete` becomes `servicetodelete` (`_` is stripped too).
  `"name": "mrs"` was only ever right for the table's default `/mrs` context root, which
  `add_service` now REJECTS as reserved. It was stale in 12 places across
  `tests/unit/helpers.py`, `test_core.py`, `test_services.py`, `lib/test_services.py` and
  blocked 80 tests in fixture setup. When a whole suite errors in one fixture, fix the
  fixture first and re-measure — the failure count went UP (6 -> 25) because unblocked
  tests then failed on their own merits.

- **`REGEXP_LIKE` does not exist in MariaDB** (MySQL 8 only). `mysql_tasks`' SQL calls it
  (e.g. `mysql_tasks_3.0.2.sql:1809`), so 3 mrs_plugin tests die with
  `MySQL Error (1305): FUNCTION ... REGEXP_LIKE does not exist`. MariaDB form is the
  `REGEXP` operator / `REGEXP_REPLACE`. (The pattern `';[:space:]*$'` there also looks like
  a typo for `';[[:space:]]*$'`.) A real portability bug, not a test issue.

## Next steps

2. **Dump & Load is ported to the shell** (shell `5ca02fc70`, 2026-10-01), so
   `util.dump_schemas` / `load_dump` exist in builds from then on. NOTE: the
   `~/.local/bin/mariadb-shell` install (Sep 29) predates it and still lacks them; use
   `~/dev/shell/bld/bin/mariadb-shell` (`MARIADB_SHELL=...`) until it is reinstalled.
   - msm `deploy_schema(backup=True)` works: `test_deployment_backup_restores_failed_update`
     breaks an update and checks the dump is loaded back. `backup` still defaults to False.
   - `mrs_plugin/lib/general.py` passes `backup=True` for the MRS metadata deploy again
     (the `mysql_tasks` deploy is skipped on MariaDB). Not exercisable yet: every metadata
     version before 4.1.6 is MySQL DDL (`VISIBLE`/`INVISIBLE` indexes) that MariaDB
     rejects, so no upgrade can run until a 4.1.7 exists. `test_md_upgrade` (`--mdupgrade`)
     fails installing 4.1.5 for that reason; its MariaDB role handling is fixed.
   - The two `test_service_as_project` skips are removed and both pass. They needed the
     shell fix `4aaf294c6` (routines/events of mixed-case schemas were silently left out
     of dumps on lctn=2 MariaDB, i.e. macOS). The `lib/` variant's GitHub steps no longer
     download `migueltadeu/tests-mrs-project` (a former Oracle employee's repo holding a
     MySQL dump, which the shell refuses to load into MariaDB): `mock_github_archive`
     patches `urllib.request.urlopen` to serve the just-stored project as a GitHub branch
     archive and checks the resolved archive URLs.

3. **`mrs_plugin` suite (2026-10-02, bld shell with `4aaf294c6`): 247 pass, 0 fail, 2
   skipped** (`--mdupgrade` and a content-set test that needs a built project). msm: 14 pass.

9. (Optional) `gui/extension/package.json:1192` still labels the plugin "MySQL Schema
   Management" in the VS Code UI — outside msm_plugin, so left inconsistent by the rebrand.
