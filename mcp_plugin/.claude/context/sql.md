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
  with `sql` (allowed, a folder), 10 MB cap, 60 s timeout. A `file_path` is
  handed to the binary as its FILE argument, so it takes SOURCE paths
  relative to the file's folder itself; inline SQL goes on stdin with
  `--source-dir` (lsp-server `wip/source-dir`, `8e98d91`, added for this).
- **SOURCE (decision 3a)**: the binary expands directives itself, reading
  the included files, so before it runs every directive
  (`msm_plugin.lib.source.directives`) is resolved
  (`resolve_include_path` + `realpath`) and must pass `is_path_allowed`;
  only a not-allowed one is refused here - a missing file or a bad slice
  the binary reports as an error of the script. Without msm_plugin the
  binary gets `--no-source`.
- **Positions** become 1-based lines and character columns
  (`_lsp_position`: the binary's json is LSP, 0-based UTF-16). An outline
  object from an include has `source` = its index in `includes` and
  positions IN THAT FILE (lsp-server docs/outline-format.md): `_with_positions`
  converts them with that file's lines and replaces `source` by the path.
- **check**: exit 1 = errors found, a normal answer. Returns `{valid,
  statements, errors, notes}` (`offset`/`length` dropped: byte units).
  It does NOT check an include's statements (only that it can be read).

## Files that matter

- `lib/sql_functions.py`, `tests/unit/test_sql.py` (a Python stand-in
  binary that records its argv and stdin; real-binary tests skip without
  one, the `--source-dir` one also without that option).
- `lib/config.py` `get_lsp_path`/`set_lsp_path`; `lib/setup_cli.py`
  `lsp_path` (a PRESENCE option, so `""` clears).

## Gotchas

- The binary reports include paths absolute and normalized, links NOT
  followed: compare with `os.path.normpath`, not `realpath` (macOS `/var`).
- Not in `MULTI_TENANT_FUNCTION_GROUPS` (no `mcp:sql` scope).
