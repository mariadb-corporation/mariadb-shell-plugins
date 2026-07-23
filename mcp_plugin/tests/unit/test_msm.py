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

from mcp_plugin.lib import config
import mcp_plugin.tests.unit.helpers as helpers

SCHEMA_NAME = "mcp_pytest_schema"
COPYRIGHT_HOLDER = "MariaDB plc and/or its affiliates."


def _accept_trust_path_callback():
    """Returns an elicitation callback that accepts and trusts the path.

    It answers every elicitation/create request with action "accept" and
    ``trust=True`` - i.e. the user confirming that the path should be added to
    the allowed paths.
    """
    import mcp.types as types

    async def _callback(context, params):
        return types.ElicitResult(action="accept", content={"trust": True})

    return _callback


def _decline_trust_path_callback():
    """Returns an elicitation callback that declines every request.

    It answers every elicitation/create request with action "decline" - i.e.
    the user refusing to add the path to the allowed paths.
    """
    import mcp.types as types

    async def _callback(context, params):
        return types.ElicitResult(action="decline")

    return _callback


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


def test_stdio_elicits_to_allow_new_path(clean_config, tmp_path):
    """Creating a project on a not-yet-allowed path succeeds when the user
    accepts the elicitation, and the path is then persisted as allowed."""
    pytest.importorskip("mcp")

    target_path = os.path.abspath(str(tmp_path / "new_project_root"))
    os.makedirs(target_path, exist_ok=True)

    # Precondition: the target path must not be allowed yet, so the server has
    # to ask the user (via elicitation) whether to trust it.
    assert config.is_path_allowed(target_path) is False

    result = helpers.call_tool(
        function_groups=["msm"],
        tool_name="msm.create_project",
        arguments={
            "schema_name": SCHEMA_NAME,
            "target_path": target_path,
            "copyright_holder": COPYRIGHT_HOLDER,
        },
        elicitation_callback=_accept_trust_path_callback(),
    )

    # The path was not allowed, but the accepted elicitation authorized it, so
    # the project is created.
    assert result.isError is False

    project_path = helpers.tool_payload(result)
    assert isinstance(project_path, str) and project_path != ""
    assert os.path.isdir(project_path)
    assert os.path.abspath(project_path).startswith(target_path)

    # Accepting the elicitation persisted the path to the allowed paths, so a
    # later access no longer needs to ask.
    assert config.is_path_allowed(target_path) is True


def test_stdio_elicits_and_declines_new_path(clean_config, tmp_path):
    """Creating a project on a not-yet-allowed path is refused when the user
    declines the elicitation, and the path is not persisted."""
    pytest.importorskip("mcp")

    target_path = os.path.abspath(str(tmp_path / "new_project_root"))
    os.makedirs(target_path, exist_ok=True)

    # Precondition: the target path must not be allowed yet, so the server has
    # to ask the user (via elicitation) whether to trust it.
    assert config.is_path_allowed(target_path) is False

    result = helpers.call_tool(
        function_groups=["msm"],
        tool_name="msm.create_project",
        arguments={
            "schema_name": SCHEMA_NAME,
            "target_path": target_path,
            "copyright_holder": COPYRIGHT_HOLDER,
        },
        elicitation_callback=_decline_trust_path_callback(),
    )

    # Declining leaves the path disallowed, so the guard reports a tool error.
    assert result.isError is True
    payload = helpers.tool_payload(result)
    assert isinstance(payload, str) and "not allowed" in payload

    # The path was not persisted and no project folder was created.
    assert config.is_path_allowed(target_path) is False
    assert os.listdir(target_path) == []
