# MariaDB Cloud vendor plugin for the MariaDB Shell dba plugin

Adds MariaDB Cloud to the `dba` global object of MariaDB Shell as
`dba.vendor.mariadbCloud` (Python: `dba.vendor.mariadb_cloud`) and to the
shared `dba` functions and MCP tools under the vendor name `mariadb_cloud`.
It needs `dba_plugin` installed next to it.

## What it supports

Feature set: `ManagedService`.

| Object type                | Operations                                 |
| -------------------------- | ------------------------------------------ |
| `managed_mariadb_services` | info, list, status, create, update, delete |

- **info** reports the server sizes (CPU, memory, and the providers,
  topologies and architectures each is offered for), versions, topologies,
  cloud providers (with their storage limits) and regions. MariaDB Cloud
  prices by billing offering rather than by size, so no prices are listed.
- **list** and **status** report each service's topology, version, size,
  nodes, provider, region, storage, endpoints (FQDN, ports, allowed IPs) and
  whether SSL is on. Never credentials.
- **create** requests a service and returns it right away, usually as
  `pending_create`; call `objectStatus` until it is `ready`.
- **delete** deletes the service and all its data.

## Create settings

| Setting        | MariaDB Cloud field | Notes                                            |
| -------------- | ------------------- | ------------------------------------------------ |
| `name`         | `name`              | Required.                                        |
| `provider`     | `provider`          | Required: `aws`, `gcp` or `azure`.               |
| `location`     | `region`            | Required, e.g. `us-east-1` (see `locations`).    |
| `topology`     | `topology`          | Required, e.g. `es-single`, `es-replica`, `galera`. |
| `version`      | `version`           | The version's `name` from `objectInfo`.          |
| `size`         | `size`              | e.g. `sky-2x8`.                                  |
| `nodes`        | `nodes`             |                                                  |
| `storage_gb`   | `storage`           |                                                  |
| `allowed_ips`  | `allow_list`        | IP addresses or CIDR ranges; a bare IP becomes a /32. |
| `labels`       | `tags`              |                                                  |

The service type is `transactional` and SSL is on unless `vendor_settings`
says otherwise; `vendor_settings` is merged into the request body as is, for
example `{"service_type": "transactional_serverless"}`,
`{"architecture": "arm64"}`, `{"maxscale_nodes": 2, "maxscale_size": "sky-2x8"}`
or `{"project_id": "<MariaDB Cloud project id>"}`.

```python
dba.vendor.mariadb_cloud.create_object("managed_mariadb_services", {
    "name": "orders", "provider": "aws", "location": "us-east-1",
    "topology": "es-single", "size": "sky-2x8", "storage_gb": 100,
    "allowed_ips": ["203.0.113.7"]})
```

## Updates

A running service's `size`, `nodes`, `storage_gb` and `allowed_ips` can change. Only one
of size, nodes and storage changes at a time: the service scales (`pending_scale`) and
the task ends once it is `ready` again. New `allowed_ips` replace the allow list.

## Projects

A MariaDB Cloud API key belongs to a user and reaches every project of the
user's organization, so one key serves every dba project: a service created
for a dba project carries the tag `dba_project=<project>`, and listing a
project shows only the services with that tag. Services deployed outside the
dba plugin appear only when no project is named.

## API key

Generate a key at https://app.skysql.com/user-profile/api-keys and store it in
the shell's secret store:

```python
dba.set.api_key("mariadb_cloud", "<key>")
```

(`dba.set.apiKey('mariadb_cloud', key)` in JavaScript mode.) For an MCP server
run `mariadb-shell -- mcp setup --setDbaApiKey=mariadb_cloud --dbaApiKeyStdin`.

## Database credentials

The plugin never fetches or returns the default database credentials of a
service, so that they cannot end up in an agent's transcript. Get them from
the MariaDB Cloud portal (the service's Connect dialog) or yourself from the
API's `GET /provisioning/v1/services/{service_id}/security/credentials`, and
replace them with an account of your own.

## Tests

```sh
cd ../dba_plugin
mariadb-shell --py -f run_tests.py --vendors=mariadb_cloud
```

`--live` also runs a read-only test against the real API with the key in
`DBA_TEST_MARIADB_CLOUD_API_KEY`; it never creates anything.
