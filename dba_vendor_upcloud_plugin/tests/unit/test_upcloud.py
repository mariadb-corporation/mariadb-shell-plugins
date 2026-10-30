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

"""Tests for the UpCloud vendor, against a mocked UpCloud API."""

# cSpell:ignore mysqlsh MariaDB upcloud ucat maxiops upclouduser

import base64

import pytest

from dba_plugin.lib import api_keys, operations
from dba_plugin.lib.general import DbaError
from dba_plugin.lib.rest_client import VendorApiError
from dba_vendor_upcloud_plugin import vendor as upcloud
from dba_vendor_upcloud_plugin.vendor import UpCloud

TOKEN = "ucat_01TESTTOKEN"
SSH_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITEST dba@test"

SERVER_A = "00000000-0000-4000-8000-00000000000a"
SERVER_B = "00000000-0000-4000-8000-00000000000b"
NETWORK = "03000000-0000-4000-8000-000000000001"
TEMPLATE = "01000000-0000-4000-8000-000030240200"


def _label(**labels):
    return [{"key": key, "value": value} for key, value in labels.items()]


def _server(uuid, title, state="started", **labels):
    return {
        "uuid": uuid,
        "title": title,
        "hostname": title,
        "state": state,
        "zone": "de-fra1",
        "plan": "2xCPU-4GB",
        "core_number": "2",
        "memory_amount": "4096",
        "labels": {"label": _label(**labels)},
    }


def _server_details(uuid, title, state="started", **labels):
    return {
        "server": {
            **_server(uuid, title, state, **labels),
            "ip_addresses": {"ip_address": [{"access": "public", "address": "94.237.1.1", "family": "IPv4"}]},
            "storage_devices": {
                "storage_device": [
                    {
                        "storage": "01aa",
                        "storage_title": f"{title} disk",
                        "storage_size": 80,
                        "storage_tier": "maxiops",
                        "boot_disk": "1",
                    }
                ]
            },
            "networking": {
                "interfaces": {
                    "interface": [
                        {"type": "public", "ip_addresses": {"ip_address": [{"address": "94.237.1.1", "family": "IPv4"}]}},
                        {
                            "type": "private",
                            "network": NETWORK,
                            "ip_addresses": {"ip_address": [{"address": "10.0.0.5", "family": "IPv4"}]},
                        },
                    ]
                }
            },
        }
    }


def _servers(mock_api):
    mock_api.add(
        "GET",
        "/1.3/server",
        json={
            "servers": {
                "server": [
                    _server(SERVER_A, "db1", env="prod", dba_project="shop"),
                    _server(SERVER_B, "db2", "stopped", env="dev"),
                ]
            }
        },
    )
    mock_api.add(
        "GET",
        "/1.3/ip_address",
        json={
            "ip_addresses": {
                "ip_address": [
                    {"access": "public", "address": "94.237.1.1", "family": "IPv4", "server": SERVER_A},
                    {"access": "utility", "address": "10.1.0.1", "family": "IPv4", "server": SERVER_A},
                    {"access": "public", "address": "94.237.9.9", "family": "IPv4", "floating": "yes"},
                ]
            }
        },
    )


def _catalog(mock_api):
    mock_api.add(
        "GET",
        "/1.3/plan",
        json={
            "plans": {
                "plan": [
                    {
                        "core_number": 2,
                        "memory_amount": 4096,
                        "name": "2xCPU-4GB",
                        "public_traffic_out": 4096,
                        "storage_size": 80,
                        "storage_tier": "maxiops",
                    }
                ]
            }
        },
    )
    mock_api.add(
        "GET",
        "/1.3/zone",
        json={
            "zones": {
                "zone": [
                    {"id": "de-fra1", "description": "Frankfurt #1", "public": "yes"},
                    {"id": "fi-priv", "description": "Private", "public": "no", "parent_zone": "fi-hel1"},
                ]
            }
        },
    )
    mock_api.add(
        "GET",
        "/1.3/storage/template",
        json={
            "storages": {
                "storage": [
                    {
                        "access": "public",
                        "title": "Ubuntu Server 24.04 LTS (Noble Numbat)",
                        "type": "template",
                        "template_type": "cloud-init",
                        "uuid": TEMPLATE,
                    }
                ]
            }
        },
    )


@pytest.fixture
def vendor(mock_api):
    instance = UpCloud(api_key=TOKEN)
    yield instance
    instance.close()


# --- Registration and the shell object -------------------------------------


def test_object_support():
    support = {entry["object_type"]: entry["operations"] for entry in UpCloud.object_support()}
    assert list(support) == ["vms", "volumes", "networking", "load_balancers", "object_storage"]
    for operations_of_type in support.values():
        assert operations_of_type == ["info", "list", "status", "deploy", "delete"]
    assert UpCloud.feature_sets == ("CoreCompute",)
    assert UpCloud.native_projects is False


def test_the_shell_object():
    from mysqlsh import globals as shell_globals

    assert shell_globals.dba.vendor.upcloud.feature_set() == ["CoreCompute"]
    support = shell_globals.dba.vendor.upcloud.object_support()
    assert [entry["object_type"] for entry in support][0] == "vms"


def test_listed_with_the_vendors(clean_api_keys):
    from mysqlsh import globals as shell_globals

    api_keys.store_api_key("upcloud", TOKEN)
    [listed] = [entry for entry in shell_globals.dba.list.vendors() if entry["name"] == "upcloud"]
    assert listed["display_name"] == "UpCloud"
    assert listed["feature_sets"] == ["CoreCompute"]
    assert listed["api_key_projects"] == ["default"]
    assert "object_storage" in listed["object_types"]


# --- Authentication and errors ---------------------------------------------


def test_a_token_is_sent_as_bearer(vendor, mock_api):
    _servers(mock_api)
    vendor.list_objects("vms")
    assert mock_api.requests[0]["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_a_username_and_password_use_basic_auth(mock_api):
    _servers(mock_api)
    instance = UpCloud(api_key="dba-sub:pa:ss")
    try:
        instance.list_objects("vms")
    finally:
        instance.close()
    expected = base64.b64encode(b"dba-sub:pa:ss").decode()
    assert mock_api.requests[0]["headers"]["authorization"] == f"Basic {expected}"


def test_the_vendors_error_is_reported_without_the_key(vendor, mock_api):
    mock_api.add(
        "GET",
        "/1.3/server",
        status=401,
        json={"error": {"error_code": "AUTHENTICATION_FAILED", "error_message": "Authentication failed."}},
    )
    with pytest.raises(VendorApiError) as error:
        vendor.list_objects("vms")
    assert "(AUTHENTICATION_FAILED): Authentication failed." in str(error.value)
    assert error.value.status == 401
    assert TOKEN not in str(error.value)


def test_without_a_key_there_is_no_client():
    with pytest.raises(DbaError, match="No API key for UpCloud"):
        UpCloud().list_objects("vms")


def test_an_unsupported_type_is_refused(vendor):
    with pytest.raises(DbaError, match="UpCloud does not support firewalls"):
        vendor.list_objects("firewalls")


# --- vms ---------------------------------------------------------------------


def test_list_vms(vendor, mock_api):
    _servers(mock_api)
    db1, db2 = vendor.list_objects("vms")
    assert db1["id"] == SERVER_A and db1["name"] == "db1" and db1["status"] == "started"
    assert db1["location"] == "de-fra1" and db1["vendor_type"] == "server"
    assert db1["labels"] == {"env": "prod", "dba_project": "shop"}
    assert db1["properties"]["plan"] == "2xCPU-4GB"
    assert db1["properties"]["cores"] == 2 and db1["properties"]["memory_gb"] == 4
    assert [ip["address"] for ip in db1["properties"]["ip_addresses"]] == ["94.237.1.1", "10.1.0.1"]
    assert db2["properties"]["ip_addresses"] == []
    # The summary plus one address listing: no request per server.
    assert len(mock_api.requests) == 2


def test_list_vms_by_tag_and_project(mock_api):
    _servers(mock_api)
    instance = UpCloud(api_key=TOKEN)
    assert [o["name"] for o in instance.list_objects("vms", "env=dev")] == ["db2"]
    assert [o["name"] for o in instance.list_objects("vms", {"env": None})] == ["db1", "db2"]
    instance.close()

    in_project = UpCloud(api_key=TOKEN, project="shop")
    assert [o["name"] for o in in_project.list_objects("vms")] == ["db1"]
    in_project.close()


def test_list_through_the_operations_with_a_project(mock_api, clean_api_keys):
    _servers(mock_api)
    api_keys.store_api_key("upcloud", TOKEN)
    listed = operations.list_objects("upcloud", "vms", project="shop")
    assert [o["name"] for o in listed] == ["db1"]
    assert mock_api.requests[0]["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_no_key_through_the_operations(clean_api_keys):
    with pytest.raises(DbaError, match=r"No UpCloud API key is stored.*dba.set.apiKey\('upcloud'"):
        operations.list_objects("upcloud", "vms")


def test_info_vms(vendor, mock_api):
    _catalog(mock_api)
    mock_api.add(
        "GET",
        "/1.3/price",
        json={"prices": {"zone": [{"name": "de-fra1", "server_plan_2xCPU-4GB": {"amount": 1, "price": 2.976}}]}},
    )
    mock_api.add("GET", "/1.3/account", json={"account": {"username": "dba-sub", "credits": 100}})
    mock_api.add("GET", "/1.3/account/details/dba-sub", json={"account": {"currency": "EUR"}})

    info = vendor.object_info("vms")
    [size] = info["sizes"]
    assert size == {
        "name": "2xCPU-4GB",
        "description": "2 vCPU, 4 GB RAM, 80 GB maxiops",
        "architecture": "x86_64",
        "cores": 2,
        "memory_gb": 4,
        "disk_gb": 80,
        "included_traffic_tb": 4,
        "prices": [{"location": "de-fra1", "hourly": 0.02976, "monthly": 20.0, "currency": "EUR"}],
        "deprecated": False,
    }
    assert info["locations"] == [
        {"name": "de-fra1", "description": "Frankfurt #1", "country": "DE", "city": "Frankfurt"}
    ]
    assert info["images"] == [
        {
            "name": "Ubuntu Server 24.04 LTS (Noble Numbat)",
            "id": TEMPLATE,
            "description": "cloud-init",
            "architecture": "x86_64",
        }
    ]


def test_info_vms_without_access_to_the_account_currency(vendor, mock_api):
    _catalog(mock_api)
    mock_api.add("GET", "/1.3/price", json={"prices": {"zone": []}})
    mock_api.add("GET", "/1.3/account", status=403, json={"error": {"error_code": "FORBIDDEN", "error_message": "no"}})
    assert vendor.object_info("vms")["sizes"][0]["prices"] == []


def _deployable(mock_api, created_state="maintenance"):
    _catalog(mock_api)
    mock_api.add(
        "GET",
        "/1.3/network",
        json={"networks": {"network": [{"uuid": NETWORK, "name": "galera", "type": "private", "zone": "de-fra1"}]}},
    )
    mock_api.add(
        "POST",
        "/1.3/server",
        status=202,
        json=lambda request: (
            202,
            _server_details(SERVER_A, request["json"]["server"]["title"], created_state),
        ),
    )


def test_deploy_vms(mock_api):
    _deployable(mock_api)
    instance = UpCloud(api_key=TOKEN, project="shop")
    try:
        created = instance.deploy_object(
            "vms",
            {
                "name": "Galera Node 1",
                "size": "2xCPU-4GB",
                "location": "de-fra1",
                "image": "Ubuntu Server 24.04 LTS (Noble Numbat)",
                "ssh_keys": [SSH_KEY],
                "user_data": "#cloud-config\n",
                "networks": ["galera"],
                "labels": {"role": "galera"},
                "vendor_settings": {"timezone": "UTC"},
            },
        )
    finally:
        instance.close()

    [request] = mock_api.requests_to("POST", "/1.3/server")
    server = request["json"]["server"]
    assert server["title"] == "Galera Node 1"
    assert server["hostname"] == "galera-node-1"
    assert server["zone"] == "de-fra1" and server["plan"] == "2xCPU-4GB"
    assert server["metadata"] == "yes" and server["user_data"] == "#cloud-config\n"
    assert server["timezone"] == "UTC"
    assert server["labels"] == {
        "label": [{"key": "role", "value": "galera"}, {"key": "dba_project", "value": "shop"}]
    }
    assert server["storage_devices"]["storage_device"] == [
        {"action": "clone", "storage": TEMPLATE, "title": "Galera Node 1 disk", "size": 80, "tier": "maxiops"}
    ]
    assert server["login_user"] == {
        "username": "upclouduser",
        "create_password": "no",
        "ssh_keys": {"ssh_key": [SSH_KEY]},
    }
    interfaces = server["networking"]["interfaces"]["interface"]
    assert [i["type"] for i in interfaces] == ["public", "utility", "private"]
    assert interfaces[2]["network"] == NETWORK

    assert created["name"] == "Galera Node 1" and created["status"] == "maintenance"
    assert created["properties"]["storages"][0]["size_gb"] == 80
    assert "password" not in str(created)


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"size": "2xCPU-4GB", "location": "de-fra1", "image": TEMPLATE}, "Missing settings for vms: name, ssh_keys"),
        ({"name": "x", "size": "2xCPU-4GB", "location": "de-fra1", "image": TEMPLATE, "ssh_keys": ["my-key"]}, "no SSH key store"),
        (
            {"name": "x", "size": "2xCPU-4GB", "location": "de-fra1", "image": TEMPLATE, "ssh_keys": [SSH_KEY], "firewalls": ["fw"]},
            "Unsupported settings for vms: firewalls",
        ),
        ({"name": "x", "size": "9xCPU", "location": "de-fra1", "image": TEMPLATE, "ssh_keys": [SSH_KEY]}, "no UpCloud plan '9xCPU'"),
        ({"name": "x", "size": "2xCPU-4GB", "location": "de-fra1", "image": "Plan 9", "ssh_keys": [SSH_KEY]}, "no UpCloud template 'Plan 9'"),
        (
            {"name": "x", "size": "2xCPU-4GB", "location": "de-fra1", "image": TEMPLATE, "ssh_keys": SSH_KEY, "networks": ["nope"]},
            "no private UpCloud network named 'nope'",
        ),
    ],
)
def test_deploy_vms_refuses(vendor, mock_api, settings, message):
    _deployable(mock_api)
    with pytest.raises(DbaError, match=message):
        vendor.deploy_object("vms", settings)
    assert not mock_api.requests_to("POST", "/1.3/server")


def test_status_vms(vendor, mock_api):
    _servers(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_B}", json=_server_details(SERVER_B, "db2", "started"))
    status = vendor.object_status("vms", "db2")
    assert status["status"] == "started"
    assert status["properties"]["storages"] == [
        {"uuid": "01aa", "title": "db2 disk", "size_gb": 80, "tier": "maxiops", "boot_disk": True}
    ]
    assert status["properties"]["ip_addresses"][0]["address"] == "94.237.1.1"


def test_delete_vms_stops_the_server_first(vendor, mock_api, monkeypatch):
    slept = []
    monkeypatch.setattr(upcloud, "_sleep", slept.append)
    _servers(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_A}", json=_server_details(SERVER_A, "db1", "started"), times=2)
    mock_api.add("GET", f"/1.3/server/{SERVER_A}", json=_server_details(SERVER_A, "db1", "stopped"))
    mock_api.add("POST", f"/1.3/server/{SERVER_A}/stop", json=_server_details(SERVER_A, "db1", "started"))
    mock_api.add("DELETE", f"/1.3/server/{SERVER_A}", status=204)

    result = vendor.delete_object("vms", "db1")
    assert result["deleted"]["id"] == SERVER_A
    assert result["result"] == {
        "status": "deleted", "stopped_first": True, "boot_disk_deleted": True, "volumes_detached": [],
    }

    [stop] = mock_api.requests_to("POST", f"/1.3/server/{SERVER_A}/stop")
    assert stop["json"] == {"stop_server": {"stop_type": "soft", "timeout": "60"}}
    [delete] = mock_api.requests_to("DELETE", f"/1.3/server/{SERVER_A}")
    assert delete["params"] == {"storages": "1"}
    assert slept == [upcloud.POLL_INTERVAL, upcloud.POLL_INTERVAL]


def test_delete_a_stopped_vm_right_away(vendor, mock_api):
    _servers(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_B}", json=_server_details(SERVER_B, "db2", "stopped"))
    mock_api.add("DELETE", f"/1.3/server/{SERVER_B}", status=204)
    assert vendor.delete_object("vms", SERVER_B)["result"]["stopped_first"] is False
    assert not mock_api.requests_to("POST", f"/1.3/server/{SERVER_B}/stop")


def test_delete_vms_keeps_the_attached_volumes(vendor, mock_api):
    # As UpCloud answers: boot_disk "0" on the system disk too.
    details = _server_details(SERVER_B, "db2", "stopped")
    details["server"]["storage_devices"]["storage_device"] = [
        {"storage": STORAGE, "storage_title": "db2 data", "address": "virtio:1", "type": "disk", "boot_disk": "0", "part_of_plan": "no"},
        {"storage": "01aa", "storage_title": "db2 disk", "address": "virtio:0", "type": "disk", "boot_disk": "0", "part_of_plan": "yes"},
        {"storage": "01cd", "storage_title": "debian.iso", "address": "ide:0:0", "type": "cdrom", "boot_disk": "0"},
    ]
    _servers(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_B}", json=details)
    mock_api.add("POST", f"/1.3/server/{SERVER_B}/storage/detach", json=details)
    mock_api.add("DELETE", f"/1.3/server/{SERVER_B}", status=204)

    assert vendor.delete_object("vms", SERVER_B)["result"]["volumes_detached"] == [STORAGE]
    [detach] = mock_api.requests_to("POST", f"/1.3/server/{SERVER_B}/storage/detach")
    assert detach["json"] == {"storage_device": {"address": "virtio:1"}}
    # Only the boot disk is left to go with the server.
    [delete] = mock_api.requests_to("DELETE", f"/1.3/server/{SERVER_B}")
    assert delete["params"] == {"storages": "1"}


@pytest.mark.parametrize(
    "devices, system_disk",
    [
        ([{"storage": "a", "address": "virtio:0"}, {"storage": "b", "address": "virtio:1", "boot_disk": "1"}], "b"),
        ([{"storage": "a", "address": "virtio:1"}, {"storage": "b", "address": "virtio:0"}], "b"),
        ([{"storage": "a", "address": "ide:0:0", "type": "cdrom"}], None),
    ],
)
def test_the_system_disk_of_a_server(devices, system_disk):
    found = upcloud._system_disk({"storage_devices": {"storage_device": devices}})
    assert (found or {}).get("storage") == system_disk


def test_delete_vms_gives_up_on_a_server_that_does_not_stop(vendor, mock_api, monkeypatch):
    monkeypatch.setattr(upcloud, "_sleep", lambda seconds: None)
    _servers(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_A}", json=_server_details(SERVER_A, "db1", "started"))
    mock_api.add("POST", f"/1.3/server/{SERVER_A}/stop", json={})
    with pytest.raises(DbaError, match="did not stop within 120 seconds"):
        vendor.delete_object("vms", "db1")
    assert len(mock_api.requests_to("POST", f"/1.3/server/{SERVER_A}/stop")) == 1
    assert not mock_api.requests_to("DELETE", f"/1.3/server/{SERVER_A}")


# --- volumes -----------------------------------------------------------------

STORAGE = "01000000-0000-4000-8000-0000000000f1"


def _storages(mock_api):
    mock_api.add(
        "GET",
        "/1.3/storage/normal",
        json={
            "storages": {
                "storage": [
                    {
                        "uuid": STORAGE,
                        "title": "data",
                        "size": 100,
                        "state": "online",
                        "tier": "maxiops",
                        "zone": "de-fra1",
                        "type": "normal",
                        "labels": _label(dba_project="shop"),
                    },
                    {"uuid": "01bb", "title": "scratch", "size": 10, "state": "online", "zone": "de-fra1", "labels": []},
                ]
            }
        },
    )


def test_list_volumes(mock_api):
    _storages(mock_api)
    instance = UpCloud(api_key=TOKEN, project="shop")
    [data] = instance.list_objects("volumes")
    instance.close()
    assert data["id"] == STORAGE and data["status"] == "online" and data["vendor_type"] == "storage"
    assert data["labels"] == {"dba_project": "shop"}
    assert data["properties"] == {"size_gb": 100, "tier": "maxiops"}


def test_info_volumes(vendor, mock_api):
    _catalog(mock_api)
    info = vendor.object_info("volumes")
    assert [tier["name"] for tier in info["tiers"]] == ["maxiops", "standard", "hdd"]
    assert [zone["name"] for zone in info["locations"]] == ["de-fra1"]


def test_deploy_a_volume_attached_to_a_vm(mock_api):
    _servers(mock_api)
    mock_api.add(
        "POST",
        "/1.3/storage",
        status=201,
        json=lambda request: (201, {"storage": {**request["json"]["storage"], "uuid": STORAGE, "state": "maintenance"}}),
    )
    mock_api.add("POST", f"/1.3/server/{SERVER_A}/storage/attach", json=_server_details(SERVER_A, "db1"))
    instance = UpCloud(api_key=TOKEN, project="shop")
    created = instance.deploy_object(
        "volumes", {"name": "data", "size_gb": 100, "vm": "db1", "vendor_settings": {"tier": "standard"}}
    )
    instance.close()

    [request] = mock_api.requests_to("POST", "/1.3/storage")
    assert request["json"] == {
        "storage": {
            "size": 100,
            "tier": "standard",
            "title": "data",
            "zone": "de-fra1",
            "labels": [{"key": "dba_project", "value": "shop"}],
        }
    }
    [attach] = mock_api.requests_to("POST", f"/1.3/server/{SERVER_A}/storage/attach")
    assert attach["json"] == {"storage_device": {"type": "disk", "storage": STORAGE}}
    assert created["labels"] == {"dba_project": "shop"}
    assert created["properties"]["servers"] == [SERVER_A]


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"name": "data"}, "Missing settings for volumes: size_gb"),
        ({"name": "data", "size_gb": 10}, "location, or a vm"),
        ({"name": "data", "size_gb": 10, "location": "de-fra1", "format": "ext4"}, "Unsupported settings for volumes: format"),
    ],
)
def test_deploy_volumes_refuses(vendor, mock_api, settings, message):
    with pytest.raises(DbaError, match=message):
        vendor.deploy_object("volumes", settings)
    assert not mock_api.requests_to("POST", "/1.3/storage")


def test_status_and_delete_volumes(vendor, mock_api):
    _storages(mock_api)
    mock_api.add(
        "GET",
        f"/1.3/storage/{STORAGE}",
        json={"storage": {"uuid": STORAGE, "title": "data", "state": "online", "size": 100, "servers": {"server": []}}},
    )
    mock_api.add("DELETE", f"/1.3/storage/{STORAGE}", status=204)
    assert vendor.object_status("volumes", "data")["properties"]["servers"] == []
    assert vendor.delete_object("volumes", "data")["result"] == {"status": "deleted"}


def test_delete_an_attached_volume_is_refused(vendor, mock_api):
    _storages(mock_api)
    mock_api.add(
        "GET",
        f"/1.3/storage/{STORAGE}",
        json={"storage": {"uuid": STORAGE, "title": "data", "servers": {"server": [SERVER_A]}}},
    )
    with pytest.raises(DbaError, match=f"attached to the server {SERVER_A}"):
        vendor.delete_object("volumes", "data")
    assert not mock_api.requests_to("DELETE", f"/1.3/storage/{STORAGE}")


# --- networking --------------------------------------------------------------


def _networks(mock_api):
    mock_api.add(
        "GET",
        "/1.3/network",
        json={
            "networks": {
                "network": [
                    {
                        "uuid": NETWORK,
                        "name": "galera",
                        "type": "private",
                        "zone": "de-fra1",
                        "labels": _label(dba_project="shop"),
                        "ip_networks": {"ip_network": [{"address": "10.0.0.0/24", "family": "IPv4"}]},
                        "servers": {"server": [{"uuid": SERVER_A, "title": "db1"}]},
                    },
                    {"uuid": "03pub", "name": "Public 1", "type": "public", "zone": "de-fra1"},
                ]
            }
        },
    )


def test_list_networking(mock_api):
    _networks(mock_api)
    instance = UpCloud(api_key=TOKEN, project="shop")
    [network] = instance.list_objects("networking")
    instance.close()
    assert network["name"] == "galera" and network["vendor_type"] == "network"
    assert network["properties"] == {"ip_ranges": ["10.0.0.0/24"], "servers": [SERVER_A]}


def test_deploy_networking(vendor, mock_api):
    mock_api.add(
        "POST",
        "/1.3/network",
        status=201,
        json=lambda request: (201, {"network": {**request["json"]["network"], "uuid": NETWORK, "type": "private"}}),
    )
    created = vendor.deploy_object(
        "networking", {"name": "galera", "ip_range": "10.0.0.0/24", "location": "de-fra1", "labels": {"env": "prod"}}
    )
    [request] = mock_api.requests_to("POST", "/1.3/network")
    assert request["json"] == {
        "network": {
            "name": "galera",
            "zone": "de-fra1",
            "ip_networks": {"ip_network": [{"address": "10.0.0.0/24", "dhcp": "yes", "family": "IPv4"}]},
            "labels": [{"key": "env", "value": "prod"}],
        }
    }
    assert created["id"] == NETWORK and created["labels"] == {"env": "prod"}

    with pytest.raises(DbaError, match="Missing settings for networking: ip_range"):
        vendor.deploy_object("networking", {"name": "x", "location": "de-fra1"})


def test_info_status_and_delete_networking(vendor, mock_api):
    _catalog(mock_api)
    _networks(mock_api)
    mock_api.add("GET", f"/1.3/network/{NETWORK}", json={"network": {"uuid": NETWORK, "name": "galera", "zone": "de-fra1"}})
    mock_api.add("DELETE", f"/1.3/network/{NETWORK}", status=204)
    assert vendor.object_info("networking")["locations"][0]["name"] == "de-fra1"
    assert vendor.object_status("networking", "galera")["id"] == NETWORK
    assert vendor.delete_object("networking", NETWORK)["result"] == {"status": "deleted"}


# --- load_balancers ----------------------------------------------------------

LB = "0aaaaaaa-0000-4000-8000-000000000001"


def _lb(**overrides):
    return {
        "uuid": LB,
        "name": "galera-lb",
        "zone": "de-fra1",
        "plan": "development",
        "configured_status": "started",
        "operational_state": "running",
        "created_at": "2026-10-01T10:00:00Z",
        "labels": _label(dba_project="shop"),
        "networks": [{"name": "public", "type": "public", "family": "IPv4", "dns_name": "lb-1.upcloudlb.com"}],
        "frontends": [{"name": "frontend-1", "mode": "tcp", "port": 3306}],
        "backends": [{"name": "backend-1"}],
        **overrides,
    }


def test_list_load_balancers_pages(mock_api, monkeypatch):
    monkeypatch.setattr(upcloud, "PAGE_SIZE", 1)
    mock_api.add("GET", "/1.3/load-balancer", json=[_lb()], times=1)
    mock_api.add("GET", "/1.3/load-balancer", json=[_lb(uuid="0b", name="other", labels=[])], times=1)
    mock_api.add("GET", "/1.3/load-balancer", json=[])
    instance = UpCloud(api_key=TOKEN, project="shop")
    [lb] = instance.list_objects("load_balancers")
    instance.close()
    assert lb["status"] == "running" and lb["created"] == "2026-10-01T10:00:00Z"
    assert lb["properties"]["frontends"] == [{"name": "frontend-1", "mode": "tcp", "port": 3306}]
    assert lb["properties"]["networks"][0]["dns_name"] == "lb-1.upcloudlb.com"
    assert [r["params"]["offset"] for r in mock_api.requests_to("GET", "/1.3/load-balancer")] == ["0", "1", "2"]


def test_info_load_balancers(vendor, mock_api):
    _catalog(mock_api)
    mock_api.add(
        "GET", "/1.3/load-balancer/plans", json=[{"name": "development", "per_server_max_sessions": 10000, "server_number": 1}]
    )
    assert vendor.object_info("load_balancers")["sizes"] == [
        {"name": "development", "nodes": 1, "max_sessions_per_node": 10000}
    ]


def test_deploy_load_balancers(vendor, mock_api):
    _servers(mock_api)
    _networks(mock_api)
    mock_api.add("GET", f"/1.3/server/{SERVER_A}", json=_server_details(SERVER_A, "db1"))
    mock_api.add("POST", "/1.3/load-balancer", status=201, json=lambda request: (201, {**request["json"], "uuid": LB}))
    created = vendor.deploy_object(
        "load_balancers",
        {
            "name": "galera-lb",
            "size": "development",
            "location": "de-fra1",
            "network": "galera",
            "targets": ["db1"],
            "services": [{"protocol": "tcp", "listen_port": 3306, "destination_port": 3306}],
        },
    )
    [request] = mock_api.requests_to("POST", "/1.3/load-balancer")
    body = request["json"]
    assert body["plan"] == "development" and body["zone"] == "de-fra1"
    assert body["networks"][1] == {"name": "private", "type": "private", "family": "IPv4", "uuid": NETWORK}
    assert body["frontends"] == [
        {"name": "frontend-1", "mode": "tcp", "port": 3306, "default_backend": "backend-1", "networks": [{"name": "public"}]}
    ]
    assert body["backends"][0]["members"] == [
        {"name": "db1", "ip": "10.0.0.5", "port": 3306, "type": "static", "weight": 100, "max_sessions": 1000, "enabled": True}
    ]
    assert body["labels"] == []
    assert created["id"] == LB

    with pytest.raises(DbaError, match="tcp or http, not 'udp'"):
        vendor.deploy_object(
            "load_balancers",
            {"name": "x", "size": "development", "location": "de-fra1", "network": "galera",
             "services": [{"protocol": "udp", "listen_port": 53}]},
        )


def test_status_and_delete_load_balancers(vendor, mock_api):
    mock_api.add("GET", "/1.3/load-balancer", json=[_lb()])
    mock_api.add("GET", f"/1.3/load-balancer/{LB}", json=_lb(operational_state="pending"))
    mock_api.add("DELETE", f"/1.3/load-balancer/{LB}", status=204)
    assert vendor.object_status("load_balancers", "galera-lb")["status"] == "pending"
    assert vendor.delete_object("load_balancers", "galera-lb")["result"] == {"status": "deleted"}


# --- object_storage ----------------------------------------------------------

OBJ = "1200aaaa-0000-4000-8000-000000000001"


def _object_storage():
    return {
        "uuid": OBJ,
        "name": "backups",
        "region": "europe-1",
        "configured_status": "started",
        "operational_state": "running",
        "created_at": "2026-10-02T10:00:00Z",
        "labels": _label(env="prod"),
        "endpoints": [{"domain_name": "abc.upcloudobjects.com", "type": "public"}],
        "users": [{"username": "x"}],
    }


def test_list_info_and_status_object_storage(vendor, mock_api):
    mock_api.add("GET", "/1.3/object-storage-2", json=[_object_storage()])
    mock_api.add("GET", f"/1.3/object-storage-2/{OBJ}", json=_object_storage())
    mock_api.add(
        "GET",
        "/1.3/object-storage-2/regions",
        json=[{"name": "europe-1", "primary_zone": "fi-hel2", "zones": [{"name": "fi-hel2"}, {"name": "de-fra1"}]}],
    )
    [service] = vendor.list_objects("object_storage", "env=prod")
    assert service["location"] == "europe-1" and service["vendor_type"] == "object-storage-2"
    assert service["properties"] == {
        "configured_status": "started",
        "endpoints": [{"domain_name": "abc.upcloudobjects.com", "type": "public"}],
    }
    assert vendor.object_status("object_storage", "backups")["status"] == "running"
    assert vendor.object_info("object_storage") == {
        "locations": [{"name": "europe-1", "primary_zone": "fi-hel2", "zones": ["fi-hel2", "de-fra1"]}]
    }


def test_deploy_and_delete_object_storage(mock_api):
    mock_api.add("POST", "/1.3/object-storage-2", status=201, json=lambda request: (201, {**request["json"], "uuid": OBJ}))
    mock_api.add("GET", "/1.3/object-storage-2", json=[{**_object_storage(), "labels": _label(dba_project="shop")}])
    mock_api.add("DELETE", f"/1.3/object-storage-2/{OBJ}", status=204)
    instance = UpCloud(api_key=TOKEN, project="shop")
    try:
        created = instance.deploy_object("object_storage", {"name": "backups", "location": "europe-1"})
        assert instance.delete_object("object_storage", "backups")["result"] == {"status": "deleted"}
        with pytest.raises(DbaError, match="Unsupported settings for object_storage: size"):
            instance.deploy_object("object_storage", {"name": "b", "location": "europe-1", "size": "1TB"})
    finally:
        instance.close()

    [request] = mock_api.requests_to("POST", "/1.3/object-storage-2")
    assert request["json"] == {
        "name": "backups",
        "region": "europe-1",
        "configured_status": "started",
        "networks": [{"name": "public", "type": "public", "family": "IPv4"}],
        "labels": [{"key": "dba_project", "value": "shop"}],
    }
    assert created["labels"] == {"dba_project": "shop"}


# --- Live ----------------------------------------------------------------------


@pytest.mark.live
def test_live_list_and_info(live_api_key):
    instance = UpCloud(api_key=live_api_key("upcloud"))
    try:
        servers = instance.list_objects("vms")
        assert all(server["object_type"] == "vms" for server in servers)
        info = instance.object_info("vms")
        assert info["sizes"] and info["locations"] and info["images"]
    finally:
        instance.close()
