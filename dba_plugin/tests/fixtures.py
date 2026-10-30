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

"""The pytest plugin every dba test module uses - the vendor plugins' too.

Loaded with ``-p dba_plugin.tests.fixtures`` by run_tests.py rather than
from a conftest.py, so that the tests of the vendor plugins, which live in
folders of their own, get the same fixtures and options.

* ``mock_api`` - a :class:`MockApi` every RestClient sends its requests to
  instead of the network, with the retry delays skipped.
* ``clean_api_keys`` - removes the stored dba API keys for the test and
  puts them back afterwards.
* ``live_api_key(vendor)`` - the API key a live test runs with, from
  ``DBA_TEST_<VENDOR>_API_KEY``; such tests are marked ``live`` and run
  only with ``--live``.
"""

# cSpell:ignore mysqlsh MariaDB httpx

import json
import os
import re

import pytest

from dba_plugin.lib import rest_client


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Also run the tests marked 'live', against the vendors' real APIs.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: test against a vendor's real API. Skipped unless --live is given.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="a live API test: pass --live to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


class MockApi:
    """A vendor API in the test process.

    Routes are matched in the order added, on the method and the URL's path
    (a full match of a regular expression); a route without a match answers
    404. Every request is recorded in ``requests`` as a dict: method, path,
    params, json, headers.
    """

    def __init__(self):
        self.routes = []
        self.requests = []

    def add(self, method, path, json=None, status=200, headers=None, times=None):
        """Adds a route.

        Args:
            method (str): The HTTP method.
            path (str): A regular expression the whole path must match.
            json: The body to answer with, or a callable taking the recorded
                request and returning ``(status, body)``.
            status (int): The status to answer with.
            headers (dict): Response headers.
            times (int): Answer this many requests only, then fall through
                to the next matching route.
        """
        self.routes.append(
            {
                "method": method.upper(),
                "path": re.compile(path),
                "json": json,
                "status": status,
                "headers": headers or {},
                "times": times,
            }
        )
        return self

    def requests_to(self, method, path):
        """The recorded requests of a method to a path (a regular expression)."""
        pattern = re.compile(path)
        return [
            request
            for request in self.requests
            if request["method"] == method.upper() and pattern.fullmatch(request["path"])
        ]

    def handle(self, request):
        import httpx2

        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        recorded = {
            "method": request.method,
            "path": request.url.path,
            "params": dict(request.url.params),
            "json": body,
            "headers": dict(request.headers),
        }
        self.requests.append(recorded)

        for route in self.routes:
            if route["method"] != request.method or not route["path"].fullmatch(request.url.path):
                continue
            if route["times"] is not None:
                if route["times"] <= 0:
                    continue
                route["times"] -= 1

            status, answer = route["status"], route["json"]
            if callable(answer):
                status, answer = answer(recorded)
            if answer is None:
                return httpx2.Response(status, headers=route["headers"])
            return httpx2.Response(status, json=answer, headers=route["headers"])

        return httpx2.Response(
            404, json={"error": {"code": "not_found", "message": f"no route for {request.method} {request.url.path}"}}
        )


@pytest.fixture
def mock_api(monkeypatch):
    import httpx2

    from dba_plugin.lib import operations

    api = MockApi()
    monkeypatch.setattr(rest_client, "_transport_factory", lambda: httpx2.MockTransport(api.handle))
    monkeypatch.setattr(rest_client, "_sleep", lambda seconds: None)
    # a vendor the operations share keeps the client - and the mock - it was
    # made with: none is carried from one test to the next
    operations.close_vendors()
    yield api
    operations.close_vendors()


def _dba_secrets():
    from mysqlsh.globals import shell

    return [name for name in shell.list_secrets() if ":DBA_API_KEY:" in name]


@pytest.fixture
def clean_api_keys():
    from mysqlsh.globals import shell

    from dba_plugin.lib import operations

    saved = {name: shell.read_secret(name) for name in _dba_secrets()}
    for name in saved:
        shell.delete_secret(name)
    operations.close_vendors()
    yield
    operations.close_vendors()
    for name in _dba_secrets():
        shell.delete_secret(name)
    for name, value in saved.items():
        shell.store_secret(name, value)


@pytest.fixture
def live_api_key():
    def _key(vendor_name):
        key = os.environ.get(f"DBA_TEST_{vendor_name.upper()}_API_KEY")
        if not key:
            pytest.skip(f"DBA_TEST_{vendor_name.upper()}_API_KEY is not set")
        return key

    return _key
