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
  included, since `SHOW CREATE REST VIEW` loses them.
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
  mcp_plugin's mrs group and the module of mariadb-shell PR #71; raise
  `MINIMUM_SHELL_VERSION` once a release has them.
- The data mapping editor has no SDK language mode (the old one showed
  per-language class names and datatypes).
