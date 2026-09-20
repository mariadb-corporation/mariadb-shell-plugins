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

import { describe, expect, it } from "vitest";

import { McpSession } from "../../mcp/session.js";
import type { McpServerCommand } from "../../shell/mcpServer.js";
import { createFakeConnector, createRecordingLog } from "../helpers.js";

const command: McpServerCommand = {
    command: "/usr/local/bin/mariadb-shell",
    args: ["--", "mcp", "start-server", "--transport=stdio"],
};

describe("McpSession", () => {
    it("starts the server and exposes the API", async () => {
        const connector = createFakeConnector();
        const log = createRecordingLog();
        const session = new McpSession(connector, log);

        const api = await session.start(command);

        expect(connector.commands).toEqual([command]);
        expect(session.isRunning).toBe(true);
        expect(session.api).toBe(api);
        expect(log.lines[0]).toContain("Starting MCP server:");
        expect(log.lines).toContain("MCP server ready.");
    });

    it("forwards the server's log output", async () => {
        const connector = createFakeConnector();
        const log = createRecordingLog();

        await new McpSession(connector, log).start(command);

        expect(log.lines)
            .toContain("Serving /usr/local/bin/mariadb-shell");
    });

    it("starts only one server when asked twice", async () => {
        const connector = createFakeConnector();
        const session = new McpSession(connector, createRecordingLog());

        const first = await session.start(command);
        const second = await session.start(command);

        expect(connector.commands).toHaveLength(1);
        expect(second).toBe(first);
    });

    it("starts only one server for concurrent callers", async () => {
        const connector = createFakeConnector();
        const session = new McpSession(connector, createRecordingLog());

        // The tree, the toolbar and the panel all reach for the API at
        // once on a cold start; only one server may come of it.
        const [a, b, c] = await Promise.all([
            session.start(command),
            session.start(command),
            session.start(command),
        ]);

        expect(connector.commands).toHaveLength(1);
        expect(b).toBe(a);
        expect(c).toBe(a);
    });

    it("closes the connection on stop", async () => {
        const connector = createFakeConnector();
        const session = new McpSession(connector, createRecordingLog());

        await session.start(command);
        await session.stop();

        expect(connector.connections[0].closed).toBe(true);
        expect(session.isRunning).toBe(false);
        expect(session.api).toBeUndefined();
    });

    it("starts a fresh server after a stop", async () => {
        const connector = createFakeConnector();
        const session = new McpSession(connector, createRecordingLog());

        await session.start(command);
        await session.stop();
        await session.start(command);

        expect(connector.commands).toHaveLength(2);
        expect(session.isRunning).toBe(true);
    });

    it("is a no-op when stopped without being started", async () => {
        const connector = createFakeConnector();
        const session = new McpSession(connector, createRecordingLog());

        await expect(session.stop()).resolves.toBeUndefined();
        expect(connector.connections).toEqual([]);
    });

    it("logs, rather than throws, when closing fails", async () => {
        const connector = createFakeConnector();
        const log = createRecordingLog();
        const session = new McpSession(connector, log);

        await session.start(command);
        connector.connections[0].close = () => {
            return Promise.reject(new Error("pipe already gone"));
        };

        await expect(session.stop()).resolves.toBeUndefined();
        expect(log.lines.join("\n")).toContain("pipe already gone");
    });

    it("lets a failed start be retried", async () => {
        let attempt = 0;
        const connector = createFakeConnector();
        const inner = connector.open.bind(connector);
        connector.open = (cmd, onLog) => {
            attempt += 1;

            return attempt === 1
                ? Promise.reject(new Error("shell exited at once"))
                : inner(cmd, onLog);
        };

        const session = new McpSession(connector, createRecordingLog());

        await expect(session.start(command))
            .rejects.toThrow("shell exited at once");
        await expect(session.start(command)).resolves.toBeDefined();
    });
});
