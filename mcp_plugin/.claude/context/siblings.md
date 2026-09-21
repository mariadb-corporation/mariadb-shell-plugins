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

2. **`mysqlsh.globals.util.dump_schemas` / `load_dump` do NOT exist in this mariadb-shell
   build**, so `msm.deploy_schema` / `msm_plugin` `deploy_schema` with `backup=True` raise
   `AttributeError: unknown attribute: dump_schemas`. The backup feature is unusable (and
   untested) until the dump/restore is reimplemented — e.g. `mariadb-dump` as a subprocess.
   `backup=False` is the default precisely because of this. STILL REPRODUCING: it is one of
   the 20 remaining mrs_plugin failures (`lib/test_services.py::test_service_as_project`).

3. **`mrs_plugin`'s 20 remaining failures**, all pre-existing and independent of this
   session's work. Grouped: 3x `REGEXP_LIKE does not exist` (see Gotchas — a genuine
   MariaDB portability bug in `mysql_tasks`' SQL); 5x `test_downstream_converter` in
   `sdk/python/tests/test_mrs_base_classes.py` returning strings instead of
   `int`/`datetime`/`date`/`timedelta`; 2x `request_path is already used` (test isolation);
   1x `dump_schemas` (item 1); several `CREATE OR REPLACE REST ...` statement-text
   mismatches; a few object-count assertions (`assert 2 == 1`, `assert 2 == 4`).

4. **`mrs_plugin/lib/general.py:221` and `:231` call `deploy_schema`** and silently lost
   their rollback dump when `backup` defaulted to False. Add `backup=True` there if that
   behaviour should be preserved (sibling plugin, deliberately untouched).

9. (Optional) `gui/extension/package.json:1192` still labels the plugin "MySQL Schema
   Management" in the VS Code UI — outside msm_plugin, so left inconsistent by the rebrand.
