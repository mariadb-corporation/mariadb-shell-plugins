# Copyright (c) 2026, MariaDB plc.
#
# SPDX-License-Identifier: GPL-2.0-only
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

"""Configuration storage for the MariaDB MCP Server Plugin.

Two kinds of configuration are persisted:

* **Connections**: the MariaDB connection URIs the MCP server is allowed to
  use, together with their passwords. These are stored as shell secrets, keyed
  by ``MCP:Connection:<uri>``, so that passwords are kept in the operating
  system's secret store rather than in a plain file.
* **Allowed paths**: the local directories the MCP server is allowed to
  access. These are stored in a ``settings.json`` file inside the plugin data
  directory (see :func:`mcp_plugin.lib.general.get_mcp_plugin_data_path`).
"""

# cSpell:ignore mysqlsh MariaDB

import json
import os

import mysqlsh

from mcp_plugin.lib import general

# Prefix used for the shell secrets that store MCP connection passwords.
CONNECTION_SECRET_PREFIX = "MCP:Connection:"

# Name of the settings file inside the plugin data directory.
SETTINGS_FILE_NAME = "settings.json"

# Key used in settings.json for the list of allowed directories.
_ALLOWED_PATHS_KEY = "allowedPaths"


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


# --- Connections (stored as shell secrets) --------------------------------


def list_connection_uris() -> list:
    """Returns the configured connection URIs.

    Returns:
        The sorted list of connection URIs that have a stored password.
    """
    return sorted(
        key[len(CONNECTION_SECRET_PREFIX):]
        for key in _shell().list_secrets()
        if key.startswith(CONNECTION_SECRET_PREFIX)
    )


def get_connection_password(uri: str) -> str:
    """Returns the stored password for the given connection URI.

    Args:
        uri (str): The connection URI.

    Returns:
        The stored password.
    """
    return _shell().read_secret(CONNECTION_SECRET_PREFIX + uri)


def store_connection(uri: str, password: str) -> None:
    """Stores the password for the given connection URI.

    Args:
        uri (str): The connection URI.
        password (str): The password to store.

    Returns:
        None
    """
    _shell().store_secret(CONNECTION_SECRET_PREFIX + uri, password)


def delete_connection(uri: str) -> None:
    """Deletes the stored password for the given connection URI.

    Args:
        uri (str): The connection URI.

    Returns:
        None
    """
    _shell().delete_secret(CONNECTION_SECRET_PREFIX + uri)


# --- Allowed paths (stored in settings.json) ------------------------------


def get_settings_file_path() -> str:
    """Returns the full path of the settings.json file."""
    return os.path.join(general.get_plugin_data_path(), SETTINGS_FILE_NAME)


def settings_file_exists() -> bool:
    """Returns whether the settings.json file exists."""
    return os.path.exists(get_settings_file_path())


def get_settings() -> dict:
    """Returns the persisted settings, or an empty dict if none exist."""
    path = get_settings_file_path()
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as settings_file:
        return json.load(settings_file)


def save_settings(settings: dict) -> None:
    """Persists the given settings to settings.json.

    Args:
        settings (dict): The settings to persist.

    Returns:
        None
    """
    with open(get_settings_file_path(), "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file, indent=4)


def get_allowed_paths() -> list:
    """Returns the list of directories the MCP server is allowed to access."""
    return list(get_settings().get(_ALLOWED_PATHS_KEY, []))


def set_allowed_paths(paths: list) -> None:
    """Persists the list of allowed directories.

    Args:
        paths (list): The allowed directories.

    Returns:
        None
    """
    settings = get_settings()
    settings[_ALLOWED_PATHS_KEY] = list(paths)
    save_settings(settings)


def add_allowed_path(path: str) -> None:
    """Adds a directory to the allowed paths and persists it to settings.json.

    The path is normalized to an absolute, user-expanded path (matching the
    format used by ``mcp.setup``). Adding a path that is already present is a
    no-op.

    Args:
        path (str): The directory to allow.

    Returns:
        None
    """
    normalized = os.path.abspath(os.path.expanduser(path))
    paths = get_allowed_paths()
    if normalized not in paths:
        paths.append(normalized)
        set_allowed_paths(paths)


def is_path_allowed(path: str) -> bool:
    """Returns whether the given path is within an allowed directory.

    A path is allowed if it equals one of the allowed directories or is located
    inside one of them. If no allowed directories are configured, nothing is
    allowed.

    Args:
        path (str): The path to check.

    Returns:
        True if access to the path is allowed, False otherwise.
    """
    allowed_paths = get_allowed_paths()
    if not allowed_paths:
        return False

    target = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    for allowed in allowed_paths:
        base = os.path.realpath(os.path.abspath(os.path.expanduser(allowed)))
        try:
            if os.path.commonpath([base, target]) == base:
                return True
        except ValueError:
            # Raised when the paths are on different drives (Windows); not a match.
            continue
    return False
