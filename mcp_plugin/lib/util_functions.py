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

"""MCP tools wrapping the shell's dump, load, copy, export and import utilities.

Every utility runs as a background task (see :mod:`mcp_plugin.lib.tasks`): the
tool starts it and returns its id at once, and ``task.get`` follows it.
A task runs the utility in a MariaDB Shell process of its own (see
:mod:`mcp_plugin.lib.util_process`), connected with the URI and password of
the connection a client got from ``db.connect``. So it never takes that
connection's lock, the server's global session is never touched, and a
utility that crashes takes only its own process down: the task reports it,
and a load is resumed.

Paths are authorized with :func:`mcp_plugin.lib.general.require_allowed_path`;
a URL, or a prefix in object storage, is no path on this machine and is left
to the utility.

The group is never served in multi-tenant mode: the files are on the server's
disk, and a dump takes connections and threads from every other user.
"""
# cSpell:ignore mysqlsh MariaDB mcpserver

import os
from typing import Optional, Union

from mcp_plugin.lib import db_functions, general, tasks, util_process, util_zip
from mcp_plugin.lib.tool_registrar import tool_error, tool_registrar

# The options the tools set themselves, or that only a call in the shell's own
# process could give.
_RESERVED_OPTIONS = ("session", "progressCallback", "showProgress")

# For the utilities whose outputUrl comes after a list (dump_schemas and
# dump_tables): the shell's command line takes it by name there, and an option
# of that name would replace output_url - with a path never checked.
_OUTPUT_URL_PARAMETER = {
    "outputUrl": "it is the tool's output_url argument, and only that one is used."
}

# Options that make the URL a prefix in object storage instead of a path.
_REMOTE_STORAGE_OPTIONS = ("s3BucketName", "osBucketName", "azureContainerName")

# Options that name a local file the utility reads or writes, whatever the
# storage its URL is in: the load's progress file, and the configuration and
# credential files of the object storage services.
_LOCAL_FILE_OPTIONS = (
    "progressFile",
    "ociConfigFile",
    "s3ConfigFile",
    "s3CredentialsFile",
    "azureConfigFile",
)

_SCHEME_DELIMITER = "://"


def _options(options: Optional[dict]) -> dict:
    """The utility options a client gave, refusing the ones set here."""
    options = dict(options or {})
    reserved = sorted(key for key in options if key in _RESERVED_OPTIONS)

    if reserved:
        raise tool_error(
            "These options are set by the tool and cannot be given: "
            + ", ".join(reserved)
            + "."
        )

    return options


def _local_path(url) -> Optional[str]:
    """The path on this machine a URL names, or None for a remote one.

    Follows the shell's storage layer (``mysqlshdk/libs/storage``): the scheme
    is what comes before the first ``://``, a URL without one is a local path,
    and so is one whose scheme is ``file`` in any case - with the scheme
    stripped. Every other scheme (an ``https`` PAR, say) is remote.
    """
    if not isinstance(url, str):
        return None

    position = url.find(_SCHEME_DELIMITER)
    if position < 0:
        return url
    if url[:position].lower() == "file":
        return url[position + len(_SCHEME_DELIMITER):]

    return None


def _local_paths(urls, options: dict) -> list:
    """The paths on this machine a utility reads or writes.

    The URLs are local unless an object storage option makes them prefixes in
    a bucket; the file options name local files in every case.
    """
    paths = []

    # Only a non-empty name makes a bucket, as in the shell's storage options
    # (``!m_container_name.empty()``): an empty one, or a None that is never
    # passed on, leaves the URL a local path, so it is checked.
    if not any(
        isinstance(options.get(key), str) and options[key]
        for key in _REMOTE_STORAGE_OPTIONS
    ):
        for url in urls:
            path = _local_path(url)
            if path is None:
                continue
            # a pattern of files is checked where the files are
            paths.append(
                os.path.dirname(path) if any(c in path for c in "*?[") else path
            )

    for key in _LOCAL_FILE_OPTIONS:
        path = _local_path(options.get(key))
        if path:
            paths.append(path)

    return paths


def _dump_zip_mode(output_url) -> Optional[str]:
    return "dump" if util_zip.is_zip_url(output_url) else None


def _zip_path(url, options: dict) -> str:
    """The local path of a dump in a zip, refusing a zip it cannot be.

    The zip is written or extracted next to itself, so it is a local file,
    never a URL or a name in object storage.
    """
    path = _local_path(url)
    if path is None or any(
        isinstance(options.get(key), str) and options[key] for key in _REMOTE_STORAGE_OPTIONS
    ):
        raise tool_error(
            f"A dump in a {util_zip.ZIP_SUFFIX} file must be a file on this "
            "machine, not a URL or object storage."
        )
    return os.path.abspath(path)


def register_util_tools(server, function_groups=()) -> None:
    """Registers the dump, load, copy, export and import tools.

    They need connections opened with db.connect, so they are registered only
    when the db function group is served as well.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    if not general.group_requirement_met(general.FUNCTION_GROUP_UTIL, function_groups):
        return

    import anyio.to_thread
    from mcp.server.mcpserver import Context

    tool = tool_registrar(server)

    async def start(
        ctx,
        kind: str,
        title: str,
        connection_id: str,
        function: str,
        args: list,
        options: Optional[dict],
        urls=(),
        target_connection_id: Optional[str] = None,
        result: Optional[dict] = None,
        forbidden: Optional[dict] = None,
        zip_mode: Optional[str] = None,
        extraction_budget_mb: Optional[int] = None,
    ) -> dict:
        """Starts a utility as a task, after checking what it is given.

        ``forbidden`` names the options this utility refuses beyond
        _RESERVED_OPTIONS, each with the reason it is refused.

        ``zip_mode`` is "dump" or "load" for a dump in a single
        ``.mariadb.dump.zip`` file, the last of ``args`` (see
        :mod:`mcp_plugin.lib.util_zip`): the utility then runs on a folder
        next to the zip, which is in the folder the zip's is, so that one is
        the path checked.
        """
        options = _options(options)
        # Before the paths, which can ask the user: without a shell to run
        # it in, the call fails whatever they answer.
        executable = util_process.shell_executable()

        zip_path = None
        paths = _local_paths(urls, options)
        if zip_mode is not None:
            zip_path = _zip_path(args[-1], options)
            paths = [os.path.dirname(zip_path)] + _local_paths((), options)

        for path in paths:
            await general.require_allowed_path(ctx, path)

        # Read here, while this is still the request's own context.
        client = general.get_client_identity(ctx)

        def prepare():
            target = None
            if target_connection_id is not None:
                target = db_functions.connection_data(target_connection_id, client)
            source = db_functions.connection_credentials(connection_id, client)
            plan = None
            # the command line is checked here too, so that what it cannot
            # take fails the call itself
            try:
                if zip_mode == "dump":
                    util_zip.check_dump_target(zip_path)
                    util_process.build_command(
                        function, args[:-1] + [util_zip.dump_directory(zip_path)],
                        options, target, forbidden,
                    )
                elif zip_mode == "load":
                    plan = util_zip.plan_load(
                        util_zip.DumpArchive.open(zip_path), options, extraction_budget_mb
                    )
                    util_process.build_command(
                        function, [plan.directory], plan.options, target, forbidden
                    )
                else:
                    util_process.build_command(function, args, options, target, forbidden)
            except util_zip.ZipDumpError as error:
                raise tool_error(str(error)) from error
            return source, target, plan

        # Reading the secret store is no work for the loop. Done before the
        # task starts, so that a wrong connection fails the call itself.
        source, target, plan = await anyio.to_thread.run_sync(prepare)

        task = tasks.create(
            tasks.TASK_TYPE_UTIL, kind, title, client, tasks.MAX_RUNNING_UTIL_TASKS,
            {"connection_id": connection_id},
        )

        def work(task):
            if zip_mode == "dump":
                return util_zip.dump_to_zip(task, zip_path, lambda directory: util_process.run(
                    task, function, args[:-1] + [directory], options, source, target, executable,
                ))
            if zip_mode == "load":
                return util_zip.load_from_zip(task, plan, lambda directory, run_options, abort: (
                    util_process.run(
                        task, function, [directory], run_options, source, target, executable,
                        abort=abort,
                    )
                ))
            util_process.run(task, function, args, options, source, target, executable)
            return result

        tasks.start(task, work)

        return {"task_id": task.id, "status": task.status}

    @tool(name="util.dump_instance", starts_tasks=True)
    async def dump_instance(
        ctx: Context,
        connection_id: str,
        output_url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts a dump of the whole server, as a background task.

        Runs MariaDB Shell's util.dump_instance; follow the task with
        task.get.

        Args:
            connection_id: The UUID returned by db.connect, for the server to
                dump.
            output_url: The directory to write the dump to, which must not
                exist or be empty; or a file ending in .mariadb.dump.zip,
                which must not exist, for the dump as a single ZIP file.
            options: The options of util.dump_instance, with their camelCase
                names, e.g. {"threads": 8, "users": false,
                "excludeSchemas": ["test"]}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_instance", f"Dump the instance to {output_url}",
            connection_id, "dump_instance", [output_url], options,
            urls=[output_url], result={"output_url": output_url},
            zip_mode=_dump_zip_mode(output_url),
        )

    @tool(name="util.dump_schemas", starts_tasks=True)
    async def dump_schemas(
        ctx: Context,
        connection_id: str,
        schemas: list,
        output_url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts a dump of database schemas, as a background task.

        Runs MariaDB Shell's util.dump_schemas; follow the task with
        task.get.

        Args:
            connection_id: The UUID returned by db.connect.
            schemas: The names of the schemas to dump.
            output_url: The directory to write the dump to, which must not
                exist or be empty; or a file ending in .mariadb.dump.zip,
                which must not exist, for the dump as a single ZIP file.
            options: The options of util.dump_schemas, with their camelCase
                names, e.g. {"threads": 8, "ddlOnly": true}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_schemas", f"Dump {', '.join(schemas)} to {output_url}",
            connection_id, "dump_schemas", [schemas, output_url], options,
            urls=[output_url], result={"output_url": output_url},
            forbidden=_OUTPUT_URL_PARAMETER, zip_mode=_dump_zip_mode(output_url),
        )

    @tool(name="util.dump_tables", starts_tasks=True)
    async def dump_tables(
        ctx: Context,
        connection_id: str,
        schema: str,
        tables: list,
        output_url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts a dump of tables and views of a schema, as a background task.

        Runs MariaDB Shell's util.dump_tables; follow the task with
        task.get.

        Args:
            connection_id: The UUID returned by db.connect.
            schema: The schema the tables are in.
            tables: The names of the tables and views to dump. Pass an empty
                list with {"all": true} in options to dump all of them.
            output_url: The directory to write the dump to, which must not
                exist or be empty; or a file ending in .mariadb.dump.zip,
                which must not exist, for the dump as a single ZIP file.
            options: The options of util.dump_tables, with their camelCase
                names, e.g. {"where": {"shop.orders": "id > 1000"}}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_tables", f"Dump tables of {schema} to {output_url}",
            connection_id, "dump_tables", [schema, tables, output_url], options,
            urls=[output_url], result={"output_url": output_url},
            forbidden=_OUTPUT_URL_PARAMETER, zip_mode=_dump_zip_mode(output_url),
        )

    @tool(name="util.export_table", starts_tasks=True)
    async def export_table(
        ctx: Context,
        connection_id: str,
        table: str,
        output_url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts an export of a table's rows to a CSV, TSV, JSON or other
        delimited text file, as a background task.

        Runs MariaDB Shell's util.export_table; follow the task with
        task.get. Use it instead of SELECT ... INTO OUTFILE or reading
        the rows with db.execute_sql and writing the file yourself; the
        file can be loaded again with util.import_table.

        Args:
            connection_id: The UUID returned by db.connect.
            table: The table, as table or schema.table.
            output_url: The file to write.
            options: The options of util.export_table, with their camelCase
                names, e.g. {"dialect": "csv", "where": "id > 10"}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "export_table", f"Export {table} to {output_url}",
            connection_id, "export_table", [table, output_url], options,
            urls=[output_url], result={"output_url": output_url},
        )

    @tool(name="util.load_dump", starts_tasks=True)
    async def load_dump(
        ctx: Context,
        connection_id: str,
        url: str,
        options: Optional[dict] = None,
        extraction_budget_mb: Optional[int] = None,
    ) -> dict:
        """Starts loading a dump into the server, as a background task.

        Runs MariaDB Shell's util.load_dump; follow the task with
        task.get. The server needs local_infile switched on. Only for
        dumps the util.dump_* tools made: for CSV, TSV, JSON or other data
        files use util.import_table.

        Args:
            connection_id: The UUID returned by db.connect, for the server to
                load into.
            url: The directory of the dump, or its .mariadb.dump.zip file.
                A zip is extracted next to it while it loads, and its data
                files are emptied once loaded; a failed load of it resumes
                where it stopped when started again.
            options: The options of util.load_dump, with their camelCase
                names, e.g. {"schema": "copy_of_shop", "loadUsers": false,
                "dropExistingObjects": true}.
            extraction_budget_mb: For a zip only: the most megabytes of data
                extracted and not yet loaded at once. By default 1024, or
                more for large chunks.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        zip_mode = "load" if util_zip.is_zip_url(url) else None
        if extraction_budget_mb is not None and zip_mode is None:
            raise tool_error("extraction_budget_mb is only for a dump in a .mariadb.dump.zip file.")
        return await start(
            ctx, "load_dump", f"Load the dump in {url}",
            connection_id, "load_dump", [url], options,
            urls=[url], result={"url": url},
            zip_mode=zip_mode, extraction_budget_mb=extraction_budget_mb,
        )

    @tool(name="util.inspect_dump")
    async def inspect_dump(ctx: Context, url: str, schema: Optional[str] = None) -> dict:
        """Tells what a dump holds, from its metadata, without loading it.

        Works on a dump's directory and on its .mariadb.dump.zip file alike,
        also on a dump still being written.

        Args:
            url: The directory of the dump, or its .mariadb.dump.zip file.
            schema: Only this schema, with every name of its objects. Without
                it, at most 100 names of each kind are listed, with counts.

        Returns:
            A dict with the dump's kind (dumpInstance, dumpSchemas or
            dumpTables), whether it is complete, the source server, begin and
            end, the dump's options, its users, and its schemas with their
            tables, views, functions, procedures and events; rows and data
            sizes are exact for a complete dump and estimates otherwise. Only
            a dump of a single schema can be loaded into another schema with
            util.load_dump's schema option.
        """
        path = _local_path(url)
        if path is None:
            raise tool_error("util.inspect_dump reads a dump on this machine, not a URL.")
        await general.require_allowed_path(ctx, path)

        def read():
            try:
                return util_zip.inspect_dump(path, schema)
            except util_zip.ZipDumpError as error:
                raise tool_error(str(error)) from error

        return await anyio.to_thread.run_sync(read)

    @tool(name="util.import_table", starts_tasks=True)
    async def import_table(
        ctx: Context,
        connection_id: str,
        urls: Union[str, list],
        options: Optional[dict] = None,
    ) -> dict:
        """Starts loading CSV, TSV, JSON-lines or other delimited text
        files into a table, as a background task.

        The tool for bulk-loading data files (any format LOAD DATA reads,
        local or in OCI, S3 and Azure object storage). Use it instead of
        LOAD DATA through db.execute_sql, mariadb-import, the mariadb client
        or generated INSERT statements.

        Runs MariaDB Shell's util.import_table; follow the task with
        task.get. The table must exist (create it with db.execute_sql
        first), and the server needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect.
            urls: A file, or a list of files, to import into one table. Names
                may contain the wildcards * and ?.
            options: The options of util.import_table, with their camelCase
                names, e.g. {"schema": "shop", "table": "orders",
                "dialect": "csv-unix", "skipRows": 1}. dialect: default
                (tab-separated), csv, csv-unix, csv-rfc-unix, tsv or json,
                refined by fieldsTerminatedBy, fieldsEnclosedBy and
                linesTerminatedBy. Also skipRows (header lines), columns and
                decodeColumns (map and transform file columns), characterSet
                and replaceDuplicates.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        files = [urls] if isinstance(urls, str) else list(urls)
        return await start(
            ctx, "import_table", f"Import {', '.join(files)}",
            connection_id, "import_table", [urls], options,
            urls=files, result={"urls": files},
        )

    @tool(name="util.copy_instance", starts_tasks=True)
    async def copy_instance(
        ctx: Context,
        connection_id: str,
        target_connection_id: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts copying a whole server to another one, as a background task.

        Runs MariaDB Shell's util.copy_instance, which dumps from the source
        and loads into the target at once, without files; follow the task
        with task.get. The target needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_instance, with their camelCase
                names, e.g. {"users": false, "threads": 8}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "copy_instance", "Copy the instance",
            connection_id, "copy_instance", [], options,
            target_connection_id=target_connection_id,
            result={"target_connection_id": target_connection_id},
        )

    @tool(name="util.copy_schemas", starts_tasks=True)
    async def copy_schemas(
        ctx: Context,
        connection_id: str,
        schemas: list,
        target_connection_id: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts copying schemas to another server, as a background task.

        Runs MariaDB Shell's util.copy_schemas, which dumps from the source
        and loads into the target at once, without files; follow the task
        with task.get. The target needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            schemas: The names of the schemas to copy.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_schemas, with their camelCase
                names, e.g. {"ddlOnly": true}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "copy_schemas", f"Copy {', '.join(schemas)}",
            connection_id, "copy_schemas", [schemas], options,
            target_connection_id=target_connection_id,
            result={"target_connection_id": target_connection_id},
        )

    @tool(name="util.copy_tables", starts_tasks=True)
    async def copy_tables(
        ctx: Context,
        connection_id: str,
        schema: str,
        tables: list,
        target_connection_id: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts copying tables of a schema to another server, as a background task.

        Runs MariaDB Shell's util.copy_tables, which dumps from the source
        and loads into the target at once, without files; follow the task
        with task.get. The target needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            schema: The schema the tables are in.
            tables: The names of the tables and views to copy.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_tables, with their camelCase
                names, e.g. {"schema": "renamed"} to load into another
                schema.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "copy_tables", f"Copy tables of {schema}",
            connection_id, "copy_tables", [schema, tables], options,
            target_connection_id=target_connection_id,
            result={"target_connection_id": target_connection_id},
        )
