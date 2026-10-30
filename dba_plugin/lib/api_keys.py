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

"""The vendors' API keys, kept in the shell's secret store.

A key is stored under ``<context>:DBA_API_KEY:<vendor>:<project>``. The
context keeps two owners apart, as the MCP plugin's two connection lists do:

* ``GUI`` - the keys the MariaDB VS Code extension stores, through
  ``dba.set.apiKey`` or an MCP server started with ``--gui``. Only those
  reach them.
* ``MCP`` - the keys ``mcp setup`` stores for the MCP server an agent talks
  to. An agent can use them, it cannot store or replace them.

A key belongs to one project: a Hetzner Cloud API token is issued for one
project, so the project picks the key. A vendor that has no projects of its
own falls back on the key stored without one (see :func:`read_api_key`).
"""

# cSpell:ignore mysqlsh MariaDB

from dba_plugin.lib.general import DbaError

CONTEXT_GUI = "GUI"
CONTEXT_MCP = "MCP"
CONTEXTS = (CONTEXT_GUI, CONTEXT_MCP)

# The project a key is stored for when none is named.
DEFAULT_PROJECT = "default"

_KEY_NAME = "DBA_API_KEY"


def _shell():
    """The shell global, imported when used so the module loads without it."""
    from mysqlsh.globals import shell

    return shell


def check_context(context) -> str:
    """Returns the context named, refusing one that does not exist."""
    name = str(context or "").strip().upper()
    if name not in CONTEXTS:
        raise DbaError(
            f"Unknown API key context '{context}'. Use one of: {', '.join(CONTEXTS)}."
        )
    return name


def _project(project) -> str:
    name = str(project).strip() if project is not None else ""
    if ":" in name:
        raise DbaError(f"A project name cannot contain ':': '{project}'.")
    return name or DEFAULT_PROJECT


def secret_key(vendor_name: str, project=None, context=CONTEXT_GUI) -> str:
    """Returns the secret store key a vendor's API key is stored under.

    Args:
        vendor_name (str): The vendor.
        project (str): The project; None for the default one.
        context (str): GUI or MCP.

    Returns:
        str: ``<context>:DBA_API_KEY:<vendor>:<project>``.
    """
    return f"{check_context(context)}:{_KEY_NAME}:{vendor_name}:{_project(project)}"


def store_api_key(vendor_name: str, key: str, project=None, context=CONTEXT_GUI) -> str:
    """Stores a vendor's API key, replacing the one stored before.

    Args:
        vendor_name (str): The vendor.
        key (str): The API key.
        project (str): The project the key is for; None for the default one.
        context (str): GUI or MCP.

    Returns:
        str: The secret store key it was stored under.

    Raises:
        DbaError: The key is empty.
    """
    if not isinstance(key, str) or not key.strip():
        raise DbaError("The API key is empty.")

    name = secret_key(vendor_name, project, context)
    _shell().store_secret(name, key.strip())
    return name


def delete_api_key(vendor_name: str, project=None, context=CONTEXT_GUI) -> bool:
    """Deletes a vendor's API key.

    Returns:
        bool: Whether there was one to delete.
    """
    name = secret_key(vendor_name, project, context)
    if name not in _shell().list_secrets():
        return False
    _shell().delete_secret(name)
    return True


def read_api_key(
    vendor_name: str, project=None, context=CONTEXT_GUI, fall_back_to_default=False
):
    """Returns a vendor's API key, or None when none is stored.

    Args:
        vendor_name (str): The vendor.
        project (str): The project; None for the default one.
        context (str): GUI or MCP.
        fall_back_to_default (bool): Use the default project's key when the
            project has none - for a vendor whose projects are only labels
            the plugin puts on objects, so that one key serves them all.

    Returns:
        str: The key, or None.
    """
    stored = set(_shell().list_secrets())
    names = [secret_key(vendor_name, project, context)]
    if fall_back_to_default:
        names.append(secret_key(vendor_name, None, context))

    for name in names:
        if name in stored:
            return _shell().read_secret(name)

    return None


def list_api_keys(context=CONTEXT_GUI, vendor_name=None) -> list:
    """Lists the vendors and projects there are API keys for. Never the keys.

    Args:
        context (str): GUI or MCP.
        vendor_name (str): Only this vendor's; None for every vendor's.

    Returns:
        list: One ``{"vendor", "project"}`` dict per key, sorted.
    """
    prefix = f"{check_context(context)}:{_KEY_NAME}:"
    found = []
    for name in _shell().list_secrets():
        if not name.startswith(prefix):
            continue
        vendor, _, project = name[len(prefix):].partition(":")
        if vendor_name is None or vendor == vendor_name:
            found.append({"vendor": vendor, "project": project})

    return sorted(found, key=lambda entry: (entry["vendor"], entry["project"]))
