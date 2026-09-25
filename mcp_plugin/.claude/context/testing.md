# The test suite

What the suite contains, how to run it, what the coverage figure means, and
the fixtures and harness traps that make a test pass or fail for the wrong
reason.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Current state

- Tests (tests/unit/, no `__init__`): `test_sandbox` (deploy FIRST, shutdown LAST +
  path-reject), `test_config` (9), `test_msm` (5: create_project, elicit-accept,
  elicit-decline, deploy-needs-db-group, lifecycle), `test_db_sql`, `test_rest_sql`,
  `test_transport_http` (**5**: list_connections, a full connect/execute/close db flow over
  HTTP, `..._ignores_a_forwarded_for_header` for S1,
  `..._binds_a_connection_to_its_mcp_session` for S3, and
  `..._rejects_a_foreign_host_header` for S4 — the last one talks raw `httpx2` rather than
  the MCP client, because it has to forge Host/Origin and assert HTTP status codes),
  `test_db_sessions` (**41** — the connection lifecycle and result serialization, in ten
  sections: the client
  identity, the binding (S2/S3/S8: `..._stays_bound_without_an_active_transport`,
  `..._is_usable_over_stdio`, `test_equivalent_spellings_of_an_address_are_the_same_client`,
  `..._is_reachable_over_either_ip_stack`, `..._is_bound_to_its_mcp_session`,
  `test_client_identity_carries_the_session_id_too`), the idle timeout, then S5's
  `test_opening_a_connection_is_logged`, `..._refused_connection_use_is_logged`,
  `test_closing_an_idle_session_is_logged`, `..._session_that_fails_to_close_is_logged`,
  `test_a_failed_reaper_pass_is_logged`, `test_logging_never_breaks_its_caller`; S6's
  `test_a_connection_does_not_live_for_ever`, `test_the_lifetime_holds_without_a_reaper`,
  `test_the_reaper_drops_a_connection_nobody_comes_back_to`,
  `test_removing_a_connection_revokes_it`, `test_a_first_open_is_validated_too`; S7's
  `test_one_client_cannot_open_connections_without_end`,
  `test_the_server_as_a_whole_has_a_limit_too`,
  `test_an_expired_connection_does_not_hold_a_slot`,
  `test_a_connection_that_fails_to_open_gives_its_slot_back`,
  `test_db_connect_refuses_a_uri_that_is_not_configured`; the URI-spelling pair
  `test_db_connect_takes_a_uri_however_the_client_spelled_it` and
  `test_db_connect_refuses_a_uri_asking_for_more_than_is_configured`; T1's
  `test_closing_a_connection_beats_a_call_that_races_it` and
  `test_a_session_being_closed_is_not_replaced_underneath`), `test_server_binding` (**6**:
  loopback vs reachable vs wildcard host classification, the no-auth warning, the default
  staying quiet, the derived Host/Origin allow lists, and that serving over HTTP starts and
  stops the connection reaper, and that `start()` refuses a configuration it cannot serve),
  NEW `test_db_threading` (**1**,
  T2: a real session opened, used and closed across three threads), NEW `test_db_recovery`
  (**1**, T3: a real session KILLed from a second session and replaced on the next call).
  Plus NEW `test_setup_cli` (**44**: the option surface, the four password sources, the
  combination guards, ordering and `--show`/`--json`) and NEW `test_migrator_tools` (**37**: registration gating, config writing/merging/
  refusals, the configured-connections-only validation, password confinement, and the
  orchestrator invocation's shape) and NEW
  `test_migrator` (**33**: AIPL-21's 14 reworked for the versioned path, the
  platform predicate, the menu-renumbering test, the XDG data-home test, the
  side-by-side-releases test, and 15 for the venv/dependency/wrapper work) and one more in
  `test_config` (the Windows menu end-to-end). **192 pass, ~62s** — the venv builds and
  the CLI tests make it slower than the old ~39s.

- **The connection-scheme session added four tests and re-spelled many.** New in
  `test_config`: `test_a_connection_stored_without_a_scheme_still_resolves` (the
  whole backwards-compatibility story in one place — reported with the scheme,
  stored without, resolving either way, and re-configuring it dropping the old
  key), `test_filling_in_the_scheme_leaves_everything_else_alone` and
  `test_superseding_a_spelling_needs_one_it_can_recognize`. Two traps the
  re-spelling exposed, both of which will bite again:
  - **A test helper that lists connections and then DELETES or reads a password
    by what it listed must use `config.list_stored_connection_uris`**, not
    `list_connection_uris` — the second reports the scheme filled in, which is
    not the secret-store key. `_empty_both_connection_lists` in `test_config` and
    `test_gui_mode`, and `_backup_connections`/`_clear_connections` in
    `conftest`, all do. Getting it wrong fails with the shell's
    `RuntimeError: Failed to delete the secret: Could not find the secret`, on
    dozens of tests at once.
  - **A test that monkeypatches the configured list to stand in for the secret
    store must patch `list_stored_connection_uris`** — that is what
    `_resolve_in_kind` and `_open_session`'s re-validation read.
    `test_db_sessions` (5 sites) and `test_db_script` do.
  - `helpers.TEST_CONNECTION_URIS` and the sandbox fixture's `uri` are spelled
    `mariadb://...` so that what is stored is what `db.list_connections` reports;
    the `setup_cli` tests deliberately keep their INPUTS scheme-less, since that
    is what a provisioning script writes.

- **Coverage: TOTAL 97% (1535 statements, 44 missed) — measured on a run with `.coverage`
  DELETED first**, on SDK 2.1.1 with `tool_registrar` restored. Per module:
  lib/migrator_functions **100**, lib/setup_cli **100**,
  lib/msm_functions 100, lib/tool_registrar **100**, lib/setup_migrator 99,
  lib/db_functions 98, lib/general 98, lib/server 98, lib/config 96,
  lib/setup_prompts 95,
  lib/sandbox_functions 88, lib/setup 87, server.py 81,
  general.py 73.
  **The trap below bit again this session**: the first run, without deleting `.coverage`,
  reported lib/setup **94** and lib/general 2-missed; the clean run says lib/setup **92** and
  lib/general 3-missed. Pass counts matched (92 both times), as always. Only ever write down a
  figure from a deleted-`.coverage` run. `lib/setup.py` rose 85 -> 92 on test_migrator's 14
  tests, so it is NO LONGER one of the two weakest modules; general.py (73) and server.py
  (81) are.
  **CORRECTION, and a trap to avoid repeating**: earlier figures in this file and in the
  T-series commit messages (up to "db_functions 100%, TOTAL 97%") were INFLATED.
  `run_tests.py` passes `--cov-append`, so `.coverage` ACCUMULATES across runs - including the
  revert-probe runs, which execute the suite against DELIBERATELY MODIFIED code and so light up
  branches the real code never takes. Pass counts were never affected; only coverage.
  **Delete `.coverage` before any run whose number you intend to write down** (plain
  `.coverage`, never the `.coverage*` glob - that matches `.coveragerc`).
  The two gaps that mattered are now CLOSED (they were: `db.connect`'s slot giveback when the
  first open fails, added in S7 with no test, and its "not a configured connection" raise; plus
  `server.start`'s three validation raises) - see the three tests named below. What is left is
  defensive and small: the bytes->hex branch, the JSON-parse fallback, `_drop_connection`'s
  already-gone return, `execute_sql_script`'s "exactly one of sql_script/file_path" raise, a
  blank statement in a script, and `_dialable_host_names`' unresolvable-hostname except.
  Note that subprocess coverage IS captured (`.coveragerc` sets `parallel`/`sigterm` and
  `run_tests.py` sets `COVERAGE_PROCESS_START`), so the stdio- and HTTP-driven tests DO count -
  the figure is not under-reporting for that reason. pytest-cov combines and removes the
  `.coverage.*` data files, so deleting plain `.coverage` really does give a clean run.

## Files that matter

- run_tests.py -> symlinks mcp_plugin + msm_plugin + mrs_plugin into a temp config home
  (`dot_mariadb_shell` under a `mcp_dot_mariadb_shell_*` temp dir), `pip install -r
  requirements.txt` (was an inline `pytest pytest-cov mcp` list — driving it off
  requirements.txt is what makes the `mcp < 3.0.0` pin actually bind), then runs pytest.
  Exits early if the install fails. `-k/--only` to filter, `-s/--shell`, `-u/--userhome`.
  `-e/--e2e` opts into the `e2e`-marked tests (it just forwards `--e2e` to pytest).
  .coveragerc omits msm/mrs/shell/site-pkgs.

- tests/conftest.py -> `pytest_addoption` (`--e2e`), the ordering-AND-e2e-skip hook
  (`pytest_collection_modifyitems(config, items)` — it takes `config` now, do not drop it
  back to `items` alone), fixtures (sandbox session, allowed_temp_dir, clean_config,
  stored_connections, non_interactive_shell).

- pytest-coverage.ini -> `addopts` plus the `markers` registration for `e2e`.

- tests/unit/helpers.py -> `call_tool` (has `elicitation_callback`), `mcp_session`,
  `list_tool_names` (what the server ADVERTISES, used for the group gate), `list_tools`
  (the same, with each tool's `input_schema` and `description`, by name - how the
  GUI-only `mcp_access` of `sandbox.deploy` is checked), `tool_payload`,
  `find_free_port`, `server_binary_available`, `shell_binary`, plus the streamable-http
  helpers: **`http_server` (subprocess + URL) and `http_client_session(url, headers=)` are
  now separate**, so one server can be driven by several clients and a client can send
  forged headers. `http_session` is a thin wrapper over both and is unchanged for callers.
  Extra headers reach the transport only via a pre-built client
  (`mcp_http_client(headers=...)` -> `create_mcp_http_client`, passed as `http_client=`) —
  SDK 2.0's `streamable_http_client` takes no `headers` argument. Passing the client in also
  lets a test mutate `client.headers` MID-SESSION (httpx merges them per request), which is
  how the S1 test forges a header without opening a second MCP session.
  `http_server(bind_host=...)` starts the server with a different `--host` while the yielded
  URL always dials 127.0.0.1 — only useful for hosts that still bind loopback, which is
  exactly what the S4 test needs (`LOCALHOST`).

## Gotchas / things not to repeat

- **`run_tests.py --only "a or b"` silently loses its quoting.** `main()` builds the pytest
  command as an f-string and runs it with `shell=True`, so a multi-word `-k` expression is
  re-split by the shell and pytest reports `file or directory not found: or`. Work around
  it by embedding the quotes (`--only="'a or b'"`), or drive pytest directly:
  `MARIADB_SHELL_USER_CONFIG_HOME=<home> <shell> --pym pytest -c mcp_plugin/pytest-coverage.ini
  --no-cov -q mcp_plugin -k "a or b"` with the plugin symlinked into `<home>/plugins`.
  Note that `-k test_db_sql` ALONE selects nothing useful: the sandbox those tests need is
  deployed by `test_sandbox_deploy`, so it has to be selected too or every db test skips
  with "sandbox was not deployed".

- **A pytest mark is NOT a `-k` pattern.** `run_tests.py --only=migration_e2e` selects the
  e2e test and it is then SKIPPED anyway, because the mark is checked in
  `pytest_collection_modifyitems` independently of `-k`. That is deliberate — only `--e2e`
  runs it — but it does mean "I filtered to it and it still did not run" is the expected
  behaviour, not a bug. Combine the two: `--only=migration_e2e --e2e`.

- **Registering the `e2e` marker in `pytest-coverage.ini` is not optional bookkeeping** —
  without the `markers =` entry every run prints a `PytestUnknownMarkWarning`, and the
  suite is otherwise warning-free, so it would be noise nobody reads.

- **`clean_config` BACKS UP AND RESTORES, it does not CLEAR.** The developer's own
  connections and the sandbox's `root@127.0.0.1:PORT`, plus the sandbox's allowed path,
  are all still present during a test using it. Assertions must be RELATIVE (membership,
  or a before/after snapshot), never `== []` or `== [mine]`. This cost 4 failing tests.
  `test_config.py` gets away with equality only because it calls its own `_clear_config()`.

- **`MARIADB_SHELL_USER_CONFIG_HOME` does NOT isolate the secret store.** Only
  settings.json follows it; connections are secrets and go to the shared store, so a
  hand-run CLI experiment writes into the DEVELOPER's real connections. One such probe
  did exactly that this session and had to be cleaned up with `--deleteConnections`.
  Use `clean_config` in tests, and clean up by hand after any live experiment.

- **There is NO async pytest plugin in mcp_plugin.** `async def test_...` is skipped with
  "async def functions are not natively supported". Wrap coroutines in `asyncio.run(...)`
  inside a SYNC test, which is what `test_msm.py` already does. This cost two failing tests.

- **`ToolError` vs `mysqlsh.Error` depends on WHICH GROUP, and it changed for migrator.**
  db/msm/sandbox wrap shell plugin functions: they raise `mysqlsh.Error`, and
  `tool_registrar` re-raises it as `ToolError` (which is what keeps the message from
  being stripped on SDK 2.1 - see the SDK-error gotcha)
  — so assert `ToolError` for a call through the registered wrapper and `mysqlsh.Error`
  for a direct call to the module-level function. That cost two failing tests when it
  was first learned. **`migrator_functions` is now the exception and has no such split**:
  it raises `ToolError` everywhere and registers without the wrapper (PR #19 review), so
  assert `ToolError` either way — applying the old either/or rule there would cost
  sixteen.

- **`str(mysqlsh.Error)` carries a `"Shell Error: "` PREFIX.** Asserting
  `"Could not X: <message>"` as one contiguous string fails; assert the two halves
  separately. This cost two failing tests.

- **A revert probe has to fail for the RIGHT reason** — read the failure text, never just the
  pass/fail count. Both T1 tests first failed under the probe on an `AttributeError:
  '_StubSession' object has no attribute 'run_sql'` and on an unrelated `mysqlsh.Error` that
  `close_session`'s own `except` swallowed, which would have "proven" the fix while testing
  nothing; `_StubSession` therefore has `run_sql` + `_StubResult`, so a call that is NOT
  refused runs to completion and the probe fails with `DID NOT RAISE`. And a
  **negative-control test needs its own probe in the OPPOSITE direction**:
  `test_an_ordinary_sql_error_keeps_the_session` cannot fail when T3's fix is removed, only
  when its predicate is made too broad — so that one took two probes.

- **`helpers.tool_payload` collapses a single-element list into the bare element** (and an
  empty list into None) — a one-row listing is NOT a list. Normalize in the test before
  iterating; this bit the list_objects assertions AND, one commit later, the deploy
  assertions. It WILL bite again.

- **Run the suite from the mcp_plugin dir with `/opt/homebrew/bin` on PATH.** Two runs this
  session died instantly on `can't open file '.../run_tests.py'` because the cwd was the
  repo root; without homebrew on PATH the sandbox deploy finds no `mariadbd`.

- **Filtering tests with `-k` breaks the db/msm/sandbox tests** — they depend on
  `test_sandbox_deploy` running first (conftest ordering hook + `sandbox.deployed` flag),
  and skip themselves if it didn't. Run the full suite to validate. For a targeted run that
  still needs the sandbox, `--only="'sandbox_deploy or <pattern>'"` works (the ordering hook
  puts the deploy first), but `test_sandbox_shutdown` is then deselected and the session
  fixture's best-effort finalizer does the teardown.

- **`run_tests.py` interpolates `-k` into a `shell=True` STRING** (`pattern = f"-k
  {args.only}"`, run_tests.py:130), so a pattern containing spaces is split by the shell and
  pytest dies with `file or directory not found: or`. The pattern needs its OWN quotes:
  `--only="'a or b'"`. Worth fixing to a proper argv list; not done yet.

- **A non-matching glob ABORTS the whole command under zsh** (`no matches found:
  .coverage.*`) — the shell here is zsh, not bash, so it does not pass the pattern through.
  A run "silently doing nothing" with only that message is this, not a test failure. The
  file to remove after a partial run is plain `.coverage` (still: never `.coverage*`, which
  matches `.coveragerc`).

- **`| tail -N` on a backgrounded run hides ALL output until it exits**, so polling the task
  output file mid-run shows an empty file and looks like a hang. Wait for the notification,
  or drop the pipe.

- **test client is plain `ClientSession(read,write)`** — happy-path tests pre-register their
  paths (via fixtures) so no elicitation fires.

- **tool_payload returns None for an empty list** (zero content blocks). Guard `or []`
  before `in`/iteration (bit test_sandbox_shutdown).

- **Don't delete `.coverage*` with a glob** — matches `.coveragerc`. Use `.coverage.*`.

- **pytest-dependency does not build in this env** — ordering is native (conftest hook +
  `sandbox.deployed` flag). Long runs: Bash `run_in_background: true` so a hang is stoppable;
  chained `sleep` polling is blocked by the harness — wait for the task notification.

- **A stale `.pyc` makes a rename look like it didn't apply.** After renaming the runtime
  home `dot_mysqlsh` -> `dot_mariadb_shell`, pytest kept printing the OLD path — the same
  temp dir across two runs, which is the tell (a fresh `TemporaryDirectory()` differs every
  run). The old path was baked into `co_filename` in `tests/unit/__pycache__/*.pyc`. Clear
  `__pycache__` and `.pytest_cache` before concluding an edit didn't take.

## Next steps

7. (Optional) Raise `general.py` (73%) and `server.py` (81%) coverage — now the two
   weakest modules. **`lib/setup.py` is no longer one of them**: AIPL-21's 14 tests took it
   from 85% to 92%, so the old wording here (84%, "the two weakest by far") is obsolete.

8. (Optional) `test_db_sql.py`'s module docstring still says "connect / execute_sql /
   close"; the flow now covers far more.
