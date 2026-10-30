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

"""Tests for the shared REST client: retries, errors, and what never leaks."""

# cSpell:ignore mysqlsh MariaDB httpx

import pytest

from dba_plugin.lib import rest_client
from dba_plugin.lib.rest_client import RestClient, VendorApiError


def _client(**options):
    return RestClient("Acme", "https://api.example.test/v1", headers={"Authorization": "Bearer s3cret"}, **options)


def test_a_json_answer_is_returned_and_the_credentials_sent(mock_api):
    mock_api.add("GET", "/v1/things", json={"things": [1, 2]})
    with _client() as client:
        assert client.get("/things", params={"page": 1, "skip": None}) == {"things": [1, 2]}

    [request] = mock_api.requests
    assert request["headers"]["authorization"] == "Bearer s3cret"
    assert request["params"] == {"page": "1"}


def test_an_empty_answer_is_none(mock_api):
    mock_api.add("DELETE", "/v1/things/1", status=204)
    with _client() as client:
        assert client.delete("/things/1") is None


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"error": {"code": "invalid_input", "message": "name is taken"}}, "(invalid_input): name is taken"),
        ({"error": {"error_code": "SERVER_NOT_FOUND", "error_message": "no server"}}, "(SERVER_NOT_FOUND): no server"),
        ({"errors": [{"message": "bad size", "type": "validation"}]}, "(validation): bad size"),
        ({"detail": "Not authenticated"}, ": Not authenticated"),
        ({"detail": [{"loc": ["body", "name"], "msg": "field required"}]}, ": field required"),
    ],
)
def test_the_vendors_error_message_is_reported(mock_api, body, expected):
    mock_api.add("POST", "/v1/things", json=body, status=422)
    with _client() as client, pytest.raises(VendorApiError) as error:
        client.post("/things", json={"name": "x"})

    assert str(error.value) == f"The Acme API refused POST /things with HTTP 422 {expected}".replace("422 :", "422:")
    assert error.value.status == 422
    assert "s3cret" not in str(error.value)


def test_a_429_is_retried_even_for_a_post(mock_api, monkeypatch):
    delays = []
    monkeypatch.setattr(rest_client, "_sleep", delays.append)
    mock_api.add("POST", "/v1/things", json={"error": {"message": "slow down"}}, status=429, headers={"Retry-After": "7"}, times=1)
    mock_api.add("POST", "/v1/things", json={"thing": {"id": 1}}, status=201)
    with _client() as client:
        assert client.post("/things", json={}) == {"thing": {"id": 1}}
    assert delays == [7.0]


def test_a_503_is_retried_for_a_get_but_not_for_a_post(mock_api):
    mock_api.add("GET", "/v1/things", json={"error": {"message": "busy"}}, status=503, times=2)
    mock_api.add("GET", "/v1/things", json={"things": []})
    mock_api.add("POST", "/v1/things", json={"error": {"message": "busy"}}, status=503)
    with _client() as client:
        assert client.get("/things") == {"things": []}
        with pytest.raises(VendorApiError, match="HTTP 503"):
            client.post("/things", json={})

    assert len(mock_api.requests_to("GET", "/v1/things")) == 3
    assert len(mock_api.requests_to("POST", "/v1/things")) == 1


def test_retries_stop(mock_api):
    mock_api.add("GET", "/v1/things", json={"error": {"message": "busy"}}, status=503)
    with _client() as client, pytest.raises(VendorApiError, match="busy"):
        client.get("/things")
    assert len(mock_api.requests) == rest_client.MAX_RETRIES + 1


def test_a_failed_connection_is_reported(monkeypatch):
    import httpx2

    def refuse(request):
        raise httpx2.ConnectError("connection refused")

    monkeypatch.setattr(rest_client, "_transport_factory", lambda: httpx2.MockTransport(refuse))
    monkeypatch.setattr(rest_client, "_sleep", lambda seconds: None)
    with _client() as client, pytest.raises(VendorApiError, match="GET /things could not be sent: connection refused"):
        client.get("/things")
