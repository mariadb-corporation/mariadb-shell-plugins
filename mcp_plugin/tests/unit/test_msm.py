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

"""Tests for the msm.* MCP tools, driven over the stdio transport."""

# cSpell:ignore mysqlsh MariaDB

import os

import pytest

import mcp_plugin.tests.unit.helpers as helpers

SCHEMA_NAME = "mcp_pytest_schema"
COPYRIGHT_HOLDER = "MariaDB plc and/or its affiliates."


def test_stdio_creates_msm_project(allowed_temp_dir):
    """Creating an MSM project through the server and verifying the result."""
    pytest.importorskip("mcp")

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
