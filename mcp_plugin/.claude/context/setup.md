# mcp.setup, interactive and not

The three interactive modules, the prompt primitives that use the shell's own
prompt types, and the command-line option surface that makes every menu item
scriptable.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The migration tooling
setup installs is in [migrator.md](migrator.md).

## Architecture / key decisions

- **`mcp.setup` is fully non-interactive-able: `lib/setup_cli.py`.** Every menu item is a
  command-line option. `setup()` became `setup(**options)`; `run_setup(**options)`
  dispatches on `setup_cli.has_options()`. Decisions:
  - **`has_options()` returns `bool(options)`, deliberately including UNKNOWN names.** A
    misspelled option must reach `_reject_unknown` and be refused; treating it as "no
    options" would start the walkthrough, which in a terminal-less script is a confusing
    way to learn about a typo. (The shell's own CLI parser refuses unknown options first,
    so `_reject_unknown` really guards the PYTHON API path, `mcp.setup(addPathz=...)`.)
  - **Four password sources, at most ONE per call**: `--passwordStdin` (first line;
    refused when stdin is a TTY), `--passwordEnv=<VARNAME>` (the NAME, so no secret in
    the command line; unset is an error, empty is an empty password), `--password`
    (discouraged), and a prompt. Giving two is refused rather than resolved by
    precedence. `--nonInteractive` turns the prompt case into an error, which is what
    keeps a CI job from waiting forever.
  - **A URI carrying a password is REFUSED**, not used and not dropped: normalization
    strips it (see `config.normalize_connection_uri`), so accepting it would store a
    connection with no password at all.
  - **Order is fixed**: deletions -> additions -> migrator. So delete+re-add in one call
    ends up added, and `--removeMigrator --installMigrator` is the REINSTALL idiom rather
    than a contradiction. Fail-fast, leaving what succeeded in place.
  - **Adding an already-configured connection UPDATES its password** and says "updated" —
    a provisioning script has to be safe to run twice. That now holds across the
    scheme change too: a connection configured before schemes were kept is stored under a
    scheme-less key, and adding it again writes the canonical key and DELETES the old one
    (`config.drop_superseded_spellings`), saying which spelling it replaced. Without that
    the two would sit side by side and resolve to neither.
  - `--show` is exclusive of the action options; `--json` only applies to `--show` and
    prints nothing else on stdout so the whole of it parses.
  - **`_cli_name()` renders option names as the generated help spells them** (camelCase:
    `--addPaths`). The shell accepts snake/kebab/camel alike, but the help lists only
    camelCase, so a message naming `--add_paths` sends the reader looking for something
    the help does not mention.
  - **`verify_connection()` lives in `setup_cli.py`, NOT `config.py`.** Moving it to
    config broke 3 tests: the interactive path verifies through the
    `setup_prompts.shell()` seam the tests fake, whereas `config._shell()` is the REAL
    shell there (config needs the real `parse_uri`/`unparse_uri`). Both setup paths must
    share the seam the tests patch.
  - `--migratorVersion` was deliberately NOT added (user's call): the pin being a code
    constant is what makes an install a property of the plugin version.

- **The interactive setup is THREE modules, not one** (refactored after the AIPL-21
  commit): `lib/setup.py` (connections, allowed paths, the menu, `run_setup`),
  `lib/setup_migrator.py` (everything migration) and `lib/setup_prompts.py` (the prompt
  primitives). The prompts module exists to break a cycle: `setup` imports
  `setup_migrator` to build its menu, so anything `setup_migrator` needed back from
  `setup` — and it needs `yes_no` — would have been circular. Injecting the prompt
  function into `manage()` was the alternative and was rejected: a shared primitive is the
  honest structure, and it also collapses the tests' patch points to ONE
  (`setup_prompts.shell`) instead of one per module.
  - Public names in `setup_migrator` dropped the now-redundant `migrator` prefix:
    `download`, `remove`, `archive_url`, `installed_version`, `is_installed`, `menu_label`,
    `print_status`, `manage`, plus the new `is_supported`. The private extraction helpers
    (`_download_archive`, `_archive_prefix`, `_archive_destination`, `_extract_archive`)
    kept theirs.
  - **The primitives use the SHELL'S OWN PROMPT TYPES, not hand-rolled text prompts**
    (PR #19 review). `shell.prompt` takes a `type` of
    `text | password | confirm | select | fileOpen | fileSave | directory`, and for
    `confirm` and `select` it renders the answers, applies `defaultValue` on an empty
    reply and RE-ASKS until the reply is valid - so none of that is reimplemented.
    Verified against a real shell, not just the docs (`shell.help("prompt")`):
    - `confirm` answers with the LABEL of the chosen option, **ampersand included** -
      `'&Yes'` / `'&No'` (hence `prompts.YES_LABEL` / `NO_LABEL`, compared against
      rather than assumed). `yes_no` lost its `" [Y/n]: "` suffix, its `.lower()`, its
      `("y","yes")` test and its empty-default branch.
    - `select` answers with the **TEXT** of the chosen option, not its index, and its
      `defaultValue` is the **1-BASED** index. So `select_index(message, count)` became
      **`select(message, choices, default=None)`**, mapping the text back to a position;
      the choices must be distinct or they could not be told apart.
    - **`select` has NO cancel**: it re-asks until it gets a valid index unless a
      default is set, so an empty reply does not back out. `select_or_cancel` appends
      `prompts.CANCEL_LABEL` as a further choice and maps it to `-1`, which is what
      keeps `if index < 0: return` working in the two delete flows.
    - The shell prints the numbered list itself (`  1) …`), so `_delete_connection` /
      `_delete_path` no longer pre-print one - they read the list without printing and
      let the prompt render it. `_print_connections` / `_print_paths` stay for the
      menu's information display.
  - **`_menu` is a select prompt too**, with `MENU_FINISH_LABEL` last and as the
    `default`, which is what preserves "an empty reply finishes". Its own range check,
    retry loop and `finish = len(entries) + 1` numbering are gone - the shell numbers
    whatever list it is given, so the Windows renumbering falls out for free and there
    is no printed menu to assert on any more (see Gotchas).
  - Prompt primitives renamed on the way out of `setup.py`: `_prompt` -> `prompts.ask`,
    `_prompt_password` -> `prompts.password`, `_prompt_yes_no` -> `prompts.yes_no`,
    `_select_index` -> `prompts.select_index`, `_shell` -> `prompts.shell`. Call them
    module-qualified (`prompts.yes_no(...)`), NOT `from ... import yes_no` — a bound
    name cannot be monkeypatched, and the whole suite drives these by patching the module.
  - `setup.py` still needs `import os` for the allowed-paths section. Dropping it while
    moving the migration code out was the refactor's one real breakage (4 tests, all
    `NameError: name 'os' is not defined`).

## Files that matter

- lib/setup.py -> the interactive `mcp.setup`: connections, allowed paths, `_first_run`,
  `_menu_entries` / `_menu` (built, not written out — see Architecture) and `run_setup`.
  Needs `import os` for the paths section. 87% covered.

- lib/setup_cli.py -> the non-interactive `mcp.setup`: `apply()` (the fixed-order
  dispatcher), `has_options`, `_reject_unknown`, `_check_combination`, `_resolve_password`
  and the three sources, `_add_connection`/`_delete_connections`/`_add_paths`/
  `_delete_paths`, `_install_migrator`/`_remove_migrator`, `configuration()`/`_show`,
  `_cli_name`, and `verify_connection` (shared with the interactive path).
  ACTION_OPTIONS / PASSWORD_OPTIONS / MODIFIER_OPTIONS / KNOWN_OPTIONS. **100% covered.**

- lib/setup_prompts.py -> `shell`, `ask`, `password`, `yes_no` (a `confirm` prompt),
  `select` / `select_or_cancel` (a `select` prompt), `YES_LABEL`, `NO_LABEL`,
  `CANCEL_LABEL`. **`select_index` is GONE** — it took a count, the replacement takes
  the choices, because the shell needs them to render and answers with the chosen TEXT.
  The ONE seam the interactive tests replace (`setup_prompts.shell`) to script a whole
  run. 95% covered.

- tests/unit/test_setup_cli.py -> the 44 CLI tests. Uses the `clean_config` fixture for
  anything that really stores a connection or a path.

## Gotchas / things not to repeat

- **Do NOT hand-roll a prompt the shell already has.** That was the PR #19 review, twice.
  `shell.prompt` does defaults, validation and re-asking for `confirm` and `select`; a
  text prompt with a `"[Y/n]"` suffix reimplements it and gets it subtly different. And
  when reading a prompt's answer, remember what it actually returns: `confirm` gives the
  LABEL with its ampersand (`'&Yes'`), `select` gives the option's TEXT and takes a
  1-BASED `defaultValue`. Both verified against a real shell.

- **The shell's `select` prompt cannot be cancelled.** It re-asks until it gets a valid
  index unless a `defaultValue` is set, so an empty reply does not back out — an
  explicit `CANCEL_LABEL` choice is the only way, which is what `select_or_cancel` adds.
  Do not "simplify" it away: two delete flows depend on `if index < 0: return`.

- **A select prompt renders its own numbered list, so do not print one first.** That is
  why `_delete_connection` / `_delete_path` read the list without printing it, while
  `_print_connections` / `_print_paths` stay for the menu's information display. Getting
  this wrong shows the list twice.

- **There is no printed menu left to assert on.** The management menu is a select
  prompt, so `capsys` sees only the shell's `  1) …` rendering, not anything the plugin
  printed — and with a FAKE shell it sees nothing at all. Assert on the CHOICES the
  prompt was given (`_FakeShell.select_prompts()`), which is the real contract now. Two
  tests failed on `'5. Finish' in menu` exactly this way.

- **`_FakeShell.prompt` must honour the `type` option** or the setup tests assert
  nothing: with a naive fake that pops a raw string, reverting `yes_no` to a hand-rolled
  text prompt still PASSES, because the terse `"y"` works either way. It now maps
  `''`/`y`/`n` to the default/`&Yes`/`&No` for `confirm` and a 1-based index to
  `choices[i-1]` for `select`, and `test_setup_first_run` asserts the prompt TYPES
  (4 `confirm`, 1 `password`) — which is what makes that revert fail. It deliberately
  does NOT emulate re-asking: a scripted invalid answer is a bug in the script, and an
  assertion says so instead of looping.

- **A faithful fake encodes YOUR reading of the shell's contract, so verify it live.**
  Every claim above about `confirm`/`select` was checked by driving a real
  `mariadb-shell` with piped input before the fake was written, and the interactive
  setup was then driven end to end the same way (`printf '2\n2\n5\nn\n\n' |
  mariadb-shell -- mcp setup`) to confirm the Cancel choice, the Finish default and a
  declined confirm all behave — the tests alone could not have shown that.

- **Do NOT patch `setup._shell` / `setup._prompt_yes_no` any more** — they are gone.
  The prompt seam is `setup_prompts.shell` (and `setup_prompts.yes_no` where a test needs
  to answer one specific question). Four tests broke on this during the refactor.

- **One revert probe was not enough for the Windows gate.** Neutralizing `is_supported()`
  failed only the predicate's own test; the menu tests patch `is_supported` directly and
  are structurally incapable of failing on it. Probe the LAYER each test actually covers
  (predicate, `_menu_entries`, `_menu`'s printed output) before claiming a test pins
  anything — same lesson as S7 and T1.
