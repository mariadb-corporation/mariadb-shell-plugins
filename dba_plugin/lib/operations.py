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

Reading - vendors, offerings, objects, a status - answers at once. Creating
and deleting take minutes, a managed service far longer, so they run as
background tasks of the shell's task manager (``mysqlsh.task_manager``, task
type ``dba``): the call checks what it can without the vendor, starts the
task and returns its id, and the caller follows it with ``task.get``. A
create task ends once the object is ready to use, a deletion's once the
object is gone; cancelling one stops the waiting, not what the vendor already
started. One object is worked on by one task at a time.
"""

# cSpell:ignore mysqlsh MariaDB

import atexit
import threading
import time

from mysqlsh import task_manager

from dba_plugin.lib import api_keys, object_types as types, vendor as vendor_lib, vendors
from dba_plugin.lib.general import DbaError

TASK_TYPE = "dba"
# How many dba tasks one owner may have running at once: a cluster takes a few
# VMs, volumes and a load balancer, created side by side.
MAX_RUNNING_TASKS = 16
# How long a task waits for the vendor, and how often it asks.
CREATE_TIMEOUT = 3600
DELETE_TIMEOUT = 1800
POLL_INTERVAL = 10
# How many asks in a row may fail - an API that answers 502 for a moment, an
# object not listed yet right after it was created - before a wait gives up.
MAX_POLL_ERRORS = 6

# The objects tasks are working on, as (vendor, project, type, name or id),
# each with the claim of the task working on it.
_busy = {}
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
        ``feature_set``, ``settings`` (the standardized settings to create objects with)
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


def _api_key(vendor_cls, project, context) -> str:
    """The API key of the project, or a refusal saying how to store one.

    Raises:
        DbaError: No key is stored.
    """
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
    return key


def open_vendor(vendor_name: str, project=None, context=api_keys.CONTEXT_GUI):
    """A new vendor instance holding the API key of the project; the caller closes it.

    Raises:
        DbaError: There is no such vendor, or no key for it.
    """
    vendor_cls = vendors.get_vendor(vendor_name)
    return vendor_cls(api_key=_api_key(vendor_cls, project, context), project=project)


# The vendors the reading operations share, one per vendor, project, key
# context and API key: their HTTP clients keep connections open between
# calls, which an MCP server answering one call after the other - a GUI
# refreshing, an agent polling a status - would otherwise open anew each time.
_shared = {}
_shared_lock = threading.Lock()


def _shared_vendor(vendor_name, project, context):
    """The open vendor for the project's current API key.

    A key stored anew gets a vendor of its own, and the one of the key it
    replaced is closed.
    """
    vendor_cls = vendors.get_vendor(vendor_name)
    key = _api_key(vendor_cls, project, context)
    wanted = (vendor_cls.name, project, context, key)
    with _shared_lock:
        vendor = _shared.get(wanted)
        if vendor is None:
            for stale in [k for k in _shared if k[:3] == wanted[:3]]:
                _shared.pop(stale).close()
            vendor = vendor_cls(api_key=key, project=project)
            # made here, under the lock: threads sharing the vendor share one client
            vendor.client  # noqa: B018 - creates it
            _shared[wanted] = vendor
    return vendor


def close_vendors() -> None:
    """Closes the vendors the reading operations share."""
    with _shared_lock:
        while _shared:
            _shared.popitem()[1].close()


atexit.register(close_vendors)


def _with_vendor(vendor_name, project, context, call):
    return call(_shared_vendor(vendor_name, project, context))


def object_info(vendor_name: str, object_type: str, project=None, context=api_keys.CONTEXT_GUI) -> dict:
    """What of an object type the vendor offers to create."""
    return _with_vendor(vendor_name, project, context, lambda v: v.object_info(object_type))


def settings_info(vendor_name: str, object_type: str, project=None, context=api_keys.CONTEXT_GUI) -> dict:
    """The settings to create objects of a type with at a vendor, described for a dialog.

    See ``Vendor.settings_info``.
    """
    return _with_vendor(vendor_name, project, context, lambda v: v.settings_info(object_type))


def validate_settings(
    vendor_name: str, object_type: str, settings, project=None, context=api_keys.CONTEXT_GUI
) -> dict:
    """Checks the settings to create an object with, against what the vendor offers too.

    See ``Vendor.validate_settings``. A setting that is not valid is
    reported, not raised.
    """
    return _with_vendor(
        vendor_name, project, context, lambda v: v.validate_settings(object_type, settings)
    )


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


def _busy_key(vendor, project, object_type, name_or_id) -> tuple:
    return (vendor.name, project or api_keys.DEFAULT_PROJECT, object_type, str(name_or_id))


def _claim(claim, vendor, keys) -> None:
    """Marks objects as worked on under a claim, refusing one another claim holds.

    Raises:
        DbaError: Another task works on one of them.
    """
    with _busy_lock:
        for key in keys:
            if _busy.get(key, claim) is not claim:
                raise DbaError(
                    f"A task is already working on the {key[2]} '{key[3]}' at "
                    f"{vendor.display_name}; wait for it to end, see task.list."
                )
        for key in keys:
            _busy[key] = claim


def _release(claim) -> None:
    with _busy_lock:
        for key in [key for key, holder in _busy.items() if holder is claim]:
            del _busy[key]


def _open_for(operation, vendor_name, object_type, project, context):
    """A vendor with the API key, that supports the operation for the type.

    Raises:
        DbaError: There is no such vendor, no key, or no such support.
    """
    vendor = open_vendor(vendor_name, project, context)
    try:
        vendor.require(operation, object_type)
    except DbaError:
        vendor.close()
        raise
    return vendor


def _start_task(kind, title, owner, vendor, project, object_type, object_name, work) -> dict:
    """Starts a dba task running ``work(task, claim)``, which owns the vendor.

    The object, by the name or id given, is claimed for the task, and ``claim``
    claims it by more of them - its id once created, its name once found -
    so that no other task works on it meanwhile. The claims are released and
    the vendor closed when the task ends, or fails to start.

    Raises:
        DbaError: A task already works on the object, the owner runs too
            many, or the task cannot start.
    """
    claim = object()
    try:
        _claim(claim, vendor, [_busy_key(vendor, project, object_type, object_name)])
        task = task_manager.create(
            TASK_TYPE,
            kind,
            title,
            owner,
            MAX_RUNNING_TASKS,
            {"vendor": vendor.name, "project": project, "object_type": object_type},
        )
    except (DbaError, task_manager.TaskError) as e:
        _release(claim)
        vendor.close()
        raise DbaError(str(e)) from e

    def run(task):
        try:
            return work(task, lambda *names: _claim(
                claim, vendor, [_busy_key(vendor, project, object_type, n) for n in names]
            ))
        finally:
            _release(claim)
            vendor.close()

    try:
        task.start(run)
    except BaseException as e:
        # no thread to be had, or a Ctrl+C meanwhile: the task manager ended
        # the task, and the work, which would let go of all this, never runs
        _release(claim)
        vendor.close()
        if isinstance(e, task_manager.TaskError):
            raise DbaError(str(e)) from e
        raise
    return {"task_id": task.id, "status": task.status}


def _wait(task, vendor, describe, poll, done, timeout):
    """Polls until ``done(answer)``, reporting each new status; returns the last answer.

    A poll that fails is tried again, :data:`MAX_POLL_ERRORS` times in a row
    at most: one failed request in an hour of asking must not fail a
    creation the vendor goes on with.

    Raises:
        DbaError: The vendor reports a failure, the polls keep failing, or
            ``timeout`` passed.
        mysqlsh.task_manager.TaskCancelled: The task was cancelled.
    """
    deadline = time.monotonic() + timeout
    status = None
    errors = 0
    while True:
        try:
            answer = poll()
        except DbaError as e:
            errors += 1
            if errors > MAX_POLL_ERRORS:
                raise
            task.message(f"Could not get the status: {e} Asking again.", "warning")
        else:
            errors = 0
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


def create_object(
    vendor_name: str,
    object_type: str,
    settings,
    project=None,
    context=api_keys.CONTEXT_GUI,
    owner=None,
) -> dict:
    """Starts a task creating one object, which ends once the object is ready.

    The settings are checked at once, as far as the vendor's rules need no
    request (``Vendor.check_create_settings``); what only the vendor's API
    refuses - a size it does not offer - fails the task.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type.
        settings (dict): The settings to create it with; ``name`` always.
        project (str): The project; None for the default one.
        context (str): Whose API keys to use.
        owner: Who starts the task, as the task manager compares owners;
            None for the shell's own tasks.

    Returns:
        dict: ``task_id`` and ``status``. The task's result is the object,
        ready to use - or stopped, where it was created not to start.
    """
    object_type = types.check_object_type(object_type)
    vendor = _open_for("create", vendor_name, object_type, project, context)
    try:
        vendor.check_create_settings(object_type, settings)
    except DbaError:
        vendor.close()
        raise
    name = str(settings["name"])
    describe = f"The {object_type} '{name}'"

    def work(task, claim):
        task.stage_started("Creating")
        created = vendor.create_object(object_type, settings)
        claim(created["id"])
        task.message(
            f"{vendor.display_name} accepted the {object_type} '{name}' (id {created['id']})."
        )
        task.stage_finished("Creating")

        task.stage_started("Waiting until it is ready")
        ready = _wait(
            task, vendor, describe,
            lambda: vendor.current_status(object_type, created),
            lambda answer: vendor.is_ready(answer) or vendor.is_stopped(answer),
            CREATE_TIMEOUT,
        )
        task.stage_finished("Waiting until it is ready")
        return ready

    return _start_task(
        "create_object",
        f"Create {object_type} '{name}' on {vendor.display_name}",
        owner, vendor, project, object_type, name, work,
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
    object_type = types.check_object_type(object_type)
    vendor = _open_for("delete", vendor_name, object_type, project, context)
    describe = f"The {object_type} '{object_name}'"

    def work(task, claim):
        task.stage_started("Deleting")
        found = vendor.find_object(object_type, object_name)
        claim(found["name"], found["id"])
        deleted = vendor.delete_found(object_type, found)
        task.stage_finished("Deleting")

        def poll():
            try:
                return vendor.current_status(object_type, found)
            except vendor_lib.ObjectNotFound:
                return None

        task.stage_started("Waiting until it is gone")
        _wait(
            task, vendor, describe, poll,
            lambda answer: answer is None or str(answer.get("status")).lower() == "deleted",
            DELETE_TIMEOUT,
        )
        task.message(f"{describe} is gone.")
        task.stage_finished("Waiting until it is gone")
        return deleted

    return _start_task(
        "delete_object",
        f"Delete {object_type} '{object_name}' on {vendor.display_name}",
        owner, vendor, project, object_type, object_name, work,
    )
