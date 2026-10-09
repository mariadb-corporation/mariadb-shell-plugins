# mrs_plugin — Project Context

## Project

`mrs_plugin` is the MariaDB Shell plugin for the MariaDB REST Data Service (MRS), forked
from Oracle's MySQL REST Service plugin (dual copyright: Oracle's line kept,
`Copyright (c) 2026, MariaDB plc.` added). It adds its functions to the `mrs` global (the
shell's built-in C++ mrs module creates it; the plugin creates it itself only on a shell
without that module). The REST SQL statements (`CREATE/ALTER/DROP/SHOW/GRANT ... REST
...`) are handled by the built-in module's `MRS` SQL handler; the plugin no longer registers
one (2026-10-08). Since 2026-10-09 the plugin keeps 15 functions (SDK generation,
project and service dump/load, `load.contentSet`, MRS script analysis, runtime management
code, audit log, version, see
[context/plugin-reduction.md](context/plugin-reduction.md)); the Python REST SQL parser is
gone. The docs, the ANTLR reference grammar and their tools moved to the mariadb-shell repo
(`docs-ref/content/mariadb-rest-service/`, `modules/mrs/antlr_grammar/`). The plugin also manages the
metadata schema (`mariadb_rest_service`, an MSM project maintained in mariadb-shell
`modules/mrs/db_schema/` since 2026-10-09) and generates the
client SDKs (`sdk/`). Plugin version `26.9.5`,
metadata schema `5.0.0` (`lib/general.py` `VERSION`, `DB_VERSION`). Consumed by
`mcp_plugin` (REST SQL through its `db.*` tools) and bundled into the shell release
packages with msm_plugin and mcp_plugin.

## Architecture / key decisions

- **Two layers**, as in msm_plugin: top-level `*.py` are the `@plugin_function` wrappers
  (`general`, `services`, `content_sets`, `dump`); `lib/*.py` does the work and holds only
  code those 13 functions reach (cleanup 2026-10-09).
- **REST SQL has two grammars, kept rule for rule in step**, both in the mariadb-shell repo
  since 2026-10-09: ANTLR (`modules/mrs/antlr_grammar/MRS{Lexer,Parser}.g4`, docs only) and
  bison (`modules/mrs/core/mrs_parser.yy`, keywords in `mrs_lexer.h`). A statement change
  goes to both, then the GitBook reference page, `npm run docs-ref:mrs-grammar-docs` and
  `docs-ref:mrs-diagrams`, and the shell's `unittest/data/mrs/grammar_test.sql`.
- **No client files in REST SQL** (2026-10-09): the plugin does all file work
  (`mrs.dump.service`, `mrs.load.service`, `mrs.load.contentSet`, project functions) and
  sends REST SQL through `session.run_sql`.
- **Projects are not REST SQL** (2026-10-08): `DUMP/LOAD REST PROJECT` were dropped from
  both grammars, the Python listener/executor and the docs; `mrs.dump.serviceProject()` /
  `mrs.load.serviceProject()` do the same (same `lib.services` calls) and are the only way.
- **Metadata schema name is resolved, never hardcoded** (2026-10-09): the schema is
  `mariadb_rest_service`, optionally with a customer prefix/postfix
  (`acme_mariadb_rest_service_eu`, MSM substitutions `schema_prefix`/`schema_postfix`). SQL in
  `lib/` and the tests uses the token `<metadata>` (`core.METADATA`); `MrsDbExec` and
  `core.metadata_sql(session, sql)` replace it with the schema from the last column
  `metadata_schema` of `SHOW REST METADATA STATUS` (cached per session object;
  `core.forget_metadata_schema(session)` after `CONFIGURE REST METADATA SCHEMA` / `USE REST
  METADATA SCHEMA`). Roles: `core.metadata_role(session, "data_provider")` = prefix +
  `mariadb_rest_service_<role>` + postfix. The schema project (Workbench model, sections,
  releases, `prepare_default_static_content.sh`) is the shell repo's
  `modules/mrs/db_schema/mariadb_rest_service.msm.project`; this repo's copy was removed.
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
- **MariaDB-only.** 5.0.0 is the first release of `mariadb_rest_service` (4.1.6 and the
  upgrade path were dropped with the rename; there are no installations). Queries must be valid under
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
- **2026-10-09, later rounds (committed with this checkpoint):**
  - `as_path` rewrites only the dump's CREATE and USE statements; `core.quote_service_path`
    for developer paths (`a24530a9`).
  - Metadata schema renamed to `mariadb_rest_service` (prefix/postfix per customer, 5.0.0 is
    its first release): `<metadata>` token + `core.metadata_schema/metadata_role`, plugin
    `db_schema/` and the 4.1.6 upgrade test removed, `tests/unit/test_metadata_schema.py`.
    msm_plugin got the substitutions feature (written by mariadb-shell-7e, committed here).
  - Async-task support removed: generator/templates emit only direct `call()`, Python and
    TypeScript base classes without task classes (SDK tests 136 -> 121 and 246 -> 202/122,
    all removed tests covered tasks), routines are CREATE only.
  - MySQL names removed: auth vendor `MariaDB Internal`, app `MariaDB`, `VENDOR MARIADB`
    (same id `31000000-...`, the SDKs detect the vendor by id); SDK prose says MariaDB REST
    Daemon/Service; mrs_notes example uses the `MariaDB` app (`btnMariaDB`).
  - Tests: mrs_plugin 164 passed (the run also collects the Python SDK tests), mcp_plugin
    596 passed / 3 skipped, Python SDK 121, TypeScript SDK 202 + 122.
- **2026-10-09, committed and pushed** (`3566d1c1`, `dee429e7`; shell `0504c64ae`,
  `ced93ef43`): file-based REST SQL dropped, `LOAD SCRIPTS` registers MRS scripts in the
  shell, new `dump.service` / `load.service` / `load.contentSet`, `update.mrsScriptsFromContentSet`
  removed; docs + grammar + doc tools moved to the shell repo as GitBook pages, MySQL names
  replaced by MariaDB (see [context/plugin-reduction.md](context/plugin-reduction.md)).
  Plugin pytest: 177 passed, 1 skipped.
- **Session 2026-10-08 (committed since):**
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
  setup): `tests/conftest.py` `init_mrs` (the grammar test now runs in the shell's
  unit tests, `mrs_grammar_test_norecord.py`). Only `mariadbd`
  on PATH and a shell are needed.
- **Fixed this session:** `CLONE REST SERVICE dev@/path` (looked up by path alone; cloned
  an empty developer list the CHECK rejects); the developer-list service lookup's HAVING
  (MariaDB 1463 under `ONLY_FULL_GROUP_BY`); MySQL-only named column CHECKs in the grammar
  setup SQL; the upgrade test's `shell.connect` closing the suite's session.
- **Known issues / open:**
  - The MRS runtime (MySQL Router's MRS) expects `BINARY(16)` ids; a 5.0.0 schema implies a
    MariaDB-side runtime. Not addressed here.
  - No skips left (179 passed on 2026-10-09 against the renamed schema).
  - CI (`.github/workflows/shell-plugins-ci.yml`) needs a shell build with the built-in mrs
    module now: without it no `MRS` handler exists and the
    REST SQL in the tests and the grammar test fails.
  - `SHOW CREATE REST VIEW` drops `@DATATYPE` and `JSON SCHEMA` of fields below a reference
    (module, pre-existing): re-creating from the text loses them. Blocks code_ext saving
    views as generated REST SQL; offered to the user, not fixed.

## Files that matter

- `init.py` -> the `mrs` sub-objects `get`, `dump`, `load`
- `services.py` / `lib/services.py` -> SDK, project and service dump/load
  (`service_script`, `dump_service_script`, `load_service_script`, `endpoint_selection`)
- `content_sets.py` / `lib/content_sets.py` -> MRS script analysis, `load_content_set`,
  `content_file_statement` (also used by the test fixture)
- Docs and grammar: mariadb-shell `docs-ref/content/mariadb-rest-service/`,
  `modules/mrs/antlr_grammar/` (not in this repo any more)
- `lib/core.py` -> `ConfigFile` (current service per connection), `MrsDbExec`, id helpers
  (`id_to_uuid`, `convert_ids_to_uuid`, `try_convert_ids_to_uuid`, `NIL_UUID`)
- `interactive.py` -> resolvers for service/schema/user/role/auth-app queries
- `lib/general.py` -> `VERSION`, `DB_VERSION`, `configure()` (deploys the metadata via msm)
- `run_tests.py`, `tests/conftest.py`, `tests/unit/helpers.py` -> test harness and sandbox
- `tests/unit/test_metadata_schema.py` -> prefixed/postfixed schema and role derivation

## Next steps

1. Docs follow-ups (in the shell repo's context): new VS Code screenshots, verify the
   open points of the conversion (daemon role activation, GTID read-your-writes, custom
   auth procedure id type). Async tasks were dropped on 2026-10-09.
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
3. (Done 2026-10-09 in 5.0.0: auth vendor `MariaDB Internal`, app `MariaDB`, keyword
   `VENDOR MARIADB`; same ids. What the REST Daemon must change for 5.0.0 is in the shell
   repo's `.claude/context/rest-daemon-5.0.md`.)
4. Open a PR for `wip/mrs_schema_improvements` (title without `[bypass-ci]`: source changes).
5. Run CI on a shell build with the built-in mrs module (the plugin has no SQL handler
   anymore).
6. **mrs_notes example: HTTPS certificate folder.** `examples/mrs_notes/vite.config.ts`
   serves the dev server over HTTPS with the certificate of the old MySQL Shell for VS Code
   extension: `<user config>/plugin_data/gui_plugin/web_certs/server.{key,crt}`, where the
   user config is `~/.mysqlsh-gui` (Windows: `AppData/Roaming/MySQL/mysqlsh-gui`). Without
   those files it falls back to plain HTTP. The MariaDB Shell for VS Code extension
   (code_ext) does not create that folder, so the path has to follow whatever that
   extension uses for its certificates (or the example creates its own); decision pending
   from the extension's owner. Left unchanged in the MySQL-name cleanup of 2026-10-09,
   together with the "generated by MySQL Workbench" header of
   `examples/mrs_notes/db_schema/mrs_notes.sql`.
7. Further schema improvements; each one goes model -> sections -> msm release, then all
   three suites.

## Gotchas / things not to repeat

- **Run tests on a shell with the built-in mrs module** (the `--disable-modules` option
  is gone again):
  `python3 run_tests.py` (finds `../mariadb-shell/build/bin/mariadb-shell`). The REST SQL
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
- **REST SQL text from Python:** quote request paths with `core.quote_ident` (dots need
  backticks); send file content as `CONTENT '...'` only for UTF-8 without NUL/backslash
  (quotes doubled), else base64 `BINARY CONTENT` (independent of `NO_BACKSLASH_ESCAPES`).
  `DROP REST CONTENT SET` takes `FROM SERVICE`, not `ON SERVICE`.
- **pyflakes/black are not installed** globally or in the shell's Python; use a scratch venv.
- **Service paths in REST SQL from Python:** use `core.quote_service_path()` (quotes the
  request path, keeps a developer list `mike@/path` unquoted); `quote_ident` on a developer
  path breaks it. `load_service_script(as_path=...)` relies on the dump naming the service
  only in its CREATE and USE statements (since 2026-10-09).
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

Checked at this checkpoint (2026-10-09), before its commit:

```text
$ git branch --show-current
wip/mrs_schema_improvements   (tracks origin; no PR yet)

$ git status --short   (this session's part, committed with this checkpoint)
 M mrs_plugin/{.gitignore,package.json,run_tests.py,lib/*,sdk/**,tests/**,examples/mrs_notes/**}
 D mrs_plugin/db_schema/**, scripts/{default_heatwave_endpoints,prepare_default_static_content.sh,run_md_upgrade_test.sh}, tests/unit/test_md_upgrade.py
?? mrs_plugin/tests/unit/test_metadata_schema.py
 M mcp_plugin/lib/db_functions.py, mcp_plugin/tests/unit/test_rest_sql.py
 M msm_plugin/{lib/core.py,lib/management.py,management.py,templates/scripts/*,tests/unit/test_management.py}
```

- Last pushed commit before this checkpoint: `a24530a9`.
- Not committed with it (other sessions' uncommitted work): `code_ext/.claude/PROJECT_CONTEXT.md`,
  `mcp_plugin/.claude/**`, `mcp_plugin/run_tests.py`, `mcp_plugin/tests/unit/helpers.py`,
  `msm_plugin/.claude/PROJECT_CONTEXT.md`.
