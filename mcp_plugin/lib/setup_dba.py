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

"""mcp.setup's part for the dba tools: the cloud vendors' API keys.

The keys an MCP server uses for agents are stored here, as
``MCP:DBA_API_KEY:<vendor>:<project>`` secrets, and only here: the dba tools
an agent gets read with them and cannot store one. The MariaDB VS Code
extension keeps its own (``GUI:``) keys, through the dba plugin.

Everything here needs the dba plugin; without it the menu does not offer
these entries and the options are refused.
"""

# cSpell:ignore mysqlsh MariaDB

import importlib.util

import mysqlsh

from mcp_plugin.lib import setup_prompts as prompts


def is_available() -> bool:
    """Whether the dba plugin is installed."""
    return importlib.util.find_spec("dba_plugin") is not None


def _dba():
    """The dba plugin's modules this needs, or a refusal saying it is missing."""
    if not is_available():
        raise mysqlsh.Error(
            "The dba plugin is not installed, so there are no cloud vendors to "
            "store an API key for."
        )
    from dba_plugin.lib import api_keys, operations
    from dba_plugin.lib.general import DbaError

    return api_keys, operations, DbaError


def store_api_key(vendor_name: str, key: str, project=None) -> str:
    """Stores a vendor's API key for the MCP server.

    Returns:
        str: What was stored, for the user.

    Raises:
        mysqlsh.Error: The vendor is unknown or the key empty.
    """
    api_keys, operations, DbaError = _dba()
    try:
        stored = operations.set_api_key(vendor_name, key, project, api_keys.CONTEXT_MCP)
    except DbaError as e:
        raise mysqlsh.Error(str(e)) from e

    return (
        f"Stored the API key of {stored['vendor']} for project "
        f"'{stored['project']}'. A running server uses it from its next call."
    )


def delete_api_key(vendor_name: str, project=None) -> str:
    """Deletes a vendor's API key of the MCP server.

    The vendor is not checked against the installed ones, so the key of a
    vendor whose plugin was removed can still be deleted.

    Returns:
        str: What was done, for the user.
    """
    api_keys, _, DbaError = _dba()
    try:
        deleted = api_keys.delete_api_key(str(vendor_name).strip().lower(), project, api_keys.CONTEXT_MCP)
    except DbaError as e:
        raise mysqlsh.Error(str(e)) from e

    where = f"{vendor_name} for project '{project or api_keys.DEFAULT_PROJECT}'"
    return f"Deleted the API key of {where}." if deleted else f"No API key of {where} was stored."


def list_api_keys():
    """The vendors and projects the MCP server has API keys for.

    Returns:
        list: ``{"vendor", "project"}`` dicts, or None without the dba plugin.
    """
    if not is_available():
        return None
    api_keys, _, _ = _dba()
    return api_keys.list_api_keys(api_keys.CONTEXT_MCP)


# --- The interactive menu ----------------------------------------------------


def menu_entries() -> list:
    """The management menu's entries for the API keys, if the plugin is there."""
    if not is_available():
        return []
    return [
        ("Add a cloud vendor API key", add_api_key),
        ("Delete a cloud vendor API key", delete_api_key_prompt),
    ]


def add_api_key() -> None:
    """Prompts for a vendor, a project and the key, and stores it."""
    _, operations, _ = _dba()
    vendors = [vendor["name"] for vendor in operations.list_vendors()]
    if not vendors:
        print("\nNo dba vendor plugin is installed.")
        return

    index = prompts.select_or_cancel("Select the cloud vendor", vendors)
    if index < 0:
        return

    project = prompts.ask("Enter the project the key is for (default: default): ")
    key = prompts.password(f"Enter the API key of {vendors[index]}: ")
    if not key:
        print("No API key entered. Nothing was stored.")
        return

    print(store_api_key(vendors[index], key, project or None))


def delete_api_key_prompt() -> None:
    """Prompts for one of the stored API keys and deletes it."""
    keys = list_api_keys() or []
    if not keys:
        print("\nNo cloud vendor API key is stored.")
        return

    index = prompts.select_or_cancel(
        "Select the API key to delete",
        [f"{key['vendor']}, project {key['project']}" for key in keys],
    )
    if index < 0:
        return

    print(delete_api_key(keys[index]["vendor"], keys[index]["project"]))
