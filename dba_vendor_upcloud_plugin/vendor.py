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

"""The UpCloud vendor: servers, storages, SDN networks, load balancers and
Managed Object Storage, through the UpCloud API 1.3.

UpCloud speaks of servers, storages and networks; the dba plugin of vms,
volumes and networking. What the API made awkward, and how it is handled:

* UpCloud has no projects, so the dba plugin labels what it creates for a
  project with ``dba_project`` - and UpCloud labels round-trip as
  ``[{"key", "value"}]`` lists. Servers wrap that list as
  ``{"label": [...]}``, the newer APIs do not; :func:`labels_from_api`
  reads both.
* The server list is a summary: no addresses, no storages. Listing adds the
  public and utility addresses from ``GET /ip_address`` - one request for
  every server, not one per server - and the status of a server fetches
  its details.
* UpCloud has no SSH key store: the ``ssh_keys`` of a VM are the public
  keys themselves, installed for the login user. They are required, so
  UpCloud never generates a password that would end up in an answer.
* A server must be stopped before it can be deleted, so deleting one stops
  it and waits, bounded by :data:`STOP_TIMEOUT`, before deleting it with
  its boot disk. Other storages - a database's data volume - are detached
  first and kept, as at the other vendors.
* Prices come in cents per hour, in the account's currency; the monthly
  price is capped at :data:`HOURS_PER_MONTH` hours, as UpCloud bills.
* Managed Databases offer MySQL, PostgreSQL, OpenSearch and Valkey, not
  MariaDB, so UpCloud implements CoreCompute only. Firewall rules belong to
  one server each at UpCloud, with no named rule set to list or apply, so
  firewalls are not supported either.
"""

# cSpell:ignore mysqlsh MariaDB upcloud ucat maxiops upclouduser

import re
import time
from typing import Optional
from datetime import datetime, timezone

from dba_plugin.lib import object_types as types, rest_client
from dba_plugin.lib import vendor as v
from dba_plugin.lib import settings_fields as sf
from dba_plugin.lib import vendors
from dba_plugin.lib.general import DbaError, SettingError

BASE_URL = "https://api.upcloud.com/1.3"

# How long deleting a server waits for it to stop, and how often it looks.
STOP_TIMEOUT = 120
POLL_INTERVAL = 5

# UpCloud bills by the hour up to this many hours a month.
HOURS_PER_MONTH = 672

# The user the public keys of a VM are installed for, unless the
# vendor_settings name another in login_user.
DEFAULT_LOGIN_USER = "upclouduser"

# The storage tiers a volume can be created on.
STORAGE_TIERS = ("maxiops", "standard", "hdd")
DEFAULT_STORAGE_TIER = "maxiops"

# Paging of the APIs that page with limit and offset.
PAGE_SIZE = 100

# Replaced by the tests, so that waiting for a server takes no time.
_sleep = time.sleep


def labels_to_api(labels: dict) -> list:
    """A labels dict as UpCloud takes it: a list of key/value dicts."""
    return [
        {"key": str(key), "value": "" if value is None else str(value)}
        for key, value in (labels or {}).items()
    ]


def labels_from_api(labels) -> dict:
    """UpCloud labels as a dict, whether wrapped in ``{"label": [...]}``
    (servers) or a plain list (storages, networks and the newer APIs)."""
    if isinstance(labels, dict):
        labels = labels.get("label")
    return {
        str(label.get("key")): label.get("value") or ""
        for label in labels or []
        if isinstance(label, dict) and label.get("key") is not None
    }


def _wrapped(body, outer: str, inner: str) -> list:
    """The list UpCloud wraps twice, as in ``{"servers": {"server": [...]}}``."""
    return ((body or {}).get(outer) or {}).get(inner) or []


def _created(value):
    """A creation time as ISO 8601, from a Unix time or an ISO string."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value)


def _gb(megabytes):
    """Megabytes as gigabytes, an int where it is a whole number."""
    try:
        gigabytes = float(megabytes) / 1024
    except (TypeError, ValueError):
        return None
    return int(gigabytes) if gigabytes.is_integer() else round(gigabytes, 2)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# What can be changed on the objects that have them: their name and labels.
_RENAMED = {"name": {"is_updatable": True}, "labels": {"is_updatable": True}}


def _hostname(name: str) -> str:
    """A valid hostname made of an object name."""
    hostname = re.sub(r"[^a-z0-9.-]+", "-", str(name).lower()).strip("-.")
    return hostname[:128] or "mariadb"


def _city(description: str) -> str:
    """The city of a zone description such as 'Frankfurt #1'."""
    return re.sub(r"\s*#\d+$", "", str(description or "")).strip() or None


def _country(zone_id: str) -> str:
    """The country code a zone id starts with, e.g. DE for de-fra1."""
    code = str(zone_id or "").split("-", 1)[0].upper()
    return {"UK": "GB"}.get(code, code) or None


def _label_filter(labels) -> Optional[dict]:
    """The listing parameters that have UpCloud filter by labels: one label=key=value,
    or label=key for any value, per label. Only the classic listings - servers,
    storages, networks - take them as such."""
    if not labels:
        return None
    return {
        "label": [key if value is None else f"{key}={value}" for key, value in labels.items()]
    }


def _find_one(items: list, value, name_key: str, what: str) -> dict:
    """The item of a name or uuid, refusing a name that is not unique."""
    for key in (name_key, "uuid"):
        found = [item for item in items if item.get(key) == str(value)]
        if len(found) > 1:
            raise DbaError(f"There are {len(found)} {what}s named '{value}'; name one by its uuid.")
        if found:
            return found[0]
    raise DbaError(f"There is no {what} named '{value}'.")


def _ip(ip: dict) -> dict:
    return {"address": ip.get("address"), "access": ip.get("access"), "family": ip.get("family")}


def _system_disk(server: dict):
    """The storage device a server was created with, the one it boots from.

    UpCloud reports boot_disk "0" on it as well, unless it was named the
    boot disk explicitly: the disk that came with the plan is the next
    sign, and the first disk on the bus the last.
    """
    disks = [
        device
        for device in _wrapped(server, "storage_devices", "storage_device")
        if device.get("type", "disk") == "disk"
    ]
    for sign in (
        lambda device: str(device.get("boot_disk")) == "1",
        lambda device: device.get("part_of_plan") == "yes",
    ):
        found = [device for device in disks if sign(device)]
        if found:
            return found[0]
    return min(disks, key=lambda device: str(device.get("address")), default=None)


@vendors.register
class UpCloud(v.Vendor):
    """UpCloud: servers, storages, SDN networks, load balancers and
    Managed Object Storage."""

    name = "upcloud"
    display_name = "UpCloud"
    feature_sets = (types.CORE_COMPUTE,)
    native_projects = False
    api_key_help = (
        "Create an API token (ucat_...) in the UpCloud Control Panel or with "
        "upctl, or store an API sub-account's credentials as 'username:password'."
    )

    settings_fields = {
        types.VMS: {
            **_RENAMED,
            "networks": {"filter": [{"setting": "location", "detail": "location"}],
                         "description": "A list of private network names or uuids to attach; only "
                         "networks in the server's zone."},
            "ssh_keys": {"caption": "SSH public keys", "item_type": sf.TEXT,
                         "description": "The public keys to install, in OpenSSH format, e.g. "
                         f"'ssh-ed25519 AAAA... user@host', for the user {DEFAULT_LOGIN_USER}. "
                         "UpCloud stores no keys. Required."},
            "hostname": sf.field(
                "Hostname",
                "The server's hostname; made of its name by default.",
                group="networking", pattern=sf.HOSTNAME_PATTERN, max_length=128, advanced=True,
            ),
        },
        types.VOLUMES: {
            **_RENAMED,
            "size_gb": {"is_updatable": True,
                        "description": "The size in GB; it can be grown later."},
            "vm": {"description": "The name or uuid of a server to attach the volume to; its zone "
                   "is the volume's."},
            "tier": sf.field(
                "Storage tier",
                "The storage tier: maxiops (fastest), standard or hdd.",
                group="storage", choices_from={"info": "tiers"}, strict=True,
                default=DEFAULT_STORAGE_TIER,
            ),
        },
        types.NETWORKING: dict(_RENAMED),
        types.LOAD_BALANCERS: {
            "labels": _RENAMED["labels"],
            "services": {"item_fields": {
                **sf.FIELDS[types.LOAD_BALANCERS]["services"]["item_fields"],
                "protocol": sf.field("Protocol", "tcp or http.", choices=["tcp", "http"],
                                     strict=True, default="tcp"),
            }},
        },
        types.OBJECT_STORAGE: {"labels": _RENAMED["labels"]},
    }

    create_settings = {
        types.VMS: {
            "required": ("name", "size", "location", "image", "ssh_keys"),
            "supported": ("size", "location", "image", "ssh_keys", "user_data", "networks"),
        },
        types.VOLUMES: {"required": ("name", "size_gb"), "supported": ("size_gb", "location", "vm")},
        types.NETWORKING: {"required": ("name", "ip_range", "location")},
        types.LOAD_BALANCERS: {"required": ("name", "size", "location", "network", "services")},
        types.OBJECT_STORAGE: {"required": ("name", "location"), "supported": ("location",)},
    }

    # --- What the settings to create an object with must be, before any request ----------

    @staticmethod
    def _check_vms(settings) -> None:
        ssh_keys = v.as_list(settings["ssh_keys"])
        if not ssh_keys or not all(
            isinstance(key, str) and key.strip().startswith(("ssh-", "ecdsa-", "sk-")) for key in ssh_keys
        ):
            raise SettingError(
                "UpCloud has no SSH key store: ssh_keys must be a list of public keys in "
                "OpenSSH format, e.g. 'ssh-ed25519 AAAA... user@host'.",
                "ssh_keys",
            )

    @staticmethod
    def _check_volumes(settings) -> None:
        if not settings.get("location") and not settings.get("vm"):
            raise SettingError(
                "Missing settings for volumes: location, or a vm to attach it to.", ("location", "vm")
            )

    def create_client(self):
        """The API client: a token is sent as a Bearer token, a
        ``username:password`` key with HTTP basic authentication."""
        key = self.api_key.strip()
        username, separator, password = key.partition(":")
        if separator and not key.startswith("ucat_") and username and password:
            return rest_client.RestClient(self.display_name, BASE_URL, auth=(username, password))
        return rest_client.RestClient(
            self.display_name, BASE_URL, headers={"Authorization": f"Bearer {key}"}
        )

    # --- Shared lookups ---------------------------------------------------

    def _paged(self, path: str) -> list:
        """Every item of a listing paged with limit and offset."""

        def page(offset):
            offset = offset or 0
            batch = self.client.get(path, params={"limit": PAGE_SIZE, "offset": offset}) or []
            return batch, offset + PAGE_SIZE if len(batch) >= PAGE_SIZE else None

        return v.paginate(page)

    def _zones(self) -> list:
        """The public zones, as locations."""
        zones = _wrapped(self.client.get("/zone"), "zones", "zone")
        return [
            {
                "name": zone.get("id"),
                "description": zone.get("description"),
                "country": _country(zone.get("id")),
                "city": _city(zone.get("description")),
            }
            for zone in zones
            if zone.get("public", "yes") == "yes"
        ]

    def _plans(self) -> list:
        return _wrapped(self.client.get("/plan"), "plans", "plan")

    def _currency(self):
        """The account's currency; None where the key may not read it."""
        try:
            username = ((self.client.get("/account") or {}).get("account") or {}).get("username")
            if not username:
                return None
            details = self.client.get(f"/account/details/{username}") or {}
            return (details.get("account") or {}).get("currency")
        except rest_client.VendorApiError:
            return None

    def _networks(self, labels=None) -> list:
        return _wrapped(self.client.get("/network", params=_label_filter(labels)), "networks", "network")

    def _find_network(self, network) -> dict:
        """The private network of a name or uuid."""
        private = [net for net in self._networks() if net.get("type") == "private"]
        return _find_one(private, network, "name", "private UpCloud network")

    def _servers(self, labels=None) -> list:
        return _wrapped(self.client.get("/server", params=_label_filter(labels)), "servers", "server")

    def _find_server(self, server, servers=None) -> dict:
        """The server summary of a name (title) or uuid.

        Args:
            server: The name or uuid.
            servers (list): The server summaries, when they are at hand.
        """
        return _find_one(
            self._servers() if servers is None else servers, server, "title", "UpCloud server"
        )

    def _server_details(self, uuid: str) -> dict:
        return (self.client.get(f"/server/{uuid}") or {}).get("server") or {}

    # --- vms: servers -----------------------------------------------------

    def _server_object(self, server: dict, addresses=None, storages=None) -> dict:
        return v.make_object(
            types.VMS,
            server.get("uuid"),
            server.get("title"),
            server.get("state"),
            server.get("zone"),
            labels_from_api(server.get("labels")),
            _created(server.get("created")),
            "server",
            plan=server.get("plan"),
            cores=_int(server.get("core_number")),
            memory_gb=_gb(server.get("memory_amount")),
            hostname=server.get("hostname"),
            ip_addresses=addresses,
            storages=storages,
        )

    def _list_vms(self, labels=None) -> list:
        servers = self._servers(labels)
        if not servers:
            return []

        addresses = {}
        for ip in _wrapped(self.client.get("/ip_address"), "ip_addresses", "ip_address"):
            if ip.get("server"):
                addresses.setdefault(ip["server"], []).append(_ip(ip))

        return [
            self._server_object(server, addresses.get(server.get("uuid"), []))
            for server in servers
        ]

    def _server_details_object(self, server: dict) -> dict:
        addresses = [_ip(ip) for ip in _wrapped(server, "ip_addresses", "ip_address")]
        storages = [
            {
                "uuid": device.get("storage"),
                "title": device.get("storage_title"),
                "size_gb": device.get("storage_size"),
                "tier": device.get("storage_tier"),
                "boot_disk": str(device.get("boot_disk")) == "1",
            }
            for device in _wrapped(server, "storage_devices", "storage_device")
        ]
        return self._server_object(server, addresses, storages)

    def _status_vms(self, obj: dict) -> dict:
        return self._server_details_object(self._server_details(obj["id"]))

    def _info_vms(self) -> dict:
        # independent requests, asked for side by side
        price_list, currency, plans, zones, templates = v.parallel(
            lambda: self.client.get("/price"),
            self._currency,
            self._plans,
            self._zones,
            self._templates,
        )
        prices = _wrapped(price_list, "prices", "zone")

        sizes = []
        for plan in plans:
            plan_name = plan.get("name")
            plan_prices = []
            for zone in prices:
                price = zone.get(f"server_plan_{plan_name}")
                if not isinstance(price, dict) or price.get("price") is None:
                    continue
                hourly = float(price["price"]) / 100
                plan_prices.append(
                    {
                        "location": zone.get("name"),
                        "hourly": round(hourly, 5),
                        "monthly": round(hourly * HOURS_PER_MONTH, 2),
                        "currency": currency,
                    }
                )

            cores = _int(plan.get("core_number"))
            memory_gb = _gb(plan.get("memory_amount"))
            disk_gb = _int(plan.get("storage_size"))
            description = f"{cores} vCPU, {memory_gb} GB RAM"
            if disk_gb:
                description += f", {disk_gb} GB {plan.get('storage_tier') or ''}".rstrip()
            if plan.get("gpu_amount"):
                description += f", {plan['gpu_amount']}x {plan.get('gpu_model') or 'GPU'}"
            sizes.append(
                {
                    "name": plan_name,
                    "description": description,
                    "architecture": "x86_64",
                    "cores": cores,
                    "memory_gb": memory_gb,
                    "disk_gb": disk_gb,
                    "included_traffic_tb": _gb(plan.get("public_traffic_out")),
                    "prices": plan_prices,
                    "deprecated": bool(plan.get("deprecated", False)),
                }
            )

        return {"sizes": sizes, "locations": zones, "images": templates}

    def _templates(self) -> list:
        templates = _wrapped(self.client.get("/storage/template"), "storages", "storage")
        return [
            {
                "name": template.get("title"),
                "id": template.get("uuid"),
                "description": template.get("template_type"),
                "architecture": "arm64" if "arm" in str(template.get("title")).lower().split() else "x86_64",
            }
            for template in templates
            if template.get("access", "public") == "public"
        ]

    def _find_template(self, image) -> dict:
        templates = self._templates()
        for template in templates:
            if image in (template["id"], template["name"]):
                return template
        lowered = str(image).lower()
        found = [t for t in templates if str(t["name"]).lower() == lowered]
        if len(found) == 1:
            return found[0]
        raise DbaError(
            f"There is no UpCloud template '{image}'. See objectInfo('vms') for the images."
        )

    def _create_vms(self, settings) -> dict:
        settings = self.check_create_settings(types.VMS, settings)
        ssh_keys = v.as_list(settings["ssh_keys"])

        plan = next((p for p in self._plans() if p.get("name") == settings["size"]), None)
        if plan is None:
            raise DbaError(
                f"There is no UpCloud plan '{settings['size']}'. See objectInfo('vms') for the sizes."
            )
        template = self._find_template(settings["image"])

        interfaces = [
            {"type": "public", "ip_addresses": {"ip_address": [{"family": "IPv4"}]}},
            {"type": "utility", "ip_addresses": {"ip_address": [{"family": "IPv4"}]}},
        ]
        for network in v.as_list(settings.get("networks")):
            interfaces.append(
                {
                    "type": "private",
                    "network": self._find_network(network)["uuid"],
                    "ip_addresses": {"ip_address": [{"family": "IPv4"}]},
                }
            )

        server = {
            "zone": settings["location"],
            "title": settings["name"],
            "hostname": _hostname(settings["name"]),
            "plan": plan["name"],
            "metadata": "yes",
            "labels": {"label": labels_to_api(settings["labels"])},
            "storage_devices": {
                "storage_device": [
                    {
                        "action": "clone",
                        "storage": template["id"],
                        "title": f"{settings['name']} disk",
                        "size": _int(plan.get("storage_size")) or 25,
                        "tier": plan.get("storage_tier") or DEFAULT_STORAGE_TIER,
                    }
                ]
            },
            "networking": {"interfaces": {"interface": interfaces}},
            "login_user": {
                "username": DEFAULT_LOGIN_USER,
                "create_password": "no",
                "ssh_keys": {"ssh_key": [key.strip() for key in ssh_keys]},
            },
        }
        if settings.get("user_data"):
            server["user_data"] = settings["user_data"]
        server.update(settings["vendor_settings"])

        created = (self.client.post("/server", json={"server": server}) or {}).get("server") or {}
        return self._server_details_object(created)

    def _delete_vms(self, obj: dict) -> dict:
        uuid = obj["id"]
        stop_sent = False
        state = None
        for _ in range(STOP_TIMEOUT // POLL_INTERVAL + 1):
            server = self._server_details(uuid)
            state = server.get("state")
            if state == "stopped":
                break
            if state == "started" and not stop_sent:
                self.client.post(
                    f"/server/{uuid}/stop",
                    json={"stop_server": {"stop_type": "soft", "timeout": "60"}},
                )
                stop_sent = True
            _sleep(POLL_INTERVAL)
        else:
            raise DbaError(
                f"The UpCloud server '{obj['name']}' did not stop within {STOP_TIMEOUT} seconds "
                f"(it is {state}); it was not deleted."
            )

        # storages=1 deletes every storage still attached, so the disks that
        # are not the server's own are detached first, to outlive it.
        detached = []
        system_disk = _system_disk(server)
        for device in _wrapped(server, "storage_devices", "storage_device"):
            if device.get("type", "disk") == "disk" and device is not system_disk:
                self.client.post(
                    f"/server/{uuid}/storage/detach",
                    json={"storage_device": {"address": device.get("address")}},
                )
                detached.append(device.get("storage"))

        self.client.delete(f"/server/{uuid}", params={"storages": 1})
        return {
            "status": "deleted",
            "stopped_first": stop_sent,
            "boot_disk_deleted": True,
            "volumes_detached": detached,
        }

    # --- volumes: storages ------------------------------------------------

    def _storage_object(self, storage: dict) -> dict:
        servers = _wrapped(storage, "servers", "server") if isinstance(storage.get("servers"), dict) else None
        return v.make_object(
            types.VOLUMES,
            storage.get("uuid"),
            storage.get("title"),
            storage.get("state"),
            storage.get("zone"),
            labels_from_api(storage.get("labels")),
            _created(storage.get("created")),
            "storage",
            size_gb=_int(storage.get("size")),
            tier=storage.get("tier"),
            encrypted=storage.get("encrypted"),
            servers=servers,
        )

    def _list_volumes(self, labels=None) -> list:
        storages = _wrapped(
            self.client.get("/storage/normal", params=_label_filter(labels)), "storages", "storage"
        )
        return [self._storage_object(storage) for storage in storages]

    def _info_volumes(self) -> dict:
        return {
            "tiers": [{"name": tier} for tier in STORAGE_TIERS],
            "locations": self._zones(),
        }

    def _status_volumes(self, obj: dict) -> dict:
        return self._storage_object((self.client.get(f"/storage/{obj['id']}") or {}).get("storage") or {})

    def _create_volumes(self, settings) -> dict:
        settings = self.check_create_settings(types.VOLUMES, settings)
        server = self._find_server(settings["vm"]) if settings.get("vm") else None
        zone = settings.get("location") or (server or {}).get("zone")
        if not zone:
            raise DbaError("Missing settings for volumes: location, or a vm to attach it to.")
        if server and settings.get("location") and settings["location"] != server.get("zone"):
            raise DbaError(
                f"The server '{settings['vm']}' is in {server.get('zone')}, not in {settings['location']}."
            )

        storage = {
            "size": _int(settings["size_gb"]),
            "tier": DEFAULT_STORAGE_TIER,
            "title": settings["name"],
            "zone": zone,
            "labels": labels_to_api(settings["labels"]),
        }
        storage.update(settings["vendor_settings"])
        created = (self.client.post("/storage", json={"storage": storage}) or {}).get("storage") or {}

        if server:
            self.client.post(
                f"/server/{server['uuid']}/storage/attach",
                json={"storage_device": {"type": "disk", "storage": created.get("uuid")}},
            )
            created["servers"] = {"server": [server["uuid"]]}
        return self._storage_object(created)

    def _delete_volumes(self, obj: dict) -> dict:
        storage = (self.client.get(f"/storage/{obj['id']}") or {}).get("storage") or {}
        servers = _wrapped(storage, "servers", "server")
        if servers:
            raise DbaError(
                f"The UpCloud storage '{obj['name']}' is attached to the server "
                f"{', '.join(servers)}; detach it or delete the server first."
            )
        self.client.delete(f"/storage/{obj['id']}")
        return {"status": "deleted"}

    # --- networking: SDN private networks ---------------------------------

    def _network_object(self, network: dict) -> dict:
        ip_networks = _wrapped(network, "ip_networks", "ip_network")
        servers = network.get("servers")
        server_uuids = (
            [server.get("uuid") for server in _wrapped(network, "servers", "server")]
            if isinstance(servers, dict)
            else None
        )
        return v.make_object(
            types.NETWORKING,
            network.get("uuid"),
            network.get("name"),
            v.STATUS_ACTIVE,
            network.get("zone"),
            labels_from_api(network.get("labels")),
            None,
            "network",
            ip_ranges=[ip.get("address") for ip in ip_networks],
            router=network.get("router"),
            servers=server_uuids,
        )

    def _list_networking(self, labels=None) -> list:
        return [
            self._network_object(network)
            for network in self._networks(labels)
            if network.get("type") == "private"
        ]

    def _info_networking(self) -> dict:
        return {"locations": self._zones()}

    def _status_networking(self, obj: dict) -> dict:
        return self._network_object((self.client.get(f"/network/{obj['id']}") or {}).get("network") or {})

    def _create_networking(self, settings) -> dict:
        settings = self.check_create_settings(types.NETWORKING, settings)
        network = {
            "name": settings["name"],
            "zone": settings["location"],
            "ip_networks": {
                "ip_network": [
                    {"address": settings["ip_range"], "dhcp": "yes", "family": "IPv4"}
                ]
            },
            "labels": labels_to_api(settings["labels"]),
        }
        network.update(settings["vendor_settings"])
        created = (self.client.post("/network", json={"network": network}) or {}).get("network") or {}
        return self._network_object(created)

    def _delete_networking(self, obj: dict) -> dict:
        self.client.delete(f"/network/{obj['id']}")
        return {"status": "deleted"}

    # --- load_balancers: Managed Load Balancers ---------------------------

    def _load_balancer_object(self, lb: dict) -> dict:
        return v.make_object(
            types.LOAD_BALANCERS,
            lb.get("uuid"),
            lb.get("name"),
            lb.get("operational_state"),
            lb.get("zone"),
            labels_from_api(lb.get("labels")),
            _created(lb.get("created_at")),
            "load-balancer",
            plan=lb.get("plan"),
            configured_status=lb.get("configured_status"),
            networks=[
                {key: net.get(key) for key in ("name", "type", "family", "uuid", "dns_name") if net.get(key)}
                for net in lb.get("networks") or []
            ],
            frontends=[
                {"name": fe.get("name"), "mode": fe.get("mode"), "port": fe.get("port")}
                for fe in lb.get("frontends") or []
            ],
            backends=[be.get("name") for be in lb.get("backends") or []],
        )

    def _list_load_balancers(self) -> list:
        return [self._load_balancer_object(lb) for lb in self._paged("/load-balancer")]

    def _info_load_balancers(self) -> dict:
        plans = self.client.get("/load-balancer/plans") or []
        return {
            "sizes": [
                {
                    "name": plan.get("name"),
                    "nodes": plan.get("server_number"),
                    "max_sessions_per_node": plan.get("per_server_max_sessions"),
                }
                for plan in plans
            ],
            "locations": self._zones(),
        }

    def _status_load_balancers(self, obj: dict) -> dict:
        return self._load_balancer_object(self.client.get(f"/load-balancer/{obj['id']}") or {})

    def _target_ip(self, target, network_uuid: str, servers: list) -> dict:
        """The name and address on the private network of a target server."""
        server = self._server_details(self._find_server(target, servers)["uuid"])
        interfaces = ((server.get("networking") or {}).get("interfaces") or {}).get("interface") or []
        for interface in interfaces:
            if interface.get("network") == network_uuid:
                for ip in _wrapped(interface, "ip_addresses", "ip_address"):
                    if ip.get("family", "IPv4") == "IPv4" and ip.get("address"):
                        return {"name": server.get("title"), "ip": ip["address"]}
        raise DbaError(
            f"The UpCloud server '{target}' has no address on the load balancer's private network."
        )

    def _create_load_balancers(self, settings) -> dict:
        settings = self.check_create_settings(types.LOAD_BALANCERS, settings)
        network = self._find_network(settings["network"])
        services = settings["services"]
        targets = v.as_list(settings.get("targets"))
        servers = self._servers() if targets else []
        members = [self._target_ip(target, network["uuid"], servers) for target in targets]

        frontends, backends = [], []
        for index, service in enumerate(services, start=1):
            protocol = str(service.get("protocol") or "tcp").lower()
            listen_port = _int(service.get("listen_port"))
            destination_port = _int(service.get("destination_port")) or listen_port
            backend = f"backend-{index}"
            backends.append(
                {
                    "name": backend,
                    "members": [
                        {
                            "name": _hostname(member["name"]),
                            "ip": member["ip"],
                            "port": destination_port,
                            "type": "static",
                            "weight": 100,
                            "max_sessions": 1000,
                            "enabled": True,
                        }
                        for member in members
                    ],
                }
            )
            frontends.append(
                {
                    "name": f"frontend-{index}",
                    "mode": protocol,
                    "port": listen_port,
                    "default_backend": backend,
                    "networks": [{"name": "public"}],
                }
            )

        lb = {
            "name": settings["name"],
            "plan": settings["size"],
            "zone": settings["location"],
            "configured_status": "started",
            "networks": [
                {"name": "public", "type": "public", "family": "IPv4"},
                {"name": "private", "type": "private", "family": "IPv4", "uuid": network["uuid"]},
            ],
            "frontends": frontends,
            "backends": backends,
            "labels": labels_to_api(settings["labels"]),
        }
        lb.update(settings["vendor_settings"])
        return self._load_balancer_object(self.client.post("/load-balancer", json=lb) or {})

    def _delete_load_balancers(self, obj: dict) -> dict:
        self.client.delete(f"/load-balancer/{obj['id']}")
        return {"status": "deleted"}

    # --- object_storage: Managed Object Storage ---------------------------

    def _object_storage_object(self, service: dict) -> dict:
        return v.make_object(
            types.OBJECT_STORAGE,
            service.get("uuid"),
            service.get("name"),
            service.get("operational_state"),
            service.get("region"),
            labels_from_api(service.get("labels")),
            _created(service.get("created_at")),
            "object-storage-2",
            configured_status=service.get("configured_status"),
            endpoints=[
                {"domain_name": endpoint.get("domain_name"), "type": endpoint.get("type")}
                for endpoint in service.get("endpoints") or []
            ],
        )

    def _list_object_storage(self) -> list:
        return [self._object_storage_object(service) for service in self._paged("/object-storage-2")]

    def _info_object_storage(self) -> dict:
        regions = self.client.get("/object-storage-2/regions") or []
        return {
            "locations": [
                {
                    "name": region.get("name"),
                    "primary_zone": region.get("primary_zone"),
                    "zones": [zone.get("name") for zone in region.get("zones") or []],
                }
                for region in regions
            ]
        }

    def _status_object_storage(self, obj: dict) -> dict:
        return self._object_storage_object(self.client.get(f"/object-storage-2/{obj['id']}") or {})

    def _create_object_storage(self, settings) -> dict:
        settings = self.check_create_settings(types.OBJECT_STORAGE, settings)
        service = {
            "name": settings["name"],
            "region": settings["location"],
            "configured_status": "started",
            "networks": [{"name": "public", "type": "public", "family": "IPv4"}],
            "labels": labels_to_api(settings["labels"]),
        }
        service.update(settings["vendor_settings"])
        return self._object_storage_object(self.client.post("/object-storage-2", json=service) or {})

    def _delete_object_storage(self, obj: dict) -> dict:
        # Accepted, not done: the service is listed for a minute or more, and
        # a second DELETE meanwhile answers 500. The dba task waits it out.
        self.client.delete(f"/object-storage-2/{obj['id']}")
        return {"status": "deleting"}
