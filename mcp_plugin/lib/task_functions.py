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

"""MCP tools following the background tasks other tools start.

``task.get``, ``task.list`` and ``task.cancel`` follow every task a client
started, whichever group's tool started it: a dump or load of the util group,
a deployment of the dba group. They are registered with the first group
served that starts tasks (see ``general.TASK_FUNCTION_GROUPS``), not as a
group of their own: a client that can start a task can always follow it.
"""
# cSpell:ignore mysqlsh mcpserver

from typing import Optional

from mcp_plugin.lib import general, tasks
from mcp_plugin.lib.tool_registrar import tool_registrar


def register_task_tools(server) -> None:
    """Registers the task tools on the given server.

    Args:
        server: The MCPServer instance to register the tools on.

    Returns:
        None
    """
    from mcp.server.mcpserver import Context

    tool = tool_registrar(server)

    @tool(name="task.get")
    def get_task(
        ctx: Context,
        task_id: str,
        since: int = 0,
        wait_ms: int = 0,
    ) -> dict:
        """Returns the state of a background task this client started.

        Args:
            task_id: The task_id the tool that started the task returned.
            since: The next_since of the previous call, to get only the
                messages reported after it. 0 gets all that are kept.
            wait_ms: Wait up to this many milliseconds (at most 30000) for
                something to change before answering, instead of answering at
                once. Ends early when the task finishes.

        Returns:
            A dict with the task's status (pending, running, completed, failed
            or cancelled), its task_type (util, dba), kind and title, its
            stages with their state, the current stage and its progress
            (current, total, percent, and throughput, eta_seconds and items
            where the stage measures them), the messages after since (each
            with seq, time, level and text), next_since for the next call,
            and, once it ended, its result or error.
        """
        task = tasks.get(task_id, general.get_client_identity(ctx))

        if wait_ms > 0:
            return task.wait_for_news(since, min(wait_ms, tasks.MAX_WAIT_MS) / 1000.0)

        return task.snapshot(since)

    @tool(name="task.list")
    def list_tasks(ctx: Context, task_type: Optional[str] = None) -> list:
        """Lists the background tasks this client started that are still kept.

        Finished tasks are kept for an hour.

        Args:
            task_type: Only the tasks of this type: util (dumps, loads,
                copies, exports, imports) or dba (cloud deployments). All
                when not given.

        Returns:
            A list with one dict per task, oldest first, as task.get returns
            it but without its messages.
        """
        return [
            task.summary()
            for task in tasks.list_for(general.get_client_identity(ctx), task_type or None)
        ]

    @tool(name="task.cancel")
    def cancel_task(ctx: Context, task_id: str) -> dict:
        """Stops a running background task this client started.

        A util task's utility is interrupted as by Ctrl+C; a load that is
        stopped can be resumed by loading the same dump again. A dba task
        stops waiting for the vendor: what the vendor already started, such
        as building a server, goes on there and has to be deleted on its own.
        The task then ends as cancelled. Cancelling a finished task does
        nothing.

        Args:
            task_id: The task_id the tool that started the task returned.

        Returns:
            The task's state, as task.get returns it, without messages.
        """
        task = tasks.get(task_id, general.get_client_identity(ctx))
        task.cancel()

        return task.summary()
