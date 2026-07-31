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

"""Tests for the msm.* MCP tools, driven over the stdio transport."""

# cSpell:ignore mysqlsh MariaDB

import asyncio
import os

import pytest

from mcp_plugin.lib import config
import mcp_plugin.tests.unit.helpers as helpers

SCHEMA_NAME = "mcp_pytest_schema"
COPYRIGHT_HOLDER = "MariaDB plc."

# A CREATE TABLE statement written into MSM section 140 (non-idempotent schema
# objects) of the development script so the prepared release has real content.
SECTION_140_SQL = """
CREATE TABLE `mcp_pytest_schema`.`mcp_pytest_table`(
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(255)
);"""


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

    assert result.is_error is False

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
    assert result.is_error is False

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
    assert result.is_error is True
    payload = helpers.tool_payload(result)
    assert isinstance(payload, str) and "not allowed" in payload

    # The path was not persisted and no project folder was created.
    assert config.is_path_allowed(target_path) is False
    assert os.listdir(target_path) == []


def test_stdio_deploy_schema_requires_the_db_group():
    """msm.deploy_schema is only advertised when the db group is served too.

    It needs a connection opened with db.connect, so serving the msm group on
    its own must leave it out - while still registering every other msm tool.
    """
    pytest.importorskip("mcp")

    msm_only = helpers.list_tool_names(["msm"])
    assert "msm.deploy_schema" not in msm_only
    # The tools registered around it are unaffected by the gate.
    assert "msm.create_project" in msm_only
    assert "msm.get_deployment_script_versions" in msm_only

    with_db = helpers.list_tool_names(["msm", "db"])
    assert "msm.deploy_schema" in with_db
    assert "msm.get_deployment_script_versions" in with_db
    assert "db.connect" in with_db


def test_stdio_msm_project_lifecycle(allowed_temp_dir, sandbox):
    """Drives a project through every remaining msm.* tool over one session.

    A single persistent stdio session (one server subprocess) is used so the
    whole create -> edit -> release -> deploy-script -> deploy lifecycle runs
    against the same project. All paths live under the pre-allowed temp
    directory, so no elicitation is involved. This exercises the msm tools that
    the create/elicit tests do not touch (get/set version, sections, release,
    deployment script and the deployment itself onto the shared sandbox).
    """
    pytest.importorskip("mcp")

    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    async def _run():
        async with helpers.mcp_session(function_groups=["msm", "db"]) as call:
            def payload(result):
                assert result.is_error is False, helpers.tool_payload(result)
                return helpers.tool_payload(result)

            # Create the project inside the allowed directory.
            project_path = payload(
                await call(
                    "msm.create_project",
                    {
                        "schema_name": SCHEMA_NAME,
                        "target_path": allowed_temp_dir,
                        "copyright_holder": COPYRIGHT_HOLDER,
                    },
                )
            )
            assert isinstance(project_path, str) and os.path.isdir(project_path)

            # A freshly created project starts at development version 0.0.1 with
            # nothing released or deployed yet.
            info = payload(
                await call(
                    "msm.get_project_information",
                    {"schema_project_path": project_path},
                )
            )
            assert info["currentDevelopmentVersion"] == "0.0.1"

            assert payload(
                await call(
                    "msm.get_released_versions",
                    {"schema_project_path": project_path},
                )
            ) in ([], None)
            assert payload(
                await call(
                    "msm.get_last_released_version",
                    {"schema_project_path": project_path},
                )
            ) is None
            assert payload(
                await call(
                    "msm.get_last_deployment_version",
                    {"schema_project_path": project_path},
                )
            ) is None
            assert payload(
                await call(
                    "msm.get_deployment_script_versions",
                    {"schema_project_path": project_path},
                )
            ) in ([], None)

            # Bump the development version and confirm it round-trips, then set
            # it back so the release below is created as 0.0.1.
            payload(
                await call(
                    "msm.set_development_version",
                    {"schema_project_path": project_path, "version": "0.0.2"},
                )
            )
            info = payload(
                await call(
                    "msm.get_project_information",
                    {"schema_project_path": project_path},
                )
            )
            assert info["currentDevelopmentVersion"] == "0.0.2"
            payload(
                await call(
                    "msm.set_development_version",
                    {"schema_project_path": project_path, "version": "0.0.1"},
                )
            )

            # Write SQL into section 140 of the development script and read it
            # back through the section tools.
            dev_file = os.path.join(
                project_path, "development", f"{SCHEMA_NAME}_next.sql"
            )
            assert os.path.isfile(dev_file)
            payload(
                await call(
                    "msm.set_section_sql_content",
                    {
                        "file_path": dev_file,
                        "section_id": "140",
                        "sql_content": SECTION_140_SQL,
                    },
                )
            )
            section_sql = payload(
                await call(
                    "msm.get_sql_content_from_section",
                    {"file_path": dev_file, "section_id": "140"},
                )
            )
            assert section_sql == SECTION_140_SQL.strip()

            # Prepare the first release (0.0.1 -> next 0.0.2). The first release
            # produces exactly the versions/<schema>_0.0.1.sql file.
            generated = payload(
                await call(
                    "msm.prepare_release",
                    {
                        "schema_project_path": project_path,
                        "version": "0.0.1",
                        "next_version": "0.0.2",
                    },
                )
            )
            assert isinstance(generated, list) and len(generated) == 1

            # The release now shows up as the last released version.
            assert payload(
                await call(
                    "msm.get_released_versions",
                    {"schema_project_path": project_path},
                )
            ) == [[0, 0, 1]]
            assert payload(
                await call(
                    "msm.get_last_released_version",
                    {"schema_project_path": project_path},
                )
            ) == [0, 0, 1]

            # Generate the deployment script for the release; it must be created
            # as a real file and then be listed among the deployment versions.
            deployment_script = payload(
                await call(
                    "msm.generate_deployment_script",
                    {"schema_project_path": project_path, "version": "0.0.1"},
                )
            )
            assert isinstance(deployment_script, str) and os.path.isfile(
                deployment_script
            )
            assert payload(
                await call(
                    "msm.get_deployment_script_versions",
                    {"schema_project_path": project_path},
                )
            ) == [[0, 0, 1]]

            # Deploy the release onto the sandbox. msm.deploy_schema is the only
            # msm tool that needs a database connection, so the db group is
            # loaded alongside and db.connect provides the connection id.
            connection_id = payload(await call("db.connect", {"uri": sandbox.uri}))
            try:
                deployed = payload(
                    await call(
                        "msm.deploy_schema",
                        {
                            "connection_id": connection_id,
                            "schema_project_path": project_path,
                            "version": "0.0.1",
                        },
                    )
                )
                assert "0.0.1" in deployed

                # The schema and the table from section 140 are really there,
                # and the deployed version is now reported as such.
                tables = payload(
                    await call(
                        "db.list_objects",
                        {
                            "connection_id": connection_id,
                            "schema_name": SCHEMA_NAME,
                        },
                    )
                )
                # tool_payload hands back the bare entry for a single-entry
                # list, and the schema holds exactly one table.
                if isinstance(tables, dict):
                    tables = [tables]
                assert "mcp_pytest_table" in [
                    entry["name"] for entry in tables or []
                ]
            finally:
                payload(
                    await call(
                        "db.execute_sql",
                        {
                            "connection_id": connection_id,
                            "sql": f"DROP SCHEMA IF EXISTS `{SCHEMA_NAME}`",
                        },
                    )
                )
                await call("db.close", {"connection_id": connection_id})

    asyncio.run(_run())
