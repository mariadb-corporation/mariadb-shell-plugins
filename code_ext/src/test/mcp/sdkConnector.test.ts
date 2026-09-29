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

import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { MariaDbApi } from "../../mcp/mariaDbApi.js";
import { createSdkConnector } from "../../mcp/sdkConnector.js";
import type { McpServerCommand } from "../../shell/mcpServer.js";

/**
 * These run the connector against a real MCP server over a real stdio
 * pipe - a Node script standing in for the shell - so the transport, the
 * handshake and the stderr plumbing are all exercised for real.
 */

const fakeServer = fileURLToPath(
    new URL("../fixtures/fakeMcpServer.mjs", import.meta.url),
);

const command: McpServerCommand = {
    command: process.execPath,
    args: [fakeServer],
};

describe("createSdkConnector", () => {
    it("connects and calls a tool", async () => {
        const connection = await createSdkConnector()
            .open(command, () => {
                // Log output is checked separately.
            });

        try {
            const result = await connection.callTool(
                "db.list_connections", {});

            expect(result.content).toEqual([
                { type: "text", text: "dba@localhost:3310" },
                { type: "text", text: "app@localhost:3311" },
            ]);
        } finally {
            await connection.close();
        }
    }, 30_000);

    it("carries the typed API over the real transport", async () => {
        const connection = await createSdkConnector()
            .open(command, () => {
                // Log output is checked separately.
            });

        try {
            await expect(new MariaDbApi(connection).listConnections())
                .resolves.toEqual([
                    "dba@localhost:3310",
                    "app@localhost:3311",
                ]);
        } finally {
            await connection.close();
        }
    }, 30_000);

    it("forwards the server's stderr line by line", async () => {
        const lines: string[] = [];
        const connection = await createSdkConnector()
            .open(command, (line) => {
                lines.push(line);
            });

        try {
            await vi.waitFor(() => {
                expect(lines).toContain("fake MCP server listening on stdio");
            });
        } finally {
            await connection.close();
        }
    }, 30_000);

    it("surfaces a tool error as a failed result", async () => {
        const connection = await createSdkConnector()
            .open(command, () => {
                // Log output is checked separately.
            });

        try {
            const result = await connection.callTool("db.nope", {});

            expect(result.isError).toBe(true);
        } finally {
            await connection.close();
        }
    }, 30_000);

    it("gives up on a call that outlasts the timeout it was given", async () => {
        const connection = await createSdkConnector()
            .open(command, () => {
                // Log output is checked separately.
            });

        try {
            // Far short of the transport's own minute, which is the point:
            // the timeout passed is the one that applies.
            await expect(connection.callTool("test.slow", {}, 100))
                .rejects.toThrow(/timed out/i);
        } finally {
            await connection.close();
        }
    }, 30_000);

    it("rejects when the server cannot be started", async () => {
        await expect(createSdkConnector().open(
            { command: "/nonexistent/mariadb-shell", args: [] },
            () => {
                // Nothing will be logged.
            },
        )).rejects.toThrow("The MCP server did not start "
            + "(/nonexistent/mariadb-shell)");
    }, 30_000);

    it("says what a server that died on the way up printed", async () => {
        const lines: string[] = [];

        await expect(createSdkConnector().open(
            {
                command: process.execPath,
                args: [
                    "-e",
                    "process.stderr.write('No module named mcp\\n');"
                    + "process.exit(3);",
                ],
            },
            (line) => { lines.push(line); },
        )).rejects.toThrow(/Its last output: No module named mcp/);
        expect(lines).toEqual(["No module named mcp"]);
    }, 30_000);

    it("logs a server that exits without being asked to", async () => {
        const lines: string[] = [];
        const connection = await createSdkConnector()
            .open(command, (line) => { lines.push(line); });

        await connection.callTool("test.exit", {}).catch(() => {
            // The call dies with the server; that is the point.
        });

        await vi.waitFor(() => {
            expect(lines).toContain("The MCP server exited unexpectedly. "
                + "Run 'MariaDB: Restart MCP Server' to start it again.");
        });
    }, 30_000);

    it("does not call a close it asked for unexpected", async () => {
        const lines: string[] = [];
        const connection = await createSdkConnector()
            .open(command, (line) => { lines.push(line); });

        await connection.close();

        expect(lines.join("\n")).not.toContain("unexpectedly");
    }, 30_000);
});
