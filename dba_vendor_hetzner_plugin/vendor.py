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

"""The Hetzner Cloud vendor: servers and the infrastructure around them.

Talks to the Hetzner Cloud API (https://docs.hetzner.cloud/reference/cloud)
through the shared :class:`RestClient`. An API token belongs to one Hetzner
project, so the dba project picks the token and nothing else.

Hetzner's names for the standardized types: vms are "server", volumes
"volume", networking "network", firewalls "firewall", load_balancers
"load_balancer", ssh_keys "ssh_key" and dns "zone". Object storage is
S3-only, not part of the Cloud API, and is not supported.

What the API makes worth knowing:

* A server created without an SSH key gets a root password, which the API
  answers with - and which would end up wherever the answer goes, an
  agent's transcript among them. Deploying a VM therefore requires SSH keys,
  and the password field is never passed on.
* Most changes answer with an action that keeps running. Deploying does not
  wait for it and reports it instead; deleting waits for it, bounded, as for
  what has to finish first - a volume's detach, a firewall's removal from
  the servers it is applied to.
* Prices come net and gross of VAT; they are reported net, in the currency
  ``/pricing`` names.
"""

# cSpell:ignore mysqlsh MariaDB hetzner rrsets proxyprotocol

import time

from dba_plugin.lib import object_types as types, rest_client, vendor as v, vendors
from dba_plugin.lib.general import DbaError

BASE_URL = "https://api.hetzner.cloud/v1"

# The most items a page of a Hetzner listing holds.
PAGE_SIZE = 50
# A stop for a listing that never ends.
LIMIT_PAGES = 100

# How long a delete waits for an action that has to finish first, and how
# often it asks.
ACTION_TIMEOUT = 120.0
ACTION_POLL_INTERVAL = 2.0

# Replaced by the tests, so that waiting for an action takes no time.
_sleep = time.sleep

# The record types a zone's rrsets can have.
DNS_RECORD_TYPES = (
    "A", "AAAA", "CAA", "CNAME", "DS", "HINFO", "HTTPS", "MX", "NS", "PTR",
    "RP", "SOA", "SRV", "SVCB", "TLSA", "TXT",
)

# The keys of a firewall rule in the standardized settings.
FIREWALL_RULE_KEYS = ("direction", "protocol", "port", "source_ips", "destination_ips", "description")
FIREWALL_PROTOCOLS = ("tcp", "udp", "icmp", "esp", "gre")

_SSH_KEYS_REQUIRED = (
    "Deploying a Hetzner Cloud VM requires ssh_keys: without one, Hetzner "
    "creates a root password and sends it back in the answer. Store a key "
    "with deployObject('ssh_keys', {name, public_key}) and name it in ssh_keys."
)


# --- Converting what the API answers --------------------------------------


def _net_price(price: dict, key: str):
    """A price's net amount as a number, or None."""
    try:
        return float(((price or {}).get(key) or {}).get("net"))
    except (TypeError, ValueError):
        return None


def _prices(prices, currency) -> list:
    """The per-location prices of a server or load balancer type."""
    return [
        {
            "location": price.get("location"),
            "hourly": _net_price(price, "price_hourly"),
            "monthly": _net_price(price, "price_monthly"),
            "currency": currency,
        }
        for price in prices or []
    ]


def _included_traffic_tb(prices):
    """The traffic a type includes per month, in TB, from its prices.

    Hetzner gives it in bytes, per location, and sells it as binary TB: 20
    TB are 20 * 1024^4 bytes.
    """
    for price in prices or []:
        if price.get("included_traffic") is not None:
            return round(price["included_traffic"] / 1024**4, 2)
    return None


def _ip(public_net, family: str):
    """The address of one family of a public_net, or None."""
    return (((public_net or {}).get(family)) or {}).get("ip")


def _action(action):
    """An action, as compact as a caller needs it, or None."""
    if not action:
        return None
    error = action.get("error") or {}
    result = {
        "id": action.get("id"),
        "command": action.get("command"),
        "status": action.get("status"),
        "progress": action.get("progress"),
        "error": error.get("message"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _deployed(obj: dict, answer: dict) -> dict:
    """A deployed object, with the actions the deployment started."""
    action = _action(answer.get("action"))
    if action:
        obj["properties"]["action"] = action
    for key in ("actions", "next_actions"):
        actions = [_action(item) for item in answer.get(key) or []]
        if actions:
            obj["properties"][key] = actions
    return obj


def _server(server: dict) -> dict:
    public_net = server.get("public_net") or {}
    datacenter = server.get("datacenter") or {}
    location = server.get("location") or datacenter.get("location") or {}
    image = server.get("image") or {}
    return v.make_object(
        types.VMS,
        server.get("id"),
        server.get("name"),
        server.get("status"),
        location.get("name"),
        server.get("labels"),
        server.get("created"),
        "server",
        size=(server.get("server_type") or {}).get("name"),
        image=image.get("name") or image.get("description"),
        ipv4=_ip(public_net, "ipv4"),
        ipv6=_ip(public_net, "ipv6"),
        private_ips=[net.get("ip") for net in server.get("private_net") or []],
        networks=[net.get("network") for net in server.get("private_net") or []],
        volumes=list(server.get("volumes") or []),
        firewalls=[item.get("id") for item in public_net.get("firewalls") or []],
        datacenter=datacenter.get("name"),
    )


def _volume(volume: dict) -> dict:
    # assignee replaces the deprecated server field.
    assignee = volume.get("assignee") or {}
    server = assignee.get("id") if assignee.get("type") == "server" else None
    if server is None:
        server = volume.get("server")
    return v.make_object(
        types.VOLUMES,
        volume.get("id"),
        volume.get("name"),
        volume.get("status"),
        (volume.get("location") or {}).get("name"),
        volume.get("labels"),
        volume.get("created"),
        "volume",
        size_gb=volume.get("size"),
        server=server,
        linux_device=volume.get("linux_device"),
        format=volume.get("format"),
    )


def _network(network: dict) -> dict:
    subnets = network.get("subnets") or []
    return v.make_object(
        types.NETWORKING,
        network.get("id"),
        network.get("name"),
        v.STATUS_ACTIVE,
        subnets[0].get("network_zone") if subnets else None,
        network.get("labels"),
        network.get("created"),
        "network",
        ip_range=network.get("ip_range"),
        subnets=[
            {
                "type": subnet.get("type"),
                "ip_range": subnet.get("ip_range"),
                "network_zone": subnet.get("network_zone"),
            }
            for subnet in subnets
        ],
        servers=list(network.get("servers") or []),
        load_balancers=list(network.get("load_balancers") or []),
    )


def _firewall(firewall: dict) -> dict:
    applied_to = firewall.get("applied_to") or []
    return v.make_object(
        types.FIREWALLS,
        firewall.get("id"),
        firewall.get("name"),
        v.STATUS_ACTIVE,
        None,
        firewall.get("labels"),
        firewall.get("created"),
        "firewall",
        rules=[
            {key: rule.get(key) for key in FIREWALL_RULE_KEYS if rule.get(key) not in (None, [])}
            for rule in firewall.get("rules") or []
        ],
        servers=[
            item["server"]["id"]
            for item in applied_to
            if item.get("type") == "server" and item.get("server")
        ],
        label_selectors=[
            item["label_selector"]["selector"]
            for item in applied_to
            if item.get("type") == "label_selector" and item.get("label_selector")
        ],
    )


def _target(target: dict) -> dict:
    """A load balancer target as ``{type, server | label_selector | ip}``."""
    kind = target.get("type")
    value = target.get(kind) or {}
    return {
        "type": kind,
        kind: value.get("id") if kind == "server" else value.get("selector") or value.get("ip"),
    }


def _load_balancer(load_balancer: dict) -> dict:
    public_net = load_balancer.get("public_net") or {}
    return v.make_object(
        types.LOAD_BALANCERS,
        load_balancer.get("id"),
        load_balancer.get("name"),
        v.STATUS_ACTIVE,
        (load_balancer.get("location") or {}).get("name"),
        load_balancer.get("labels"),
        load_balancer.get("created"),
        "load_balancer",
        size=(load_balancer.get("load_balancer_type") or {}).get("name"),
        ipv4=_ip(public_net, "ipv4"),
        ipv6=_ip(public_net, "ipv6"),
        private_ips=[net.get("ip") for net in load_balancer.get("private_net") or []],
        networks=[net.get("network") for net in load_balancer.get("private_net") or []],
        algorithm=(load_balancer.get("algorithm") or {}).get("type"),
        services=[
            {
                "protocol": service.get("protocol"),
                "listen_port": service.get("listen_port"),
                "destination_port": service.get("destination_port"),
            }
            for service in load_balancer.get("services") or []
        ],
        targets=[_target(target) for target in load_balancer.get("targets") or []],
    )


def _zone(zone: dict) -> dict:
    return v.make_object(
        types.DNS,
        zone.get("id"),
        zone.get("name"),
        zone.get("status"),
        None,
        zone.get("labels"),
        zone.get("created"),
        "zone",
        mode=zone.get("mode"),
        ttl=zone.get("ttl"),
        record_count=zone.get("record_count"),
        nameservers=(zone.get("authoritative_nameservers") or {}).get("assigned"),
    )


def _ssh_key(ssh_key: dict) -> dict:
    return v.make_object(
        types.SSH_KEYS,
        ssh_key.get("id"),
        ssh_key.get("name"),
        v.STATUS_ACTIVE,
        None,
        ssh_key.get("labels"),
        ssh_key.get("created"),
        "ssh_key",
        fingerprint=ssh_key.get("fingerprint"),
    )


def _body(**fields) -> dict:
    """A request body without the fields that were not given."""
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


@vendors.register
class Hetzner(v.Vendor):
    """Hetzner Cloud, through its Cloud API."""

    name = "hetzner"
    display_name = "Hetzner Cloud"
    feature_sets = (types.CORE_COMPUTE,)
    native_projects = True
    api_key_help = (
        "Create an API token in the Hetzner Console (https://console.hetzner.com): "
        "open the project, then Security > API tokens > Generate API token, with "
        "Read & Write permission to deploy. A token belongs to one project, so "
        "store one per project."
    )

    def create_client(self):
        return rest_client.RestClient(
            self.display_name, BASE_URL, headers={"Authorization": f"Bearer {self.api_key}"}
        )

    # --- Helpers ----------------------------------------------------------

    def _all(self, items_key: str, **params) -> list:
        """Every item of the listing ``/<items_key>``, through its pages."""
        items = []
        page = 1
        for _ in range(LIMIT_PAGES):
            body = self.client.get(
                f"/{items_key}", params={**params, "page": page, "per_page": PAGE_SIZE}
            ) or {}
            items.extend(body.get(items_key) or [])
            page = ((body.get("meta") or {}).get("pagination") or {}).get("next_page")
            if not page:
                break
        return items

    def _pricing(self) -> dict:
        return (self.client.get("/pricing") or {}).get("pricing") or {}

    def _locations(self) -> list:
        return [
            {
                "name": location.get("name"),
                "description": location.get("description"),
                "country": location.get("country"),
                "city": location.get("city"),
                "network_zone": location.get("network_zone"),
            }
            for location in self._all("locations")
        ]

    def _resolve_ids(self, items_key: str, what: str, values) -> list:
        """The ids of objects given by name or id, in the order given.

        Raises:
            DbaError: One of them does not exist.
        """
        values = v.as_list(values)
        if not values:
            return []

        items = self._all(items_key)
        by_name = {item.get("name"): item["id"] for item in items}
        by_id = {str(item["id"]): item["id"] for item in items}

        ids, unknown = [], []
        for value in values:
            key = str(value)
            if key in by_name:
                ids.append(by_name[key])
            elif key in by_id:
                ids.append(by_id[key])
            else:
                unknown.append(key)
        if unknown:
            raise DbaError(
                f"There is no {what} named {', '.join(repr(name) for name in unknown)} "
                f"at {self.display_name}"
                + (f" in project '{self.project}'" if self.project else "")
                + ". Known: "
                + (", ".join(sorted(str(name) for name in by_name)) or "none")
                + "."
            )
        return ids

    def _resolve_id(self, items_key: str, what: str, value):
        """The id of one object given by name or id, or None for none."""
        ids = self._resolve_ids(items_key, what, value)
        if len(ids) > 1:
            raise DbaError(f"Name one {what}, not {len(ids)}.")
        return ids[0] if ids else None

    def _network_zone(self, location: str) -> str:
        """The network zone a location is in; a network zone is itself."""
        locations = self._all("locations")
        zones = sorted({item.get("network_zone") for item in locations if item.get("network_zone")})
        if location in zones:
            return location
        for item in locations:
            if item.get("name") == location:
                return item.get("network_zone")
        raise DbaError(
            f"Unknown location or network zone '{location}' at {self.display_name}. Use one of: "
            + ", ".join(zones + sorted(item.get("name") for item in locations))
            + "."
        )

    def _wait(self, action) -> dict:
        """Waits, bounded, for an action to finish and returns it.

        Raises:
            DbaError: The action failed, or is still running at the timeout.
        """
        if not action:
            return None
        polls = int(ACTION_TIMEOUT / ACTION_POLL_INTERVAL)
        for poll in range(polls + 1):
            if action.get("status") == "success":
                return _action(action)
            if action.get("status") == "error":
                raise DbaError(
                    f"The {self.display_name} action {action.get('command')} "
                    f"({action.get('id')}) failed: "
                    + ((action.get("error") or {}).get("message") or "no reason given")
                    + "."
                )
            if poll == polls:
                break
            _sleep(ACTION_POLL_INTERVAL)
            action = (self.client.get(f"/actions/{action['id']}") or {}).get("action") or action

        raise DbaError(
            f"The {self.display_name} action {action.get('command')} ({action.get('id')}) "
            f"is still running after {int(ACTION_TIMEOUT)} seconds. Try again once it finished."
        )

    def _deleted(self, answer) -> dict:
        """What a delete reports, once the action it started finished.

        Waiting keeps what depends on the deleted object - a network the
        deleted server was attached to - from finding it still there.
        """
        action = self._wait((answer or {}).get("action"))
        return {"action": action, "status": "deleted"} if action else {"status": "deleted"}

    # --- vms --------------------------------------------------------------

    def _list_vms(self) -> list:
        return [_server(server) for server in self._all("servers")]

    def _info_vms(self) -> dict:
        currency = self._pricing().get("currency")
        sizes = [
            {
                "name": server_type.get("name"),
                "description": server_type.get("description"),
                "architecture": v.architecture(server_type.get("architecture")),
                "cpu_type": server_type.get("cpu_type"),
                "cores": server_type.get("cores"),
                "memory_gb": server_type.get("memory"),
                "disk_gb": server_type.get("disk"),
                "included_traffic_tb": _included_traffic_tb(server_type.get("prices")),
                "prices": _prices(server_type.get("prices"), currency),
                "deprecated": bool(server_type.get("deprecated")),
            }
            for server_type in self._all("server_types")
        ]
        images = [
            {
                "name": image.get("name"),
                "description": image.get("description"),
                "architecture": v.architecture(image.get("architecture")),
                "os_flavor": image.get("os_flavor"),
            }
            for image in self._all("images", type="system", status="available")
        ]
        return {"sizes": sizes, "locations": self._locations(), "images": images}

    def _status_vms(self, obj: dict) -> dict:
        return _server((self.client.get(f"/servers/{obj['id']}") or {}).get("server") or {})

    def _deploy_vms(self, settings) -> dict:
        settings = v.check_settings(types.VMS, settings, required=("name", "size", "image"))
        vendor_settings = settings["vendor_settings"]
        if not settings.get("ssh_keys") and not vendor_settings.get("ssh_keys"):
            raise DbaError(_SSH_KEYS_REQUIRED)

        body = _body(
            name=settings["name"],
            server_type=settings["size"],
            image=settings["image"],
            location=settings.get("location"),
            ssh_keys=self._resolve_ids("ssh_keys", "SSH key", settings.get("ssh_keys")),
            networks=self._resolve_ids("networks", "network", settings.get("networks")),
            firewalls=[
                {"firewall": firewall_id}
                for firewall_id in self._resolve_ids("firewalls", "firewall", settings.get("firewalls")
                )
            ],
            user_data=settings.get("user_data"),
            labels=settings["labels"],
        )
        body.update(vendor_settings)
        if not body.get("ssh_keys"):
            raise DbaError(_SSH_KEYS_REQUIRED)

        answer = self.client.post("/servers", json=body) or {}
        # The answer's root_password is null with SSH keys, and is never
        # passed on in any case.
        return _deployed(_server(answer.get("server") or {}), answer)

    def _delete_vms(self, obj: dict) -> dict:
        return self._deleted(self.client.delete(f"/servers/{obj['id']}"))

    # --- volumes ----------------------------------------------------------

    def _list_volumes(self) -> list:
        return [_volume(volume) for volume in self._all("volumes")]

    def _info_volumes(self) -> dict:
        pricing = self._pricing()
        return {
            "locations": self._locations(),
            "prices": [
                {
                    "price_per_gb_month": _net_price(pricing.get("volume"), "price_per_gb_month"),
                    "currency": pricing.get("currency"),
                }
            ],
            "formats": ["ext4", "xfs"],
        }

    def _status_volumes(self, obj: dict) -> dict:
        return _volume((self.client.get(f"/volumes/{obj['id']}") or {}).get("volume") or {})

    def _deploy_volumes(self, settings) -> dict:
        settings = v.check_settings(
            types.VOLUMES, settings, required=("name", "size_gb"),
            supported=("size_gb", "location", "vm", "format"),
        )
        if not settings.get("location") and not settings.get("vm"):
            raise DbaError("A volume needs a location, or a vm to attach it to.")
        try:
            size = int(settings["size_gb"])
        except (TypeError, ValueError) as e:
            raise DbaError(f"size_gb must be a whole number of GB, not '{settings['size_gb']}'.") from e

        server_id = self._resolve_id("servers", "VM", settings.get("vm"))
        body = _body(
            name=settings["name"],
            size=size,
            location=settings.get("location"),
            format=settings.get("format"),
            assignee={"type": "server", "id": server_id} if server_id is not None else None,
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/volumes", json=body) or {}
        return _deployed(_volume(answer.get("volume") or {}), answer)

    def _delete_volumes(self, obj: dict) -> dict:
        result = {}
        if obj["properties"].get("server") is not None:
            # An attached volume cannot be deleted: detach it first.
            answer = self.client.post(f"/volumes/{obj['id']}/actions/detach") or {}
            result["detach"] = self._wait(answer.get("action"))
        result.update(self._deleted(self.client.delete(f"/volumes/{obj['id']}")))
        return result

    # --- networking -------------------------------------------------------

    def _list_networking(self) -> list:
        return [_network(network) for network in self._all("networks")]

    def _info_networking(self) -> dict:
        zones = {}
        for location in self._all("locations"):
            zones.setdefault(location.get("network_zone"), []).append(location.get("name"))
        return {
            "network_zones": [
                {"name": zone, "locations": sorted(locations)}
                for zone, locations in sorted(zones.items(), key=lambda item: str(item[0]))
            ]
        }

    def _deploy_networking(self, settings) -> dict:
        settings = v.check_settings(types.NETWORKING, settings, required=("name", "ip_range"))
        subnets = None
        if settings.get("location"):
            # One subnet spanning the network, in the location's zone, so
            # that servers there can be attached right away.
            subnets = [
                {
                    "type": "cloud",
                    "ip_range": settings["ip_range"],
                    "network_zone": self._network_zone(settings["location"]),
                }
            ]
        body = _body(
            name=settings["name"],
            ip_range=settings["ip_range"],
            subnets=subnets,
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/networks", json=body) or {}
        return _deployed(_network(answer.get("network") or {}), answer)

    def _delete_networking(self, obj: dict) -> dict:
        return self._deleted(self.client.delete(f"/networks/{obj['id']}"))

    # --- firewalls --------------------------------------------------------

    def _list_firewalls(self) -> list:
        return [_firewall(firewall) for firewall in self._all("firewalls")]

    def _info_firewalls(self) -> dict:
        return {"directions": ["in", "out"], "protocols": list(FIREWALL_PROTOCOLS)}

    @staticmethod
    def _firewall_rule(rule) -> dict:
        """A standardized firewall rule as Hetzner takes it."""
        if not isinstance(rule, dict):
            raise DbaError("Each firewall rule must be a dict.")
        unknown = sorted(set(rule) - set(FIREWALL_RULE_KEYS))
        if unknown:
            raise DbaError(
                f"Unsupported firewall rule keys: {', '.join(unknown)}. "
                f"Supported: {', '.join(FIREWALL_RULE_KEYS)}."
            )

        direction = str(rule.get("direction") or "in").lower()
        if direction not in ("in", "out"):
            raise DbaError(f"A firewall rule's direction is 'in' or 'out', not '{direction}'.")
        protocol = str(rule.get("protocol") or "").lower()
        if protocol not in FIREWALL_PROTOCOLS:
            raise DbaError(
                f"A firewall rule's protocol is one of {', '.join(FIREWALL_PROTOCOLS)}, "
                f"not '{rule.get('protocol')}'."
            )
        ips_key = "source_ips" if direction == "in" else "destination_ips"
        ips = v.as_list(rule.get(ips_key))
        if not ips:
            raise DbaError(f"A firewall rule of direction '{direction}' needs {ips_key}.")

        return _body(
            direction=direction,
            protocol=protocol,
            port=None if rule.get("port") in (None, "") else str(rule["port"]),
            **{ips_key: ips},
            description=rule.get("description"),
        )

    def _deploy_firewalls(self, settings) -> dict:
        settings = v.check_settings(types.FIREWALLS, settings)
        rules = [self._firewall_rule(rule) for rule in v.as_list(settings.get("rules"))]
        body = _body(
            name=settings["name"],
            rules=rules,
            apply_to=[
                {"type": "server", "server": {"id": server_id}}
                for server_id in self._resolve_ids("servers", "VM", settings.get("vms"))
            ],
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/firewalls", json=body) or {}
        return _deployed(_firewall(answer.get("firewall") or {}), answer)

    def _delete_firewalls(self, obj: dict) -> dict:
        result = {}
        remove_from = [
            {"type": "server", "server": {"id": int(server_id)}}
            for server_id in obj["properties"].get("servers") or []
        ] + [
            {"type": "label_selector", "label_selector": {"selector": selector}}
            for selector in obj["properties"].get("label_selectors") or []
        ]
        if remove_from:
            # A firewall still applied to something cannot be deleted.
            answer = self.client.post(
                f"/firewalls/{obj['id']}/actions/remove_from_resources",
                json={"remove_from": remove_from},
            ) or {}
            result["removed_from_resources"] = [
                self._wait(action) for action in answer.get("actions") or []
            ]
        result.update(self._deleted(self.client.delete(f"/firewalls/{obj['id']}")))
        return result

    # --- load_balancers ---------------------------------------------------

    def _list_load_balancers(self) -> list:
        return [
            _load_balancer(load_balancer)
            for load_balancer in self._all("load_balancers")
        ]

    def _info_load_balancers(self) -> dict:
        currency = self._pricing().get("currency")
        sizes = [
            {
                "name": lb_type.get("name"),
                "description": lb_type.get("description"),
                "max_connections": lb_type.get("max_connections"),
                "max_services": lb_type.get("max_services"),
                "max_targets": lb_type.get("max_targets"),
                "included_traffic_tb": _included_traffic_tb(lb_type.get("prices")),
                "prices": _prices(lb_type.get("prices"), currency),
                "deprecated": bool(lb_type.get("deprecated")),
            }
            for lb_type in self._all("load_balancer_types")
        ]
        return {
            "sizes": sizes,
            "locations": self._locations(),
            "algorithms": ["round_robin", "least_connections"],
        }

    def _status_load_balancers(self, obj: dict) -> dict:
        return _load_balancer(
            (self.client.get(f"/load_balancers/{obj['id']}") or {}).get("load_balancer") or {}
        )

    @staticmethod
    def _lb_service(service) -> dict:
        """A standardized load balancer service as Hetzner takes it.

        Hetzner requires a health check and the proxy protocol setting; a
        TCP check of the destination port is the default. Keys beyond the
        standardized ones are passed through.
        """
        if not isinstance(service, dict) or service.get("listen_port") in (None, ""):
            raise DbaError("Each load balancer service must be a dict with a listen_port.")
        try:
            listen_port = int(service["listen_port"])
            destination_port = int(service.get("destination_port") or listen_port)
        except (TypeError, ValueError) as e:
            raise DbaError("A load balancer service's ports must be numbers.") from e

        return {
            "protocol": "tcp",
            "proxyprotocol": False,
            "health_check": {
                "protocol": "tcp",
                "port": destination_port,
                "interval": 15,
                "timeout": 10,
                "retries": 3,
            },
            **service,
            "listen_port": listen_port,
            "destination_port": destination_port,
        }

    def _deploy_load_balancers(self, settings) -> dict:
        settings = v.check_settings(types.LOAD_BALANCERS, settings, required=("name", "size"))
        network_id = self._resolve_id("networks", "network", settings.get("network"))
        body = _body(
            name=settings["name"],
            load_balancer_type=settings["size"],
            location=settings.get("location"),
            network=network_id,
            services=[self._lb_service(service) for service in v.as_list(settings.get("services"))],
            targets=[
                # Through the private network when there is one.
                {"type": "server", "server": {"id": server_id}, "use_private_ip": network_id is not None}
                for server_id in self._resolve_ids("servers", "VM", settings.get("targets")
                )
            ],
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/load_balancers", json=body) or {}
        return _deployed(_load_balancer(answer.get("load_balancer") or {}), answer)

    def _delete_load_balancers(self, obj: dict) -> dict:
        return self._deleted(self.client.delete(f"/load_balancers/{obj['id']}"))

    # --- dns --------------------------------------------------------------

    def _list_dns(self) -> list:
        return [_zone(zone) for zone in self._all("zones")]

    def _info_dns(self) -> dict:
        return {"record_types": list(DNS_RECORD_TYPES)}

    def _status_dns(self, obj: dict) -> dict:
        return _zone((self.client.get(f"/zones/{obj['id']}") or {}).get("zone") or {})

    @staticmethod
    def _rrsets(records) -> list:
        """Standardized records as Hetzner's rrsets: one per name and type."""
        rrsets = {}
        for record in v.as_list(records):
            if not isinstance(record, dict) or not record.get("type") or record.get("value") in (None, ""):
                raise DbaError("Each DNS record must be a dict with a type and a value.")
            record_type = str(record["type"]).upper()
            if record_type not in DNS_RECORD_TYPES:
                raise DbaError(
                    f"Unknown DNS record type '{record['type']}'. Use one of: "
                    + ", ".join(DNS_RECORD_TYPES)
                    + "."
                )
            name = str(record.get("name") or "@")
            rrset = rrsets.setdefault(
                (name, record_type), {"name": name, "type": record_type, "records": []}
            )
            rrset["records"].append({"value": str(record["value"])})
            if record.get("ttl") is not None:
                rrset["ttl"] = int(record["ttl"])
        return list(rrsets.values())

    def _deploy_dns(self, settings) -> dict:
        settings = v.check_settings(types.DNS, settings)
        body = _body(
            name=settings["name"],
            mode="primary",
            ttl=None if settings.get("ttl") is None else int(settings["ttl"]),
            rrsets=self._rrsets(settings.get("records")),
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/zones", json=body) or {}
        return _deployed(_zone(answer.get("zone") or {}), answer)

    def _delete_dns(self, obj: dict) -> dict:
        return self._deleted(self.client.delete(f"/zones/{obj['id']}"))

    # --- ssh_keys ---------------------------------------------------------

    def _list_ssh_keys(self) -> list:
        return [_ssh_key(ssh_key) for ssh_key in self._all("ssh_keys")]

    def _deploy_ssh_keys(self, settings) -> dict:
        settings = v.check_settings(types.SSH_KEYS, settings, required=("name", "public_key"))
        body = _body(
            name=settings["name"],
            public_key=str(settings["public_key"]).strip(),
            labels=settings["labels"],
        )
        body.update(settings["vendor_settings"])

        answer = self.client.post("/ssh_keys", json=body) or {}
        return _ssh_key(answer.get("ssh_key") or {})

    def _delete_ssh_keys(self, obj: dict) -> dict:
        return self._deleted(self.client.delete(f"/ssh_keys/{obj['id']}"))
