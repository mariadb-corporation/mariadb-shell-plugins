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

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import {
    createNodeProcessRunner,
    createNodeShellEnvironment,
} from "../../shell/nodeRuntime.js";

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

    it("logs why a binary could not be probed", async () => {
        const lines: string[] = [];
        const environment = createNodeShellEnvironment((line) => {
            lines.push(line);
        });
        const missing = path.join(sandbox, "no-such-mariadb-shell");

        await environment.probeVersion(missing);

        expect(lines).toEqual([`"${missing} --version": not found.`]);
    });

    it.skipIf(process.platform === "win32")(
        "logs a binary that is not executable", async () => {
            const lines: string[] = [];
            const environment = createNodeShellEnvironment((line) => {
                lines.push(line);
            });
            const plain = path.join(sandbox, "plain-file");
            await fs.writeFile(plain, "");
            await fs.chmod(plain, 0o644);

            await environment.probeVersion(plain);

            expect(lines).toEqual(
                [`"${plain} --version": not executable (EACCES).`]);
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

    it.skipIf(process.platform === "win32")(
        "ends the command and what it started when aborted", async () => {
            const runner = createNodeProcessRunner();
            const controller = new AbortController();
            let grandchild: number | undefined;

            // What the installer looks like from here: a shell whose own
            // children - curl, bash - do the work.
            await expect(runner.run({
                command: "/bin/sh",
                args: ["-c", "sleep 30 & echo $!; wait"],
            }, (line) => {
                grandchild = Number(line);
                controller.abort();
            }, controller.signal)).rejects.toThrow("Cancelled.");

            expect(grandchild).toBeGreaterThan(0);
            await vi.waitFor(() => {
                expect(() => { process.kill(grandchild ?? 0, 0); })
                    .toThrow();
            });
        }, 10_000);
});
