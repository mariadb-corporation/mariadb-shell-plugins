# dba_plugin — Project Context

## Project

`dba_plugin` registers the shell global `dba`: deploy and manage MariaDB Server
and Galera Cluster deployments on cloud vendors. Each vendor is its own plugin,
`dba_vendor_<name>_plugin` (siblings in this repo: `hetzner`, `upcloud`,
`mariadb_cloud`), adding `dba.vendor.<name>` and implementing the interface in
`lib/vendor.py`. The MCP plugin serves the functions as the `dba` group (see
[`mcp_plugin/.claude/context/dba.md`](../../mcp_plugin/.claude/context/dba.md)).

## Architecture / key decisions

- **Direct REST, no vendor SDKs** (the user's choice): the SDKs need `requests`
  and `python-dateutil`, which the shell does not bundle; MariaDB Cloud has
  none. One client, `lib/rest_client.py` on the bundled `httpx2`: retries (429
  always; 502/503/504 and connection errors for idempotent methods,
  `Retry-After` up to 30 s), the vendor's own error message, never a header in
  a message. It keeps no request state, so threads share it;
  `get_with_headers` returns MariaDB Cloud's next-page token header.
- **Shared machinery in `lib/vendor.py`:** `paginate(fetch)` for every vendor's
  paging (page number, offset, header token); `parallel(*calls)` runs an
  offering's independent info requests side by side; `state_of(status)` gives
  every object a `state` (ready, pending, stopped, deleting, failed, unknown)
  next to the vendor's `status`, and the task waits use it.
- **Settings** are declared per vendor and type (`create_settings`: required,
  supported) with static rules spanning settings in `_check_<type>(settings)` (a
  single value is checked by its field, and arrives typed);
  `check_create_settings` runs both before a create task starts and in
  `_create_<type>`. Rules needing a request (a size offered) fail the task.
- **Update** takes only the settings that change, each `is_updatable`
  (`fields()` forces it False where the vendor has no `_update_<type>`):
  `check_update_settings` / `update_problems` (+ optional
  `_check_update_<type>`), then `_update_<type>(obj, settings)` returns the
  object as the vendor answers (no extra GET: the task polls); `update_found`
  checks `grows_only` fields against the object (`growth_problems`) and keeps
  `dba_project` in new labels. Validation with an object name checks an
  update, filtering choices by what the object has for the settings their
  `filter` rules name (`current_value`: key, else property).
- **Settings for a dialog:** every standardized setting is a field in
  `lib/settings_fields.py` (caption, group, type, range, `choices_from`,
  `filter`, `display`); `SETTINGS` descriptions derive from it. A vendor's
  `settings_fields` changes attributes of standardized keys and adds its own
  settings (`vendor_specific`, moved into `vendor_settings` at `vendor_key`
  by `settings_problems`); `settings_order`/`settings_groups` follow its
  console. `Vendor.settings_info` fills choices from `_info_<type>` and the
  deployed objects (side by side); `Vendor.validate_settings` collects
  every request-free problem (`settings_problems`, keys from `SettingError`),
  then checks values against the choices, honouring `filter`. The shell sorts
  dict keys, so clients order settings by `position`. Hetzner's VM dialog
  mirrors its console (sizes table grouped by cpu_type/category, per-location
  prices and availability, images by os_flavor/os_version, IPv4/IPv6,
  networks by network zone, volumes, placement group, label rules,
  32 KiB cloud-init).
- **Label filters on the vendor's side** where verified: a `_list_<type>`
  taking `labels=` gets the tag and project filter (Hetzner `label_selector`
  everywhere; UpCloud `label` on servers, storages, networks). The core
  filters the answer anyway. Not for UpCloud load balancers and object
  storage or MariaDB Cloud: their syntax is unverified, and a filter read
  differently would drop objects the core cannot bring back.
- **Reading operations share an open vendor** per vendor, project, key
  context and API key (`operations._shared_vendor`), so its connections stay
  open between calls; a new key gets a new vendor. Tasks open their own.
- **Functions** (shell names; MCP: `dba.<verb>_<rest>`): `dba.list.vendors
  ([featureSet])`, `dba.set.apiKey`, `dba.list.vendor.objectSupport`,
  `dba.get.vendor.objectInfo`, `dba.list.vendor.objects([tags, project])`,
  `dba.get.vendor.objectStatus`, `dba.create.vendor.object`,
  `dba.update.vendor.object`, `dba.delete.vendor.object`. Feature sets `ManagedService`, `CoreCompute`;
  standardized object types in `lib/object_types.py`.
- **API keys** in the shell secret store: `GUI:DBA_API_KEY:<vendor>:<project>`
  (shell functions, `--gui` MCP) and `MCP:DBA_API_KEY:...` (`mcp setup`). A
  project is native for Hetzner (one token per project); for the others it is
  the label `dba_project=<project>`, and the `default` key serves every project.
- **Load order:** the shell loads plugin folders in unspecified order and
  refuses registering a function twice, so `__init__.py` files import nothing,
  and a vendor plugin's `init.py` calls `objects.register_vendor_plugin`, which
  creates `dba` / `dba.vendor` if missing (`ensure_parent_objects`).
- **Create, update and delete are background tasks** of the shell's
  `mysqlsh.task_manager` (shipped with the shell, as is the `task` global),
  type `dba` (`lib/operations.py`). The shell and the plugins are released
  together, so the plugin needs no fallback for a shell without it.
  - Checked at once: vendor, key, type support (`vendor.require`), the
    generic settings (`check_settings`: name, known keys). Vendor-specific
    rules (e.g. Hetzner's ssh_keys) fail the task instead.
  - A create (or update) task ends when the status is ready (`READY_STATUSES`) or stopped
    (`off`/`stopped`); up to 1 h. A delete ends when the object is gone (404
    or not listed) or `deleted`; up to 30 min. A failed/error status fails it.
  - Polls every 10 s by id (`vendor.current_status`: `_status_<type>`, a 404
    = gone, else listing); up to 6 failed polls in a row are warnings. A
    deleted UpCloud object storage is reported `deleting`: it stays listed
    for a while and answers a second DELETE with 500.
  - One task per object: claims by name AND id, project None = `default`,
    type normalized; released when the task ends or fails to start.
  - Cancelling stops the waiting, not the vendor.
- **Command line:** `dba create|update|delete vendor object`
  are `cli=True` and return through `task_manager.answer(task, fqn)`
  (`objects.task_answer`): as THE command-line call they follow the task
  (statuses on stderr, result printed, error = exit 1, Ctrl+C cancels, 130);
  anywhere else they return `{task_id, status}`. Scripts wait for running
  tasks at exit; an interactive `\quit` only warns.
- Vendor objects get the same functions without the vendor name
  (`dba.vendor.<camel>.createObject` etc., `cli=False`).
- **Architectures** normalized to `x86_64` / `arm64` (`vendor.architecture`).
  Objects without vendor state report `STATUS_ACTIVE` ("active").
- **Deleting a VM keeps its volumes** (contract in `lib/vendor.py`). UpCloud
  deletes with `storages=1`, so it detaches every disk but the system one first
  (`_system_disk`: `boot_disk` "1", else `part_of_plan` "yes", else the first
  on the bus - UpCloud reports `boot_disk` "0" on the system disk).

## Current state

- Core and three vendors built, with mock-API unit tests per vendor and
  read-only live tests (`--live`). Run: `cd dba_plugin && mariadb-shell --py
  -f run_tests.py [--vendors=a,b] [--live] [-k ...]` with a shell build that
  has `mysqlsh.task_manager` first on PATH.
- Verified live:
  - Hetzner: every read; create and delete of SSH key, network, firewall, VM,
    volume (the detach and firewall-removal paths), also as tasks.
  - UpCloud: every read; network, VM, volume, load balancer, object storage;
    the VM delete that keeps its volumes.
  - MariaDB Cloud: every read; a serverless service (`serverless-standalone`)
    created and deleted. Dedicated services need a payment method on the
    account (HTTP 402 without one).

## Files that matter

- `lib/vendor.py`: the vendor contract (module docstring), `Vendor`,
  `make_object`, `check_settings`, `settings_problems`, `applying_choices`,
  `SETTINGS`, `READY_STATUSES`,
  `ObjectNotFound`, `current_status`, `delete_found`, `require`.
- `lib/settings_fields.py`: the fields of the standardized settings, their
  groups, and the request-free value checks (`field_errors`).
- `lib/operations.py`: shared by shell and MCP; the task machinery.
- `lib/objects.py`: shell object registration, `shell_call`, `task_answer`.
- `lib/api_keys.py`, `lib/vendors.py` (discovery of `dba_vendor_*_plugin`),
  `lib/rest_client.py`, `functions.py`, `init.py`.
- `run_tests.py`, `tests/fixtures.py` (`MockApi`, `mock_api`, `--live`,
  `DBA_TEST_<VENDOR>_API_KEY`), `tests/unit/test_core.py`.
- `../dba_vendor_<name>_plugin/vendor.py` + `tests/unit/test_<name>.py`.

## Next steps

1. The vscode-extension: move from `util.*` to the `task.*` tools (see the MCP
   plugin's `context/dba.md`), then a dba UI on top of them.
2. The mariadb-shell release workflow must bundle the four new plugins.
3. Label filters on UpCloud load balancers and object storage and on MariaDB
   Cloud services, once their syntax is verified against objects that carry
   labels (their APIs accept `label` / `tags` parameters without an error).
4. Unverified: UpCloud monthly price = hourly x 672; MariaDB Cloud required
   create fields beyond the serverless case; Hetzner load balancer create live.

## Gotchas / things not to repeat

- Live test tokens never go into the conversation, a file in the repository or
  a command line: keep them outside the repository, readable only by the
  user, and pass them as `DBA_TEST_<VENDOR>_API_KEY="$(cat <file>)"`. Ask
  before any live create that costs money; use a throwaway vendor project.
- A first `_system_disk` that trusted `boot_disk` alone orphaned an UpCloud
  system disk: after any live UpCloud VM test, list the private storages and
  delete strays.
- Docstrings are parsed: a line ending in `:` is a section header (also inside
  Args), and every parameter must be documented.
- Through the shell global a `mysqlsh.Error` arrives as `RuntimeError`.
- Create settings on the CLI: `--name=db1 --size=...` map into the `settings`
  dict; an error reads `Error: Shell Error: ...` (the shell's format).
