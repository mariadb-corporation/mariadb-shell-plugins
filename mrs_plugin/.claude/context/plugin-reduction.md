# Plugin reduction: coverage by REST SQL (decision pending)

Back to the index: [../PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

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
- **Keep (13, decided by the user):** SDK (`get.sdkBaseClasses`, `get.sdkServiceClasses`,
  `get.sdkOptions`, `dump.sdkServiceFiles`), `dump/load.serviceProject`, MRS scripts
  (`get.fileMrsScriptDefinitions`, `get.folderMrsScriptDefinitions`,
  `get.folderMrsScriptLanguage`, `update.mrsScriptsFromContentSet`: TypeScript endpoints
  found by `@Mrs.module/schema/script/trigger` decorators; needed by the VS Code
  extension; the module only stores the LOAD SCRIPTS flag), `get.runtimeManagementCode`
  (TypeScript for the extension's DB Notebook), `dump.auditLog`, `version`.
- **Routers, now covered (2026-10-08):** MySQL Router is forked as the **MariaDB REST
  Daemon**; the user wants DAEMON(S), never ROUTER(S), in REST SQL. `SHOW REST DAEMONS
  [FORMAT=JSON]` (`list.routers`, `list.routerIds`), `SHOW REST SERVICES FOR DAEMON <id>`
  (`get.routerServices`, via the `router_services` view), `DROP REST DAEMON [IF EXISTS]
  <id>` (`delete.router`; also deletes `router_status` / `router_general_log` rows, whose
  FKs are NO ACTION, so the Python delete failed on any reporting router).
- **OCI support is dropped:** `get.ociDomainAppSecret` goes; the `OCI OAuth2` auth vendor
  row could go in a later schema version. `ignoreVersionUpgrade` is covered by
  `CONFIGURE REST METADATA MERGE OPTIONS {"ignore_service_upgrades_till": "<version>"}`.
- **Metadata info, now covered (2026-10-09):** `get.availableMetadataVersions` and
  `get.configurationOptions` -> `SHOW REST [METADATA] STATUS FORMAT=JSON`
  (`available_metadata_versions` from the bundled project's `releases/versions`,
  `configuration_options` = `config.data`). Nothing is undecided any more: 13 keep,
  105 remove.
- **Kept functions and REST SQL (2026-10-09):** every kept function that uses the
  database takes `session` and runs its SQL through `session.run_sql`.
  `load.serviceProject` runs each service file statement by statement
  (`lib.services.load_service_script`, `mysqlsh.mysql.split_script`);
  `dump.serviceProject` writes them with `SHOW CREATE REST SERVICE ... INCLUDING
  <endpoints> ENDPOINTS` (`lib.services.dump_service_script`, flags mapped by
  `endpoint_selection`), not the Python `get_service_create_statement` any more. `get.sdkOptions`, the three script analysis
  functions and `version` have no session (no database access); the user was offered
  an unused one for a uniform signature.
- **Done 2026-10-09:** the 105 functions are removed (files `schemas`, `db_objects`,
  `content_files`, `auth_apps`, `users`, `roles`, `routers`, `script` deleted; `init.py`
  keeps the sub-objects `get`, `dump`, `load`, `update`), with the Python REST SQL stack
  (`lib/mrs_parser/`, `MrsDdl*.py`, `lib/script.py`, `lib/grants.py`,
  `scripts/generate_mrs_parser.sh`, npm `update-mrs-parser`). 45 tests of removed
  functions are gone (`test_content_files.py`, `test_routers.py` entirely); the fixtures
  (`tests/conftest.py`, `tests/unit/helpers.py`) use `lib` and `CONFIGURE REST METADATA`
  instead of the wrappers. 203 tests pass. The dev guide's `mrs.add.*` sections are
  replaced by REST SQL.
- **`lib` cleanup (2026-10-09):** only code reachable from the 13 kept functions remains
  (static call graph from them, iterated to a fixed point): `lib/auth_apps`, `roles`,
  `routers`, `users` deleted, 211+ unused functions, classes and constants removed,
  the metadata version branches (`current_version[0] <= 2` ...) reduced to the 5.x path,
  `lib.general.get_status` is now `SHOW REST METADATA STATUS FORMAT=JSON` through
  `session.run_sql`, `DB_VERSION` / `REQUIRED_ROUTER_VERSION` gone (the shell module's
  `k_schema_version` is the source; `db_schema/README.md` points there). Plugin Python code
  13.9k -> 7.6k lines. Tests of removed code and the REST SQL behaviour tests (`test_roles`,
  `test_users`, `test_grants`, service `test_sql_*`; the shell repo tests REST SQL) are
  gone; the fixtures create services and content sets and drop objects with REST SQL
  (`tests/unit/helpers.py`: `ServiceCT`, `SchemaCT`, `DbObjectCT`,
  `create_mrs_phonebook_schema`); 174 tests pass.
- Nothing outside the plugin calls `mrs.*` by name (code_ext, mcp_plugin, msm_plugin
  checked); mcp_plugin only sends REST SQL.

## File-based REST SQL moved to the plugin (2026-10-09)

- Reason: a MariaDB server plugin (or any client sending REST SQL) cannot read the
  developer's files, so the shell module dropped every form that reads or writes client
  files: `CREATE REST CONTENT SET ... FROM '<dir>' [IGNORE ...]`, `CREATE REST CONTENT FILE
  ... FROM '<file>'`, `DUMP REST SERVICE ... TO`, `LOAD REST SERVICE FROM`. Files are sent
  inline (`[BINARY] CONTENT '<text|base64>'`); `SHOW CREATE REST SERVICE ... INCLUDING
  DATABASE [AND STATIC [AND DYNAMIC]] | ALL ENDPOINTS` is the dump.
- `LOAD [TYPESCRIPT] SCRIPTS` is ALTER-only (`alterRestContentSetOptions`/`loadScripts`):
  the module analyses the stored files and registers the MRS scripts itself (C++ port in
  the shell's `mrs_scripts.cc`). Only static folders stay public; sources and build
  output become private (enabled = 2). SHOW CREATE writes set, files, then `ALTER ... LOAD
  TYPESCRIPT SCRIPTS`.
- Plugin: `update.mrsScriptsFromContentSet` removed with its lib code (`lib/content_files.py`
  deleted; `init.py` has no `update` sub-object any more). New, REST SQL through
  `session.run_sql` only:
  - `mrs.dump.service(service_path, file_path, endpoints="DATABASE", overwrite)`.
  - `mrs.load.service(file_path, as_path)`: `as_path` rewrites the path token of the
    first statement (must be `CREATE ... REST SERVICE`) and the first `SERVICE <path>` of
    every statement.
  - `mrs.load.contentSet(directory, content_set_path, service_path, ignore_list,
    load_scripts, replace)`: ignore list matched against the path relative to the
    directory (`/node_modules/...`), default `*node_modules/*, */.*`; UTF-8 text without
    NUL or backslash is sent as `CONTENT` (quotes doubled), all else base64 `BINARY
    CONTENT`, so it works with and without `NO_BACKSLASH_ESCAPES`; each file gets
    `OPTIONS {"last_modification": ...}`; `load_scripts=None` auto-detects
    (`get_folder_mrs_scripts_language`).
- The test fixture's `/test_content_set` (`tests/unit/helpers.py`) is created with inline
  files via `lib.content_sets.content_file_statement`. New tests:
  `tests/unit/test_content_sets.py`, `test_services.py::test_dump_and_load_service`.
  177 passed.
- `DROP REST CONTENT SET` takes `FROM SERVICE`, not `ON SERVICE`.
- Private rule kept as ported (user decision, option a): in a scripts set only static
  folders stay public, `dist/` etc. stays private (server code). A PWA goes into its own
  content set (no LOAD SCRIPTS = all public) or a static folder such as `web/`.
- Docs lead with the plugin functions: new devGuide sections `StaticContent.md` ("Static
  Content and MRS Scripts") and `DeployingRESTServices.md`, included from `index.md` and
  `index_one_page.md`; tips with `mrs.load.content_set()` at the top of CREATE REST
  CONTENT SET/FILE and ALTER REST CONTENT SET. Python names are snake_case
  (`mrs.load.content_set`, `mrs.dump.service`, `mrs.load.service`). `index.html` and
  `index_one_page.html` rebuilt with plain pandoc: the committed copies were formatted
  differently, so their diffs are mostly layout. The one-page build now also rewrites
  `href="index.html#` to in-page links (`scripts/generate_html_docs.sh`).

## Docs and grammar moved to the shell repo (2026-10-09)

- `docs/` (pandoc), `grammar/` (ANTLR g4 + test copy), `scripts/update_grammar_docs.py`,
  `generate_rrd_svg_files.py`, `generate_html_docs.sh`, `run_grammar_test.sh` and their
  npm scripts are removed. The docs are GitBook pages in mariadb-shell
  `docs-ref/content/mariadb-rest-service/`, the grammar and both scripts in
  `modules/mrs/antlr_grammar/` (npm `docs-ref:mrs-diagrams`, `docs-ref:mrs-grammar-docs`
  at the shell repo root). The grammar test was an identical copy of the shell's
  `unittest/data/mrs/grammar_test.sql` (run by `mrs_grammar_test_norecord.py`).
- The two images of `examples/mrs_notes/README.md` moved to `examples/mrs_notes/images/`;
  `tools/update_version/update_version.py` no longer bumps docs versions.
