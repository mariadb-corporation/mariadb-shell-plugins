/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

/**
 * The oldest MariaDB Shell this extension can talk to. The MCP plugin's
 * stdio transport and the tool set the extension relies on only exist from
 * this release onwards.
 */
export const MINIMUM_SHELL_VERSION = "26.9.2";

/** Name of the shell executable, without a platform specific extension. */
export const SHELL_BINARY_NAME = "mariadb-shell";

/** Arguments that make the shell host the MCP server over stdin/stdout. */
export const MCP_SERVER_ARGS = [
    "--",
    "mcp",
    "start-server",
    "--transport=stdio",
];

export const INSTALL_SCRIPT_URL_POSIX =
    "https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.sh";

export const INSTALL_SCRIPT_URL_WINDOWS =
    "https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.ps1";
