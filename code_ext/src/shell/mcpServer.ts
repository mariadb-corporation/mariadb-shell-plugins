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

import { AGENT_MCP_SERVER_ARGS, MCP_SERVER_ARGS } from "./constants.js";
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
 * @param client Who the server is for: this extension (`gui`, with
 *        `--gui`), or an autonomous agent such as Copilot (`agent`).
 *
 * @returns The command to spawn.
 */
export const buildMcpServerCommand = (
    location: ShellLocation,
    client: "gui" | "agent",
): McpServerCommand => {
    const args = client === "gui" ? MCP_SERVER_ARGS : AGENT_MCP_SERVER_ARGS;

    return { command: location.binaryPath, args: [...args] };
};
