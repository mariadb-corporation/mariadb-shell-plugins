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
- **Undecided:** `get.availableMetadataVersions`, `get.configurationOptions` -> maybe
  `SHOW REST METADATA STATUS`.
- Removing `run.script` lets `grammar/`'s Python use, `lib/mrs_parser/`,
  `MrsDdlListener/Executor*.py`, `lib/script.py` go; first move
  `lib/services.run_sql_script` (used by `load.serviceProject`) to `session.run_sql` (the
  shell's MRS handler catches it) and drop `lib/content_files.py`'s `MrsDdlExecutor`
  import. The ANTLR grammar stays as the docs grammar.
- Nothing outside the plugin calls `mrs.*` by name (code_ext, mcp_plugin, msm_plugin
  checked); mcp_plugin only sends REST SQL.
