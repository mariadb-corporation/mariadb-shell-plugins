/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

/**
 * The oldest MariaDB Shell this extension can talk to. The MCP plugin's
 * stdio transport and the tool set the extension relies on only exist from
 * this release onwards.
 *
 * 26.9.3 is the floor because of the connection URI. Its parser is the first
 * to accept the `mariadb://` scheme, and the MCP plugin now stores connections
 * with their scheme - which is the only way to ask for a `mariadb+ssh://`
 * tunnel. An older shell cannot parse the URIs this one stores.
 *
 * 26.9.4 is the first release whose MCP plugin carries what this extension
 * asked of it: `db.test_connection` for the connection editor, and the
 * per-statement results of `db.execute_sql_script` - `statement_index`,
 * `execution_time`, `error`, `warnings` and `stop_on_error`.
 */
export const MINIMUM_SHELL_VERSION = "26.9.4";

/** Name of the shell executable, without a platform specific extension. */
export const SHELL_BINARY_NAME = "mariadb-shell";

/**
 * Arguments that make the shell host the MCP server over stdin/stdout.
 *
 * `--gui` tells the server its client is this extension rather than an
 * autonomous agent, which is what gives it access to every local path - the
 * user picks those in VS Code's own dialogs, so the server's allowed-path
 * list has nothing left to confirm - and what makes the connection list
 * writable, through the `db.add_connection` and `db.delete_connection` tools
 * that only a `--gui` server serves.
 *
 * A shell whose MCP plugin predates the option ignores it rather than
 * failing, because the plugin function takes its options as a dictionary and
 * only reads the ones it knows. Such a server simply comes up without GUI
 * mode, so connection management is unavailable and paths go back to being
 * confirmed - which is why {@link MINIMUM_SHELL_VERSION} is the real gate.
 */
export const MCP_SERVER_ARGS = [
    "--",
    "mcp",
    "start-server",
    "--transport=stdio",
    "--gui",
];

export const INSTALL_SCRIPT_URL_POSIX =
    "https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.sh";

export const INSTALL_SCRIPT_URL_WINDOWS =
    "https://github.com/mariadb-corporation/mariadb-shell/raw/main/install.ps1";
