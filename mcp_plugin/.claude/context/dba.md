# The dba group, and the task tools

The MCP side of the dba plugin (cloud deployments, see
[`dba_plugin/.claude/PROJECT_CONTEXT.md`](../../../dba_plugin/.claude/PROJECT_CONTEXT.md)):
the `dba.*` tools, the MCP API keys `mcp setup` stores, and the `task.*`
tools every background task is followed with.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). The util tasks are in
[util.md](util.md).

## Architecture / key decisions

- `lib/dba_functions.py` (`FUNCTION_GROUP_DBA = "dba"`, in
  `SUPPORTED_FUNCTION_GROUPS`, not in `MULTI_TENANT_FUNCTION_GROUPS`: the keys
  are the server's, so every tenant would reach the same vendor accounts).
  Registers nothing (a `log_event`) where `dba_plugin.lib` does not import.
- **Who gets what:** without `--gui` (an agent) only the reading tools -
  `dba.list_vendors`, `dba.list_vendor_object_support`,
  `dba.get_vendor_object_info`, `dba.get_vendor_object_settings_info`,
  `dba.validate_vendor_object_settings` (creates nothing), `dba.list_vendor_objects`,
  `dba.get_vendor_object_status` - with the `MCP:` keys. With `--gui` also
  `dba.set_api_key`, `dba.create_vendor_object`, `dba.update_vendor_object`,
  `dba.delete_vendor_object`,
  with the `GUI:` keys. Names: `dba.<verb>_<rest>` (`dba.list.vendor.objects`
  -> `dba.list_vendor_objects`).
- **Create, update and delete start a task** owned by the client
  (`tasks.owner(general.get_client_identity(ctx))`, so both tools take `ctx`)
  and return `{task_id, status}`; `DbaError` -> `tool_error`.
- **Server instructions** get a rule when dba is served and
  `setup_dba.is_available()`: never ask for a vendor API key; point to
  `mariadb-shell -- mcp setup --setDbaApiKey=<vendor> --dbaApiKeyStdin`.
- **`mcp setup`** (`lib/setup_dba.py`, `lib/setup_cli.py`):
  `--setDbaApiKey=<vendor>`, `--deleteDbaApiKey=<vendor>`, `--dbaProject`,
  `--dbaApiKeyEnv=<VAR>`, `--dbaApiKeyStdin`; menu entries "Add/Delete a cloud
  vendor API key" (after the paths, before the migrator). The key sources reuse
  `_password_from_stdin` / `_password_from_env` with the option name passed.
  Refused in multi-tenant mode. `configuration()["dba_api_keys"]` lists
  vendor/project, never keys (None without the plugin).
- **Task tools** (`lib/task_functions.py`): `task.get(task_id, since, wait_ms)`,
  `task.list(task_type)`, `task.cancel(task_id)`, registered in
  `build_mcp_server` once, when any of `general.TASK_FUNCTION_GROUPS` (util,
  dba) is served - not a group of their own. There are no `util.*` task
  tools and no aliases for them: the user's call, as the VS Code extension
  has not been released and moves to `task.*` itself.
- **`lib/tasks.py`** is a thin layer over the shell's `mysqlsh.task_manager`
  (shipped with the shell): `create(task_type, kind, title, client,
  max_running, attributes)`, `get`, `list_for(client, task_type)`,
  `start(task, work)` (a failed start -> `tool_error`), `owner(client)`; the
  util tasks use type `util`, max 4 running per client, attribute
  `connection_id`. The registry's own tests live in the shell.
- **A task counts from its start:** the task manager lists it, finds it and
  counts it against the owner's `max_running` from `Task.start()` on, not
  from `create()`, which only refuses one too many early. A task created and
  never started holds nothing - so a failure between the two leaves no
  phantom. A test of listing, ownership or the cap must start its tasks
  (`test_util.py`'s `_started`, work waiting on an event the test releases).

## Files that matter

- `lib/dba_functions.py`, `lib/task_functions.py`, `lib/tasks.py`,
  `lib/setup_dba.py`; `lib/setup_cli.py` (`DBA_API_KEY_OPTIONS`,
  `_check_dba_api_key_combination`, `_resolve_dba_api_key`).
- `tests/unit/test_dba.py` (stub vendor `stubcloud`, a stdio `SimpleNamespace`
  ctx, `_ended()` waits for a task), `tests/unit/test_util.py` (of the
  registry only what the MCP side adds: client ownership, the cap and a
  failed start as tool errors).

## Gotchas / things not to repeat

- The MCP server itself runs as a command-line call (`mcp start-server`), so
  `task_manager.answer()` matches the CLI function by NAME: the dba tools never
  block. Do not reintroduce a plain "this is a CLI call" flag.
- `task.list` in the shell and `task.list` here see different tasks: the
  shell's own (owner None) vs. the client's. An MCP server never waits for
  its tasks at exit.
- The `--gui` server's `owner` for stdio is `ClientIdentity(None, None, None)`
  normalized - not None - which is what keeps the exit wait away from it.
- Ctrl+C on the HTTP server prints `ERROR: KeyboardInterrupt ...` and exits 1:
  uvicorn's doing, not the shell's command-line interrupt handler (the same
  without it).

## Next steps

- The vscode-extension moves to the `task.*` tools: `src/mcp/utilApi.ts`
  (`util.get_task` etc.), `src/util/taskMonitor.ts`, their tests, the
  task_type in the Tasks view, its minimum shell version. No `util.*` fallback.
