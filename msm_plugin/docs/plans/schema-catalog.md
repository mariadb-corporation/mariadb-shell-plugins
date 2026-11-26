# Schema snapshot out of the SQL, catalog on the server, drift in the diagrams

Status: plan, 2026-11-26. Three PRs, plus a small preparatory one in the
VS Code extension. Nothing here is started.

## Why

MSM Section 160 of a development script holds the `msm_schema_diagrams`
VIEW with one `schema_definition` row (format `msm-schema/1`) and one row per
diagram. The `schema_definition` row is the **development snapshot**: the
objects the CREATE statements define, as `mariadb-lsp outline` writes them,
plus every object's `range` / `selection_range` / `source` (0-based lines in
the files of this checkout), the `files` list, the outline's `notes`,
`rest_outline` and the `source_hash` that schema edits are checked against.

That whole document goes to every server and into every released version
file. Measured on the MRS metadata project
(`mariadb-shell/mysqlshdk/libs/mrs/db_schema/mariadb_rest_service.msm.project`):

| `schema_definition` row | bytes |
| --- | --- |
| as deployed today | 414,507 |
| without `range`, `selection_range`, `source` | 300,966 |
| also without `files`, `notes`, `rest`, `rest_outline` | 300,354 |
| the two diagram rows together | 31,420 |

The server does need a catalog: the extension's live viewer
(`ConnectionModelSource.read()` → `assembleSchemaModel`,
`vscode-extension/src/modeler/modelFormat.ts`) draws every table card's
columns, indexes, foreign keys and triggers from that row and throws without
it. It never reads INFORMATION_SCHEMA. But it uses none of the positions,
files or notes; the only members it reads outside the object lists are
`schema` and `source_hash`.

So the change is not "no snapshot", it is **two documents, one shape**:

1. The **development snapshot** (`msm-schema/1`, everything it has today)
   lives in a project file, not in SQL. It is a cache of the parser's
   output keyed by `source_hash`; it carries checkout-specific paths and
   line numbers and belongs to the project alone.
2. The **catalog** (`msm-catalog/1`): the same object lists with positions,
   files, notes and REST outline stripped, written as the `schema_definition`
   row of `msm_schema_diagrams`. It is what a server needs to draw diagrams
   and what a drift check compares the live schema against.

What it buys: Section 160 stops changing on every edit (positions no longer
live in it), so the `_shift_script_lines` dance of #89 mostly goes, diffs of
the development script are quiet, releases stop freezing file positions,
the yearly copyright bump no longer rewrites Section 160, and servers carry
25% less (more if routine bodies were left out; this plan keeps them, as a
hand-edited trigger body is drift too).

Drift ("the live schema differs from what was deployed / from the project")
becomes a comparison of two catalogs made by the same parser: the live
side is `SHOW CREATE` per object run through `mariadb-lsp outline`, which
`lib/live_schema.py` already does for views (`_view_reads`).

## Decisions taken in this plan (change here, not in the PRs)

- Snapshot file: `development/schema_definition.json`, the `msm-schema/1`
  document as is (`files` paths are already relative to `development/`).
  Committed to git like the row is today, so `base_hash` is stable across
  machines; it is derived and may be deleted, `msm.updateSchemaDefinition`
  makes it again.
- Catalog row: name `schema_definition` (unchanged, `workbench.py` reserves
  it), format `msm-catalog/1`, first row of the VIEW as today. Members:
  `format`, `schema`, `source_hash` (so a client can tell which snapshot a
  deployment came from), `options`, and the six object lists with
  `range` / `selection_range` / `source` removed at every depth. `files`,
  `notes`, `rest`, `rest_outline`, `outline_format` are dropped. Routine,
  trigger and event bodies stay.
- The mcp / shell answers of `get_schema_diagrams`, `edit_schema`,
  `set_schema_statements`, `set_rest_statements` and friends keep returning
  the rows **with the `msm-schema/1` snapshot first**, built from the file.
  Clients see no change; only the storage moves.
- `source_hash` keeps excluding Section 160 (diagram changes must not
  invalidate the snapshot). The snapshot file is not hashed either.
- Migration is on load, silent: a project whose VIEW still has an
  `msm-schema/1` row and no snapshot file takes that row as the snapshot;
  the next write puts the file down and replaces the row by the catalog.
  No format-version bump, no explicit command (though
  `msm.updateSchemaDefinition` does it too).
- The deployment template and the released files' layout are unchanged:
  Section 160 of a version file is still copied from the development script
  and still runs after 150 in the deployment script.

Open decisions, to settle before PR 1 starts:

- Whether `rest` should stay in the catalog so a server-side diagram can
  tell a REST object figure of a dropped endpoint (today
  `reconcile_diagram` runs only in the project; the live viewer draws
  `RestObjectFigure` from its own members). Plan: drop it.
- Whether a fresh clone without `development/schema_definition.json` and
  without the binary should still be able to **read** diagrams: yes, since
  the rows are in the SQL; edits refuse with the existing "no snapshot yet"
  message.

## PR 0 (vscode-extension): accept `msm-catalog/1`

Ships first; backward compatible. Without it a server deployed by the new
msm shows "The msm_schema_diagrams view has no schema snapshot".

- `src/modeler/modelFormat.ts`: `CATALOG_FORMAT = "msm-catalog/1"`;
  `ISchemaDefinition.format` is either; `assembleSchemaModel` and
  `schemaDefinitionOf` take a row of either format (snapshot preferred where
  both are present, which only happens on an un-migrated project read through
  msm, never on a server).
- `src/modeler/diagramQueries.ts`: `listDiagramsSql`'s
  `LIKE '%diagram%'` already excludes `msm-catalog/1`; leave it, add a test
  that says so.
- Tests: `src/test/modeler/modelerPanel.test.ts` and `modelSources.test.ts`
  get one connection-source case with a catalog row.
- Doc: `.claude/context/modeler.md` (theirs) notes the two formats.

## PR 1 (msm_plugin + mcp_plugin): snapshot file, catalog row

### `lib/diagrams.py` (format only, no I/O)

- `CATALOG_FORMAT = "msm-catalog/1"`, `POSITION_MEMBERS = ("range",
  "selection_range", "source")`, `CATALOG_DROPPED = ("files", "notes",
  "rest", "rest_outline", "outline_format")`.
- `catalog_document(snapshot: dict) -> dict`: a deep copy with
  `POSITION_MEMBERS` removed at every depth (dicts inside lists included:
  columns, indexes, foreign keys, checks, parameters all carry `source` /
  `range`) and `CATALOG_DROPPED` off the top level; `format` set to
  `CATALOG_FORMAT`. Pure, tested on a snapshot fixture: no key of
  `POSITION_MEMBERS` anywhere in the result, object counts equal, bodies
  equal.
- `is_catalog_row(row)` / `is_snapshot_row(row)`.
- `dump_document`: `_POSITION_NUMBER_WIDTH` and `is_position` were only
  needed because positions sat in the VIEW and had to move without changing
  a line break. Keep them in this PR (the un-migrated path still writes a
  snapshot row until its first write), remove in a follow-up once no
  project writes `msm-schema/1` into SQL.
- `dump_snapshot(document) -> str` for the file: `json.dumps` with
  `indent=1`, sorted keys off (keep outline order), trailing newline. A
  readable diff matters more than a byte here.

### `lib/modeling.py`

- `SNAPSHOT_FILE_NAME = "schema_definition.json"`;
  `_Project` gets `snapshot: Optional[dict]` and `snapshot_text:
  Optional[str]` (as read, for `write_if_changed`) and `snapshot_in_view:
  bool` (migration pending). `_Project.load`: read the file if present
  (strict: unreadable JSON or a document whose `format` is not
  `SCHEMA_FORMAT` raises, the file is msm's own); else take an
  `msm-schema/1` row of `_rows` as the snapshot and set `snapshot_in_view`.
- `_rows(project)` returns the VIEW's rows as stored (diagram rows, plus a
  catalog or legacy snapshot row). Add `_diagram_rows(project)` = only the
  `msm-diagram/1` rows, and move every caller that wants diagrams to it.
- `_schema_row(rows)` goes. Readers of the snapshot take
  `project.snapshot`: `_snapshot_is_current`, `list_diagrams`
  (`schema_definition.current`), `get_diagram_rows` (prepends a synthetic
  `{name: schema_definition, format: msm-schema/1, document:
  project.snapshot}` row so the mcp answer is unchanged), `_current_snapshot`,
  `rest_objects` consumers, `split_sections`.
- `_replace_snapshot(project, rows, ...)` sets `project.snapshot`, reconciles
  the diagram rows against it, and returns `(document, diagram_rows,
  changed)`; no row insertion.
- `_write(project, diagram_rows)`:
  1. Render the VIEW from `[catalog_row(project.snapshot)] + diagram_rows`
     where a snapshot exists, else diagram rows only (a project without a
     snapshot has no catalog; the live viewer then says so, as today).
  2. `_plan_write` once; the line delta of Section 160 still moves the
     snapshot's positions (`_shift_script_lines` on `project.snapshot`), but
     since positions no longer sit in the VIEW the second planning pass and
     the "changed its line count" check go.
  3. Write the breakout file, the script, and the snapshot file through
     `sections_lib.write_if_changed` (the file joins the returned `files`
     list only when written). A project with `snapshot_in_view` writes the
     file here for the first time, and the VIEW without the legacy row.
- `keeping_snapshot_current`: writes the new `source_hash` into the file
  only; Section 160 is untouched (today it rewrites the VIEW for 71 bytes).
- `read_diagram_rows` / `write_diagram_rows` (public, used by tests and
  `test_diagrams`'s helpers): keep names, now diagrams + the synthetic
  snapshot row in, diagram rows out. Check every caller.
- `create_project_from_workbench_model`, `_imported_snapshot`,
  `create_project_from_live_schema`: go through `_replace_snapshot` +
  `_write`, so they only need the signature changes.

### `lib/editing.py`, `lib/rest_editing.py`

- `check_base_hash(project, base_hash)` compares against
  `project.snapshot["source_hash"]`; returns the diagram rows. `edit_result`
  and `write_texts` build the answer's rows from `project.snapshot` +
  diagram rows (same shape as today). `_edited_sections` and the base-hash
  consumers at editing.py:1009 / 1039 read `project.snapshot`.
- `rest_editing` only needs `check_base_hash` / `write_texts`; no own change
  beyond the row helper.

### `lib/management.py`, `lib/renaming.py`, `lib/sections.py`

- `prepare_release`: unchanged in substance (`update_schema_definition`
  then copy the sections). Add the snapshot file to the "files the release
  rewrote" note in the docstring, not to the returned list (it is not a
  release file).
- `set_project_settings` → `keeping_snapshot_current`: already covered.
- `renaming.rename_schema`: the file moves with the folder copy; the final
  `update_schema_definition` (renaming.py:432) writes it in the copy. Add it
  to the `files` mapping test.
- `sections.split_sections`: `snapshot_updated` semantics unchanged.
- `templates/scripts/schema_next.sql`, `schema_a.b.c.sql`,
  `schema_deployment_a.b.c.sql`: the Section 160 description says the VIEW
  holds the catalog (`msm-catalog/1`) and the diagrams, and that the
  development snapshot is in `development/schema_definition.json`.
  `templates/msm.project/development/README.md` gets a line for the file.

### `msm_plugin/modeling.py` (shell wrappers), `mcp_plugin`

- Wrapper docstrings and `mcp_plugin/lib/msm_functions.py`
  (`list_schema_diagrams`, `get_schema_diagrams`, `update_schema_definition`,
  the tool table in `mcp_plugin/README.md` around line 333): say where the
  snapshot lives and that the VIEW's first row is the catalog. The answers'
  shape stays.
- mcp `require_project_access` / allowed paths: the file is inside the
  project, nothing to add.

### Tests

- `tests/unit/test_diagrams.py`: `catalog_document` (no positions anywhere,
  counts and bodies kept); `render_view_sql` with a catalog row; the
  snapshot tests (real binary) read the file instead of the row, one asserts
  the VIEW written has `msm-catalog/1` first and no `msm-schema/1`; the
  "VIEW over 1 MB deploys on the sandbox" test deploys a catalog row and
  reads it back with the extension's `readDiagramsSql` shape.
- Migration test: a project fixture whose Section 160 holds a legacy
  `msm-schema/1` row and no file → `list_diagrams` reports `current` as
  before, `edit_schema` with the row's `source_hash` succeeds, and after it
  the file exists and the VIEW has the catalog row.
- `test_editing.py`, `test_layout.py`, `test_sections.py`,
  `test_workbench.py`, `test_placement.py`, `test_rest_editing.py`,
  `test_renaming.py`: the row lookups (`SCHEMA_ROW_NAME` / `schema_definition`
  in rows) move to the helper that reads the file; count today: 19 + 5 + 5 +
  6 + 5 + 2 + 2 + 1 references.
- `test_management.py`: a release + deploy on the sandbox, then
  `SELECT format FROM msm_schema_diagrams` gives `msm-catalog/1` first.
- mcp `tests/unit/test_msm.py`: `get_schema_diagrams` still returns the
  `msm-schema/1` row first.

### Docs

- `ai-plugins` schema-management skills (overview, develop): the snapshot
  file and the catalog row; re-run `sync-skills.sh`.
- `msm_plugin/.claude/PROJECT_CONTEXT.md`: the Schema diagrams bullet.

### Order inside the PR

diagrams.py (pure, tested first) → modeling.py load/write/migration →
editing/rest_editing → management/renaming → wrappers and mcp docstrings →
templates → tests → docs. Run the suite with `MSM_TEST_LSP` set, since the
snapshot tests need the binary.

## PR 2 (msm_plugin + mcp_plugin): `compareLiveSchema`

A comparison of a live schema with a catalog, both in the outline's shape,
made by the same parser.

### The live catalog, `lib/live_schema.py`

- `live_catalog(session, schema, lsp_path=None) -> dict`: the `msm-catalog/1`
  document of a live schema.
  1. `list_schema_objects` (exists) for the names; `_show_create` (exists)
     per table, view, routine, trigger, event, and `SHOW CREATE SEQUENCE`.
  2. One script: `USE <schema>;` then the statements, each through the
     existing cleaners: `statements.without_definer`,
     `statements.unqualified` (own-schema qualifier off), `table_sql`
     (AUTO_INCREMENT off; **keep** every foreign key here, unlike the
     import), routines/triggers/events in `DELIMITER %%` blocks via
     `statements.delimiter_block`. Reuse `schema_sql`'s pieces, not
     `schema_sql` itself (it warns and reorders for an import).
  3. `lsp.outline(binary, script)` (no source dir) →
     `diagrams.schema_document_from_outline` → `diagrams.catalog_document`.
     The outline's `notes` with `applied: false` are returned as
     `warnings` (an object the parser could not read is reported, not
     silently equal).
  Needs `lsp.outlines_column_types` (feature `column_types`, lsp-server #52,
  already probed for the REST SDK) so both sides carry the normalized type.
  Without it the comparison still runs on the written type with a warning.

### The comparison, `lib/schema_diff.py` (no shell imports)

- `compare_catalogs(expected: dict, actual: dict) -> dict`:
  ```
  {
    "tables":    {"added": [name], "dropped": [name], "changed": [TableDiff]},
    "views":     {...}, "routines": {...}, "triggers": {...},
    "events":    {...}, "sequences": {...},
    "equal": bool
  }
  TableDiff = {"name", "columns": {"added", "dropped", "changed": [MemberDiff],
               "reordered": bool}, "indexes": {...}, "foreign_keys": {...},
               "checks": {...}, "options": [MemberDiff]}
  MemberDiff = {"name", "member", "expected", "actual"}
  ```
  `added` / `dropped` are from the **expected** side's view: `added` is on
  the server only, `dropped` is in the catalog but not on the server.
- Names match as `diagrams.same_name` (case-insensitive, like the outline).
  Foreign keys match by name; the server names unnamed ones
  `<table>_ibfk_<n>` exactly as `diagrams.name_foreign_keys` does, so the
  names agree by construction; a generated name that does not match falls
  back to matching by (columns, referenced table, referenced columns).
- **The one normalization rule**: a member the expected catalog leaves
  unset (`None` / missing: no charset, no collation, no engine, no
  `ROW_FORMAT`, no comment) is not compared, because the server filled a
  default. A member the expected side states is compared after
  normalization: type through `column_type` where present, else the
  written type upper-cased with display widths dropped (`int(11)` → `INT`);
  defaults with quotes and `CURRENT_TIMESTAMP()` / `current_timestamp()`
  folded; collations lower-cased. Bodies (routine, trigger, event) compare
  after trailing-whitespace trimming only; a difference is reported as
  `member: body` with both texts, no finer detail.
- Views compare by `definition` text the outline writes, same trimming;
  columns are not compared until lsp's `view_columns` exists.
- Table options compared: `engine`, `charset`, `collation`, `comment`,
  `row_format`, partitioning text; never `AUTO_INCREMENT`, `AVG_ROW_LENGTH`,
  `STATS_*`.

### The shell function and the expected side, `lib/live_schema.py` + `management.py`

- `compare_live_schema(session, schema, against="deployed" | "project" |
  "release", version=None, schema_project_path=None, lsp_path=None) ->
  dict`:
  - `deployed`: `SELECT document FROM <schema>.msm_schema_diagrams WHERE
    format = 'msm-catalog/1'` on the same session; refused when there is
    none (a schema deployed before PR 1, or not an MSM schema).
  - `project`: `_Project.load(path).snapshot` → `catalog_document`; the
    answer carries `snapshot_current` so a stale snapshot is visible; a
    missing snapshot is refused with the existing message.
  - `release`: `releases/versions/<file>_<version>.sql` outlined with
    Section 160 blanked (as `_outline_input` does) → catalog. Default
    `version` = the server's `msm_schema_version`, so "did anyone change
    the deployed 1.4.0 by hand" is one call.
  - Answer: `{schema, against, version (server's msm_schema_version or
    None), expected_version (release), snapshot_current (project), diff,
    warnings}`.
- Shell: `msm.compareLiveSchema(schema, against=, version=,
  schema_project_path=, lsp_path=)` in `management.py`, interactive mode
  prints a per-object summary; CLI `mariadb-shell -- msm
  compare-live-schema <schema> --against=project`.
- mcp: `msm.compare_live_schema(connection_id, schema, against, version?,
  schema_project_path?)` in `msm_functions.py`, with the `db` group like
  `msm.list_live_schema_objects`; `_in_project` only for `project` /
  `release`. Synchronous: one `SHOW CREATE` per object, the MRS project's
  170 objects take well under a second on a local server. If a schema with
  thousands of objects turns up, a task variant comes later.

### Tests, `tests/unit/test_live_schema.py` (sandbox + binary)

- Deploy a project (the `project` fixture of test_diagrams, released) and
  compare `deployed`, `project`, `release`: all `equal`.
- Normalization guard: a table with `INT`, `BIGINT UNSIGNED`, `VARCHAR(10)`
  without charset, `BOOLEAN`, `SERIAL`, `DECIMAL(10,2)`, `JSON`, `UUID`,
  `TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`, an
  unnamed FK, a CHECK, a generated column: deployed and compared → `equal`.
  This is the test every "spelled differently" fix lands in.
- Drift cases, each one ALTER by hand on the sandbox: column type change,
  column added, column dropped, index added, FK dropped, table dropped,
  table added, trigger body edited, routine dropped, view definition
  changed. Each asserts the exact diff entry and nothing else.
- Without `column_types` (monkeypatched probe): same cases, `warnings` says
  the types were compared as written.
- mcp `test_msm.py`: the tool over a sandbox connection, `against:
  deployed`.

### Docs

- `mcp_plugin/README.md` tool table, ai-plugins develop/deploy skills ("to
  see whether a deployed schema was changed by hand, …"),
  `PROJECT_CONTEXT.md`.

## PR 3 (vscode-extension): drift in the viewer

Read-only painting; generating the ALTERs to reconcile is out of scope.

### Where it is triggered

- Viewer toolbar: a **Drift** toggle, per diagram, kept in `IDiagramView`.
  - Connection source: compares `against: "deployed"` on the diagram's own
    connection and schema.
  - Project source: asks for a connection (QuickPick over the Connections
    model, remembered per project in workspace state) and compares
    `against: "project"`; the Drift bar offers "against release x.y.z"
    where the server reports a version.
- Connections tree: a schema with drift shows a badge after a compare ran;
  no automatic compare on expand (one `SHOW CREATE` per object is not a
  tree-expansion cost).
- `src/mcp/msmApi.ts`: `compareLiveSchema(connectionId, schema, against,
  options)` → `IDriftReport` (the PR 2 answer, typed in
  `src/modeler/modelFormat.ts` next to `ISchemaDefinition`).

### Protocol and webview

- `src/modeler/modelerProtocol.ts`: host → webview `{ kind: "drift";
  report: IDriftReport | undefined }` (undefined clears); webview → host
  `{ kind: "requestDrift" }` and `{ kind: "clearDrift" }`.
- `webview/src/modeler/tableCard.ts`: card content takes an optional
  `drift` for its table:
  - changed column / index / FK / check: the row in the warning colour
    with a tooltip "expected … / live …" (both texts from `MemberDiff`);
  - dropped (in catalog, not live): the row struck through;
  - added (live only): an extra row appended under its section in a
    dashed style, labelled from the live catalog;
  - a table dropped on the server: the card's border in the error colour
    and a "missing on server" badge in its title;
  - column order changed: a small "reordered" note under the Columns
    heading.
- Connections: an FK the server lacks draws its line dashed in the error
  colour; an FK the server has and the diagram does not is listed, not
  drawn (the diagram's figures are not changed by a drift view).
- A **Drift bar** above the canvas: "n objects differ on <connection>",
  live-only objects listed by name (tables, views, routines, triggers,
  events, sequences the catalog lacks), "Refresh" and "Hide". Objects that
  differ but have no figure on this diagram are listed there too, so a
  diagram that shows three tables still says the schema has drift
  elsewhere.
- Routine group / event group / view / sequence cards: a changed body or
  definition marks the row; a dropped one strikes it; added ones go in the
  bar.
- Legend entry in the viewer's existing legend.

### Tests

- `src/test/modeler/diagramService.test.ts`: the API call and its
  arguments for both sources; a stale snapshot (`snapshot_current: false`)
  shows a notice offering `msm.update_schema_definition` first.
- `webview/test/modeler/tableCard.test.tsx`: every painting case above on
  one fixture table.
- `ui-test/tests/09-msm.test.ts`: optional e2e: deploy, ALTER, toggle
  Drift, assert the badge.

## Sequence and dependencies

```text
PR 0  extension accepts msm-catalog/1          (no dependency)
PR 1  msm: snapshot file + catalog row         after PR 0 is released
PR 2  msm + mcp: compare_live_schema           after PR 1 (reads the catalog row)
PR 3  extension: drift painting                after PR 2's tool is in the shell build
```

lsp-server needs no change for PR 0–3. Later improvements that make the
comparison finer, and belong to lsp-server: `view_columns` (view columns
compared), and an outline of the server's own `SHOW CREATE` output that is
guaranteed to round-trip (today's cleaners in `live_schema.py` handle the
known differences).

## What to leave alone

- Section 160's inline / breakout rule and `maxInlineSectionSize`: images in
  diagrams still make MB-sized VIEWs.
- `source_hash` semantics, `base_hash` checks, every mcp answer shape.
- The deployment template, the update template, released files.
- `rest_sdk_data`, `rest_removal`, `placement`: they outline afresh and
  never read the snapshot row.
