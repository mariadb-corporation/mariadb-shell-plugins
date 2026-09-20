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

import { MCP_SERVER_ARGS } from "./constants.js";
import type { ShellLocation } from "./locator.js";

export interface McpServerCommand {
    command: string;
    args: string[];
}

/**
 * Builds the command that makes a shell host the MCP server over stdio.
 *
 * The process is spawned by the MCP client's stdio transport rather than
 * here, because its stdin and stdout carry the protocol and have to belong
 * to the transport from the first byte.
 *
 * @param location The shell to run.
 *
 * @returns The command to spawn.
 */
export const buildMcpServerCommand = (
    location: ShellLocation,
): McpServerCommand => {
    return { command: location.binaryPath, args: [...MCP_SERVER_ARGS] };
};
