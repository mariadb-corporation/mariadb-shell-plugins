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

"""MCP tools wrapping the MariaDB REST Service (``mrs``) plugin.

The REST services themselves are managed with REST SQL (``CREATE REST
SERVICE ...``, ``SHOW REST SERVICES FORMAT=JSON``), which the shell's
built-in ``mrs`` module handles, so a client sends it through
``db.execute_sql`` like any other statement. What REST SQL cannot do is work
with the client's files, and that is what the ``mrs_plugin`` functions
wrapped here are for: client SDK generation, dumping and loading services and
projects, uploading a directory as a content set, MRS script analysis and the
audit log export.

The tools run while the shell is in non-interactive mode, so the wrapped
plugin functions return their results instead of printing them. Path
arguments are authorized through
:func:`mcp_plugin.lib.general.require_allowed_path`, which may ask the user -
via MCP elicitation - to trust a path that is not yet allowed.
"""

# cSpell:ignore mysqlsh MariaDB mcpserver

from typing import Optional

from mcp_plugin.lib import db_functions, general
from mcp_plugin.lib.tool_registrar import tool_registrar


def register_mrs_tools(server, function_groups=()) -> None:
    """Registers the MariaDB REST Service tools on the given server.

    The tools that only read files are always registered. The ones that work
    on a REST service need a database connection opened with db.connect, so
    they are only registered when the db function group is served as well.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    import anyio.to_thread
    from mcp.server.mcpserver import Context
    from mrs_plugin import content_sets, dump, general as mrs_general
    from mrs_plugin import services as mrs_services

    tool = tool_registrar(server)

    def _kwargs(**pairs) -> dict:
        """Builds a kwargs dict, dropping keys whose value is None."""
        return {key: value for key, value in pairs.items() if value is not None}

    async def _on_session(ctx, connection_id, work):
        """Runs work(session) on the connection, on a worker thread.

        Everything work does blocks - waiting for the connection's lock, then
        the plugin's SQL - so it must not run on the thread driving the event
        loop, see msm.deploy_schema.
        """
        # Read here, while this is still the request's own context; the
        # worker thread has no request context to read it from.
        client = general.get_client_identity(ctx)

        def _run():
            with db_functions.use_session(connection_id, client) as session:
                return work(session)

        return await anyio.to_thread.run_sync(_run)

    @tool(name="mrs.version")
    def version() -> str:
        """Returns the version of the MariaDB REST Service plugin.

        Returns:
            The plugin version.
        """
        return mrs_general.version()

    @tool(name="mrs.get_sdk_base_classes")
    def get_sdk_base_classes(
        sdk_language: str = "TypeScript",
        prepare_for_runtime: bool = False,
    ) -> str:
        """Returns the source of the client SDK base classes.

        Args:
            sdk_language: The SDK language, TypeScript or Python.
            prepare_for_runtime: Prepare the code to run in an editor at
                runtime rather than to be written to files.

        Returns:
            The source of the base classes.
        """
        return mrs_services.get_sdk_base_classes(
            sdk_language=sdk_language, prepare_for_runtime=prepare_for_runtime
        )

    @tool(name="mrs.get_sdk_options")
    async def get_sdk_options(ctx: Context, directory: str) -> Optional[dict]:
        """Reads the SDK options stored with an exported client SDK.

        Args:
            directory: The directory holding the mrs.config.json file.

        Returns:
            The stored SDK options, or an empty dict if there are none.
        """
        await general.require_allowed_path(ctx, directory)
        return mrs_services.get_stored_sdk_options(directory)

    @tool(name="mrs.get_folder_mrs_script_language")
    async def get_folder_mrs_script_language(
        ctx: Context,
        path: str,
        ignore_list: Optional[str] = None,
    ) -> Optional[str]:
        """Checks whether a directory holds MRS scripts.

        Args:
            path: The directory to check.
            ignore_list: Comma-separated file patterns to ignore.

        Returns:
            "TypeScript" if the directory holds MRS scripts, else None.
        """
        await general.require_allowed_path(ctx, path)
        return content_sets.get_folder_mrs_scripts_language(
            path, **_kwargs(ignore_list=ignore_list)
        )

    @tool(name="mrs.get_folder_mrs_script_definitions")
    async def get_folder_mrs_script_definitions(
        ctx: Context,
        path: str,
        ignore_list: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Optional[dict]:
        """Returns the MRS script definitions of the files in a directory.

        Args:
            path: The directory to analyse.
            ignore_list: Comma-separated file patterns to ignore.
            language: The language the MRS scripts are written in. Detected
                from the files if not given.

        Returns:
            A dict with the script_modules, the interfaces they use and the
            errors found.
        """
        await general.require_allowed_path(ctx, path)
        return await anyio.to_thread.run_sync(
            lambda: content_sets.get_folder_mrs_script_definitions(
                path, **_kwargs(ignore_list=ignore_list, language=language)
            )
        )

    @tool(name="mrs.get_file_mrs_script_definitions")
    async def get_file_mrs_script_definitions(
        ctx: Context,
        path: str,
        language: str = "TypeScript",
    ) -> list:
        """Returns the MRS script modules defined in one file.

        Args:
            path: The file to analyse.
            language: The language the MRS scripts are written in, the only
                one supported being TypeScript.

        Returns:
            The script modules of the file, each with its class_name,
            properties and scripts.
        """
        await general.require_allowed_path(ctx, path)
        return content_sets.get_file_mrs_script_definitions(path, language=language)

    # Registered only together with the db group: these work on a REST
    # service and need a connection opened with db.connect, which they would
    # have no way to obtain otherwise.
    if general.FUNCTION_GROUP_DB not in function_groups:
        return

    @tool(name="mrs.get_sdk_service_classes")
    async def get_sdk_service_classes(
        ctx: Context,
        connection_id: str,
        service_id: Optional[str] = None,
        service_url: Optional[str] = None,
        sdk_language: str = "TypeScript",
        prepare_for_runtime: bool = False,
    ) -> str:
        """Returns the source of the client SDK classes of a REST service.

        Args:
            connection_id: The UUID returned by db.connect.
            service_id: The id of the REST service. Defaults to the current
                service, or the only one.
            service_url: The URL the REST service is reached at.
            sdk_language: The SDK language, TypeScript or Python.
            prepare_for_runtime: Prepare the code to run in an editor at
                runtime rather than to be written to files.

        Returns:
            The source of the service classes.
        """
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.get_sdk_service_classes(
                session=session,
                sdk_language=sdk_language,
                prepare_for_runtime=prepare_for_runtime,
                **_kwargs(service_id=service_id, service_url=service_url),
            ),
        )

    @tool(name="mrs.dump_sdk_service_files")
    async def dump_sdk_service_files(
        ctx: Context,
        connection_id: str,
        directory: str,
        options: Optional[dict] = None,
    ) -> bool:
        """Writes the client SDK files of a REST service to a directory.

        Args:
            connection_id: The UUID returned by db.connect.
            directory: The directory to write the SDK files to.
            options: How the SDK is generated: service_id or
                url_context_root (the service), sdk_language, service_url
                (required unless stored in the directory already),
                add_app_base_class, header, db_connection_uri.

        Returns:
            True on success.
        """
        await general.require_allowed_path(ctx, directory)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.dump_sdk_service_files(
                session=session, directory=directory, **_kwargs(options=options)
            ),
        )

    @tool(name="mrs.get_runtime_management_code")
    async def get_runtime_management_code(ctx: Context, connection_id: str) -> str:
        """Returns the TypeScript code that manages REST services at runtime.

        Args:
            connection_id: The UUID returned by db.connect.

        Returns:
            The TypeScript source.
        """
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.get_runtime_management_code(session=session),
        )

    @tool(name="mrs.dump_service")
    async def dump_service(
        ctx: Context,
        connection_id: str,
        service_path: str,
        file_path: str,
        endpoints: Optional[str] = None,
        overwrite: bool = False,
    ) -> None:
        """Writes the REST SQL script that recreates a REST service to a file.

        Args:
            connection_id: The UUID returned by db.connect.
            service_path: The request path of the REST service.
            file_path: The file to write.
            endpoints: The endpoints to include: DATABASE (the default),
                DATABASE AND STATIC, DATABASE AND STATIC AND DYNAMIC or ALL.
                An empty string writes only the CREATE REST SERVICE statement.
            overwrite: Overwrite the file if it exists.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, file_path)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.dump_service(
                service_path,
                file_path,
                session=session,
                overwrite=overwrite,
                **_kwargs(endpoints=endpoints),
            ),
        )

    @tool(name="mrs.load_service")
    async def load_service(
        ctx: Context,
        connection_id: str,
        file_path: str,
        as_path: Optional[str] = None,
    ) -> None:
        """Runs a REST SQL script written by mrs.dump_service.

        Args:
            connection_id: The UUID returned by db.connect.
            file_path: The REST SQL script.
            as_path: Create the REST service under this request path instead
                of the one in the script.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, file_path)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.load_service(
                file_path, session=session, **_kwargs(as_path=as_path)
            ),
        )

    @tool(name="mrs.dump_service_project")
    async def dump_service_project(
        ctx: Context,
        connection_id: str,
        destination: str,
        services: list,
        settings: dict,
        schemas: Optional[list] = None,
        overwrite: bool = False,
        zip: bool = False,
    ) -> None:
        """Writes REST services and their database schemas as an MRS project.

        Args:
            connection_id: The UUID returned by db.connect.
            destination: The directory to create the project in.
            services: The services to include, each a dict with name
                (the request path) and the include_database_endpoints,
                include_static_endpoints and include_dynamic_endpoints flags.
            settings: The project details: name, icon_path, description,
                publisher, version.
            schemas: The database schemas to include, each a dict with name
                and file_path (a schema script to copy).
            overwrite: Overwrite the destination if it exists.
            zip: Zip the project directory.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, destination)
        await general.require_allowed_path(ctx, (settings or {}).get("icon_path"))
        for schema in schemas or []:
            await general.require_allowed_path(ctx, schema.get("file_path"))

        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.dump_service_as_project(
                session=session,
                destination=destination,
                services=services,
                schemas=schemas or [],
                settings=settings,
                overwrite=overwrite,
                zip=zip,
            ),
        )

    @tool(name="mrs.load_service_project")
    async def load_service_project(
        ctx: Context,
        connection_id: str,
        source: str,
    ) -> None:
        """Loads an MRS project written by mrs.dump_service_project.

        Args:
            connection_id: The UUID returned by db.connect.
            source: The project directory or zip file.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, source)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: mrs_services.load_service_project(
                session=session, source=source
            ),
        )

    @tool(name="mrs.load_content_set")
    async def load_content_set(
        ctx: Context,
        connection_id: str,
        directory: str,
        content_set_path: str,
        service_path: Optional[str] = None,
        ignore_list: Optional[str] = None,
        load_scripts: Optional[bool] = None,
        replace: bool = False,
    ) -> Optional[dict]:
        """Uploads the files of a directory as a REST content set.

        Args:
            connection_id: The UUID returned by db.connect.
            directory: The directory holding the files.
            content_set_path: The request path of the content set.
            service_path: The request path of the REST service. Defaults to
                the current one.
            ignore_list: Comma-separated file patterns to ignore, matched
                against the path relative to the directory. Defaults to
                "*node_modules/*, */.*".
            load_scripts: Register the MRS scripts of the files. By default
                they are registered if the directory holds any.
            replace: Replace the content set if it exists.

        Returns:
            A dict with the request paths of the uploaded files and a
            message.
        """
        await general.require_allowed_path(ctx, directory)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: content_sets.load_content_set(
                directory,
                content_set_path,
                session=session,
                replace=replace,
                **_kwargs(
                    service_path=service_path,
                    ignore_list=ignore_list,
                    load_scripts=load_scripts,
                ),
            ),
        )

    @tool(name="mrs.dump_audit_log")
    async def dump_audit_log(
        ctx: Context,
        connection_id: str,
        file_path: str,
        audit_log_position_file: Optional[str] = None,
        audit_log_position: Optional[int] = None,
        starting_from_today: Optional[bool] = None,
        when_server_is_writeable: Optional[bool] = None,
    ) -> None:
        """Appends the new entries of the MRS audit log to a file.

        Args:
            connection_id: The UUID returned by db.connect.
            file_path: The file to write the audit log to.
            audit_log_position_file: The file keeping the export position.
                Defaults to mrs_audit_log_position.json next to file_path.
            audit_log_position: The position to export from; everything up
                to it counts as exported.
            starting_from_today: Only export entries from today on when no
                position is known. Defaults to true.
            when_server_is_writeable: Only export when the server is not
                read only. Defaults to false.

        Returns:
            None
        """
        await general.require_allowed_path(ctx, file_path)
        await general.require_allowed_path(ctx, audit_log_position_file)
        return await _on_session(
            ctx,
            connection_id,
            lambda session: dump.export_audit_log(
                file_path,
                session=session,
                **_kwargs(
                    audit_log_position_file=audit_log_position_file,
                    audit_log_position=audit_log_position,
                    starting_from_today=starting_from_today,
                    when_server_is_writeable=when_server_is_writeable,
                ),
            ),
        )
