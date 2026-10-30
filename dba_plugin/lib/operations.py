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
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib import api_keys, object_types as types, vendor as vendor_lib, vendors
from dba_plugin.lib.general import DbaError


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


def deploy_object(
    vendor_name: str, object_type: str, settings, project=None, context=api_keys.CONTEXT_GUI
) -> dict:
    """Deploys one object with the given settings."""
    if settings is not None and not isinstance(settings, dict):
        raise DbaError("settings must be a dict.")
    return _with_vendor(
        vendor_name, project, context, lambda v: v.deploy_object(object_type, settings)
    )


def delete_object(
    vendor_name: str, object_type: str, object_name: str, project=None, context=api_keys.CONTEXT_GUI
) -> dict:
    """Deletes one deployed object, by its name or id."""
    return _with_vendor(
        vendor_name, project, context, lambda v: v.delete_object(object_type, object_name)
    )
