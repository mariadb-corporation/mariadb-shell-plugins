# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

"""MariaDB Cloud: managed MariaDB services, through its REST API.

The API is documented at https://apidocs.skysql.com (Swagger 2.0, served
as openapi.json there). What this plugin relies on:

* Base URL ``https://api.skysql.com``, the API key in the ``X-API-Key``
  header. A key belongs to a user and reaches every project of the user's
  organization, so the dba plugin's projects are labels here (see
  ``native_projects``).
* ``/provisioning/v1/services`` lists (GET) and creates (POST) services;
  ``/provisioning/v1/services/{id}`` reads (GET) and deletes (DELETE) one.
  A service carries a ``tags`` dict of strings, which is where the labels,
  the ``dba_project`` one among them, are kept.
* The listings page by token: ``page_size`` (at most 100) and
  ``page_token`` in the query, the next page's token in the
  ``X-SkySQL-Page-Token-Next`` response header, the body a plain list.
* Errors are ``{"code": <status>, "errors": [{"error", "message", "type"}]}``,
  which the shared REST client already reads.

The default database credentials of a new service are deliberately never
fetched (``/security/credentials``): what a function returns may end up in
an agent's transcript. The user reads them in the portal.
"""

# cSpell:ignore mysqlsh MariaDB skysql maxscale nlb privatelink privateconnect

import datetime
import ipaddress

from dba_plugin.lib import object_types as types, rest_client, vendor as v, vendors
from dba_plugin.lib import settings_fields as sf
from dba_plugin.lib.general import DbaError, SettingError

API_URL = "https://api.skysql.com"
PROVISIONING = "/provisioning/v1"

# The response header carrying the token of the next page of a listing.
PAGE_TOKEN_HEADER = "X-SkySQL-Page-Token-Next"
PAGE_SIZE = 100

# The service type a new service gets unless vendor_settings names another,
# e.g. transactional_serverless.
DEFAULT_SERVICE_TYPE = "transactional"

# A service in this status is gone; the API may still list it for a while.
RETIRED = "retired"


def _iso_time(epoch):
    """An epoch time in seconds (or milliseconds) as ISO 8601, or None."""
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    if epoch > 1e12:
        epoch = epoch / 1000
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


def _value(value):
    """A value for an object's properties: empty strings and lists are None."""
    return None if value in ("", [], {}) else value


def _endpoints(service: dict) -> list:
    """A service's endpoints, compactly: where to connect and who may."""
    compact = []
    for endpoint in service.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        entry = {
            "name": endpoint.get("name"),
            "fqdn": endpoint.get("fqdn"),
            "mechanism": endpoint.get("mechanism"),
            "visibility": endpoint.get("visibility"),
            "ports": {
                port.get("name") or port.get("purpose"): port.get("port")
                for port in endpoint.get("ports") or []
                if isinstance(port, dict)
            },
            "allowed_ips": [
                item.get("ip")
                for item in endpoint.get("allow_list") or []
                if isinstance(item, dict) and item.get("ip")
            ],
        }
        compact.append({key: value for key, value in entry.items() if _value(value) is not None})
    return compact


def _tags(tags) -> dict:
    """Labels as MariaDB Cloud tags, and back: a dict of strings."""
    if not isinstance(tags, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in tags.items()}


def _allow_list(allowed_ips) -> list:
    """The allowed_ips setting as the API's allow_list.

    A bare IP address becomes a /32 (or /128) network, as the API's allow
    list takes CIDR ranges; an entry may also be the API's own
    ``{"ip", "comment"}`` dict.
    """
    allow_list = []
    for item in v.as_list(allowed_ips):
        if isinstance(item, dict):
            ip, comment = str(item.get("ip") or "").strip(), item.get("comment")
        else:
            ip, comment = str(item).strip(), None
        if not ip:
            continue
        try:
            network = ipaddress.ip_network(ip, strict=False)
        except ValueError as e:
            raise SettingError(f"allowed_ips: '{ip}' is no IP address or CIDR range.", "allowed_ips") from e
        entry = {"ip": str(network)}
        if comment:
            entry["comment"] = str(comment)
        allow_list.append(entry)
    return allow_list


@vendors.register
class MariadbCloud(v.Vendor):
    """MariaDB Cloud, MariaDB's own DBaaS."""

    name = "mariadb_cloud"
    display_name = "MariaDB Cloud"
    feature_sets = (types.MANAGED_SERVICE,)
    # A key is a user's and reaches every MariaDB Cloud project of the
    # organization, so one key serves every dba project and the services
    # of a dba project are found by their dba_project tag.
    native_projects = False
    api_key_help = (
        "Generate a MariaDB Cloud API key at https://app.skysql.com/user-profile/api-keys "
        "(the portal's user profile, API Keys)."
    )

    settings_fields = {
        types.MANAGED_MARIADB_SERVICES: {
            "size": {"is_updatable": True},
            "nodes": {"is_updatable": True},
            "storage_gb": {"is_updatable": True,
                           "description": "The storage size in GB, within the provider's "
                           "storage_gb_min and storage_gb_max."},
            "allowed_ips": {
                "is_updatable": True,
                "description": "The IP addresses or CIDR ranges allowed to connect: a bare "
                "address is a /32 (or /128) range. An entry may also be {ip, comment}.",
                "item_fields": {
                    "ip": sf.field("Address", "The IP address or CIDR range.",
                                   format=sf.FORMAT_IP_OR_CIDR, required=True),
                    "comment": sf.field("Comment", "What the entry is for."),
                },
            },
            "ssl_enabled": sf.field(
                "SSL/TLS",
                "Whether clients connect with SSL/TLS.",
                sf.BOOLEAN, group="security", default=True, advanced=True,
            ),
        },
    }
    settings_order = {
        types.MANAGED_MARIADB_SERVICES: (
            "name", "provider", "location", "topology", "version", "size", "nodes", "storage_gb",
        ),
    }

    create_settings = {
        types.MANAGED_MARIADB_SERVICES: {"required": ("name", "provider", "location", "topology")},
    }

    @staticmethod
    def _check_managed_mariadb_services(settings) -> None:
        """What a service's settings must be, before any request, beyond their fields."""
        if not isinstance(settings["vendor_settings"].get("tags") or {}, dict):
            raise SettingError("vendor_settings tags must be a dict.", "vendor_settings")

    @staticmethod
    def _check_update_managed_mariadb_services(settings) -> None:
        """A running service scales one way at a time, and refuses a change while it scales."""
        scaling = [key for key in ("size", "nodes", "storage_gb") if key in settings]
        if len(scaling) > 1:
            raise SettingError(
                "MariaDB Cloud changes one of size, nodes and storage_gb at a time: change "
                f"{' and '.join(scaling)} one after the other, each once the service is ready again.",
                scaling,
            )

    def create_client(self):
        return rest_client.RestClient(
            self.display_name, API_URL, headers={"X-API-Key": self.api_key}
        )

    # --- Helpers ----------------------------------------------------------

    def _get_all(self, path: str, params=None) -> list:
        """Every item of a listing paged with a token, which comes in a header."""

        def page(token):
            query = {**(params or {}), "page_size": PAGE_SIZE}
            if token:
                query["page_token"] = token
            body, headers = self.client.get_with_headers(path, params=query)
            if isinstance(body, list):
                batch = body
            elif isinstance(body, dict):
                # Not documented, but tolerated: a body wrapping its items.
                batch = body.get("items") or body.get("data") or []
            else:
                batch = []
            return batch, (headers.get(PAGE_TOKEN_HEADER) or None) if batch else None

        return v.paginate(page)

    @staticmethod
    def _service(service: dict) -> dict:
        """A service as a dba object. It carries no credentials."""
        storage = service.get("storage_volume") or {}
        return v.make_object(
            types.MANAGED_MARIADB_SERVICES,
            service.get("id"),
            service.get("name"),
            status=service.get("status"),
            location=service.get("region"),
            labels=_tags(service.get("tags")),
            created=_iso_time(service.get("created_on")),
            vendor_type="service",
            service_type=_value(service.get("service_type")),
            topology=_value(service.get("topology")),
            version=_value(service.get("version")),
            size=_value(service.get("size")),
            nodes=service.get("nodes"),
            provider=_value(service.get("provider")),
            architecture=v.architecture(_value(service.get("architecture"))),
            tier=_value(service.get("tier")),
            storage_gb=storage.get("size"),
            volume_type=_value(storage.get("volume_type")),
            maxscale_nodes=service.get("maxscale_nodes") or None,
            fqdn=_value(service.get("fqdn")),
            endpoints=_value(_endpoints(service)),
            ssl_enabled=service.get("ssl_enabled"),
            mariadb_cloud_project_id=_value(service.get("project_id")),
            errors=_value(service.get("errors")),
        )

    # --- managed_mariadb_services -----------------------------------------

    def _list_managed_mariadb_services(self) -> list:
        return [
            self._service(service)
            for service in self._get_all(f"{PROVISIONING}/services")
            if isinstance(service, dict) and service.get("status") != RETIRED
        ]

    def _info_managed_mariadb_services(self) -> dict:
        """What can be created. MariaDB Cloud prices by billing offering,
        not by size, so no prices are reported here."""
        # five independent listings, asked for side by side
        size_list, version_list, topology_list, provider_list, region_list = v.parallel(
            lambda: self._get_all(
                f"{PROVISIONING}/sizes", {"type": "server", "service_type": DEFAULT_SERVICE_TYPE}
            ),
            lambda: self._get_all(f"{PROVISIONING}/versions"),
            lambda: self._get_all(f"{PROVISIONING}/topologies", {"service_type": DEFAULT_SERVICE_TYPE}),
            lambda: self.client.get(f"{PROVISIONING}/providers") or [],
            lambda: self._get_all(f"{PROVISIONING}/regions"),
        )

        sizes = {}
        for size in size_list:
            if size.get("is_active") is False or not size.get("name"):
                continue
            entry = sizes.setdefault(
                size["name"],
                {
                    "name": size["name"],
                    "description": size.get("display_name"),
                    "cpu": size.get("cpu"),
                    "memory": size.get("ram"),
                    "providers": [],
                    "topologies": [],
                    "architectures": [],
                },
            )
            for key, field in (
                ("providers", "provider"),
                ("topologies", "topology"),
                ("architectures", "architecture"),
            ):
                value = v.architecture(size.get(field)) if field == "architecture" else size.get(field)
                if value and value not in entry[key]:
                    entry[key].append(value)

        versions = {}
        for version in version_list:
            if not version.get("name"):
                continue
            entry = versions.setdefault(
                version["name"],
                {
                    "name": version["name"],
                    "description": version.get("display_name") or version.get("version"),
                    "product": version.get("product"),
                    "default": bool(version.get("default")),
                    "topologies": [],
                },
            )
            entry["default"] = entry["default"] or bool(version.get("default"))
            if version.get("topology") and version["topology"] not in entry["topologies"]:
                entry["topologies"].append(version["topology"])

        topologies = [
            {
                "name": topology.get("name"),
                "description": topology.get("display_name"),
                "database": topology.get("database"),
                "storage_engine": topology.get("storage_engine"),
                "default": bool(topology.get("is_default")),
            }
            for topology in sorted(
                topology_list,
                key=lambda topology: topology.get("order") or 0,
            )
            if topology.get("name")
        ]

        providers = [
            {
                "name": provider.get("name"),
                "description": provider.get("display_name"),
                "default": bool(provider.get("default")),
                "storage_gb_min": provider.get("volume_size_min_gb"),
                "storage_gb_max": provider.get("volume_size_max_gb"),
            }
            for provider in provider_list
            if provider.get("name") and provider.get("active") is not False
        ]

        locations = [
            {
                "name": region.get("name"),
                "description": region.get("region"),
                "provider": region.get("provider"),
                "default": bool(region.get("default")),
            }
            for region in sorted(
                region_list,
                key=lambda region: (region.get("provider") or "", region.get("order") or 0),
            )
            if region.get("name") and region.get("is_available") is not False
        ]

        return {
            "sizes": list(sizes.values()),
            "versions": list(versions.values()),
            "topologies": topologies,
            "providers": providers,
            "locations": locations,
        }

    def _create_managed_mariadb_services(self, settings) -> dict:
        """Requests a new service and returns it as the API accepted it,
        typically ``pending_create``; it does not wait for it to be ready."""
        settings = self.check_create_settings(types.MANAGED_MARIADB_SERVICES, settings)

        body = {
            "name": str(settings["name"]),
            "service_type": DEFAULT_SERVICE_TYPE,
            "provider": str(settings["provider"]).lower(),
            "region": str(settings["location"]),
            "topology": str(settings["topology"]),
            "ssl_enabled": True,
        }
        for key in ("version", "size"):
            if settings.get(key) not in (None, ""):
                body[key] = str(settings[key])
        # checked and made whole numbers by their fields
        if settings.get("nodes") is not None:
            body["nodes"] = settings["nodes"]
        if settings.get("storage_gb") is not None:
            body["storage"] = settings["storage_gb"]
        if settings.get("allowed_ips") not in (None, "", []):
            body["allow_list"] = _allow_list(settings["allowed_ips"])
        vendor_settings = dict(settings["vendor_settings"])
        vendor_tags = vendor_settings.pop("tags", None) or {}
        body.update(vendor_settings)
        # The labels win over tags given in vendor_settings: the dba_project
        # one must survive, or the project would lose the service.
        tags = {**_tags(vendor_tags), **_tags(settings["labels"])}
        if tags:
            body["tags"] = tags

        created = self.client.post(f"{PROVISIONING}/services", json=body)
        if not isinstance(created, dict) or not created.get("id"):
            raise DbaError(
                f"{self.display_name} accepted the service '{body['name']}' but did not "
                "report it; list the services to find it."
            )
        return self._service(created)

    def _delete_managed_mariadb_services(self, obj: dict) -> dict:
        answer = self.client.delete(f"{PROVISIONING}/services/{obj['id']}")
        status = answer.get("status") if isinstance(answer, dict) else None
        return {"id": obj["id"], "status": status or "pending_delete"}

    def _update_managed_mariadb_services(self, obj: dict, settings) -> dict:
        service = f"{PROVISIONING}/services/{obj['id']}"
        if "allowed_ips" in settings:
            self.client.put(f"{service}/security/allowlist", json=_allow_list(settings["allowed_ips"]))
        if "size" in settings:
            self.client.post(f"{service}/size", json={"size": str(settings["size"])})
        if "nodes" in settings:
            self.client.post(f"{service}/nodes", json={"nodes": settings["nodes"]})
        if "storage_gb" in settings:
            self.client.patch(f"{service}/storage", json={"size": settings["storage_gb"]})
        return obj

    def _status_managed_mariadb_services(self, obj: dict) -> dict:
        return self._service(self.client.get(f"{PROVISIONING}/services/{obj['id']}"))
