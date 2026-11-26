# Before GA: the things that are cheap to change now and costly after

Status: plan, 2026-11-26. Five items, each its own PR unless noted; all in
msm_plugin except where said. Ranked by (cost after GA) / (cost now). The
two larger designs are separate plans: [schema-catalog.md](schema-catalog.md)
and [section-banners.md](section-banners.md).

## 1. The collation in Section 110

`templates/scripts/schema_next.sql:32`, `schema_a.b.c.sql:32`,
`schema_deployment_a.b.c.sql:32`:

```sql
CREATE SCHEMA IF NOT EXISTS ${schema_identifier}
    DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

`utf8mb4_0900_ai_ci` is MySQL's; MariaDB takes it as an alias of
`utf8mb4_uca1400_ai_ci` since 11.4.5 and refuses it before. Every new
project freezes it into its first release.

- Decide the server floor the shell supports (the shell's own compatibility
  statement). At or above 10.10: write `utf8mb4_uca1400_ai_ci`. Below:
  drop the `COLLATE` clause and take the server's utf8mb4 default
  (`utf8mb4_general_ci` before 11.4... note that the default changes with
  the server version, so the explicit `uca1400` is the better choice where
  the floor allows it).
- Existing projects: Section 110 of a **development** script is copied into
  each version file by `prepare_release`, so an old dev script keeps the
  old collation release after release. Do not migrate silently (a collation
  change on an existing deployment is a data change). Instead:
  `msm.info()` / `prepare_release` warn once when the dev script's 110
  names `utf8mb4_0900_ai_ci`, pointing at the one-line edit. The MRS
  metadata project (`mariadb-shell/mysqlshdk/libs/mrs/db_schema/…`) has it
  in `_next.sql:53` and in the released 5.0.0; change the dev script before
  5.1.0, and the released file stays as it is.
- Tests: the sandbox deploy tests already cover 12.3; add one that greps
  every template for `0900` (fails) so it does not come back.
- Docs: the collation line in the schema-management-create skill and in
  `mariadb-shell/docs-ref` (the MSM pages document issue 4 as it behaves
  today; update them).

## 2. The section numbering, one conscious pass

The ids are the public API: the ai-plugins skills, the extension's REST and
diagram code (`REST_SECTION = "180"`, 160), the docs and every user's
script name them. 250 was removed already. One review, written down in
`docs/script-format.md` (from [section-banners.md](section-banners.md)),
answering:

- 160 (generated, "do not edit by hand") sits between the hand-written 150
  and 170. After schema-catalog it is small and quiet; it must run after 140
  (the VIEW is not table-dependent, but a deployment that fails in 150 should
  not leave a catalog that claims the new version). Keep.
- Reserved ranges per script kind (`0xx`, `1xx`, `2xx`, `3xx`, `9xx`) and the
  rule that an id is never reused, so a later section (e.g. data migration
  helpers between 240 and 260) has a place. Write the ranges, add nothing
  now.
- 280 (update script) ↔ 380 (deployment) pairs, 260 ↔ 360, 270 ↔ 370: the
  pairing rule (`2xx` of the update lands in `3xx` of the deployment) is
  implicit in `generate_deployment_script`; state it.
- Outcome expected: no renumbering. The PR is the document plus a test that
  the templates' ids are within their ranges, so a later template edit
  cannot drift.

## 3. `msm.project.json`: drop the mirrors, add the version

**Done on `wip/msm-project-format-version`.** As built: every writer goes
through `management.project_settings_text` (`write_project_settings`, and
`editing.online_alter_settings_text`, which `edit_schema` writes through its
own all-or-nothing `write_texts`): `formatVersion` first, `copyrights`
derived for a single holder file, `copyrightHolder` / `yearOfCreation`
dropped. `get_project_settings` checks the version (`check_project_format`:
missing = 1, a later one "made by a newer MariaDB Shell", a non-integer
refused) and hands readers the list alone, so `get_project_information`
answers the same for a migrated and an un-migrated project (a change from
the plan below, which kept the mirror in that answer). `set_project_settings`
refuses `formatVersion` and the two legacy keys. The extension reads
`copyrightHolder` only as a fallback when `copyrights` is empty
(`msmCommands.ts` `projectContext`), which msm never answers, so it needs no
change.

Written today by `create_schema_project_folder` (`lib/management.py:1030`)
and `_checked_settings` (`:2130`): `copyrightHolder` and `yearOfCreation`
mirror `copyrights[0]`. They were kept "so tools written against the earlier
single holder format keep reading the project"; no such tool exists outside
this repo, and the MRS project shows the cost: its `copyrightHolder` is
Oracle's while the tracking holder is MariaDB's. A mirror is a forever cost
after GA.

- Stop **writing** the mirrors: creation, `_checked_settings`, and the
  first-year-bump migration (`get_project_copyrights`' path that writes
  the list). `write_project_settings` of an existing project drops them.
- Keep **reading** a legacy `copyrightHolder` / `yearOfCreation` into a one
  entry list when `copyrights` is missing (`get_project_copyrights:444`
  path), so projects from before keep working.
- `set_project_settings` with a mirror key: still refused, same message.
- `get_project_information` (mcp `msm.get_project_information`): returns the
  file as read, so an un-migrated project still shows the mirror until its
  next write; the tool's docstring says `copyrights` is the list to read.
  Check the extension (`src/mcp/msmApi.ts`, the MSM project tree) and the
  skills for a reader of `copyrightHolder`; there should be none.
- Add `"formatVersion": 1` in the same PR (specified in
  [section-banners.md](section-banners.md), "Format version"): creation
  writes it, readers take a missing one as 1, a higher one is refused.
- Tests: `test_management.py` project-settings cases (`copyrights` written,
  no mirrors; legacy file read; `formatVersion` rules); the fixture projects
  in `tests/` that carry the mirrors get them removed; a test that
  `write_project_settings` on a legacy file drops the mirrors and adds the
  version.
- Update the MRS project's `msm.project.json` by hand in mariadb-shell
  (it is checked in there), in the PR that bumps it to 5.1.0.

## 4. The small known issues, closed in one PR

Known issue 3 (`check_mysql_identifier` inverted) is **already fixed**:
`lib/management.py:392` takes `must_be_usable_when_unquoted`, the callers
pass `not allow_special_chars`, and `test_management.py:252` covers it.
Mark it fixed in `PROJECT_CONTEXT.md`. The rest, each verified against the
code on 2026-11-26 before being touched:

| # | issue | fix |
| --- | --- | --- |
| 5 | `prepare_release` non-interactive without `next_version` builds a `ValueError` and does not raise it | `raise`; test |
| 6 | `generateDeploymentScript` prompts for `version` without the `get_interactive_default()` check | add the check; test with `useWizards=False` |
| 7 | a `get` function outside a project dies with `StopIteration: User-defined function threw an exception` | one `ValueError("… is not an MSM project folder")` from the project locator; test |
| 9 | `createNewProjectFolder` error for a missing `target_path` says both `schema_name` and `schema_project_path` are needed | name the missing argument |
| 10 | an empty `plugin_data/msm_plugin/backups/` stays after a backup; `util.dump_schemas` prints progress despite `showProgress: False`; `Charset id '33'` warnings | remove the empty dir after the dump is deleted; the progress and charset lines are the shell's (`util.dump_schemas`), file them there and note it |
| 11 | `lib/core.py` defines `MsmDbExec` twice (`:436`, `:654`); `get_db_schema_version_int` calls `get_db_schema_version` without `schema_name` | delete the first class; fix or delete the unused function |
| 13 | `msm.get.deploymentScriptVersions` docstring says "Adds a new database schema release" (`management.py:665`, and `:411`); `package.json` has `"publisher": "Oracle"` and `run-pytest` carries an mrs test name | fix the three texts |
| 8 | the `local_infile` error text said `SET PERSIST GLOBAL` (MySQL) | check `management.py` around `:2800`; if gone, mark fixed |

Docs: the `mariadb-shell/docs-ref` MSM pages describe issues 7 and 8 as they
behave; update them in the same change. Next step 5 of the context (add
`NO_AUTO_CREATE_USER` to the MRS project's dev script) is done:
`_next.sql:44` has it.

## 5. The mcp tool and parameter names, one sweep

`mcp_plugin/README.md:290-330` lists 41 `msm.*` tools. They are the names
agents learn from the skills and the extension calls from `msmApi.ts`; a
rename after GA breaks third-party prompts and scripts, a rename now breaks
two in-house callers that ship together. No aliases: a tool listed twice
confuses an LLM more than a rename.

The naming rule to review against: `verb_object[_qualifier]`; `get_X` and
`set_X` mirror each other exactly; `list_*` answers a list; the object acted
on is the first parameter; `schema_project_path` is the project everywhere
(optional, the working directory by default) and `target_path` only where a
new folder is made.

Candidates seen in the table (decide each; expected outcome in brackets):

- `msm.get_sql_content_from_section` vs `msm.set_section_sql_content`
  [rename the getter to `get_section_sql_content`].
- `msm.create_project` (wrapper `create_new_project_folder`) vs
  `import_workbench_model` / `import_live_schema` which also make projects
  [keep; the verbs say what the source is].
- `msm.update_schema_definition` makes the snapshot; after schema-catalog
  the row is a "catalog" and the file a "schema definition" [keep: the
  file is what it updates].
- `msm.get_released_versions`, `get_last_released_version`,
  `get_last_deployment_version`, `get_deployment_script_versions`
  [keep; "deployment version" is the version a deployment script deploys,
  say so in the docstrings].
- `msm.rename_schema` vs `msm.rename_schema_diagram` [keep].
- `msm.online_alter_table` (a task) vs `msm.deploy_schema` [keep].
- Parameters: `lsp_path` is exposed on some tools and not others (an agent
  has no business choosing a binary) [remove from every mcp tool; the
  server finds it]; `connection_id` first on the live tools [already];
  `diagram` vs `name` for a diagram's name across the diagram tools
  [one name, `diagram`].

The PR: the decided renames swept through `mcp_plugin/lib/msm_functions.py`,
`mcp_plugin/README.md`, its tests, `vscode-extension/src/mcp/msmApi.ts` and
its tests, the ai-plugins skills (`sync-skills.sh`) and
`dev-plugin-tests/test_e2e_msm_claude.py`. Land it **before** the skills are
re-synced for schema-catalog PR 1, so the skills change once.

## Sequence

```text
1  collation            any time; smallest
4  small issues         any time
3  project file         before section-banners PR A, or as one PR with it
5  mcp names            before schema-catalog PR 1's skill sync
2  numbering document   with section-banners PR A (same document)
```
