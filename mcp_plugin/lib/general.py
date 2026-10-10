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

# cSpell:ignore mysqlsh MariaDB pydantic elicit uvicorn

# Define plugin version
import datetime
import ipaddress
import os
import pathlib
import sys
import time
from typing import NamedTuple, Optional

import mysqlsh

from mcp_plugin.lib.tool_registrar import tool_error

VERSION = "26.10.1"

# Default MCP server bind settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

# MCP transport settings
TRANSPORT_STREAMABLE_HTTP = "streamable-http"
TRANSPORT_STDIO = "stdio"
SUPPORTED_TRANSPORTS = (TRANSPORT_STREAMABLE_HTTP, TRANSPORT_STDIO)
DEFAULT_TRANSPORT = TRANSPORT_STREAMABLE_HTTP

# How long (in seconds) a database connection opened with db.connect may sit
# unused before its SESSION is closed automatically. The connection itself stays
# valid and opens a new session when it is used again. Only applied when serving
# over HTTP, where the server outlives the client that opened the connection; see
# mcp_plugin.lib.db_functions.
SESSION_IDLE_TIMEOUT = 1800

# How long (in seconds) a connection opened with db.connect stays valid AT ALL,
# counted from when it was opened and regardless of how much it is used. Where
# SESSION_IDLE_TIMEOUT only recycles the session behind a connection, this ends
# the connection: the UUID stops working and the client has to call db.connect
# again, which is what re-checks the URI against the configured connections.
#
# It is what bounds three things that are otherwise unbounded: how long a UUID
# is worth guessing, how long a connection removed with mcp.setup (or by
# sandbox.delete) can still be used, and how long the process keeps the record
# of a connection nobody ever closed. Twelve hours covers a working day's worth
# of interaction without a client ever noticing the limit, and still means that
# revoking a connection takes effect on its own rather than only when an operator
# restarts the server.
CONNECTION_MAX_LIFETIME = 43200

# How many connections opened with db.connect may be open at once, in total and
# per client. Without them, a loop of db.connect calls costs the caller nothing
# and the server a real database session each - held for up to
# SESSION_IDLE_TIMEOUT, which is enough to use up the server's max_connections -
# plus a record that is kept until the connection expires.
#
# The per-client limit is what stops one client from doing that; the total is
# what stops several from doing it together, or one client that the server cannot
# tell apart from several. Both are far above what a client legitimately needs -
# a connection is opened by an explicit tool call, and one that no longer serves
# a purpose is closed with db.close - and the error a refused call gets says so,
# so a client that has simply been forgetting to close its connections is told
# what to do rather than being cut off.
#
# Over stdio every request presents the same (empty) identity, so all of the
# connections there are one client's and the per-client limit is what applies.
MAX_CONNECTIONS_TOTAL = 64
MAX_CONNECTIONS_PER_CLIENT = 16

# How many connections one user may hold at once in multi-tenant mode, across
# every MCP session and client they use. The per-client limit does not bound
# that: a user can open as many MCP sessions as they like, each a client of its
# own. A server-side gateway such as Arcade is one such user per end user, so
# this is what keeps one of them from taking every connection the server has.
MAX_CONNECTIONS_PER_USER = 32

# The transport the MCP server is currently being served with, set by
# mcp_plugin.lib.server.start() before it starts serving. None while no server
# is running, which is also what the in-process tests see.
_active_transport = None

# Whether the server is being served for the MariaDB VS Code extension, set by
# mcp_plugin.lib.server.start() from the --gui option before it starts serving.
# False while no server is running, which is also what the in-process tests see.
_gui_mode = False

# Whether the server is serving several users, each authenticated and each with
# connections and allowed paths of their own, set by
# mcp_plugin.lib.server.start() from the configuration before the tools are
# built (see mcp_plugin.lib.tenants). False while no server is running.
_multi_tenant = False

# MCP function groups that can be loaded independently
FUNCTION_GROUP_DB = "db"
FUNCTION_GROUP_MSM = "msm"
FUNCTION_GROUP_SANDBOX = "sandbox"
# The migrator group is supported wherever the group list is concerned, but its
# tools register only where the migration tooling is actually installed - see
# mcp_plugin.lib.migrator_functions.register_migrator_tools.
FUNCTION_GROUP_MIGRATOR = "migrator"
# The dump, load, copy, export and import utilities, run as background tasks;
# its tools need connections, so they register only alongside the db group.
FUNCTION_GROUP_UTIL = "util"
SUPPORTED_FUNCTION_GROUPS = (
    FUNCTION_GROUP_DB,
    FUNCTION_GROUP_MSM,
    FUNCTION_GROUP_SANDBOX,
    FUNCTION_GROUP_MIGRATOR,
    FUNCTION_GROUP_UTIL,
)
DEFAULT_FUNCTION_GROUPS = SUPPORTED_FUNCTION_GROUPS

# The groups a multi-tenant server serves, and the only ones it may. The
# sandbox and migrator groups run local server processes and long jobs that
# write to the server's disk - resources one user would take from all the
# others - the util group's dumps and loads are such jobs too, and the msm
# group works on schema project folders, which live on the developer's own
# machine, not on a remote server. None of them are offered to tenants.
MULTI_TENANT_FUNCTION_GROUPS = (FUNCTION_GROUP_DB,)

def utc_timestamp() -> str:
    """Returns the current time as the files record it: ISO 8601, UTC, seconds."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def get_plugin_data_path() -> str:
    # Get msm plugin data folder, create if it does not exist yet
    mcm_plugin_data_path = os.path.abspath(
        mysqlsh.plugin_manager.general.get_shell_user_dir("plugin_data", "mcp_plugin")
    )
    pathlib.Path(mcm_plugin_data_path).mkdir(parents=True, exist_ok=True)

    return mcm_plugin_data_path


# Name of the directory the MySQL-to-MariaDB migration tooling is installed
# under (see :func:`mcp_plugin.lib.setup_migrator.download`). It sits in the
# user's data home rather than in this plugin's data directory: the tooling is a
# standalone program that outlives any one plugin install and that things other
# than this plugin may want to run, so it is installed where such a program
# belongs and not somewhere only mcp_plugin would think to look.
MIGRATOR_DIR_NAME = "mariadb-migrator"

# The release of the MySQL-to-MariaDB migration tooling that mcp.setup installs.
# It is the name of a release TAG in the tooling's repository, which is what the
# source archive is built from, so bumping this to a newer tag is all there is to
# installing a newer release.
#
# A pinned release rather than the main branch: what an installation contains is
# then a property of this plugin's version and not of the day it was set up, so
# two installations of the same plugin drive the same tooling, and a release that
# turns out to break something can be answered by pinning the one before it.
MIGRATOR_VERSION = "v1.5.0"


def get_data_home() -> str:
    """Returns the base directory user-specific program data belongs in.

    ``$XDG_DATA_HOME`` when it names an absolute path, and ``~/.local/share``
    otherwise, which is the default the XDG base directory specification gives
    it. A relative value is ignored rather than resolved against the current
    directory, as the specification requires.

    Returns:
        The absolute path of the data home, whether or not it exists yet.
    """
    data_home = os.environ.get("XDG_DATA_HOME", "")
    if data_home and os.path.isabs(data_home):
        return data_home

    return os.path.join(os.path.expanduser("~"), ".local", "share")


def get_bin_home() -> str:
    """Returns the directory user-installed executables belong in.

    ``~/.local/bin``, the companion of :func:`get_data_home` and the path
    systemd and the XDG user-dirs convention put user-installed programs on.
    There is no ``$XDG_BIN_HOME`` in the base directory specification to honour,
    so unlike the data home this is not configurable.

    Note this directory is NOT always on PATH - whether it is depends on the
    user's shell profile - so anything installed here has to say so rather than
    assume it is reachable.

    Returns:
        The absolute path of the user's binary directory, whether or not it
        exists yet.
    """
    return os.path.join(os.path.expanduser("~"), ".local", "bin")


def get_migrator_root() -> str:
    """Returns the directory the migration tooling's releases are installed under.

    One directory per installed release, named after the release (see
    :func:`get_migrator_path`), so that installing a different one neither
    disturbs nor is disturbed by what is already there.

    Returns:
        The absolute path of the migration tooling's root directory, whether or
        not it exists yet.
    """
    return os.path.join(get_data_home(), MIGRATOR_DIR_NAME)


def get_migrator_path(version: str = None) -> str:
    """Returns the directory a release of the migration tooling lives in.

    The release is part of the path rather than something recorded inside the
    install, which makes the directory name the one authoritative answer to
    which release a copy is - there is no second record to disagree with it -
    and lets releases sit side by side.

    Unlike :func:`get_plugin_data_path` this does NOT create the directory: it
    is the directory's existence that says whether that release has been
    downloaded at all.

    Args:
        version (str): The release to name the directory of. Defaults to the
            configured :data:`MIGRATOR_VERSION`.

    Returns:
        The absolute path of that release's directory, whether or not it exists
        yet.
    """
    return os.path.join(get_migrator_root(), version or MIGRATOR_VERSION)


# Name of the directory downloaded MariaDB server packages are installed under
# (see :mod:`mcp_plugin.lib.sandbox_servers`). Like the migration tooling above
# it sits outside this plugin's data directory: a server installation is a
# standalone program, several sandboxes made by different tools may want to run
# the same one, and a user looking for "where did that server go" should find it
# somewhere a server belongs.
SANDBOX_SERVER_DIR_NAME = "mariadb-sandbox-server"


def get_sandbox_server_root() -> str:
    """Returns the directory downloaded MariaDB server packages are installed under.

    One directory per version, named after it (see
    :func:`get_sandbox_server_path`), so that several versions sit side by side
    and a sandbox pinned to one is unaffected by another being added.

    This does NOT follow :func:`get_data_home` on Windows. The XDG base
    directory specification is a Unix convention and ``~/.local/share`` is not
    where a Windows user expects a program to be; unpacked programs belong under
    ``%LOCALAPPDATA%\\Programs`` there, which is where a per-user install of
    anything else on that machine already is. ``%LOCALAPPDATA%`` is read from
    the environment with ``~/AppData/Local`` as the fallback, because the
    variable is missing often enough - a service account, a stripped
    environment - that not having an answer then would be worse than guessing
    the default correctly.

    Returns:
        The absolute path of the server root, whether or not it exists yet.
    """
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            local_app_data = os.path.join(
                os.path.expanduser("~"), "AppData", "Local"
            )
        return os.path.join(local_app_data, "Programs", SANDBOX_SERVER_DIR_NAME)

    return os.path.join(get_data_home(), SANDBOX_SERVER_DIR_NAME)


def get_sandbox_server_path(version: str) -> str:
    """Returns the directory one MariaDB server version is installed in.

    The version is the directory name and there is no version file inside an
    installation, exactly as with :func:`get_migrator_path`: the path is then
    the one authoritative answer to which version a copy is, and no second
    record can disagree with it.

    This does NOT create the directory - its existence is what says the version
    has been downloaded.

    Args:
        version (str): The full ``major.minor.patch`` version.

    Returns:
        The absolute path of that version's directory, whether or not it exists
        yet.
    """
    return os.path.join(get_sandbox_server_root(), version)


def set_active_transport(transport) -> None:
    """Records the transport the MCP server is being served with.

    Read in exactly ONE place: ``db.connect`` refusing to open a connection at
    all when it cannot identify the client it would belong to, which only makes
    sense for a server reachable by more than one (see
    :mod:`mcp_plugin.lib.db_functions`). The idle-session reaper used to be the
    second reader; it is now started and stopped by
    :func:`mcp_plugin.lib.server.start`, which knows the transport from its own
    argument rather than from a global that outlives the server.

    It is deliberately NOT what decides whether an open connection is checked
    against the address it was opened from. That check is unconditional, so
    that it cannot be turned off by serving without going through
    :func:`mcp_plugin.lib.server.start`, or by a transport left recorded from a
    server that has already stopped.

    Args:
        transport (str): The transport being served, or None to reset.

    Returns:
        None
    """
    global _active_transport

    _active_transport = transport


def is_http_transport() -> bool:
    """Returns whether the server is being served over HTTP.

    Returns:
        True while a server is running with the streamable-http transport.
    """
    return _active_transport == TRANSPORT_STREAMABLE_HTTP


def set_gui_mode(enabled) -> None:
    """Records whether the server is being served for the VS Code extension.

    Set from the ``--gui`` option by :func:`mcp_plugin.lib.server.start`, before
    the tools are registered - :func:`mcp_plugin.lib.db_functions.register_db_tools`
    reads it to decide whether the connection-management tools exist at all, so
    a later change would leave a server advertising the wrong set of tools.

    GUI mode is a statement about WHO the client is, not about what it asked
    for: the extension is a user interface the user drives directly, on this
    machine, over stdio, so the two things it turns on are the two the
    allow-lists exist to ask an autonomous client about.

    * Every local path is accessible (see
      :func:`mcp_plugin.lib.config.is_path_allowed`). The extension opens files
      the user picked in VS Code's own file dialogs, so an allow-list would ask
      the user to confirm a choice they had just made.
    * The connection list can be written, not only read (see
      :func:`mcp_plugin.lib.db_functions.register_db_tools`), which is what lets
      the extension manage connections instead of sending the user to
      ``mcp.setup``.

    Both are a real widening of what a client can do, so it is deliberately not
    something a client can ask for over the protocol: it is decided once, on the
    command line that started the server.

    Args:
        enabled (bool): Whether the server is being served for the extension.

    Returns:
        None
    """
    global _gui_mode

    _gui_mode = bool(enabled)


def is_gui_mode() -> bool:
    """Returns whether the server is being served for the VS Code extension.

    Returns:
        True while a server is running that was started with ``--gui``.
    """
    return _gui_mode


def set_multi_tenant(enabled) -> None:
    """Records whether the server is serving several authenticated users.

    Set by :func:`mcp_plugin.lib.server.start` from the persisted configuration
    (see :func:`mcp_plugin.lib.tenants.is_multi_tenant`), before the tools are
    built. What it changes is read in a few places, each of which fails CLOSED
    when it cannot tell whose request it is serving:

    * every tool call has to come from an authenticated user, with the scope
      for the tool's group (see :mod:`mcp_plugin.lib.tool_registrar`);
    * the connections a user can list and open are their own (see
      :mod:`mcp_plugin.lib.db_functions`), and so are the paths they may use
      (see :func:`mcp_plugin.lib.config.is_path_allowed`);
    * a path that is not allowed is refused rather than offered to the client
      to trust (see :func:`require_allowed_path`): the client is the party the
      list restricts.

    Args:
        enabled (bool): Whether the server is multi-tenant.

    Returns:
        None
    """
    global _multi_tenant

    _multi_tenant = bool(enabled)


def is_multi_tenant() -> bool:
    """Returns whether the server is serving several authenticated users.

    Returns:
        True while a server is running in multi-tenant mode.
    """
    return _multi_tenant


# The token every form of a loopback address is normalized to. A client talking
# to a dual-stack server may be seen as ::1 on one request and 127.0.0.1 on the
# next while being the very same client, so all loopback forms have to compare
# equal. It is deliberately not a valid IP literal, so it can never collide with
# an address a client really connects from.
LOOPBACK_ADDRESS = "loopback"


def normalize_client_address(address) -> Optional[str]:
    """Returns a client address in the form used to compare addresses.

    The same client can be reported under more than one spelling of its
    address: an IPv4 client on a dual-stack socket arrives as the IPv4-mapped
    ``::ffff:a.b.c.d``, an IPv6 address can be written out uncompressed, and a
    local client shows up as either ``127.0.0.1`` or ``::1``. As the connection
    binding compares addresses for equality, they must all be reduced to one
    spelling first - otherwise the same client is mistaken for a different one.

    Anything that is not an IP address is passed through unchanged; it is then
    only ever compared with itself.

    Args:
        address (str): The address to normalize, or None.

    Returns:
        The normalized address, or None if there was none.
    """
    if address is None:
        return None

    address = address.strip()
    if not address:
        return None

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        # Not an IP address at all - a unix socket path, for instance.
        return address

    # ::ffff:a.b.c.d and a.b.c.d are the same host, addressed twice over.
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped

    if parsed.is_loopback:
        return LOOPBACK_ADDRESS

    # str() of a parsed address is its canonical (compressed) form.
    return str(parsed)


# The HTTP header carrying the MCP session id. Named here rather than imported
# from ``mcp.server.streamable_http`` because this module must stay importable
# without the optional ``mcp`` dependency; the name is fixed by the MCP
# specification.
MCP_SESSION_ID_HEADER = "mcp-session-id"


class ClientIdentity(NamedTuple):
    """Who a database connection belongs to.

    Every part is needed, and comparing two identities is a plain tuple
    equality - see
    :meth:`mcp_plugin.lib.db_functions._Connection.is_accessible_from`.

    Attributes:
        address: The normalized peer address the request came from, or None
            when the transport has none (stdio).
        session_id: The MCP session id the request was made on, or None when
            the transport has no sessions (stdio). For an authenticated user of
            a multi-tenant server it is the authorization instead (see
            :meth:`Principal.authorization`), and the address is None.
        user: The ``mcp_user_id`` of the authenticated user who made the
            request, or None where the server does not authenticate. Part of
            the same equality, so a connection one user opened is unusable by
            another even on the same address and MCP session - behind a gateway
            that serves many users from one address, this is the part that
            tells them apart.
    """

    address: Optional[str] = None
    session_id: Optional[str] = None
    user: Optional[str] = None


class Principal(NamedTuple):
    """The authenticated user a request is made by, in multi-tenant mode.

    Built from the access token the request was authenticated with, whichever
    way it was issued (see :mod:`mcp_plugin.lib.auth`), so the tools never have
    to know how a user signed in.

    Attributes:
        mcp_user_id: The user's id, which also names their secret group.
        scopes: The scopes the token grants, as a tuple.
        auth_method: How the user authenticated, for the log.
        grant_id: The grant of this server's own authorization server the
            token was issued under, or ``""``. Its login connection is the
            principal's (see :mod:`mcp_plugin.lib.oauth_builtin`).
        client_id: The OAuth client the token was issued to, or ``""``.
    """

    mcp_user_id: str
    scopes: tuple = ()
    auth_method: str = ""
    grant_id: str = ""
    client_id: str = ""

    def authorization(self) -> str:
        """Returns what a connection this principal opens is bound to.

        The grant the token was issued under - one user's authorization of one
        client - or, for a token without one (Keycloak, an API key), the client
        it was issued to. Not the MCP session, nor the address: a gateway such
        as Arcade opens a new session for every tool call, from whichever of its
        addresses, so a connection bound to either would be lost after the call
        that opened it. The user's other clients still cannot use it.
        """
        if self.grant_id:
            return f"grant:{self.grant_id}"

        return f"client:{self.client_id}"


# The claim of an access token that carries the mcp_user_id. Set by every token
# verifier in mcp_plugin.lib.auth, and read by get_principal.
MCP_USER_ID_CLAIM = "mcp_user_id"

# The claim of an access token that says how the user authenticated.
AUTH_METHOD_CLAIM = "auth_method"

# The claim of an access token naming the grant it was issued under.
GRANT_CLAIM = "grant"


def principal_from_access_token(token) -> Optional[Principal]:
    """Returns the principal an access token stands for.

    Args:
        token: The SDK's ``AccessToken``, or None.

    Returns:
        The :class:`Principal`, or None when there is no token or it carries
        no user.
    """
    claims = getattr(token, "claims", None) or {}
    mcp_user_id = claims.get(MCP_USER_ID_CLAIM)
    if not mcp_user_id:
        return None

    return Principal(
        str(mcp_user_id).lower(),
        tuple(getattr(token, "scopes", None) or ()),
        str(claims.get(AUTH_METHOD_CLAIM, "")),
        str(claims.get(GRANT_CLAIM, "") or ""),
        str(getattr(token, "client_id", "") or ""),
    )


def get_principal(ctx) -> Optional[Principal]:
    """Returns the authenticated user the current request was made by.

    Read from the request the transport attached to the context, where the
    SDK's bearer authentication middleware put the user it authenticated - the
    same chain :func:`get_client_address` reads, and NOT the SDK's context
    variable, so it also works for code that reads it before handing work to a
    worker thread.

    Args:
        ctx: The MCP request context, or None.

    Returns:
        The :class:`Principal`, or None when the request was not authenticated
        - always the case for a server that does not authenticate, and for
        stdio.
    """
    if ctx is None:
        return None

    try:
        request = getattr(ctx.request_context, "request", None)
    except Exception:  # noqa: BLE001 - no request context outside a request
        return None

    return principal_from_request(request)


def principal_from_request(request) -> Optional[Principal]:
    """Returns the authenticated user an HTTP request was made by.

    Args:
        request: The transport's request object, or None.

    Returns:
        The :class:`Principal`, or None when the request was not authenticated.
    """
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None

    return principal_from_access_token(
        getattr(scope.get("user"), "access_token", None)
    )


def normalize_client_identity(client) -> ClientIdentity:
    """Returns a client identity in the form used to compare identities.

    Applied when an identity is produced, when one is stored and when one is
    compared, so that no caller can reintroduce a mismatch by handing in a raw
    value. It is idempotent.

    Args:
        client (ClientIdentity): The identity to normalize, or None.

    Returns:
        The normalized identity; an empty one when there was none.
    """
    if client is None:
        return ClientIdentity()

    user = getattr(client, "user", None)

    return ClientIdentity(
        normalize_client_address(client.address),
        client.session_id or None,
        str(user).lower() if user else None,
    )


def get_client_session_id(ctx) -> Optional[str]:
    """Returns the MCP session id the current request was made on.

    The id is generated by the server when a client initializes its session and
    sent back by the client in the ``Mcp-Session-Id`` header of every later
    request, so unlike the peer address it is a secret the client has to have
    been told. The transport rejects a request naming a session that does not
    exist before any tool runs.

    Args:
        ctx: The MCP request context, or None.

    Returns:
        The session id, or None if the request has none - which is the case for
        stdio, where the one connection to the one client IS the session.
    """
    if ctx is None:
        return None

    try:
        request = getattr(ctx.request_context, "request", None)
    except Exception:  # noqa: BLE001 - no request context outside a request
        return None

    headers = getattr(request, "headers", None)
    if headers is None:
        return None

    return headers.get(MCP_SESSION_ID_HEADER) or None


def get_client_identity(ctx) -> ClientIdentity:
    """Returns the identity of the client that made the current request.

    Args:
        ctx: The MCP request context, or None.

    Returns:
        The normalized :class:`ClientIdentity`. All of its parts are None over
        stdio, where there is only ever the one client, and the user is None
        wherever the request was not authenticated.
    """
    principal = get_principal(ctx)
    if principal is not None and is_multi_tenant():
        return normalize_client_identity(
            ClientIdentity(None, principal.authorization(), principal.mcp_user_id)
        )

    return normalize_client_identity(
        ClientIdentity(
            get_client_address(ctx),
            get_client_session_id(ctx),
            principal.mcp_user_id if principal else None,
        )
    )


# What the log names a user by. Ids are never written to the log, not even in
# part: a connection id and an MCP session id are credentials - whoever holds one
# can use the connection it belongs to - and a user, token, grant or client id
# is no business of whoever reads the log either. A connection is named by the
# configured connection it was opened on, a client by its address and a user by
# the name their record has, if it has one.
def log_user_name(mcp_user_id) -> Optional[str]:
    """Returns the name a user is written to the log by.

    Args:
        mcp_user_id (str): The user, or None.

    Returns:
        The ``name`` of the user's record, or None if there is no such user, the
        user has no name or the users cannot be read.
    """
    if not mcp_user_id:
        return None

    # Imported lazily to avoid a circular import (tenants imports general).
    from mcp_plugin.lib import tenants

    try:
        record = tenants.get_user(mcp_user_id) or {}
    except Exception:  # noqa: BLE001 - logging must not break the caller
        return None

    return str(record.get("name") or "") or None


def log_user(mcp_user_id, unnamed="a user") -> str:
    """Returns a user in the form it is written to the log in.

    Args:
        mcp_user_id (str): The user, or None.
        unnamed (str): What to write for a user without a name.

    Returns:
        ``user='<name>'``, or ``unnamed`` when the user has no name.
    """
    name = log_user_name(mcp_user_id)

    return f"user='{name}'" if name else unnamed


def describe_client(client) -> str:
    """Returns a client identity in the form it is written to the log in.

    The address is written out in the normalized form it is compared in, so a
    log line and the binding it reports on cannot disagree. The session id is
    never written, being a secret the client was issued.

    Args:
        client (ClientIdentity): The identity to describe, or None.

    Returns:
        A one-line description; an address a request did not have (over stdio)
        is written as ``"-"``. The user is only written where there is one, and
        only if they have a name.
    """
    client = normalize_client_identity(client)

    description = f"address={client.address or '-'}"
    if client.user:
        user = log_user(client.user, unnamed="")
        if user:
            description += f" {user}"

    return description


def log_event(message) -> None:
    """Writes one line about a security-relevant event to stderr.

    What the MCP server does with the connections it hands out needs to leave a
    trace: a client refused the use of somebody else's connection is answered
    exactly as if that connection did not exist, so without a log entry an
    attempt to take one over cannot be seen at all - and a session that fails to
    close, or a reaper pass that fails, would otherwise be swallowed whole.

    Written to stderr, and to stderr in both transports: it is where the shell's
    own diagnostics and uvicorn's go, and in stdio mode stderr is the stream
    everything else is redirected to as well, precisely because the protocol
    owns stdout there (see :func:`mcp_plugin.lib.server._serve_stdio`). Printed
    rather than logged through :mod:`logging` so the lines appear whether or not
    anything has configured logging - under uvicorn an unconfigured logger would
    drop them at INFO level. One line per connection event, never one per
    request or per statement, so a stdio client that hands the server a pipe for
    its stderr (the MCP client library hands it its own) is not flooded with it.

    Never raises: logging is not worth failing a tool call over, and the idle
    reaper logs from inside its own ``except`` block, where an exception would
    end the thread.

    Args:
        message (str): The event to record.

    Returns:
        None
    """
    try:
        print(
            f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [mcp] {message}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:  # noqa: BLE001 - logging must not break the caller
        pass


# Every spelling a client may dial a loopback-bound server by. Used to build the
# Host/Origin allow list, which has to accept all of them: which one a client
# uses is its own choice, not the server's.
LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost", "[::1]")


def is_wildcard_host(host) -> bool:
    """Returns whether the given host binds every interface.

    A wildcard bind has no single name a client dials it by, so the allow list
    for the Host header cannot be derived from it (see
    :func:`mcp_plugin.lib.server._transport_security_settings`).

    Args:
        host (str): The host the server is asked to bind to.

    Returns:
        True for the "all interfaces" wildcards and for no host at all.
    """
    if not host:
        return True

    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_unspecified
    except ValueError:
        return False


def is_loopback_host(host) -> bool:
    """Returns whether binding to the given host keeps the server local.

    Anything this returns False for is reachable from outside the machine, and
    the MCP server has no authentication of its own (see
    :func:`mcp_plugin.lib.server._warn_if_reachable_from_the_network`).

    Args:
        host (str): The host the server is asked to bind to.

    Returns:
        True if the host is a loopback address or name, False for every address
        that is reachable from another machine - including the "all interfaces"
        wildcards, which are not loopback even though they include it.
    """
    if not host:
        # An empty host means "all interfaces" to the underlying socket.
        return False

    host = host.strip().strip("[]")

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an address: only the well-known local names count as loopback. A
        # name that resolves to a loopback address is deliberately not looked
        # up - the warning errs towards being shown.
        return host.lower() in ("localhost", "localhost.localdomain")


def get_client_address(ctx) -> Optional[str]:
    """Returns the IP address the current request was sent from.

    The address is taken from the transport's own request object, i.e. from the
    peer address of the TCP connection the request arrived on. It is not read
    from any header, as those are client-supplied and can be forged.

    That the request object really carries the peer address depends on the
    server being run with uvicorn's proxy-header handling disabled, which
    :func:`mcp_plugin.lib.server._serve_streamable_http` takes care of;
    otherwise uvicorn would overwrite it with the ``X-Forwarded-For`` header for
    every request coming from a trusted address - loopback included.

    The address is normalized (see :func:`normalize_client_address`) so that
    the one returned when a connection is opened and the one returned when it
    is used later can be compared for equality.

    Args:
        ctx: The MCP request context, or None.

    Returns:
        The client's normalized IP address, or None if the transport does not
        have one - which is the case for stdio, where the client is the parent
        process.
    """
    if ctx is None:
        return None

    try:
        # The HTTP transports attach the request they received to the context;
        # stdio has no request object to attach, and outside of a request the
        # context has no request context at all.
        request = getattr(ctx.request_context, "request", None)
    except Exception:  # noqa: BLE001 - no request context outside a request
        return None

    return normalize_client_address(
        getattr(getattr(request, "client", None), "host", None)
    )


async def require_allowed_path(ctx, path) -> None:
    """Ensures the given path is within a directory the MCP server may access.

    A value of ``None`` is left to the caller's own default handling. If the
    path is not yet allowed, the user is asked - via MCP elicitation - whether
    to trust it. On confirmation the path is added to the allowed paths on disk
    (see :func:`mcp_plugin.lib.config.add_allowed_path`) and the call returns
    normally; otherwise a ``ToolError`` is raised.

    In GUI mode every path is allowed, so this returns without eliciting
    anything and without writing the path to the allow-list - the check it
    performs is what
    :func:`mcp_plugin.lib.config.is_path_allowed` has already answered.

    In multi-tenant mode it is checked against the calling user's own allowed
    paths, and two things change. A path that is not allowed is refused
    outright, never offered to the client to trust: the client is the party
    the list restricts, and an administrator grants paths with ``mcp.setup``.
    And ``None`` is not left alone: it is checked as the server's working
    directory, which is a path like any other and has to be allowed.

    Args:
        ctx: The MCP request context, used to elicit confirmation from the
            user. May be ``None``, in which case no elicitation is attempted.
        path: The filesystem path to authorize, or ``None``.

    Returns:
        None
    """
    # Imported lazily to avoid a circular import (config imports general).
    from mcp_plugin.lib import config

    if is_multi_tenant():
        checked = os.getcwd() if path is None else path
        if config.is_path_allowed(checked, get_client_identity(ctx).user):
            return

        raise tool_error(
            f"Access to path '{checked}' is not allowed. Ask the administrator "
            "to add it (or a parent directory) to your allowed paths with "
            "mcp.setup."
        )

    if path is None:
        return

    if config.is_path_allowed(path):
        return

    if await _confirm_trust_path(ctx, path):
        config.add_allowed_path(path)
        return

    raise tool_error(
        f"Access to path '{path}' is not allowed. Add it (or a parent "
        "directory) to the allowed paths with mcp.setup."
    )


async def _confirm_trust_path(ctx, path) -> bool:
    """Asks the user, via MCP elicitation, whether to trust the given path.

    Args:
        ctx: The MCP request context, or ``None``.
        path: The filesystem path to ask about.

    Returns:
        True if the user confirmed the path should be trusted; False if the
        user declined or cancelled, or if the client does not support
        elicitation.
    """
    if ctx is None:
        return False

    from pydantic import BaseModel, Field

    class ConfirmTrustPath(BaseModel):
        trust: bool = Field(
            default=False,
            description=f"Add '{path}' to the allowed paths and continue?",
        )

    try:
        result = await ctx.elicit(
            message=(
                f"The path '{path}' is not in the MCP server's list of allowed "
                "paths. Trust it as an allowed path?"
            ),
            schema=ConfirmTrustPath,
        )
    except Exception:  # noqa: BLE001 - client may not support elicitation
        return False

    return result.action == "accept" and getattr(result.data, "trust", False)
