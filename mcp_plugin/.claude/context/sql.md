# The sql group

`sql.check`, `sql.outline` and `sql.format`: SQL text checked, outlined and
formatted by the MariaDB SQL language server's binary, `mariadb-lsp`
(mariadb-corporation/lsp-server, `~/git/lsp-server`), without a database.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The SOURCE rules it
shares are in [db-tools.md](db-tools.md) (MSM SOURCE directives).

## Architecture / key decisions

All agreed with the user on 2026-10-19 (memory `sql-group-plan`):

- **Name `sql`**: the group is about SQL text, as `db` is about live servers;
  not `lsp` (an implementation detail an agent has no use for).
- **Only `check`, `outline`, `format`.** Not `complete` (cursor-driven),
  `split` (`db.execute_sql_script` splits), `tokens`/`tree` (parser
  debugging, noisy), `connections` (= `db.list_connections`), `lsp`.
- **No binary, no group**: `register_sql_tools` returns without registering
  (and `general.log_event`s why), like the migrator group. Found by
  `find_lsp()`: `config.get_lsp_path()` (`mcp setup --lspPath=PATH`, the
  binary or its folder, `""` clears; settings key `lspPath`), else
  `shutil.which`, else `general.get_bin_home()` (`~/.local/bin`) - the VS
  Code extension's order. A candidate counts only if `--version` answers
  `mariadb-lsp <version>`. Read once, when the server starts. No server
  instructions rule: the instructions builder does not know whether the
  binary was found, and must not name tools that were not registered.
- **Offline only**: no `--connection` (the binary reads the shell's secret
  store by URI; multi-tenant per-user groups are unverified). A later phase
  may take a `connection_id`.
- **Input**: `sql` XOR `file_path` (allowed-path check), `source_dir` only
  with `sql` (allowed, a folder) - `db_functions.read_sql_input`, the SAME
  function `db.execute_sql_script` uses - then a 10 MB cap, 60 s timeout. A `file_path` is
  handed to the binary as its FILE argument, so it takes SOURCE paths
  relative to the file's folder itself; inline SQL goes on stdin with
  `--source-dir` (lsp-server `wip/source-dir`, `8e98d91`, added for this).
- **SOURCE: expanded HERE, the binary reads nothing** (2026-10-19, after
  `/security-review`; replaces decision 3a, where the binary expanded them
  after a pre-check). The pre-check found directives with msm's regex (a
  line starts after `\n`), the binary with its own scanner (also after a
  lone `\r`, `lsp-server .../source_include.cc` `find_source_directives`):
  `COMMENT '\rSOURCE '/home/u/.ssh/id_rsa'[0:2000];\r'` was never checked,
  and `sql.outline` returned the file in the comment (reproduced). Now
  `db_functions.read_sql_input` normalizes `\r\n`/`\r` to `\n` (inline
  text; a file is read in text mode, so is already), `_prepare` expands with
  `db_functions.expand_sources(..., consequence="Nothing was checked.")`
  (msm's `expand_source_directives` + the checked `_read_allowed_include`,
  as `db.execute_sql_script`), and every check/outline call passes
  `--no-source` with the text on stdin - one parser decides what a
  directive is. A missing include, a bad slice, a nested SOURCE, a relative
  path without a folder, or no msm_plugin now FAIL the call (tool error),
  like the db tool. `--source-dir` is no longer used (`file_path` too goes
  on stdin, expanded). Bonus: check now checks included statements.
- **Positions** become 1-based lines and character columns
  (`_position_converter`): the binary's LSP position (0-based, UTF-16) in
  the expanded text -> its offset -> `source.locate`, which now also returns
  the column in the included file (`Segment.first_column` for a slice
  starting mid-line, `Segment.indent` taken off indented lines). A position
  in an include gets `file`; outline `includes` is built from the segments
  (`{file, line of the directive}`).
- **check**: exit 1 = errors found, a normal answer. Returns `{valid,
  statements, errors, notes}` (`offset`/`length` dropped: byte units).

## Files that matter

- `lib/sql_functions.py`, `tests/unit/test_sql.py` (a Python stand-in
  binary that records its argv and stdin; real-binary tests skip without
  one, the `--source-dir` one also without that option).
- `lib/config.py` `get_lsp_path`/`set_lsp_path`; `lib/setup_cli.py`
  `lsp_path` (a PRESENCE option, so `""` clears; it reports what
  `find_lsp()` then finds, or that none answers).

## Gotchas

- An include's `file` is the RESOLVED path (`_read_allowed_include`
  realpaths it): on macOS `/var/...` comes back as `/private/var/...`.
- Not in `MULTI_TENANT_FUNCTION_GROUPS` (no `mcp:sql` scope).
- Never let the binary resolve an include again without checking what it
  READ: two scanners disagreeing on one byte was enough for an arbitrary
  file read. The lsp-server takes a lone `\r` as a line break ON PURPOSE
  (user): msm reads files in text mode, where it already is one, and VS
  Code counts it as a line end too; the gap was inline text, now
  normalized in `read_sql_input`.
