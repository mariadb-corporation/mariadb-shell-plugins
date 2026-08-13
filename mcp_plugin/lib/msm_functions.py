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

"""MCP tools wrapping the MariaDB Schema Management (``msm``) plugin.

Each tool registered here wraps the corresponding function of the
``msm_plugin/management.py`` module and is exposed on the MCPServer built
in :mod:`mcp_plugin.lib.server`.

The tools run while the shell is in non-interactive mode, so the wrapped
``msm`` plugin functions return their results instead of prompting for input.
Path arguments are authorized through
:func:`mcp_plugin.lib.general.require_allowed_path`, which may ask the user - via
MCP elicitation - to trust a path that is not yet allowed.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

from typing import Optional

from mcp_plugin.lib import db_functions, general


def register_msm_tools(server, function_groups=()) -> None:
    """Registers the MariaDB Schema Management tools on the given server.

    All tools but msm.deploy_schema work on a schema project on disk and are
    always registered. msm.deploy_schema needs a database connection opened
    with db.connect, so it is only registered when the db function group is
    served as well - it would have no way to obtain a connection otherwise.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    import anyio.to_thread
    from mcp.server.mcpserver import Context
    from msm_plugin import management as msm

    def _kwargs(**pairs) -> dict:
        """Builds a kwargs dict, dropping keys whose value is None."""
        return {key: value for key, value in pairs.items() if value is not None}

    @server.tool(name="msm.create_project")
    async def create_project(
        ctx: Context,
        schema_name: str,
        target_path: str,
        copyright_holder: Optional[str] = None,
        license: Optional[str] = None,
        overwrite_existing: bool = False,
        allow_special_chars: bool = False,
        enforce_target_path: bool = False,
    ) -> Optional[str]:
        """Creates a new database schema project folder.

        Args:
            schema_name: The name of the database schema.
            target_path: The path the project folder should be created in.
            copyright_holder: The name of the copyright holder.
            license: The license to use for the project.
            overwrite_existing: Overwrite the project folder if it exists.
            allow_special_chars: Allow all characters in the schema name.
            enforce_target_path: Create the target_path if it does not exist.

        Returns:
            The path of the created project folder.
        """
        await general.require_allowed_path(ctx, target_path)
        return msm.create_new_project_folder(
            schema_name=schema_name,
            target_path=target_path,
            copyright_holder=copyright_holder,
            **_kwargs(
                license=license,
                overwrite_existing=overwrite_existing,
                allow_special_chars=allow_special_chars,
                enforce_target_path=enforce_target_path,
            ),
        )

    @server.tool(name="msm.get_project_information")
    async def get_project_information(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[dict]:
        """Returns information about a database schema project.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            A dict with information about the schema project.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.get_project_information(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.set_development_version")
    async def set_development_version(
        ctx: Context,
        version: str,
        schema_project_path: Optional[str] = None,
    ) -> None:
        """Sets the development version of a schema project.

        Args:
            version: The new development version (major.minor.patch).
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.set_development_version(
            **_kwargs(version=version, schema_project_path=schema_project_path)
        )

    @server.tool(name="msm.get_released_versions")
    async def get_released_versions(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns all released versions of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of released versions.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.get_released_versions(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.get_last_released_version")
    async def get_last_released_version(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last released version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last released version.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.get_last_released_version(**_kwargs(schema_project_path=schema_project_path))

    @server.tool(name="msm.get_last_deployment_version")
    async def get_last_deployment_version(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the last deployment version of a database schema.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The last deployment version.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.get_last_deployment_version(
            **_kwargs(schema_project_path=schema_project_path)
        )

    @server.tool(name="msm.prepare_release")
    async def prepare_release(
        ctx: Context,
        version: str,
        next_version: Optional[str] = None,
        schema_project_path: Optional[str] = None,
        allow_to_stay_on_same_version: bool = False,
        overwrite_existing: bool = False,
    ) -> Optional[list]:
        """Prepares a new database schema release.

        Args:
            version: The version to create for the release (major.minor.patch).
            next_version: The next development version.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            allow_to_stay_on_same_version: Allow staying on the same version
                for further development work.
            overwrite_existing: Overwrite existing files.

        Returns:
            The list of generated files.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.prepare_release(
            **_kwargs(
                version=version,
                next_version=next_version,
                schema_project_path=schema_project_path,
                allow_to_stay_on_same_version=allow_to_stay_on_same_version,
                overwrite_existing=overwrite_existing,
            )
        )

    @server.tool(name="msm.get_sql_content_from_section")
    async def get_sql_content_from_section(
        ctx: Context, file_path: str, section_id: str
    ) -> Optional[str]:
        """Returns the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.

        Returns:
            The SQL content as a string.
        """
        await general.require_allowed_path(ctx, file_path)
        return msm.get_sql_content_from_section(file_path=file_path, section_id=section_id)

    @server.tool(name="msm.set_section_sql_content")
    async def set_section_sql_content(
        ctx: Context, file_path: str, section_id: str, sql_content: str
    ) -> None:
        """Sets the SQL content of an MSM section of a file.

        Args:
            file_path: The path of the SQL file.
            section_id: The id of the section.
            sql_content: The SQL content to set.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, file_path)
        return msm.set_section_sql_content(
            file_path=file_path, section_id=section_id, sql_content=sql_content
        )

    @server.tool(name="msm.generate_deployment_script")
    async def generate_deployment_script(
        ctx: Context,
        version: str,
        schema_project_path: Optional[str] = None,
        overwrite_existing: bool = False,
    ) -> Optional[str]:
        """Generates the deployment script for a release.

        Args:
            version: The version to create the deployment script for.
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.
            overwrite_existing: Overwrite existing files.

        Returns:
            The file name of the deployment script.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.generate_deployment_script(
            **_kwargs(
                version=version,
                schema_project_path=schema_project_path,
                overwrite_existing=overwrite_existing,
            )
        )

    @server.tool(name="msm.get_deployment_script_versions")
    async def get_deployment_script_versions(
        ctx: Context,
        schema_project_path: Optional[str] = None,
    ) -> Optional[list]:
        """Returns the list of deployment script versions.

        Args:
            schema_project_path: The path to the schema project. Defaults to
                the current working directory.

        Returns:
            The list of deployed versions.
        """
        await general.require_allowed_path(ctx, schema_project_path)
        return msm.get_deployment_script_versions(
            **_kwargs(schema_project_path=schema_project_path)
        )

    # Registered last, and only together with the db group: deploying needs a
    # connection opened with db.connect, so without that group the tool could
    # never obtain one and is left unadvertised rather than exposed as something
    # that cannot succeed.
    if general.FUNCTION_GROUP_DB in function_groups:

        @server.tool(name="msm.deploy_schema")
        async def deploy_schema(
            ctx: Context,
            connection_id: str,
            version: Optional[str] = None,
            schema_project_path: Optional[str] = None,
            backup_directory: Optional[str] = None,
            backup: bool = False,
        ) -> Optional[str]:
            """Deploys a version of a database schema onto an open connection.

            Runs the deployment script of the given version, which has to have
            been generated with msm.generate_deployment_script first. If the
            schema already exists it is updated to the requested version.

            Args:
                connection_id: The UUID returned by db.connect, identifying the
                    connection to deploy onto.
                version: The version to deploy. Defaults to the latest version a
                    deployment script exists for.
                schema_project_path: The path to the schema project. Defaults to
                    the current working directory.
                backup_directory: The directory to write the backup to. Only
                    used when backup is enabled.
                backup: Whether to dump an existing schema before updating it,
                    so it can be restored if the update fails. Defaults to
                    False.

            Returns:
                A message describing what was deployed.
            """
            await general.require_allowed_path(ctx, schema_project_path)
            await general.require_allowed_path(ctx, backup_directory)

            # Read here, while this is still the request's own context; the
            # worker thread below has no request context to read it from.
            client = general.get_client_identity(ctx)

            def _deploy():
                with db_functions.use_session(connection_id, client) as session:
                    return msm.deploy_schema(
                        session=session,
                        **_kwargs(
                            version=version,
                            schema_project_path=schema_project_path,
                            backup_directory=backup_directory,
                            backup=backup,
                        ),
                    )

            # On a worker thread, not here. This function is a coroutine, so its
            # body runs on the thread driving the event loop, and everything in
            # _deploy blocks: waiting for the connection's lock, and then running
            # a whole deployment script on it. Inline, that would stop the server
            # answering any client at all for as long as it took. The sync db
            # tools get this for free - the SDK already runs them on a worker
            # thread - and use_session now refuses to be entered anywhere else.
            return await anyio.to_thread.run_sync(_deploy)
