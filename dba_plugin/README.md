# MariaDB DBA Plugin

This folder contains the **MariaDB DBA Plugin** for MariaDB Shell. It registers the global
`dba` object, which deploys and manages MariaDB Server and MariaDB Galera Cluster
deployments on cloud vendors: managed MariaDB services where a vendor offers them, and
the VMs, volumes, networks and the rest of the infrastructure to run MariaDB on.

Each cloud vendor is a plugin of its own, `dba_vendor_<name>_plugin`, that adds
`dba.vendor.<name>` and implements the vendor interface in
[`lib/vendor.py`](lib/vendor.py). The `dba` functions take the vendor's name and work the
same for every vendor:

| Vendor plugin | Vendor name | Feature set |
| --- | --- | --- |
| [`dba_vendor_hetzner_plugin`](../dba_vendor_hetzner_plugin/README.md) | `hetzner` | CoreCompute |
| [`dba_vendor_upcloud_plugin`](../dba_vendor_upcloud_plugin/README.md) | `upcloud` | CoreCompute |
| [`dba_vendor_mariadb_cloud_plugin`](../dba_vendor_mariadb_cloud_plugin/README.md) | `mariadb_cloud` | ManagedService |

The functions are also offered as MCP tools by the [MCP plugin](../mcp_plugin/README.md)'s
`dba` group, named with `_` for the nested names (`dba.list.vendor.objects` is
`dba.list_vendor_objects`).

## Feature sets and object types

A vendor reports the **feature sets** it implements:

- `ManagedService` - managed MariaDB services the vendor deploys and operates.
- `CoreCompute` - infrastructure to run MariaDB Server or a Galera Cluster on.

Objects are named with **standardized object types**, whatever the vendor calls them:
`managed_mariadb_services`, `vms`, `volumes`, `networking`, `firewalls`,
`load_balancers`, `dns`, `object_storage` and `ssh_keys`.
`dba.list.vendor.objectSupport()` lists them with their deployment settings, and
`dba.list.vendor.objectSupport(vendor)` what one vendor supports.

## Shell functions

| Function | MCP tool | Description |
| --- | --- | --- |
| `dba.list.vendors([featureSet])` | `dba.list_vendors` | The vendors, all or those implementing a feature set, with the projects an API key is stored for. |
| `dba.set.apiKey(vendorName, key[, project])` | `dba.set_api_key` (`--gui`) | Stores a vendor's REST API key in the shell's secret store. |
| `dba.list.vendor.objectSupport([vendorName])` | `dba.list_vendor_object_support` | The standardized object types, or what a vendor supports. |
| `dba.get.vendor.objectInfo(vendorName, objectType[, project])` | `dba.get_vendor_object_info` | What of a type can be deployed: sizes (architecture, cores, RAM, disk, included traffic, prices), locations, images, versions. |
| `dba.list.vendor.objects(vendorName, objectType[, tags, project])` | `dba.list_vendor_objects` | The deployed objects of a type, filtered by tags and project. |
| `dba.get.vendor.objectStatus(vendorName, objectType, objectName[, project])` | `dba.get_vendor_object_status` | The current status of one object. |
| `dba.deploy.vendor.object(vendorName, objectType, settings[, project])` | `dba.deploy_vendor_object` (`--gui`) | Starts a task deploying an object with the given settings. |
| `dba.delete.vendor.object(vendorName, objectType, objectName[, project])` | `dba.delete_vendor_object` (`--gui`) | Starts a task deleting an object, by name or id. |

Every vendor object has the same functions without the vendor name:
`dba.vendor.<name>.featureSet()`, `objectSupport()`, `objectInfo()`, `listObjects()`,
`objectStatus()`, `deployObject()` and `deleteObject()`.

An MCP server started without `--gui` - one an agent talks to - offers only the tools that
read.

### Deploying and deleting are background tasks

Deploying takes minutes, a managed service far longer, so `dba.deploy.vendor.object` and
`dba.delete.vendor.object` do not wait for it. They check what they can at once - the
vendor, its API key, that it can deploy or delete the type, that the settings name the
object - then start a task of the shell's task manager (task type `dba`) and return its
`task_id`. The task:

- **deploys** the object and then waits until the vendor reports it ready to use
  (`running`, `started`, `available`, `online`, `active`, `ready`), up to an hour, with
  the object as its result; a status naming a failure or an error fails it;
- **deletes** the object and then waits until the vendor no longer lists it, up to half
  an hour, with the object as it was and what the vendor reported as its result.

Each status the vendor reports on the way is a message of the task. Follow it with
`task.get(taskId[, since][, waitMs])` in the shell, or the `task.get` MCP tool; list the
tasks with `task.list("dba")`. `task.cancel(taskId)` stops the waiting, not the vendor:
what it already started goes on there. One object is worked on by one task at a time.

Tasks live in the process that started them, so the process must not end under them:

- **On the command line** the call waits for its task:
  `mariadb-shell -- dba deploy vendor object hetzner vms --name=db1 ...` writes the
  vendor's statuses to stderr as they come and prints the object once it is ready, or
  fails with the task's error and exit code 1. Ctrl+C cancels the task.
- **A script** (`-f`, `-e`) that ends with tasks still running waits for them before the
  shell exits; Ctrl+C stops the waiting.
- **An interactive session** that ends with tasks still running says which stop with it.

```python
dba.set.api_key("hetzner", "<token>", "prod")
dba.list.vendors("CoreCompute")
dba.get.vendor.object_info("hetzner", "vms", "prod")["sizes"]
started = dba.deploy.vendor.object("hetzner", "vms", {
    "name": "galera-1", "size": "cx32", "location": "fsn1", "image": "debian-13",
    "ssh_keys": ["admin"], "labels": {"cluster": "galera"},
}, "prod")
task.get(started["task_id"], 0, 30000)    # waits up to 30 s for news
dba.list.vendor.objects("hetzner", "vms", "cluster=galera", "prod")
dba.get.vendor.object_status("hetzner", "vms", "galera-1", "prod")
dba.delete.vendor.object("hetzner", "vms", "galera-1", "prod")
```

### Objects

Every object is reported in one shape: `object_type`, `id`, `name`, `status` (in the
vendor's words, or `active` for an object the vendor gives no state), `location`, `labels`, `created`, `vendor_type` (what the vendor calls
it) and a compact `properties` dict of what else matters about it, such as addresses.

### Settings

`dba.deploy.vendor.object` takes standardized settings - `name` always, plus per type,
for example `size`, `location`, `image` and `ssh_keys` for `vms` - and `labels` and
`vendor_settings`, a dict merged into the vendor's own request as is, for whatever the
standardized ones do not cover. A setting the vendor does not support is refused rather
than ignored. VMs are only deployed with SSH keys, so that no root password is created
and handed back.

## API keys and projects

Keys are kept in the shell's secret store, one per vendor and project:

- `GUI:DBA_API_KEY:<vendor>:<project>` - stored by `dba.set.apiKey` or the `--gui` MCP
  server, and used by the shell functions and the VS Code extension.
- `MCP:DBA_API_KEY:<vendor>:<project>` - stored by
  `mariadb-shell -- mcp setup --setDbaApiKey=<vendor> [--dbaProject=<project>]`, and used
  by an MCP server an agent talks to.

The project is `default` when none is named. A vendor whose API has projects of its own
(a Hetzner Cloud token belongs to one project) needs a key per project. For the others the
project is a label: what is deployed for a project gets the label `dba_project=<project>`,
listing a project filters on it, and the `default` key serves every project that has none
of its own.

## Writing a vendor plugin

A vendor plugin is a folder `dba_vendor_<name>_plugin` with:

- `vendor.py` - a `dba_plugin.lib.vendor.Vendor` subclass, registered with
  `@dba_plugin.lib.vendors.register`. It names the vendor and its feature sets, creates
  its API client (`dba_plugin.lib.rest_client.RestClient`, the one HTTP client every
  vendor uses), and implements `_list_<type>` for each object type it supports, plus
  `_info_<type>`, `_deploy_<type>`, `_delete_<type>` and `_status_<type>` as far as it
  supports them. The module docstring of `lib/vendor.py` is the contract.
- `init.py` - `dba_plugin.lib.objects.register_vendor_plugin(<the class>)`, which adds
  `dba.vendor.<name>` and its functions.
- `__init__.py` - importing nothing. The shell refuses to register a function twice, so
  `init.py` must run once only - and the vendor plugin may load before `dba_plugin`,
  which is why `register_vendor_plugin` creates `dba` and `dba.vendor` when they are
  not there yet.

The vendors' own Python SDKs are deliberately not used: they need packages the shell does
not bundle (`requests`, `python-dateutil`), and MariaDB Cloud has none. All the APIs are
JSON over HTTPS, so the shared client on the bundled `httpx2` serves them alike, with the
same retries (a 429 always; a 502, 503, 504 or a failed connection for idempotent
requests, honouring `Retry-After`) and the same error messages.

## Tests

```bash
cd dba_plugin
mariadb-shell --py -f run_tests.py                       # the core and every vendor plugin
mariadb-shell --py -f run_tests.py --vendors=hetzner     # the core and one vendor
DBA_TEST_HETZNER_API_KEY=... mariadb-shell --py -f run_tests.py --live   # + read-only live tests
```

The tests run against a mock of each vendor's API (the `mock_api` fixture in
[`tests/fixtures.py`](tests/fixtures.py)) and keep their secrets in a temporary shell
config home with the plaintext credential helper. The live tests only read - they list
objects and offerings - and run for each vendor whose `DBA_TEST_<VENDOR>_API_KEY` is set.
