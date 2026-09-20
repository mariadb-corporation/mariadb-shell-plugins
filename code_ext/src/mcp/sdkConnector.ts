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

import type { Readable } from "node:stream";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import type { McpServerCommand } from "../shell/mcpServer.js";
import { LineReader } from "../shell/lineReader.js";
import type { IToolResult } from "./protocol.js";
import type { IMcpConnection, IMcpConnector } from "./session.js";

/** Identifies this extension to the server during the handshake. */
const CLIENT_INFO = { name: "mariadb-vscode", version: "26.9.0" };

/**
 * The real connector, backed by the MCP SDK's stdio transport.
 *
 * The transport spawns the shell itself and owns its stdin and stdout,
 * which carry the protocol; only stderr is ours to read, and it is where
 * the server's log output arrives.
 *
 * @returns A connector that starts servers with the MCP SDK.
 */
export const createSdkConnector = (): IMcpConnector => {
    return {
        open: async (
            command: McpServerCommand,
            onLog: (line: string) => void,
        ): Promise<IMcpConnection> => {
            const transport = new StdioClientTransport({
                command: command.command,
                args: [...command.args],
                stderr: "pipe",
            });

            const client = new Client(CLIENT_INFO, { capabilities: {} });
            await client.connect(transport);

            // Only available once the transport has spawned the process,
            // which connect() has just done.
            const reader = new LineReader(onLog);
            // Typed as a bare Stream by the SDK, but `stderr: "pipe"` above
            // makes it the process's stderr pipe, which is readable.
            const stderr = transport.stderr as Readable | null;
            stderr?.setEncoding("utf8");
            stderr?.on("data", (chunk: string) => {
                reader.push(chunk);
            });

            return {
                callTool: async (
                    name: string,
                    args: Record<string, unknown>,
                ): Promise<IToolResult> => {
                    return await client.callTool({
                        name,
                        arguments: args,
                    }) as IToolResult;
                },

                close: async (): Promise<void> => {
                    await client.close();
                    reader.flush();
                },
            };
        },
    };
};
