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

# cSpell:ignore mysqlsh MariaDB pydantic elicit

# Define plugin version
import os
import pathlib
from typing import Optional

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
# unused before it is closed automatically. Only applied when serving over
# HTTP, where the server outlives the client that opened the connection; see
# mcp_plugin.lib.db_functions.
SESSION_IDLE_TIMEOUT = 1800

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

    The transport decides whether the connection safeguards that only make
    sense for a server reachable over the network are applied - binding a
    database connection to the client address that opened it and closing it
    when it has been unused for too long (see
    :mod:`mcp_plugin.lib.db_functions`).

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


def get_client_address(ctx) -> Optional[str]:
    """Returns the IP address the current request was sent from.

    The address is taken from the transport's own request object, i.e. from the
    peer address of the TCP connection the request arrived on. It is not read
    from any header, as those are client-supplied and can be forged.

    Args:
        ctx: The MCP request context, or None.

    Returns:
        The client's IP address, or None if the transport does not have one -
        which is the case for stdio, where the client is the parent process.
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

    return getattr(getattr(request, "client", None), "host", None)


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
