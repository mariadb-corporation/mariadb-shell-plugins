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
tool starts it and returns its id at once, and ``util.get_task`` follows it.
A task works on a session of its own, opened on the connection a client got
from ``db.connect``: on the connection's own session it would hold that
connection's lock for as long as it runs, and every other tool call on it
would wait. The utility is given that session and a progress callback through
its ``session`` and ``progressCallback`` options, so it neither touches the
shell's global session nor prints anything.

Paths are authorized with :func:`mcp_plugin.lib.general.require_allowed_path`;
a URL, or a prefix in object storage, is no path on this machine and is left
to the utility.

The group is never served in multi-tenant mode: the files are on the server's
disk, and a dump takes connections and threads from every other user.
"""
# cSpell:ignore mysqlsh MariaDB mcpserver

import os
from typing import Optional, Union

import mysqlsh

from mcp_plugin.lib import db_functions, general, tasks
from mcp_plugin.lib.tool_registrar import tool_error, tool_registrar

# The options the tools set themselves.
_RESERVED_OPTIONS = ("session", "progressCallback", "showProgress")

# Options that make the URL a prefix in object storage instead of a path.
_REMOTE_STORAGE_OPTIONS = ("s3BucketName", "osBucketName", "azureContainerName")


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


def _local_paths(urls, options: dict) -> list:
    """The paths on this machine among the URLs a utility reads or writes."""
    if any(key in options for key in _REMOTE_STORAGE_OPTIONS):
        return []

    paths = []
    for url in urls:
        if "://" in url:
            continue
        # a pattern of files is checked where the files are
        paths.append(os.path.dirname(url) if any(c in url for c in "*?[") else url)

    return paths


def _run_utility(function: str, args: list, options: dict, session, callback):
    """Runs a util function on a session, reporting to a callback."""
    try:
        getattr(mysqlsh.globals.util, function)(
            *args, dict(options, session=session, progressCallback=callback)
        )
    except Exception as error:
        text = str(error)
        if "Invalid options" in text and "progressCallback" in text:
            raise RuntimeError(
                "This MariaDB Shell is too old for the util tools: its "
                "utilities have no 'session' and 'progressCallback' options. "
                "Update MariaDB Shell."
            ) from error
        raise
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - the work is over either way
            pass


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
    if general.FUNCTION_GROUP_DB not in function_groups:
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
    ) -> dict:
        """Starts a utility as a task, after checking what it is given."""
        options = _options(options)

        for path in _local_paths(urls, options):
            await general.require_allowed_path(ctx, path)

        # Read here, while this is still the request's own context.
        client = general.get_client_identity(ctx)

        def prepare():
            target = None
            if target_connection_id is not None:
                target = db_functions.connection_data(target_connection_id, client)
            return db_functions.open_separate_session(connection_id, client), target

        # Opening a session is a round trip to the server: not on the loop.
        # Done before the task starts, so that a wrong connection fails the
        # call itself.
        session, target = await anyio.to_thread.run_sync(prepare)

        try:
            task = tasks.create(kind, title, client, connection_id)
        except Exception:
            session.close()
            raise

        call_args = list(args) + ([target] if target is not None else [])

        def work(callback):
            _run_utility(function, call_args, options, session, callback)
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

        Runs MariaDB Shell's util.dump_instance on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.

        Args:
            connection_id: The UUID returned by db.connect, for the server to
                dump.
            output_url: The directory to write the dump to. It must not exist
                or be empty.
            options: The options of util.dump_instance, with their camelCase
                names, e.g. {"threads": 8, "users": false,
                "excludeSchemas": ["test"]}. session, progressCallback and
                showProgress are set by the tool.

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

        Runs MariaDB Shell's util.dump_schemas on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.

        Args:
            connection_id: The UUID returned by db.connect.
            schemas: The names of the schemas to dump.
            output_url: The directory to write the dump to. It must not exist
                or be empty.
            options: The options of util.dump_schemas, with their camelCase
                names, e.g. {"threads": 8, "ddlOnly": true}. session,
                progressCallback and showProgress are set by the tool.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_schemas", f"Dump {', '.join(schemas)} to {output_url}",
            connection_id, "dump_schemas", [schemas, output_url], options,
            urls=[output_url], result={"output_url": output_url},
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

        Runs MariaDB Shell's util.dump_tables on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.

        Args:
            connection_id: The UUID returned by db.connect.
            schema: The schema the tables are in.
            tables: The names of the tables and views to dump. Pass an empty
                list with {"all": true} in options to dump all of them.
            output_url: The directory to write the dump to. It must not exist
                or be empty.
            options: The options of util.dump_tables, with their camelCase
                names, e.g. {"where": {"shop.orders": "id > 1000"}}. session,
                progressCallback and showProgress are set by the tool.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "dump_tables", f"Dump tables of {schema} to {output_url}",
            connection_id, "dump_tables", [schema, tables, output_url], options,
            urls=[output_url], result={"output_url": output_url},
        )

    @tool(name="util.export_table")
    async def export_table(
        ctx: Context,
        connection_id: str,
        table: str,
        output_url: str,
        options: Optional[dict] = None,
    ) -> dict:
        """Starts an export of a table's rows to a file, as a background task.

        Runs MariaDB Shell's util.export_table on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.

        Args:
            connection_id: The UUID returned by db.connect.
            table: The table, as table or schema.table.
            output_url: The file to write.
            options: The options of util.export_table, with their camelCase
                names, e.g. {"dialect": "csv", "where": "id > 10"}. session,
                progressCallback and showProgress are set by the tool.

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

        Runs MariaDB Shell's util.load_dump on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.
        The server needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the server to
                load into.
            url: The directory of a dump made with util.dump_instance,
                util.dump_schemas or util.dump_tables.
            options: The options of util.load_dump, with their camelCase
                names, e.g. {"schema": "copy_of_shop", "loadUsers": false,
                "dropExistingObjects": true}. session, progressCallback and
                showProgress are set by the tool.

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
        """Starts importing files into a table, as a background task.

        Runs MariaDB Shell's util.import_table on a session of its own on the
        connection, and returns at once. Follow the task with util.get_task.
        The table must exist, and the server needs local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect.
            urls: A file, or a list of files, to import. Names may contain the
                wildcards * and ?.
            options: The options of util.import_table, with their camelCase
                names, e.g. {"schema": "shop", "table": "orders",
                "dialect": "csv-unix", "skipRows": 1}. session,
                progressCallback and showProgress are set by the tool.

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
        and loads into the target at the same time without writing files, and
        returns at once. Follow the task with util.get_task. The target needs
        local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_instance, with their camelCase
                names, e.g. {"users": false, "threads": 8}. session,
                progressCallback and showProgress are set by the tool.

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

        Runs MariaDB Shell's util.copy_schemas, which dumps from the source and
        loads into the target at the same time without writing files, and
        returns at once. Follow the task with util.get_task. The target needs
        local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            schemas: The names of the schemas to copy.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_schemas, with their camelCase
                names, e.g. {"ddlOnly": true}. session, progressCallback and
                showProgress are set by the tool.

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

        Runs MariaDB Shell's util.copy_tables, which dumps from the source and
        loads into the target at the same time without writing files, and
        returns at once. Follow the task with util.get_task. The target needs
        local_infile switched on.

        Args:
            connection_id: The UUID returned by db.connect, for the source.
            schema: The schema the tables are in.
            tables: The names of the tables and views to copy.
            target_connection_id: The UUID returned by db.connect, for the
                target. It must be another server.
            options: The options of util.copy_tables, with their camelCase
                names, e.g. {"schema": "renamed"} to load into another
                schema. session, progressCallback and showProgress are set by
                the tool.

        Returns:
            A dict with the task_id to follow and the task's status.
        """
        return await start(
            ctx, "copy_tables", f"Copy tables of {schema}",
            connection_id, "copy_tables", [schema, tables], options,
            target_connection_id=target_connection_id,
            result={"target_connection_id": target_connection_id},
        )

    @tool(name="util.get_task")
    def get_task(
        ctx: Context,
        task_id: str,
        since: int = 0,
        wait_ms: int = 0,
    ) -> dict:
        """Returns the state of a task started by a util tool.

        Args:
            task_id: The task_id a util tool returned.
            since: The next_since of the previous call, to get only the
                messages printed after it. 0 gets all that are kept.
            wait_ms: Wait up to this many milliseconds (at most 30000) for
                something to change before answering, instead of answering at
                once. Ends early when the task finishes.

        Returns:
            A dict with the task's status (pending, running, completed, failed
            or cancelled), its kind and title, its stages with their state,
            the current stage and its progress (current, total, percent,
            throughput, eta_seconds and items where the stage measures them),
            the messages after since (each with seq, time, level and text),
            next_since for the next call, and, once it ended, its result or
            error.
        """
        client = general.get_client_identity(ctx)
        task = tasks.get(task_id, client)

        snapshot = task.snapshot(since)
        if wait_ms > 0 and not snapshot["messages"] and snapshot["status"] not in tasks.FINISHED_STATES:
            version = task.version
            task.wait_for_change(version, min(wait_ms, tasks.MAX_WAIT_MS) / 1000.0)
            snapshot = task.snapshot(since)

        return snapshot

    @tool(name="util.list_tasks")
    def list_tasks(ctx: Context) -> list:
        """Lists the tasks this client started that are still kept.

        Finished tasks are kept for an hour.

        Returns:
            A list with one dict per task, as util.get_task returns it but
            without its messages.
        """
        client = general.get_client_identity(ctx)
        listed = []
        for task in tasks.list_for(client):
            snapshot = task.snapshot(task.message_count)
            snapshot.pop("messages")
            snapshot.pop("messages_dropped")
            listed.append(snapshot)

        return listed

    @tool(name="util.cancel_task")
    def cancel_task(ctx: Context, task_id: str) -> dict:
        """Stops a running task.

        The utility stops at its next progress update, as it does on Ctrl+C,
        and the task then ends as cancelled. A load that is stopped can be
        resumed by loading the same dump again.

        Args:
            task_id: The task_id a util tool returned.

        Returns:
            The task's state, as util.get_task returns it, without messages.
        """
        client = general.get_client_identity(ctx)
        task = tasks.get(task_id, client)
        task.cancel()

        snapshot = task.snapshot(task.message_count)
        snapshot.pop("messages")
        snapshot.pop("messages_dropped")

        return snapshot
