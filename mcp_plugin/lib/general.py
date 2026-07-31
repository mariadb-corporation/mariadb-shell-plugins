# Copyright (c) 2026, MariaDB plc and/or its affiliates.
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


async def require_allowed_path(ctx, path) -> None:
    """Ensures the given path is within a directory the MCP server may access.

    A value of ``None`` is left to the caller's own default handling. If the
    path is not yet allowed, the user is asked - via MCP elicitation - whether
    to trust it. On confirmation the path is added to the allowed paths on disk
    (see :func:`mcp_plugin.lib.config.add_allowed_path`) and the call returns
    normally; otherwise a :class:`mysqlsh.Error` is raised.

    Args:
        ctx: The FastMCP request context, used to elicit confirmation from the
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
        ctx: The FastMCP request context, or ``None``.
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
