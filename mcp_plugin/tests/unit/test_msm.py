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


# --- the project list -----------------------------------------------------------


@pytest.fixture
def project_lists(tmp_path, monkeypatch):
    """Keeps every project list a test writes in a directory of its own."""
    from msm_plugin.lib import projects

    data_path = tmp_path / "msm_plugin_data"
    data_path.mkdir()
    monkeypatch.setattr(projects, "get_data_path", lambda: str(data_path))

    return projects


def _msm_tools():
    """Registers the msm tools in-process, without a server."""
    from mcp_plugin.lib import msm_functions

    recorder = helpers.ToolRecorder()
    msm_functions.register_msm_tools(recorder, function_groups=["msm"])

    return recorder.tools


def _call(tools, name, *args, **kwargs):
    """Calls one of the async msm tools, as a single-tenant stdio client."""
    return asyncio.run(tools[name](None, *args, **kwargs))


def _make_project(root, name):
    """Makes a folder that passes for a schema project and returns its path."""
    import json

    folder = os.path.join(root, f"{name}.msm.project")
    os.makedirs(folder)
    with open(os.path.join(folder, "msm.project.json"), "w") as settings:
        json.dump({"schemaName": name}, settings)

    return folder


def test_the_project_colors_are_the_connection_colors(project_lists):
    """One palette for both lists, so a client shows them with one piece of code."""
    assert project_lists.COLORS == config.CONNECTION_COLORS


def test_the_project_list_is_managed_through_the_tools(project_lists, allowed_temp_dir):
    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")
    assert _call(tools, "msm.list_projects") == []

    added = _call(tools, "msm.add_project", shop, folder="Shop", color="green")
    assert added == {
        "project_path": shop,
        "folder": "/Shop",
        "caption": "",
        "color": "green",
        "mcp_access": True,
        "schema_name": "shop",
        "exists": True,
    }
    assert _call(tools, "msm.list_projects") == [{**added, "development_version": None}]

    moved = _call(
        tools, "msm.update_project", shop, new_folder="/Archive", new_caption="Old"
    )
    assert moved["folder"] == "/Archive"
    assert moved["caption"] == "Old"
    assert moved["color"] == "green"

    assert _call(tools, "msm.remove_project", shop) == shop
    assert _call(tools, "msm.list_projects") == []
    assert os.path.isdir(shop)


def test_a_created_project_is_listed(project_lists, allowed_temp_dir):
    tools = _msm_tools()

    created = _call(
        tools,
        "msm.create_project",
        SCHEMA_NAME,
        allowed_temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
    )

    [listed] = _call(tools, "msm.list_projects")
    assert listed["project_path"] == created
    assert listed["folder"] == "/"
    assert listed["schema_name"] == SCHEMA_NAME
    assert listed["development_version"] == "0.0.1"


def test_a_project_is_created_into_its_place_in_the_list(project_lists, allowed_temp_dir):
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    with pytest.raises(ToolError, match="not a color"):
        _call(tools, "msm.create_project", SCHEMA_NAME, allowed_temp_dir,
              copyright_holder=COPYRIGHT_HOLDER, color="teal")
    # Refused before the folder was made.
    assert os.listdir(allowed_temp_dir) == []

    _call(tools, "msm.create_project", SCHEMA_NAME, allowed_temp_dir,
          copyright_holder=COPYRIGHT_HOLDER, folder="Shop", caption="Shop", color="blue")
    [listed] = _call(tools, "msm.list_projects")
    assert (listed["folder"], listed["caption"], listed["color"]) == ("/Shop", "Shop", "blue")


def test_adding_a_project_needs_an_allowed_path(project_lists, clean_config, tmp_path):
    """The list is no way around the allowed paths."""
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(str(tmp_path), "shop")
    assert config.is_path_allowed(shop) is False

    with pytest.raises(ToolError, match="not allowed"):
        _call(tools, "msm.add_project", shop)

    assert _call(tools, "msm.list_projects") == []


def test_a_missing_project_is_listed_as_such(project_lists, allowed_temp_dir):
    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")
    _call(tools, "msm.add_project", shop)
    os.remove(os.path.join(shop, "msm.project.json"))

    [listed] = _call(tools, "msm.list_projects")

    assert listed["exists"] is False
    assert listed["schema_name"] is None
    assert _call(tools, "msm.remove_project", shop) == shop


def test_a_bad_detail_is_refused_before_anything_is_written(project_lists, allowed_temp_dir):
    """The plugin's ValueError reaches the client as a tool error, nothing written."""
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")

    with pytest.raises(ToolError, match="not a color"):
        _call(tools, "msm.add_project", shop, color="teal")
    with pytest.raises(ToolError, match="not in the project list"):
        _call(tools, "msm.update_project", shop, new_folder="/Shop")

    assert _call(tools, "msm.list_projects") == []


# --- MCP access to a project ----------------------------------------------------


def _parameters(tools, name):
    import inspect

    return list(inspect.signature(tools[name]).parameters)


def test_only_a_gui_server_offers_mcp_access(project_lists, gui_mode):
    from mcp_plugin.lib import general

    gui_tools = _msm_tools()
    assert "mcp_access" in _parameters(gui_tools, "msm.create_project")
    assert "mcp_access" in _parameters(gui_tools, "msm.add_project")
    assert "new_mcp_access" in _parameters(gui_tools, "msm.update_project")
    assert "mcp_access:" in gui_tools["msm.add_project"].__doc__

    general.set_gui_mode(False)
    tools = _msm_tools()
    assert "mcp_access" not in _parameters(tools, "msm.create_project")
    assert "mcp_access" not in _parameters(tools, "msm.add_project")
    assert "new_mcp_access" not in _parameters(tools, "msm.update_project")
    assert "mcp_access" not in tools["msm.add_project"].__doc__
    assert "{gui_argument_doc}" not in tools["msm.update_project"].__doc__


def test_a_gui_server_adds_projects_without_mcp_access(project_lists, gui_mode, tmp_path):
    tools = _msm_tools()
    shop = _make_project(str(tmp_path), "shop")
    notes = _make_project(str(tmp_path), "notes")

    assert _call(tools, "msm.add_project", shop)["mcp_access"] is False
    assert _call(tools, "msm.add_project", notes, mcp_access=True)["mcp_access"] is True
    assert _call(tools, "msm.update_project", notes, new_mcp_access=False)[
        "mcp_access"
    ] is False

    created = _call(
        tools,
        "msm.create_project",
        SCHEMA_NAME,
        str(tmp_path),
        copyright_holder=COPYRIGHT_HOLDER,
    )
    listed = {project["project_path"]: project for project in _call(tools, "msm.list_projects")}
    assert listed[created]["mcp_access"] is False
    assert len(listed) == 3, "a --gui server lists every project"


def test_an_agent_adds_projects_with_mcp_access(project_lists, allowed_temp_dir):
    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")

    assert _call(tools, "msm.add_project", shop)["mcp_access"] is True
    created = _call(
        tools,
        "msm.create_project",
        SCHEMA_NAME,
        allowed_temp_dir,
        copyright_holder=COPYRIGHT_HOLDER,
    )
    assert all(project["mcp_access"] for project in _call(tools, "msm.list_projects"))
    assert created in [project["project_path"] for project in _call(tools, "msm.list_projects")]


def test_an_agent_cannot_reach_a_project_kept_from_it(project_lists, allowed_temp_dir):
    """Listed without MCP access, a project is out of an agent's reach, even
    inside an allowed path: not listed, not read, not changed, not removed."""
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")
    notes = _make_project(allowed_temp_dir, "notes")
    project_lists.add_project(shop, mcp_access=False)
    project_lists.add_project(notes)

    assert [project["project_path"] for project in _call(tools, "msm.list_projects")] == [notes]

    refused = [
        ("msm.get_project_information", (), {"schema_project_path": shop}),
        ("msm.get_released_versions", (), {"schema_project_path": shop}),
        ("msm.prepare_release", ("0.0.1",), {"schema_project_path": shop}),
        ("msm.get_sql_content_from_section", (os.path.join(shop, "x.sql"), "100"), {}),
        ("msm.add_project", (shop,), {"caption": "mine now"}),
        ("msm.update_project", (shop,), {"new_folder": "/Taken"}),
        ("msm.remove_project", (shop,), {}),
    ]
    for name, args, kwargs in refused:
        with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
            _call(tools, name, *args, **kwargs)

    [kept] = [p for p in project_lists.list_projects() if p["project_path"] == shop]
    assert kept["caption"] == "" and kept["folder"] == "/" and kept["mcp_access"] is False


def test_an_agent_cannot_replace_a_project_kept_from_it(project_lists, allowed_temp_dir):
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, SCHEMA_NAME)
    project_lists.add_project(shop, mcp_access=False)

    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        _call(
            tools,
            "msm.create_project",
            SCHEMA_NAME,
            allowed_temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
            overwrite_existing=True,
        )
    assert os.path.isfile(os.path.join(shop, "msm.project.json"))


def test_the_working_directory_counts_for_a_project_left_out(
    project_lists, allowed_temp_dir, monkeypatch
):
    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, "shop")
    project_lists.add_project(shop, mcp_access=False)
    monkeypatch.chdir(shop)

    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        _call(tools, "msm.get_project_information")
    # "" reaches the working directory on disk just as None does.
    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        _call(tools, "msm.get_project_information", schema_project_path="")


def test_an_agent_cannot_recreate_a_missing_project_kept_from_it(
    project_lists, allowed_temp_dir
):
    """A listed project whose folder went missing keeps its setting: creating
    a project in its place would otherwise add it to the list with access."""
    import shutil

    from mcp.server.mcpserver.exceptions import ToolError

    tools = _msm_tools()
    shop = _make_project(allowed_temp_dir, SCHEMA_NAME)
    project_lists.add_project(shop, mcp_access=False)
    shutil.rmtree(shop)

    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        _call(
            tools,
            "msm.create_project",
            SCHEMA_NAME,
            allowed_temp_dir,
            copyright_holder=COPYRIGHT_HOLDER,
        )
    assert not os.path.exists(shop)
    (listed,) = project_lists.list_projects()
    assert listed["mcp_access"] is False


def test_other_tool_groups_cannot_reach_a_project_kept_from_it(
    project_lists, allowed_temp_dir
):
    """The check sits in require_allowed_path, which every path argument of
    every group goes through."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_plugin.lib import general

    shop = _make_project(allowed_temp_dir, "shop")
    project_lists.add_project(shop, mcp_access=False)

    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        asyncio.run(general.require_allowed_path(None, os.path.join(shop, "dump")))
    asyncio.run(general.require_allowed_path(None, os.path.join(allowed_temp_dir, "dump")))


def test_the_other_case_of_a_project_kept_from_it_is_refused_too(
    project_lists, allowed_temp_dir
):
    """Where the file system ignores case, SHOP.msm.project is the listed
    shop.msm.project, and the allowed path keeps its spelling."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_plugin.lib import general

    shop = _make_project(allowed_temp_dir, "shop")
    project_lists.add_project(shop, mcp_access=False)
    recased = os.path.join(allowed_temp_dir, "SHOP.msm.project", "dump")
    if not os.path.isdir(os.path.dirname(recased)):
        pytest.skip("the file system tells the two spellings apart")

    with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
        asyncio.run(general.require_allowed_path(None, recased))


def test_a_sql_script_in_a_project_kept_from_it_is_not_read(project_lists, allowed_temp_dir):
    """db.execute_sql_script checks its paths itself, the same way."""
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_plugin.lib import db_functions

    shop = _make_project(allowed_temp_dir, "shop")
    script = os.path.join(shop, "script.sql")
    with open(script, "w", encoding="utf-8") as script_file:
        script_file.write("SELECT 1;")
    project_lists.add_project(shop, mcp_access=False)

    for text, file_path, source_dir in ((None, script, None), ("SELECT 1;", None, shop)):
        with pytest.raises(ToolError, match="keeps to the MariaDB VS Code extension"):
            db_functions.read_sql_input(
                None, text, file_path, source_dir, "sql_script", "inline"
            )
    with pytest.raises(ValueError, match="keeps to the MariaDB VS Code extension"):
        db_functions.allowed_include(script, None)


def test_a_gui_server_reaches_every_project(project_lists, gui_mode, tmp_path):
    tools = _msm_tools()
    shop = _make_project(str(tmp_path), "shop")
    project_lists.add_project(shop, mcp_access=False)

    assert _call(tools, "msm.update_project", shop, new_caption="Mine")["caption"] == "Mine"
    assert _call(tools, "msm.remove_project", shop) == shop
