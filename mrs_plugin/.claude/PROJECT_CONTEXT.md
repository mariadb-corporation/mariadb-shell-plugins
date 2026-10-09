# mrs_plugin — Project Context

## Project

`mrs_plugin` is the MariaDB Shell plugin for the MariaDB REST Data Service (MRS), forked
from Oracle's MySQL REST Service plugin (dual copyright: Oracle's line kept,
`Copyright (c) 2026, MariaDB plc.` added). It adds its functions to the `mrs` global (the
shell's built-in C++ mrs module creates it; the plugin creates it itself only on a shell
without that module). The REST SQL statements (`CREATE/ALTER/DROP/SHOW/GRANT ... REST
...`) are handled by the built-in module's `MRS` SQL handler; the plugin no longer registers
one (2026-10-08). Since 2026-10-09 the plugin keeps only 13 functions (SDK generation,
project dump/load, MRS script analysis, runtime management code, audit log, version, see
[context/plugin-reduction.md](context/plugin-reduction.md)); the Python REST SQL parser is
gone. The ANTLR grammar in `grammar/` stays the reference grammar of REST SQL (docs,
railroad diagrams). The plugin also manages the
`mysql_rest_service_metadata` schema (an MSM project under `db_schema/`) and generates the
client SDKs (`sdk/`). Plugin version `26.9.5`,
metadata schema `5.0.0` (`lib/general.py` `VERSION`, `DB_VERSION`). Consumed by
`mcp_plugin` (REST SQL through its `db.*` tools) and bundled into the shell release
packages with msm_plugin and mcp_plugin.

## Architecture / key decisions

- **Two layers**, as in msm_plugin: top-level `*.py` are the `@plugin_function` wrappers
  (`general`, `services`, `content_sets`, `dump`); `lib/*.py` does the work and holds only
  code those 13 functions reach (cleanup 2026-10-09).
- **REST SQL has two grammars, kept rule for rule in step**: ANTLR here
  (`grammar/MRS{Lexer,Parser}.g4`) and bison in the shell
  (`mariadb-shell/modules/mrs/core/mrs_parser.yy`, keywords in `mrs_lexer.h`). A statement
  change goes to both, then: the docs section
  (`docs/sections/sql/*.md`), the railroad SVGs, both `grammar_test.sql` copies.
- **Projects are not REST SQL** (2026-10-08): `DUMP/LOAD REST PROJECT` were dropped from
  both grammars, the Python listener/executor and the docs; `mrs.dump.serviceProject()` /
  `mrs.load.serviceProject()` do the same (same `lib.services` calls) and are the only way.
- **Metadata schema = MSM project** `db_schema/mysql_rest_service_metadata.msm.project`.
  Tables are designed in MySQL Workbench (`development/wb/*.mwb`, a zip of
  `document.mwb.xml` + `@db/data.db` + `@scripts` + `lock`) and forward-engineered into
  `development/sections/140-10_tables.sql`; the dev script pulls the sections in with
  `SOURCE '...'[700:-115]` / `[89:]` (character slices: the export's header and footer, the
  copyright). Releases, update and deployment scripts are generated with the msm plugin
  functions, never by hand (process in `db_schema/README.md`).
- **Ids are UUIDs (5.0.0, 2026-10-08).** Every id/FK column is MariaDB `UUID`,
  single-column PKs `DEFAULT UUID_v7()` (time-ordered, index-friendly), `get_sequence_id()`
  returns `UUID_v7()`. In Python an id is the canonical lower-case hyphenated string;
  `lib.core.id_to_uuid` accepts that, the legacy `0x`+32-hex form, base64 of the 16 bytes
  and raw bytes (so old configs and SDK data still resolve). `convert_id_to_string` returns
  the UUID string; `convert_id_to_base64_string` keeps the SDK config's base64 format.
  `interactive.py` resolvers tell an id from a `host/path` query by VALUE (`_as_id`), no
  longer by `bytes` type. Well-known ids are `'3X000000-0000-0000-0000-000000000000'`
  (`0x30` MRS vendor, `0x31` MySQL vendor / app / Full Access role / any-host).
- **Workbench cannot type UUID**: its table editor parses types with its built-in MySQL
  grammar. The model carries a `db.UserDatatype` `UUID` (sqlDefinition `UUID`, actual type
  BINARY); forward engineering emits the sqlDefinition. Assigned by the `UUID_Columns`
  plugin in `development/wb/Audit_Log_Triggers_grt.py` (switches every `BINARY(16)` column,
  adds the `UUID_v7()` default to single-column PKs, idempotent).
- **MariaDB-only.** Releases before 4.1.6 (MySQL DDL: `VISIBLE`/`INVISIBLE` indexes etc.)
  were deleted; 4.1.6 is the only version upgradable to 5.0.0. Queries must be valid under
  `ONLY_FULL_GROUP_BY` the MariaDB way (no functional dependency, no plain columns in
  HAVING) because MSM scripts set that mode; the test conftest sets it too.

## Context files

| File | What is in it |
| --- | --- |
| [`context/plugin-reduction.md`](context/plugin-reduction.md) | Which `mrs.*` plugin functions REST SQL in the shell's mrs module covers, which stay (and why), and what the removal of the other 105 (done 2026-10-09) changed |
| [`context/metadata-schema-5.0.0.md`](context/metadata-schema-5.0.0.md) | The exact changes of metadata schema 4.1.6 -> 5.0.0 (UUID ids): tables, data, views, routines, triggers, the update script, releases, plugin code |

## Current state

- **Branch `wip/mrs_schema_improvements`**, pushed, no PR. Commits `863f536e` (suites on
  their own sandbox), `da8fa01d` (5.0.0), `8e3d70c9` (shell option + context), `d1b5f1ee`
  (context), `71651e6f` (REST SQL moves to the shell's mrs module), then the daemon
  statements and this checkpoint; a rebase replaced the hashes earlier checkpoints named.
- **Session 2026-10-08 (uncommitted, both repos):**
  - New REST SQL `SHOW REST USERS [(ON|FROM) [SERVICE] path] [FOR AUTH APP name]`,
    `SHOW REST AUTH VENDORS`, `SHOW REST SERVICES [FOR AUTH APP name]` (replace
    `mrs.list.users`, `mrs.get.authenticationVendors`, `mrs.list.authenticationAppServices`).
    USERS: no filter = current service, else all users; `FOR AUTH APP` alone ignores the
    current service. New keyword `VENDORS`, allowed unquoted as a name (`identifierKeyword`,
    like `FILES`). In ANTLR, bison, the module (executor + metadata), docs, tests.
  - `DUMP/LOAD REST PROJECT` removed everywhere (also their keywords `PROJECT`, `VERSION`,
    `ICON`, `PUBLISHER`, `DESCRIPTION`); `mrs.dump.serviceProject()` gained the statement's
    `~` expansion + path validation for destination, icon and schema files.
  - The shell module's `mrs.runScript()` removed (`\source`, `--sql -f`,
    `SHOW CREATE REST SERVICE ... INCLUDING ... ENDPOINTS` + running the script cover
    it; the `mrs` object has only `help`).
  - `lib/mrs_parser/` regenerated (it also lagged the earlier `FILES` parity change), docs
    updated (`UseAndShow.md`, `Dump.md` "REST Projects", `Introduction.md`), RRD SVGs
    regenerated with pruning, `docs/sql.html` rebuilt (other HTML pages left as committed).
  - For code_ext's GUI (replaces the 8 GUI helpers): `FORMAT=JSON` (also `= json`,
    `='json'`, `TRADITIONAL` = default; else `Unknown REST format name: 'x'`) closing every
    `SHOW CREATE REST ...` statement -> one cell, pretty JSON of the object (metadata column
    names as keys, UUID ids, options embedded; views/routines with `objects` -> `fields` ->
    `object_reference`; service + `INCLUDING DATABASE ENDPOINTS` with `schemas` ->
    `db_objects`; never secrets, password hashes or file bytes). New
    `SHOW REST COLUMNS FROM [TABLE|VIEW|PROCEDURE|FUNCTION] [schema.]name [FORMAT=JSON]`
    (columns + references in both directions, or parameters + return type; type detected,
    schema defaults to the current REST schema's, then `DATABASE()`). `SHOW REST METADATA
    STATUS` gained `metadata_version` (max `audit_log.id`, for polling). New keywords
    `COLUMNS` (usable as a name) and `TABLE` (only as a data-mapping key: after `FROM` it
    would be ambiguous). FORMAT at the END was the user's choice (the server puts it first).
  - 2026-10-09: `SHOW REST [METADATA] STATUS FORMAT=JSON` (with
    `available_metadata_versions`, `configuration_options`; available/required versions
    now also before the schema exists); project dump/load through REST SQL (see
    [context/plugin-reduction.md](context/plugin-reduction.md)).
  - Green: shell mrs filter 69 tests, plugin pytest 248/2 skipped, grammar test.
  - The tree also holds another session's uncommitted grammar-parity work (scripts,
    `generate_rrd_svg_files.py`, docs prose, SVGs); commit or untangle together.
- **Tests, all green on the 2026-10-08 shell build with the built-in mrs module** (the
  REST SQL in the tests goes to its handler): pytest 248 passed / 2 skipped (~45s); grammar
  test passed. Earlier, on a shell started with the since removed `--disable-modules=mrs`
  and with the plugin's own handler:
  `--mdupgrade` 4.1.6 -> 5.0.0 passed; mcp_plugin 596 passed.
- **SQL handler removed (2026-10-08):** `script.py` kept `mrs.run.script` (removed with it on 2026-10-09);
  the `@sql_handler("MRS")`, `MRS_PREFIXES` and `get_shell_result` are gone, so the plugin
  loads next to the shell's built-in module without "An SQL Handler named 'MRS' already
  exists".
- **Both suites deploy their own sandbox** on a free port and remove it (also on a failed
  setup): `tests/conftest.py` `init_mrs`; `scripts/run_grammar_test.sh`. Only `mariadbd`
  on PATH and a shell are needed.
- **Fixed this session:** `CLONE REST SERVICE dev@/path` (looked up by path alone; cloned
  an empty developer list the CHECK rejects); the developer-list service lookup's HAVING
  (MariaDB 1463 under `ONLY_FULL_GROUP_BY`); MySQL-only named column CHECKs in the grammar
  setup SQL; the upgrade test's `shell.connect` closing the suite's session.
- **Known issues / open:**
  - The MRS runtime (MySQL Router's MRS) expects `BINARY(16)` ids; a 5.0.0 schema implies a
    MariaDB-side runtime. Not addressed here.
  - The two skips: `test_md_upgrade` (opt-in `--mdupgrade`) and a content-set test that
    needs a built project.
  - CI (`.github/workflows/shell-plugins-ci.yml`) needs a shell build with the built-in mrs
    module now: without it no `MRS` handler exists and the
    REST SQL in the tests and the grammar test fails.
  - The `db_schema/` MSM project is now maintained in the mariadb-shell repo
    (`modules/mrs/db_schema/`); this copy is to be removed and the plugin pointed there.
  - `SHOW CREATE REST VIEW` drops `@DATATYPE` and `JSON SCHEMA` of fields below a reference
    (module, pre-existing): re-creating from the text loses them. Blocks code_ext saving
    views as generated REST SQL; offered to the user, not fixed.

## Files that matter

- `init.py` -> the `mrs` sub-objects `get`, `dump`, `load`, `update`
- `grammar/MRSLexer.g4`, `grammar/MRSParser.g4` -> REST SQL reference grammar (docs only)
- `docs/sections/sql/*.md` -> REST SQL reference (one ```antlr block + `::=` SVG per rule);
  `scripts/update_grammar_docs.py` -> syncs the rule blocks with the grammar;
  `scripts/generate_rrd_svg_files.py` (npm `update-rrd-svg-files`) -> `docs/images/sql/*.svg`;
  `scripts/generate_html_docs.sh` (npm `update-html-docs`) -> `docs/*.html`
- `lib/core.py` -> `ConfigFile` (current service per connection), `MrsDbExec`, id helpers
  (`id_to_uuid`, `convert_ids_to_uuid`, `try_convert_ids_to_uuid`, `NIL_UUID`)
- `lib/services.py`, `lib/MrsDdlExecutor.py`, `lib/MrsDdlListener.py` -> REST SQL to metadata
- `interactive.py` -> resolvers for service/schema/user/role/auth-app queries
- `lib/general.py` -> `VERSION`, `DB_VERSION`, `configure()` (deploys the metadata via msm)
- `db_schema/README.md` -> the schema change process (Workbench, plugins, msm release)
- `db_schema/.../development/mysql_rest_service_metadata_next.sql` + `sections/*.sql`
- `db_schema/.../development/wb/Audit_Log_Triggers_grt.py` -> WB plugins `Audit_Log_Triggers`
  and `UUID_Columns`
- `db_schema/.../releases/{versions,updates,deployment}/` -> 4.1.6 and 5.0.0 only
- `run_tests.py`, `tests/conftest.py`, `tests/unit/helpers.py` -> test harness and sandbox
- `scripts/run_grammar_test.sh`, `grammar/test/{grammar_test,grammar_test_setup,sakila-schema}.sql`
- `scripts/run_md_upgrade_test.sh`, `tests/unit/test_md_upgrade.py` -> upgrade = fresh install check

## Next steps

1. Decide the plugin reduction with the user ([context/plugin-reduction.md](context/plugin-reduction.md)), then remove the functions,
   their tests and the Python REST SQL stack.
2. Fix the `@DATATYPE` / `JSON SCHEMA` loss in `SHOW CREATE REST VIEW` (if the user agrees).
- **Later (user's note):** a metadata schema version that renames the `router*` tables
  (`router`, `router_status`, `router_session`, `router_general_log`, the `router_services`
  view) to `daemon*`, with the daemon fork, the module's queries and
  `required_router_version` following. In the same version the ids of these tables
  (`router.id`, `router_status.id`, `router_session.id`, `router_general_log.id` and their
  `router_id` / `router_session_id` references, INT UNSIGNED AUTO_INCREMENT today) become
  `UUID` like every other id (`DEFAULT UUID_v7()`), so `DROP REST DAEMON` and
  `SHOW REST SERVICES FOR DAEMON` will take a UUID instead of an integer (grammar:
  `daemonId`, bison `daemon_id`), and the audit triggers' `CAST(LPAD(HEX(id)...) AS UUID)`
  for router ids go away.
3. Commit this session's work (plugin repo, and `wip/mrs_module` in mariadb-shell),
   separating it from the other session's parity changes as the user wants.
4. Open a PR for `wip/mrs_schema_improvements` (title without `[bypass-ci]`: source changes).
5. Run CI on a shell build with the built-in mrs module (the plugin has no SQL handler
   anymore).
6. Remove `db_schema/` here and point the plugin at the shell's copy.
7. Further schema improvements; each one goes model -> sections -> msm release, then all
   three suites.

## Gotchas / things not to repeat

- **Run tests on a shell with the built-in mrs module** (the `--disable-modules` option
  is gone again):
  `MARIADB_SHELL=<build>/bin/mariadb-shell <build>/bin/mariadb-shell --py -f run_tests.py`
  and `MARIADB_SHELL=<build>/bin/mariadb-shell scripts/run_grammar_test.sh`. The REST SQL
  goes to the built-in handler. The installed 26.9.2 release
  shell is no fallback: it bundles its own older mrs_plugin, which shadows the repo's
  (`ImportPathMismatchError` on the conftest).
- **Run only full release scripts** (`releases/versions/`, or deploy via msm). Section files
  and Workbench exports are fragments.
- **Workbench headless**: `MySQLWorkbench --run-script x.py --quit-when-done` runs WITHOUT a
  model loaded (`--model` is ignored with it); load `document.mwb.xml` with
  `grt.unserialize`, save with `grt.serialize`, then RESTORE the root attributes
  `document_type="MySQL Workbench Model" version="1.4.4"` or WB says "does not contain a
  Workbench document". `import` of a plugin file gets WB's installed copy; load the repo's by
  path (`importlib.util.spec_from_file_location`). The table export that matches the File >
  Export layout: `DbMySQL.generateSQLForDifferences(empty_catalog, catalog, options)` per
  table, in the committed file's table order (`makeSQLExportScript` with an empty dict emits
  no tables).
- **Update script traps (MariaDB):** an FK column cannot be altered in place (1833): drop
  the FKs, MODIFY, re-add, all in section 240. `DROP FUNCTION` inside section 240 fails
  (1357, it runs in a stored procedure): put it in 290. Re-adding an FK must match the
  sections' final definition (`fk_priv_role_priv_role1` is `ON DELETE CASCADE` via 140-20).
- **`shell.connect()` closes the previous global session**; switch temporarily with
  `shell.open_session()` + `shell.set_session()`.
- **A `(_binary ?)` introducer** turns a bound UUID string into bytes and matches nothing.
- **Integers do not cast to UUID** implicitly (4078); use
  `CAST(LPAD(HEX(n), 32, '0') AS UUID)`. Short binary literals (`0x31`) are rejected too;
  write the full UUID.
- **`service.name` is a DB DEFAULT EXPRESSION** (`REGEXP_REPLACE(url_context_root, ...)`),
  so `/test` is named `test`; `/mrs` is reserved.
- `scripts/update_grammar_docs.py` must run with `python3` (under `mariadb-shell -f`,
  `sys.argv[0]` is empty and its `chdir` fails). It rewrites rule blocks of every section
  that differs from the grammar, not just the ones you touched.
- **HTML docs:** the committed `docs/*.html` were prettified and year-bumped by hand after
  generation, so `update-html-docs` churns every page and the templates put back
  `2022, 2025, Oracle`. Rebuild only the page whose content changed and fix line 3.
  The build needs `pandoc-include` (`pip install pandoc-include`, a venv works).
- Keywords used as names need quotes (`FOR AUTH APP app` fails: `APP` is a keyword); only
  `FILES` and `VENDORS` are allowed unquoted.
- **A stored data mapping holds every column**: the ones left out of the mapping are
  disabled fields (`enabled` false), visible in `FORMAT=JSON`; filter on `enabled`.
- The sandbox on port 3360 (from a mariadb-shell session, sakila loaded) still has 4.1.6
  metadata, so 5.x statements refuse it; do not upgrade it, verify with the scripted tests
  (own sandboxes) or `run_tests.py`.
- `run_tests.py -k "a or b"` is quoted with `shlex.quote` now; a pattern that misses the
  sandbox-deploying fixture still works because `init_mrs` is session-scoped.

## Git state

Checked at this checkpoint (2026-10-08), before its commit:

```text
$ git -C mrs_plugin branch --show-current
wip/mrs_schema_improvements   (tracks origin; no PR yet)

$ git -C mrs_plugin status --short   (mrs_plugin part; committed with this checkpoint)
 M .claude/PROJECT_CONTEXT.md
?? .claude/context/{plugin-reduction,metadata-schema-5.0.0}.md (split out of it)
 M docs/sections/sql/{Drop,UseAndShow}.md, docs/sql.html
 M grammar/MRSLexer.g4, grammar/MRSParser.g4, grammar/test/grammar_test.sql
 M lib/mrs_parser/* (regenerated)
 M/?? docs/images/sql/*.svg (3 modified; new dropRestDaemonStatement, showRestDaemonsStatement, daemonId)
```

- Last pushed commit: `71651e6f`. Commits on top of `main`: `863f536e`, `da8fa01d`,
  `8e3d70c9`, `d1b5f1ee`, `71651e6f`, plus this checkpoint's commit.
- Outside mrs_plugin the tree has uncommitted changes of other sessions that are NOT
  committed with it (`code_ext`, `mcp_plugin`, `msm_plugin` context files,
  `mcp_plugin/run_tests.py`, `mcp_plugin/tests/unit/helpers.py`).
