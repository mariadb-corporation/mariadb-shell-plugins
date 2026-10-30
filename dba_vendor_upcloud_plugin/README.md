# MariaDB DBA UpCloud Vendor Plugin

Adds [UpCloud](https://upcloud.com) to the MariaDB Shell `dba` plugin, as
`dba.vendor.upcloud` and as the vendor `upcloud` of every `dba` function.
It talks to the UpCloud API 1.3 (`https://api.upcloud.com/1.3`) through the
dba plugin's own REST client, not the UpCloud SDK.

Feature set: **CoreCompute**. UpCloud Managed Databases offer MySQL,
PostgreSQL, OpenSearch and Valkey, but not MariaDB, so there is no
`managed_mariadb_services`.

## API key

Either form is accepted:

* an API token (`ucat_...`), sent as a Bearer token - recommended;
* an API sub-account's `username:password`, sent with HTTP basic auth
  (UpCloud plans to retire basic auth).

```python
dba.set.apiKey('upcloud', 'ucat_...')
dba.set.apiKey('upcloud', 'dba-api-user:password')
```

UpCloud has no projects: one key serves every project, and what is deployed
for a project is labelled `dba_project=<project>`.

## Object types

Every type supports `info`, `list`, `status`, `deploy` and `delete`.
`labels` and `vendor_settings` are accepted for every type.

| Type | UpCloud object | Settings |
| --- | --- | --- |
| `vms` | server | `name`, `size` (plan), `location` (zone), `image` (template name or uuid), `ssh_keys` (required: public keys in OpenSSH format), `user_data`, `networks` (private network names or uuids) |
| `volumes` | storage | `name`, `size_gb`, `location` and/or `vm` (attaches it) |
| `networking` | SDN private network | `name`, `ip_range`, `location` |
| `load_balancers` | Managed Load Balancer | `name`, `size` (plan), `location`, `network` (private network), `targets` (VMs on that network), `services` (`[{protocol: tcp\|http, listen_port, destination_port}]`) |
| `object_storage` | Managed Object Storage | `name`, `location` (region) |

Notes:

* `ssh_keys` are the public keys themselves - UpCloud has no SSH key store.
  They are installed for the user `upclouduser`, and no password is created.
* A VM gets a public and a utility IPv4 address, plus one address per
  private network named, and a boot disk of its plan's size and tier.
* `vendor_settings` are merged into the object of the request body - the
  `server`, `storage` or `network` object, or the load balancer and object
  storage request itself - e.g. `{"tier": "standard"}` for a volume or
  `{"login_user": {...}}` for a VM.
* Deleting a VM stops it first, waiting up to 120 seconds, and deletes its
  boot disk with it. Volumes attached to it are detached and kept. Backups
  are kept.
* A volume attached to a server is not deleted; detach it or delete the
  server.
* Firewalls are not supported: UpCloud firewall rules belong to one server
  each, with no rule set to name, list or apply to several.

## Tests

```sh
cd ../dba_plugin
mariadb-shell --py -f run_tests.py --vendors=upcloud
DBA_TEST_UPCLOUD_API_KEY=ucat_... mariadb-shell --py -f run_tests.py --vendors=upcloud --live
```

The live test only reads: it lists servers and the VM catalog.
