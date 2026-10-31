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

"""The dba functions of the shell.

They store and use the GUI API keys - those of the MariaDB VS Code
extension and of the user at the shell. The MCP keys are mcp setup's (see
dba_plugin.lib.api_keys).
"""

# cSpell:ignore mysqlsh MariaDB

from mysqlsh.plugin_manager import plugin_function

from dba_plugin.lib import operations
from dba_plugin.lib.objects import plain, shell_call, task_answer


@plugin_function("dba.list.vendors", shell=True, cli=True, web=True)
def list_vendors(feature_set: str = None) -> list:
    """Lists the cloud vendors, optionally only those with a feature set.

    Args:
        feature_set (str): Only the vendors implementing this feature set,
            ManagedService or CoreCompute.

    Returns:
        One dict per vendor with its name, display_name, feature_sets,
        object_types and api_key_projects (the projects an API key is
        stored for).
    """
    return shell_call(lambda: operations.list_vendors(feature_set))


@plugin_function("dba.set.apiKey", shell=True, cli=False, web=True)
def set_api_key(vendor_name: str, key: str, project: str = None) -> dict:
    """Stores the REST API key of a vendor in the shell's secret store.

    The key is stored as GUI:DBA_API_KEY:<vendor>:<project>, replacing the
    one stored before. The keys an MCP server uses for agents are stored
    with mcp setup instead.

    Args:
        vendor_name (str): The vendor, e.g. hetzner.
        key (str): The API key.
        project (str): The project the key is for. Defaults to default.

    Returns:
        The vendor, project and secret store key. Never the API key.
    """
    return shell_call(lambda: operations.set_api_key(vendor_name, key, project))


@plugin_function("dba.list.vendor.objectSupport", shell=True, cli=True, web=True)
def list_object_support(vendor_name: str = None) -> list:
    """Lists the standardized object types the dba plugin supports.

    Without a vendor, every object type with its description, feature set,
    settings to create objects with and the vendors supporting it. With one, the types
    that vendor supports and the operations it supports on each.

    Args:
        vendor_name (str): The vendor.

    Returns:
        One dict per object type.
    """
    return shell_call(lambda: operations.object_support(vendor_name))


@plugin_function("dba.get.vendor.objectInfo", shell=True, cli=True, web=True)
def get_object_info(vendor_name: str, object_type: str, project: str = None) -> dict:
    """Returns what of an object type a vendor offers to create.

    For VMs the sizes (architecture, cores, RAM, disk, included traffic
    and prices where the vendor reports them), locations and images; for a
    managed MariaDB service its sizes, versions and regions; for the other
    types what applies to them.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        project (str): The project whose API key to use.

    Returns:
        A dict of lists.
    """
    return shell_call(lambda: operations.object_info(vendor_name, object_type, project))


@plugin_function("dba.get.vendor.objectSettingsInfo", shell=True, cli=True, web=True)
def get_object_settings_info(vendor_name: str, object_type: str, project: str = None) -> dict:
    """Returns the settings to create an object type with, described for a dialog.

    Every setting the vendor takes for the type, the standardized ones it
    supports and its own, with what a dialog needs to show and check it.
    That is its position, section, caption, description, type, whether it
    is required, its default, range and form, and whether it can be
    changed once the object is deployed. Where there is a choice, the
    values to choose from come with it, with how to show them and which
    apply with the other settings chosen. These are what the vendor offers,
    such as sizes with their resources and prices, locations and images,
    and what is deployed, such as networks, SSH keys and VMs.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        project (str): The project whose API key to use.

    Returns:
        A dict: vendor, display_name, object_type, groups (the sections,
        in order) and settings (each setting by its key, in order). See the
        dba plugin's README for every attribute of a setting.
    """
    return shell_call(lambda: operations.settings_info(vendor_name, object_type, project))


@plugin_function("dba.validate.vendor.objectSettings", shell=True, cli=True, web=True)
def validate_object_settings(
    vendor_name: str, object_type: str, settings: dict, project: str = None
) -> dict:
    """Checks the settings to create an object with, before it is created.

    Checks what dba.create.vendor.object checks before it starts its task,
    which is the settings required and supported, the type, range and form
    of each value and the vendor's own rules. Then it checks them against
    the vendor, that the sizes, locations, images and versions named are
    offered, together, and that the objects named are deployed. Nothing is
    created.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        settings (dict): The settings, as dba.create.vendor.object takes them.
        project (str): The project to create it in.

    Returns:
        A dict: valid, errors and warnings. Each problem has a message and
        the keys of the settings it is about.
    """
    return shell_call(
        lambda: operations.validate_settings(vendor_name, object_type, plain(settings), project)
    )


@plugin_function("dba.get.vendor.objectStatus", shell=True, cli=True, web=True)
def get_object_status(
    vendor_name: str, object_type: str, object_name: str, project: str = None
) -> dict:
    """Returns the current status of one deployed vendor object.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        object_name (str): The object's name or id.
        project (str): The project it belongs to.

    Returns:
        The object, with its current status.
    """
    return shell_call(
        lambda: operations.object_status(vendor_name, object_type, object_name, project)
    )


@plugin_function("dba.list.vendor.objects", shell=True, cli=True, web=True)
def list_objects(
    vendor_name: str, object_type: str, tags=None, project: str = None
) -> list:
    """Lists the deployed objects of a type at a vendor.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        tags: Only objects with these tags: a dict, a list of key=value
            strings or a comma-separated string of them. A key without a
            value matches any value.
        project (str): The project to list.

    Returns:
        One dict per object: object_type, id, name, status, location,
        labels, created, vendor_type and properties.
    """
    return shell_call(
        lambda: operations.list_objects(vendor_name, object_type, plain(tags), project)
    )


@plugin_function("dba.create.vendor.object", shell=True, cli=True, web=True)
def create_object(
    vendor_name: str, object_type: str, settings: dict, project: str = None
) -> dict:
    """Starts creating a vendor object, such as a VM, as a background task.

    The settings of each object type are listed by
    dba.list.vendor.objectSupport(). Every type takes name, labels and
    vendor_settings, a dict merged into the vendor's own request as is.

    Creating takes minutes, a managed service far longer, so this returns
    the id of a task at once. Follow it with task.get(taskId), which reports
    the vendor's status of the object as it changes. The task ends once the
    object is ready to use, with the object as its result. Cancelling it
    with task.cancel(taskId) stops the waiting, not the vendor; delete what
    was created on its own.

    On the command line, mariadb-shell -- dba create vendor object ...,
    the call waits for the task instead. It writes the vendor's statuses
    to stderr as they come and returns the object once it is ready, or
    fails with the task's error. Ctrl+C cancels the task.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        settings (dict): The object's settings.
        project (str): The project to create it in.

    Returns:
        The task_id and status of the task; on the command line, the object.
    """
    return shell_call(
        lambda: task_answer(
            operations.create_object(vendor_name, object_type, plain(settings), project),
            "dba.create.vendor.object",
        )
    )


@plugin_function("dba.delete.vendor.object", shell=True, cli=True, web=True)
def delete_object(
    vendor_name: str, object_type: str, object_name: str, project: str = None
) -> dict:
    """Starts deleting a vendor object, such as a VM, as a background task.

    Returns the id of a task at once. Follow it with task.get(taskId). The
    task ends once the vendor no longer lists the object, with the object as
    it was and what the vendor reported as its result. Deleting a VM keeps
    the volumes attached to it. On the command line, the call waits for the
    task and returns its result.

    Args:
        vendor_name (str): The vendor.
        object_type (str): The standardized object type, e.g. vms.
        object_name (str): The object's name or id.
        project (str): The project it belongs to.

    Returns:
        The task_id and status of the task; on the command line, the object
        deleted and what the vendor reported.
    """
    return shell_call(
        lambda: task_answer(
            operations.delete_object(vendor_name, object_type, object_name, project),
            "dba.delete.vendor.object",
        )
    )
