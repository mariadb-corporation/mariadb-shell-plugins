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

"""Tests for the Hetzner Cloud vendor, against a mocked Cloud API."""

# cSpell:ignore mysqlsh MariaDB hetzner rrsets proxyprotocol

import pytest

from dba_plugin.lib import api_keys, object_types as types, operations, vendors
from dba_plugin.lib.general import DbaError
from dba_vendor_hetzner_plugin import vendor as hetzner
from dba_vendor_hetzner_plugin.vendor import Hetzner

TOKEN = "hc-test-token-never-shown"


@pytest.fixture
def vendor(mock_api, monkeypatch):
    monkeypatch.setattr(hetzner, "_sleep", lambda seconds: None)
    instance = Hetzner(api_key=TOKEN, project="prod")
    yield instance
    instance.close()


def _page(key, items, next_page=None):
    return {key: items, "meta": {"pagination": {"page": 1, "per_page": 50, "next_page": next_page}}}


def _price(location, hourly, monthly):
    return {
        "location": location,
        "price_hourly": {"net": hourly, "gross": "0"},
        "price_monthly": {"net": monthly, "gross": "0"},
        "included_traffic": 21990232555520,
        "price_per_tb_traffic": {"net": "1.00", "gross": "1.19"},
    }


def _server(id, name, labels=None, status="running", **extra):
    return {
        "id": id,
        "name": name,
        "status": status,
        "created": "2026-10-01T10:00:00Z",
        "labels": labels or {},
        "server_type": {"name": "cx22", "cores": 2},
        "location": {"name": "fsn1", "network_zone": "eu-central"},
        "image": {"name": "ubuntu-24.04", "description": "Ubuntu 24.04"},
        "public_net": {
            "ipv4": {"ip": "192.0.2.10"},
            "ipv6": {"ip": "2001:db8::/64"},
            "floating_ips": [],
            "firewalls": [{"id": 7, "status": "applied"}],
        },
        "private_net": [{"network": 3, "ip": "10.0.0.2"}],
        "volumes": [11],
        "root_password": None,
        **extra,
    }


LOCATIONS = [
    {"name": "fsn1", "description": "Falkenstein DC Park 1", "country": "DE", "city": "Falkenstein", "network_zone": "eu-central"},
    {"name": "hel1", "description": "Helsinki DC Park 1", "country": "FI", "city": "Helsinki", "network_zone": "eu-central"},
    {"name": "ash", "description": "Ashburn, VA", "country": "US", "city": "Ashburn, VA", "network_zone": "us-east"},
]
PRICING = {"pricing": {"currency": "EUR", "vat_rate": "19.00", "volume": {"price_per_gb_month": {"net": "0.044", "gross": "0.05"}}}}


# --- Registration -----------------------------------------------------------


def test_the_vendor_is_registered_with_its_object_support():
    assert vendors.get_vendor("hetzner") is Hetzner
    assert Hetzner.native_projects is True
    assert Hetzner.feature_sets == (types.CORE_COMPUTE,)
    assert Hetzner.object_types() == [
        "vms", "volumes", "networking", "firewalls", "load_balancers", "dns", "ssh_keys",
    ]

    support = {entry["object_type"]: entry["operations"] for entry in Hetzner.object_support()}
    assert support["vms"] == ["info", "list", "status", "deploy", "delete"]
    assert support["ssh_keys"] == ["list", "status", "deploy", "delete"]
    assert "object_storage" not in support
    assert "managed_mariadb_services" not in support


def test_what_is_not_supported_is_refused(vendor):
    with pytest.raises(DbaError, match="does not support object_storage"):
        vendor.list_objects("object_storage")
    with pytest.raises(DbaError, match="does not support 'info' for ssh_keys"):
        vendor.object_info("ssh_keys")


# --- vms ----------------------------------------------------------------------


def test_a_tag_filter_is_asked_of_hetzner_and_checked_here(vendor, mock_api):
    # the API's answer is taken as a superset: what does not match is dropped here
    mock_api.add("GET", "/v1/servers", json=_page(
        "servers", [_server(1, "db1", {"env": "prod", "role": "galera"}), _server(2, "db2", {"env": "test"})]
    ))
    assert [o["name"] for o in vendor.list_objects("vms", "env=prod, role")] == ["db1"]
    [request] = mock_api.requests_to("GET", "/v1/servers")
    assert request["params"]["label_selector"] == "env=prod,role"
    # no filter, no selector
    vendor.list_objects("vms")
    assert "label_selector" not in mock_api.requests_to("GET", "/v1/servers")[1]["params"]


def test_vms_are_listed_through_every_page_and_filtered_by_labels(vendor, mock_api):
    pages = {
        "1": _page("servers", [_server(1, "db1", {"env": "prod", "role": "galera"})], next_page=2),
        "2": _page("servers", [_server(2, "db2", {"env": "test"})]),
    }
    mock_api.add("GET", "/v1/servers", json=lambda request: (200, pages[request["params"]["page"]]))

    listed = vendor.list_objects("vms")
    assert [obj["name"] for obj in listed] == ["db1", "db2"]
    [first, second] = mock_api.requests_to("GET", "/v1/servers")
    assert first["params"] == {"page": "1", "per_page": "50"}
    assert second["params"] == {"page": "2", "per_page": "50"}
    assert first["headers"]["authorization"] == f"Bearer {TOKEN}"

    assert listed[0] == {
        "object_type": "vms",
        "id": "1",
        "name": "db1",
        "status": "running",
        "state": "ready",
        "location": "fsn1",
        "labels": {"env": "prod", "role": "galera"},
        "created": "2026-10-01T10:00:00Z",
        "vendor_type": "server",
        "properties": {
            "size": "cx22",
            "image": "ubuntu-24.04",
            "ipv4": "192.0.2.10",
            "ipv6": "2001:db8::/64",
            "private_ips": ["10.0.0.2"],
            "networks": [3],
            "volumes": [11],
            "firewalls": [7],
        },
    }

    # Hetzner has projects of its own: no project label is filtered on.
    assert [obj["name"] for obj in vendor.list_objects("vms", tags="env=prod")] == ["db1"]
    assert [obj["name"] for obj in vendor.list_objects("vms", tags={"role": None})] == ["db1"]
    assert vendor.list_objects("vms", tags=["env=staging"]) == []


def _vm_info_routes(mock_api):
    pricing = {"pricing": {
        **PRICING["pricing"],
        "primary_ips": [
            {"type": "ipv6", "prices": []},
            {"type": "ipv4", "prices": [_price("fsn1", "0.0008", "0.50")]},
        ],
    }}
    mock_api.add("GET", "/v1/pricing", json=pricing)
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    mock_api.add(
        "GET",
        "/v1/placement_groups",
        json=_page("placement_groups", [{"id": 4, "name": "galera-spread", "type": "spread"}]),
    )
    mock_api.add(
        "GET",
        "/v1/server_types",
        json=_page(
            "server_types",
            [
                {
                    "id": 1, "name": "cx22", "description": "CX22", "cores": 2, "memory": 4.0,
                    "disk": 40, "deprecated": False, "cpu_type": "shared", "architecture": "x86",
                    "storage_type": "local", "category": "cost_optimized",
                    "prices": [_price("fsn1", "0.0060", "3.79"), _price("hel1", "0.0060", "3.79")],
                }
            ],
        ),
    )
    mock_api.add(
        "GET",
        "/v1/images",
        json=_page(
            "images",
            [
                {"id": 5, "name": "ubuntu-24.04", "description": "Ubuntu 24.04", "type": "system",
                 "architecture": "x86", "os_flavor": "ubuntu", "os_version": "24.04"},
                {"id": 6, "name": "debian-13", "description": "Debian 13", "type": "system",
                 "architecture": "arm", "os_flavor": "debian", "os_version": "13"},
            ],
        ),
    )


def test_vm_info_reports_sizes_locations_and_system_images(vendor, mock_api):
    _vm_info_routes(mock_api)
    info = vendor.object_info("vms")
    assert info["sizes"] == [
        {
            "name": "cx22",
            "description": "CX22",
            "architecture": "x86_64",
            "cpu_type": "shared",
            "category": "cost_optimized",
            "cores": 2,
            "memory_gb": 4.0,
            "disk_gb": 40,
            "included_traffic_tb": 20.0,
            "prices": [
                {"location": "fsn1", "hourly": 0.006, "monthly": 3.79, "currency": "EUR"},
                {"location": "hel1", "hourly": 0.006, "monthly": 3.79, "currency": "EUR"},
            ],
            "locations": ["fsn1", "hel1"],
            "deprecated": False,
        }
    ]
    assert info["locations"][0] == {
        "name": "fsn1", "description": "Falkenstein DC Park 1", "country": "DE",
        "city": "Falkenstein", "network_zone": "eu-central",
    }
    assert info["images"][0] == {
        "name": "ubuntu-24.04", "description": "Ubuntu 24.04", "architecture": "x86_64",
        "os_flavor": "ubuntu", "os_version": "24.04",
    }
    assert info["ipv4_prices"] == [{"location": "fsn1", "hourly": 0.0008, "monthly": 0.5, "currency": "EUR"}]
    assert info["placement_groups"] == [{"name": "galera-spread", "id": 4, "type": "spread"}]
    [images] = mock_api.requests_to("GET", "/v1/images")
    assert images["params"]["type"] == "system"


def _settings_routes(mock_api):
    _vm_info_routes(mock_api)
    mock_api.add("GET", "/v1/ssh_keys", json=_page("ssh_keys", [{"id": 21, "name": "admin"}, {"id": 22, "name": "ci"}]))
    mock_api.add(
        "GET",
        "/v1/networks",
        json=_page("networks", [
            {"id": 3, "name": "galera-net", "subnets": [{"network_zone": "eu-central"}]},
            {"id": 4, "name": "us-net", "subnets": [{"network_zone": "us-east"}]},
        ]),
    )
    mock_api.add("GET", "/v1/firewalls", json=_page("firewalls", [{"id": 7, "name": "mariadb"}]))
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(11, "data1"), _volume(12, "data2")]))


def test_the_vm_settings_are_described_as_the_console_shows_them(vendor, mock_api):
    _settings_routes(mock_api)
    info = vendor.settings_info("vms")
    settings = info["settings"]

    # Hetzner's console order: the type first, the name last
    assert list(settings) == [
        "size", "location", "image", "enable_ipv4", "enable_ipv6", "networks", "ssh_keys",
        "volumes", "automount", "firewalls", "placement_group", "labels", "user_data", "name",
        "start_after_create", "vendor_settings",
    ]
    assert [details["position"] for details in settings.values()] == list(range(1, 17))
    assert [group["key"] for group in info["groups"]] == [
        "size", "location", "image", "networking", "ssh_keys", "storage", "security",
        "placement", "labels", "advanced", "general",
    ]
    assert info["groups"][0] == {
        "key": "size", "caption": "Type",
        "description": "The size of the object: its resources, and what it costs.", "position": 1,
    }

    size = settings["size"]
    assert (size["required"], size["strict"], size["type"]) == (True, True, "string")
    assert size["display"]["style"] == "table"
    assert size["display"]["group_by"] == ["cpu_type", "category", "architecture"]
    [cx22] = size["choices"]
    assert cx22["value"] == "cx22" and cx22["description"] == "CX22"
    assert cx22["details"]["locations"] == ["fsn1", "hel1"]
    assert cx22["details"]["prices"][0] == {"location": "fsn1", "hourly": 0.006, "monthly": 3.79, "currency": "EUR"}
    assert size["filter"] == [{"setting": "location", "detail": "locations"}]

    assert [c["details"]["city"] for c in settings["location"]["choices"]] == ["Falkenstein", "Helsinki", "Ashburn, VA"]
    assert settings["image"]["display"] == {"style": "cards", "group_by": ["os_flavor"], "item_caption": "os_version"}
    assert not settings["image"].get("strict")

    ipv4 = settings["enable_ipv4"]
    assert (ipv4["type"], ipv4["default"], ipv4["vendor_specific"], ipv4["vendor_key"]) == (
        "boolean", True, True, "public_net.enable_ipv4"
    )
    assert ipv4["prices"] == [{"location": "fsn1", "hourly": 0.0008, "monthly": 0.5, "currency": "EUR"}]
    assert settings["enable_ipv6"].get("prices") is None

    # what is deployed in the project, to choose from
    assert [c["value"] for c in settings["ssh_keys"]["choices"]] == ["admin", "ci"]
    assert [(c["value"], c["details"]["location"]) for c in settings["networks"]["choices"]] == [
        ("galera-net", "eu-central"), ("us-net", "us-east"),
    ]
    assert settings["networks"]["filter"] == [
        {"setting": "location", "detail": "location", "selected_detail": "network_zone"}
    ]
    assert [c["value"] for c in settings["volumes"]["choices"]] == ["data1", "data2"]
    assert [c["value"] for c in settings["firewalls"]["choices"]] == ["mariadb"]
    assert settings["placement_group"]["choices"] == [
        {"value": "galera-spread", "caption": "galera-spread", "description": None,
         "details": {"id": 4, "type": "spread"}}
    ]

    assert settings["name"]["is_editable"] and settings["labels"]["is_editable"]
    assert not settings["size"]["is_editable"]
    assert settings["labels"]["value_max_length"] == 63
    assert settings["user_data"]["max_bytes"] == 32768


def test_vm_settings_are_validated_without_a_request_first(vendor, mock_api):
    result = vendor.validate_settings(
        "vms",
        {"name": "db 1", "size": "cx22", "image": "ubuntu-24.04", "ssh_keys": ["admin"],
         "enable_ipv4": "maybe", "labels": {"-bad": "x"}},
    )
    assert result["valid"] is False and result["warnings"] == []
    assert [error["settings"] for error in result["errors"]] == [["name"], ["labels"], ["enable_ipv4"]]
    assert result["errors"][2]["message"] == "The setting enable_ipv4 must be true or false, not 'maybe'."
    assert mock_api.requests == []

    result = vendor.validate_settings(
        "vms",
        {"name": "db1", "size": "cx22", "image": "ubuntu-24.04", "ssh_keys": ["admin"],
         "enable_ipv4": False, "enable_ipv6": "no"},
    )
    assert result["errors"] == [
        {"settings": ["enable_ipv4", "enable_ipv6", "networks"],
         "message": "A server without public addresses needs a private network: name one in networks."}
    ]
    assert mock_api.requests == []


def test_vm_settings_are_validated_against_what_hetzner_offers(vendor, mock_api):
    _settings_routes(mock_api)
    valid = {"name": "db1", "size": "cx22", "location": "fsn1", "image": "ubuntu-24.04",
             "ssh_keys": ["admin"], "networks": ["galera-net"], "volumes": ["data1"]}
    assert vendor.validate_settings("vms", valid) == {"valid": True, "errors": [], "warnings": []}

    result = vendor.validate_settings(
        "vms",
        {"name": "db1", "size": "cx22", "location": "ash", "image": "debian-13",
         "ssh_keys": ["admin", "nobody"], "networks": ["galera-net"], "volumes": ["data1"],
         "placement_group": "galera-spread"},
    )
    assert result["valid"] is False
    assert result["errors"] == [
        {"settings": ["size", "location"],
         "message": "Hetzner Cloud does not offer the size 'cx22' with location 'ash'."},
        {"settings": ["image", "size"],
         "message": "Hetzner Cloud does not offer the image 'debian-13' with size 'cx22'."},
        {"settings": ["networks", "location"],
         "message": "The networks 'galera-net' cannot be used with location 'ash'."},
        {"settings": ["ssh_keys"], "message": "There is no ssh_keys named 'nobody' at Hetzner Cloud in project 'prod'."},
        {"settings": ["volumes", "location"], "message": "The volumes 'data1' cannot be used with location 'ash'."},
    ]

    result = vendor.validate_settings("vms", {**valid, "size": "cx99", "image": "my-snapshot"})
    assert result["errors"] == [
        {"settings": ["size"], "message": "Hetzner Cloud does not offer the size 'cx99'. Choose one of: cx22."}
    ]
    assert result["warnings"] == [
        {"settings": ["image"], "message": "Hetzner Cloud does not list the image 'my-snapshot'; the "
         "deployment fails if it does not take it."}
    ]


def test_a_vm_takes_hetzner_settings_of_its_own(vendor, mock_api):
    _settings_routes(mock_api)
    mock_api.add(
        "POST", "/v1/servers", status=201,
        json={"server": _server(9, "db3", status="initializing")},
    )
    vendor.deploy_object(
        "vms",
        {"name": "db3", "size": "cx22", "image": "ubuntu-24.04", "ssh_keys": ["admin"],
         "networks": ["galera-net"], "enable_ipv4": False, "enable_ipv6": "true",
         "volumes": ["data2"], "automount": "yes", "placement_group": "galera-spread",
         "start_after_create": False},
    )
    [request] = mock_api.requests_to("POST", "/v1/servers")
    assert request["json"] == {
        "name": "db3", "server_type": "cx22", "image": "ubuntu-24.04", "ssh_keys": [21],
        "networks": [3], "public_net": {"enable_ipv4": False, "enable_ipv6": True},
        "volumes": [12], "automount": True, "placement_group": 4, "start_after_create": False,
    }


def _deploy_routes(mock_api):
    mock_api.add("GET", "/v1/ssh_keys", json=_page("ssh_keys", [{"id": 21, "name": "admin"}, {"id": 22, "name": "ci"}]))
    mock_api.add("GET", "/v1/networks", json=_page("networks", [{"id": 3, "name": "galera-net"}]))
    mock_api.add("GET", "/v1/firewalls", json=_page("firewalls", [{"id": 7, "name": "mariadb"}]))
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1")]))


def test_a_vm_is_deployed_with_names_resolved_to_ids(vendor, mock_api):
    _deploy_routes(mock_api)
    mock_api.add(
        "POST",
        "/v1/servers",
        status=201,
        json={
            "server": _server(9, "db3", {"env": "prod"}, status="initializing"),
            "action": {"id": 100, "command": "create_server", "status": "running", "progress": 0, "error": None},
            "next_actions": [{"id": 101, "command": "start_server", "status": "running", "progress": 0}],
            # Never with SSH keys - but were it set, it must not be passed on.
            "root_password": "pw-must-never-show",
        },
    )

    deployed = vendor.deploy_object(
        "vms",
        {
            "name": "db3",
            "size": "cx22",
            "image": "ubuntu-24.04",
            "location": "fsn1",
            "ssh_keys": ["admin", 22],
            "networks": ["galera-net"],
            "firewalls": ["7"],
            "user_data": "#cloud-config\n",
            "labels": {"env": "prod"},
            "vendor_settings": {"start_after_create": True},
        },
    )

    [request] = mock_api.requests_to("POST", "/v1/servers")
    assert request["json"] == {
        "name": "db3",
        "server_type": "cx22",
        "image": "ubuntu-24.04",
        "location": "fsn1",
        "ssh_keys": [21, 22],
        "networks": [3],
        "firewalls": [{"firewall": 7}],
        "user_data": "#cloud-config\n",
        "labels": {"env": "prod"},
        "start_after_create": True,
    }
    assert deployed["id"] == "9"
    assert deployed["status"] == "initializing"
    assert deployed["properties"]["action"] == {"id": 100, "command": "create_server", "status": "running", "progress": 0}
    assert deployed["properties"]["next_actions"][0]["command"] == "start_server"
    assert "pw-must-never-show" not in repr(deployed)
    assert TOKEN not in repr(deployed)


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"name": "db", "size": "cx22", "image": "ubuntu-24.04"}, "requires ssh_keys"),
        ({"name": "db", "size": "cx22", "image": "ubuntu-24.04", "ssh_keys": []}, "requires ssh_keys"),
        ({"name": "db", "size": "cx22", "image": "ubuntu-24.04", "ssh_keys": ["admin"], "vendor_settings": {"ssh_keys": []}}, "requires ssh_keys"),
        ({"name": "db", "image": "ubuntu-24.04", "ssh_keys": ["admin"]}, "Missing settings for vms: size"),
        ({"name": "db", "size": "cx22", "image": "x", "ssh_keys": ["admin"], "nodes": 3}, "Unsupported settings for vms: nodes"),
        ({"name": "db", "size": "cx22", "image": "x", "ssh_keys": ["nobody"]}, "no SSH key named 'nobody'.*Known: admin, ci"),
        ({"name": "db", "size": "cx22", "image": "x", "ssh_keys": ["admin"], "networks": ["other"]}, "no network named 'other'"),
        ({"name": "db", "size": "cx22", "image": "x", "ssh_keys": ["admin"], "labels": "env=prod"}, "labels must be a dict"),
    ],
)
def test_a_vm_deployment_is_refused_before_anything_is_created(vendor, mock_api, settings, message):
    _deploy_routes(mock_api)
    with pytest.raises(DbaError, match=message):
        vendor.deploy_object("vms", settings)
    assert mock_api.requests_to("POST", "/v1/servers") == []


def test_a_vm_status_is_fetched_fresh_by_id(vendor, mock_api):
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1")]))
    mock_api.add("GET", "/v1/servers/1", json={"server": _server(1, "db1", status="off")})

    status = vendor.object_status("vms", "db1")
    assert status["status"] == "off"
    assert len(mock_api.requests_to("GET", "/v1/servers/1")) == 1


def test_a_vm_is_deleted_by_name_once_the_delete_finished(vendor, mock_api):
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1")]))
    mock_api.add("DELETE", "/v1/servers/1", json={"action": {"id": 5, "command": "delete_server", "status": "running", "progress": 0}})
    mock_api.add("GET", "/v1/actions/5", json={"action": {"id": 5, "command": "delete_server", "status": "success", "progress": 100}})

    result = vendor.delete_object("vms", "db1")
    assert result["deleted"]["id"] == "1"
    assert result["result"] == {
        "action": {"id": 5, "command": "delete_server", "status": "success", "progress": 100},
        "status": "deleted",
    }


def test_the_vendors_error_is_reported_without_the_token(vendor, mock_api):
    mock_api.add(
        "GET", "/v1/servers", status=401,
        json={"error": {"code": "unauthorized", "message": "unable to authenticate", "details": None}},
    )
    with pytest.raises(DbaError) as error:
        vendor.list_objects("vms")
    assert str(error.value) == (
        "The Hetzner Cloud API refused GET /servers with HTTP 401 (unauthorized): unable to authenticate"
    )
    assert TOKEN not in str(error.value)


def test_no_token_is_refused():
    with pytest.raises(DbaError, match="No API key for Hetzner Cloud"):
        Hetzner().list_objects("vms")


# --- volumes ------------------------------------------------------------------


def _volume(id, name, server=None, **extra):
    return {
        "id": id, "name": name, "status": "available", "size": 50, "created": "2026-10-01T10:00:00Z",
        "location": {"name": "fsn1"}, "labels": {"env": "prod"}, "linux_device": f"/dev/disk/by-id/scsi-0HC_Volume_{id}",
        "format": "xfs", "server": server,
        "assignee": {"id": server, "type": "server"} if server else None, **extra,
    }


def test_volumes_are_listed_with_the_server_they_are_attached_to(vendor, mock_api):
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(11, "data1", server=1), _volume(12, "data2")]))
    listed = vendor.list_objects("volumes")
    assert listed[0]["properties"] == {
        "size_gb": 50, "server": 1, "linux_device": "/dev/disk/by-id/scsi-0HC_Volume_11", "format": "xfs",
    }
    assert "server" not in listed[1]["properties"]
    assert listed[0]["vendor_type"] == "volume"


def test_volume_info_reports_locations_and_the_price_per_gb(vendor, mock_api):
    mock_api.add("GET", "/v1/pricing", json=PRICING)
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    info = vendor.object_info("volumes")
    assert info["prices"] == [{"price_per_gb_month": 0.044, "currency": "EUR"}]
    assert [location["name"] for location in info["locations"]] == ["fsn1", "hel1", "ash"]


def test_a_volume_is_deployed_attached_to_a_vm(vendor, mock_api):
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1")]))
    mock_api.add(
        "POST", "/v1/volumes", status=201,
        json={"volume": _volume(13, "data3", server=1), "action": {"id": 1, "command": "create_volume", "status": "running"}, "next_actions": []},
    )
    deployed = vendor.deploy_object("volumes", {"name": "data3", "size_gb": "50", "vm": "db1", "format": "xfs"})

    [request] = mock_api.requests_to("POST", "/v1/volumes")
    assert request["json"] == {"name": "data3", "size": 50, "format": "xfs", "assignee": {"type": "server", "id": 1}}
    assert deployed["properties"]["server"] == 1
    assert deployed["properties"]["action"]["command"] == "create_volume"


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"name": "d", "size_gb": 10}, "needs a location, or a vm"),
        ({"name": "d", "size_gb": "ten", "location": "fsn1"}, "size_gb must be a whole number"),
        ({"name": "d", "location": "fsn1"}, "Missing settings for volumes: size_gb"),
    ],
)
def test_a_volume_deployment_is_refused(vendor, mock_api, settings, message):
    with pytest.raises(DbaError, match=message):
        vendor.deploy_object("volumes", settings)
    assert mock_api.requests == []


def test_an_attached_volume_is_detached_before_it_is_deleted(vendor, mock_api):
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(11, "data1", server=1)]))
    mock_api.add("POST", "/v1/volumes/11/actions/detach", status=201, json={"action": {"id": 40, "command": "detach_volume", "status": "running", "progress": 0}})
    mock_api.add("GET", "/v1/actions/40", json={"action": {"id": 40, "command": "detach_volume", "status": "running", "progress": 50}}, times=1)
    mock_api.add("GET", "/v1/actions/40", json={"action": {"id": 40, "command": "detach_volume", "status": "success", "progress": 100}})
    mock_api.add("DELETE", "/v1/volumes/11", status=204)

    result = vendor.delete_object("volumes", "data1")
    assert result["result"] == {
        "detach": {"id": 40, "command": "detach_volume", "status": "success", "progress": 100},
        "status": "deleted",
    }
    assert [request["method"] + " " + request["path"] for request in mock_api.requests] == [
        "GET /v1/volumes", "POST /v1/volumes/11/actions/detach", "GET /v1/actions/40",
        "GET /v1/actions/40", "DELETE /v1/volumes/11",
    ]


def test_a_detached_volume_is_deleted_right_away(vendor, mock_api):
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(12, "data2")]))
    mock_api.add("DELETE", "/v1/volumes/12", status=204)
    assert vendor.delete_object("volumes", "12")["result"] == {"status": "deleted"}
    assert mock_api.requests_to("POST", "/v1/volumes/12/actions/detach") == []


def test_a_failed_detach_stops_the_delete(vendor, mock_api):
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(11, "data1", server=1)]))
    mock_api.add(
        "POST", "/v1/volumes/11/actions/detach", status=201,
        json={"action": {"id": 40, "command": "detach_volume", "status": "error", "error": {"code": "action_failed", "message": "server locked"}}},
    )
    with pytest.raises(DbaError, match=r"detach_volume \(40\) failed: server locked"):
        vendor.delete_object("volumes", "data1")
    assert mock_api.requests_to("DELETE", "/v1/volumes/11") == []


def test_waiting_for_a_detach_is_bounded(vendor, mock_api, monkeypatch):
    monkeypatch.setattr(hetzner, "ACTION_TIMEOUT", 6.0)
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(11, "data1", server=1)]))
    mock_api.add("POST", "/v1/volumes/11/actions/detach", status=201, json={"action": {"id": 40, "command": "detach_volume", "status": "running"}})
    mock_api.add("GET", "/v1/actions/40", json={"action": {"id": 40, "command": "detach_volume", "status": "running"}})

    with pytest.raises(DbaError, match="still running after 6 seconds"):
        vendor.delete_object("volumes", "data1")
    assert len(mock_api.requests_to("GET", "/v1/actions/40")) == 3
    assert mock_api.requests_to("DELETE", "/v1/volumes/11") == []


def test_a_volume_status_is_fetched_by_id(vendor, mock_api):
    mock_api.add("GET", "/v1/volumes", json=_page("volumes", [_volume(12, "data2")]))
    mock_api.add("GET", "/v1/volumes/12", json={"volume": _volume(12, "data2", status="creating")})
    assert vendor.object_status("volumes", "data2")["status"] == "creating"


# --- networking ---------------------------------------------------------------


def _network(id, name):
    return {
        "id": id, "name": name, "ip_range": "10.0.0.0/16", "created": "2026-10-01T10:00:00Z",
        "subnets": [{"type": "cloud", "ip_range": "10.0.0.0/16", "network_zone": "eu-central", "gateway": "10.0.0.1"}],
        "routes": [], "servers": [1, 2], "load_balancers": [], "labels": {}, "protection": {"delete": False},
    }


def test_networks_are_listed_with_their_zone(vendor, mock_api):
    mock_api.add("GET", "/v1/networks", json=_page("networks", [_network(3, "galera-net")]))
    [network] = vendor.list_objects("networking")
    assert network["location"] == "eu-central"
    # Hetzner gives a network no state.
    assert network["status"] == "active"
    assert network["properties"] == {
        "ip_range": "10.0.0.0/16",
        "subnets": [{"type": "cloud", "ip_range": "10.0.0.0/16", "network_zone": "eu-central"}],
        "servers": [1, 2],
        "load_balancers": [],
    }


def test_network_info_reports_the_network_zones(vendor, mock_api):
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    assert vendor.object_info("networking") == {
        "network_zones": [
            {"name": "eu-central", "locations": ["fsn1", "hel1"]},
            {"name": "us-east", "locations": ["ash"]},
        ]
    }


@pytest.mark.parametrize("location", ["hel1", "eu-central"])
def test_a_network_is_deployed_with_a_subnet_in_the_locations_zone(vendor, mock_api, location):
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    mock_api.add("POST", "/v1/networks", status=201, json={"network": _network(4, "net2")})

    deployed = vendor.deploy_object(
        "networking", {"name": "net2", "ip_range": "10.0.0.0/16", "location": location, "labels": {"a": "b"}}
    )
    [request] = mock_api.requests_to("POST", "/v1/networks")
    assert request["json"] == {
        "name": "net2",
        "ip_range": "10.0.0.0/16",
        "subnets": [{"type": "cloud", "ip_range": "10.0.0.0/16", "network_zone": "eu-central"}],
        "labels": {"a": "b"},
    }
    assert deployed["id"] == "4"


def test_a_network_in_an_unknown_location_is_refused(vendor, mock_api):
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    with pytest.raises(DbaError, match="Unknown location or network zone 'mars1'"):
        vendor.deploy_object("networking", {"name": "n", "ip_range": "10.0.0.0/16", "location": "mars1"})
    assert mock_api.requests_to("POST", "/v1/networks") == []


def test_a_network_is_deleted(vendor, mock_api):
    mock_api.add("GET", "/v1/networks", json=_page("networks", [_network(3, "galera-net")]))
    mock_api.add("DELETE", "/v1/networks/3", status=204)
    assert vendor.delete_object("networking", "galera-net")["result"] == {"status": "deleted"}


# --- firewalls ----------------------------------------------------------------


def _firewall(id, name, applied_to=()):
    return {
        "id": id, "name": name, "labels": {}, "created": "2026-10-01T10:00:00Z",
        "rules": [{"direction": "in", "protocol": "tcp", "port": "3306", "source_ips": ["10.0.0.0/16"], "destination_ips": [], "description": None}],
        "applied_to": list(applied_to),
    }


def test_a_firewall_is_deployed_with_its_rules_and_vms(vendor, mock_api):
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1"), _server(2, "db2")]))
    mock_api.add(
        "POST", "/v1/firewalls", status=201,
        json={
            "firewall": _firewall(7, "mariadb", [{"type": "server", "server": {"id": 1}}]),
            "actions": [{"id": 60, "command": "apply_firewall", "status": "running", "progress": 0}],
        },
    )

    deployed = vendor.deploy_object(
        "firewalls",
        {
            "name": "mariadb",
            "rules": [
                {"protocol": "TCP", "port": 3306, "source_ips": ["10.0.0.0/16"], "description": "MariaDB"},
                {"direction": "out", "protocol": "udp", "port": "53", "destination_ips": "0.0.0.0/0"},
            ],
            "vms": ["db1", "2"],
        },
    )
    [request] = mock_api.requests_to("POST", "/v1/firewalls")
    assert request["json"] == {
        "name": "mariadb",
        "rules": [
            {"direction": "in", "protocol": "tcp", "port": "3306", "source_ips": ["10.0.0.0/16"], "description": "MariaDB"},
            {"direction": "out", "protocol": "udp", "port": "53", "destination_ips": ["0.0.0.0/0"]},
        ],
        "apply_to": [{"type": "server", "server": {"id": 1}}, {"type": "server", "server": {"id": 2}}],
    }
    assert deployed["properties"]["servers"] == [1]
    assert deployed["properties"]["rules"] == [
        {"direction": "in", "protocol": "tcp", "port": "3306", "source_ips": ["10.0.0.0/16"]}
    ]
    assert deployed["properties"]["actions"][0]["command"] == "apply_firewall"


@pytest.mark.parametrize(
    "rule, message",
    [
        ({"protocol": "tcp", "port": 22}, "needs source_ips"),
        ({"protocol": "sctp", "source_ips": ["0.0.0.0/0"]}, "protocol is one of"),
        ({"direction": "sideways", "protocol": "tcp", "source_ips": ["0.0.0.0/0"]}, "direction must be one of: in, out"),
        ({"protocol": "tcp", "source_ips": ["0.0.0.0/0"], "action": "drop"}, "Unsupported firewall rule keys: action"),
        ("tcp/3306", "must be a dict"),
    ],
)
def test_a_bad_firewall_rule_is_refused(vendor, mock_api, rule, message):
    with pytest.raises(DbaError, match=message):
        vendor.deploy_object("firewalls", {"name": "fw", "rules": [rule]})
    assert mock_api.requests == []


def test_an_applied_firewall_is_removed_from_its_resources_before_it_is_deleted(vendor, mock_api):
    applied = [
        {"type": "server", "server": {"id": 1}},
        {"type": "label_selector", "label_selector": {"selector": "role=db"}, "applied_to_resources": []},
    ]
    mock_api.add("GET", "/v1/firewalls", json=_page("firewalls", [_firewall(7, "mariadb", applied)]))
    mock_api.add(
        "POST", "/v1/firewalls/7/actions/remove_from_resources", status=201,
        json={"actions": [{"id": 70, "command": "remove_firewall", "status": "success", "progress": 100}]},
    )
    mock_api.add("DELETE", "/v1/firewalls/7", status=204)

    result = vendor.delete_object("firewalls", "mariadb")
    [remove] = mock_api.requests_to("POST", "/v1/firewalls/7/actions/remove_from_resources")
    assert remove["json"] == {
        "remove_from": [
            {"type": "server", "server": {"id": 1}},
            {"type": "label_selector", "label_selector": {"selector": "role=db"}},
        ]
    }
    assert result["result"] == {
        "removed_from_resources": [{"id": 70, "command": "remove_firewall", "status": "success", "progress": 100}],
        "status": "deleted",
    }


def test_firewall_info():
    assert Hetzner(api_key=TOKEN).object_info("firewalls")["directions"] == ["in", "out"]


# --- load_balancers -------------------------------------------------------------


def _load_balancer(id, name):
    return {
        "id": id, "name": name, "created": "2026-10-01T10:00:00Z", "labels": {},
        "public_net": {"enabled": True, "ipv4": {"ip": "192.0.2.50"}, "ipv6": {"ip": "2001:db8::1"}},
        "private_net": [{"network": 3, "ip": "10.0.0.5"}],
        "location": {"name": "fsn1"}, "load_balancer_type": {"name": "lb11"},
        "algorithm": {"type": "round_robin"},
        "services": [{"protocol": "tcp", "listen_port": 3306, "destination_port": 3306, "proxyprotocol": False}],
        "targets": [{"type": "server", "server": {"id": 1}, "use_private_ip": True}, {"type": "label_selector", "label_selector": {"selector": "role=db"}}],
    }


def test_load_balancers_are_listed_compactly(vendor, mock_api):
    mock_api.add("GET", "/v1/load_balancers", json=_page("load_balancers", [_load_balancer(30, "galera-lb")]))
    [lb] = vendor.list_objects("load_balancers")
    assert lb["properties"] == {
        "size": "lb11", "ipv4": "192.0.2.50", "ipv6": "2001:db8::1", "private_ips": ["10.0.0.5"],
        "networks": [3], "algorithm": "round_robin",
        "services": [{"protocol": "tcp", "listen_port": 3306, "destination_port": 3306}],
        "targets": [{"type": "server", "server": 1}, {"type": "label_selector", "label_selector": "role=db"}],
    }


def test_load_balancer_info_reports_the_types_with_prices(vendor, mock_api):
    mock_api.add("GET", "/v1/pricing", json=PRICING)
    mock_api.add("GET", "/v1/locations", json=_page("locations", LOCATIONS))
    mock_api.add(
        "GET", "/v1/load_balancer_types",
        json=_page("load_balancer_types", [{"id": 1, "name": "lb11", "description": "LB11", "max_connections": 10000, "max_services": 5, "max_targets": 25, "deprecated": None, "prices": [_price("fsn1", "0.0098", "5.39")]}]),
    )
    [size] = vendor.object_info("load_balancers")["sizes"]
    assert size["name"] == "lb11"
    assert size["max_targets"] == 25
    assert size["prices"] == [{"location": "fsn1", "hourly": 0.0098, "monthly": 5.39, "currency": "EUR"}]
    assert size["deprecated"] is False


def test_a_load_balancer_is_deployed_with_services_and_private_targets(vendor, mock_api):
    mock_api.add("GET", "/v1/networks", json=_page("networks", [{"id": 3, "name": "galera-net"}]))
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1"), _server(2, "db2")]))
    mock_api.add("POST", "/v1/load_balancers", status=201, json={"load_balancer": _load_balancer(30, "galera-lb"), "action": {"id": 80, "command": "create_load_balancer", "status": "running"}})

    vendor.deploy_object(
        "load_balancers",
        {
            "name": "galera-lb", "size": "lb11", "location": "fsn1", "network": "galera-net",
            "targets": ["db1", "db2"], "services": [{"listen_port": 3306}],
        },
    )
    [request] = mock_api.requests_to("POST", "/v1/load_balancers")
    assert request["json"] == {
        "name": "galera-lb",
        "load_balancer_type": "lb11",
        "location": "fsn1",
        "network": 3,
        "services": [
            {
                "protocol": "tcp", "proxyprotocol": False, "listen_port": 3306, "destination_port": 3306,
                "health_check": {"protocol": "tcp", "port": 3306, "interval": 15, "timeout": 10, "retries": 3},
            }
        ],
        "targets": [
            {"type": "server", "server": {"id": 1}, "use_private_ip": True},
            {"type": "server", "server": {"id": 2}, "use_private_ip": True},
        ],
    }


def test_a_load_balancer_service_without_a_port_is_refused(vendor, mock_api):
    with pytest.raises(DbaError, match=r"services\[0\] needs listen_port"):
        vendor.deploy_object("load_balancers", {"name": "lb", "size": "lb11", "services": [{"protocol": "tcp"}]})


def test_a_load_balancer_status_and_delete(vendor, mock_api):
    mock_api.add("GET", "/v1/load_balancers", json=_page("load_balancers", [_load_balancer(30, "galera-lb")]))
    mock_api.add("GET", "/v1/load_balancers/30", json={"load_balancer": _load_balancer(30, "galera-lb")})
    mock_api.add("DELETE", "/v1/load_balancers/30", status=204)
    assert vendor.object_status("load_balancers", "galera-lb")["properties"]["size"] == "lb11"
    assert vendor.delete_object("load_balancers", "30")["result"] == {"status": "deleted"}


# --- dns ----------------------------------------------------------------------


def _zone(id, name, status="ok"):
    return {
        "id": id, "name": name, "status": status, "mode": "primary", "ttl": 3600, "record_count": 4,
        "created": "2026-10-01T10:00:00Z", "labels": {"env": "prod"}, "primary_nameservers": [],
        "authoritative_nameservers": {"assigned": ["hydrogen.ns.hetzner.com."]},
        "registrar": "other", "protection": {"delete": False},
    }


def test_zones_are_listed(vendor, mock_api):
    mock_api.add("GET", "/v1/zones", json=_page("zones", [_zone(50, "example.com")]))
    [zone] = vendor.list_objects("dns", tags="env")
    assert zone["vendor_type"] == "zone"
    assert zone["properties"] == {"mode": "primary", "ttl": 3600, "record_count": 4, "nameservers": ["hydrogen.ns.hetzner.com."]}


def test_a_zone_is_deployed_with_its_records_as_rrsets(vendor, mock_api):
    mock_api.add("POST", "/v1/zones", status=201, json={"zone": _zone(51, "example.org", status="updating"), "action": {"id": 90, "command": "create_zone", "status": "running"}})
    deployed = vendor.deploy_object(
        "dns",
        {
            "name": "example.org",
            "ttl": 600,
            "records": [
                {"name": "db", "type": "a", "value": "10.0.0.2"},
                {"name": "db", "type": "A", "value": "10.0.0.3", "ttl": 60},
                {"type": "TXT", "value": "\"v=spf1 -all\""},
            ],
        },
    )
    [request] = mock_api.requests_to("POST", "/v1/zones")
    assert request["json"] == {
        "name": "example.org",
        "mode": "primary",
        "ttl": 600,
        "rrsets": [
            {"name": "db", "type": "A", "records": [{"value": "10.0.0.2"}, {"value": "10.0.0.3"}], "ttl": 60},
            {"name": "@", "type": "TXT", "records": [{"value": "\"v=spf1 -all\""}]},
        ],
    }
    assert deployed["status"] == "updating"


def test_a_bad_dns_record_is_refused(vendor, mock_api):
    with pytest.raises(DbaError, match="Unknown DNS record type 'BOGUS'"):
        vendor.deploy_object("dns", {"name": "example.org", "records": [{"type": "BOGUS", "value": "x"}]})
    with pytest.raises(DbaError, match=r"records\[0\] needs value"):
        vendor.deploy_object("dns", {"name": "example.org", "records": [{"type": "A"}]})


def test_a_zone_status_and_delete(vendor, mock_api):
    mock_api.add("GET", "/v1/zones", json=_page("zones", [_zone(50, "example.com")]))
    mock_api.add("GET", "/v1/zones/50", json={"zone": _zone(50, "example.com", status="updating")})
    mock_api.add("DELETE", "/v1/zones/50", status=201, json={"action": {"id": 91, "command": "delete_zone", "status": "running"}})
    mock_api.add("GET", "/v1/actions/91", json={"action": {"id": 91, "command": "delete_zone", "status": "success"}})
    assert vendor.object_status("dns", "example.com")["status"] == "updating"
    assert vendor.delete_object("dns", "example.com")["result"] == {
        "action": {"id": 91, "command": "delete_zone", "status": "success"},
        "status": "deleted",
    }
    assert vendor.object_info("dns")["record_types"][0] == "A"


# --- ssh_keys -----------------------------------------------------------------


def test_an_ssh_key_is_deployed_listed_and_deleted(vendor, mock_api):
    key = {"id": 21, "name": "admin", "fingerprint": "b7:2f:30", "public_key": "ssh-ed25519 AAAA admin", "labels": {}, "created": "2026-10-01T10:00:00Z"}
    mock_api.add("POST", "/v1/ssh_keys", status=201, json={"ssh_key": key})
    mock_api.add("GET", "/v1/ssh_keys", json=_page("ssh_keys", [key]))
    mock_api.add("DELETE", "/v1/ssh_keys/21", status=204)

    deployed = vendor.deploy_object("ssh_keys", {"name": "admin", "public_key": " ssh-ed25519 AAAA admin\n"})
    assert mock_api.requests_to("POST", "/v1/ssh_keys")[0]["json"] == {"name": "admin", "public_key": "ssh-ed25519 AAAA admin"}
    assert deployed["properties"] == {"fingerprint": "b7:2f:30"}

    assert vendor.list_objects("ssh_keys")[0]["name"] == "admin"
    assert vendor.delete_object("ssh_keys", "admin")["result"] == {"status": "deleted"}

    with pytest.raises(DbaError, match="Missing settings for ssh_keys: public_key"):
        vendor.deploy_object("ssh_keys", {"name": "other"})


# --- Through the operations and the shell ---------------------------------------


def test_the_shell_object_reports_the_vendor():
    from mysqlsh import globals

    hetzner_object = globals.dba.vendor.hetzner
    assert list(hetzner_object.feature_set()) == ["CoreCompute"]
    support = {entry["object_type"]: list(entry["operations"]) for entry in hetzner_object.object_support()}
    assert support["volumes"] == ["info", "list", "status", "deploy", "delete"]

    listed = {entry["name"]: entry for entry in globals.dba.list.vendors()}
    assert list(listed["hetzner"]["feature_sets"]) == ["CoreCompute"]
    assert "dns" in list(listed["hetzner"]["object_types"])
    assert [entry["name"] for entry in globals.dba.list.vendors("ManagedService")].count("hetzner") == 0


def test_the_project_picks_the_token(clean_api_keys, mock_api):
    from mysqlsh import globals

    api_keys.store_api_key("hetzner", "hc-prod-token", "prod")
    mock_api.add("GET", "/v1/servers", json=_page("servers", [_server(1, "db1")]))

    [vm] = globals.dba.vendor.hetzner.list_objects("vms", None, "prod")
    assert vm["name"] == "db1"
    assert mock_api.requests[0]["headers"]["authorization"] == "Bearer hc-prod-token"
    assert operations.list_objects("hetzner", "vms", project="prod")[0]["id"] == "1"

    listed = {entry["name"]: entry for entry in operations.list_vendors()}
    assert listed["hetzner"]["api_key_projects"] == ["prod"]

    # A token belongs to one project: no other project falls back on it.
    with pytest.raises(Exception, match="No Hetzner Cloud API key is stored for project 'test'.*Hetzner Console"):
        globals.dba.vendor.hetzner.list_objects("vms", None, "test")


def test_a_refusal_reaches_the_shell_before_any_task(clean_api_keys, mock_api):
    from mysqlsh import globals

    api_keys.store_api_key("hetzner", "hc-default-token")
    with pytest.raises(Exception, match="requires ssh_keys"):
        globals.dba.vendor.hetzner.deploy_object("vms", {"name": "db", "size": "cx22", "image": "ubuntu-24.04"})
    assert mock_api.requests == []
    assert globals.task.list("dba") == []


# --- Live -----------------------------------------------------------------------


@pytest.mark.live
def test_live_read_only(live_api_key):
    vendor = Hetzner(api_key=live_api_key("hetzner"))
    try:
        assert isinstance(vendor.list_objects("vms"), list)
        info = vendor.object_info("vms")
        assert info["sizes"] and info["locations"] and info["images"]
        assert all(price["currency"] for size in info["sizes"] for price in size["prices"])
        settings = vendor.settings_info("vms")["settings"]
        assert settings["size"]["choices"] and settings["location"]["choices"]
        assert all(choice["details"]["locations"] for choice in settings["size"]["choices"])
    finally:
        vendor.close()
