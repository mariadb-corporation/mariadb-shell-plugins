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

"""The shell objects of the dba plugin, and how a vendor plugin joins them.

The shell loads plugin folders in the order the file system lists them, so
a vendor plugin may load before ``dba_plugin``. It cannot attach
``dba.vendor.<name>`` to a ``dba`` that does not exist yet, and it cannot
import ``dba_plugin``'s ``init.py`` to create it: the shell refuses to
register a function twice, so that would break whichever plugin loads
second. Creating an OBJECT that exists already is a no-op, though, so
:func:`ensure_parent_objects` creates ``dba`` and ``dba.vendor`` - with the
same docs as ``init.py`` - for whichever plugin comes first, and only
``init.py`` registers ``dba``'s functions.
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib import operations, vendors
from dba_plugin.lib.general import DbaError

DBA_DOC = """Deploys and manages MariaDB Server and MariaDB Galera Cluster deployments.

    The dba global object deploys MariaDB on cloud vendors: managed MariaDB
    services where the vendor offers them, and the VMs, volumes, networks
    and the rest of the infrastructure to run MariaDB Server or a Galera
    Cluster on. Every vendor is a plugin of its own, reached as
    dba.vendor.<name>, and every function here takes the vendor's name.
    Objects are named with standardized object types, such as vms or
    managed_mariadb_services, whatever the vendor calls them.
    """

VENDOR_DOC = """The cloud vendor plugins.

    Each installed vendor plugin adds an object here, named after the vendor,
    with the same functions: featureSet, objectSupport, objectInfo,
    objectSettingsInfo, validateObjectSettings, listObjects, objectStatus,
    createObject and deleteObject.
    """


def ensure_parent_objects() -> None:
    """Creates the dba and dba.vendor objects, unless they exist."""
    from mysqlsh.plugin_manager.registrar import PluginRegistrar

    registrar = PluginRegistrar()

    class dba:  # pylint: disable=invalid-name,missing-class-docstring
        __doc__ = DBA_DOC

    class vendor:  # pylint: disable=invalid-name,missing-class-docstring
        __doc__ = VENDOR_DOC

    registrar.register_object("dba", PluginRegistrar.FunctionData(dba).format_info())
    registrar.register_object(
        "dba.vendor", PluginRegistrar.FunctionData(vendor).format_info()
    )


def plain(value):
    """A value from the shell as plain Python: dicts and lists all the way down.

    The shell hands a dictionary argument over as its own mapping type; the
    vendor plugins send what they get as JSON, so it is copied first.
    """
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    if hasattr(value, "items"):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)) or hasattr(value, "__iter__"):
        return [plain(item) for item in value]
    return value


def shell_call(call):
    """Runs a dba operation for a shell function.

    Returns:
        What the operation returns.

    Raises:
        mysqlsh.Error: The operation refused or failed, with its message.
    """
    import mysqlsh
    from mysqlsh import task_manager

    try:
        return call()
    except (DbaError, task_manager.TaskError) as e:
        raise mysqlsh.Error(str(e)) from e


def task_answer(started: dict, function: str):
    """What a shell function returns for the task it started.

    The task's id and status - or, where the function is the command-line
    call, the task's result once it ended, as the process ends right after
    the call (see ``mysqlsh.task_manager.answer``).

    Args:
        started (dict): What the operation that started the task returned.
        function (str): The shell function, e.g. "dba.create.vendor.object".
    """
    from mysqlsh import task_manager

    return task_manager.answer(task_manager.get(started["task_id"]), function)


def camel_case(name: str) -> str:
    """``mariadb_cloud`` as the shell names an object: ``mariadbCloud``."""
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


def register_vendor_plugin(vendor_cls) -> None:
    """Registers a vendor and its ``dba.vendor.<name>`` shell object.

    Called by a vendor plugin's ``init.py``, once. Every vendor gets the
    same functions, so they are made here rather than in each plugin:

    * featureSet() - the feature sets it implements.
    * objectSupport() - the object types it supports, and how.
    * objectInfo(objectType[, project]) - what of a type can be created.
    * objectSettingsInfo(objectType[, project]) - its settings, for a dialog.
    * validateObjectSettings(objectType, settings[, project]) - checks them.
    * listObjects(objectType[, tags, project]) - the deployed objects.
    * objectStatus(objectType, objectName[, project]) - one object's status.
    * createObject(objectType, settings[, project]) - starts a task creating one.
    * deleteObject(objectType, objectName[, project]) - starts a task deleting one.

    Args:
        vendor_cls: The :class:`dba_plugin.lib.vendor.Vendor` subclass.

    Returns:
        None
    """
    from mysqlsh.plugin_manager import plugin, plugin_function

    vendors.register(vendor_cls)
    ensure_parent_objects()

    name = vendor_cls.name
    display = vendor_cls.display_name
    object_name = camel_case(name)
    prefix = f"dba.vendor.{object_name}"

    vendor_object = type(
        object_name,
        (),
        {
            "__doc__": f"""{display}: {', '.join(vendor_cls.feature_sets)}.

    Creates and manages {display} objects for MariaDB. The same functions
    are reached through the dba functions with the vendor name
    '{name}'. {vendor_cls.api_key_help}
    """
        },
    )
    plugin(parent="dba.vendor")(vendor_object)

    def feature_set() -> list:
        return shell_call(lambda: operations.feature_set(name))

    feature_set.__doc__ = f"""Returns the feature sets {display} implements.

        Returns:
            A list of ManagedService and/or CoreCompute.
        """

    def object_support() -> list:
        return shell_call(lambda: operations.object_support(name))

    object_support.__doc__ = f"""Returns the object types {display} supports.

        Returns:
            One dict per standardized object type, with its description,
            feature set and the operations supported on it.
        """

    def object_info(object_type: str, project: str = None) -> dict:
        return shell_call(
            lambda: operations.object_info(name, object_type, project)
        )

    object_info.__doc__ = f"""Returns what of an object type {display} offers to create.

        For VMs the sizes (architecture, cores, RAM, disk, included
        traffic and prices), locations and images; for the other types what
        applies to them.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            project (str): The project whose API key to use.

        Returns:
            A dict of lists.
        """

    def object_settings_info(object_type: str, project: str = None) -> dict:
        return shell_call(lambda: operations.settings_info(name, object_type, project))

    object_settings_info.__doc__ = f"""Returns the settings to create a {display} object type with, for a dialog.

        Each setting with its position, section, caption, description,
        type, whether it is required, its range, whether it can be changed
        once deployed, and the values {display} offers to choose from.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            project (str): The project whose API key to use.

        Returns:
            A dict: vendor, display_name, object_type, groups and settings.
        """

    def validate_object_settings(object_type: str, settings: dict, project: str = None) -> dict:
        return shell_call(
            lambda: operations.validate_settings(name, object_type, plain(settings), project)
        )

    validate_object_settings.__doc__ = f"""Checks the settings to create a {display} object with, before it is created.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            settings (dict): The settings, as createObject takes them.
            project (str): The project to create it in.

        Returns:
            A dict: valid, errors and warnings, each problem with a message
            and the keys of the settings it is about.
        """

    def list_objects(object_type: str, tags=None, project: str = None) -> list:
        return shell_call(
            lambda: operations.list_objects(name, object_type, plain(tags), project)
        )

    list_objects.__doc__ = f"""Lists the deployed {display} objects of a type.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            tags: Only objects with these tags: a dict, a list of key=value
                strings or a comma-separated string of them.
            project (str): The project to list.

        Returns:
            A list of objects.
        """

    def object_status(object_type: str, object_name: str, project: str = None) -> dict:
        return shell_call(
            lambda: operations.object_status(name, object_type, object_name, project)
        )

    object_status.__doc__ = f"""Returns the current status of one {display} object.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            object_name (str): The object's name or id.
            project (str): The project it belongs to.

        Returns:
            The object, with its current status.
        """

    def create_object(object_type: str, settings: dict, project: str = None) -> dict:
        return shell_call(
            lambda: operations.create_object(name, object_type, plain(settings), project)
        )

    create_object.__doc__ = f"""Starts creating one {display} object as a background task.

        Returns the id of a task at once; follow it with task.get(taskId).
        It ends once the object is ready to use, with the object as its
        result.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            settings (dict): The object's settings, see
                dba.list.vendor.objectSupport().
            project (str): The project to create it in.

        Returns:
            The task_id and status of the task.
        """

    def delete_object(object_type: str, object_name: str, project: str = None) -> dict:
        return shell_call(
            lambda: operations.delete_object(name, object_type, object_name, project)
        )

    delete_object.__doc__ = f"""Starts deleting one {display} object as a background task.

        Returns the id of a task at once; follow it with task.get(taskId).
        It ends once the object is gone.

        Args:
            object_type (str): The standardized object type, e.g. vms.
            object_name (str): The object's name or id.
            project (str): The project it belongs to.

        Returns:
            The task_id and status of the task.
        """

    for function_name, function in (
        ("featureSet", feature_set),
        ("objectSupport", object_support),
        ("objectInfo", object_info),
        ("objectSettingsInfo", object_settings_info),
        ("validateObjectSettings", validate_object_settings),
        ("listObjects", list_objects),
        ("objectStatus", object_status),
        ("createObject", create_object),
        ("deleteObject", delete_object),
    ):
        plugin_function(f"{prefix}.{function_name}", shell=True, cli=False)(function)

