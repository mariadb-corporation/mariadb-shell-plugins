/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { describe, expect, it } from "vitest";

import { createFakeSpawner, createRecordingLog } from "../test/helpers.js";
import { buildMcpServerCommand, McpServerController } from "./mcpServer.js";
import type { ShellLocation } from "./locator.js";

const location: ShellLocation = {
    binaryPath: "/usr/local/bin/mariadb-shell",
    version: { major: 26, minor: 9, patch: 2 },
    source: "path",
};

describe("buildMcpServerCommand", () => {
    it("starts the MCP server over stdio", () => {
        expect(buildMcpServerCommand(location)).toEqual({
            command: "/usr/local/bin/mariadb-shell",
            args: ["--", "mcp", "start-server", "--transport=stdio"],
        });
    });

    it("uses the located binary", () => {
        const managed: ShellLocation = {
            binaryPath:
                "/Users/mzinner/.local/share/mariadb-shell/26.9.2/bin"
                + "/mariadb-shell",
            version: { major: 26, minor: 9, patch: 2 },
            source: "managed",
        };

        expect(buildMcpServerCommand(managed).command)
            .toBe(managed.binaryPath);
    });

    it("hands out a fresh argument array each time", () => {
        const first = buildMcpServerCommand(location);
        first.args.push("--extra");

        expect(buildMcpServerCommand(location).args).toHaveLength(4);
    });
});

describe("McpServerController", () => {
    it("spawns the server and logs the command", () => {
        const spawner = createFakeSpawner();
        const log = createRecordingLog();
        const controller = new McpServerController(spawner, log);

        controller.start(location);

        expect(spawner.commands).toEqual([buildMcpServerCommand(location)]);
        expect(controller.isRunning).toBe(true);
        expect(log.lines[0]).toBe(
            "Starting MCP server: /usr/local/bin/mariadb-shell "
            + "-- mcp start-server --transport=stdio",
        );
    });

    it("forwards the server's output to the log", () => {
        const spawner = createFakeSpawner();
        const log = createRecordingLog();
        const controller = new McpServerController(spawner, log);

        controller.start(location);
        spawner.processes[0].emit("MCP server listening on stdio");

        expect(log.lines).toContain("MCP server listening on stdio");
    });

    it("kills the running server on stop", () => {
        const spawner = createFakeSpawner();
        const controller = new McpServerController(
            spawner,
            createRecordingLog(),
        );

        controller.start(location);
        controller.stop();

        expect(spawner.processes[0].killed).toBe(true);
        expect(controller.isRunning).toBe(false);
    });

    it("is a no-op when stopped twice", () => {
        const spawner = createFakeSpawner();
        const controller = new McpServerController(
            spawner,
            createRecordingLog(),
        );

        controller.start(location);
        controller.stop();
        controller.stop();

        expect(spawner.commands).toHaveLength(1);
    });

    it("replaces a running server when started again", () => {
        const spawner = createFakeSpawner();
        const controller = new McpServerController(
            spawner,
            createRecordingLog(),
        );

        controller.start(location);
        controller.start(location);

        expect(spawner.processes[0].killed).toBe(true);
        expect(spawner.processes[1].killed).toBe(false);
        expect(controller.isRunning).toBe(true);
    });

    it("notices when the server exits on its own", async () => {
        const spawner = createFakeSpawner();
        const log = createRecordingLog();
        const controller = new McpServerController(spawner, log);

        controller.start(location);
        spawner.processes[0].finish(3);
        await spawner.processes[0].exited;
        await Promise.resolve();

        expect(controller.isRunning).toBe(false);
        expect(log.lines).toContain("MCP server exited with code 3.");
    });

    it("keeps the new server when a restarted one reports its exit",
        async () => {
            const spawner = createFakeSpawner();
            const controller = new McpServerController(
                spawner,
                createRecordingLog(),
            );

            controller.start(location);
            controller.start(location);
            // The first process only now reports that it is gone.
            await spawner.processes[0].exited;
            await Promise.resolve();

            expect(controller.isRunning).toBe(true);
        });
});
