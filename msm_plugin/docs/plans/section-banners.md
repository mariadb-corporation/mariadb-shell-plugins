# MSM section banners: one grammar, three parsers, checked

Status: plan, 2026-11-26. Four PRs across msm_plugin, vscode-extension and
lsp-server. Nothing here is started. Independent of
[schema-catalog.md](schema-catalog.md), but PR A here should land before GA
for the same reason: it changes what msm accepts, and that is cheap now.

## Why

An MSM script is cut into sections by comment banners:

```text
-- #############################################################################
-- MSM Section 140: Non-idempotent Schema Objects
-- -----------------------------------------------------------------------------
-- CREATE TABLE statements and standard INSERTs.
-- #############################################################################
```

Comments are the right vehicle: a release or deployment script runs
unchanged through any client, the files diff well, and people read and edit
them. The marker family (`-- MSM Section`, `-- ### MSM-LOOP-START`,
`-- ### MSM-UPDATE-STEP-START`, `/*<msm:name>*/`) stays. What is missing is
a written grammar, and three parsers that agree on it:

| where | how it reads a banner |
| --- | --- |
| msm, `lib/management.py:44` `MSM_SECTIONS_REGEX` | `--\s+#+\s+--\s+MSM\s+Section\s+(\d+):\s*(.*?)$\s+.*?--\s+#+`, DOTALL, **not line-anchored**; one implementation, 18 call sites in 5 modules through `get_script_sections` |
| extension, `src/mrs/restScript.ts:778` `SECTION_HEADER` | line-anchored at column 0, every banner line starts with `--`, CRLF-aware |
| lsp-server, `text_util.h:190` `is_msm_marker` | a line starting with `-- MSM Section ` (cuts `leading_comments`, bounds statement placement) |

What msm's regex gets wrong today (each becomes a test in PR A):

1. **Phantom sections.** Not anchored to a line start, so a banner-shaped
   comment anywhere opens a section; and the match is made **inside string
   literals** too, so an `INSERT` of default content holding
   `-- ####\n-- MSM Section 5: x\n-- ####` splits the script there.
2. **Duplicate ids win silently.** `sections[id] = ...` overwrites; a script
   with two `140` banners loses the first body on the next
   `write_sections_to_file`, without a word.
3. **Order is not checked.** `150` before `140` in a development script is
   accepted and written back as found, while `generate_deployment_script`
   emits sections in template order; the two disagree silently.
4. **The lazy `.*?--\s+#+` ends the header at the first `-- #`**, so a
   description line `-- # note` cuts the banner short and the rest of the
   description becomes SQL of the section.
5. **CRLF**: `(.*?)$` keeps `\r` in the title; a CRLF script is read with
   `\r` inside every section body and written back with `\n` joins, mixed.

Only a tokenizer fixes 1 for string literals; column-0 anchoring fixes it for
every practical case, and the tokenizer is lsp-server's, which already
recognizes the marker (PR C, PR D).

## The grammar (normative; goes into `docs/script-format.md`)

A **banner** is:

```text
line 1:   "-- " followed by one or more "#", at column 0, nothing else but
          trailing blanks
line 2:   "-- MSM Section " <id> ": " <title>, at column 0
          <id>    = exactly three digits
          <title> = one line, trimmed; may be empty
lines 3+: zero or more description lines, each "--" at column 0 followed by
          a blank or the end of the line (a "-- ---…" rule line is one of them)
last:     "-- " followed by one or more "#", at column 0
```

- A banner starts a section that runs to the character before the next
  banner's first line, or to the end of the script. Text before the first
  banner is the pseudo-section `license`.
- Ids are unique within a script. In development scripts, version files
  and update scripts they are ascending; deployment scripts have their own
  order (the template's) and are not checked for it.
- Line endings: `\n` or `\r\n`; the banner's lines must use the script's
  own. msm reads either and **writes `\n`**, so a CRLF script becomes LF on
  its first write by msm (decision: msm does not maintain CRLF).
- A banner inside a string literal, or inside a routine body as a comment,
  is a banner to the regex readers (msm, extension) and not one to the
  tokenizer (lsp-server). The spec says: don't; PR D makes msm refuse such
  a script when a binary is at hand.
- The breakout header `sections.breakout_header` writes (`-- ----…` then
  `-- MSM Section <id>: <title>`) is **not a banner**: no `-- ###` lines. It
  is a marker line to lsp-server (so the header never becomes a statement's
  `leading_comments`) and nothing to msm's section reader. The spec names it
  as such.
- Id ranges, documented so new sections get an id that fits: `0xx` script
  kind, `1xx` creation (development, version, deployment), `2xx` update,
  `3xx` deployment-only machinery, `9xx` version and restoration. `250` was
  removed in 2026-10 and is never reused.
- The other markers, stated in the same document: `-- ### MSM-LOOP-START:
  UPDATABLE-VERSIONS[(indent=N)]` / `-END` (deployment template only),
  `-- ### MSM-UPDATE-STEP-START <from> <to>` / `-END` (generated deployment
  scripts), `/*<msm:name>*/` substitutions, `SOURCE '<path>'[a:b];`
  includes (already specified in lsp-server `docs/plans/source-includes.md`;
  link, don't copy).

### Format version

`msm.project.json` gets `"formatVersion": 1`. Readers take a missing one as
1; a project with a higher version than the running msm knows is refused
with "made by a newer MariaDB Shell". Written on creation and added by the
next `write_project_settings` of an existing project. The grammar above is
version 1; it is what every script msm ever wrote conforms to, so no script
changes. The field exists so a later grammar change can be told apart.
(Also used by [lock-in-cleanups.md](lock-in-cleanups.md) item 3, which
touches the same file.)

## PR A (msm_plugin): the grammar in msm

- `lib/sections.py` owns the banner: `SECTION_BANNER` (compiled, MULTILINE,
  line-anchored as the grammar says, `\r?` before every `\n`),
  `banner(section_id, title, description_lines) -> str` (the one writer),
  `parse_banner(match) -> (id, title)`. `management.MSM_SECTIONS_REGEX`
  becomes an alias for the transition and goes once no caller is left.
- `management.get_script_sections(script, ordered=False)`:
  - normalizes `\r\n` to `\n` first (one place; `_Project.load`,
    `prepare_release`, `set_section_sql_content` and the breakout readers
    all go through it);
  - raises `ValueError("MSM Section 140 appears twice, at lines 61 and
    212.")` on a duplicate id;
  - with `ordered=True` raises on a descending id (`"MSM Section 150 comes
    before 140"`); the development, version and update readers pass it,
    `generate_deployment_script` and the deployment template reader don't;
  - a title is the trimmed rest of line 2; a banner whose description has a
    `-- #…` line is read to the closing line, not the first `-- #`.
- The three banner writers use `sections.banner`: `complete_sections`
  (renders from the template, so it only needs the reader), `editing.py:222`
  and `:232` (the pending update script's 240 / 260 banners),
  `sections.py:110` stays `breakout_header` (not a banner).
- `write_sections_to_file` keeps joining with `\n` (the decision above).
- Tests, `tests/unit/test_sections.py` + a fixture folder
  `tests/fixtures/sections/`: one `.sql` per case with an expected `.json`
  (`[{id, title, body_start_line, body_end_line}]` or `{error: "..."}`):
  the template as is; CRLF copy of it; duplicate id; descending id in a
  dev script (error) and the deployment template (accepted); `-- # note`
  in a description; an indented `   -- MSM Section 150` (not a banner); a
  banner-shaped text inside a string literal (**matched** by the regex,
  recorded as such so PR D can flip the expectation); a title with trailing
  blanks; an empty title; a two-digit id (not a banner). The fixture folder
  is the contract the other two repos copy (PR B, PR C).
- `formatVersion`: `create_schema_project_folder` writes it;
  `get_project_settings` checks it; `write_project_settings` adds it when
  missing. One test per rule.
- Docs: `docs/script-format.md` (new, the grammar above);
  `PROJECT_CONTEXT.md` Section model bullet; the ai-plugins overview skill's
  description of a section header, re-run `sync-skills.sh`.

## PR B (vscode-extension): same grammar

- `src/mrs/restScript.ts` `SECTION_HEADER`: already line-anchored; align
  the description-line rule (`--` then blank or end), three-digit id, and
  add the `ordered` check to `sectionSpan`'s caller for development
  scripts (a duplicate 180 must not silently pick the first).
- Copy `tests/fixtures/sections/` into `src/test/fixtures/sections/` with a
  note of origin; `src/test/mrs/restScript.test.ts` runs every fixture.
- `.claude/context/mrs-msm.md` (theirs) points at `script-format.md`.

## PR C (lsp-server): sections in the outline

- `is_msm_marker`: column 0 and the three-digit id, as the grammar says;
  `schema_edit.cc`'s `is_banner_line` follows.
- Outline feature `sections` (`--features` lists it):
  `sections: [{id, title, range, body_range, source}]` per file the
  outline reads (the script and each include), found by the tokenizer over
  comment tokens only, so a banner-shaped string literal or a banner inside
  a routine body is **not** a section. `range` = the banner's first
  character to the next banner (or end of file), `body_range` = after the
  banner's last line. Duplicate ids are reported as a note
  (`kind: duplicate-section`, `applied: false`); order is not the
  server's business.
- Fixtures copied from PR A with the string-literal case expecting **no**
  section; `unittest/mariadblanguageserver/catalog_t.cc` or a new
  `sections_t.cc`; `docs/outline-format.md` documents the member.

## PR D (msm_plugin): the tokenizer as the check

msm keeps the regex (it must read sections without a binary: the CLI
`get`/`set` functions, release generation, deployment). With a binary at
hand it verifies the regex against the tokenizer, once per load:

- `lsp.outlines_sections(binary)` probe (feature `sections`).
- `_Project.load` and `prepare_release` compare the regex's `(id, start
  line)` list with the outline's for the script and every include; a
  difference (a banner the tokenizer does not see, or sees elsewhere) is
  refused: "Line 212 looks like an MSM Section banner but is inside a
  string literal; move it or change the text." Nothing is written.
- `set_section_sql_content` on a development script does the same when the
  binary is there, before writing.
- The string-literal fixture's expectation flips to the error. One test
  without a binary shows the regex result is still taken.

## Sequence

```text
PR A  msm: grammar, checks, fixtures, formatVersion    first
PR B  extension: align, fixtures                        any time after A
PR C  lsp-server: marker rule, outline sections         any time after A
PR D  msm: verify against the outline                   after C ships in a shell build
```

A alone removes every silent corruption in the list; B and C make the
three readers agree; D closes the string-literal hole.

## What to leave alone

- The section ids and titles; the templates' text (apart from nothing: the
  templates already conform).
- `MSM-LOOP-*`, `MSM-UPDATE-STEP-*`, `/*<msm:…>*/`: specified, not changed.
- `mysqlsh.mysql.split_script` for running scripts: unrelated to sections.
