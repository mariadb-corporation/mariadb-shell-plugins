# Session and branch history

The session-by-session record and the commit history behind it. Read it to
find out why something is the way it is, or what a branch name in an older
commit message refers to — not to find out what is true today.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md), whose Git state section
is the current one.

## Current state

- **THIS SESSION: the sandbox server-version feature, on a NEW branch
  `wip/sandbox-binaries` off `main` (NOT off `wip/AIPL-21`, which was merged as PR #19 and
  deleted).** Five commits, all green, all described under Git state. The shape of it:
  1. Verified the (then untracked) `lib/sandbox_server_versions.json` against a
     `lib/SERVER_SHA256SUMS` the user had beside it — 12/12 matched — and fixed one
     misindented brace. That checksum file is GONE from the tree now; the user removed it.
  2. Built `lib/sandbox_servers.py` + `sandbox.list_available_versions` + `server_version`
     on `sandbox.deploy` (see Architecture). 90 new tests, none touching the network.
  3. Added the bare-`major` form.
  4. Re-pinned all 12 checksums after the release assets were re-uploaded mid-session.
  5. Fixed the deploy message's shutdown advice after the Windows run found it wrong.

- **PROVEN on real downloads, not just stubs.** macOS: `install("11.8.9")` fetched the
  26MB package, checksum passed, `bin/mariadbd` came out 0755, `--version` said 11.8.9,
  xattrs clean, and a sandbox deployed on it answered `SELECT VERSION()` with
  `11.8.9-MariaDB` while the PATH server was 12.3.2. That flow is now the `e2e` test.

- **PROVEN on Windows 11 ARM64, through codex over a real MCP server** (VM at
  `ssh dev@192.168.10.103`, cmd.exe default shell, mariadb-shell 26.9.1 at
  `C:\Users\dev\AppData\Local\Programs\mariadb-shell\26.9.1`). The plugin there was
  REPLACED with this branch's build (`git archive HEAD:mcp_plugin` -> scp -> `tar -xf`);
  **the original 26.9.0 is backed up at `C:\Users\dev\mcp_plugin.backup-26.9.0`** and
  ours is what is installed. Codex confirmed, cold (the download dir was cleared first):
  `list_available_versions()` -> `11.8.9, 12.3.3`; `series="11"` -> `11.8.9`;
  `series="nonsense"` -> the full refusal message intact; `deploy(server_version="11.8")`
  downloaded the win32-arm64 package and started it; **`sandbox.version` -> `11.8.9`**,
  which can ONLY have come from the download since that machine has no server on its PATH
  at all; `sandbox.kill` worked. `platform_key()` -> `win32-arm64`, root ->
  `C:\Users\dev\AppData\Local\Programs\mariadb-sandbox-server`, and
  `find_server_binary` picked `bin\mariadbd.exe` over the neighbouring `mariadbd-safe`.
  Left behind on the VM, deliberately: the downloaded `11.8.9` in the standard location.
  Codex was configured with `-c` overrides, so `~/.codex/config.toml` there is UNTOUCHED.

- **PREVIOUS SESSION (short): AIPL-21 was already built and committed; the session verified it,
  re-measured it, and got blocked on the push.** In order: read this file, found it STALE on
  AIPL-21 (it described the AIPL-16 / `wip/MCP-CONN-HANDLING` era and never mentioned the
  migrator at all), reconstructed the real status from `git log` + the tree, ran the full
  suite on the REBASED base (**92 pass, 96%**), then attempted
  `git push --force-with-lease` — **DENIED by the Claude Code auto-mode permission
  classifier**, not by a lease failure and not by a test failure. **The user then ran that
  push MANUALLY and it landed**: `origin/wip/AIPL-21` is now `0137ea80`, divergence 0/0, and
  the pre-rebase twin `a5d0a177` is no longer reachable from the remote (all three verified,
  not assumed). The user's standing decision: **hold the PR until more functionality is
  implemented.** NO code changed at that point; only this file.

- **THEN, same session: the setup was REFACTORED and the Windows gate added** (see
  Architecture for the shape and the reasoning). `lib/setup.py` 623 -> 254 lines; new
  `lib/setup_migrator.py` (390) and `lib/setup_prompts.py` (81). **95 pass, 96% TOTAL**
  (1083 stmts / 48 missed, clean `.coverage`): lib/setup_migrator **99**, lib/setup 87,
  lib/setup_prompts 81. The missed-line count did not move (20 before, 20 across the three
  modules after) — setup.py's percentage fell from 92 only because the denominator
  shrank, NOT because coverage was lost. **THREE revert probes, each against its own
  layer, because the first one alone was misleading**:
  1. `is_supported()` -> `return True`: only `test_the_tooling_is_not_offered_on_windows`
     failed. The menu tests patch `is_supported` directly, so they CANNOT fail on this and
     it would have been wrong to claim they pin the predicate.
  2. `_menu_entries` appends the entry unconditionally:
     `test_the_windows_menu_leaves_the_entry_out_and_renumbers` failed (`assert 5 == 4`).
  3. `print_status()` un-gated in `_menu`:
     `test_setup_menu_hides_the_migrator_where_it_is_unsupported` failed on the
     status line leaking into the output. Probe 3's own output also confirmed the Windows
     menu renders as `1..4` + `5. Finish`, with no migration line.
  **This work is COMMITTED** — see Git state.

- **THEN the install location was MOVED, same session**, on the user's instruction: from
  `<plugin data>/mariadb-migrator` to `~/.local/share/mariadb-migrator/<version>` (see
  Architecture for the six decisions). Two of them were the user's answers to a direct
  question rather than mine: **remove clears the whole tree**, and **the
  `.migrator-version` file is dropped**. **97 pass, 96%** (1089 stmts / 48 missed, clean
  `.coverage`); lib/setup_migrator 99, lib/general 98. THREE more revert probes, one per
  new behaviour:
  1. `get_migrator_path()` -> the root (release dropped from the path): **15 tests failed**.
  2. `remove()` scoped to the configured release only: 2 failed, including the removal test
     and the menu-flow test.
  3. `get_data_home()` hardcoded to `~/.local/share`: the XDG test failed on
     `'/Users/mzinner/.local/share' == '/opt/somewhere/share'`.

- **THEN, same session: the python3 check was REVERTED and the install became
  self-contained**, on the user's instruction after the feasibility work below. `mcp.setup`
  now builds the venv and installs dependencies into the downloaded folder, and installs a
  wrapper at `~/.local/bin/mariadb-migrator` that overwrites an older one of ours.
  **111 pass, 96%** (1168 stmts / 48 missed, clean `.coverage`), lib/setup_migrator 99.
  **VERIFIED FOR REAL, not just in tests**: `printf '5\ny\n5\ny\n6\n' | mariadb-shell
  --py -e "mcp.setup()"` removed and reinstalled the tooling with a live GitHub fetch and a
  live PyPI install (typer 0.12.3, click 8.1.7, rich 13.7.1, PyYAML 6.0.2), then
  `mariadb-migrator --help` ran **by name on a PATH containing NO python at all** (a
  scratch dir of 1188 symlinks to /usr/bin, /bin and /usr/sbin with every `python*`
  excluded; `command -v python3` -> NONE). The orchestrator itself
  (`python3 -m orchestrator.migrationctl --help`) also ran in that environment, resolving
  python3 to `<install>/.venv/bin/python3`.

- **THEN: CI failed on Linux and the cause was a real bug** (run 33866466116, PR #19).
  3 tests failed, all `subprocess.CalledProcessError ... '-m', 'ensurepip' ... exit status
  127` out of `venv.EnvBuilder`. Diagnosis and fix: `symlinks=True` — see the
  EnvBuilder bullet under Architecture. The local install was rebuilt with
  `--removeMigrator --installMigrator` so its venv interpreter is a symlink too.

- **THEN, same session: `migration.*` was renamed to `migrator.*`** (user's instruction),
  and the rename carried further than asked because a partial one would have been
  incoherent:
  - `lib/setup_migration.py` -> **`lib/setup_migrator.py`** (asked for).
  - The four MCP tools `migration.*` -> **`migrator.*`** (asked for).
  - **`FUNCTION_GROUP_MIGRATION = "migration"` -> `FUNCTION_GROUP_MIGRATOR = "migrator"`**
    — REQUIRED, not cosmetic: every other group name mirrors its tool prefix
    (`db.*`/`db`), so `--functionGroups=migration` enabling `migrator.*` tools would have
    been incoherent. **This is a user-visible CLI change**: `--functionGroups=migration`
    is now rejected.
  - `register_migration_tools` -> `register_migrator_tools` (matches `register_db_tools`).
  - `tests/unit/test_migration_tools.py` -> `test_migrator_tools.py`, and
    `tests/unit/test_migrator.py` -> **`test_setup_migrator.py`** so each test file names
    the module it tests, as `test_setup_cli.py` already did — otherwise
    `test_migrator.py` and `test_migrator_tools.py` would have sat side by side testing
    different modules.
  - The `--show`/`--json` key `"migration_tooling"` -> **`"migrator"`**.
  - Fixed along the way: `mcp.startServer`'s docstring still listed the groups as
    `"db", "sandbox" and "msm"`, so the CLI help for `--functionGroups` had never
    mentioned the new group at all. A pre-existing bug the rename surfaced.
  Verified against the real shell: 31 tools, all four `migrator.*` present, no
  `migration.*` left, and `--functionGroups=migration` now refused.

- **THEN, same session: every mcp.setup item became a CLI option** (`lib/setup_cli.py`,
  514 lines; see Architecture). **COMMITTED as `2f335a6c`.** **192 pass, 97% TOTAL** (1530 stmts / 50 missed, clean
  `.coverage`); `lib/setup_cli.py` **100%**. New `tests/unit/test_setup_cli.py` (44
  tests). SIX revert probes, all discriminating: `has_options` narrowed to known names,
  password sources made non-exclusive, `--noVerify` ignored, URI-password accepted,
  additions before deletions, `--nonInteractive` still prompting.
  Also verified LIVE against the real config, with cleanup afterwards: `--passwordEnv`,
  `--passwordStdin`, `--password`, `--deleteConnections`, `--show`, `--show --json`
  (piped through a JSON parser), and every combination guard.

- **THEN, same session: the four migrator MCP tools were added**
  (`lib/migrator_functions.py`, 599 lines; see Architecture). **141 pass, 96% TOTAL**
  (1325 stmts / 48 missed, clean `.coverage`), and **`lib/migrator_functions.py` is at
  100%**. New test file `tests/unit/test_migrator_tools.py` (30 tests). Verified against
  the REAL install: `server.build_mcp_server(DEFAULT_FUNCTION_GROUPS)` lists **31 tools**
  including all four `migrator.*`. FOUR revert probes, one per property that matters:
  1. confinement removed (`configured_uri = uri`) ->
     `test_an_unconfigured_host_yields_no_password` failed with
     `KeyError: 'admin@some-host-nobody-allowed:3306'`.
  2. passwords added to the outcome dict -> the no-secret-in-output assertion failed,
     printing the leak.
  3. `stdin=DEVNULL` removed -> the invocation test failed with `KeyError: 'stdin'`.
  4. registration gate removed -> `test_no_tools_are_registered_without_an_install` failed.

- **THEN, same session: `set_config` was tightened to REFUSE unconfigured servers**
  (user's instruction). `validate_connections()` added and called from both `write_config`
  and `_run_orchestrator`; `set_config` now also returns `connections` (account key ->
  configured URI). **148 pass, 96%**, lib/migrator_functions still **100%**. THREE more
  revert probes:
  1. write-time validation removed -> 6 tests failed, incl.
     `test_a_merge_cannot_assemble_a_forbidden_connection` ("DID NOT RAISE").
  2. run-time re-validation removed ->
     `test_revoking_a_connection_stops_a_run_already_configured` failed.
  3. host-without-account check removed ->
     `test_a_host_named_without_an_account_is_refused` failed.
  **COMMITTED** — see Git state. Still to come per the user: a SKILL covering how to
  write `config/migration.yaml`.

- **THIS SESSION (2026-09-04, a NEW one): PR #19 was checked out and the end-to-end
  migration test was built, then made opt-in.** In order: `gh pr checkout 19` ->
  `wip/AIPL-21` at `90fb6405`; read this file; then built
  `tests/unit/test_migration_e2e.py` (the user's spec: two sandboxes, the source from
  `/usr/local/mysql/bin/mysqld`, connections via the setup CLI, schema via the db tools,
  install via the CLI, `set_config` per `config/migration.yaml.example`, `run`, verify).
  **The feature works end to end against real servers**: MySQL 26.7.0 -> MariaDB 12.3.2,
  all 7 orchestrator steps DONE, exit 0, in ~3s of actual migration and ~20s of test.
  **Re-verified on MCP SDK 2.1.1** after the `tool_registrar` revert (PASSED, 23.4s),
  reusing the `v1.4.0-beta` install already on the machine — so that run did NOT exercise
  the live GitHub/PyPI download path, by the fixture's design.
  - **Built by PROBING first, not by writing the test and iterating on it.** Five
    hand-driven runs in the scratchpad found five separate blockers (all in Gotchas), each
    identified from the tooling's own `report.json` and `run.log` rather than guessed at.
    Do that again: a failing 20s pytest run tells you far less than the report does.
  - **`pv` was installed with Homebrew** to get past the last blocker. It is a documented
    (optional) dependency of the tooling; it was flagged to the user as a change to their
    machine. **It went missing again and had to be reinstalled** (`brew install pv`, 1.11.0,
    pulling `json-c`) before the e2e test would do anything but skip — so check `pv` is
    actually there before concluding anything from an `--e2e` run, and read the SKIP line:
    a skipped e2e test still exits 0 and still prints a coverage table.
  - **PROVEN to discriminate, in BOTH halves, and the two probes are different tests**:
    pointing `SRC_DBS` at another schema fails the RUN assertion (`exited 3`), while
    removing the trigger from the source schema fails the TARGET assertion (`triggers on
    the target: []`) with the run still green. The first probe alone would not have shown
    the verification works — it never reaches it, because the run-level assertions come
    first. Same lesson as S7 and the Windows gate: probe the layer each assertion covers.
  - A third discriminating failure came for free: an earlier run left a `state.json` and
    the next run reported every step `SKIPPED` with exit 0. That is now prevented (see
    Gotchas) AND the report-status assertion catches it.
  - **THEN, on the user's instruction, it was made opt-in** (see Architecture):
    192 pass + 1 skipped by default, 193 pass with `--e2e`, both measured. Also documented
    in the README's new "End-to-end tests" section.

- **THEN (2026-09-07): the FIRST REVIEW COMMENTS on PR #19 were addressed** — three
  inline comments from `mariadb-ReneRamirez`, all correct, all accepted. They were on
  PR #19 (mariadb-shell-plugins), NOT the ai-plugins PR #11 that had just been opened;
  `gh pr view --json comments` showed nothing, they were on
  `gh api repos/.../pulls/19/comments` — check BOTH endpoints before reporting "no
  comments".
  1. **`setup_prompts.yes_no` and `select_index` reimplemented what the shell's own
     prompts do.** Fixed with `confirm` / `select` (see Architecture), and `_menu` was
     converted too on the user's instruction — the biggest instance of the same
     anti-pattern, and it did not even go through `select_index`.
  2. **The migrator tools were coupled to shell-plugin conventions.** They are plain
     tool functions, so they now raise `ToolError` and register with plain
     `server.tool` (see Architecture). `import mysqlsh` is gone from the module.
  3. Two open points were put to the user, who chose: **catch the secret-store
     `RuntimeError` and re-raise it as a `ToolError`** (rather than let it go generic),
     and **uniform lazy dispatch for all four registrars** (rather than a shim for
     migrator alone).
  4. **THEN, on the user's request: a stdio-level error test and a `tool_registrar`
     docstring fix** — and the docstring fix turned out to be more than wording. Writing
     the test surfaced that **SDK 2.0 appends a tool's message rather than replacing
     it**, so the registrar looked redundant and its stated premise looked false, and it
     was deleted. **2.1.0 then reinstated the masking and broke CI, so that conclusion
     held only for 2.0.0 and the module is back** — see the SDK-error gotcha, which is
     the one to read; the paragraph below records what was believed at the time. The
     test was rewritten to assert what is
     actually true — the refusal arrives as `is_error=True` carrying the tool's own
     sentence — and to say outright that it canNOT discriminate exception types,
     because on 2.0.0 nothing did. `test_a_refusal_reaches_the_client_with_its_own_words`
     is the only test here that makes a real round trip; it SKIPS when the tooling is
     not installed, since the group then registers nothing.
  **195 pass + 1 skipped, 196 with `--e2e`, 97%** (1533 stmts / 47 missed);
  `lib/migrator_functions.py` still 100%, `lib/setup_prompts.py` 81 -> 95.
  **FOUR revert probes, one per property, each failing for the right reason**: eager
  `migrator_functions` import -> the lazy-import test lists all 110 SDK modules;
  secret-store catch removed -> the raw `RuntimeError` escapes; `yes_no` hand-rolled
  again -> `assert 0 == 4` on the confirm count; `_menu` hand-rolled again -> "the
  management menu was never offered as a select prompt". The third of those is the one
  that mattered: without the prompt-TYPE assertion the fake would have let a reverted
  `yes_no` pass.
  **Also verified LIVE against the real shell**, which the tests cannot do: the select
  prompt renders `1) …`, `2) Cancel`; an empty reply takes the Finish default; a
  declined confirm left the install and the developer's connection untouched.

- **The old orphaned install is GONE** (the user asked for it): 944K deleted from
  `~/.mariadb-shell/plugin_data/mcp_plugin/mariadb-migrator`. Checked first with
  `find -newermt` that nothing had been modified after the Aug 10 extraction timestamp —
  the `sqldata.cfg` vs `sqldata.cfg-example` differences ship that way upstream and were
  NOT user edits. `settings.json` was left untouched. There is still no adoption step for
  a pre-existing copy elsewhere, and none was asked for.

- **The tooling was then INSTALLED FOR REAL at the new location**, by piping answers to the
  actual `mcp.setup()` menu (`printf '5\ny\n6\n' | mariadb-shell --py -e "mcp.setup()"`)
  rather than by calling `download()` — so a live GitHub fetch and the real extraction
  were exercised end to end. `~/.local/share/mariadb-migrator/v1.4.0-beta`, 1.0M, entry
  point and `scripts/*.sh` `-rwxr-xr-x`, no `.migrator-version`, no leftover work dirs, and
  the menu re-rendered as `5. Remove ...` / `6. Finish`. **`useWizards` is TRUE under
  `--py -e` with piped stdin**, so the interactive setup CAN be driven this way; the
  "must be run from an interactive shell session" guard does not block it.

- **The `v1.4.0-beta` tag MOVED upstream between Aug 10 and Sep 4**: the re-download is not
  byte-identical to the orphan (CHANGELOG.md 38,514 -> 44,891; `mariadb-migrator` 108,555 ->
  115,423; README.md 34,946 -> 37,562). **This partly undercuts the pinning rationale** in
  `lib/general.py`, which claims a pinned release makes an install a property of the plugin
  version rather than of the day it was set up — that only holds for an immutable tag, and
  this one was not. Pinning a commit SHA would actually deliver it. RAISED with the user,
  NOT changed.

- Noted while inspecting the release: `config/source.yaml` ships with a plaintext
  `MYSQL_PWD: "Root@1234"` at `-rw-r--r--`. A placeholder, but it is the file a user would
  put a real password into, and it would inherit those permissions. Raised, not changed.

- Everything from the previous checkpoint's "Next steps" list is DONE and committed:
  REST SQL work (0e97c9e9), the sibling mrs_plugin management-session fix (ca47b8c8 then
  reworked in 82e18c4c), msm lifecycle + streamable-http transport tests (09fa116c).

## Next steps

0. **`wip/sandbox-binaries` is pushed with a PR open** (see Git state). Review comments on
   it are the next thing to expect.

1. **The PR is OPEN as #19** (see Git state) — the user asked for it once the migration
   tools landed, lifting the earlier "wait with the PR" instruction. Review comments on it
   are the next thing to expect.

2. **DECIDE: restore the Windows VM's original plugin, or leave this branch's build?** It
   is at `C:\Users\dev\mcp_plugin.backup-26.9.0`; ours is installed. Left as-is because
   the user asked for the replacement.

## Git state

- **Branch: `wip/sandbox-binaries`, cut from `main` at `99b54290` ("Updating version to
  26.9.1").** NOT from `wip/AIPL-21` — that branch was merged as PR #19 (`34817a49` on
  main) and DELETED, so `git merge-base --is-ancestor wip/AIPL-21 HEAD` errors with "not a
  valid object name". The AIPL-21 bullets below are history.
  FIVE commits on top of `main`, each green on its own tree:
  1. `fbba10df` "Deploy a sandbox on a requested MariaDB server version, downloading it if
     needed" (8 files, +2422/-16) — `lib/sandbox_servers.py`,
     `lib/sandbox_server_versions.json`, `tests/unit/test_sandbox_servers.py`, the two
     path helpers in `lib/general.py`, the tool + `server_version` in
     `lib/sandbox_functions.py`, README, conftest and the `e2e` marker text.
  2. `352b0f3e` "Accept a bare major version too, so 'give me MariaDB 11' resolves"
     (4 files, +272/-68) — also normalizes the version `install()` is handed.
  3. `fe228659` "Re-pin the sandbox server checksums to the re-uploaded v26.9.1 packages"
     (1 file, +12/-12) — see the Gotchas entry; verified against what is served before
     committing.
  4. `b065b105` "Say in the test that the download test is platform-neutral, because it
     is" (1 file, +12/-4) — documentation only, prompted by the CI-runs-on-Linux question.
  5. `774bfaa8` "Tell the client how to shut down a sandbox on a downloaded server"
     (3 files, +25/-4) — the Windows finding.
  Plus this file's update, uncommitted at the time of writing.
- CI (`.github/workflows/shell-plugins-ci.yml:119`) runs `cd mcp_plugin && msh --py -f
  run_tests.py` with NO `--e2e`, so neither end-to-end test runs there.
- **HISTORY BELOW.** Branch: `wip/AIPL-21`, PR #19, open against `main`.** NOT `wip/AIPL-16`; the AIPL-16
  bullet further down is now history. **EIGHT commits on top of `main`** (the earlier
  "THREE commits" wording here was stale and the list stopped at the fifth):
  1. `0137ea80` "AIPL-21: Install the MySQL-to-MariaDB migration tooling from mcp.setup"
     (6 files, +869/-16) — the original download step.
  2. `010600da` "AIPL-21: Make the installed migration tooling self-contained and runnable"
     (8 files, +1596/-543) — the three-module split, the Windows gate, the move to
     `~/.local/share/mariadb-migrator/<version>`, the venv + dependencies, and the
     `~/.local/bin` wrapper.
  3. `8b7a73f4` "AIPL-21: Add the migration MCP tools" (5 files, +1617/-3) — the
     `migration` function group.
  4. `377d48da` "AIPL-21: Update the mcp_plugin project context".
  5. `2f335a6c` "AIPL-21: Make every mcp.setup item available as a command-line option"
     (6 files, +1329/-46) — `lib/setup_cli.py`, the four password sources and
     `--show`/`--json`.
  6. `c15b187a` "AIPL-21: Record the non-interactive setup in the project context".
  7. `6cecd4bb` "AIPL-21: Rename migration.* to migrator.* and setup_migration to
     setup_migrator".
  8. `90fb6405` "AIPL-21: Build the tooling's virtual environment with a symlinked
     interpreter" — the Linux CI fix (see Architecture: `symlinks=True` is mandatory).
  Plus, **UNCOMMITTED in the working tree as of 2026-09-07**: the response to the first
  three PR #19 review comments (the shell's own prompt types, the migrator `ToolError` /
  plain registration, the lazy registrar dispatch, plus tests and this file). Green at
  194 + 1 skipped / 195 with `--e2e`, and probed four ways, but NOT yet committed - the
  user had not asked for a commit when it was written.
  Plus **the commit the previous checkpoint describes**: the opt-in end-to-end migration test
  (`tests/unit/test_migration_e2e.py` new, plus `tests/conftest.py`, `run_tests.py`,
  `pytest-coverage.ini`, `README.md` and this file). ONE commit: the test and the
  mechanism that keeps it out of a standard run are the same change, and the test was
  never green in the tree without both.
  **Split into 2 rather than 5 deliberately, and each was PROVEN green on its own tree**:
  the module split, the Windows gate, the relocation and the provisioning all interleave in
  `lib/setup_migrator.py` and its tests, so splitting them further would have meant
  committing states that were never run green (the same reasoning as the S5/S6/S7 and
  T1..T4 commits). The MCP tools ARE separable, so they are their own commit — built by
  moving `lib/migrator_functions.py` and `tests/unit/test_migrator_tools.py` out of the
  tree, reverting `lib/server.py` and holding back the `FUNCTION_GROUP_MIGRATOR` and
  README-tools regions, running the suite (**111 pass**), committing, then restoring
  (**148 pass**). Do that again rather than eyeballing whether a split commit builds.
  **IN SYNC with `origin/wip/AIPL-21` (divergence 0/0).** The rebase was force-pushed
  — `git push --force-with-lease origin wip/AIPL-21`, run MANUALLY by the user after the
  auto-mode classifier refused it (see Gotchas). The pre-rebase twin `a5d0a177` is no longer
  reachable from the remote, so any future reference to it is to a dangling commit. The
  commit is authored `Mike Zinner <mike@zinner.org>` and co-authored by Claude.
- **PRs #16 (`wip/MCP-CONN-HANDLING`), #17 (uppercase README.md) and #18 (version 26.9.0)
  are all MERGED** (verified with `gh pr list`, not assumed), so `main` carries a8428531 /
  77318a27 / a286d58b — that is what `wip/AIPL-21` was rebased onto. The "PR #16 open"
  claim in the bullet below is therefore SUPERSEDED.
- **A NEW branch `wip/MCP-CONN-HANDLING` was cut from `main` (1d6a9c37) after the AIPL-16
  work below was merged**, for one reported bug: `db.connect` compared the URI it was given
  with the configured ones as STRINGS, so `mariadb://root@127.0.0.1:PORT` — the form a
  client naturally writes, and one `shell.parse_uri` rejects as an invalid scheme — never
  matched the stored `root@127.0.0.1:PORT`. Fixed with URI normalization/resolution in
  `lib/config.py` (see the Connections bullet under Architecture), applied in `db.connect`
  and in `mcp.setup`'s add-connection flow, documented in the README's new "Which URI names
  which connection" section, and pinned by 5 tests. Both fixes were PROVEN to discriminate
  by reverting them: without the `db.connect` one the test fails with the reported error
  verbatim, without the `mcp.setup` one `not a uri` is stored as a connection. **COMMITTED
  as 612cd05e (one commit, 7 files, +426/-23), pushed to `origin/wip/MCP-CONN-HANDLING`, PR
  #16 open against `main`.** The user hand-edited the README section before the commit
  (dropped the "not even a scheme the parser accepts" aside and the `mysqlx://` sentence);
  that wording is theirs, leave it alone.
- **Remotes (CORRECTED, verified with `git remote -v`): there is exactly ONE remote,
  `origin` = mariadb-corporation/mariadb-shell-plugins, and it IS the push target.** `main`
  tracks it. The earlier claim in this file — `mariadb` as the push target, `origin` as
  mysql/mysql-shell-plugins, plus a `local_office` NAS mirror — no longer holds for this
  checkout; every `mariadb/<branch>` reference in the bullets below means what is now
  `origin/<branch>`. Check `git remote` rather than trusting a remembered name.
- (HISTORY, superseded by the branch bullet at the top of this section) Branch:
  **`wip/AIPL-16`**, cut from `main` (which was at 8da59831) and pushed to
  `wip/AIPL-16` on that remote.
- **The session started in DETACHED HEAD** at `mariadb/wip/AIPL-16` (ecc6bc3c) with no
  local branch — `git checkout -b wip/AIPL-16` was needed before committing. Check
  `git branch --show-current` before assuming there is a branch to commit onto.
- The security work is eight commits on that branch:
  1. **S1 + S2/S8** (faa08b11) — the proxy-header fix and the transport-independent,
     normalized binding.
  2. the `sandbox.deploy` `sandbox_dir` docstring note (5b94d940) — a pre-existing edit the
     session inherited unstaged, committed separately on the user's instruction because it
     is not part of the security work.
  3. **S3 + S4** (9c877f37) — the session-id binding, the no-authentication README section
     and warning, and the explicit Host/Origin validation.
  4. **S5 + S6 + S7** (b86b0541) — the audit trail, the hard TTL + URI re-validation, and the
     caps. ONE commit, not three: S6 and S7 both call S5's `log_event` and all three interleave
     inside the same functions and docstrings, so splitting them would have meant committing
     intermediate states that were never run green. The user asked for the commit without
     specifying granularity after that reasoning was put to them, and the same reasoning (and
     the same answer) applied to every commit after it.
  5. **T1 + T2 + T3 + T4** (f80673ce) — the close/use race, the cross-thread verification and
     its regression test, dead-session detection, and the session_restarted signal. ONE commit
     again, for the same reason: T3 and T4 both build on paths T1 changed, and all four touch
     `use_session`, `_Connection` and the same docstrings, so any split would have committed
     states that were never run green. **Its coverage claim (100% / 97%) is WRONG** — see the
     Coverage bullet under Current state.
  6. **T5 + T6** (542c0d79) — session work off the event-loop thread, and the reaper's lifetime
     moved to `server.start()`. Together because T6's wiring test asserts the order
     `start`/`served`/`stop` around the same `server.start()` branch T5 left alone, and both
     land in `use_session`/`server.py`. Also carries the README change from
     `mariadb-shell --py -e "mcp.setup()"` to `mariadb-shell -- mcp setup` (the user's own
     wording) and the corrected coverage figure.
  7. **the three untested paths** (17a3408d) — `db.connect`'s slot giveback and its
     unconfigured-URI refusal, and `server.start`'s three validation raises. Tests only.
  8. **T8 + T9** (HEAD when this was written) — duplicate column labels keyed apart and read by
     position, and the FK reference mapping ordered by the key's own ordinal. Together because
     both are `_serialize_result`/introspection output and both are pinned in the same
     `test_db_sql` flow.
- **The remote branch moved mid-session and the push was rejected.** `mariadb/wip/AIPL-16`
  had gained e9122f6d (a merge of `main`, bringing 962165d7, a CI job-name change touching
  only `.github/workflows/shell-plugins-ci.yml`). Rebased rather than merged (one commit, no
  file overlap) and then **re-ran the full suite on the new base before pushing** — do that
  again rather than pushing a commit that was only ever green on the old base. Note
  `git stash push -- <path>` was needed first: the inherited unstaged edit blocked the
  rebase.
- Earlier commits on the branch: 0bba1318 (connection safeguards as first built),
  6067fd8c (idle timeout 10 -> 30 min), ecc6bc3c (branch rename recorded here).
- `.claude/skills/create-shell-plugin/SKILL.md` is still deliberately left UNSTAGED (not
  this work).
- The `wip/AIPL-5` history below predates it and is already in `main`:
- Session history: 3482634a (db introspection tools) -> the msm_plugin MariaDB/sandbox/
  backup commit -> the mcp_plugin `msm.deploy_schema` + db-group-gate commit -> 72c07ef8
  (doc the new db/msm tools + `get_mcp_plugin_data_path` -> `get_plugin_data_path`).
- Then THIS session: the three rebranding commits from `mariadb/rennox/rebranding`
  (759c375d msm, 7f119fd6 mrs, f11a3897 mcp) cherry-picked with `-n` onto `wip/AIPL-5`,
  reviewed by the user, and committed together with the MCP SDK 2.0 migration and the
  test-suite fixes.
- `mariadb/rennox/rebranding` itself is NOT merged — its commits were replayed, so the
  branch will look unmerged and a future merge would conflict. Rebase or drop it.
- One unrelated pre-existing edit was left UNSTAGED on purpose:
  `.claude/skills/create-shell-plugin/SKILL.md` (not this session's work).

### Verbatim snapshot at the previous checkpoint (2026-09-04, second of that day)

```
$ git -C mcp_plugin status --short
(no output — clean)

$ git -C mcp_plugin branch --show-current
wip/AIPL-21

$ git status -sb | head -1
  ## wip/AIPL-21...origin/wip/AIPL-21
  (indented by one space here only so the line does not read as a Markdown heading
   to the greps this file is navigated with; the real output has no leading space.
   No [ahead/behind] suffix = in sync, after the manual force-push.)
```

Note: `.coverage`, `htmlcov/`, `plugin-tests.xml` and `plugin-test-coverage.xml` are
regenerated by every suite run and are gitignored, which is why a clean status survives
running the tests. The `.claude/skills/create-shell-plugin/SKILL.md` edit that earlier
checkpoints recorded as deliberately UNSTAGED no longer shows in status — do not go
looking for it.
