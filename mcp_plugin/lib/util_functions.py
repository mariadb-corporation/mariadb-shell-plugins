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

from mcp_plugin.lib import db_functions, general, tasks, util_process
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
    ) -> dict:
        """Starts a utility as a task, after checking what it is given.

        ``forbidden`` names the options this utility refuses beyond
        _RESERVED_OPTIONS, each with the reason it is refused.
        """
        options = _options(options)
        # Before the paths, which can ask the user: without a shell to run
        # it in, the call fails whatever they answer.
        executable = util_process.shell_executable()

        for path in _local_paths(urls, options):
            await general.require_allowed_path(ctx, path)

        # Read here, while this is still the request's own context.
        client = general.get_client_identity(ctx)

        def prepare():
            target = None
            if target_connection_id is not None:
                target = db_functions.connection_data(target_connection_id, client)
            source = db_functions.connection_credentials(connection_id, client)
            # the command line is checked here too, so that what it cannot
            # take fails the call itself
            util_process.build_command(function, args, options, target, forbidden)
            return source, target

        # Reading the secret store is no work for the loop. Done before the
        # task starts, so that a wrong connection fails the call itself.
        source, target = await anyio.to_thread.run_sync(prepare)

        task = tasks.create(
            tasks.TASK_TYPE_UTIL, kind, title, client, tasks.MAX_RUNNING_UTIL_TASKS,
            {"connection_id": connection_id},
        )

        def work(task):
            util_process.run(task, function, args, options, source, target, executable)
            return result

        task.start(work)

        return {"task_id": task.id, "status": task.status}

    @tool(name="util.dump_instance")
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
            output_url: The directory to write the dump to. It must not exist
                or be empty.
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
        )

    @tool(name="util.dump_schemas")
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
            output_url: The directory to write the dump to. It must not exist
                or be empty.
            options: The options of util.dump_schemas, with their camelCase
                names, e.g. {"threads": 8, "ddlOnly": true}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_schemas", f"Dump {', '.join(schemas)} to {output_url}",
            connection_id, "dump_schemas", [schemas, output_url], options,
            urls=[output_url], result={"output_url": output_url},
            forbidden=_OUTPUT_URL_PARAMETER,
        )

    @tool(name="util.dump_tables")
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
            output_url: The directory to write the dump to. It must not exist
                or be empty.
            options: The options of util.dump_tables, with their camelCase
                names, e.g. {"where": {"shop.orders": "id > 1000"}}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_tables", f"Dump tables of {schema} to {output_url}",
            connection_id, "dump_tables", [schema, tables, output_url], options,
            urls=[output_url], result={"output_url": output_url},
            forbidden=_OUTPUT_URL_PARAMETER,
        )

    @tool(name="util.export_table")
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

    @tool(name="util.load_dump")
    async def load_dump(
        ctx: Context,
        connection_id: str,
        url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts loading a dump into the server, as a background task.

        Runs MariaDB Shell's util.load_dump; follow the task with
        task.get. The server needs local_infile switched on. Only for
        dumps the util.dump_* tools made: for CSV, TSV, JSON or other data
        files use util.import_table.

        Args:
            connection_id: The UUID returned by db.connect, for the server to
                load into.
            url: The directory of the dump.
            options: The options of util.load_dump, with their camelCase
                names, e.g. {"schema": "copy_of_shop", "loadUsers": false,
                "dropExistingObjects": true}.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "load_dump", f"Load the dump in {url}",
            connection_id, "load_dump", [url], options,
            urls=[url], result={"url": url},
        )

    @tool(name="util.import_table")
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

    @tool(name="util.copy_instance")
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

    @tool(name="util.copy_schemas")
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

    @tool(name="util.copy_tables")
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
