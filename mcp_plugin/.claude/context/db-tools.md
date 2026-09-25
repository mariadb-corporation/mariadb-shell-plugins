# The db tools

`db.execute_sql` and `db.execute_sql_script`, the per-statement script
results the VS Code extension reads, the three introspection tools and the
SQL behind them.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Connecting and session
handling are in [connections.md](connections.md).

## Architecture / key decisions

- **Every result set of a statement is read** (`_serialize_result` loops
  `result.next_result()`, `_read_result_set` reads one). A CALL returns one
  set per SELECT the procedure runs, then a status with none. The FIRST
  stays in `columns`/`rows`, so a client that knows nothing more still reads
  it; the rest go, in order, in `additional_result_sets` (`[{columns,
  rows}]`), present only when there is more than one. Before, only the first
  was read and the rest silently dropped (the next statement still ran:
  the shell drains them). `getattr` guards `next_result` for results that
  have none. Verified live against a sandbox, through both tools.
- **Column types** (2026-09-25, for the extension's value display): every
  result set carries `column_types`, one per column in the order of
  `columns` (first set at the top level, the rest inside
  `additional_result_sets`). `_column_type` is the name of the shell's
  `Type` (`<Type.INTEGER>` -> `INTEGER`) with one refinement, `BLOB`:
  measured on 12.3, BINARY, VARBINARY, every BLOB AND VECTOR are all
  `BYTES`; only the `BLOB` flag (`get_flags()`) sets the blobs apart, and
  nothing tells a VECTOR from a VARBINARY. JSON and the spatial types have
  their own (`JSON`, `GEOMETRY`). Left out when no column reports a type
  (stub results, an older shell). Values are unchanged: binary still comes
  as hex.

- **Paging** (2026-09-25, for the VS Code extension's result pages):
  `db.execute_sql_script(limit=)` and `db.execute_sql(limit=, offset=)`.
  `_limit_statement` appends `\nLIMIT n [OFFSET m]` (own line, so a
  trailing `--` comment cannot swallow it) to a statement whose first word
  is SELECT or WITH, judged on `_top_level_words` - a small scanner that
  steps over strings, backtick identifiers and all comments and reports
  each word with its paren depth. Left AS WRITTEN: anything else; a WITH
  with a top-level INSERT/UPDATE/DELETE/REPLACE (or no SELECT); and any
  top-level LIMIT, FETCH, OFFSET (limits itself already), INTO, PROCEDURE,
  LOCK, FOR UPDATE / FOR SHARE (LIMIT must come BEFORE those, so appending
  is a syntax error). `FOR SYSTEM_TIME` is not a locking FOR and is limited.
  - `_run_limited` sends `limit + 1` rows' worth; `_page_result` drops the
    extra row and sets `has_more_pages` (true/false). The key is present
    ONLY when the limit was applied - that is how a client knows it can
    page. snake_case like every other key on this wire.
  - A limited statement refused with **1064** is re-run as written: a
    syntax error means nothing ran, so it cannot take effect twice. Any
    other error is NOT retried.
  - `_check_paging`: non-negative ints only (a JSON `true` is refused), and
    an offset needs a limit - refused before the session is touched.
  - Tests: `test_db_paging.py` (the scanner and the rules, table-driven;
    the tools over a stub session that honours LIMIT/OFFSET), plus a block
    in `_db_flow` against the real server proving every limited form is
    accepted WITH the LIMIT and the skipped ones still run.

- **SQL exec**: `db.execute_sql` = single statement (+ optional `?` params, one result
  dict). `db.execute_sql_script` = multi-statement via `mysqlsh.mysql.split_script()`,
  returns a LIST; accepts `sql_script` XOR `file_path` (file must be an allowed path).
  `sandbox.deploy` port REQUIRED int on all 7; `ssl=False` default.

- **Warnings come back with their texts, and the count is read at the right moment**
  (2026-09-22, for `code_ext`, which shows one row per warning under the statement that
  raised it):
  - `_serialize_result` reads `result.warnings_count` **after** `fetch_all()`, not
    before. It is `mysql_warning_count`, which for a statement returning a result set is
    not final until that set has been consumed — read first, it answered **0 for every
    SELECT**, however many warnings the statement went on to report. A statement with no
    result set was always right, which is why this survived so long. Verified against a
    real server in `test_db_sql.py`'s `_db_flow`: `SELECT CAST('not-a-number' AS
    UNSIGNED)` reports 1, code 1292.
  - `_serialize_warnings(result)` adds `warnings`: one dict per warning with `level`,
    `code` and `message`, exactly as `SHOW WARNINGS` reports them. Present only when
    there are any, so a client can skip the key.
  - Fields are read **by ATTRIBUTE** (`warning.level`). A warning comes back as a shell
    `Row`, the same type a query result row is, and subscripting one takes the sequence
    path — `warning["level"]` raises `sequence index must be integer, not 'str'`. That
    is the opposite of the surrounding code, which reads result rows by POSITION for its
    own reason (a label cannot reach the second of two columns sharing one).
  - `get_warnings` is looked up with `getattr` and missing means no texts, so a shell
    that predates it still reports the count. That also keeps this safe to ship without
    the extension, and the extension safe to ship without this.

- **Script results carry their position, their time, and their own failure**
  (2026-09-20, for the VS Code extension in `code_ext`, which cannot work any of it out
  from the outside — the whole script is ONE call):
  - `_serialize_result(result, session_restarted=, statement_index=, execution_time=)`.
    Both new args are optional and omitted from the output when not passed, so
    `db.execute_sql` (one statement, nothing to count) is unchanged. `execution_time` is
    seconds, `round(..., 6)` — the digits a float carries past that are clock noise.
  - **A failing statement no longer RAISES.** Its entry carries `error` (the message) and
    `statement` (the text, `_abbreviate`d to 2000 chars) INSTEAD of a result set, and the
    entries for everything that already ran are returned with it. That is the point: a
    script is NOT a transaction, and the old behaviour threw away the record of what had
    already taken effect. Callers must check every entry for an `error` key — the docstring
    says so in capitals, because an LLM client sees `isError: false` on the tool result.
  - `stop_on_error: bool = True`. True ends the script at the first failure (fewer entries
    than statements means it stopped early); False attempts every statement, one entry per
    statement, error or not. Default True: a script whose later statements build on its
    earlier ones must not plough on.
  - The failure is `general.log_event`'d with the statement index and the connection's
    log-id prefix before being returned, so the server log still shows it.
  - `statement_index` is `enumerate()` over the non-empty pieces the splitter returned —
    NOT `len(results)`, which is what it used to be. The two only agree while every piece
    produces an entry, and a comment-only piece does not (next bullet).
  - **A whole-line `--`/`#` comment is NOT run** (2026-09-20). The shell's splitter returns
    one as a statement of its own — see the `split_script` gotcha — and the server ACCEPTS a
    comment-only query (OK packet, 0 columns) rather than rejecting it, so `-- a note`
    before a query used to run and report a result of its own. `_is_comment_only()` filters
    them in the loop. It still **takes up a `statement_index`**, deliberately: a caller
    pairs results with the statements it split for itself by that number, and renumbering
    would move every result after a comment onto the wrong statement. Keeping the numbering
    is also what makes this safe to ship WITHOUT the extension: an unpatched `code_ext`
    keeps pairing correctly and simply stops seeing the phantom row.
  - `_is_comment_only()` tests only for a leading `#`, or `--` before whitespace/end. That
    is enough BECAUSE the splitter only splits out a comment that OPENS a statement, and
    the range it returns ends at that line — so a piece that starts with one is a piece
    that is nothing else. Verified against the real splitter: `SELECT 1\n-- mid\nFROM
    dual;` stays ONE piece, `/*!40101 ... */` and `/*+ ... */` (both of which DO carry SQL)
    are never split out, and `SELECT 1--2` is arithmetic.
  - Tested in `test_db_sql.py`'s `_db_flow`: `statement_index`/`execution_time` on every
    entry of the happy script; a duplicate-key script under the default (2 entries, the
    second carrying `error`, `statement_index` 1, no `rows`) plus a follow-up SELECT
    PROVING the first insert really survived; then the same script with
    `stop_on_error: False` (3 entries, the third succeeding after two failures); and a
    leading-comment script against the real server (ONE entry, `statement_index` 1).
    `test_db_script.py` covers the splitting on a recording stub session — what reached the
    server, not what the server made of it.

- **Introspection tools** (committed in 3482634a; all sync, all built on the user's own
  SQL — the queries came from the user verbatim, only parameterized; do NOT "improve" them
  without asking):
  - `db.list_schemas(connection_id)` -> `_LIST_SCHEMAS_SQL` over I_S.SCHEMATA. Returns a
    bare LIST of `{schema_name, schema_type, schema_comment}` (lowercase aliases — the
    USER renamed them from upper case), not the full `_serialize_result` envelope, since
    a fixed SELECT has no useful affected-rows/warnings. `schema_type` is computed in SQL
    (System Schema / System Information Schema / User Schema) and is ALSO the first
    ORDER BY key, so rows sort by LABEL TEXT -> user schemas come LAST. Intended.
  - `db.list_objects(connection_id, schema_name, object_type="table")` -> one query per
    type in the `_LIST_OBJECTS_SQL` dict (table, view, function, procedure, sequence,
    trigger, event), each taking the schema as its single `?`. Columns: `name`+`comment`
    for table/view, `name`+`datatype` for sequence, `name` alone for the rest.
  - `db.get_object_details(connection_id, schema_name, object_name, object_type="table")`
    -> `{"basic": {schema, name, type, comment}}` for every type, plus per type:
    table -> `columns`+`constraints`+`references`; view -> `columns` only;
    function/procedure -> `parameters` (name, mode, datatype, parameter_default) +
    `returns`; sequence/trigger/event -> a single-row `details` dict.
  - Shared plumbing: `_query_rows` (run + return rows), `_normalize_object_type`
    (case-insensitive validation against the `_LIST_OBJECTS_SQL` keys, used by BOTH
    list_objects and get_object_details), `_parse_json_fields`.

- **Introspection SQL gotchas that are baked in on purpose**:
  - `table` listing uses `TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')` — system
    versioned tables have their OWN TABLE_TYPE and would otherwise vanish. Sequences and
    views have their own types, so they drop out by themselves.
  - `INFORMATION_SCHEMA.SEQUENCES` is **MariaDB 11.5+**. No fallback for older servers
    (would need I_S.TABLES `TABLE_TYPE='SEQUENCE'` + I_S.COLUMNS on
    `next_not_cached_value`).
  - A routine's RETURNS clause is the `ORDINAL_POSITION = 0` row of I_S.PARAMETERS (no
    name, no mode) -> split into `returns`, excluded from `parameters`. Procedures have
    no such row, so `returns` is None. Return type comes from PARAMETERS, NOT from
    `ROUTINES.DTD_IDENTIFIER` — one source, one documented rule.
  - Triggers have NO comment column anywhere in the information_schema, hence
    `SELECT NULL as comment` in `_OBJECT_BASIC_SQL["trigger"]`.
  - Existence check is "no `_OBJECT_BASIC_SQL` row" (works for all 7 types and catches a
    type mismatch, e.g. asking for a table as a sequence), NOT "no columns".

- **Deliberate changes to the user's supplied SQL** (each flagged to and left standing by the
  user):
  - constraints query needed `AND tc.TABLE_NAME = kcu.TABLE_NAME` — constraint names are
    unique per TABLE, not per schema, so joining on schema alone makes every table's
    `PRIMARY` match every other table's PK columns. Test pins this (two PK tables in the
    schema, `items` must report exactly one constraint row).
  - dropped the columns query's dead `LEFT OUTER JOIN` on KEY_COLUMN_USAGE: nothing was
    selected from it and it can duplicate a column that sits in two FKs. Restore with
    DISTINCT if fields from it are ever needed.
  - also added `ORDER BY tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION` for determinism.
  - **T9: the four aggregates in `_OBJECT_REFERENCES_SQL` are ordered by
    `k.ORDINAL_POSITION`** — the column's place in the FOREIGN KEY. The user asked for
    `c.ORDINAL_POSITION` (its place in the TABLE); that was flagged as the wrong ordinal, with
    the measurement below, and the user said commit. Do not switch it back.

- **REST SQL** (commit 0e97c9e9): `db.execute_sql` can run MRS REST SQL (e.g.
  `CONFIGURE REST METADATA`) because `mrs_plugin` registers a shell SQL handler
  (`@sql_handler("MRS", prefixes=...)`, prefixes incl. "CONFIGURE REST ") that intercepts
  `session.run_sql`. Requires mrs_plugin loaded in the server subprocess (symlinked by
  run_tests). antlr4 (MRS parser dep) is bundled in mariadb-shell.

## Current state

- Shell fns: `mcp.info`, `mcp.version`, `mcp.setup`, `mcp.startServer` (options: `host`,
  `port`, `transport`, `function_groups`, **`allowed_hosts`**).

- Tools: **31 total**, in 4 groups. migrator.* (**4**: `set_config`, `plan`, `run`,
  `resume`) — registered ONLY where the tooling is installed, see the migration bullet
  under Architecture. db.* (**8**: `list_connections`, `connect`, `list_schemas`, `list_objects`,
  `get_object_details`, `execute_sql` (+ `limit`/`offset`), `execute_sql_script` — now
  with `stop_on_error`, `limit` and per-statement `statement_index`/`execution_time`/
  `error`/`has_more_pages`; every result set carries `column_types` — `close`),
  msm.* (**12**, path-guarded, async — the 12th is `deploy_schema`, gated on the db group),
  sandbox.* (7, `sandbox_dir`-guarded, async, port required).

- The three introspection tools (`db.list_schemas`, `db.list_objects`,
  `db.get_object_details`) are COMMITTED (3482634a, pushed).

## Files that matter

- tests/unit/test_db_sql.py -> single `_db_flow` coroutine over ONE stdio session:
  connect -> execute_sql (incl. a DECIMAL/DATETIME serialization check and T8's two
  columns sharing one label) -> execute_sql_script (inline + file + denied + the
  statement_index/execution_time metadata + a failing script under both stop_on_error
  modes) ->
  list_schemas -> creates one object of EVERY type in a throwaway schema (incl. a
  system-versioned table, a sequence, a trigger, an event, an `orders` table with an FK
  to `items`, and T9's `pairs`/`pair_refs` whose composite FK is declared out of table
  order) -> list_objects (all 7 types + default + case-insensitivity + bad type +
  unknown schema) -> get_object_details (table both FK directions incl. the composite
  key's column order from both sides, view, function, procedure, sequence, trigger,
  event, plus not-found errors) -> DROP SCHEMA -> close. **The object script's statement
  count and the table-listing order are asserted, so adding a table to the flow means
  updating both.**

- lib/msm_functions.py, lib/sandbox_functions.py -> async tools w/ `ctx: Context`;
  msm_functions also holds the db-group-gated `msm.deploy_schema`.

## Gotchas / things not to repeat

- **`db.execute_sql_script` returning a failure instead of raising is a CONTRACT CHANGE,
  and a quiet one.** The tool result's `isError` is now `false` for a script that failed:
  the failure lives in an entry's `error` key. An LLM client that only looks at `isError`
  will read a failed script as a success — which is why the docstring says, in capitals,
  to check every entry. If this ever needs revisiting, the alternatives both cost more
  than they give: raising again throws away the record of what already ran (a script is
  not a transaction, so that record is the useful part), and returning a top-level
  `{ok: false, results: [...]}` envelope would break every existing caller of a tool whose
  contract is "a LIST, one entry per statement".

- **`db.get_object_details` does NOT return a `constraint_name` on a reference row.** The
  constraint is `reference_mapping["constraint"]`, and it is `<schema>.<name>`, not the
  bare name. The row's own keys are `position`, `name`, `ref_column_names`,
  `reference_mapping`, `table_schema`, `table_name`. Cost one failing assertion in the e2e
  test, on a `KeyError`.

- **The shell already returns DECIMAL and DATETIME as STRINGS.** Verified: a
  `CAST(2 AS DECIMAL(10,2))` arrives as `'2.00'`, and I_S.SEQUENCES `START_VALUE` /
  `INCREMENT` as `'1'`. So `_serialize_result` needs NO numeric conversion — a
  `numbers.Number -> int/float` branch was written, proven unreachable, and REMOVED. Do
  not re-add it. What is left is bytes -> hex plus a `str()` guard for anything exotic
  (that guard is the single uncovered line in db_functions.py; it is insurance against a
  serialization crash killing a tool call, not a code path the tests exercise). Making
  decimals into JSON numbers would need CASTs in the individual queries (and
  `CAST(... AS SIGNED)` would mangle an UNSIGNED sequence past 2^63) or numeric-string
  sniffing, which would silently reinterpret genuine VARCHAR data. Don't.

- **run_sql rejects multi-statement** (1064) — split via `mysqlsh.mysql.split_script`, and
  don't leave a trailing `;` on single-statement SQL constants.

- **`split_script` returns a whole-line comment as a STATEMENT.** `-- a note\n\nSELECT 1;`
  comes back as `["-- a note", "SELECT 1"]`. That is the C++ `Sql_splitter::next_range()`
  (`mysqlshdk/libs/utils/utils_mysql_parsing.cc`, the `case '#'` and `case '-'` branches:
  *"if the whole line is a comment return it"*), which is right for the interactive shell —
  its line editor has to echo comment lines. The splitter DOES mark them, with an empty
  delimiter, but `split_sql()` keeps only `std::get<0>(p)` and throws the delimiter away,
  so nothing survives the `mysqlsh.mysql.split_script` boundary. Every consumer in the
  shell has this (`mysqlshdk::mysql::execute_sql_script`, `dump_loader`) — the real fix is
  a `skipComments` option on `splitScript`, which would cost a shell release and a
  `MINIMUM_SHELL_VERSION` bump, so `db.execute_sql_script` filters on this side instead.

- **REST SQL / MRS session duplication**: MRS runs metadata DDL on a SECOND session so
  `USE x` etc. don't disturb the caller. `mrs_plugin/lib/general.py` originally did
  `if "shell.Object" in str(type(session))` -> no-arg `shell.open_session()` = duplicate the
  GLOBAL session (carries password). The MCP db layer uses `shell.open_session(connection_data)`
  and sets NO global session -> that path raised "An open session is required when
  duplicating sessions". A native shell session has NO `.connection_options` attr (GUI-only)
  — only `get_uri()` (no password), so `open_session(session.connection_options)` fails with
  "unknown attribute: connection_options" and URI-reopen can't carry credentials. Landed as
  ca47b8c8 (reuse caller's session) then 82e18c4c (original code restored + a different
  workaround until `session.clone()` exists). Alternative never taken:
  `shell.set_session(session)` in db.connect.

## Next steps

6. (Optional, open questions raised with the user and NOT yet answered)
   - `object_type` is a plain `str` + `_normalize_object_type`, not `Literal[...]`; a
     Literal would publish the 7 values as a JSON-schema enum to clients but would reject
     `"Table"`.
   - the flag columns of `get_object_details` (`not_null`, `is_primary`, ...) and
     `to_many` inside `reference_mapping` stay 1/0 as the user's SQL produces them, not
     JSON booleans.
   - `interval_value`/`interval_field` of an event are raw, not composed into a readable
     schedule.
