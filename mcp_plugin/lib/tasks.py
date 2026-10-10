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

"""Background tasks: work that outlives the tool call that started it.

A dump or a load can run for hours, far longer than an MCP client waits for a
tool call to return, so the ``util`` tools start one as a task and return its
id at once. The client then follows it with ``util.get_task``, which returns
the task's state, the progress of its current stage and the messages it
printed since the last call, and can stop it with ``util.cancel_task``.

A task runs on a thread of its own and reports through the shell utility's
``progressCallback`` option: every event updates the task under its lock and
wakes the callers waiting for a change. Cancelling only sets a flag; the next
event returns "cancel" to the utility, which then stops as it does on ^C.

A task belongs to the client that started it, exactly as a connection does:
another client is told it does not exist. Finished tasks are kept for
:data:`FINISHED_TASK_LIFETIME` seconds and at most :data:`MAX_FINISHED_TASKS`
of them, the oldest dropped first, so that a client that comes back late can
still read how its task ended.
"""
# cSpell:ignore mysqlsh

import collections
import threading
import time
import uuid

from mcp_plugin.lib import general
from mcp_plugin.lib.tool_registrar import tool_error

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
FINISHED_STATES = (COMPLETED, FAILED, CANCELLED)

# How long a finished task is kept, and how many are, at most.
FINISHED_TASK_LIFETIME = 3600
MAX_FINISHED_TASKS = 100
# How many tasks one client may have running at once. A dump opens a session
# per thread, so a client starting many at once would take every connection
# the server allows.
MAX_RUNNING_TASKS_PER_CLIENT = 4
# The messages kept per task; older ones are dropped, and the snapshot says how
# many were.
MAX_MESSAGES = 2000
# The longest util.get_task waits for a change.
MAX_WAIT_MS = 30000

_tasks = {}
_tasks_lock = threading.Lock()


class Task:
    """One background task and everything a client can learn about it."""

    def __init__(self, kind: str, title: str, client, connection_id=None):
        self.id = str(uuid.uuid4())
        self.kind = kind
        self.title = title
        self.client = general.normalize_client_identity(client)
        self.connection_id = connection_id
        self.status = PENDING
        self.created_at = general.utc_timestamp()
        self.started_at = None
        self.finished_at = None
        self.finished_monotonic = None
        # the stages in the order they started, each with its state
        self.stages = []
        self.stage = None
        self.progress = None
        self.messages = collections.deque(maxlen=MAX_MESSAGES)
        self.message_count = 0
        self.result = None
        self.error = None
        self.cancel_requested = False
        # bumped on every change, so a waiting caller can tell one happened
        self.version = 0
        self.changed = threading.Condition()

    # --- reported by the running utility ---------------------------------

    def on_event(self, event) -> object:
        """The utility's progressCallback: records one event.

        Args:
            event (dict): The event, as the shell hands it over.

        Returns:
            "cancel" once the task is to stop, else None.
        """
        event = dict(event)
        kind = event.get("type")

        with self.changed:
            if kind == "message":
                self._add_message(event.get("level", "info"), event.get("text", ""))
            elif kind == "stageStarted":
                self.stage = event.get("stage")
                self.stages.append({"name": self.stage, "status": RUNNING, "seconds": None})
                self.progress = None
            elif kind == "progress":
                self.stage = event.get("stage", self.stage)
                self.progress = _progress_of(event)
            elif kind == "stageFinished":
                for stage in reversed(self.stages):
                    if stage["name"] == event.get("stage") and stage["status"] == RUNNING:
                        stage["status"] = COMPLETED
                        stage["seconds"] = event.get("seconds")
                        break

            self._touch()
            return "cancel" if self.cancel_requested else None

    def _add_message(self, level: str, text: str) -> None:
        self.message_count += 1
        self.messages.append(
            {
                "seq": self.message_count,
                "time": general.utc_timestamp(),
                "level": level,
                "text": text,
            }
        )

    def _touch(self) -> None:
        self.version += 1
        self.changed.notify_all()

    # --- lifecycle -------------------------------------------------------

    def start(self, work) -> None:
        """Runs the work on a thread of its own.

        Args:
            work: A function taking the progress callback and returning the
                task's result, or raising to fail it.
        """
        thread = threading.Thread(
            target=self._run, args=(work,), name=f"mcp-task-{self.id}", daemon=True
        )
        with self.changed:
            self.status = RUNNING
            self.started_at = general.utc_timestamp()
            self._touch()
        thread.start()

    def _run(self, work) -> None:
        try:
            result = work(self.on_event)
        except Exception as error:  # noqa: BLE001 - reported to the client
            self._finish(CANCELLED if self.cancel_requested else FAILED, error=str(error).strip())
        else:
            self._finish(COMPLETED, result=result)

    def _finish(self, status: str, result=None, error=None) -> None:
        with self.changed:
            self.status = status
            self.result = result
            self.error = error
            self.finished_at = general.utc_timestamp()
            self.finished_monotonic = time.monotonic()
            # a stage still running ends as the task did
            for stage in self.stages:
                if stage["status"] == RUNNING:
                    stage["status"] = status
            if status == CANCELLED:
                self._add_message("info", "The task was cancelled.")
            elif status == FAILED and error:
                self._add_message("error", error)
            self._touch()

    def cancel(self) -> None:
        """Asks the running utility to stop, at its next event."""
        with self.changed:
            if self.status in FINISHED_STATES:
                return
            self.cancel_requested = True
            self._touch()

    # --- read by clients -------------------------------------------------

    def snapshot(self, since: int = 0) -> dict:
        """The task's state, with the messages after ``since``.

        Args:
            since (int): The ``seq`` of the last message the caller has.

        Returns:
            dict: The snapshot.
        """
        with self.changed:
            messages = [m for m in self.messages if m["seq"] > since]
            first_kept = self.messages[0]["seq"] if self.messages else self.message_count + 1

            return {
                "task_id": self.id,
                "kind": self.kind,
                "title": self.title,
                "connection_id": self.connection_id,
                "status": self.status,
                "cancel_requested": self.cancel_requested,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "stage": self.stage,
                "progress": dict(self.progress) if self.progress else None,
                "stages": [dict(stage) for stage in self.stages],
                "messages": messages,
                # messages that were dropped before the caller could read them
                "messages_dropped": max(0, first_kept - since - 1),
                "next_since": self.message_count,
                "result": self.result,
                "error": self.error,
            }

    def wait_for_change(self, version: int, timeout: float) -> None:
        """Waits until the task changed after ``version``, or it finished.

        Args:
            version (int): The version the caller has seen.
            timeout (float): The longest to wait, in seconds.
        """
        deadline = time.monotonic() + timeout
        with self.changed:
            while self.version == version and self.status not in FINISHED_STATES:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                self.changed.wait(left)


def _progress_of(event: dict) -> dict:
    """The progress of a stage as the client gets it, with a percentage."""
    current = event.get("current", 0)
    total = event.get("total", 0)
    progress = {"current": current, "total": total}

    for key, name in (
        ("throughput", "throughput"),
        ("etaSeconds", "eta_seconds"),
        ("items", "items"),
        ("totalIsApproximate", "total_is_approximate"),
        ("totalKnown", "total_known"),
    ):
        if key in event:
            progress[name] = event[key]

    known = event.get("totalKnown", True)
    progress["percent"] = (
        min(100.0, round(current * 100.0 / total, 1)) if known and total else None
    )
    return progress


def _prune() -> None:
    """Drops finished tasks past their lifetime, and the oldest beyond the cap.

    Called with :data:`_tasks_lock` held.
    """
    now = time.monotonic()
    finished = sorted(
        (task for task in _tasks.values() if task.finished_monotonic is not None),
        key=lambda task: task.finished_monotonic,
    )
    for index, task in enumerate(finished):
        expired = now - task.finished_monotonic > FINISHED_TASK_LIFETIME
        too_many = len(finished) - index > MAX_FINISHED_TASKS
        if expired or too_many:
            _tasks.pop(task.id, None)


def create(kind: str, title: str, client, connection_id=None) -> Task:
    """Registers a new task for a client, refusing one too many.

    Args:
        kind (str): What the task does, e.g. "dump_schemas".
        title (str): A line describing it, for a list of tasks.
        client (ClientIdentity): The client starting it.
        connection_id (str): The connection it works on, if any.

    Returns:
        Task: The task, not started yet.
    """
    task = Task(kind, title, client, connection_id)

    with _tasks_lock:
        _prune()
        running = sum(
            1
            for other in _tasks.values()
            if other.client == task.client and other.status not in FINISHED_STATES
        )
        if running >= MAX_RUNNING_TASKS_PER_CLIENT:
            raise tool_error(
                f"Already {running} tasks are running; wait for one to finish "
                "or cancel one with util.cancel_task before starting another."
            )
        _tasks[task.id] = task

    return task


def get(task_id: str, client) -> Task:
    """Looks a task up for a client.

    A task of another client is reported exactly like one that does not
    exist, as a connection is.

    Args:
        task_id (str): The id util tools returned.
        client (ClientIdentity): The requesting client.

    Returns:
        Task: The task.
    """
    client = general.normalize_client_identity(client)

    with _tasks_lock:
        _prune()
        task = _tasks.get(task_id)

    if task is None or task.client != client:
        raise tool_error(
            f"There is no task with the id '{task_id}'. Use util.list_tasks to "
            "see the tasks of this client."
        )

    return task


def list_for(client) -> list:
    """The tasks of a client, oldest first.

    Args:
        client (ClientIdentity): The requesting client.

    Returns:
        list: The tasks.
    """
    client = general.normalize_client_identity(client)

    with _tasks_lock:
        _prune()
        return [task for task in _tasks.values() if task.client == client]


def clear() -> None:
    """Forgets every task. For tests only: running ones keep running."""
    with _tasks_lock:
        _tasks.clear()
