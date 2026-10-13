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

"""Tests for the mrs.* MCP tools, driven over the stdio transport.

The tools that work on a REST service run against the shared sandbox. They
deploy the REST metadata under a schema name of their own and drop it again,
so that test_rest_sql.py still finds a server without any.
"""

# cSpell:ignore mysqlsh MariaDB mcpt

import asyncio
import json
import os

import pytest

pytest.importorskip("mcp")

import mcp_plugin.tests.unit.helpers as helpers

# A metadata schema of this module's own: <prefix>mariadb_rest_service.
_METADATA_SCHEMA = "mcpt_mariadb_rest_service"
_ROLES = ("admin", "schema_admin", "dev", "user", "meta_provider", "data_provider")
_DATA_SCHEMA = "mcp_mrs_test"

_MRS_SCRIPT = """@Mrs.module({ name: "hello", requestPath: "/hello" })
class Hello {
    @Mrs.script({ name: "greet", requiresAuth: false })
    public static async greet(name: string): Promise<string> {
        return "Hello " + name;
    }
}
"""

_SESSION_TOOLS = {
    "mrs.get_sdk_service_classes",
    "mrs.dump_sdk_service_files",
    "mrs.get_runtime_management_code",
    "mrs.dump_service",
    "mrs.load_service",
    "mrs.dump_service_project",
    "mrs.load_service_project",
    "mrs.load_content_set",
    "mrs.dump_audit_log",
}


def _write(directory, relative_path, text):
    path = os.path.join(directory, *relative_path.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    return path


def test_the_session_tools_need_the_db_group():
    """Without db.connect there is no session, so they are left out."""
    alone = set(helpers.list_tool_names(["mrs"]))
    assert "mrs.version" in alone
    assert "mrs.get_folder_mrs_script_definitions" in alone
    assert not alone & _SESSION_TOOLS

    with_db = set(helpers.list_tool_names(["mrs", "db"]))
    assert _SESSION_TOOLS <= with_db


def test_mrs_is_a_default_group():
    """But not one of a multi-tenant server: its tools work on local files."""
    from mcp_plugin.lib import general

    assert "mrs" in general.DEFAULT_FUNCTION_GROUPS
    assert "mrs" not in general.MULTI_TENANT_FUNCTION_GROUPS


def test_version_and_sdk_base_classes():
    version = helpers.tool_payload(helpers.call_tool(["mrs"], "mrs.version"))
    assert isinstance(version, str) and version.count(".") == 2

    source = helpers.tool_payload(
        helpers.call_tool(["mrs"], "mrs.get_sdk_base_classes", {"sdk_language": "TypeScript"})
    )
    assert "class MrsBaseService" in source


def test_mrs_scripts_are_found_in_a_folder(allowed_temp_dir):
    _write(allowed_temp_dir, "src/hello.mts", _MRS_SCRIPT)
    _write(allowed_temp_dir, "static/index.html", "<html></html>")

    language = helpers.tool_payload(
        helpers.call_tool(
            ["mrs"], "mrs.get_folder_mrs_script_language", {"path": allowed_temp_dir}
        )
    )
    assert language == "TypeScript"

    definitions = helpers.tool_payload(
        helpers.call_tool(
            ["mrs"], "mrs.get_folder_mrs_script_definitions", {"path": allowed_temp_dir}
        )
    )
    (module,) = definitions["script_modules"]
    assert module["class_name"] == "Hello"
    assert [script["function_name"] for script in module["scripts"]] == ["greet"]
    # The analysis also wants the project built; nothing else is wrong.
    assert [error["kind"] for error in definitions["errors"]] == ["BuildError"]

    one_file = helpers.tool_payload(
        helpers.call_tool(
            ["mrs"],
            "mrs.get_file_mrs_script_definitions",
            {"path": os.path.join(allowed_temp_dir, "src", "hello.mts")},
        )
    )
    # A list of one comes back as that one item.
    if isinstance(one_file, dict):
        one_file = [one_file]
    assert [module["class_name"] for module in one_file] == ["Hello"]


def test_a_path_that_is_not_allowed_is_refused(clean_config, tmp_path):
    """The client did not advertise elicitation, so nobody could trust it."""
    result = helpers.call_tool(
        ["mrs"], "mrs.get_folder_mrs_script_language", {"path": str(tmp_path)}
    )

    assert result.is_error is True
    assert "not allowed" in result.content[0].text


def test_sdk_options_of_a_folder_without_any(allowed_temp_dir):
    options = helpers.tool_payload(
        helpers.call_tool(["mrs"], "mrs.get_sdk_options", {"directory": allowed_temp_dir})
    )
    assert options in ({}, None)


async def _service_flow(uri, directory):
    async with helpers.mcp_session(["db", "mrs"], timeout=180) as call:
        connection_id = helpers.tool_payload(await call("db.connect", {"uri": uri}))

        async def sql(statement):
            result = await call(
                "db.execute_sql", {"connection_id": connection_id, "sql": statement}
            )
            assert result.is_error is False, result.content[0].text
            return result

        async def mrs(tool, **arguments):
            result = await call(tool, {"connection_id": connection_id, **arguments})
            assert result.is_error is False, result.content[0].text
            return helpers.tool_payload(result)

        async def service_paths():
            result = await sql("SHOW REST SERVICES")
            return sorted(row["REST SERVICE Path"] for row in helpers.tool_rows(result))

        try:
            await sql(f"CREATE DATABASE {_DATA_SCHEMA}")
            await sql(
                f"CREATE TABLE {_DATA_SCHEMA}.product"
                "(id INT PRIMARY KEY, name VARCHAR(40))"
            )
            await sql(f"CONFIGURE REST METADATA SCHEMA {_METADATA_SCHEMA}")
            await sql("CREATE REST SERVICE /mcpTest")
            await sql(f"CREATE REST SCHEMA /db ON SERVICE /mcpTest FROM `{_DATA_SCHEMA}`")
            await sql(
                "CREATE REST VIEW /product ON SERVICE /mcpTest SCHEMA /db "
                f"AS {_DATA_SCHEMA}.product CLASS Product {{ id: id @KEY, name: name }}"
            )

            # A service dumped to a file and loaded again under another path.
            script = os.path.join(directory, "service.sql")
            await mrs("mrs.dump_service", service_path="/mcpTest", file_path=script)
            with open(script, encoding="utf-8") as f:
                assert "CREATE OR REPLACE REST SERVICE /mcpTest" in f.read()
            await mrs("mrs.load_service", file_path=script, as_path="/mcpCopy")
            assert await service_paths() == ["/mcpCopy", "/mcpTest"]

            # The copy written as a project, dropped, and loaded again.
            project = os.path.join(directory, "project")
            await mrs(
                "mrs.dump_service_project",
                destination=project,
                services=[{
                    "name": "/mcpCopy",
                    "include_database_endpoints": True,
                    "include_static_endpoints": False,
                    "include_dynamic_endpoints": False,
                }],
                settings={"name": "MCP test", "version": "1.0.0"},
            )
            assert os.path.isfile(os.path.join(project, "mrs.package.json"))
            await sql("DROP REST SERVICE /mcpCopy")
            await mrs("mrs.load_service_project", source=project)
            assert await service_paths() == ["/mcpCopy", "/mcpTest"]

            # The client SDK of the service, as text and as files.
            (row,) = helpers.tool_rows(
                await sql("SHOW CREATE REST SERVICE /mcpTest FORMAT=JSON")
            )
            service = json.loads(next(iter(row.values())))
            classes = await mrs(
                "mrs.get_sdk_service_classes",
                service_id=service["id"],
                service_url="https://localhost:8443/mcpTest",
            )
            assert "Product" in classes

            sdk_dir = os.path.join(directory, "sdk")
            os.makedirs(sdk_dir)
            assert await mrs(
                "mrs.dump_sdk_service_files",
                directory=sdk_dir,
                options={
                    "url_context_root": "/mcpTest",
                    "service_url": "https://localhost:8443/mcpTest",
                    "sdk_language": "TypeScript",
                },
            ) is True
            assert os.listdir(sdk_dir)
            stored = helpers.tool_payload(
                await call("mrs.get_sdk_options", {"directory": sdk_dir})
            )
            assert stored["serviceUrl"] == "https://localhost:8443/mcpTest"

            # A directory uploaded as a content set, its MRS script registered.
            web = os.path.join(directory, "web")
            _write(web, "src/hello.mts", _MRS_SCRIPT)
            _write(web, "static/index.html", "<html></html>")
            _write(web, "dist/hello.mjs", "export class Hello {}")
            loaded = await mrs(
                "mrs.load_content_set",
                directory=web,
                content_set_path="/app",
                service_path="/mcpTest",
            )
            assert len(loaded["files"]) == 3
            sets = helpers.tool_rows(await sql("SHOW REST CONTENT SETS ON SERVICE /mcpTest"))
            assert [row["REST CONTENT SET path"] for row in sets] == ["/app"]

            # The audit log records all of it.
            log = os.path.join(directory, "audit.log")
            await mrs("mrs.dump_audit_log", file_path=log, starting_from_today=False)
            with open(log, encoding="utf-8") as f:
                assert f.read().strip()

            assert "mrs" in (await mrs("mrs.get_runtime_management_code")).lower()
        finally:
            await call(
                "db.execute_sql",
                {"connection_id": connection_id,
                 "sql": f"DROP DATABASE IF EXISTS {_METADATA_SCHEMA}"},
            )
            await call(
                "db.execute_sql",
                {"connection_id": connection_id,
                 "sql": f"DROP DATABASE IF EXISTS {_DATA_SCHEMA}"},
            )
            for role in _ROLES:
                await call(
                    "db.execute_sql",
                    {"connection_id": connection_id,
                     "sql": f"DROP ROLE IF EXISTS mcpt_mariadb_rest_service_{role}"},
                )
            await call("db.close", {"connection_id": connection_id})


def test_mrs_tools_work_on_a_rest_service(sandbox, allowed_temp_dir):
    if not sandbox.deployed:
        pytest.skip("sandbox was not deployed")

    asyncio.run(_service_flow(sandbox.uri, allowed_temp_dir))
