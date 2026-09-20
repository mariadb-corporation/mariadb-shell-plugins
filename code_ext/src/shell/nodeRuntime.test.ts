/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
    createNodeMcpServerSpawner,
    createNodeProcessRunner,
    createNodeShellEnvironment,
} from "./nodeRuntime.js";

/**
 * These exercise the thin adapter that fronts the real file system and real
 * child processes, so they spawn actual processes. `process.execPath` stands
 * in for the shell binary: it is guaranteed to be there, it answers
 * `--version`, and it can be told to print and exit however a test needs.
 */

let sandbox: string;

beforeAll(async () => {
    sandbox = await fs.mkdtemp(path.join(os.tmpdir(), "mariadb-ext-"));
});

afterAll(async () => {
    await fs.rm(sandbox, { recursive: true, force: true });
});

describe("createNodeShellEnvironment", () => {
    it("reports the host platform and home directory", () => {
        const environment = createNodeShellEnvironment();

        expect(environment.platform).toBe(process.platform);
        expect(environment.homeDir).toBe(os.homedir());
    });

    it("captures the output of a --version call", async () => {
        const environment = createNodeShellEnvironment();

        const output = await environment.probeVersion(process.execPath);

        expect(output).toContain(process.version);
    });

    it("returns undefined for a binary that does not exist", async () => {
        const environment = createNodeShellEnvironment();

        await expect(
            environment.probeVersion(
                path.join(sandbox, "no-such-mariadb-shell"),
            ),
        ).resolves.toBeUndefined();
    });

    it("tells existing paths from missing ones", async () => {
        const environment = createNodeShellEnvironment();
        const file = path.join(sandbox, "present");
        await fs.writeFile(file, "");

        await expect(environment.pathExists(file)).resolves.toBe(true);
        await expect(environment.pathExists(path.join(sandbox, "absent")))
            .resolves.toBe(false);
    });

    it("lists only the sub directories of a prefix", async () => {
        const environment = createNodeShellEnvironment();
        const prefix = path.join(sandbox, "prefix");
        await fs.mkdir(path.join(prefix, "26.9.2"), { recursive: true });
        await fs.mkdir(path.join(prefix, "26.10.0"), { recursive: true });
        await fs.writeFile(path.join(prefix, "notes.txt"), "");

        const entries = await environment.listDirectories(prefix);

        expect(entries.sort()).toEqual(["26.10.0", "26.9.2"]);
    });

    it("returns an empty listing for a missing prefix", async () => {
        const environment = createNodeShellEnvironment();

        await expect(
            environment.listDirectories(path.join(sandbox, "nowhere")),
        ).resolves.toEqual([]);
    });
});

describe("createNodeProcessRunner", () => {
    it("streams output line by line and returns the exit code",
        async () => {
            const runner = createNodeProcessRunner();
            const lines: string[] = [];

            const code = await runner.run({
                command: process.execPath,
                args: [
                    "-e",
                    "process.stdout.write('==> Downloading\\n');"
                    + "process.stderr.write('==> Unpacking\\n');"
                    + "process.exit(0);",
                ],
            }, (line) => {
                lines.push(line);
            });

            expect(code).toBe(0);
            expect(lines.sort())
                .toEqual(["==> Downloading", "==> Unpacking"]);
        });

    it("reports a non-zero exit code", async () => {
        const runner = createNodeProcessRunner();

        const code = await runner.run({
            command: process.execPath,
            args: ["-e", "process.exit(7);"],
        }, () => {
            // No output expected.
        });

        expect(code).toBe(7);
    });

    it("emits a trailing line that has no newline", async () => {
        const runner = createNodeProcessRunner();
        const lines: string[] = [];

        await runner.run({
            command: process.execPath,
            args: ["-e", "process.stdout.write('==> Installed 26.9.2');"],
        }, (line) => {
            lines.push(line);
        });

        expect(lines).toEqual(["==> Installed 26.9.2"]);
    });

    it("rejects when the command cannot be spawned", async () => {
        const runner = createNodeProcessRunner();

        await expect(runner.run({
            command: path.join(sandbox, "no-such-interpreter"),
            args: [],
        }, () => {
            // No output expected.
        })).rejects.toThrow();
    });
});

describe("createNodeMcpServerSpawner", () => {
    it("surfaces the server's stderr and its exit code", async () => {
        const spawner = createNodeMcpServerSpawner();
        const lines: string[] = [];

        const child = spawner.spawn({
            command: process.execPath,
            args: [
                "-e",
                "process.stderr.write('MCP server ready\\n');"
                + "process.exit(0);",
            ],
        }, (line) => {
            lines.push(line);
        });

        await expect(child.exited).resolves.toBe(0);
        expect(lines).toContain("MCP server ready");
    });

    it("stops a running server when killed", async () => {
        const spawner = createNodeMcpServerSpawner();

        const child = spawner.spawn({
            command: process.execPath,
            // Stays alive until it is killed.
            args: ["-e", "setInterval(() => {}, 1000);"],
        }, () => {
            // No output expected.
        });

        child.kill();

        await expect(child.exited).resolves.toBeNull();
    });

    it("logs a failure to start rather than throwing", async () => {
        const spawner = createNodeMcpServerSpawner();
        const lines: string[] = [];

        const child = spawner.spawn({
            command: path.join(sandbox, "no-such-mariadb-shell"),
            args: ["--", "mcp", "start-server", "--transport=stdio"],
        }, (line) => {
            lines.push(line);
        });

        await child.exited;

        expect(lines.join("\n"))
            .toContain("Failed to start the MCP server");
    });
});
