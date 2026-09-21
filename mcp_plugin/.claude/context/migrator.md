# The migrator: install and tools

The MySQL-to-MariaDB migration tooling — the naming rule, the four
`migrator.*` tools, the install `mcp.setup` performs, the Windows gate, and
the one opt-in end-to-end migration test.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The setup surface that
installs it is in [setup.md](setup.md).

## Architecture / key decisions

- **The naming rule, applied throughout: `migrator` is the TOOL, `migration` is what it
  performs.** So the module, the install, the MCP group and its tools, and the CLI options
  are all `migrator`; and the things that stay `migration` are the tool's OWN external
  names — `config/migration.yaml` (`MIGRATION_CONFIG`, `MIGRATION_CONFIG_EXAMPLE`) and
  `orchestrator.migrationctl` (`ORCHESTRATOR_MODULE`) — plus prose about a migration run.
  Renaming those two would break the tooling, so do not "finish the job" on them.

- **The migrator MCP tools live in `lib/migrator_functions.py`** (function group
  `migrator`, registered in `lib/server.py` like the other three). Four tools:
  `migrator.set_config`, `.plan`, `.run`, `.resume`. Decisions that matter:
  - **They are NOT wrappers around shell plugin functions, and are coded accordingly**
    (PR #19 review). They drive a program of their own, so they raise the SDK's
    **`ToolError`** directly and register with plain **`server.tool`** - NOT
    `mysqlsh.Error` through `tool_registrar`, which exists to translate a shell API's
    exception into one whose text reaches the client. `lib/migrator_functions.py` does
    not import `mysqlsh` at all any more. db/msm/sandbox DO still use the registrar and
    must keep it: those really are wrappers.
    - **Raising `ToolError` is what makes the text ARRIVE on SDK 2.1, not merely the
      right shape** (see the SDK-error gotcha, and note the earlier claim here that
      "nothing is lost by not converting" was true only of 1.28.x/2.0.0). Anything else
      out of these tools is a crash the client reads as a bare "Error executing tool
      <name>", which is why every refusal in this module raises `ToolError` and why the
      one plain exception it can meet gets converted by hand: the secret store's
      `RuntimeError` out of
      `config.get_connection_password` ("Could not find the secret" when a connection
      was removed while a migration was starting) - `_connection_passwords` catches it
      and re-raises with the store's own words plus which connection and which config
      key it was for.
  - **`ToolError` is imported at MODULE scope, which is only safe because
    `lib/server.py` resolves every registrar LAZILY.** See the import-hazard gotcha:
    importing it loads ~110 `mcp.*` modules, `mcp.client.stdio` among them, and the
    plugin must load without any of them. `_FUNCTION_GROUP_REGISTRARS` therefore maps a
    group to a `(module, function)` NAME pair that `_registrar()` resolves with
    `importlib` when the group is actually served. `db_functions` is still imported at
    module scope in `server.py` - for the connection reaper `start()` owns, not for
    registration - which costs nothing since `lib/__init__.py` imports it anyway.
    Pinned by `test_loading_the_plugin_imports_no_mcp_sdk_module`, which runs a shell
    SUBPROCESS because this process has long since imported the SDK itself.
  - **Registration is GATED on `setup_migrator.is_installed()`.** No install -> the group
    registers NOTHING and says so via `log_event`, rather than advertising four tools whose
    every call would fail. Consequence: installing the tooling takes effect on the NEXT
    server start, not the current one.
  - **A configuration may only name CONFIGURED connections, and one that does not is
    REFUSED rather than written** (`validate_connections()`). This is the primary security
    property, tightened on the user's instruction after the first cut: originally an
    unconfigured host merely went without a password, so the file was written and the run
    failed later. Now such a configuration cannot exist on disk at all.
    - Every account the config names is checked, via `_named_connections()`: SRC_ADMIN_USER
      and SRC_USER on `SRC_HOST:SRC_PORT`, the TGT pair on `TGT_HOST:TGT_PORT`, REPL_USER on
      the SOURCE.
    - **A side naming a HOST but no account is also refused** (`_CONNECTION_SIDES`). That was
      the hole the check would otherwise have: name a forbidden host, omit the user, and
      nothing composes a URI to test.
    - **Validated on the MERGED result**, so two individually harmless `merge=True` calls
      cannot assemble a forbidden connection.
    - **Validated AGAIN in `_run_orchestrator`**, not only at write time — so removing a
      connection with `mcp.setup` stops runs already configured against it (revocation),
      and a hand-edited file does not slip past.
    - The refusal message lists the configured connections and points at
      `db.list_connections`.
  - **Passwords never touch disk and are confined to CONFIGURED connections.** The second
    security property. `set_config` REFUSES `SRC_PASS`,
    `SRC_ADMIN_PASS`, `TGT_PASS`, `TGT_ADMIN_PASS`, `REPL_PASS` (non-empty only — the
    tooling's own example has them as empty strings). At run time
    `_connection_passwords()` composes `<user>@<host>:<port>` from the config's OWN fields
    (`_PASSWORD_SOURCES` maps each password key to its user/host/port keys) and resolves it
    with `config.resolve_connection_uri`. **Unresolved -> the password is simply not
    supplied**, so writing an arbitrary host into the config gets nothing for it. The
    returned dict carries `passwords_from` (the configured URI) and NEVER the secret; a
    test serializes the whole outcome and asserts no secret appears in it. Since
    `validate_connections` now runs first, the "unresolved -> no password" branch in
    `_connection_passwords` is belt to that braces rather than the primary guard.
  - **`REPL_PASS` is looked up on the SOURCE** (`REPL_USER@SRC_HOST:SRC_PORT`) — the
    replication user lives there. There is no host field of its own in the config.
  - **Every invocation gets `stdin=subprocess.DEVNULL`.** NOT optional: the orchestrator
    prompts (`_prompt_required_env`) for anything missing, and **`plan` has no
    `--non-interactive` flag at all** (verified against the installed release's `--help`).
    Without a closed stdin an incomplete config would hang the tool call until timeout.
    This is also why an earlier manual `plan` run printed "Aborted." rather than hanging.
  - **`--non-interactive` is passed to `run` and `resume` ONLY.** Passing it to `plan`
    would break the invocation.
  - **The child gets `cwd=install_dir` plus `VIRTUAL_ENV` and a prepended `<venv>/bin` on
    PATH** — what `activate` does, without a shell. `PYTHONHOME` is popped. So the
    orchestrator and anything it shells out to resolve `python3` inside the venv. `out`
    must be RELATIVE to the install (absolute is refused).
  - `set_config`'s body is the module-level `write_config()`, not a closure — lifted out
    deliberately so it is testable without a server. Written via `mkstemp` + `os.replace`
    at **0o600** (it names hosts and accounts and may carry `APP_USER_DEFAULT_PASSWORD`),
    with the staged file removed on any failure.
  - **PyYAML 6.0.3 IS importable in the shell's Python**, so the config is rendered with
    `yaml.safe_dump(..., sort_keys=False)` rather than hand-built.
  - Values are stringified (`_stringify`): booleans become the tooling's `"1"`/`"0"`, None
    becomes `""`, and a list/dict is REFUSED rather than silently flattened.

- **There is ONE end-to-end migration test and it is OPT-IN**
  (`tests/unit/test_migration_e2e.py`, marked `e2e`). It deploys a MySQL source and a
  MariaDB target with `sandbox.deploy`, registers both through the `mcp setup` COMMAND
  LINE, creates a schema on the source with the `db.*` tools, installs the tooling with
  `mcp setup --installMigrator`, migrates with `migrator.set_config` + `migrator.run`, and
  checks every migrated object and row on the target. Decisions:
  - **Opt-in via a marker, SKIPPED rather than deselected.** `pytestmark =
    pytest.mark.e2e` on the module; `tests/conftest.py` adds a `--e2e` option and skips
    anything carrying the mark when it is absent (inside the EXISTING
    `pytest_collection_modifyitems`, which now takes `config` as well as `items`);
    `run_tests.py` grew `-e/--e2e`; the marker is registered in `pytest-coverage.ini` so
    there is no `PytestUnknownMarkWarning`. Skipped, not deselected, so a standard run
    still says the test exists and why it did not run. **`--only=migration_e2e` alone does
    NOT opt in** — the mark is checked independently of `-k`, which is the point: nothing
    but `--e2e` runs it.
  - **Why it is not in the standard suite at all**: it deploys two servers, reaches GitHub
    and PyPI, and installs software into `~/.local`. CI calls `run_tests.py` with no flags,
    so it is skipped there.
  - **It covers MODE 1 (`one_step`) only.** Mode 3 (`staged`) cannot run on macOS at all:
    `scripts/25_staged_dump.sh` uses `declare -A` (bash 4.0+) and `wait -n` (bash 4.3+),
    and macOS ships bash 3.2. Not a configuration that can be worked around — see Gotchas.
  - **Three things about the tooling are configured for, not rediscovered**: an upstream
    `mysqldump` via `MARIADB_DUMP_BIN`, `ALLOW_ROOT_USERS=1`, and `pv` being present. Each
    is a real defect or requirement, documented at its own call site in the test and in
    Gotchas.
  - **It reuses an existing install and removes one it made itself**, and restores the
    tooling's `config/migration.yaml` either way. `clean_config` restores the connections
    and allowed paths. A developer's install is not this test's to replace.

- **The migration tooling is NOT offered on Windows** (`setup_migrator.is_supported()`,
  `os.name != "nt"`). It is a POSIX shell entry point driving a directory of shell
  scripts, so an install on Windows would be a menu entry that only ever disappoints, and
  nothing else in the plugin depends on it. Two deliberate choices here:
  - **The predicate tests for Windows, not for Linux-or-macOS.** What actually decides it
    is the POSIX shell, which every other platform this shell builds for has, so a new
    Unix should not have to be added to a list to get a feature that already works on it.
    If the user ever wants the strict allowlist instead, it is one line.
  - **The menu is BUILT, not written out** (`_menu_entries()` returns (label, action)
    pairs; `_menu` numbers them and appends Finish at `len(entries) + 1`). This is what
    makes the Windows case fall out: the entry is absent and every number after it,
    Finish included, shifts up by one on its own. Finish is **6** on Linux/macOS and **5**
    on Windows — do not re-hardcode either. `print_status()` is gated too: "not shown"
    has to cover the status line the menu prints above itself, not just the entry.

- **Migration tooling install (AIPL-21)**: `mcp.setup` can download the MySQL-to-MariaDB
  migration tooling (github.com/mariadb-corporation/Mysql-to-MariaDB-Migration) into
  **`<data home>/mariadb-migrator/<version>`** — `~/.local/share/mariadb-migrator/v1.5.0`
  by default. Decisions worth not relitigating:
  - **NOT the plugin data directory** (it was, until the user redirected it): the tooling is
    a standalone program that outlives any one plugin install and that things other than
    this plugin may want to run, so it goes where such a program belongs. The path helpers
    are in `lib/general.py`: `get_data_home()`, `get_migrator_root()`,
    `get_migrator_path(version=None)`.
  - **`$XDG_DATA_HOME` is honoured when ABSOLUTE**, else `~/.local/share`. A relative value
    is ignored rather than resolved against cwd — the XDG spec requires that, and
    resolving it would put the install wherever the shell happened to start. Empty is unset,
    not `/`.
  - **The release is part of the PATH, and there is NO version file.** `.migrator-version`
    was dropped on the user's call: the directory name is the one authoritative record, with
    nothing to disagree with it. `installed_versions()` reads the directory names;
    `installed_version()` (singular) is GONE.
  - **Download working directories are dot-prefixed** (`MIGRATOR_WORK_PREFIX`):
    `.<version>.new`, `.<version>.old`, and the archive's temp dir as `.download-*`, all
    inside the root. That is what lets `installed_versions()` tell a release from a download
    in progress by name alone — do not un-prefix them.
  - **`remove()` deletes the WHOLE root, every release** (user's call over per-release
    removal). Releases accumulate as the pin moves on, and a release the pin has moved past
    would otherwise be unreachable from the menu and need `rm -rf` by hand.
  - **`menu_label()` and `manage()` key on `installed_versions()`; `is_installed()` is about
    the CONFIGURED release.** They differ on purpose: with an old release present but not
    the configured one, the menu must offer Remove, or all-or-nothing removal would strand
    it. An earlier draft had `menu_label` on `is_installed()` and `manage` on the list,
    which would have shown "Download" and then performed a remove.
  - **NO system Python is required, and the python3-on-PATH CHECK WAS REVERTED.** It
    existed for about an hour and was removed on the user's instruction once the shell's
    own Python was shown to be able to build the venv. Do not reintroduce it:
    `python_is_available`, `_print_python_requirement`, `MIGRATOR_PYTHON` and
    `HOMEBREW_INSTALL_COMMAND` are all gone deliberately.
  - **A download is THREE steps, reported separately** (`manage()` orchestrates;
    `download()` stays fetch-and-extract only so the extraction tests need no network):
    1. `download()` — archive into `<root>/<version>`.
    2. `provision()` — `venv.EnvBuilder(with_pip=True, clear=True)` IN-PROCESS (1.6s,
       measured), then `<venv>/bin/python3 -m pip install -r orchestrator/requirements.txt`
       as a subprocess with `cwd=target_dir` and a 300s timeout.
    3. `install_wrapper()` — `~/.local/bin/mariadb-migrator`.
    A failure in 2 or 3 leaves the extracted copy in place and SAYS which step stopped —
    it is a complete install, just not yet a runnable one.
  - **`venv.EnvBuilder(..., symlinks=True)` is MANDATORY, and is NOT the constructor's
    default** (`python -m venv`'s CLI passes it on POSIX; `EnvBuilder()` does not). The
    shell's interpreter is built with `Py_ENABLE_SHARED` and finds `libpython` through a
    loader path relative to its own location (macOS rpath `@executable_path/..`, Linux
    `$ORIGIN`), so a COPY of it inside the venv looks for that library beside the copy,
    does not find it, and dies before running anything with the loader's **exit status
    127**. A symlink leaves the resolved location, and the library, where the interpreter
    expects them.
    **This broke CI on Linux while passing locally**, and the reason it passed locally is
    worth knowing: the interpreter also carries a STALE ABSOLUTE rpath from the machine it
    was built on (`/Users/mzinner/git/Python-3.14.6/lib`), which resolves on that machine
    and nowhere else. Never take "the venv interpreter runs here" as evidence that it is
    built correctly.
    Pinned by a test asserting the interpreter is a SYMLINK resolving to
    `sys._base_executable` — a structural assertion on purpose, so a platform where the
    copy happens to run still catches the regression.
  - **The venv is built by the interpreter running this code**, i.e. the shell's bundled
    CPython at `<shell>/lib/mariadb-shell/bin/python3.14`. `venv`, `ensurepip` and `pip`
    are all bundled. `pip` is given **`--require-virtualenv`** so a wrong venv path can
    never install into the shell's own site-packages.
  - **`MIGRATOR_VENV_DIR = ".venv"` is NOT free to rename** — the tooling's own launcher
    sources `.venv/bin/activate` when it finds it (line ~257 of `mariadb-migrator`).
  - **The wrapper must BOTH `cd` and activate.** The tooling resolves `scripts/`,
    `orchestrator/` and `config/` relative to cwd, and its dep check
    (`scripts/00_check_python_deps.sh`) does an unconditional `command -v python3` as its
    step 1, which runs BEFORE the launcher would activate `.venv` itself. Activating in
    the wrapper is what makes that check pass with no system python3. Consequence worth
    documenting: relative args are relative to the INSTALL dir, so
    `--out artifacts/plan` lands under `<version>/artifacts/plan`.
  - **A foreign `mariadb-migrator` is NEVER overwritten.** `_wrapper_is_ours()` requires
    `MIGRATOR_WRAPPER_MARKER` in the file (or a symlink into the tooling root, the other
    shape an earlier install could have left). Anything else raises and is left alone —
    and `remove_wrapper()` leaves it too. An unreadable path (e.g. a directory) counts as
    NOT ours.
  - `remove()` removes the wrapper FIRST, then the tree: a wrapper pointing at a deleted
    install is worse than none.
  Decisions inherited from the original AIPL-21 commit, still standing:
  - **The release is PINNED** as `general.MIGRATOR_VERSION` (currently `v1.5.0`), not
    tracked from main: what an install contains is then a property of the plugin version and
    not of the day it was set up, and a release that breaks something is answered by pinning
    the one before it. There is deliberately NO update step — a different release means
    Remove, then Download.
  - **Menu only, never the first run.** It is a download nothing else here depends on, so
    `_first_run` does not offer it. `_migrator_menu_label()` shows whichever of Download /
    Remove applies, and adding the entry moved the menu's "finish" answer 5 -> 6
    (`test_config.py` had to follow — that is the whole of its diff).
  - **Extraction is hand-rolled on purpose** (`_extract_archive`): `zipfile.extractall` does
    NOT restore file modes, and the payload is shell scripts plus the `mariadb-migrator`
    entry point, so the recorded modes are re-applied or nothing is executable.
    `_archive_prefix` strips the archive's ONE top-level directory (only one — a test pins
    that), and `_archive_destination` refuses any entry that would be written outside the
    target (zip-slip).
  - **The new copy is staged beside the old one and swapped in only once complete**, so a
    failed download leaves an installed copy exactly as it was — and a failed SWAP puts the
    old copy back. Both directions are pinned by tests.
  - The installed release is recorded in `.migrator-version` (`MIGRATOR_VERSION_FILE`) and
    read back by `installed_migrator_version()`; a copy without it reports "unknown
    version", and a mismatch with the configured release is called out in the menu.
  - Download and removal failures are REPORTED, not raised, out of `_manage_migrator` — the
    setup menu must survive a network problem. `MIGRATOR_DOWNLOAD_TIMEOUT` is 120s.

## Files that matter

- lib/migrator_functions.py -> the `migrator` function group: `write_config` (the
  module-level body of `migrator.set_config`), `validate_connections` (the
  configured-connections-only gate, called from BOTH the write and the run path),
  `_named_connections`, `_connection_passwords`, `_invoke` / `_run_orchestrator`,
  `_load_config_env`, `_stringify`, `_render_config`, `_artifacts_dir`, `_read_report`,
  `_tail`, `register_migrator_tools`, `_PASSWORD_SOURCES`, `_CONNECTION_SIDES`.
  Raises `ToolError` throughout and imports NO `mysqlsh`; registers with plain
  `server.tool`. **100% covered — keep it that way.**

- tests/unit/test_migrator_tools.py -> the 30 migrator-tool tests. The orchestrator is
  NEVER run: `recorded_run` stubs `subprocess.run` and the assertions are on the command,
  cwd, env and stdin. `fake_install` builds a stand-in install (config/, the example, a
  stub venv python); `configured_connections` PATCHES `config.resolve_connection_uri`,
  `get_connection_password` AND `list_connection_uris` rather than storing secrets, so the
  suite never writes to the developer's real secret store. **All three patches matter**:
  missing `list_connection_uris` let one test read and PRINT the developer's real
  connections into the failure output.

- tests/unit/test_migration_e2e.py -> the ONE opt-in end-to-end test (marked `e2e`; see
  Architecture). Nothing is stubbed: two real sandboxes, the real setup CLI in a
  subprocess, a real install, a real migration. Named `migration_` and not `migrator_`
  per the naming rule — it exercises a MIGRATION. `MYSQL_SERVER_BINARY` is overridable
  with `MCP_TEST_MYSQLD`; `_unsupported_reason()` holds every skip condition in one place,
  so a machine that cannot run a migration says WHICH part is missing.

- lib/setup_migrator.py -> ALL migration-tooling code: `is_supported` (the Windows gate),
  `archive_url`, `installed_versions`, `_download_archive`, `_archive_prefix`,
  `_archive_destination`, `_extract_archive`, `download`, `remove`, `is_installed`,
  `menu_label`, `print_status`, `manage`, `MIGRATOR_WORK_PREFIX`, plus the provisioning
  and wrapper work (`provision`, `_venv_python`, `MIGRATOR_VENV_DIR`,
  `MIGRATOR_REQUIREMENTS`, `MIGRATOR_PIP_TIMEOUT`, `wrapper_path`, `_wrapper_contents`,
  `_wrapper_is_ours`, `install_wrapper`, `remove_wrapper`, `MIGRATOR_WRAPPER_MARKER`,
  `_print_path_hint`). 99% covered.

- tests/unit/test_setup_migrator.py -> the 17 install tests, driven with a monkeypatched
  `urlopen` and a `migrator_data_path` fixture — NO network, so they are fast and need no
  sandbox. Weighted toward the FAILURE paths: a failed download keeps the installed copy, a
  failed swap puts it back, only ONE wrapping directory is stripped, an archive cannot write
  outside the target, a copy with no recorded version reads as unknown, and download /
  removal failures are reported rather than raised. Two of the 17 are the platform gate:
  the predicate itself (patching `os.name` to `"nt"`) and `_menu_entries` losing the entry.
  The user-facing "not shown" case is in test_config, driven through `run_setup`.

## Gotchas / things not to repeat

- **The migration tooling has THREE bash-3.2 / MySQL-source defects, and the e2e test
  configures around all of them.** Every one was found by running the tooling and reading
  its own `report.json`, not by reading its source first. Do not "simplify" the
  configuration in `_migration_env` by dropping any of these:
  - **`mariadb-dump` CANNOT dump a MySQL 8.4+ source**: it issues `SHOW PACKAGE STATUS`
    (an Oracle-mode MariaDB feature) and MySQL answers 1064. The tooling's own preflight
    prints a WARNING naming the fix and then proceeds to fail anyway, so the warning is
    the whole diagnosis. Fix: `MARIADB_DUMP_BIN=<mysql install>/bin/mysqldump`. This
    ALSO happens to sidestep the next one, because the upstream branch composes
    `--ssl-mode=<value>` rather than an empty array.
  - **`"${SRC_SSL_ARGS[@]}"` on an EMPTY array is an unbound-variable error under
    `set -u` in bash 3.2** (fine from bash 4.4 on). With `mariadb-dump` and
    `SRC_SSL_MODE=DISABLED` — or with `SRC_SSL_MODE` unset — the array is empty and
    `10_one_step_migration.sh:226` dies. So mode 1 with the DEFAULT dump tool cannot work
    on macOS at all, whatever the SSL mode.
  - **Without `pv`, mode 1 reports a SUCCESSFUL migration as FAILED on bash 3.2.** The
    no-pv fallback starts a background heartbeat and kills it from an `EXIT` trap whose
    `wait` both holds the script's stdout open for a further 60s and (bash 3.2 only) makes
    the script exit **143**. The data is all correctly copied; the verdict is wrong. `pv`
    is documented as OPTIONAL, so this is the documented path being broken. The test skips
    when `pv` is missing AND bash < 4 — deliberately that conjunction, not "no pv", so a
    Linux box without pv is not skipped for a bug it does not have.

- **Mode 3 (`staged`) needs bash 4 and there is no way around it.**
  `25_staged_dump.sh:345` uses `declare -A` (4.0+) and the parallel paths use `wait -n`
  (4.3+). `STAGED_PARALLEL=1` does not help: `declare -A` is reached first. macOS ships
  bash 3.2, so mode 3 is untestable here — do not spend another probe on it.

- **The tooling refuses to migrate as `root` unless `ALLOW_ROOT_USERS=1`.** A sandbox has
  no other account, so every e2e migration needs it. The refusal is a `typer.BadParameter`
  out of `migrationctl run`, i.e. it looks like a configuration error, not a policy.

- **A migration run into a REUSED artifacts directory migrates NOTHING and says it
  succeeded.** The orchestrator is resume-safe: a `state.json` left by an earlier run makes
  the next one report every step `SKIPPED` with exit 0. The e2e fixture therefore clears
  the out dir BEFORE the run as well as after, and the test asserts every step is `DONE`
  rather than just that the run exited 0 — either alone would have missed it. This was
  observed for real, as the second run of a passing test.

- **`--non-interactive` skips the orchestrator's `_prompt_required_env` ENTIRELY**, and
  that function is also where the "align migration creds with admin creds"
  (`SRC_USER = SRC_ADMIN_USER`, and so on) assignment lives. `run` does its own
  `setdefault` for those four, so they survive — but `plan` does NOT, and it has no
  `--non-interactive` flag either. Set `SRC_USER`/`TGT_USER` explicitly rather than
  relying on the derivation; the e2e config does, which is also what makes
  `_connection_passwords` supply `SRC_PASS`/`TGT_PASS` and not just the admin pair.

- **The migrator does NOT live under the plugin data path** and has not since the user
  redirected it. `general.get_plugin_data_path()` is not on that road any more; the
  fixture patches `general.get_data_home`. Any test that patches the plugin data path
  expecting to redirect an install is patching the wrong seam and will write into the real
  `~/.local/share`.

- **Any test that reaches `write_config` or `_invoke` needs the `configured_connections`
  fixture**, or `validate_connections` queries the developer's REAL connections and the
  test both fails and prints them. This bit
  `test_a_failed_write_leaves_no_staged_file_behind` the moment validation was added.

- **"It runs on my machine" is NOT evidence a venv is built correctly.** The shell's
  interpreter carries a stale absolute rpath from its build tree, so on the build machine
  a mis-built venv (copied interpreter) runs anyway and only fails on Linux CI. Assert the
  STRUCTURE (symlink to `sys._base_executable`), not just that a probe subprocess exits 0.

- **`venv.EnvBuilder(with_pip=True)` ITSELF calls `subprocess.run`** (for `ensurepip`).
  A test that patches `subprocess.run` and asserts one call will see TWO — select the
  pip-install call (`command[1:4] == ["-m", "pip", "install"]`) instead of assuming it is
  the only subprocess. This cost one failing test.

- **The wrapper tests need `general.get_bin_home` patched** (`migrator_bin_home`
  fixture), or they write into the developer's real `~/.local/bin`, which is on PATH.

- **`installed_version()` (singular) no longer exists** — it is `installed_versions()`,
  returning a sorted list read off the directory names. There is no `.migrator-version`
  file any more; do not reintroduce one "for clarity", it is a second record that can
  disagree with the path.

## Next steps

3. **Write the SKILL for authoring `config/migration.yaml`.** The user said this is coming
   ("We will write a skill covering how to write that later") and it is the one piece of
   the migration feature deliberately left out. It has to teach an LLM: the shape
   (top-level `mode`, then `env` of STRING values), that only accounts among the
   configured MCP connections may be named (`migrator.set_config` refuses anything else),
   that the five password keys are refused and resolved from the secret store instead, and
   which keys each mode needs. The tooling's own
   `config/migration.yaml.example` is the template and
   `migrator.set_config` returns its path.

1. **Mode 3 (`staged`) has NO end-to-end coverage and cannot get any on macOS.** It needs
   bash 4.0 (`declare -A`) and 4.3 (`wait -n`); macOS ships 3.2. Options if the user wants
   it: `brew install bash` (bash 5 lands on the PATH ahead of /bin, and the tooling's
   scripts are `#!/usr/bin/env bash`, so it would also make the other two bash-3.2 defects
   disappear), or run the test on Linux — where `/usr/local/mysql/bin/mysqld` is not
   present either, so the source would need a different binary via `MCP_TEST_MYSQLD`. A
   mode-3 test gated on `bash >= 4.0` was considered and NOT written: it could not be run
   here, and an unverified test in the suite is a liability.

1. **The three tooling defects the e2e test works around are worth REPORTING upstream**
   (Mysql-to-MariaDB-Migration): the two bash-3.2 breakages in mode 1 and mode 3, and the
   `mariadb-dump`-against-MySQL failure that the preflight warns about but the default
   configuration walks straight into. Not done — no issue was filed.
