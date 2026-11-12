# Extension groups: MCP tools of other shell plugins

Any shell plugin offers a function as an MCP tool with
`@plugin_function(..., mcp={"group": ...})`; this plugin serves it. Built on
branch `mcp-extension-tools` in both repos (2026-11-12), from the plan the user
approved (artifact https://claude.ai/artifact/V9zamb6H2gtrhTM4ejfwmX).

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The server wiring it plugs
into is in [server.md](server.md), the sessions in [connections.md](connections.md),
the tasks in [util.md](util.md) and [dba.md](dba.md).

## Architecture / key decisions

- **Declared in the shell, not here** (user decision). mariadb-shell's
  `registrar.py`: `plugin_function(..., mcp=None)` -> `FunctionData.mcp`, checked
  and completed by `_mcp_definition` at load: keys `group` (required),
  `name` (default `_python_name` of the FQN's last part), `readOnly`,
  `destructive`, `task`, `gui` (bools), `paths` (named parameters, not
  `session`/`task`). `mcp=True` is refused. A mistake fails the function's
  registration like a docstring mistake does. Shell test:
  `plugin_decorator_norecord.py`, the three `MCP` chunks.
- **Discovery** (`lib/extension_functions.py`): `registrar.get_registry()` read
  when a server is built; `getattr(definition, "mcp", None)` so an older shell
  reads as none. `_registry()` is the one seam the tests patch.
- **Group names**: the user's choice, `mcp={"group": ...}`. A built-in group or
  `task` is never an extension group (`_RESERVED_GROUPS`): left out of
  `available_groups()`, refused by `check_group_name`, logged at build.
- **Switching on: all three ways** (user decision): `--functionGroups` names one
  for a run (an unknown name is an error listing the groups offered);
  `settings.json` `extensionGroups` is served when no groups are named
  (`config.get/set_extension_groups`); `mcp setup --extensionGroups=<list>` and
  the menu entry "Switch an extension group on or off" (only shown where a group
  is offered or set) edit it. A set group no plugin offers is skipped with a log
  line (`server._default_extension_groups`), not an error. `--show` reports
  `extension_groups: {served_by_default, available}`.
- **Multi-tenant serves none** (first version): `_check_multi_tenant` refuses an
  extension group with its own message, and the multi-tenant default never adds
  them.
- **Publishing** (`ExtensionTool`): the SDK builds the schema from the
  function's signature and type hints and calls it with keywords, so
  `_make_tool_function` WRITES OUT an async function with `exec` - `ctx:
  Context`, then every published parameter keyword-only (so required/optional
  order cannot clash), types from the docstring's `(type)` via `_TYPES`
  (`Optional[...]` for a `None` default, `Any` untyped). Description = brief,
  details (shell markup `<b>`/`@li` made plain), an `Args:` block of the
  PUBLISHED parameters (connection_id line, `options` with its Keyword Args),
  the task sentence. Over 4,096 characters is logged.
- **Filled-in parameters**: `session` -> published `connection_id` (required,
  first), call wrapped in `db_functions.use_session(connection_id, client)` on a
  worker thread; served only with the db group, else logged and left out.
  `task` (only with `mcp.task`) -> the shell `Task`. `**options` -> `options`
  object spread back; an option naming a real argument is refused. `ctx`,
  `connection_id`, `options`, `_x` as plugin parameter names, `*args` and
  positional-only parameters make the tool unservable (logged).
- **Order inside a call**: `require_allowed_path` for each `paths` parameter
  first (it may elicit, so never under a connection's lock), then the session.
- **Tasks**: `tasks.create(group, name, tool_name, client, 4, {"tool",
  "connection_id"})`; `session_opener` resolved before the task starts (a wrong
  connection fails the call), a fresh session per task, closed in `finally`.
  `register_extension_tools` returns whether a task tool was served, and
  `build_mcp_server` then registers the `task.*` tools too.
- **Results** go through `_plain`: run_sql results -> list of row dicts, the
  shell's Dict/List, dates (isoformat), Decimals (str), anything else `str`.
- **Errors**: the plugin raises `mysqlsh.Error`; `tool_registrar` converts it
  (client sees `Shell Error: ...`). Our own refusals use `tool_error`.
- **Logging**: one `[mcp] server: serving extension tool <tool> of <FQN>` line
  per tool, one `... not served: <why>` per refusal. `gui` tools without
  `--gui` are left out silently.

## Files that matter

- mariadb-shell `python/packages/mysqlsh/plugin_manager/registrar.py`
  (`_mcp_definition`, `_python_name`), refreshed into the build by the
  `python_extensions` target.
- `lib/extension_functions.py` - everything above.
- `lib/server.py` - `start()` (available groups read once, defaults, unknown
  names), `build_mcp_server` (built-ins by `_FUNCTION_GROUP_REGISTRARS`, the
  rest as extensions), `_default_extension_groups`, `_check_multi_tenant`.
- `lib/config.py` `get/set_extension_groups`; `lib/setup_cli.py`
  `_set_extension_groups`, `_extension_groups`, `_print_extension_groups`;
  `lib/setup.py` `_extension_groups_menu_entries`, `_toggle_extension_group`.
- `tests/unit/test_extension_tools.py` (21) and the fixture plugin
  `tests/fixtures/extension_plugins/mcp_ext_test/` (group `ext_test`).
- Docs: README "Tools of other shell plugins (extension groups)"; mariadb-shell
  `docs-ref/.../extending-mariadb-shell/plugins.md` "Offering a Function as an
  MCP Tool" and `.claude/skills/create-shell-plugin/SKILL.md`.

## Gotchas

- **`MARIADB_SHELL_PLUGINS_PATH` replaces the user plugin folder.** The stdio
  tests add the fixture folder to it (`fixture_plugin` fixture), and include the
  user folder (where run_tests.py links mcp_plugin) when it is unset. The shell's
  `plugin_decorator_norecord` test fails wholesale in a terminal that exports
  it: run the shell tests with `env -u MARIADB_SHELL_PLUGINS_PATH -u
  MARIADB_SHELL_DISABLE_BUILTIN_PLUGINS`.
- **Shell scripted tests run line by line**: a `def` with blank lines in its
  body ends early (IndentationError on the next lines). The shell test uses a
  template string and a loop instead.
- One row comes back from `helpers.tool_payload` as the row itself (one content
  block per list element).
- `FunctionData(function, fully_qualified_name=..., shell=False, mcp=...)` builds
  a registry entry without touching the shell's objects: what the tests use.

## Next steps

- A demo plugin, as a separate project (user decision): `session`, `paths`,
  `task` and `**options` once each, with a README.
- The ai-plugins copies of `create-shell-plugin` (`additional-skills`, vendored;
  re-run `sync-skills.sh`) do not have the `mcp=` section yet.
- Later, not in v1: tenants, server instructions from extensions, moving the
  wrapper modules (`rest_functions.py` and others) onto this, elicitation from
  plugin code.
