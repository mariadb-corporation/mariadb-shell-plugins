# Working practices

How to work on this plugin rather than what it does: keeping this context
current, what auto mode may not do, and the Windows VM and codex quirks that
look like plugin bugs and are not.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md).

## Gotchas / things not to repeat

- **Rene's PR #30 review: keep the MCP layer a thin interface to the database, and
  don't over-build.** Three rounds of review corrected the same tendency: parsing
  `str(get_type())` and making up a `BLOB` type (use `get_type().data`, and pass the
  flags through for the client to interpret); a getter table reporting eleven Column
  fields when the only consumer reads two (`_column_metadata` is now just `type` and
  `flags`); separate `additional_result_sets` instead of one `result_sets` list. Before
  adding a field, a table or a derived value, check what a consumer actually reads, and
  pass shell data through rather than interpreting it. His other standing rule, on
  exceptions (`ToolError` in tool code), is in server.md's Architecture section.

- **`/checkpoint` needs its target folder** — invoked bare it must ask, but this session
  is non-interactive; target was inferred as `mcp_plugin` from the session's work.

- **`/checkpoint` says "overwrite" — do NOT take that literally on this file.** It is a
  MULTI-SESSION accumulation (1300+ lines); rewriting it with only the current session's
  summary would destroy the S1..S8 / T1..T9 record, the architecture rationale and these
  gotchas. Update the stale parts and ADD the new material instead. This session did exactly
  that, on purpose.
  **FIXED AT THE SOURCE on 2026-09-21**: `.claude/commands/checkpoint.md` no longer says
  overwrite. It now branches on the layout it finds and states outright that a checkpoint
  is an update, not a regeneration, so the warning above is the behaviour the command
  asks for rather than a deviation from it. The same checkpoint split this context into
  `context/`, which is the layout that command's branch A expects.

- **This file went stale and cost the session's opening minutes.** It described the AIPL-16
  era while HEAD was an AIPL-21 commit, so the branch name, the test count, the coverage
  table and the "weakest modules" list were all wrong, and the migrator work was invisible.
  When asked "what is the status of X", check `git log` and the tree BEFORE trusting this
  file, and say plainly that it is stale rather than answering out of it.

- **This file goes stale fast** — the previous checkpoint listed 3 "next steps" that were
  all already committed, claimed the introspection tools were UNCOMMITTED when they were in
  3482634a, and carried an SDK-1.x threading gotcha that 2.0 reversed. Re-check `git log`
  and the installed SDK against it before trusting it.

- **A force-push is not yours to make in auto mode.** `git push --force-with-lease` was
  blocked by the permission classifier this session. The right response is to STOP and hand
  the user the exact command — never to look for another route to the same effect.

- **Windows Credential Manager DOES NOT WORK over SSH, and it looks like a broken
  install.** On the VM `shell.options["credentialStore.helper"]` reads `<invalid>` and
  `list_credential_helpers()` returns `[]`, so every `config.store_connection` /
  `list_connection_uris` fails with "current credential helper is invalid". The helper
  binary IS present (`bin/mariadb-secret-store-windows-credential.exe`); running it
  directly gives **error 1312, "a specified logon session does not exist"** — the classic
  Credential Manager refusal under a NETWORK logon (type 3), which is what SSH gives. It
  would work from an interactive desktop session. Do not chase this as a plugin or
  packaging defect, and do not try to verify `db.connect` over SSH on that box.

- **PowerShell 5.1 strips double quotes out of arguments to a native exe.** Two runs were
  lost to it: a `codex exec` prompt containing `"SELECT VERSION()"` got split into extra
  arguments, and `-c mcp_servers.x.args=["a","b"]` arrived as `[a,b]` and failed TOML
  parsing. Fixes that work: pass long text on **stdin** (`Get-Content -Raw f | codex exec
  … -`), and use TOML **single-quoted literal strings** (`args=['a','b']`, `command='C:\…'`)
  which need no escaping and survive the mangling.

- **`codex exec` refuses MCP tool calls under its default approval policy** — every call
  comes back "MCP tool call requires approval, but approval policy is never", which reads
  like a broken server. `--approve-for-me` is the sanctioned fix; it cannot be combined
  with `--sandbox`. Reach for `--dangerously-bypass-approvals-and-sandbox` only if that
  fails. Also: the MCP server occasionally fails to expose ANY tools for one run — retry
  before diagnosing. Confirm the server itself is healthy by piping
  `initialize`/`initialized`/`tools/list` JSON-RPC straight into
  `mariadb-shell -- mcp start-server --transport=stdio`.

- **A `@plugin_function` docstring line that ENDS IN A COLON breaks the whole plugin.**
  The shell's docstring parser takes it for a section header ("ERROR: Invalid format:
  section without content: …"), the plugin does not load, and EVERY test then errors
  with `ModuleNotFoundError: No module named 'mcp_plugin'`. Hit on 2026-10-06 with "…by
  any of their identities:". Reword the sentence; never end a description line with `:`.

- **The shell's camelCase capitalizes every word**, so `add_oauth_client` is
  `--addOauthClient`, never `--addOAuthClient`. The shell refuses the latter as an
  invalid option. Check `mariadb-shell -- mcp <command> --help` before documenting an
  option name.

- **`run_tests.py -k` pastes the pattern UNQUOTED into a shell command**, so
  `-k "a or b"` silently runs nothing ("file or directory not found: and"). Run one
  single-word pattern per call. `-k` runs also skip `test_sandbox_deploy`, so every
  sandbox-dependent test fails or errors there (`MySQL Error (2002)`); only a full run
  judges those.

- **The test runner isolates the secret store, a manual smoke test does not.**
  `run_tests.py` uses a temp `MARIADB_SHELL_USER_CONFIG_HOME` and the plaintext
  helper, so tests never touch the developer's keychain or users. For a manual run
  through the real shell, do the same:
  - a scratchpad home, with `mcp_plugin` AND `msm_plugin` symlinked into its
    `plugins/` (the default groups need msm: without it the server dies with
    `No module named 'msm_plugin'` and curl waits for ever)
  - `shell.options.set_persist('credentialStore.helper','plaintext')`

  Remove the home by its LITERAL path: `rm -rf $(…)` is blocked by a safety check.

- **A throwaway `mariadbd` for OAuth sign-in tests:**
  - `mariadb-install-db --auth-root-authentication-method=normal`
  - start it FROM its datadir with a relative `--socket=s.sock`, since the scratchpad
    path exceeds the Unix socket path limit
  - DROP the anonymous `''@'localhost'` / `''@'<hostname>'` accounts, or `'ada'@'%'`
    logins fail with 1045, because the anonymous account matches first

- **Test with a real MCP client before calling a transport feature done.** Claude Code
  speaks MCP 2026-07-28, which has NO sessions; no test client did, so the S3
  session-id rule refused every Claude Code `db.connect` (M22) while 560 tests passed.
  `claude -p "…" --mcp-config mcp.json --strict-mcp-config --allowedTools mcp__<name>`
  (with a `headers` entry for an API key) drives it headless. Its OAuth sign-in needs a
  person at the browser.

- **Import `httpx2`, never `httpx`.** MCP SDK 2.3 depends on `httpx2`; the shell ships no `httpx`. A local build that still had `httpx` 0.28.1 (left over from an older SDK, and pulled in by manually installed `anthropic`/`openai`) hid this, and PR #37 failed CI on collection. The local build's `site-packages` was cleaned on 2026-10-06 to match `build/bundled-python-deps` exactly; after a `--pym pip install`, run `msh --pym pip check` and check for duplicate `*.dist-info` folders.
