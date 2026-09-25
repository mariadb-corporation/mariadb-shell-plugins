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
  :func:`normalize_connection_uri`). The URI carries its scheme - ``mariadb``
  unless it says otherwise - which is what lets a connection be configured
  through an SSH tunnel, as ``mariadb+ssh://``. Connections stored before that
  was so have no scheme in their key; they are reported with the default one
  filled in and resolve either way, so nothing has to be migrated.

  A connection may also sit in a *folder*, a path such as ``/Sandboxes`` or
  ``/Sandboxes/note_app``, which is written into the key in front of the URI:
  ``MCP:Connection:/Sandboxes:<uri>``. A connection at the top level has no
  path in its key, which is how every connection stored before folders existed
  reads, so again nothing is migrated. The folder is presentation for the VS
  Code extension and nothing else: a connection is still identified by its URI
  alone, one URI is in one folder per list, and outside GUI mode the folder is
  never reported (see :func:`list_connections_with_paths`).

  There are two lists of them, told apart by a *kind* and stored under a prefix
  of their own: :data:`CONNECTION_KIND_MCP`, the connections ``mcp.setup``
  curates and any MCP client may open, and :data:`CONNECTION_KIND_GUI`, the ones
  the MariaDB VS Code extension manages for itself. Every function here takes
  the kind and defaults it to the MCP list, which is what everything written
  before the GUI list existed means. The GUI list is only reachable at all from
  a server started with ``--gui`` (see
  :func:`mcp_plugin.lib.general.set_gui_mode`).
* **Allowed paths**: the local directories the MCP server is allowed to
  access. These are stored in a ``settings.json`` file inside the plugin data
  directory (see :func:`mcp_plugin.lib.general.get_mcp_plugin_data_path`).
"""

# cSpell:ignore mysqlsh MariaDB mysqlx unparse

import json
import os
import re
from typing import Optional

import mysqlsh

from mcp_plugin.lib import general

# Prefix used for the shell secrets that store MCP connection passwords.
CONNECTION_SECRET_PREFIX = "MCP:Connection:"

# Prefix used for the shell secrets that store the connection passwords the
# MariaDB VS Code extension manages. A separate prefix and not a flag inside the
# secret: the two lists have different owners and different reach. The MCP list
# is what an autonomous client may open and is curated with ``mcp.setup``; the
# GUI list is the user's own, added and removed from the extension while it
# runs. Keeping them apart means a connection the user made for the editor is
# not silently handed to whatever else drives this server, and neither list can
# be emptied by an operation meant for the other.
GUI_CONNECTION_SECRET_PREFIX = "GUI:Connection:"

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

# The folder a connection at the top level is reported in. Never written into
# a key: a top-level connection is stored as ``<prefix><uri>``, exactly as every
# connection was before folders existed.
ROOT_CONNECTION_PATH = "/"

# The folder sandbox.deploy files the connections it registers under.
SANDBOX_CONNECTION_PATH = "/Sandboxes"

# The characters a folder name may not hold. ``/`` separates folders, so it
# cannot also be part of one; ``:`` ends the path in a stored key, which is how
# the path and the URI after it are told apart.
_PATH_ELEMENT_FORBIDDEN = (":",)

# Name of the settings file inside the plugin data directory.
SETTINGS_FILE_NAME = "settings.json"

# Key used in settings.json for the list of allowed directories.
_ALLOWED_PATHS_KEY = "allowedPaths"


def _shell():
    """Returns the shell global object."""
    return mysqlsh.globals.shell


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
        mysqlsh.Error: If a folder name holds a ``:``, which would end the
            path early in the stored key, or the path is not a string.
    """
    if path is None:
        return ""

    if not isinstance(path, str):
        raise mysqlsh.Error(
            f"The connection folder must be a string such as '/Sandboxes', "
            f"not {path!r}."
        )

    elements = [element.strip() for element in path.split("/")]
    elements = [element for element in elements if element != ""]

    for element in elements:
        if any(char in element for char in _PATH_ELEMENT_FORBIDDEN):
            raise mysqlsh.Error(
                f"The folder name '{element}' contains a ':', which a folder "
                "name may not. Use '/' to put a folder inside another."
            )

    return "".join(f"/{element}" for element in elements)


def _split_connection_key(suffix) -> tuple:
    """Takes the part of a secret key after its prefix apart.

    Args:
        suffix (str): ``<uri>`` or ``/path:<uri>``. A URI never starts with a
            slash - it starts with a scheme or a user name - so a leading slash
            is what says a path comes first, and the first ``:`` after it ends
            the path, since no folder name may hold one.

    Returns:
        A ``(uri, path)`` tuple, the path ``""`` for the top level.
    """
    if suffix.startswith("/"):
        colon = suffix.find(":")
        if colon > 0:
            return (suffix[colon + 1:], suffix[:colon])

    return (suffix, "")


def _connection_key(uri, kind, path) -> str:
    """Returns the secret key a connection is stored under.

    Args:
        uri (str): The connection URI, as it is stored.
        kind: The connection kind.
        path (str): The folder, normalized; ``""`` for the top level.

    Returns:
        The key.
    """
    prefix = connection_secret_prefix(kind)

    return f"{prefix}{path}:{uri}" if path else f"{prefix}{uri}"


def _list_stored_connections(kind=None) -> list:
    """Returns every stored connection of one kind as ``(uri, path)`` pairs.

    Args:
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        The pairs, sorted by URI.
    """
    prefix = connection_secret_prefix(kind)

    return sorted(
        _split_connection_key(key[len(prefix):])
        for key in _shell().list_secrets()
        if key.startswith(prefix)
    )


def _stored_keys_of(uri, kind=None) -> list:
    """Returns the secret keys a stored URI is filed under.

    One, ordinarily. More only where the store was edited behind this plugin's
    back and one URI ended up in two folders.

    Args:
        uri (str): The connection URI, exactly as it is stored.
        kind: The connection kind.

    Returns:
        The keys, empty if the URI is not stored in that list.
    """
    return [
        _connection_key(stored_uri, kind, path)
        for stored_uri, path in _list_stored_connections(kind)
        if stored_uri == uri
    ]


def get_connection_path(uri, kind=None) -> str:
    """Returns the folder a stored connection is filed in.

    Args:
        uri (str): The connection URI, exactly as it is stored.
        kind: The connection kind, or None for :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        The folder, normalized; ``""`` for the top level or a URI not stored.
    """
    for stored_uri, path in _list_stored_connections(kind):
        if stored_uri == uri:
            return path

    return ""


def list_connections_with_paths(kind=None) -> list:
    """Returns the configured connections of one kind with their folders.

    What the VS Code extension lists in GUI mode. Nothing else reports a
    folder: outside GUI mode :func:`list_connection_uris` is the list, and the
    folders are invisible to an agent.

    Args:
        kind: The connection kind to list, or None for
            :data:`DEFAULT_CONNECTION_KIND`. :data:`CONNECTION_KIND_ALL`
            lists every kind, the MCP list first.

    Returns:
        A list of ``{"uri": ..., "path": ..., "kind": ...}`` dicts, sorted by
        URI within each kind: the URI reported as :func:`list_connection_uris`
        reports it, the path :data:`ROOT_CONNECTION_PATH` for the top level,
        and the kind the list it is in - half of what identifies it, since one
        URI can be in both.
    """
    if (
        isinstance(kind, str)
        and kind.strip().lower() == CONNECTION_KIND_ALL
    ):
        return [
            connection
            for each in SUPPORTED_CONNECTION_KINDS
            for connection in list_connections_with_paths(each)
        ]

    kind = normalize_connection_kind(kind)

    return sorted(
        (
            {
                "uri": with_default_scheme(uri),
                "path": path or ROOT_CONNECTION_PATH,
                "kind": kind,
            }
            for uri, path in _list_stored_connections(kind)
        ),
        key=lambda connection: connection["uri"],
    )


def list_stored_connection_uris(kind=None) -> list:
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

    Returns:
        The sorted list of stored connection URIs of that kind, without the
        folder any of them is filed in.
    """
    return sorted(uri for uri, _ in _list_stored_connections(kind))


def list_connection_uris(kind=None) -> list:
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

    Returns:
        The sorted list of connection URIs of that kind that have a stored
        password.
    """
    return sorted(
        with_default_scheme(uri) for uri in list_stored_connection_uris(kind)
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


def _resolve_in_kind(uri, kind) -> Optional[str]:
    """Returns the configured URI of one kind that the given URI names.

    Args:
        uri: The connection URI to resolve.
        kind (str): The connection kind to look in, already normalized.

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
    configured_uris = list_stored_connection_uris(kind)

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


def find_connection(uri, kinds=None) -> Optional[tuple]:
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
        configured_uri = _resolve_in_kind(uri, kind)
        if configured_uri is not None:
            return (configured_uri, kind)

    return None


def resolve_connection_uri(uri, kind=None) -> Optional[str]:
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

    Returns:
        The configured connection URI, or None if no configured connection is
        the one named.

    Raises:
        mysqlsh.Error: If more than one configured connection is - the same
            connection configured twice, under two spellings.
    """
    return _resolve_in_kind(uri, normalize_connection_kind(kind))


def get_connection_password(uri: str, kind=None) -> str:
    """Returns the stored password for the given connection URI.

    Args:
        uri (str): The connection URI.
        kind: The connection kind it is stored under, or None for
            :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        The stored password.
    """
    keys = _stored_keys_of(uri, kind)
    # A URI not stored is read at the top-level key, which is what fails with
    # the shell's own error for a missing secret.
    return _shell().read_secret(keys[0] if keys else _connection_key(uri, kind, ""))


def store_connection(uri: str, password: str, kind=None, path=None) -> None:
    """Stores the password for the given connection URI.

    A plain write, under exactly the URI it is given. Anything CONFIGURING a
    connection - as opposed to restoring or moving one - follows it with
    :func:`drop_superseded_spellings`, which is what keeps one connection to one
    key.

    One URI is in one folder, so storing it under a new folder MOVES it: the
    new key is written first and the old one deleted after, which leaves the
    connection configured under one of the two if anything fails in between.

    Args:
        uri (str): The connection URI.
        password (str): The password to store.
        kind: The connection kind to store it as, or None for
            :data:`DEFAULT_CONNECTION_KIND`.
        path: The folder to file it in (see :func:`normalize_connection_path`).
            None keeps the folder it is already in, or the top level for a new
            connection - so replacing a password never moves a connection.

    Returns:
        None
    """
    old_keys = _stored_keys_of(uri, kind)
    folder = (
        get_connection_path(uri, kind)
        if path is None
        else normalize_connection_path(path)
    )
    key = _connection_key(uri, kind, folder)

    _shell().store_secret(key, password)

    for old_key in old_keys:
        if old_key != key:
            _shell().delete_secret(old_key)


def drop_superseded_spellings(uri, kind=None) -> list:
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
        for configured_uri in list_stored_connection_uris(kind)
        if configured_uri != uri
        and normalize_connection_uri(configured_uri) == normalized
    ]

    for old_uri in superseded:
        delete_connection(old_uri, kind)

    return superseded


def delete_connection(uri: str, kind=None) -> None:
    """Deletes the stored password for the given connection URI.

    Args:
        uri (str): The connection URI.
        kind: The connection kind it is stored under, or None for
            :data:`DEFAULT_CONNECTION_KIND`.

    Returns:
        None
    """
    keys = _stored_keys_of(uri, kind)
    # A URI not stored goes to the top-level key, which is what raises the
    # shell's own error for a missing secret - callers rely on that.
    for key in keys or [_connection_key(uri, kind, "")]:
        _shell().delete_secret(key)


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

    The one exception is a server started with ``--gui``, where EVERY path is
    allowed. The allow-list answers the question "should this client be trusted
    with this directory", and in GUI mode the client is a user interface the
    user is driving on this machine: the path it names is one the user just
    picked in VS Code, so there is nothing left to ask them. See
    :func:`mcp_plugin.lib.general.set_gui_mode`. This is the single chokepoint
    for that - both ``db.execute_sql_script`` and
    :func:`mcp_plugin.lib.general.require_allowed_path` come through here.

    Args:
        path (str): The path to check.

    Returns:
        True if access to the path is allowed, False otherwise.
    """
    if general.is_gui_mode():
        return True

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
