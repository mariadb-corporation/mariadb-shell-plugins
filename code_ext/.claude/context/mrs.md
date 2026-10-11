# MariaDB REST Service (MRS)

The REST Service part of the Connections view and its dialogs: a port of
the MySQL Shell for VS Code extension's MySQL REST Service support
(`mysql-shell-plugins/gui/extension` + the frontend's `modules/mrs`), with
MariaDB's names and REST SQL as the only API. Branch `wip/mrs_code_ext`
(2026-10-09). TypeScript notebooks are out of scope for now.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Architecture

- **Reading is `SHOW ... FORMAT=JSON`, writing is REST SQL**, both through
  `db.execute_sql` on the tree's `UI Backend` session, so every call is an
  action row of that connection. The shell's built-in `mrs` module answers
  each JSON statement with one cell (a document, or an array for a list).
  mariadb-shell PR #71 added FORMAT=JSON to every list statement, `SHOW
  REST SCRIPTS`, `SHOW REST AUTH APPS ON ANY SERVICE` and a no-op
  `USE REST METADATA SCHEMA x` when x is the session's already, for this.
- **What needs the developer's files goes through the `mrs.*` MCP tools**
  (mcp_plugin `lib/mrs_functions.py`, the mrs plugin's functions): SDK
  export / rebuild, service and project dump/load, uploading a folder as a
  content set, MRS script analysis. `McpSession.mrsTools`, `MrsToolsApi`.
  A `--gui` server allows every path, so no elicitation happens.
- **Several metadata schemas** (`<prefix>mariadb_rest_service<postfix>`):
  `SHOW REST METADATA SCHEMAS FORMAT=JSON`; with more than one, each gets a
  root of its own (`metadataSchema` on every node) and every call is sent
  as a script `USE REST METADATA SCHEMA x; ...` in ONE `execute_sql_script`
  call, so nothing interleaves on the shared session (`MrsApi.run`). The
  `mrs.*` tools work on the session's chosen schema: the commands run
  `api.run(scope, [])` (the USE alone) first.
- **The current service is session state** (`USE REST SERVICE`), shown by
  `is_current` in `SHOW REST SERVICES`; Set as Current runs the USE on the
  tree's session.

## Files

| File | What |
| --- | --- |
| `src/mrs/mrsTypes.ts` | The FORMAT=JSON documents (status, service, schema, object with data mappings, content set/file, auth app/vendor, user, role, daemon, columns, script definitions). `vscode`-free |
| `src/mrs/restSql.ts` | Every statement and the quoting (text single quoted with `''` and `\\`; request paths plain or back-ticked; developer paths `devs@/path`) |
| `src/mrs/dataMapping.ts` | The data mapping editor's model: columns + stored fields merged, references loaded lazily, the edits, and the mapping's REST SQL (`mappingText`, `objectSql`) |
| `src/mrs/mrsDialogs.ts` | Each dialog's fields, defaults, validation (the old dialogs' messages) and statements; shared by webview and host |
| `src/mrs/mrsApi.ts` | `MrsApi` (REST SQL calls) and `MrsToolsApi` (`mrs.*` tools) |
| `src/mrs/mrsDialogPanel.ts`, `mrsDialogProtocol.ts` | One panel class for every dialog (one per kind), the message protocol |
| `src/mrs/mrsCommands.ts` | Every `mariadb.mrs.*` command; builds the dialog specs |
| `src/tree/mrsModel.ts`, `mrsTreeItems.ts` | The tree nodes, their children, their rows |
| `webview/src/mrs.tsx`, `webview/src/mrs/` | The dialogs' frontend: `MrsDialog` (frame, message loop), `dialogs.tsx` (the 8 dialogs), `fields.tsx`, `DataMappingEditor.tsx`; `vite.mrs.config.ts` builds `dist/webview/mrs.{js,css}` |
| `images/{light,dark}/mrs*.svg`, `restDaemon*.svg`, `docs.svg` | The old extension's icons; `router*` renamed `restDaemon*`, `ociProfile` -> `mrsUser` |

## The tree

Connection (open) -> **MariaDB REST Service** (per metadata schema; icon
disabled / update available) -> services (icon by current, in development,
published, disabled) -> REST schemas (module / access suffix
Disabled/Private/Locked) -> objects (Table/View/Procedure/Function/Script
icons); content sets -> files; linked auth apps. Then **REST Daemons**
(active / not active / needs upgrade against `required_rest_daemon_version`)
-> the services each serves, and **REST Authentication Apps** -> users.
Private (enabled = 2) rows are hidden unless Show Private Items. A server
without metadata shows no root; "Configure MariaDB REST Service..." on a
connection deploys it. Context values: `mariadbMrs<Kind>[.<state>]`.

## Data mapping editor

`webview/src/mrs/DataMappingEditor.tsx` follows the old
`MrsObjectFieldEditor` row for row. Only its look and interactions
differ from a plain form, so keep them when changing it:

- The tree draws the class row first, then `{` / `[ {` and `}` /
  `}, ... ]` bracket rows around each opened reference's list, and a
  closing `}`.
- Icons are the old SVGs (`webview/src/assets/mrs/`), drawn as CSS masks
  on `<div>`s in `--vscode-icon-foreground`. Vite inlines them as `data:`
  URIs, which the CSP allows for images. Never use `<button>`: the
  dialog's button styles give them the blue boxes.
- Flags (`.fieldOptions`): those that are on (`.selected`) always show.
  `.notSelected` flags and `.action` icons (select/deselect all, delete
  field) show only on `.jsonCell:hover`; the `…` label stands in for them
  until then.
- Names are text. A double click opens an inline input: Enter keeps,
  Escape or blur drops. A new field's `newField` is all selected; a result
  column is edited as `name: type`, with the caret before the colon.
- A single click on a reference row toggles it after 200 ms; a double
  click cancels that. The checkbox opens and includes a closed reference.
  Unticking a reference closes it and drops its loaded children.
- Unnest (1:1, n:1) shows the reference's enabled fields as copies at its
  level, marked "(⤵ ref)", with the reference's checkbox indeterminate.
  A 1:n reduced to one field is `unnest` in the model, but stays a list
  with the other fields disabled.
- The DB Object input is read-only: renaming the database object is not
  in the model.
- Tests find things by `data-field` / `data-copy`, `aria-label` and
  `role="switch"`/`"checkbox"`. Keep those attributes.

- `newKey()` in `src/mrs/dataMapping.ts` runs in two module copies: the
  extension builds a table's fields, the dialog a reference's. Keys carry a
  random prefix per copy. With a bare counter, `city.name` and `country.name`
  both got `new-2`, one edit rendered two inputs, and the blur of one dropped
  the edit.

## Decisions

- Old features left out: MySQL Router bootstrap/start/stop/kill and service
  debug (the REST Daemon is not in these repos; user decision: list and
  delete only), JSON dump/load of schemas/objects (plugin functions
  removed), "Include All Objects" SHOW CREATE variants (no such REST SQL),
  OpenAPI UI content set (`${openApiUi}` not ported to the module), load
  project from URL, the major-upgrade drop-and-recreate prompt (5.0.0 is
  the first release).
- Added beyond the old extension: Edit Content Set (ALTER; the old one was
  a TODO), Delete Content File, Load REST SERVICE SQL Script, the schema
  dialog's Auth. Required, a metadata schema name on first deployment.
- Edits use ALTER (CREATE OR REPLACE would give new ids); a view is saved
  with its whole mapping generated here, `@DATATYPE` and `JSON SCHEMA`
  included. `SHOW CREATE REST VIEW` lost them until mariadb-shell's
  2026-10-11 fix (on #71's branch, see the shell repo's mrs-module.md);
  generating the text here keeps working with a shell without it.
- Reference CRUD flags are always written explicitly (`@INSERT`/
  `@NOINSERT`...) so a following `@NOCHECK` is the reference's, not the
  field's (the grammar resolves the ambiguity toward the field).
- `GRANT/REVOKE REST ROLE` always name the role's scope (`ON ANY SERVICE`
  for a global role): the shell looks a role up on the current service
  otherwise, and `Full Access` was "not found".
- Request URLs: `mariadb.mrs.restDaemonUrl` (default
  `https://localhost:8443`) + service + schema + object path.

## Tests

- Unit tests under `src/test/mrs/`, `src/test/tree/mrs*.test.ts`,
  `webview/test/MrsDialog.test.tsx`, `DataMappingEditor.test.tsx`.
- UI tests: `ui-test/tests/02-mrs.test.ts` (ExTester, see
  testing-and-debugging). It runs against a sandbox that `01-sandbox`
  deploys and `99-cleanup` drops. It hovers before clicking a flag,
  since flags that are off are not displayed otherwise.
- **Opt-in end-to-end**: `src/test/mrs/mrsShell.e2e.test.ts` deploys a
  sandbox through the real MCP server and runs the dialogs' statements:
  `MARIADB_SHELL_E2E=<path to mariadb-shell> npx vitest run mrsShell` with
  `/opt/homebrew/bin` on PATH. The shell runs with
  `MARIADB_SHELL_USER_CONFIG_HOME` set through `/usr/bin/env` (the MCP SDK
  passes a server only a few variables; changing HOME instead loses the
  macOS keychain the secret store needs) and this repo's plugins linked in.

## Known gaps / next steps

- The `mrs.*` tools and REST SQL FORMAT=JSON need a shell carrying
  mcp_plugin's mrs group and the module of mariadb-shell PR #71. No release
  has them yet (26.10.1, the current `MINIMUM_SHELL_VERSION`, does not);
  raise it once one does.
- The data mapping editor has no SDK language mode (the old one showed
  per-language class names and datatypes).

## In the Actions tab

What the REST Service does is a row of the connection's Actions in the
panel (2026-10-10), in plain words, after the user found the first take
far too verbose:

- `src/mrs/mrsActions.ts`: `IMrsAction` (id, message, pending, detail,
  error, statements) and `describeRestSql()`, which names a call's REST
  SQL by the statement that matters (the first that is not a USE) in both
  forms (`IMrsWords`): "Creating REST Service '/myService/v1'…" while it
  runs, "Created REST Service '/myService/v1'" once done; also Saving,
  Changing, Deleting, Configuring, Granting, Revoking, Reading. Title Case
  object, name in single quotes - the user's form.
- Every action is reported TWICE under one id: as it starts (`pending`,
  the spinner a running SQL script has) and as it ended; the panel
  replaces the row (`putAction`). `MrsApi.#act` does it for a `run()` on
  its own and for a `group()` (AsyncLocalStorage: every call inside is
  part of the group's action); `MrsToolsApi.#tool` for each `mrs.*` call
  ("Dumping the REST Client SDK to ..." / "Dumped ...").
- Not shown unless they fail: a read on its own (a dialog filling its
  choices) and the tree's listings (`onlyFailures`) - they drowned the
  real actions.
- Every action carries the REST SQL it ran (`IMrsStatement`: the
  statement on one line, a caption saying what it does from
  `describeStatement()` - "Switched to REST Metadata Schema 'm'",
  "Granted REST Role 'Full Access'" - or its error, or "... - not run,
  after the error", and its time). ONE statement is shown beside the
  action, in the Information column, with no row under it; SEVERAL are the
  rows under it, caption in the message column and the statement beside
  it, so the REST SQL can be learned by reading. Success shows as for SQL:
  no error marker and no error row (the user's rule, 2026-10-11).
- **Verbose** (`mariadb.actions.logAllCalls`, read on every call as
  `MrsApi`'s `verbose`): the listings and standalone reads are shown too.
- `src/mrs/mrsActivity.ts` `mrsActionRow()`: pending - an event row with
  the spinner; done - an event row with the one statement (or the detail)
  in the Information column, or a run row with a child per statement;
  failed - a run row with the done message, "Finished with 1 error", and
  the failed statement (or a row of its own) as its child, pointed at by
  `jumpToRowId`, as a failed SQL script shows. Work on no connection goes
  under General Actions, with `logAllCalls` only.
- UI tests in `02-mrs`: "Created REST Service '/uitest'" with its
  captioned statements under it, "Deleted REST Service '/uitest'" with
  its one statement beside it, "Configured REST Metadata", no "Listed REST"
  row by default, the SDK dump, and with the setting switched on in the
  Settings editor the listings with their REST SQL under them.
- **No success notifications** (2026-10-11, the user's call): what an MRS
  action did is its row in the Actions tab, so saving a dialog, a delete,
  Set as Current, linking an auth app, project / service dump and load and
  the SDK export say nothing else. A dialog's `save` and `#drop` return
  nothing. Kept: the clipboard copies, Export REST SQL to a file, and
  "every auth app is linked already" - none of them is an action row.
- **A failure is notified AND shown**: the command wrapper's catch
  (`reportError`) and a dialog's failed save (`IMrsDialogHost.onFailed`,
  also when the dialog stays open with the error in it) call
  `IMrsCommandsHost.showActions(uri)` -> `ResultViewProvider.showActions`:
  the panel comes up on that connection's actions, and a `showActions`
  message switches the webview to the Actions tab and scrolls to the newest
  failed row (`jumpToRowId`, else the row).
- **Long operations (checked 2026-10-11):** closing a dialog tab cancels
  nothing; `#save` runs on, a later failure is notified. A content set
  upload is several top-level rows (the `mrs.load_content_set` call, then
  the settings' REST SQL), not one group. `MRS_TOOL_TIMEOUT_MS` (10 min)
  ends the CLIENT's wait: the server's thread finishes the upload, the
  extension reports a timeout and skips the settings statements. A
  connection closed mid-run drops the final report, leaving the spinner.
