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
import ipaddress
import os
import pathlib
import sys
import time
from typing import NamedTuple, Optional

import mysqlsh

VERSION = "2026.5.0"

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

# The transport the MCP server is currently being served with, set by
# mcp_plugin.lib.server.start() before it starts serving. None while no server
# is running, which is also what the in-process tests see.
_active_transport = None

# MCP function groups that can be loaded independently
FUNCTION_GROUP_DB = "db"
FUNCTION_GROUP_MSM = "msm"
FUNCTION_GROUP_SANDBOX = "sandbox"
SUPPORTED_FUNCTION_GROUPS = (
    FUNCTION_GROUP_DB,
    FUNCTION_GROUP_MSM,
    FUNCTION_GROUP_SANDBOX,
)
DEFAULT_FUNCTION_GROUPS = SUPPORTED_FUNCTION_GROUPS

def get_plugin_data_path() -> str:
    # Get msm plugin data folder, create if it does not exist yet
    mcm_plugin_data_path = os.path.abspath(
        mysqlsh.plugin_manager.general.get_shell_user_dir("plugin_data", "mcp_plugin")
    )
    pathlib.Path(mcm_plugin_data_path).mkdir(parents=True, exist_ok=True)

    return mcm_plugin_data_path


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

    Both parts are needed, and comparing two identities is a plain tuple
    equality - see
    :meth:`mcp_plugin.lib.db_functions._Connection.is_accessible_from`.

    Attributes:
        address: The normalized peer address the request came from, or None
            when the transport has none (stdio).
        session_id: The MCP session id the request was made on, or None when
            the transport has no sessions (stdio).
    """

    address: Optional[str] = None
    session_id: Optional[str] = None


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

    return ClientIdentity(
        normalize_client_address(client.address),
        client.session_id or None,
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
        The normalized :class:`ClientIdentity`. Both of its parts are None over
        stdio, where there is only ever the one client.
    """
    return normalize_client_identity(
        ClientIdentity(get_client_address(ctx), get_client_session_id(ctx))
    )


# How many leading characters of an id are written to the log. A connection id
# and an MCP session id are both credentials - whoever holds one can use the
# connection it belongs to - so neither is ever logged in full. A prefix is
# enough to recognize the lines belonging to one connection as one another's.
LOG_ID_PREFIX_LENGTH = 8


def log_id_prefix(value) -> str:
    """Returns as much of an id as may be written to the log.

    Args:
        value (str): The id to shorten, or None.

    Returns:
        The first :data:`LOG_ID_PREFIX_LENGTH` characters, marked as a prefix,
        or ``"-"`` when there is no id.
    """
    if not value:
        return "-"

    return f"{value[:LOG_ID_PREFIX_LENGTH]}..."


def describe_client(client) -> str:
    """Returns a client identity in the form it is written to the log in.

    The address is written out in the normalized form it is compared in, so a
    log line and the binding it reports on cannot disagree. The session id is
    only ever given as a prefix, being a secret the client was issued.

    Args:
        client (ClientIdentity): The identity to describe, or None.

    Returns:
        A one-line description; the parts a request did not have (both of them,
        over stdio) are written as ``"-"``.
    """
    client = normalize_client_identity(client)

    return (
        f"address={client.address or '-'} "
        f"session={log_id_prefix(client.session_id)}"
    )


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
    normally; otherwise a :class:`mysqlsh.Error` is raised.

    Args:
        ctx: The MCP request context, used to elicit confirmation from the
            user. May be ``None``, in which case no elicitation is attempted.
        path: The filesystem path to authorize, or ``None``.

    Returns:
        None
    """
    if path is None:
        return

    # Imported lazily to avoid a circular import (config imports general).
    from mcp_plugin.lib import config

    if config.is_path_allowed(path):
        return

    if await _confirm_trust_path(ctx, path):
        config.add_allowed_path(path)
        return

    raise mysqlsh.Error(
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
