# Hetzner Cloud vendor plugin for the MariaDB DBA plugin

Adds `dba.vendor.hetzner` to MariaDB Shell. It creates and manages the
infrastructure for MariaDB Server and Galera Cluster on Hetzner Cloud
(feature set `CoreCompute`), through the
[Hetzner Cloud API](https://docs.hetzner.cloud/reference/cloud).

## API token

A Hetzner Cloud API token belongs to one project. Create one in the
[Hetzner Console](https://console.hetzner.com): open the project, then
Security > API tokens > Generate API token, with Read & Write permission
to create objects. Store it once per project:

```python
dba.set.apiKey('hetzner', '<token>', 'my-project')
dba.vendor.hetzner.listObjects('vms', None, 'my-project')
```

A token stored without a project serves the default project only. Other
projects do not fall back on it.

## Object types

| Object type      | Hetzner name  | Operations                          |
| ---------------- | ------------- | ----------------------------------- |
| `vms`            | server        | info, list, status, create, delete  |
| `volumes`        | volume        | info, list, status, create, delete  |
| `networking`     | network       | info, list, status, create, delete  |
| `firewalls`      | firewall      | info, list, status, create, delete  |
| `load_balancers` | load_balancer | info, list, status, create, delete  |
| `dns`            | zone          | info, list, status, create, delete  |
| `ssh_keys`       | ssh_key       | list, status, create, delete        |

Object storage is not supported because it is S3-only and not part of the
Cloud API. Hetzner offers no managed MariaDB services.

## Create settings

Every type takes `name` (required), `labels` (a dict, stored as Hetzner
labels) and `vendor_settings` (a dict merged into the API request body as
is). Names given for SSH keys, networks, firewalls and VMs are resolved to
their ids. Creating does not wait for Hetzner's action to finish; the
action is returned in the object's properties.

- `vms`: `size` (server type, e.g. `cx22`), `image` (e.g. `ubuntu-24.04`)
  and `ssh_keys` are required. `location`, `user_data`, `networks` and
  `firewalls` are optional. A VM cannot be created without SSH keys,
  because Hetzner would otherwise create a root password and send it back
  in the answer.
- `volumes`: `size_gb` is required, along with `location` or `vm` (the VM to
  attach the volume to). `format` (`ext4` or `xfs`) is optional. Deleting
  an attached volume detaches it first.
- `networking`: `ip_range` is required. `location` is a location such as
  `fsn1` or a network zone such as `eu-central`. With a location, one
  subnet that spans the whole network is created in that zone.
- `firewalls`: `rules` is a list of `{direction, protocol, port,
  source_ips | destination_ips, description}`. The direction defaults to
  `in`. `vms` lists the VMs to apply the firewall to. Deleting a firewall
  removes it from its resources first.
- `load_balancers`: `size` (load balancer type, e.g. `lb11`) is required.
  `location`, `network`, `targets` (VMs) and `services` (`{protocol,
  listen_port, destination_port}`) are optional. A service gets a TCP
  health check of its destination port by default. Targets use the private
  network when one is given.
- `dns`: `ttl` and `records` (`{name, type, value, ttl}`) are optional.
  Records of the same name and type become one RRSet.
- `ssh_keys`: `public_key` is required.

## Tests

```sh
cd ../dba_plugin
mariadb-shell --py -f run_tests.py --vendors=hetzner
DBA_TEST_HETZNER_API_KEY=<token> mariadb-shell --py -f run_tests.py --vendors=hetzner --live
```

The live test only reads. It lists servers and the VM info.
