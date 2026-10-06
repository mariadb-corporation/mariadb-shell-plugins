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
  by ``MCP:CONN:<uri>``, so that passwords are kept in the operating
  system's secret store rather than in a plain file. A connection is looked up
  by its URI, and a client need not spell it exactly as it was configured (see
  :func:`normalize_connection_uri`). The URI carries its scheme - ``mariadb``
  unless it says otherwise - which is what lets a connection be configured
  through an SSH tunnel, as ``mariadb+ssh://``. Connections stored before that
  was so have no scheme in their key; they are reported with the default one
  filled in and resolve either way, so nothing has to be migrated.

  A connection may also have *details*: the *folder* it is filed in, a path
  such as ``/Sandboxes`` or ``/Sandboxes/note_app``, a *caption* to show
  instead of the URI and a *color* (see :func:`get_connection_details`). They
  are presentation for the VS Code extension and nothing else, and are kept
  out of the secret store, in a ``connections.json`` file next to
  ``settings.json``, keyed by the list and the stored URI. The secret store
  stays the one record of which connections exist: a connection with no
  details is at the top level with no caption, and details of a connection
  that is not stored are ignored and dropped on the next write, so the two can
  never disagree about anything but how a connection is shown. Outside GUI
  mode the details are never reported (see
  :func:`list_connections_with_details`).

  Folders used to be written into the key, ``MCP:CONN:/Sandboxes:<uri>``; such
  keys are moved to the plain key, their folder into ``connections.json``, the
  first time the store is read (see :func:`upgrade_connection_keys`).

  There are two lists of them, told apart by a *kind* and stored under a prefix
  of their own: :data:`CONNECTION_KIND_MCP`, the connections ``mcp.setup``
  curates and any MCP client may open, and :data:`CONNECTION_KIND_GUI`, the ones
  the MariaDB VS Code extension manages for itself. Every function here takes
  the kind and defaults it to the MCP list, which is what everything written
  before the GUI list existed means. The GUI list is only reachable at all from
  a server started with ``--gui`` (see
  :func:`mcp_plugin.lib.general.set_gui_mode`).

  The prefixes used to be spelled out, ``MCP:Connection:`` and
  ``GUI:Connection:``. They were shortened because a key is limited to
  :data:`MAX_CONNECTION_KEY_BYTES` and every byte of prefix is one the URI
  cannot have; connections stored under the old ones are moved to the
  new ones the first time the store is read (see
  :func:`upgrade_connection_keys`).
  In multi-tenant mode every user has connections of their own, kept in a
  shell secret GROUP named by the user's ``mcp_user_id`` (see
  :mod:`mcp_plugin.lib.tenants`). The keys are the same - a group is not part
  of the key - so every connection function takes the user as
  ``mcp_user_id``, None meaning the ``generic`` group every single-tenant
  connection is in. A user's connections have no details: those are for the VS
  Code extension, which is not served in multi-tenant mode.
* **Allowed paths**: the local directories the MCP server is allowed to
  access. These are stored in a ``settings.json`` file inside the plugin data
  directory (see :func:`mcp_plugin.lib.general.get_mcp_plugin_data_path`).
  A file of their own rather than a section of ``connections.json``: they are
  an access control, the details are cosmetic, and a write of one must never
  be able to lose the other.
"""

# cSpell:ignore mysqlsh MariaDB mysqlx unparse

import json
import os
import re
from typing import Optional

import mysqlsh

from mcp_plugin.lib import general

# Prefix used for the shell secrets that store MCP connection passwords. Short,
# because it counts against MAX_CONNECTION_KEY_BYTES like the folder and URI do.
CONNECTION_SECRET_PREFIX = "MCP:CONN:"

# Prefix used for the shell secrets that store the connection passwords the
# MariaDB VS Code extension manages. A separate prefix and not a flag inside the
# secret: the two lists have different owners and different reach. The MCP list
# is what an autonomous client may open and is curated with ``mcp.setup``; the
# GUI list is the user's own, added and removed from the extension while it
# runs. Keeping them apart means a connection the user made for the editor is
# not silently handed to whatever else drives this server, and neither list can
# be emptied by an operation meant for the other.
#
# The same length as CONNECTION_SECRET_PREFIX, so that moving a connection
# between the two lists never changes whether its key fits.
GUI_CONNECTION_SECRET_PREFIX = "GUI:CONN:"

# The two lists a connection can belong to, named for the callers that use them.
CONNECTION_KIND_MCP = "mcp"
CONNECTION_KIND_GUI = "gui"
SUPPORTED_CONNECTION_KINDS = (CONNECTION_KIND_MCP, CONNECTION_KIND_GUI)

# Not a list of its own: what db.list_connections takes in GUI mode to report
# both lists in one call, each entry saying which it is in. Nothing is stored
# under it, which is why it is not among SUPPORTED_CONNECTION_KINDS and
# normalize_connection_kind refuses it.
CONNECTION_KIND_ALL = "all"

# The list everything that does not say otherwise means. Every caller that
# predates the GUI list - mcp.setup, sandbox.deploy, the migrator tools - works
# on the MCP connections, so leaving the kind out has to go on meaning that.
DEFAULT_CONNECTION_KIND = CONNECTION_KIND_MCP

# The secret prefix each kind is stored under.
_CONNECTION_SECRET_PREFIXES = {
    CONNECTION_KIND_MCP: CONNECTION_SECRET_PREFIX,
    CONNECTION_KIND_GUI: GUI_CONNECTION_SECRET_PREFIX,
}

# The prefix each kind was stored under before the prefixes were shortened.
# Read only by upgrade_connection_keys, which moves what it finds under them.
_LEGACY_CONNECTION_SECRET_PREFIXES = {
    CONNECTION_KIND_MCP: "MCP:Connection:",
    CONNECTION_KIND_GUI: "GUI:Connection:",
}

# Whether this process has moved every connection off the legacy prefixes.
# Once is enough: nothing writes under them any more. Left False after a
# failure, so the connections that could not be moved are tried again.
_connection_keys_upgraded = False

# The scheme a connection URI without one means. Connections used to be stored
# with the scheme stripped off, because the shell's own parser rejected
# ``mariadb://`` outright; MariaDB Shell 26.9.3 takes the whole family, and an
# SSH tunnel can only be ASKED for by scheme (``mariadb+ssh://``), so a scheme
# now has to survive into the stored URI rather than being thrown away. It is
# filled in where a URI leaves it out, so that a spelling with it and one
# without still name the same connection.
DEFAULT_CONNECTION_SCHEME = "mariadb"

# The schemes that speak the MariaDB client-server protocol, and so open on
# :data:`DEFAULT_PORT` when a URI names no port. ``mysqlx`` is deliberately NOT
# among them: it is a different protocol on a different port, so a ``mysqlx://``
# URI stays a different connection. The ``+ssh`` forms ARE, because the
# authority of a ``+ssh`` URI is the database endpoint - the tunnel is described
# by the ``ssh-*`` options, not by the host and port.
PROTOCOL_SCHEMES = ("mariadb", "mariadb+ssh", "mysql", "mysql+ssh")

# The TCP port a connection without one is opened on. Named here because a URI
# that leaves the port out and one that spells out the default name the same
# server, and the two have to compare equal.
DEFAULT_PORT = 3306

# Matches the ``scheme://`` a URI starts with, if it has one. The scheme
# grammar is RFC 3986's, which allows the ``+`` that ``mariadb+ssh`` uses.
_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

# The folder a connection at the top level is reported in. Never stored: a
# connection with no folder in connections.json is at the top level.
ROOT_CONNECTION_PATH = "/"

# The folder sandbox.deploy files the connections it registers under.
SANDBOX_CONNECTION_PATH = "/Sandboxes"

# The most bytes a stored key - prefix and URI together - may take. The
# shell's Windows credential helper stores the key as a CREDENTIAL_ATTRIBUTE
# value, which Windows caps at CRED_MAX_VALUE_SIZE (256 bytes); a longer one
# fails in CredWrite with nothing to say why. It is enforced on every platform
# rather than only on Windows, so a connection that can be configured on one
# machine can be configured on any, and the limit is in UTF-8 BYTES, so a
# non-ASCII URI uses it up faster than its length suggests.
MAX_CONNECTION_KEY_BYTES = 256

# The colors a connection may be given, by name, so that every client can map
# them onto its own palette - the VS Code extension onto its theme's chart
# colors, which is what keeps them readable in a light and a dark theme alike.
CONNECTION_COLORS = ("red", "orange", "yellow", "green", "blue", "purple")

# The most characters a connection caption may have. It is a label for a tree
# row, not a description.
MAX_CONNECTION_CAPTION_LENGTH = 100

# Name of the settings file inside the plugin data directory.
SETTINGS_FILE_NAME = "settings.json"

# Name of the file inside the plugin data directory that holds the details of
# the stored connections (see get_connection_details).
CONNECTIONS_FILE_NAME = "connections.json"

# The version of the connections.json format, written into the file so that a
# later format can tell an older file apart.
_CONNECTIONS_FILE_VERSION = 1

# The details a connection can have, each stored only when it is not empty.
_CONNECTION_DETAIL_FIELDS = ("path", "caption", "color")

# Key used in settings.json for the list of allowed directories.
_ALLOWED_PATHS_KEY = "allowedPaths"


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


def secret_options(mcp_user_id=None) -> tuple:
    """Returns the trailing arguments that make a secret call use a user's group.

    Every generic-secret function of the shell takes an optional options
    dictionary whose ``group`` names the secret group to work in. A user's
    secrets are in the group named by their ``mcp_user_id``; everything else is
    in the default ``generic`` group, which is what a call without options uses
    - so for no user this returns nothing at all, and the call is exactly the
    one made before groups existed.

    Args:
        mcp_user_id: The user whose group to use, or None for the ``generic``
            group.

    Returns:
        A tuple to splat after the call's own arguments: empty, or holding the
        one options dictionary.
    """
    if mcp_user_id is None:
        return ()

    return ({"group": mcp_user_id},)


# --- Connections (stored as shell secrets) --------------------------------


def normalize_connection_kind(kind) -> str:
    """Returns the connection kind a caller named, checked against the list.

    Args:
        kind: The kind to use, or None for :data:`DEFAULT_CONNECTION_KIND`.
            Matched without regard to case or surrounding space, since it
            reaches this straight from a tool argument.

    Returns:
        One of :data:`SUPPORTED_CONNECTION_KINDS`.

    Raises:
        mysqlsh.Error: If it names no known kind. Refused rather than defaulted:
            a misspelled kind would otherwise read, write or delete in the other
            list than the one the caller meant.
    """
    if kind is None:
        return DEFAULT_CONNECTION_KIND

    normalized = str(kind).strip().lower()
    if normalized not in _CONNECTION_SECRET_PREFIXES:
        raise mysqlsh.Error(
            f"'{kind}' is not a known connection kind. Supported kinds are: "
            f"{', '.join(SUPPORTED_CONNECTION_KINDS)}."
        )

    return normalized


def connection_secret_prefix(kind=None) -> str:
    """Returns the shell-secret prefix one kind of connection is stored under.

    Args:
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        The secret key prefix, including its trailing colon.
    """
    return _CONNECTION_SECRET_PREFIXES[normalize_connection_kind(kind)]


def usable_connection_kinds() -> tuple:
    """Returns the connection kinds this server may open a connection from.

    The MCP list always; the GUI list only where a server was started with
    ``--gui``, which is the one setting in which the connections the extension
    made for itself are meant to be reachable at all.

    The GUI list comes FIRST, so that when one connection is in both lists it is
    the GUI entry that is opened. The two can hold different passwords, and in
    GUI mode the entry the user last worked with through the extension is the
    one they mean; the MCP entry still answers for every URI the GUI list does
    not name.

    Returns:
        The kinds to search, in the order they are searched.
    """
    if general.is_gui_mode():
        return (CONNECTION_KIND_GUI, CONNECTION_KIND_MCP)

    return (CONNECTION_KIND_MCP,)


def normalize_connection_path(path) -> str:
    """Returns a connection folder in the one form it is stored and reported in.

    Args:
        path: The folder, as a caller wrote it: ``/Sandboxes/note_app``. The
            leading slash may be left out, and empty elements - a doubled or a
            trailing slash - are dropped, as are blanks around an element. None,
            ``""`` and ``"/"`` all mean the top level.

    Returns:
        ``""`` for the top level, otherwise ``/`` followed by the folder names
        joined with ``/``.

    Raises:
        mysqlsh.Error: If the path is not a string.
    """
    if path is None:
        return ""

    if not isinstance(path, str):
        raise mysqlsh.Error(
            f"The connection folder must be a string such as '/Sandboxes', "
            f"not {path!r}."
        )

    elements = [element.strip() for element in path.split("/")]

    return "".join(f"/{element}" for element in elements if element != "")


def normalize_connection_caption(caption) -> str:
    """Returns a connection caption in the one form it is stored in.

    Args:
        caption: The caption, as a caller wrote it. None and blanks mean none.

    Returns:
        The caption without surrounding blanks, ``""`` for none.

    Raises:
        mysqlsh.Error: If it is not a string, holds a line break or other
            control character, or is longer than
            :data:`MAX_CONNECTION_CAPTION_LENGTH`.
    """
    if caption is None:
        return ""

    if not isinstance(caption, str):
        raise mysqlsh.Error(
            f"The connection caption must be a string, not {caption!r}."
        )

    caption = caption.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in caption):
        raise mysqlsh.Error(
            "The connection caption must be a single line of text."
        )

    if len(caption) > MAX_CONNECTION_CAPTION_LENGTH:
        raise mysqlsh.Error(
            "The connection caption may have at most "
            f"{MAX_CONNECTION_CAPTION_LENGTH} characters, and it has "
            f"{len(caption)}."
        )

    return caption


def normalize_connection_color(color) -> str:
    """Returns a connection color in the one form it is stored in.

    Args:
        color: One of :data:`CONNECTION_COLORS`, in any case. None and ``""``
            mean none.

    Returns:
        The color name in lower case, ``""`` for none.

    Raises:
        mysqlsh.Error: If it names no color in :data:`CONNECTION_COLORS`.
    """
    if color is None:
        return ""

    normalized = str(color).strip().lower()
    if normalized and normalized not in CONNECTION_COLORS:
        raise mysqlsh.Error(
            f"'{color}' is not a connection color. Use one of: "
            f"{', '.join(CONNECTION_COLORS)}, or '' for none."
        )

    return normalized


def _connection_key(uri, kind) -> str:
    """Returns the secret key a connection is stored under.

    Args:
        uri (str): The connection URI, as it is stored.
        kind: The connection kind.

    Returns:
        The key.
    """
    return f"{connection_secret_prefix(kind)}{uri}"


def check_connection_key_length(uri, kind=None) -> None:
    """Refuses a connection whose stored key would be too long to store.

    Called before anything is written, so a connection that cannot be stored
    fails with a reason instead of somewhere inside the secret store - or, on
    Windows, with the credential manager's own unexplained error.

    Args:
        uri (str): The connection URI, exactly as it is to be stored.
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        None

    Raises:
        mysqlsh.Error: If the key would take more than
            :data:`MAX_CONNECTION_KEY_BYTES`.
    """
    key_bytes = len(_connection_key(uri, kind).encode("utf-8"))
    if key_bytes <= MAX_CONNECTION_KEY_BYTES:
        return

    prefix_bytes = len(connection_secret_prefix(kind).encode("utf-8"))
    raise mysqlsh.Error(
        f"The connection '{uri}' cannot be stored: its URI may take at most "
        f"{MAX_CONNECTION_KEY_BYTES - prefix_bytes} bytes, and it takes "
        f"{key_bytes - prefix_bytes}. "
        "The secret store keys the password by it, and Windows allows no more "
        f"than {MAX_CONNECTION_KEY_BYTES} bytes per key. Use a shorter URI."
    )


# --- Connection details (stored in connections.json) ----------------------


def get_connections_file_path() -> str:
    """Returns the full path of the connections.json file."""
    return os.path.join(general.get_plugin_data_path(), CONNECTIONS_FILE_NAME)


def _stored_uris_by_kind() -> dict:
    """Returns the stored URIs of every kind, read with one listing.

    Straight from the secret store and without the upgrade, which is what lets
    :func:`upgrade_connection_keys` itself write details.

    Returns:
        A dict of kind to the set of URIs stored under its prefix.
    """
    keys = list(_shell().list_secrets())

    return {
        kind: {key[len(prefix):] for key in keys if key.startswith(prefix)}
        for kind, prefix in _CONNECTION_SECRET_PREFIXES.items()
    }


def _read_connections_file() -> dict:
    """Returns the details of every connection, as connections.json has them.

    Read defensively: the file only ever says how a connection is SHOWN, so
    one that is missing, unreadable or of an unknown shape means no details,
    not an error that would stop the connections from being listed.

    Returns:
        A dict of kind to a dict of stored URI to that connection's non-empty
        details.
    """
    path = get_connections_file_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as connections_file:
            content = json.load(connections_file)
    except (OSError, ValueError) as error:
        general.log_event(f"config: could not read '{path}': {error}")
        return {}

    if not isinstance(content, dict):
        return {}

    details = {}
    for kind in SUPPORTED_CONNECTION_KINDS:
        entries = content.get(kind)
        if not isinstance(entries, dict):
            continue

        details[kind] = {
            uri: {
                field: value
                for field, value in entry.items()
                if field in _CONNECTION_DETAIL_FIELDS
                and isinstance(value, str)
                and value
            }
            for uri, entry in entries.items()
            if isinstance(entry, dict)
        }

    return details


def _write_connections_file(details: dict) -> None:
    """Persists the details of every connection to connections.json.

    Details of a connection that is no longer stored are dropped on the way,
    which is what keeps the file from collecting whatever was deleted behind
    this plugin's back (by an older shell, say). The file is replaced whole
    and atomically, so a reader never sees half of it.

    Args:
        details (dict): What :func:`_read_connections_file` returns, changed.

    Returns:
        None
    """
    stored = _stored_uris_by_kind()
    content = {"version": _CONNECTIONS_FILE_VERSION}
    for kind in SUPPORTED_CONNECTION_KINDS:
        content[kind] = {
            uri: entry
            for uri, entry in sorted(details.get(kind, {}).items())
            if entry and uri in stored[kind]
        }

    path = get_connections_file_path()
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as connections_file:
        json.dump(content, connections_file, indent=4)
    os.replace(temporary, path)


def get_connection_details(uri, kind=None) -> dict:
    """Returns how a stored connection is shown: its folder, caption and color.

    Args:
        uri (str): The connection URI, exactly as it is stored.
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        A dict with ``path`` (normalized, ``""`` for the top level),
        ``caption`` and ``color``, each ``""`` where the connection has none -
        which is everything for a URI that is not stored.
    """
    entry = _read_connections_file().get(normalize_connection_kind(kind), {})

    return _details_of(entry.get(uri, {}))


def _details_of(entry: dict) -> dict:
    """Returns one connection's stored details with every field filled in."""
    return {field: entry.get(field, "") for field in _CONNECTION_DETAIL_FIELDS}


def set_connection_details(
    uri, kind=None, path=None, caption=None, color=None
) -> dict:
    """Changes how a stored connection is shown, leaving its password alone.

    Args:
        uri (str): The connection URI, exactly as it is stored.
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.
        path: The folder to file it in (see :func:`normalize_connection_path`).
        caption: The caption to show (see :func:`normalize_connection_caption`).
        color: The color to show (see :func:`normalize_connection_color`).
            For each of the three, None keeps what the connection has and
            ``""`` clears it.

    Returns:
        The connection's details as they now are, as
        :func:`get_connection_details` reports them.

    Raises:
        mysqlsh.Error: If a detail is not valid, before anything is written.
    """
    kind = normalize_connection_kind(kind)
    changes = _normalized_details(path, caption, color)

    details = _read_connections_file()
    entry = dict(details.setdefault(kind, {}).get(uri, {}))
    if changes:
        entry.update(changes)
        entry = {field: value for field, value in entry.items() if value}
        details[kind][uri] = entry
        _write_connections_file(details)

    return _details_of(entry)


def _normalized_details(path, caption, color) -> dict:
    """Returns the details a caller asked for, normalized; None ones left out.

    Raises:
        mysqlsh.Error: If a detail is not valid.
    """
    changes = {}
    if path is not None:
        changes["path"] = normalize_connection_path(path)
    if caption is not None:
        changes["caption"] = normalize_connection_caption(caption)
    if color is not None:
        changes["color"] = normalize_connection_color(color)

    return changes


def _drop_connection_details(uri, kind) -> None:
    """Forgets the details of a connection that was deleted.

    Best effort: the secret is already gone, and details nothing is stored
    under are ignored anyway and dropped on the next write.
    """
    details = _read_connections_file()
    if uri not in details.get(kind, {}):
        return

    del details[kind][uri]
    try:
        _write_connections_file(details)
    except OSError as error:
        general.log_event(
            f"config: could not forget the details of '{uri}' ({kind}): {error}"
        )


# --- Connections (stored as shell secrets), continued ----------------------


def _split_folder_key(suffix) -> tuple:
    """Takes a key suffix apart that may still carry a folder.

    Only for :func:`upgrade_connection_keys`: folders used to be written into
    the key as ``/path:<uri>``. A URI never starts with a slash - it starts
    with a scheme or a user name - so a leading slash is what said a path came
    first, and the first ``:`` after it ended the path, since no folder name
    could hold one then.

    Args:
        suffix (str): The part of a key after its prefix.

    Returns:
        A ``(uri, path)`` tuple, the path ``""`` where there was none.
    """
    if suffix.startswith("/"):
        colon = suffix.find(":")
        if colon > 0:
            return (suffix[colon + 1:], suffix[:colon])

    return (suffix, "")


def upgrade_connection_keys() -> int:
    """Moves the connections stored under an earlier key format to the current.

    Two earlier formats are read: the legacy prefixes ``MCP:Connection:`` and
    ``GUI:Connection:``, now :data:`CONNECTION_SECRET_PREFIX` and
    :data:`GUI_CONNECTION_SECRET_PREFIX`, and a folder written into the key,
    ``<prefix>/path:<uri>``, which now goes into connections.json while the key
    becomes ``<prefix><uri>``. A key can be in both.

    Run once per process, from everything that reads or writes a connection,
    so it happens whichever entry point is used first, and only once the store
    is actually needed, rather than on every shell start.

    Every new key is written first, then the folders, then the old keys are
    deleted, so an interruption leaves a connection under both keys rather than
    under neither; the next run then finds the new key there and only deletes
    the old one. A key that cannot be moved is logged and left where it is, and
    the upgrade is tried again on the next read.

    Returns:
        The number of connections moved.
    """
    global _connection_keys_upgraded
    if _connection_keys_upgraded:
        return 0

    shell = _shell()
    keys = list(shell.list_secrets())
    present = set(keys)
    old_keys = []
    folders = []
    failed = False

    for kind in SUPPORTED_CONNECTION_KINDS:
        prefix = connection_secret_prefix(kind)
        for old_prefix in (_LEGACY_CONNECTION_SECRET_PREFIXES[kind], prefix):
            for key in keys:
                if not key.startswith(old_prefix):
                    continue

                uri, path = _split_folder_key(key[len(old_prefix):])
                new_key = prefix + uri
                if new_key == key:
                    continue

                try:
                    # Already there means an earlier run was interrupted after
                    # writing it, or the connection was stored again since;
                    # either way it is the newer one, so it is kept.
                    if new_key not in present:
                        shell.store_secret(new_key, shell.read_secret(key))
                        present.add(new_key)
                    old_keys.append(key)
                    if path:
                        folders.append((kind, uri, path))
                except Exception as error:  # noqa: BLE001 - one key must not stop the rest
                    failed = True
                    general.log_event(
                        f"config: could not move the connection '{key}' to "
                        f"'{new_key}': {error}"
                    )

    if folders:
        details = _read_connections_file()
        for kind, uri, path in folders:
            # A folder already in the file was set after the key was written.
            details.setdefault(kind, {}).setdefault(uri, {}).setdefault(
                "path", path
            )
        try:
            _write_connections_file(details)
        except OSError as error:
            general.log_event(
                f"config: could not keep the folders of the moved connections: "
                f"{error}"
            )

    moved = 0
    for key in old_keys:
        try:
            shell.delete_secret(key)
            moved += 1
        except Exception as error:  # noqa: BLE001 - one key must not stop the rest
            failed = True
            general.log_event(
                f"config: could not delete the old connection key '{key}': "
                f"{error}"
            )

    if moved:
        general.log_event(
            f"config: moved {moved} connection(s) to the "
            f"'{CONNECTION_SECRET_PREFIX}<uri>' and "
            f"'{GUI_CONNECTION_SECRET_PREFIX}<uri>' secret keys"
        )

    _connection_keys_upgraded = not failed

    return moved


def _list_stored_connections(kind=None, mcp_user_id=None) -> list:
    """Returns every stored connection URI of one kind, exactly as stored.

    Args:
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user whose connections to list, or None for the
            ``generic`` group.

    Returns:
        The URIs, sorted.
    """
    if mcp_user_id is None:
        # Only the generic group can hold keys in an earlier format: groups
        # came after both of them.
        upgrade_connection_keys()
    prefix = connection_secret_prefix(kind)

    return sorted(
        key[len(prefix):]
        for key in _shell().list_secrets(*secret_options(mcp_user_id))
        if key.startswith(prefix)
    )


def list_connections_with_details(kind=None) -> list:
    """Returns the configured connections of one kind with their details.

    What the VS Code extension lists in GUI mode. Nothing else reports the
    details: outside GUI mode :func:`list_connection_uris` is the list, and the
    folders, captions and colors are invisible to an agent.

    Args:
        kind: The connection kind to list, or None for
            :data:`DEFAULT_CONNECTION_KIND`. :data:`CONNECTION_KIND_ALL`
            lists every kind, the MCP list first.

    Returns:
        A list of ``{"uri", "path", "kind", "caption", "color"}`` dicts,
        sorted by URI within each kind: the URI reported as
        :func:`list_connection_uris` reports it, the path
        :data:`ROOT_CONNECTION_PATH` for the top level, the kind the list it is
        in - half of what identifies it, since one URI can be in both - and the
        caption and color, ``""`` for none.
    """
    if (
        isinstance(kind, str)
        and kind.strip().lower() == CONNECTION_KIND_ALL
    ):
        return [
            connection
            for each in SUPPORTED_CONNECTION_KINDS
            for connection in list_connections_with_details(each)
        ]

    kind = normalize_connection_kind(kind)
    # Listed first: that is what runs the upgrade, which may write the file.
    uris = _list_stored_connections(kind)
    entries = _read_connections_file().get(kind, {})

    connections = []
    for uri in uris:
        details = _details_of(entries.get(uri, {}))
        connections.append(
            {
                "uri": with_default_scheme(uri),
                "path": details["path"] or ROOT_CONNECTION_PATH,
                "kind": kind,
                "caption": details["caption"],
                "color": details["color"],
            }
        )

    return sorted(connections, key=lambda connection: connection["uri"])


def list_stored_connection_uris(kind=None, mcp_user_id=None) -> list:
    """Returns the connection URIs of one kind exactly as they are stored.

    This is the KEY list: what comes back is what the secret store is keyed on,
    so it is what a password is read under and what a connection is deleted by.
    Connections configured before MariaDB Shell 26.9.3 are stored without a
    scheme, so the spellings here are not all of one form - use
    :func:`list_connection_uris` for the list to report, and
    :func:`resolve_connection_uri` to get from any spelling back to the key.

    Args:
        kind: The connection kind to list, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user whose connections to list, or None for the
            ``generic`` group.

    Returns:
        The sorted list of stored connection URIs of that kind, without the
        folder any of them is filed in.
    """
    return _list_stored_connections(kind, mcp_user_id)


def list_connection_uris(kind=None, mcp_user_id=None) -> list:
    """Returns the configured connection URIs of one kind, as they are named.

    The spelling to report and to hand out. A connection configured before the
    scheme was kept is stored without one (see
    :func:`list_stored_connection_uris`), and is reported with
    :data:`DEFAULT_CONNECTION_SCHEME` filled in - that names the same
    connection, and every lookup resolves either spelling back to the key it is
    stored under, so the only thing this changes is how the connection reads.

    Args:
        kind: The connection kind to list, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user whose connections to list, or None for the
            ``generic`` group.

    Returns:
        The sorted list of connection URIs of that kind that have a stored
        password.
    """
    return sorted(
        with_default_scheme(uri)
        for uri in list_stored_connection_uris(kind, mcp_user_id)
    )


def with_default_scheme(uri) -> str:
    """Returns the given URI with :data:`DEFAULT_CONNECTION_SCHEME` filled in.

    Textual and deliberately so: it is used on URIs that are already in the
    form they are meant to keep - a stored key being reported, a URI on its way
    to ``db.connect`` - where re-normalizing would change more than the scheme
    and, for a key this shell can no longer parse, would fail outright.

    Args:
        uri: The connection URI. Anything that is not a string is handed back
            untouched, for callers that pass one through on the way to a
            parser that will refuse it properly.

    Returns:
        The URI with a scheme, or what was given if it already had one.
    """
    if not isinstance(uri, str):
        return uri

    uri = uri.strip()
    if uri == "" or _SCHEME_PREFIX.match(uri):
        return uri

    return f"{DEFAULT_CONNECTION_SCHEME}://{uri}"


def parse_connection_uri(uri) -> Optional[dict]:
    """Returns a connection URI taken apart, with its scheme filled in.

    The one place a connection URI is parsed, so that everything reading one
    agrees on which spellings are acceptable. A URI that names no scheme gets
    :data:`DEFAULT_CONNECTION_SCHEME`, which is the protocol the shell would
    have opened it on anyway - written out so that a URI with the scheme and one
    without come apart the same way, and so that everything downstream can read
    the scheme instead of having to know the default.

    A scheme that IS named is kept, whatever it is: that is what makes
    ``mariadb+ssh://`` reach the shell at all, and what keeps ``mysqlx://`` a
    connection of its own.

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

    # Schemes are case-insensitive (RFC 3986) and the shell's parser is not, so
    # the prefix is lowered on the way in rather than a client being told that
    # `MariaDB://` is not a scheme.
    prefix = _SCHEME_PREFIX.match(uri)
    if prefix:
        uri = prefix.group(0).lower() + uri[prefix.end():]

    try:
        # A plain dict, so that what was parsed can be adjusted.
        connection_data = dict(_shell().parse_uri(uri))
    except Exception:  # noqa: BLE001 - not a URI, so not a connection either
        return None

    connection_data.setdefault("scheme", DEFAULT_CONNECTION_SCHEME)

    return connection_data


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
    left-out scheme, which is :data:`DEFAULT_CONNECTION_SCHEME` (so a URI
    written ``dba@host`` and one written ``mariadb://dba@host`` are one
    connection), a missing port, the case of the host, and a password written
    into the URI, which is never used - the stored one is. Anything a URI says
    over and above that is kept and has to match, so a URI naming a default
    schema, a connection option or a DIFFERENT scheme is NOT the same connection
    as one that does not: it would otherwise be answered with a connection that
    quietly does not do what it asked for, an option like ``ssl-mode=REQUIRED``
    or the ``+ssh`` tunnel included.

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
            # A socket given as an absolute path comes back as the HOST rather
            # than as a socket, and a port on it would be nonsense. No host name
            # carries a path separator, so this tells the two apart.
            and "/" not in host
            and "\\" not in host
            and connection_data.get("scheme") in PROTOCOL_SCHEMES
        ):
            # Left to the shell this would default to the same port; spelled
            # out, a URI with and one without the default port compare equal.
            # Only for the protocols above - another scheme has its own default.
            connection_data["port"] = DEFAULT_PORT

        # The shell's own rendering of the parsed URI: options in a fixed
        # order, percent-encoding and a trailing slash normalized.
        return _shell().unparse_uri(connection_data)
    except Exception:  # noqa: BLE001 - not a URI, so not a connection either
        return None


def _resolve_in_kind(uri, kind, mcp_user_id=None) -> Optional[str]:
    """Returns the configured URI of one kind that the given URI names.

    Args:
        uri: The connection URI to resolve.
        kind (str): The connection kind to look in, already normalized.
        mcp_user_id: The user whose connections to look in, or None for the
            ``generic`` group.

    Returns:
        The configured connection URI AS IT IS STORED, which is the key its
        password is under, or None if that list holds no connection the given
        URI names. That is why the stored list is what is searched: a
        connection configured before the scheme was kept is reported with one
        and stored without, and the key is what a caller needs back.

    Raises:
        mysqlsh.Error: If more than one connection in that list is - the same
            connection configured twice, under two spellings.
    """
    configured_uris = list_stored_connection_uris(kind, mcp_user_id)

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


def find_connection(uri, kinds=None, mcp_user_id=None) -> Optional[tuple]:
    """Returns the configured connection the given URI names, and its kind.

    The kind comes back with the URI because the URI on its own no longer
    identifies a connection: the same one can be in both lists under two
    passwords, and everything done with it afterwards - reading the password,
    re-validating it when a session is reopened - has to happen in the list it
    was found in.

    The lists are searched in the order they are given and the FIRST one holding
    a match wins, so a URI in both is resolved to one connection rather than
    being refused as ambiguous. Two spellings of one connection WITHIN a list
    are still refused, which is the case nobody can disambiguate (see
    :func:`_resolve_in_kind`).

    Args:
        uri: The connection URI to resolve.
        kinds: The connection kinds to search, in order. Defaults to
            :func:`usable_connection_kinds`, which is the GUI list before the
            MCP one where the server was started with ``--gui`` and the MCP list
            alone otherwise.
        mcp_user_id: The user whose connections to search, or None for the
            ``generic`` group.

    Returns:
        A ``(configured_uri, kind)`` tuple, or None if no configured connection
        in any of those lists is the one named.

    Raises:
        mysqlsh.Error: If one of the lists holds the same connection twice.
    """
    if kinds is None:
        kinds = usable_connection_kinds()

    for kind in kinds:
        kind = normalize_connection_kind(kind)
        configured_uri = _resolve_in_kind(uri, kind, mcp_user_id)
        if configured_uri is not None:
            return (configured_uri, kind)

    return None


def resolve_connection_uri(uri, kind=None, mcp_user_id=None) -> Optional[str]:
    """Returns the configured connection URI that the given URI names.

    The URI a client passes to ``db.connect`` does not have to be spelled
    exactly like the configured one, only name the same connection (see
    :func:`normalize_connection_uri`). What comes back is the configured URI,
    which is the key everything else uses: the password is read under it and the
    connection is opened, logged and re-validated on it.

    This looks in ONE list. Use :func:`find_connection` where the connection may
    be in either, which is what ``db.connect`` does.

    Args:
        uri: The connection URI to resolve.
        kind: The connection kind to look in, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user whose connections to look in, or None for the
            ``generic`` group.

    Returns:
        The configured connection URI, or None if no configured connection is
        the one named.

    Raises:
        mysqlsh.Error: If more than one configured connection is - the same
            connection configured twice, under two spellings.
    """
    return _resolve_in_kind(uri, normalize_connection_kind(kind), mcp_user_id)


def get_connection_password(uri: str, kind=None, mcp_user_id=None) -> str:
    """Returns the stored password for the given connection URI.

    Args:
        uri (str): The connection URI.
        kind: The connection kind it is stored under, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user it is stored for, or None for the ``generic``
            group.

    Returns:
        The stored password.
    """
    if mcp_user_id is None:
        upgrade_connection_keys()

    # A URI not stored fails with the shell's own error for a missing secret.
    return _shell().read_secret(
        _connection_key(uri, kind), *secret_options(mcp_user_id)
    )


def store_connection(
    uri: str,
    password: str,
    kind=None,
    path=None,
    caption=None,
    color=None,
    mcp_user_id=None,
) -> None:
    """Stores the password for the given connection URI, and its details.

    A plain write, under exactly the URI it is given. Anything CONFIGURING a
    connection - as opposed to restoring or moving one - follows it with
    :func:`drop_superseded_spellings`, which is what keeps one connection to one
    key.

    The password is written first and the details after, so a failure in
    between leaves a connection that is stored and merely shown at the top
    level.

    Args:
        uri (str): The connection URI.
        password (str): The password to store.
        kind: The connection kind to store it as, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        path: The folder to file it in (see :func:`normalize_connection_path`).
        caption: The caption to show for it.
        color: The color to show it in (one of :data:`CONNECTION_COLORS`).
            For each of the three, None keeps what the connection has, or
            none for a new connection - so replacing a password never moves a
            connection - and ``""`` clears it.
        mcp_user_id: The user to store it for, or None for the ``generic``
            group. A user's connection has no details, so giving one with a
            user is refused.

    Returns:
        None

    Raises:
        mysqlsh.Error: If the URI makes a key longer than
            :data:`MAX_CONNECTION_KEY_BYTES`, or a detail is not valid, before
            anything is written.
    """
    kind = normalize_connection_kind(kind)
    changes = _normalized_details(path, caption, color)
    if changes and mcp_user_id is not None:
        raise mysqlsh.Error(
            "A user's connection has no folder, caption or color: those are "
            "for the VS Code extension, which multi-tenant mode does not serve."
        )
    check_connection_key_length(uri, kind)
    if mcp_user_id is None:
        upgrade_connection_keys()

    _shell().store_secret(
        _connection_key(uri, kind), password, *secret_options(mcp_user_id)
    )

    if changes:
        set_connection_details(uri, kind, **changes)


def drop_superseded_spellings(uri, kind=None, mcp_user_id=None) -> list:
    """Deletes the connections that name the same one as the given URI.

    One connection has one key, and this is what holds that after a connection
    is configured. The case it exists for is not hypothetical: every connection
    configured before the scheme was kept is stored without one, so configuring
    it again writes the new key and would leave the old one sitting next to it -
    and the pair then resolves to NEITHER, since two spellings of one connection
    are refused as ambiguous (see :func:`_resolve_in_kind`).

    Called AFTER the new key is written, so a failure in between leaves the
    connection configured under one of the two rather than under neither.

    Unlike :func:`resolve_connection_uri` it does not raise when more than one
    matches: here the answer is to clear them all out rather than to refuse.

    Args:
        uri: The connection URI just stored, which is the one kept. It is never
            deleted, and neither is a configured key this shell cannot parse -
            an unparsable key cannot be shown to name the same connection, and
            guessing is worse than leaving it alone.
        kind: The connection kind to clear up, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user whose connections to clear up, or None for the
            ``generic`` group.

    Returns:
        The connection URIs that were deleted - empty in the ordinary case. A
        caller holding open connections on one of them has to close them, as it
        would for a deletion.
    """
    normalized = normalize_connection_uri(uri)
    if normalized is None:
        return []

    superseded = [
        configured_uri
        for configured_uri in list_stored_connection_uris(kind, mcp_user_id)
        if configured_uri != uri
        and normalize_connection_uri(configured_uri) == normalized
    ]

    if mcp_user_id is not None:
        # A user's connections have no details to pass on.
        for old_uri in superseded:
            delete_connection(old_uri, kind, mcp_user_id)

        return superseded

    # The connection kept is the one being configured, so how it was shown
    # goes with it - unless it has details of its own already.
    inherited = next(
        (
            details
            for details in (
                get_connection_details(old_uri, kind) for old_uri in superseded
            )
            if any(details.values())
        ),
        None,
    )
    if inherited and not any(get_connection_details(uri, kind).values()):
        set_connection_details(uri, kind, **inherited)

    for old_uri in superseded:
        delete_connection(old_uri, kind)

    return superseded


def delete_connection(uri: str, kind=None, mcp_user_id=None) -> None:
    """Deletes the stored password for the given connection URI, and its details.

    Args:
        uri (str): The connection URI.
        kind: The connection kind it is stored under, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        mcp_user_id: The user it is stored for, or None for the ``generic``
            group.

    Returns:
        None
    """
    kind = normalize_connection_kind(kind)
    if mcp_user_id is None:
        upgrade_connection_keys()

    # A URI not stored raises the shell's own error for a missing secret -
    # callers rely on that. The details go only once the secret has.
    _shell().delete_secret(_connection_key(uri, kind), *secret_options(mcp_user_id))
    if mcp_user_id is None:
        _drop_connection_details(uri, kind)


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

    Replaced whole and atomically: the file is an access control (the allowed
    paths, and whether the server is multi-tenant), so a reader must never see
    half of it.

    Args:
        settings (dict): The settings to persist.

    Returns:
        None
    """
    path = get_settings_file_path()
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file, indent=4)
    os.replace(temporary, path)


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


def is_path_allowed(path: str, mcp_user_id=None) -> bool:
    """Returns whether the given path is within an allowed directory.

    A path is allowed if it equals one of the allowed directories or is located
    inside one of them. If no allowed directories are configured, nothing is
    allowed.

    The one exception is a server started with ``--gui``, where EVERY path is
    allowed. The allow-list answers the question "should this client be trusted
    with this directory", and in GUI mode the client is a user interface the
    user is driving on this machine: the path it names is one the user just
    picked in VS Code, so there is nothing left to ask them. See
    :func:`mcp_plugin.lib.general.set_gui_mode`. This is the single chokepoint
    for that - both ``db.execute_sql_script`` and
    :func:`mcp_plugin.lib.general.require_allowed_path` come through here.

    In multi-tenant mode the list is the calling user's own (see
    :func:`mcp_plugin.lib.tenants.get_allowed_paths`), and a call that names
    no user is allowed nothing: the server-wide list belongs to single-tenant
    mode, and falling back to it would hand every user whatever it holds.

    Args:
        path (str): The path to check.
        mcp_user_id: The user asking, in multi-tenant mode.

    Returns:
        True if access to the path is allowed, False otherwise.
    """
    if general.is_gui_mode():
        return True

    if general.is_multi_tenant():
        if mcp_user_id is None:
            return False

        # Imported here: tenants imports this module.
        from mcp_plugin.lib import tenants

        allowed_paths = tenants.get_allowed_paths(mcp_user_id)
    else:
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
