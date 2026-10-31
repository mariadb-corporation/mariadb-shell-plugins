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

"""Tests for the MariaDB Cloud vendor, against a mock of its API."""

# cSpell:ignore mysqlsh MariaDB skysql maxscale

import pytest

from dba_plugin.lib import api_keys, object_types as types, operations, vendor as v
from dba_plugin.lib.general import DbaError
from dba_plugin.lib.rest_client import VendorApiError

from dba_vendor_mariadb_cloud_plugin.vendor import PAGE_TOKEN_HEADER, MariadbCloud

SERVICES = "/provisioning/v1/services"
TYPE = types.MANAGED_MARIADB_SERVICES
KEY = "test-mariadb-cloud-key"


def _service(id, name, status="ready", tags=None, **extra):
    return {
        "id": id,
        "name": name,
        "status": status,
        "region": "us-east-1",
        "provider": "aws",
        "topology": "es-single",
        "service_type": "transactional",
        "version": "11.4.5-3",
        "size": "sky-2x8",
        "nodes": 1,
        "architecture": "amd64",
        "tier": "foundation",
        "created_on": 1767225600,
        "fqdn": f"{id}.mdb0002.db.skysql.com",
        "ssl_enabled": True,
        "project_id": "b4ffd36d",
        "storage_volume": {"size": 100, "volume_type": "gp3", "iops": 3000},
        "endpoints": [
            {
                "name": "primary",
                "fqdn": f"{id}.mdb0002.db.skysql.com",
                "mechanism": "nlb",
                "visibility": "public",
                "ports": [{"name": "readwrite", "port": 3306, "purpose": "readwrite"}],
                "allow_list": [{"ip": "203.0.113.7/32", "comment": ""}],
            }
        ],
        "tags": tags or {},
        "errors": [],
        **extra,
    }


@pytest.fixture
def cloud():
    vendor = MariadbCloud(api_key=KEY)
    yield vendor
    vendor.close()


def test_the_vendor_describes_itself():
    assert MariadbCloud.feature_sets == (types.MANAGED_SERVICE,)
    assert MariadbCloud.native_projects is False
    assert "app.skysql.com" in MariadbCloud.api_key_help
    assert MariadbCloud.object_support() == [
        {
            "object_type": TYPE,
            "description": types.OBJECT_TYPES[TYPE]["description"],
            "feature_set": types.MANAGED_SERVICE,
            "operations": ["info", "list", "status", "create", "update", "delete"],
        }
    ]


def test_a_listing_follows_the_page_tokens_and_sends_the_key(mock_api, cloud):
    mock_api.add(
        "GET", SERVICES, json=[_service("dbpgf1", "one")], headers={PAGE_TOKEN_HEADER: "t2"}, times=1
    )
    mock_api.add("GET", SERVICES, json=[_service("dbpgf2", "two", status="pending_create")])

    services = cloud.list_objects(TYPE)

    assert [service["name"] for service in services] == ["one", "two"]
    first, second = mock_api.requests_to("GET", SERVICES)
    assert first["headers"]["x-api-key"] == KEY
    assert first["params"] == {"page_size": "100"}
    assert second["params"] == {"page_size": "100", "page_token": "t2"}
    assert services[1]["status"] == "pending_create"


def test_a_service_is_reported_compactly_and_without_credentials(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("dbpgf1", "one", tags={"env": "prod"})])

    [service] = cloud.list_objects(TYPE)

    assert service == {
        "object_type": TYPE,
        "id": "dbpgf1",
        "name": "one",
        "status": "ready",
        "state": "ready",
        "location": "us-east-1",
        "labels": {"env": "prod"},
        "created": "2026-01-01T00:00:00+00:00",
        "vendor_type": "service",
        "properties": {
            "service_type": "transactional",
            "topology": "es-single",
            "version": "11.4.5-3",
            "size": "sky-2x8",
            "nodes": 1,
            "provider": "aws",
            "architecture": "x86_64",
            "tier": "foundation",
            "storage_gb": 100,
            "volume_type": "gp3",
            "fqdn": "dbpgf1.mdb0002.db.skysql.com",
            "endpoints": [
                {
                    "name": "primary",
                    "fqdn": "dbpgf1.mdb0002.db.skysql.com",
                    "mechanism": "nlb",
                    "visibility": "public",
                    "ports": {"readwrite": 3306},
                    "allowed_ips": ["203.0.113.7/32"],
                }
            ],
            "ssl_enabled": True,
            "mariadb_cloud_project_id": "b4ffd36d",
        },
    }
    assert KEY not in str(service)


def test_retired_services_are_not_listed(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("a", "one", status="retired"), _service("b", "two")])
    assert [service["id"] for service in cloud.list_objects(TYPE)] == ["b"]


def test_a_listing_is_filtered_by_tags_and_by_the_project_label(mock_api):
    mock_api.add(
        "GET",
        SERVICES,
        json=[
            _service("a", "one", tags={"env": "prod", v.PROJECT_LABEL: "shop"}),
            _service("b", "two", tags={"env": "test", v.PROJECT_LABEL: "shop"}),
            _service("c", "three", tags={"env": "prod"}),
        ],
    )

    vendor = MariadbCloud(api_key=KEY)
    assert [s["id"] for s in vendor.list_objects(TYPE, "env=prod")] == ["a", "c"]
    vendor.close()

    vendor = MariadbCloud(api_key=KEY, project="shop")
    assert [s["id"] for s in vendor.list_objects(TYPE)] == ["a", "b"]
    assert [s["id"] for s in vendor.list_objects(TYPE, {"env": "prod"})] == ["a"]
    vendor.close()


def _info_routes(mock_api):
    mock_api.add(
        "GET",
        "/provisioning/v1/sizes",
        json=[
            {"name": "sky-2x8", "display_name": "Sky-2x8", "cpu": "2 vCPU", "ram": "8 GB",
             "provider": "aws", "topology": "es-single", "architecture": "amd64", "type": "server"},
            {"name": "sky-2x8", "display_name": "Sky-2x8", "cpu": "2 vCPU", "ram": "8 GB",
             "provider": "gcp", "topology": "es-replica", "architecture": "amd64", "type": "server"},
            {"name": "sky-old", "is_active": False, "provider": "aws"},
        ],
        headers={PAGE_TOKEN_HEADER: "p2"},
        times=1,
    )
    mock_api.add(
        "GET",
        "/provisioning/v1/sizes",
        json=[{"name": "sky-4x16", "display_name": "Sky-4x16", "cpu": "4 vCPU", "ram": "16 GB",
               "provider": "aws", "topology": "galera", "architecture": "arm64"}],
    )
    mock_api.add(
        "GET",
        "/provisioning/v1/versions",
        json=[
            {"name": "11.4.5-3", "display_name": "11.4.5", "product": "server", "topology": "es-single", "default": True},
            {"name": "11.4.5-3", "display_name": "11.4.5", "product": "server", "topology": "galera"},
        ],
    )
    mock_api.add(
        "GET",
        "/provisioning/v1/topologies",
        json=[
            {"name": "galera", "display_name": "Galera Cluster", "order": 2, "database": "server"},
            {"name": "es-single", "display_name": "Single node", "order": 1, "is_default": True},
        ],
    )
    mock_api.add(
        "GET",
        "/provisioning/v1/providers",
        json=[
            {"name": "aws", "display_name": "AWS", "active": True, "default": True,
             "volume_size_min_gb": 10, "volume_size_max_gb": 16000},
            {"name": "old", "active": False},
        ],
    )
    mock_api.add(
        "GET",
        "/provisioning/v1/regions",
        json=[
            {"name": "us-east-1", "region": "US East", "provider": "aws", "order": 1, "is_available": True},
            {"name": "closed-1", "provider": "aws", "is_available": False},
        ],
    )


def test_the_info_lists_what_can_be_created(mock_api, cloud):
    _info_routes(mock_api)
    info = cloud.object_info(TYPE)

    assert info["sizes"] == [
        {"name": "sky-2x8", "description": "Sky-2x8", "cpu": "2 vCPU", "memory": "8 GB",
         "providers": ["aws", "gcp"], "topologies": ["es-single", "es-replica"], "architectures": ["x86_64"]},
        {"name": "sky-4x16", "description": "Sky-4x16", "cpu": "4 vCPU", "memory": "16 GB",
         "providers": ["aws"], "topologies": ["galera"], "architectures": ["arm64"]},
    ]
    assert info["versions"] == [
        {"name": "11.4.5-3", "description": "11.4.5", "product": "server", "default": True,
         "topologies": ["es-single", "galera"]},
    ]
    assert [t["name"] for t in info["topologies"]] == ["es-single", "galera"]
    assert info["providers"] == [
        {"name": "aws", "description": "AWS", "default": True, "storage_gb_min": 10, "storage_gb_max": 16000}
    ]
    assert info["locations"] == [
        {"name": "us-east-1", "description": "US East", "provider": "aws", "default": False}
    ]
    [first_sizes, _] = mock_api.requests_to("GET", "/provisioning/v1/sizes")
    assert first_sizes["params"]["type"] == "server"
    assert mock_api.requests_to("GET", "/provisioning/v1/topologies")[0]["params"]["service_type"] == "transactional"


def test_a_creation_maps_the_standard_settings(mock_api):
    mock_api.add("POST", SERVICES, json=_service("dbnew", "orders", status="pending_create"), status=202)

    vendor = MariadbCloud(api_key=KEY, project="shop")
    created = vendor.create_object(
        TYPE,
        {
            "name": "orders",
            "provider": "AWS",
            "location": "us-east-1",
            "topology": "galera",
            "version": "11.4.5-3",
            "size": "sky-2x8",
            "nodes": 3,
            "storage_gb": 100,
            "allowed_ips": ["203.0.113.7", "198.51.100.0/24"],
            "labels": {"env": "prod"},
            "vendor_settings": {"architecture": "arm64", "ssl_enabled": False, "tags": {"team": "a", "env": "x"}},
        },
    )
    vendor.close()

    [request] = mock_api.requests_to("POST", SERVICES)
    assert request["json"] == {
        "name": "orders",
        "service_type": "transactional",
        "provider": "aws",
        "region": "us-east-1",
        "topology": "galera",
        "ssl_enabled": False,
        "version": "11.4.5-3",
        "size": "sky-2x8",
        "nodes": 3,
        "storage": 100,
        "allow_list": [{"ip": "203.0.113.7/32"}, {"ip": "198.51.100.0/24"}],
        "architecture": "arm64",
        "tags": {"team": "a", "env": "prod", v.PROJECT_LABEL: "shop"},
    }
    assert request["headers"]["x-api-key"] == KEY
    assert created["id"] == "dbnew"
    assert created["status"] == "pending_create"
    assert mock_api.requests_to("GET", r".*/security/credentials") == []


def test_a_minimal_creation_sends_only_what_was_given(mock_api, cloud):
    mock_api.add("POST", SERVICES, json=_service("dbnew", "small"), status=202)
    cloud.create_object(TYPE, {"name": "small", "provider": "gcp", "location": "us-central1", "topology": "es-single"})
    [request] = mock_api.requests_to("POST", SERVICES)
    assert request["json"] == {
        "name": "small",
        "service_type": "transactional",
        "provider": "gcp",
        "region": "us-central1",
        "topology": "es-single",
        "ssl_enabled": True,
    }


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"provider": "aws", "location": "x", "topology": "es-single"}, "Missing settings .*: name"),
        ({"name": "a", "provider": "aws", "topology": "es-single"}, "Missing settings .*: location"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "image": "x"}, "Unsupported settings .*: image"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "nodes": 0}, "nodes must be at least 1"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "storage_gb": "lots"}, "storage_gb must be a whole number"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "allowed_ips": ["nope"]}, "must be an IP address or CIDR range, not 'nope'"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "labels": "x"}, "labels must be a dict"),
        ({"name": "a", "provider": "aws", "location": "x", "topology": "t", "vendor_settings": {"tags": "x"}}, "tags must be a dict"),
    ],
)
def test_invalid_settings_are_refused_before_any_request(mock_api, cloud, settings, message):
    with pytest.raises(DbaError, match=message):
        cloud.create_object(TYPE, settings)
    assert mock_api.requests == []


def test_the_service_settings_are_described_for_a_dialog(mock_api, cloud):
    _info_routes(mock_api)
    info = cloud.settings_info(TYPE)
    settings = info["settings"]
    assert list(settings) == [
        "name", "provider", "location", "topology", "version", "size", "nodes", "storage_gb",
        "allowed_ips", "ssl_enabled", "labels", "vendor_settings",
    ]
    assert [key for key, details in settings.items() if details["required"]] == [
        "name", "provider", "location", "topology",
    ]
    assert [key for key, details in settings.items() if details["is_updatable"]] == [
        "size", "nodes", "storage_gb", "allowed_ips",
    ]
    assert settings["provider"]["choices"][0]["details"] == {
        "default": True, "storage_gb_min": 10, "storage_gb_max": 16000,
    }
    assert settings["ssl_enabled"]["vendor_specific"] and settings["ssl_enabled"]["default"] is True
    assert [c["value"] for c in v.applying_choices(settings, "size", {"provider": "aws", "topology": "galera"})] == [
        "sky-4x16"
    ]


def test_service_settings_are_validated_against_what_mariadb_cloud_offers(mock_api, cloud):
    _info_routes(mock_api)
    base = {"name": "orders", "provider": "aws", "location": "us-east-1", "topology": "es-single"}
    assert cloud.validate_settings(TYPE, {**base, "size": "sky-2x8", "version": "11.4.5-3"})["valid"] is True


def test_a_size_is_validated_with_the_provider_and_topology_chosen(mock_api, cloud):
    # one test per validation: the first page of the sizes is answered once
    _info_routes(mock_api)
    base = {"name": "orders", "provider": "aws", "location": "us-east-1", "topology": "es-single"}
    result = cloud.validate_settings(
        TYPE,
        {**base, "topology": "galera", "size": "sky-2x8",
         "allowed_ips": ["10.0.0.0/8", {"ip": "192.0.2.1", "comment": "office"}], "ssl_enabled": "false"},
    )
    assert result["errors"] == [
        {"settings": ["size", "provider", "topology"],
         "message": "MariaDB Cloud does not offer the size 'sky-2x8' with provider 'aws' and topology 'galera'."}
    ]

    refused = cloud.validate_settings(TYPE, {**base, "allowed_ips": [{"comment": "no ip"}]})
    assert refused["errors"] == [{"settings": ["allowed_ips"], "message": "The setting allowed_ips[0] needs ip."}]


def test_the_vendors_own_settings_reach_the_request(mock_api):
    mock_api.add("POST", SERVICES, json=_service("dbnew", "orders", status="pending_create"), status=202)
    MariadbCloud(api_key=KEY).create_object(
        TYPE,
        {"name": "orders", "provider": "aws", "location": "us-east-1", "topology": "es-single",
         "ssl_enabled": "no"},
    )
    [request] = mock_api.requests_to("POST", SERVICES)
    assert request["json"]["ssl_enabled"] is False


def test_a_creation_the_api_does_not_report_is_refused(mock_api, cloud):
    mock_api.add("POST", SERVICES, json=None, status=202)
    with pytest.raises(DbaError, match="did not report it"):
        cloud.create_object(TYPE, {"name": "a", "provider": "aws", "location": "x", "topology": "t"})


def test_a_service_is_deleted_by_name(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("dbpgf1", "one"), _service("dbpgf2", "two")])
    mock_api.add("DELETE", f"{SERVICES}/dbpgf2", json=None)

    result = cloud.delete_object(TYPE, "two")

    assert result["deleted"]["id"] == "dbpgf2"
    assert result["result"] == {"id": "dbpgf2", "status": "pending_delete"}
    assert len(mock_api.requests_to("DELETE", f"{SERVICES}/dbpgf2")) == 1


def test_a_service_of_another_project_is_not_deleted(mock_api):
    mock_api.add("GET", SERVICES, json=[_service("a", "one", tags={v.PROJECT_LABEL: "other"})])
    vendor = MariadbCloud(api_key=KEY, project="shop")
    with pytest.raises(DbaError, match="no managed_mariadb_services named 'one'.*project 'shop'"):
        vendor.delete_object(TYPE, "one")
    vendor.close()
    assert mock_api.requests_to("DELETE", f"{SERVICES}/.*") == []


def test_the_status_is_read_fresh(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("dbpgf1", "one", status="pending_create")])
    mock_api.add("GET", f"{SERVICES}/dbpgf1", json=_service("dbpgf1", "one", status="ready"))

    status = cloud.object_status(TYPE, "dbpgf1")

    assert status["status"] == "ready"
    assert status["name"] == "one"


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (401, {"code": 401, "errors": [{"error": "Unauthorized", "message": "invalid API key", "type": "error"}]},
         "HTTP 401 (error): invalid API key"),
        (400, {"code": 400, "errors": [{"error": "Bad Request", "message": "invalid size", "type": "error",
                                        "location": "body"}]},
         "HTTP 400 (error): invalid size"),
        (402, {"code": 402, "errors": [{"error": "Payment required", "type": "error"}]},
         "HTTP 402 (error): Payment required"),
    ],
)
def test_the_apis_errors_are_reported(mock_api, cloud, status, body, expected):
    mock_api.add("POST", SERVICES, json=body, status=status)
    with pytest.raises(VendorApiError) as error:
        cloud.create_object(TYPE, {"name": "a", "provider": "aws", "location": "x", "topology": "t"})
    assert str(error.value) == f"The MariaDB Cloud API refused POST {SERVICES} with {expected}"
    assert error.value.status == status
    assert KEY not in str(error.value)


def test_without_a_key_nothing_is_sent(mock_api):
    with pytest.raises(DbaError, match="No API key for MariaDB Cloud"):
        MariadbCloud().list_objects(TYPE)
    assert mock_api.requests == []


# --- Through the dba operations and the shell ------------------------------


def test_the_shell_object_reports_the_feature_set():
    from mysqlsh import globals as shell_globals

    assert list(shell_globals.dba.vendor.mariadb_cloud.feature_set()) == [types.MANAGED_SERVICE]
    names = [vendor["name"] for vendor in shell_globals.dba.list.vendors("ManagedService")]
    assert "mariadb_cloud" in names
    names = [vendor["name"] for vendor in shell_globals.dba.list.vendors("CoreCompute")]
    assert "mariadb_cloud" not in names


def test_no_stored_key_is_reported_with_where_to_get_one(clean_api_keys):
    with pytest.raises(DbaError, match="No MariaDB Cloud API key is stored.*app.skysql.com"):
        operations.list_objects("mariadb_cloud", TYPE)


def test_one_key_serves_every_project(mock_api, clean_api_keys):
    api_keys.store_api_key("mariadb_cloud", KEY)
    mock_api.add(
        "GET",
        SERVICES,
        json=[_service("a", "one", tags={v.PROJECT_LABEL: "shop"}), _service("b", "two")],
    )

    listed = operations.list_objects("mariadb_cloud", TYPE, project="shop")

    assert [service["id"] for service in listed] == ["a"]
    assert mock_api.requests[0]["headers"]["x-api-key"] == KEY


def test_the_shell_function_lists_services(mock_api, clean_api_keys):
    from mysqlsh import globals as shell_globals

    api_keys.store_api_key("mariadb_cloud", KEY)
    mock_api.add("GET", SERVICES, json=[_service("a", "one", tags={"env": "prod"}), _service("b", "two")])

    listed = shell_globals.dba.vendor.mariadb_cloud.list_objects(TYPE, "env=prod")

    assert [service["name"] for service in listed] == ["one"]


# --- Against the real API --------------------------------------------------


@pytest.mark.live
def test_live_listing_and_info(live_api_key):
    vendor = MariadbCloud(api_key=live_api_key("mariadb_cloud"))
    try:
        services = vendor.list_objects(TYPE)
        assert isinstance(services, list)
        for service in services:
            assert service["object_type"] == TYPE and service["id"]

        info = vendor.object_info(TYPE)
        assert set(info) == {"sizes", "versions", "topologies", "providers", "locations"}
        assert info["providers"] and info["locations"] and info["topologies"]
    finally:
        vendor.close()


# --- updates ------------------------------------------------------------------


def test_a_service_is_resized_and_its_allow_list_replaced(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("dbpgf1", "orders")])
    mock_api.add("PUT", f"{SERVICES}/dbpgf1/security/allowlist", json=[])
    mock_api.add("POST", f"{SERVICES}/dbpgf1/size", status=202, json={})
    updated = cloud.update_object(TYPE, "orders", {"size": "sky-4x16", "allowed_ips": ["198.51.100.1", {"ip": "10.0.0.0/8", "comment": "vpn"}]})
    assert mock_api.requests_to("POST", f"{SERVICES}/dbpgf1/size")[0]["json"] == {"size": "sky-4x16"}
    assert mock_api.requests_to("PUT", f"{SERVICES}/dbpgf1/security/allowlist")[0]["json"] == [
        {"ip": "198.51.100.1/32"}, {"ip": "10.0.0.0/8", "comment": "vpn"},
    ]
    # the service as found: the update task follows its status
    assert updated["id"] == "dbpgf1"


def test_storage_and_nodes_change_one_at_a_time(mock_api, cloud):
    mock_api.add("GET", SERVICES, json=[_service("dbpgf1", "orders")])
    mock_api.add("PATCH", f"{SERVICES}/dbpgf1/storage", status=202, json={})
    cloud.update_object(TYPE, "orders", {"storage_gb": "200"})
    assert mock_api.requests_to("PATCH", f"{SERVICES}/dbpgf1/storage")[0]["json"] == {"size": 200}
    with pytest.raises(DbaError, match="changes one of size, nodes and storage_gb at a time"):
        cloud.check_update_settings(TYPE, {"nodes": 3, "storage_gb": 300})
