# dba_plugin — Project Context

## Project

`dba_plugin` registers the shell global `dba`: deploy and manage MariaDB Server
and Galera Cluster deployments on cloud vendors. Each vendor is its own plugin,
`dba_vendor_<name>_plugin` (siblings in this repo: `hetzner`, `upcloud`,
`mariadb_cloud`), adding `dba.vendor.<name>` and implementing the interface in
`lib/vendor.py`. The MCP plugin serves the functions as the `dba` group (see
[`mcp_plugin/.claude/context/dba.md`](../../mcp_plugin/.claude/context/dba.md)).
Built 2026-10-30 on branch `wip/dba_plugin`; version 26.10.4 with the rest.

## Architecture / key decisions

- **Direct REST, no vendor SDKs** (user's choice): the SDKs need `requests` and
  `python-dateutil`, which the shell does not bundle; MariaDB Cloud has none.
  One client, `lib/rest_client.py` on the bundled `httpx2`: retries (429
  always; 502/503/504 and connection errors for idempotent methods,
  `Retry-After` up to 30 s), the vendor's own error message, never a header in
  a message. `last_headers` carries MariaDB Cloud's next-page token header.
- **Functions** (shell names; MCP: `dba.<verb>_<rest>`): `dba.list.vendors
  ([featureSet])`, `dba.set.apiKey`, `dba.list.vendor.objectSupport`,
  `dba.get.vendor.objectInfo`, `dba.list.vendor.objects([tags, project])`,
  `dba.get.vendor.objectStatus`, `dba.deploy.vendor.object`,
  `dba.delete.vendor.object`. Feature sets `ManagedService`, `CoreCompute`;
  standardized object types in `lib/object_types.py`.
- **API keys** in the shell secret store: `GUI:DBA_API_KEY:<vendor>:<project>`
  (shell functions, `--gui` MCP) and `MCP:DBA_API_KEY:...` (`mcp setup`). A
  project is native for Hetzner (one token per project); for the others it is
  the label `dba_project=<project>`, and the `default` key serves every project.
- **Load order:** the shell loads plugin folders in unspecified order and
  refuses registering a function twice, so `__init__.py` files import nothing,
  and a vendor plugin's `init.py` calls `objects.register_vendor_plugin`, which
  creates `dba` / `dba.vendor` if missing (`ensure_parent_objects`).
- **Deploy and delete are background tasks** of the shell's
  `mysqlsh.task_manager` (mariadb-shell `wip/task_plugin`), type `dba`
  (`lib/operations.py`):
  - Checked at once: vendor, key, type support (`vendor.require`), the
    generic settings (`check_settings`: name, known keys). Vendor-specific
    rules (e.g. Hetzner's ssh_keys) fail the task instead.
  - A deploy ends when the status is ready (`READY_STATUSES`) or stopped
    (`off`/`stopped`); up to 1 h. A delete ends when the object is gone (404
    or not listed) or `deleted`; up to 30 min. A failed/error status fails it.
  - Polls every 10 s by id (`vendor.current_status`: `_status_<type>`, a 404
    = gone, else listing); up to 6 failed polls in a row are warnings.
  - One task per object: claims by name AND id, project None = `default`,
    type normalized; released when the task ends or fails to start.
  - Cancelling stops the waiting, not the vendor.
- **Command line:** `dba deploy vendor object` / `dba delete vendor object`
  are `cli=True` and return through `task_manager.answer(task, fqn)`
  (`objects.task_answer`): as THE command-line call they follow the task
  (statuses on stderr, result printed, error = exit 1, Ctrl+C cancels, 130);
  anywhere else they return `{task_id, status}`. Scripts wait for running
  tasks at exit; an interactive `\quit` only warns.
- Vendor objects get the same functions without the vendor name
  (`dba.vendor.<camel>.deployObject` etc., `cli=False`).
- **Architectures** normalized to `x86_64` / `arm64` (`vendor.architecture`).
  Objects without vendor state report `STATUS_ACTIVE` ("active").
- **Deleting a VM keeps its volumes** (contract in `lib/vendor.py`). UpCloud
  deletes with `storages=1`, so it detaches every disk but the system one first
  (`_system_disk`: `boot_disk` "1", else `part_of_plan` "yes", else the first
  on the bus - UpCloud reports `boot_disk` "0" on the system disk).

## Current state

- Built and tested: core + 3 vendors; 182 pass, 3 skipped (the live tests).
  Run: `cd dba_plugin && mariadb-shell --py -f run_tests.py [--vendors=a,b]
  [--live] [-k ...]` with `/Users/mzinner/git/mariadb-shell/build/bin` first on
  PATH (needs `mysqlsh.task_manager`).
- Live-verified 2026-10-30:
  - Hetzner: every read; deploy/delete of SSH key, network, firewall, `cax11`
    VM, volume (detach and firewall removal paths); task round trips.
  - UpCloud: every read; network, STARTER VM, volume, load balancer, object
    storage; the volume-keeping VM delete.
  - MariaDB Cloud: every read; a serverless service (11.8.9,
    `serverless-standalone`, aws eu-central-1) deployed and deleted. Dedicated
    services answer HTTP 402 (no payment method on the account).
- Commits on `wip/dba_plugin` (pushed): a5e67046 (the plugins), 72ae57eb
  (simplify), c4a6d072 (tasks), 90fdd59d (CLI waits); plus the code-review
  fixes and the adaptations to e0acbc7c7 of this checkpoint's commit.

## Files that matter

- `lib/vendor.py`: the vendor contract (module docstring), `Vendor`,
  `make_object`, `check_settings`, `SETTINGS`, `READY_STATUSES`,
  `ObjectNotFound`, `current_status`, `delete_found`, `require`.
- `lib/operations.py`: shared by shell and MCP; the task machinery.
- `lib/objects.py`: shell object registration, `shell_call`, `task_answer`.
- `lib/api_keys.py`, `lib/vendors.py` (discovery of `dba_vendor_*_plugin`),
  `lib/rest_client.py`, `functions.py`, `init.py`.
- `run_tests.py`, `tests/fixtures.py` (`MockApi`, `mock_api`, `--live`,
  `DBA_TEST_<VENDOR>_API_KEY`), `tests/unit/test_core.py`.
- `../dba_vendor_<name>_plugin/vendor.py` + `tests/unit/test_<name>.py`.

## Next steps

1. Code reviews (user's plan) of this branch and mariadb-shell `wip/task_plugin`.
2. Step 3: vscode-extension PR moving to `task.*` (see the MCP dba.md).
3. Not done, from reviews: one pager for the three vendors; a common status
   vocabulary next to the vendor's; vendor-specific settings checked before
   the task starts; `object_info` requests in parallel; a client per vendor
   kept open in the MCP server; server-side project label filters; UpCloud
   object storage delete reports `deleted` while it is still going.
4. The mariadb-shell release workflow must bundle the four new plugins.
5. Unverified: UpCloud monthly price = hourly x 672; MariaDB Cloud required
   create fields beyond the serverless case; Hetzner LB deploy live.

## Gotchas / things not to repeat

- Test tokens: `~/.config/dba-test/{hetzner,hetzner_rw,upcloud,mariadb_cloud}`
  (mode 600). Never print or paste them; read with `$(cat ...)` into
  `DBA_TEST_<VENDOR>_API_KEY`, or from a script. `hetzner_rw` is for the
  throwaway project `dba-test`; ask before any deploy that costs money.
- 2026-10-30: an UpCloud system disk `dba-rt-vm disk` (10 GB, de-fra1,
  `01df7093-44b4-46b1-a226-6231e076fd63`) was orphaned by a first, wrong
  `_system_disk` fix; its deletion was blocked by the permission check. Ask
  the user whether it is gone.
- Docstrings are parsed: a line ending in `:` is a section header (also inside
  Args), and every parameter must be documented.
- Through the shell global a `mysqlsh.Error` arrives as `RuntimeError`.
- Deploy settings on the CLI: `--name=db1 --size=...` map into the `settings`
  dict; the output's error reads `Error: Shell Error: ...` (the shell's format).

## Git state

Checked at this checkpoint (2026-10-30), before its commit:

```text
$ git branch --show-current
wip/dba_plugin

$ git status --short   (one repository: every project's lines)
 M dba_plugin/lib/operations.py
 M dba_plugin/lib/vendor.py
 M dba_plugin/tests/unit/test_core.py
 M mcp_plugin/.claude/PROJECT_CONTEXT.md
 M mcp_plugin/.claude/context/server.md
 M mcp_plugin/.claude/context/setup.md
 M mcp_plugin/.claude/context/testing.md
 M mcp_plugin/.claude/context/util.md
 M mcp_plugin/lib/tasks.py
 M mcp_plugin/lib/util_functions.py
 M mcp_plugin/tests/unit/test_dba.py
 M mcp_plugin/tests/unit/test_util.py
?? dba_plugin/.claude/
?? mcp_plugin/.claude/context/dba.md
```

- All of it is committed and pushed with this checkpoint, on `wip/dba_plugin`
  (code-review fixes, the adaptation to mariadb-shell e0acbc7c7, the context).
- `wip/dba_plugin` has no PR yet: code reviews first, then the vscode-extension
  step (step 3).
- mariadb-shell `wip/task_plugin` (pushed, clean): d9aa4fe5e, 662919d49,
  373562984, a7ac48018, e0acbc7c7 (the last one by another session). The
  plugins need it; shell and plugins are always released together.
