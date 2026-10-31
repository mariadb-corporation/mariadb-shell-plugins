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

"""Background tasks of MCP clients: the shell's task manager, owned per client.

A dump or a load can run for hours, far longer than an MCP client waits for a
tool call to return, so the tools that start one return a task id at once,
and the client follows the task with ``task.get``, lists its tasks with
``task.list`` and stops one with ``task.cancel`` (see
:mod:`mcp_plugin.lib.task_functions`).

The tasks themselves are the shell's (``mysqlsh.task_manager``), shared with
every plugin that starts one - the dba plugin's deployments among them - so
that one ``task.list`` shows them all, each with its ``task_type``. What this
module adds is the MCP side of it: a task belongs to the client that started
it, exactly as a connection does, and another client is told it does not
exist; and the task manager's refusals become tool errors.
"""
# cSpell:ignore mysqlsh

from mysqlsh import task_manager
from mysqlsh.task_manager import (  # noqa: F401 - the states, for callers
    CANCELLED,
    COMPLETED,
    FAILED,
    FINISHED_STATES,
    MAX_WAIT_MS,
    PENDING,
    RUNNING,
    TaskError,
)

from mcp_plugin.lib import general
from mcp_plugin.lib.tool_registrar import tool_error

TASK_TYPE_UTIL = "util"

# How many util tasks one client may have running at once. A dump opens a
# session per thread, so a client starting many at once would take every
# connection the server allows.
MAX_RUNNING_UTIL_TASKS = 4


def owner(client):
    """The task owner a client is: its identity, normalized for comparing."""
    return general.normalize_client_identity(client)


def create(task_type: str, kind: str, title: str, client, max_running: int, attributes=None):
    """Registers a new task for a client, refusing one too many.

    Args:
        task_type (str): The kind of plugin work, e.g. "util" or "dba".
        kind (str): What the task does, e.g. "dump_schemas".
        title (str): A line describing it, for a list of tasks.
        client (ClientIdentity): The client starting it.
        max_running (int): How many tasks of the type the client may have
            running at once, this one included.
        attributes (dict): More to report with the task, e.g. the
            connection it works on.

    Returns:
        mysqlsh.task_manager.Task: The task, not started yet.
    """
    try:
        return task_manager.create(
            task_type, kind, title, owner(client), max_running, attributes
        )
    except TaskError as e:
        raise tool_error(str(e)) from e


def start(task, work) -> None:
    """Starts a task's work on its own thread, refusing as a tool error where it cannot.

    The task manager ends a task it could not start, so it is not left
    running.
    """
    try:
        task.start(work)
    except TaskError as e:
        raise tool_error(str(e)) from e


def get(task_id: str, client):
    """Looks a task up for a client.

    A task of another client is reported exactly like one that does not
    exist, as a connection is.

    Args:
        task_id (str): The id the tool that started the task returned.
        client (ClientIdentity): The requesting client.

    Returns:
        mysqlsh.task_manager.Task: The task.
    """
    try:
        return task_manager.get(task_id, owner(client))
    except TaskError as e:
        raise tool_error(str(e)) from e


def list_for(client, task_type=None) -> list:
    """The tasks of a client, oldest first.

    Args:
        client (ClientIdentity): The requesting client.
        task_type (str): Only the tasks of this type; None for all.

    Returns:
        list: The tasks.
    """
    return task_manager.list_tasks(owner(client), task_type)


def clear() -> None:
    """Forgets every task. For tests only: running ones keep running."""
    task_manager.clear()
