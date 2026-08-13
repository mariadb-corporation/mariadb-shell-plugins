# Copyright (c) 2026, MariaDB plc.
#
# SPDX-License-Identifier: GPL-2.0-only
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

"""Sandbox lifecycle tests for the MariaDB MCP server, driven over stdio.

The lifecycle is split into two parts ordered around the rest of the suite by
the ``pytest_collection_modifyitems`` hook in conftest.py:

* ``test_sandbox_deploy`` runs first and deploys the shared sandbox instance.
* All other tests run in between and use that shared sandbox (see the
  ``sandbox`` fixture and ``test_db_sql.py``).
* ``test_sandbox_shutdown`` runs last and stops and deletes the sandbox,
  verifying the teardown.

The db and shutdown tests skip when ``sandbox.deployed`` is False (i.e. the
deployment failed). All are skipped if no MariaDB/MySQL server binary is on the
PATH.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver mariadbd

import os

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers

# A deploy initializes a data directory and starts the server, so it needs a
# far more generous timeout than a normal tool call.
DEPLOY_TIMEOUT = 300


def _sandbox_call(tool_name, arguments, timeout=None):
    """Calls a sandbox tool over stdio and returns the CallToolResult."""
    return helpers.call_tool(
        function_groups=["sandbox"],
        tool_name=tool_name,
        arguments=arguments,
        timeout=timeout,
    )


def test_sandbox_deploy(sandbox):
    """Part 1: deploys the shared sandbox and verifies it is up. Runs first."""
    # TLS is disabled so the deploy does not depend on a working openssl for
    # certificate generation; a sandbox is for local testing only.
    deploy_result = _sandbox_call(
        "sandbox.deploy",
        {
            "port": sandbox.port,
            "sandbox_dir": sandbox.sandbox_dir,
            "password": sandbox.password,
            "ssl": False,
        },
        timeout=DEPLOY_TIMEOUT,
    )
    assert deploy_result.is_error is False
    # Mark the sandbox as deployed so dependent tests run (and are torn down).
    sandbox.deployed = True
    assert os.path.isdir(sandbox.instance_dir)

    # Deploy registers the instance as a configured connection, so it now shows
    # up in db.list_connections.
    listed = helpers.tool_payload(
        helpers.call_tool(function_groups=["db"], tool_name="db.list_connections")
    )
    assert sandbox.uri in listed

    # The deployed instance reports a vendor and a version.
    vendor = helpers.tool_payload(
        _sandbox_call(
            "sandbox.vendor",
            {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
        )
    )
    assert vendor in ("MariaDB", "MySQL")

    server_version = helpers.tool_payload(
        _sandbox_call(
            "sandbox.version",
            {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
        )
    )
    assert isinstance(server_version, str) and server_version != ""


def test_sandbox_shutdown(sandbox):
    """Part 2: stops and deletes the shared sandbox. Runs last."""
    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    stop_result = _sandbox_call(
        "sandbox.stop",
        {
            "port": sandbox.port,
            "sandbox_dir": sandbox.sandbox_dir,
            "password": sandbox.password,
        },
    )
    assert stop_result.is_error is False

    delete_result = _sandbox_call(
        "sandbox.delete",
        {"port": sandbox.port, "sandbox_dir": sandbox.sandbox_dir},
    )
    assert delete_result.is_error is False

    # After a successful delete the instance directory is gone.
    assert not os.path.isdir(sandbox.instance_dir)

    # Delete also removes the connection that deploy registered. An empty
    # connection list yields no content blocks, i.e. a None payload.
    listed = helpers.tool_payload(
        helpers.call_tool(function_groups=["db"], tool_name="db.list_connections")
    ) or []
    assert sandbox.uri not in listed


def test_sandbox_dir_outside_allowed_paths_is_rejected(allowed_temp_dir, tmp_path):
    """A sandbox_dir outside the allowed paths must be rejected by the server."""
    disallowed_dir = str(tmp_path / "not_allowed")
    os.makedirs(disallowed_dir, exist_ok=True)

    result = _sandbox_call(
        "sandbox.vendor",
        {"port": helpers.find_free_port(), "sandbox_dir": disallowed_dir},
    )

    # The path guard raises, which the MCP server reports as a tool error.
    assert result.is_error is True
    payload = helpers.tool_payload(result)
    assert isinstance(payload, str) and "not allowed" in payload
