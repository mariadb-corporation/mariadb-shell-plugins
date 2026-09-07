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

"""Configuration storage for the MariaDB MCP Server Plugin.

Two kinds of configuration are persisted:

* **Connections**: the MariaDB connection URIs the MCP server is allowed to
  use, together with their passwords. These are stored as shell secrets, keyed
  by ``MCP:Connection:<uri>``, so that passwords are kept in the operating
  system's secret store rather than in a plain file. A connection is looked up
  by its URI, and a client need not spell it exactly as it was configured (see
  :func:`normalize_connection_uri`).
* **Allowed paths**: the local directories the MCP server is allowed to
  access. These are stored in a ``settings.json`` file inside the plugin data
  directory (see :func:`mcp_plugin.lib.general.get_mcp_plugin_data_path`).
"""

# cSpell:ignore mysqlsh MariaDB mysqlx unparse

import json
import os
from typing import Optional

import mysqlsh

from mcp_plugin.lib import general

# Prefix used for the shell secrets that store MCP connection passwords.
CONNECTION_SECRET_PREFIX = "MCP:Connection:"

# URI scheme prefixes that mean the same thing as a bare ``user@host:port``:
# the MariaDB client-server protocol. A client writing a URI of its own tends to
# put one in front of it - ``mariadb://`` is not even a scheme the shell's own
# parser accepts - so they are stripped before parsing and all three spellings
# of one connection compare equal. ``mysqlx://`` is deliberately NOT among
# them: it names a different protocol on a different port. Compared
# case-insensitively.
PROTOCOL_SCHEME_PREFIXES = ("mariadb://", "mysql://")

# The TCP port a connection without one is opened on. Named here because a URI
# that leaves the port out and one that spells out the default name the same
# server, and the two have to compare equal.
DEFAULT_PORT = 3306

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


def parse_connection_uri(uri) -> Optional[dict]:
    """Returns a connection URI taken apart, with any protocol scheme removed.

    The one place a connection URI is parsed, so that everything reading one
    agrees on which spellings are acceptable: a ``mariadb://`` or ``mysql://``
    prefix is stripped first (see :data:`PROTOCOL_SCHEME_PREFIXES`), since the
    shell's own parser rejects those schemes.

    Args:
        uri: The connection URI to parse.

    Returns:
        The parsed URI as a plain dict that callers may adjust, or None if it is
        not a URI the shell can parse - in which case it does not name a
        connection that could be opened either.
    """
    if not isinstance(uri, str):
        return None

    uri = uri.strip()
    if not uri:
        return None

    for prefix in PROTOCOL_SCHEME_PREFIXES:
        if uri[:len(prefix)].lower() == prefix:
            uri = uri[len(prefix):]
            break

    try:
        # A plain dict, so that what was parsed can be adjusted.
        return dict(_shell().parse_uri(uri))
    except Exception:  # noqa: BLE001 - not a URI, so not a connection either
        return None


def normalize_connection_uri(uri) -> Optional[str]:
    """Returns a connection URI in the form used to compare connection URIs.

    One connection can be written down in more than one way, and the spelling a
    connection is configured under is not the one a client necessarily sends:
    ``db.list_connections`` hands out the stored key, but a client composing a
    URI itself tends to put a scheme in front of it, leave out the default port
    or case the host differently. As connections are looked up by their URI,
    those spellings must be reduced to one first - otherwise a client is told a
    connection it can see listed is not configured.

    Only spellings that name the very same connection are folded together: a
    ``mariadb://`` or ``mysql://`` prefix (see
    :data:`PROTOCOL_SCHEME_PREFIXES`), a missing port, the case of the host, and
    a password written into the URI, which is never used - the stored one is.
    Anything a URI says over and above that is kept and has to match, so a URI
    naming a default schema or a connection option is NOT the same connection as
    one that does not: it would otherwise be answered with a connection that
    quietly does not do what it asked for, an option like ``ssl-mode=REQUIRED``
    included.

    Args:
        uri: The connection URI to normalize.

    Returns:
        The normalized URI, or None if it is not a URI the shell can parse - in
        which case it does not name a connection that could be opened either.
    """
    connection_data = parse_connection_uri(uri)
    if connection_data is None:
        return None

    # Guarded as a whole: whatever the shell cannot put back together is not a
    # URI that could be opened either, so it names no connection and there is
    # nothing to compare.
    try:
        # The password of a configured connection comes from the secret store,
        # so one written into the URI says nothing about which one is meant.
        connection_data.pop("password", None)

        host = connection_data.get("host")
        if host is not None:
            # Host names are case-insensitive; user names are not.
            connection_data["host"] = host.lower()

        if (
            host
            and "port" not in connection_data
            and "socket" not in connection_data
            and "scheme" not in connection_data
        ):
            # Left to the shell this would default to the same port; spelled
            # out, a URI with and one without the default port compare equal.
            # Only for the protocol above - another scheme has its own default.
            connection_data["port"] = DEFAULT_PORT

        # The shell's own rendering of the parsed URI: options in a fixed
        # order, percent-encoding and a trailing slash normalized.
        return _shell().unparse_uri(connection_data)
    except Exception:  # noqa: BLE001 - not a URI, so not a connection either
        return None


def resolve_connection_uri(uri) -> Optional[str]:
    """Returns the configured connection URI that the given URI names.

    The URI a client passes to ``db.connect`` does not have to be spelled
    exactly like the configured one, only name the same connection (see
    :func:`normalize_connection_uri`). What comes back is the configured URI,
    which is the key everything else uses: the password is read under it and the
    connection is opened, logged and re-validated on it.

    Args:
        uri: The connection URI to resolve.

    Returns:
        The configured connection URI, or None if no configured connection is
        the one named.

    Raises:
        mysqlsh.Error: If more than one configured connection is - the same
            connection configured twice, under two spellings.
    """
    configured_uris = list_connection_uris()

    # The spelling that was stored is a match for itself whatever it looks
    # like, including one no longer parsable by this shell.
    if uri in configured_uris:
        return uri

    normalized = normalize_connection_uri(uri)
    if normalized is None:
        return None

    matches = [
        configured_uri
        for configured_uri in configured_uris
        if normalize_connection_uri(configured_uri) == normalized
    ]

    if not matches:
        return None

    if len(matches) > 1:
        # Two configured connections naming the same one: they may well hold
        # different passwords, so which was meant is for whoever configured
        # them to say, not for this to guess.
        raise mysqlsh.Error(
            f"'{uri}' names more than one configured connection "
            f"({', '.join(matches)}). Pass one of them as it is listed by "
            "db.list_connections, or remove the duplicate with mcp.setup."
        )

    return matches[0]


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
