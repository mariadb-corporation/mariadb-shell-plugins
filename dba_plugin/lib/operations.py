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

"""The dba operations, shared by the shell functions and the MCP tools.

Each takes the API key ``context`` its caller works in: the shell functions
and an MCP server started with ``--gui`` use the GUI keys, any other MCP
server the MCP keys (see :mod:`dba_plugin.lib.api_keys`). Each raises
:class:`DbaError` only, which its caller turns into its own kind of error.

Reading - vendors, offerings, objects, a status - answers at once. Deploying
and deleting take minutes, a managed service far longer, so they run as
background tasks of the shell's task manager (``mysqlsh.task_manager``, task
type ``dba``): the call checks what it can without the vendor, starts the
task and returns its id, and the caller follows it with ``task.get``. A
deployment's task ends once the object is ready to use, a deletion's once the
object is gone; cancelling one stops the waiting, not what the vendor already
started. One object is worked on by one task at a time.
"""

# cSpell:ignore mysqlsh MariaDB

import threading
import time

from mysqlsh import task_manager

from dba_plugin.lib import api_keys, object_types as types, vendor as vendor_lib, vendors
from dba_plugin.lib.general import DbaError

TASK_TYPE = "dba"
# How many dba tasks one owner may have running at once: a cluster takes a few
# VMs, volumes and a load balancer, deployed side by side.
MAX_RUNNING_TASKS = 16
# How long a task waits for the vendor, and how often it asks.
DEPLOY_TIMEOUT = 3600
DELETE_TIMEOUT = 1800
POLL_INTERVAL = 10

# The objects a task is working on, as (vendor, project, type, name or id).
_busy = set()
_busy_lock = threading.Lock()


def list_vendors(feature_set=None, context=api_keys.CONTEXT_GUI) -> list:
    """The vendors, optionally only those implementing a feature set.

    Args:
        feature_set (str): Only vendors implementing this one.
        context (str): Whose API keys to report the projects of.

    Returns:
        list: One dict per vendor: ``name``, ``display_name``,
        ``feature_sets``, ``object_types`` and ``api_key_projects``, the
        projects an API key is stored for.
    """
    wanted = types.check_feature_set(feature_set) if feature_set else None
    keys = api_keys.list_api_keys(context)

    result = []
    for name, vendor_cls in vendors.all_vendors().items():
        if wanted and wanted not in vendor_cls.feature_sets:
            continue
        result.append(
            {
                "name": name,
                "display_name": vendor_cls.display_name,
                "feature_sets": list(vendor_cls.feature_sets),
                "object_types": vendor_cls.object_types(),
                "api_key_projects": [key["project"] for key in keys if key["vendor"] == name],
            }
        )
    return result


def feature_set(vendor_name: str) -> list:
    """The feature sets a vendor implements."""
    return list(vendors.get_vendor(vendor_name).feature_sets)


def object_support(vendor_name=None) -> list:
    """The standardized object types, for one vendor or for all.

    Args:
        vendor_name (str): The vendor; None for every object type with the
            vendors that support it.

    Returns:
        list: For a vendor, what :meth:`Vendor.object_support` reports. For
        none, one dict per type: ``object_type``, ``description``,
        ``feature_set``, ``settings`` (the standardized deployment settings)
        and ``vendors``.
    """
    if vendor_name:
        return vendors.get_vendor(vendor_name).object_support()

    all_vendors = vendors.all_vendors()
    return [
        {
            "object_type": object_type,
            "description": details["description"],
            "feature_set": details["feature_set"],
            "settings": vendor_lib.settings_keys(object_type),
            "vendors": [
                name
                for name, vendor_cls in all_vendors.items()
                if object_type in vendor_cls.object_types()
            ],
        }
        for object_type, details in types.OBJECT_TYPES.items()
    ]


def set_api_key(vendor_name: str, key: str, project=None, context=api_keys.CONTEXT_GUI) -> dict:
    """Stores a vendor's API key.

    Returns:
        dict: ``vendor``, ``project`` and ``secret`` (the secret store key
        it was stored under). Never the API key.
    """
    vendor_cls = vendors.get_vendor(vendor_name)
    name = api_keys.store_api_key(vendor_cls.name, key, project, context)
    return {
        "vendor": vendor_cls.name,
        "project": name.rsplit(":", 1)[1],
        "secret": name,
    }


def open_vendor(vendor_name: str, project=None, context=api_keys.CONTEXT_GUI):
    """A vendor instance holding the API key of the project.

    Raises:
        DbaError: There is no such vendor, or no key for it.
    """
    vendor_cls = vendors.get_vendor(vendor_name)
    key = api_keys.read_api_key(
        vendor_cls.name,
        project,
        context,
        fall_back_to_default=not vendor_cls.native_projects,
    )
    if not key:
        where = (
            f"dba.set.apiKey('{vendor_cls.name}', <key>"
            + (f", '{project}'" if project else "")
            + ")"
            if context == api_keys.CONTEXT_GUI
            else f"mariadb-shell -- mcp setup --setDbaApiKey={vendor_cls.name}"
            + (f" --dbaProject={project}" if project else "")
            + " --dbaApiKeyStdin"
        )
        raise DbaError(
            f"No {vendor_cls.display_name} API key is stored"
            + (f" for project '{project}'" if project else "")
            + f". Store one with {where}."
            + (f" {vendor_cls.api_key_help}" if vendor_cls.api_key_help else "")
        )

    return vendor_cls(api_key=key, project=project)


def _with_vendor(vendor_name, project, context, call):
    vendor = open_vendor(vendor_name, project, context)
    try:
        return call(vendor)
    finally:
        vendor.close()


def object_info(vendor_name: str, object_type: str, project=None, context=api_keys.CONTEXT_GUI) -> dict:
    """What of an object type the vendor offers to deploy."""
    return _with_vendor(vendor_name, project, context, lambda v: v.object_info(object_type))


def list_objects(
    vendor_name: str, object_type: str, tags=None, project=None, context=api_keys.CONTEXT_GUI
) -> list:
    """The deployed objects of a type, filtered by tags and project."""
    return _with_vendor(
        vendor_name, project, context, lambda v: v.list_objects(object_type, tags)
    )


def object_status(
    vendor_name: str, object_type: str, object_name: str, project=None, context=api_keys.CONTEXT_GUI
) -> dict:
    """The current status of one deployed object."""
    return _with_vendor(
        vendor_name, project, context, lambda v: v.object_status(object_type, object_name)
    )


def _start_task(kind, title, owner, busy_key, vendor, work) -> dict:
    """Starts a dba task running ``work(task)`` with the vendor, and closes it after.

    Raises:
        DbaError: A task already works on the object, or the owner runs too
            many.
    """
    with _busy_lock:
        if busy_key in _busy:
            vendor.close()
            raise DbaError(
                f"A task is already working on the {busy_key[2]} '{busy_key[3]}' at "
                f"{vendor.display_name}; wait for it to end, see task.list."
            )
        try:
            task = task_manager.create(
                TASK_TYPE,
                kind,
                title,
                owner,
                MAX_RUNNING_TASKS,
                {"vendor": vendor.name, "project": busy_key[1], "object_type": busy_key[2]},
            )
        except task_manager.TaskError as e:
            vendor.close()
            raise DbaError(str(e)) from e
        _busy.add(busy_key)

    def run(task):
        try:
            return work(task)
        finally:
            with _busy_lock:
                _busy.discard(busy_key)
            vendor.close()

    task.start(run)
    return {"task_id": task.id, "status": task.status}


def _wait(task, vendor, describe, poll, done, timeout):
    """Polls until ``done(answer)``, reporting each new status; returns the last answer.

    Raises:
        DbaError: The vendor reports a failure, or ``timeout`` passed.
        mysqlsh.task_manager.TaskCancelled: The task was cancelled.
    """
    deadline = time.monotonic() + timeout
    status = None
    while True:
        answer = poll()
        if answer is not None and answer.get("status") != status:
            status = answer.get("status")
            task.message(f"{describe} is {status}.")
        if done(answer):
            return answer
        if answer is not None and vendor.has_failed(answer):
            raise DbaError(f"{vendor.display_name} reports {describe} as {status}.")
        if time.monotonic() > deadline:
            raise DbaError(
                f"{describe} is still {status} after {timeout // 60} minutes. "
                f"{vendor.display_name} may still be working on it: follow it with "
                "dba.get.vendor.objectStatus."
            )
        task.sleep(POLL_INTERVAL)


def deploy_object(
    vendor_name: str,
    object_type: str,
    settings,
    project=None,
    context=api_keys.CONTEXT_GUI,
    owner=None,
) -> dict:
    """Starts a task deploying one object, which ends once the object is ready.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type.
        settings (dict): The deployment settings; ``name`` always.
        project (str): The project; None for the default one.
        context (str): Whose API keys to use.
        owner: Who starts the task, as the task manager compares owners;
            None for the shell's own tasks.

    Returns:
        dict: ``task_id`` and ``status``. The task's result is the object,
        ready to use.
    """
    if not isinstance(settings, dict) or not settings.get("name"):
        raise DbaError("settings must be a dict naming the object: {\"name\": ...}.")

    vendor = open_vendor(vendor_name, project, context)
    try:
        vendor._operation("deploy", object_type)
    except DbaError:
        vendor.close()
        raise
    name = str(settings["name"])
    describe = f"The {object_type} '{name}'"

    def work(task):
        task.stage_started("Deploying")
        deployed = vendor.deploy_object(object_type, settings)
        task.message(
            f"{vendor.display_name} accepted the {object_type} '{name}' (id {deployed['id']})."
        )
        task.stage_finished("Deploying")

        task.stage_started("Waiting until it is ready")
        ready = _wait(
            task, vendor, describe,
            lambda: vendor.object_status(object_type, deployed["id"]),
            vendor.is_ready,
            DEPLOY_TIMEOUT,
        )
        task.stage_finished("Waiting until it is ready")
        return ready

    return _start_task(
        "deploy_object",
        f"Deploy {object_type} '{name}' on {vendor.display_name}",
        owner,
        (vendor.name, project, object_type, name),
        vendor,
        work,
    )


def delete_object(
    vendor_name: str,
    object_type: str,
    object_name: str,
    project=None,
    context=api_keys.CONTEXT_GUI,
    owner=None,
) -> dict:
    """Starts a task deleting one object, by name or id, which ends once it is gone.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type.
        object_name (str): The object's name or id.
        project (str): The project; None for the default one.
        context (str): Whose API keys to use.
        owner: Who starts the task, as the task manager compares owners;
            None for the shell's own tasks.

    Returns:
        dict: ``task_id`` and ``status``. The task's result is ``deleted``
        (the object as it was) and ``result`` (what the vendor reported).
    """
    if object_name in (None, ""):
        raise DbaError("object_name is empty.")

    vendor = open_vendor(vendor_name, project, context)
    try:
        vendor._operation("delete", object_type)
    except DbaError:
        vendor.close()
        raise
    describe = f"The {object_type} '{object_name}'"

    def gone():
        try:
            vendor.find_object(object_type, deleted["deleted"]["id"])
        except vendor_lib.ObjectNotFound:
            return None
        return {"status": "deleting"}

    deleted = {}

    def work(task):
        task.stage_started("Deleting")
        deleted.update(vendor.delete_object(object_type, object_name))
        task.stage_finished("Deleting")

        task.stage_started("Waiting until it is gone")
        _wait(task, vendor, describe, gone, lambda answer: answer is None, DELETE_TIMEOUT)
        task.message(f"{describe} is gone.")
        task.stage_finished("Waiting until it is gone")
        return dict(deleted)

    return _start_task(
        "delete_object",
        f"Delete {object_type} '{object_name}' on {vendor.display_name}",
        owner,
        (vendor.name, project, object_type, str(object_name)),
        vendor,
        work,
    )
