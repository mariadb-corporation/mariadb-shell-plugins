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

"""MCP tools for the dba plugin: MariaDB deployments on cloud vendors.

Thin wrappers around ``dba_plugin.lib.operations``, registered only where the
dba plugin is installed. What a client gets depends on who it is:

* With ``--gui`` (the MariaDB VS Code extension) every tool, working with the
  GUI API keys: storing a key, deploying and deleting included. Deploying
  and deleting start a background task of the client and return its id;
  ``task.get`` follows it (see :mod:`mcp_plugin.lib.task_functions`).
* Otherwise (an agent) only the tools that read: vendors, object types,
  offerings, deployed objects and their status, with the MCP API keys that
  ``mcp setup --setDbaApiKey`` stores. An agent can neither store a key nor
  create or delete anything that costs money or holds data.

A multi-tenant server does not serve this group (see
``general.MULTI_TENANT_FUNCTION_GROUPS``): the keys are the server's, not a
user's, so every user would reach the same vendor accounts.
"""

# cSpell:ignore mysqlsh MariaDB

from typing import Optional, Union

from mcp_plugin.lib import general, tasks
from mcp_plugin.lib.tool_registrar import tool_error, tool_registrar


def register_dba_tools(server, function_groups=()) -> None:
    """Registers the dba tools on the given server, if the dba plugin is there.

    Args:
        server: The MCPServer instance to register the tools on.
        function_groups (list): All function groups being served.

    Returns:
        None
    """
    try:
        from dba_plugin.lib import api_keys, operations
        from dba_plugin.lib.general import DbaError
    except ImportError:
        general.log_event("dba tools not registered: the dba plugin is not installed")
        return

    gui = general.is_gui_mode()
    context = api_keys.CONTEXT_GUI if gui else api_keys.CONTEXT_MCP
    tool = tool_registrar(server)

    def _run(call):
        try:
            return call()
        except DbaError as e:
            raise tool_error(str(e)) from e

    @tool(name="dba.list_vendors")
    def list_vendors(feature_set: Optional[str] = None) -> list:
        """Lists the cloud vendors MariaDB can be deployed on.

        Args:
            feature_set: Only vendors implementing this feature set:
                ManagedService (managed MariaDB services) or CoreCompute
                (VMs and infrastructure to run MariaDB Server or Galera on).

        Returns:
            One object per vendor: name, display_name, feature_sets,
            object_types and api_key_projects (projects an API key is
            stored for; a vendor without one cannot be used yet).
        """
        return _run(lambda: operations.list_vendors(feature_set, context))

    @tool(name="dba.list_vendor_object_support")
    def list_vendor_object_support(vendor_name: Optional[str] = None) -> list:
        """Lists the standardized object types, such as vms or volumes.

        Args:
            vendor_name: A vendor from dba.list_vendors. Without it, every
                type with its description, feature set, deployment settings
                and the vendors supporting it.

        Returns:
            For a vendor, the types it supports and the operations on each.
        """
        return _run(lambda: operations.object_support(vendor_name))

    @tool(name="dba.get_vendor_object_info")
    def get_vendor_object_info(
        vendor_name: str, object_type: str, project: Optional[str] = None
    ) -> dict:
        """Returns what of an object type a vendor offers to deploy.

        For vms the sizes (architecture, cores, RAM, disk, included traffic,
        prices), locations and images; for managed_mariadb_services sizes,
        versions, topologies and locations.

        Args:
            vendor_name: The vendor.
            object_type: The standardized object type.
            project: The project whose API key to use.
        """
        return _run(
            lambda: operations.object_info(vendor_name, object_type, project, context)
        )

    @tool(name="dba.list_vendor_objects")
    def list_vendor_objects(
        vendor_name: str,
        object_type: str,
        tags: Union[dict, list, str, None] = None,
        project: Optional[str] = None,
    ) -> list:
        """Lists the deployed objects of a type at a vendor.

        Args:
            vendor_name: The vendor.
            object_type: The standardized object type.
            tags: Only objects with these tags: {"key": "value"},
                ["key=value", "key"] or "key=value,key". A key alone matches
                any value.
            project: The project to list.

        Returns:
            One object each: object_type, id, name, status, location,
            labels, created, vendor_type and properties.
        """
        return _run(
            lambda: operations.list_objects(vendor_name, object_type, tags, project, context)
        )

    @tool(name="dba.get_vendor_object_status")
    def get_vendor_object_status(
        vendor_name: str, object_type: str, object_name: str, project: Optional[str] = None
    ) -> dict:
        """Returns the current status of one deployed object.

        Args:
            vendor_name: The vendor.
            object_type: The standardized object type.
            object_name: The object's name or id.
            project: The project it belongs to.
        """
        return _run(
            lambda: operations.object_status(
                vendor_name, object_type, object_name, project, context
            )
        )

    if not gui:
        return

    @tool(name="dba.set_api_key")
    def set_api_key(vendor_name: str, key: str, project: Optional[str] = None) -> dict:
        """Stores a vendor's REST API key in the shell's secret store.

        Replaces the key stored before for that vendor and project.

        Args:
            vendor_name: The vendor.
            key: The API key.
            project: The project the key is for; the default one if omitted.

        Returns:
            The vendor, project and secret store key. Never the API key.
        """
        return _run(lambda: operations.set_api_key(vendor_name, key, project, context))

    from mcp.server.mcpserver import Context

    @tool(name="dba.deploy_vendor_object")
    def deploy_vendor_object(
        ctx: Context,
        vendor_name: str,
        object_type: str,
        settings: dict,
        project: Optional[str] = None,
    ) -> dict:
        """Starts deploying an object, such as a VM, at a vendor, as a background task.

        Args:
            vendor_name: The vendor.
            object_type: The standardized object type.
            settings: The object's settings, as listed by
                dba.list_vendor_object_support; always name, and optionally
                labels and vendor_settings (merged into the vendor's own
                request as is).
            project: The project to deploy it in.

        Returns:
            The task_id and status of the task. Follow it with task.get: it
            reports the vendor's status of the object as it changes, and
            ends once the object is ready to use, with the object as its
            result. Cancelling it with task.cancel stops the waiting, not
            the vendor.
        """
        owner = tasks.owner(general.get_client_identity(ctx))
        return _run(
            lambda: operations.deploy_object(
                vendor_name, object_type, settings, project, context, owner
            )
        )

    @tool(name="dba.delete_vendor_object")
    def delete_vendor_object(
        ctx: Context,
        vendor_name: str,
        object_type: str,
        object_name: str,
        project: Optional[str] = None,
    ) -> dict:
        """Starts deleting an object, such as a VM, at a vendor, as a background task.

        Args:
            vendor_name: The vendor.
            object_type: The standardized object type.
            object_name: The object's name or id.
            project: The project it belongs to.

        Returns:
            The task_id and status of the task. Follow it with task.get: it
            ends once the vendor no longer lists the object, with the object
            as it was and what the vendor reported as its result. Deleting a
            VM keeps the volumes attached to it.
        """
        owner = tasks.owner(general.get_client_identity(ctx))
        return _run(
            lambda: operations.delete_object(
                vendor_name, object_type, object_name, project, context, owner
            )
        )
