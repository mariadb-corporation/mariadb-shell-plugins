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

/** How many of the server's last stderr lines a start failure quotes. */
const STDERR_LINES_KEPT = 5;

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

            // Attached before the process exists: the SDK hands out a
            // stream up front for exactly this, and a server that dies
            // while starting says why on stderr, before any handshake.
            const recent: string[] = [];
            const reader = new LineReader((line) => {
                recent.push(line);
                if (recent.length > STDERR_LINES_KEPT) {
                    recent.shift();
                }
                onLog(line);
            });
            // Typed as a bare Stream by the SDK, but `stderr: "pipe"` above
            // makes it a readable PassThrough onto the process's stderr.
            const stderr = transport.stderr as Readable | null;
            stderr?.setEncoding("utf8");
            stderr?.on("data", (chunk: string) => {
                reader.push(chunk);
            });

            const client = new Client(CLIENT_INFO, { capabilities: {} });
            try {
                await client.connect(transport);
            } catch (error) {
                reader.flush();
                const message = error instanceof Error
                    ? error.message
                    : String(error);
                const said = recent.length > 0
                    ? ` Its last output: ${recent.join(" | ")}`
                    : "";
                throw new Error(
                    `The MCP server did not start (${command.command}): `
                    + `${message}.${said}`,
                );
            }

            // Only a close nobody asked for is news: the calls that follow
            // will all fail, and this is the line that says why.
            let closing = false;
            client.onclose = () => {
                if (!closing) {
                    onLog("The MCP server exited unexpectedly. Run "
                        + "'MariaDB: Restart MCP Server' to start it again.");
                }
            };

            return {
                callTool: async (
                    name: string,
                    args: Record<string, unknown>,
                    timeoutMs?: number,
                ): Promise<IToolResult> => {
                    return await client.callTool(
                        { name, arguments: args },
                        undefined,
                        timeoutMs === undefined ? undefined : { timeout: timeoutMs },
                    ) as IToolResult;
                },

                close: async (): Promise<void> => {
                    closing = true;
                    await client.close();
                    reader.flush();
                },
            };
        },
    };
};
