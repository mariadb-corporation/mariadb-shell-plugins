# Copyright (c) 2026, MariaDB plc and/or its affiliates.
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

"""Tests for the MariaDB MCP server, driven over the stdio transport."""

# cSpell:ignore mysqlsh MariaDB

import os

import pytest

# The MCP client SDK is required to talk to the stdio server.
pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers

SCHEMA_NAME = "mcp_pytest_schema"
COPYRIGHT_HOLDER = "MariaDB plc and/or its affiliates."


def test_stdio_lists_stored_connections(stored_connections):
    """Storing two connections and reading them back over the stdio server."""
    result = helpers.call_tool(
        function_groups=["db"],
        tool_name="db.list_connections",
    )

    assert result.isError is False

    listed = helpers.tool_payload(result)
    assert isinstance(listed, list)

    # The two stored connections must be reported by the server.
    for uri in stored_connections:
        assert uri in listed


def test_stdio_creates_msm_project(allowed_temp_dir):
    """Creating an MSM project through the server and verifying the result."""
    result = helpers.call_tool(
        function_groups=["msm"],
        tool_name="msm.create_project",
        arguments={
            "schema_name": SCHEMA_NAME,
            "target_path": allowed_temp_dir,
            "copyright_holder": COPYRIGHT_HOLDER,
        },
    )

    assert result.isError is False

    project_path = helpers.tool_payload(result)
    assert isinstance(project_path, str) and project_path != ""

    # The project folder must have been created inside the allowed directory
    # and must not be empty.
    assert os.path.isdir(project_path)
    assert os.path.abspath(project_path).startswith(os.path.abspath(allowed_temp_dir))
    assert os.listdir(project_path)
    # The temporary directory (and this project) is removed by the fixture.
