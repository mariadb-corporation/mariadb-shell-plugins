# mrs_plugin — Project Context

## Project

`mrs_plugin` is the MariaDB Shell plugin for the MariaDB REST Data Service (MRS), forked
from Oracle's MySQL REST Service plugin (dual copyright: Oracle's line kept,
`Copyright (c) 2026, MariaDB plc.` added). It adds its functions to the `mrs` global (the
shell's built-in C++ mrs module creates it; the plugin creates it itself only on a shell
without that module). The REST SQL statements (`CREATE/ALTER/DROP/SHOW/GRANT ... REST
...`) are handled by the built-in module's `MRS` SQL handler; the plugin no longer registers
one (2026-10-08). The ANTLR grammar in `grammar/` stays the reference grammar of REST SQL
(docs, railroad diagrams) and drives the generated parser in `lib/mrs_parser/`, which only
`mrs.run.script` and `lib.script.run_mrs_script` still use. The plugin also manages the
`mysql_rest_service_metadata` schema (an MSM project under `db_schema/`) and generates the
client SDKs (`sdk/`). Plugin version `26.9.5`,
metadata schema `5.0.0` (`lib/general.py` `VERSION`, `DB_VERSION`). Consumed by
`mcp_plugin` (REST SQL through its `db.*` tools) and bundled into the shell release
packages with msm_plugin and mcp_plugin.

## Architecture / key decisions

- **Two layers**, as in msm_plugin: top-level `*.py` are the `@plugin_function` wrappers
  (prompting, printing); `lib/*.py` does the work; `lib/MrsDdlListener.py` +
  `lib/MrsDdlExecutor.py` turn the REST SQL into `lib` calls (only for `mrs.run.script`
  now); `script.py` holds `mrs.run.script`.
- **REST SQL has two grammars, kept rule for rule in step**: ANTLR here
  (`grammar/MRS{Lexer,Parser}.g4`) and bison in the shell
  (`mariadb-shell/modules/mrs/core/mrs_parser.yy`, keywords in `mrs_lexer.h`). A statement
  change goes to both, then: regenerate `lib/mrs_parser/`, the docs section
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

## Current state

- **Branch `wip/mrs_schema_improvements`**, pushed, no PR. Commits `863f536e` (suites on
  their own sandbox), `da8fa01d` (5.0.0), `8e3d70c9` (shell option + context), `d1b5f1ee`
  (context); a rebase replaced the hashes earlier checkpoints named.
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
    `LOAD REST SERVICE FROM` cover it; the `mrs` object has only `help`).
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
  - Green: shell mrs filter 66 tests, plugin pytest 248/2 skipped, grammar test.
  - The tree also holds another session's uncommitted grammar-parity work (scripts,
    `generate_rrd_svg_files.py`, docs prose, SVGs); commit or untangle together.
- **Tests, all green on the 2026-10-08 shell build with the built-in mrs module** (the
  REST SQL in the tests goes to its handler): pytest 248 passed / 2 skipped (~45s); grammar
  test passed. Earlier, on a shell started with the since removed `--disable-modules=mrs`
  and with the plugin's own handler:
  `--mdupgrade` 4.1.6 -> 5.0.0 passed; mcp_plugin 596 passed.
- **SQL handler removed (2026-10-08, uncommitted):** `script.py` keeps `mrs.run.script`;
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

## Plugin reduction: coverage by REST SQL (decision pending)

Analysis of 2026-10-08; the user wants to discuss before anything is removed.

- **Covered by REST SQL (83):** `configure`, `status`, `ls`, `cd`, `set.currentService`; all
  `add/list/get/enable/disable/delete/update/set.*` of service, schema, dbObject,
  contentSet, authenticationApp(+Link), user, userRole(s), role, rolePrivilege; content file
  listing; every `get/dump.*CreateStatement`; `dump/load.serviceSqlScript` (DUMP/LOAD REST
  SERVICE, no ZIP); `run.script`; `list.users`, `get.authenticationVendors`,
  `list.authenticationAppServices` (new statements).
- **GUI helpers, now covered too (2026-10-08):** `get.objects`,
  `get.objectFieldsWithReferences` -> `SHOW CREATE REST VIEW|PROCEDURE|FUNCTION ...
  FORMAT=JSON`; `get.tableColumnsWithReferences`, `get.dbObjectParameters`,
  `get.dbFunctionReturnType` -> `SHOW REST COLUMNS`; `get.currentServiceMetadata` ->
  `metadata_version` + `SHOW REST SERVICES` `current`; `get.contentSetCount` -> count rows;
  `get.serviceRequestPathAvailability` -> client-side check against the SHOW lists.
  code_ext (re-implementing the MySQL Shell for VS Code GUI) reaches the shell only via
  mcp_plugin's `db.*` SQL tools, so REST SQL is its only API; recommended: it writes views
  by generating `CREATE OR REPLACE REST VIEW` text (no JSON input form).
- **Suggested to remove as well:** legacy JSON
  `dump/load.service/schema/object`, `get.ociDomainAppSecret`, `info`,
  `ignoreVersionUpgrade`, `get.runtimeManagementCode`.
- **Keep (12):** SDK (`get.sdkBaseClasses`, `get.sdkServiceClasses`, `get.sdkOptions`,
  `dump.sdkServiceFiles`), `dump/load.serviceProject`, MRS scripts
  (`get.fileMrsScriptDefinitions`, `get.folderMrsScriptDefinitions`,
  `get.folderMrsScriptLanguage`, `update.mrsScriptsFromContentSet`), `dump.auditLog`,
  `version`.
- **Undecided:** routers (`list.routerIds`, `list.routers`, `get.routerServices`,
  `delete.router`) -> REST SQL in the module once a MariaDB runtime exists;
  `get.availableMetadataVersions`, `get.configurationOptions` -> maybe
  `SHOW REST METADATA STATUS`.
- Removing `run.script` lets `grammar/`'s Python use, `lib/mrs_parser/`,
  `MrsDdlListener/Executor*.py`, `lib/script.py` go; first move
  `lib/services.run_sql_script` (used by `load.serviceProject`) to `session.run_sql` (the
  shell's MRS handler catches it) and drop `lib/content_files.py`'s `MrsDdlExecutor`
  import. The ANTLR grammar stays as the docs grammar.
- Nothing outside the plugin calls `mrs.*` by name (code_ext, mcp_plugin, msm_plugin
  checked); mcp_plugin only sends REST SQL.

## Metadata schema 4.1.6 -> 5.0.0: the exact changes

Branch `wip/mrs_schema_improvements`, commit `58714069` (2026-10-08). Diffed against `main`.

### Tables (`sections/140-10_tables.sql`, regenerated from the Workbench model)

- **63 columns in 29 tables** go from `BINARY(16)` to `UUID`, NULL-ability unchanged. The
  19 single-column primary keys (`*`) also get `DEFAULT UUID_v7()`; nothing else gets a
  default:
  - `url_host`: id*
  - `service`: id*, parent_id, url_host_id
  - `db_schema`: id*, service_id
  - `db_object`: id*, db_schema_id
  - `auth_vendor`: id*
  - `auth_app`: id*, auth_vendor_id, default_role_id
  - `mrs_user`: id*, auth_app_id
  - `redirect`: id*
  - `url_host_alias`: id*, url_host_id
  - `content_set`: id*, service_id
  - `content_file`: id*, content_set_id
  - `audit_log`: old_row_id, new_row_id
  - `mrs_role`: id*, derived_from_role_id, specific_to_service_id
  - `mrs_user_has_role`: user_id, role_id
  - `mrs_user_hierarchy_type`: id*, specific_to_service_id
  - `mrs_user_hierarchy`: user_id, reporting_to_user_id, user_hierarchy_type_id
  - `mrs_privilege`: id*, role_id
  - `mrs_user_group`: id*, specific_to_service_id
  - `mrs_user_group_has_role`: user_group_id, role_id
  - `mrs_user_has_group`: user_id, user_group_id
  - `mrs_group_hierarchy_type`: id*
  - `mrs_user_group_hierarchy`: user_group_id, parent_group_id, group_hierarchy_type_id
  - `mrs_db_object_row_group_security`: db_object_id, group_hierarchy_type_id
  - `router_session`: user_id, service_id
  - `object`: id*, db_object_id, row_ownership_field_id
  - `object_reference`: id*, reduce_to_value_of_field_id, row_ownership_field_id
  - `object_field`: id*, object_id, parent_reference_id, represents_reference_id
  - `service_has_auth_app`: service_id, auth_app_id
  - `content_set_has_obj_def`: content_set_id, db_object_id
- Unchanged: the integer keys of `router`, `router_status`, `router_session.id`,
  `router_general_log`, `audit_log.id`, `config`, `audit_log_status`.
- `db_object.fk_db_objects_db_schema1_idx` was `INVISIBLE` in the model and hand-stripped
  in the SQL; it is now visible in the model, so the export carries `VISIBLE` like every
  other index. The only other text change is the export date line.
- In the model only (no SQL effect): the `service.in_development` comment no longer uses
  MySQL's `->>`, matching the committed SQL.

### Data, views, routines, triggers

- `140-30_inserts.sql`: the 14 short binary literals (`0x30`..`0x35`, MariaDB rejects them
  as UUIDs) become full UUID literals: `0x31` -> `'31000000-0000-0000-0000-000000000000'`,
  etc. Same bytes, so upgraded rows keep their ids.
- `150-10_views.sql`, `object_fields_with_references`: `reduce_to_value_of_field_id` and
  `row_ownership_field_id` in the `object_reference` JSON are emitted as UUID strings
  instead of `TO_BASE64(...)` (4 places).
- `150-20_procedures_functions.sql`: `UUID_TO_BIN_SWAP` and `BIN_TO_UUID_SWAP` removed;
  `get_sequence_id()` returns `UUID` and is `RETURN UUID_v7()` (was `UUID_TO_BIN(UUID(), 1)`
  with the swap fallback); `sdk_service_data(IN service_id UUID)` and its four cursor
  variables (`schema_id`, `db_object_id`, `object_id`, `field_id`) are `UUID`.
- `150-30_triggers.sql`: the `router` audit triggers write the integer id as
  `CAST(LPAD(HEX(x.id), 32, '0') AS UUID)` (was `UNHEX(LPAD(CONV(x.id, 10, 16), 32, '0'))`,
  3 places); the two `mrs_user` checks compare `auth_vendor_id` with
  `'30000000-0000-0000-0000-000000000000'` (was `0x3000...`).
- `150-40_audit_log_triggers.sql` (regenerated by the WB plugin): the 236
  `CONCAT("0x", HEX(OLD/NEW.col))` values in the row JSON become the bare UUID columns, so
  the audit log holds `"id": "01a1..."` instead of `"id": "0x01A1..."`; the `config`
  triggers store their TINYINT key in the UUID `old_row_id`/`new_row_id` via the same
  `CAST(LPAD(HEX(...)) AS UUID)`.
- `140-20`, `140-40`, `150-50`, `170_roles.sql`: unchanged.
- Dev script: section 010's `SQL_MODE` gains `NO_AUTO_CREATE_USER`; section 910 is now
  `SELECT 5, 0, 1` (set by `prepare_release`).

### Update script `releases/updates/..._4.1.6_to_5.0.0.sql`

- Section 240 (runs inside `msm_update_4.1.6_to_5.0.0()`): 36 `ALTER TABLE ... DROP FOREIGN
  KEY`, then one `ALTER TABLE ... MODIFY COLUMN ...` per table for the 63 columns (same
  definitions as 140-10, defaults included), then the 36 FKs re-added as 140-10 defines
  them, except `fk_priv_role_priv_role1` with `ON DELETE CASCADE` as 140-20 redefines it.
  The 16 bytes of every id are kept; only the type changes.
- Section 290: `DROP FUNCTION IF EXISTS` for the two swap functions.
- Sections 230, 250, 270 untouched (template). Views, routines and triggers reach the
  target state through the deployment's full run of 150, as always.

### Releases

- Added: `versions/..._5.0.0.sql`, `updates/..._4.1.6_to_5.0.0.sql`,
  `deployment/..._deployment_5.0.0.sql`, all generated by the msm functions.
- Deleted: versions 4.0.0-4.1.5 (11), their deployment scripts (11) and all 11 earlier
  update scripts (4.0.0_to_4.0.1 .. 4.1.5_to_4.1.6). Kept: `versions/4.1.6`,
  `deployment/4.1.6`. (Git shows the 4.1.5 version and deployment files as renamed to
  5.0.0, being similar enough.)
- `CHANGELOG.md` has a 5.0.0 entry; `lib/general.py` `DB_VERSION = [5, 0, 0]`.

### Plugin code for the new id type

- `lib/core.py`: `id_to_binary` -> `id_to_uuid` (also takes UUID strings, returns the
  canonical string); `convert_ids_to_binary` / `try_convert_ids_to_binary` ->
  `..._to_uuid`, renamed in every caller; `convert_id_to_string` returns the UUID string
  (was `0x` + hex); `convert_id_to_base64_string` encodes the UUID's bytes; `NIL_UUID`;
  `ConfigFile` drops a stored `current_service_id` that is no id instead of failing.
- Constants as UUID strings: `lib/auth_apps.DEFAULT_ROLE_ID`, `lib/roles.FULL_ACCESS_ROLE_ID`,
  `lib/users.MRS_VENDOR_ID`; `lib/services` uses `NIL_UUID` for "no current service" and
  normalizes the stored current service id.
- `lib/users.get_user_roles`: `(_binary ?)` -> `?`.
- `interactive.py`: `_as_id()`; `resolve_service/schema/user/role/auth_app` test the value.
- Tests: expected ids in UUID form (`test_auth_vendors`), `update_services` with an int
  id now expects MariaDB 4078, a well-formed unknown UUID replaces `b"no_service"`.

## Files that matter

- `init.py` -> the `mrs` global and its sub-objects; `script.py` -> `mrs.run.script`
- `grammar/MRSLexer.g4`, `grammar/MRSParser.g4` -> REST SQL reference grammar;
  `scripts/generate_mrs_parser.sh` (npm `update-mrs-parser`) -> `lib/mrs_parser/`
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

1. Decide the plugin reduction with the user (section above), then remove the functions,
   their tests and the Python REST SQL stack.
2. Fix the `@DATATYPE` / `JSON SCHEMA` loss in `SHOW CREATE REST VIEW` (if the user agrees).
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
- **Regenerating `lib/mrs_parser/`:** `npm install` (antlr4ng-cli 1.0.2, ANTLR 4.13.1,
  matches the shell's runtime), but the script's `-lib ../../../gui/frontend/...` dir does
  not exist here: antlr4ng then fails yet still writes files. Run it without `-lib`, then
  format the generated `.py` files with black (the committed ones are black-formatted).
  A bare `MRSLexer`/`MRSParser` needs an `isSqlModeActive` attribute (see `lib/script.py`).
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

Checked at this checkpoint (2026-10-08, after the FORMAT=JSON work):

```text
$ git -C mrs_plugin branch --show-current
wip/mrs_schema_improvements   (pushed, up to date with origin; no PR yet)

$ git -C mrs_plugin status --short   (mrs_plugin part; summarized)
 M .claude/PROJECT_CONTEXT.md, script.py, services.py, package.json
 M grammar/MRSLexer.g4, grammar/MRSParser.g4, grammar/test/grammar_test.sql
 M lib/MrsDdlExecutor.py, lib/MrsDdlListener.py, lib/mrs_parser/* (regenerated)
 M scripts/generate_mrs_parser.sh, scripts/run_grammar_test.sh, scripts/update_grammar_docs.py
 D scripts/fix_rrd_svg_files.sh (staged)
?? scripts/generate_rrd_svg_files.py
 M docs/README.md, docs/sql.html, docs/sections/sql/{Alter,ConfigureAndCreate,Dump,Introduction,UseAndShow}.md
 M/D/?? docs/images/sql/*.svg (25 modified, 12 deleted, 6 new incl. formatClause,
        showRestColumnsStatement, showRestUsersStatement, showRestAuthVendorsStatement)
```

- Outside mrs_plugin the tree also has uncommitted changes of other sessions
  (`code_ext`, `mcp_plugin`, `msm_plugin` context files, `mcp_plugin/run_tests.py`,
  `mcp_plugin/tests/unit/helpers.py`).
- Commits on top of `main`: `863f536e`, `da8fa01d`, `8e3d70c9`, `d1b5f1ee`.
