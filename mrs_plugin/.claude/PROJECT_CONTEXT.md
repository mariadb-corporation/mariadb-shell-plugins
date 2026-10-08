# mrs_plugin — Project Context

## Project

`mrs_plugin` is the MariaDB Shell plugin for the MariaDB REST Data Service (MRS), forked
from Oracle's MySQL REST Service plugin (dual copyright: Oracle's line kept,
`Copyright (c) 2026, MariaDB plc.` added). It registers the `mrs` global and the `MRS` SQL
handler (`CREATE/ALTER/DROP/SHOW/GRANT ... REST ...`, ANTLR grammar in `grammar/`, parser
in `lib/mrs_parser/`), manages the `mysql_rest_service_metadata` schema (an MSM project
under `db_schema/`), and generates the client SDKs (`sdk/`). Plugin version `26.9.5`,
metadata schema `5.0.0` (`lib/general.py` `VERSION`, `DB_VERSION`). Consumed by
`mcp_plugin` (REST SQL through its `db.*` tools) and bundled into the shell release
packages with msm_plugin and mcp_plugin.

## Architecture / key decisions

- **Two layers**, as in msm_plugin: top-level `*.py` are the `@plugin_function` wrappers
  (prompting, printing); `lib/*.py` does the work; `lib/MrsDdlListener.py` +
  `lib/MrsDdlExecutor.py` turn the REST SQL into `lib` calls; `script.py` is the
  `@sql_handler("MRS")`.
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

- **Branch `wip/mrs_schema_improvements`**, pushed, no PR. Commits `35c80f6a` (suites on
  their own sandbox), `58714069` (5.0.0), `01c4e06c` (shell option + context).
- **Tests, all green on the 2026-10-08 shell build with `--disable-modules=mrs`:**
  pytest 248 passed / 2 skipped (~50s); grammar test passed; `--mdupgrade` 4.1.6 -> 5.0.0
  passed; mcp_plugin 596 passed.
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
  - CI (`.github/workflows/shell-plugins-ci.yml`) does not pass `--disable-modules=mrs`;
    needed once the CI shell carries the built-in mrs module.

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

- `init.py` -> the `mrs` global and its sub-objects; `script.py` -> the `MRS` SQL handler
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

1. Open a PR for `wip/mrs_schema_improvements` (title without `[bypass-ci]`: source changes).
2. Add `--disable-modules=mrs` to the CI runs once the CI shell build has the built-in mrs
   module (`-M` for `run_tests.py`, `MARIADB_SHELL_OPTIONS` for the grammar script).
3. Decide how the built-in `mrs` module and this plugin coexist (same `MRS` handler name).
4. Further schema improvements on this branch; each one goes model -> sections -> msm
   release, then all three suites.

## Gotchas / things not to repeat

- **Run tests on a shell with the built-in mrs module** as
  `mariadb-shell --disable-modules=mrs --py -f run_tests.py -M="--disable-modules=mrs"`
  (add `--mdupgrade ./tests/unit/test_md_upgrade.py` for the upgrade test) and
  `MARIADB_SHELL_OPTIONS=--disable-modules=mrs scripts/run_grammar_test.sh`. Without it:
  `An SQL Handler named 'MRS' already exists` and nothing loads. The installed 26.9.2 release
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
- `run_tests.py -k "a or b"` is quoted with `shlex.quote` now; a pattern that misses the
  sandbox-deploying fixture still works because `init_mrs` is session-scoped.

## Git state

Checked at this checkpoint (2026-10-08):

```text
$ git -C mrs_plugin branch --show-current
wip/mrs_schema_improvements   (pushed, tracks origin; no PR yet)

$ git -C mrs_plugin status --short
(clean before this checkpoint's context file)
```

- Commits on top of `main` (`65930f54`): `35c80f6a`, `58714069`, `01c4e06c`.
