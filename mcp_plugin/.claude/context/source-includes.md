# MSM SOURCE includes

How `db.execute_sql_script` and the `sql` tools ([sql.md](sql.md)) expand MSM
`SOURCE '<path>'[<start>:<end>];` directives: the shared implementation, the
path checks and the result placement. Moved out of
[db-tools.md](db-tools.md) on 2026-10-19 when that file passed 400 lines.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Architecture / key decisions

- **MSM SOURCE directives are expanded** (2026-10-19,
  PR #61, based on PR #60's SOURCE rules). One
  implementation: `msm_plugin/lib/source.py` (no shell imports) holds
  `MSM_SECTION_SOURCE_REGEX`, `get_source_slice[_offset]`,
  `resolve_include_path` (what a directive's path NAMES - isabs, join to
  base_dir, normpath; a relative path without base_dir is an error - in one
  place, so the readers only check and read),
  `expand_source_directives(script, base_dir, read, allow_nested)`, which
  returns the expanded text AND its `Segment`s, and `locate(expanded,
  segments, offset)` -> (offset in the script as given, file, line). msm's
  `substitute_source_statements_with_content` is a wrapper of it
  (`allow_nested=True`, the default `read_include`); management.py imports
  only `expand_source_directives`. mcp imports `msm_plugin.lib.source` lazily in `_expand_sources`,
  as the msm tools import msm: without msm_plugin a script with a directive
  is refused, any other runs.
  - Line breaks: `read_sql_input` makes inline `\r\n` and lone `\r` into
    `\n` (user's decision, 2026-10-19), as a file read in text mode already
    is, so a directive after a lone `\r` is expanded; a `\r` in a string
    literal of an inline script arrives as `\n`, as from a file.
  - Expanded BEFORE `split_script` (a directive can be in a routine body)
    and before the session is used. Every include is resolved, checked and
    read up front: relative to the file_path's folder, or to the new
    `source_dir` for an inline script (refused with file_path, refused if
    not allowed or not a folder; no source_dir = only absolute paths). mcp's
    reader `_read_allowed_include(path, user)` does `os.path.realpath`, then
    `config.is_path_allowed` (which realpaths too), and reads the RESOLVED
    path, so `..` and symlinks
    cannot leave the allowed paths. `allow_nested=False` refuses a SOURCE
    line in the selected part (msm leaves it, to fail halfway). Refusals are
    tool errors like an unreadable file_path, naming the directive's line and
    the script or file, the refusal worded by `general.path_not_allowed`
    (also `require_allowed_path`'s and file_path's, so a tenant is told to
    ask the administrator): `SOURCE 'x' on line 3 of /p/dev.sql: ... Nothing
    was run.`
  - **statement_index stays the caller's** (agreed with the user): the
    extension splits the editor text itself and pairs by it
    (vscode-extension `src/sql/executionService.ts`). `_place_statements`
    splits the unexpanded script too, finds each piece's offset (pieces are
    substrings, found in order), maps every expanded statement with
    `source.locate`: a statement from an include takes the directive's index (an
    include's segment is placed at the SOURCE keyword, where the splitter's
    piece starts, not at the indentation), so several entries can share one.
    Such entries carry `source: {file (resolved), line}`; others have no new
    key.
  - Tests: the SOURCE block at the end of `test_db_script.py` (stub
    session, real splitter; files written with `helpers.write_file`), `test_a_development_script_runs_with_its_section_files`
    in `test_db_sql.py` (sandbox: tables from one section, a procedure body
    from `[ln2:ln3]` of another, then CALL).

