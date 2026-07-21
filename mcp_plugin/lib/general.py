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

def get_mcp_plugin_data_path() -> str:
    # Get msm plugin data folder, create if it does not exist yet
    mcm_plugin_data_path = os.path.abspath(
        mysqlsh.plugin_manager.general.get_shell_user_dir("plugin_data", "mcp_plugin")
    )
    pathlib.Path(mcm_plugin_data_path).mkdir(parents=True, exist_ok=True)

    return mcm_plugin_data_path
