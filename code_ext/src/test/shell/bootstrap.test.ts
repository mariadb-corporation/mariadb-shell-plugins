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

import {
    createFakeEnvironment,
    createFakeRunner,
    createRecordingLog,
    type FakeEnvironmentOptions,
} from "../helpers.js";
import { ensureShell } from "../../shell/bootstrap.js";
import {
    buildInstallCommand,
    type ProgressHost,
} from "../../shell/installer.js";

const MINIMUM = "26.9.3";
const PREFIX = "/Users/mzinner/.local/share/mariadb-shell";
const MANAGED_BINARY = `${PREFIX}/26.9.3/bin/mariadb-shell`;

/**
 * Builds the `--version` line the shell prints.
 *
 * @param version The version to report.
 *
 * @returns A realistic version line.
 */
const versionLine = (version: string): string => {
    return `mariadb-shell   Ver ${version} for osx10.21 on arm64 `
        + "- for MariaDB 13.1.0-MariaDB (Source distribution)";
};

/**
 * Builds a progress host that records what it was shown.
 *
 * @returns The host and its recordings.
 */
const createRecordingProgress = (): ProgressHost & {
    titles: string[];
    messages: string[];
} => {
    const titles: string[] = [];
    const messages: string[] = [];

    return {
        titles,
        messages,
        withProgress: async <T>(
            title: string,
            task: (
                report: (message: string) => void,
                signal: AbortSignal,
            ) => Promise<T>,
        ) => {
            titles.push(title);

            return await task((message) => {
                messages.push(message);
            }, new AbortController().signal);
        },
    };
};

describe("ensureShell", () => {
    it("uses a shell that is already on the PATH", async () => {
        const environment = createFakeEnvironment({
            versions: { "mariadb-shell": versionLine("26.9.3") },
        });
        const runner = createFakeRunner();
        const log = createRecordingLog();

        const result = await ensureShell({
            environment,
            runner,
            progress: createRecordingProgress(),
            log,
            minimumVersion: MINIMUM,
        });

        expect(result.installed).toBe(false);
        expect(result.location.source).toBe("path");
        // Nothing was downloaded.
        expect(runner.calls).toEqual([]);
        expect(log.lines.at(-1)).toContain("found on the PATH");
    });

    it("uses a local installation when the PATH has no usable shell",
        async () => {
            const environment = createFakeEnvironment({
                directories: { [PREFIX]: ["26.9.3"] },
                files: [MANAGED_BINARY],
                versions: { [MANAGED_BINARY]: versionLine("26.9.3") },
            });
            const runner = createFakeRunner();

            const result = await ensureShell({
                environment,
                runner,
                progress: createRecordingProgress(),
                log: createRecordingLog(),
                minimumVersion: MINIMUM,
            });

            expect(result.installed).toBe(false);
            expect(result.location).toEqual({
                binaryPath: MANAGED_BINARY,
                version: { major: 26, minor: 9, patch: 3 },
                source: "managed",
            });
            expect(runner.calls).toEqual([]);
        });

    it("installs the shell when neither the PATH nor the local "
        + "installation has one", async () => {
        // The installer's side effect: the version directory appears.
        const state: FakeEnvironmentOptions = {
            directories: { [PREFIX]: [] },
            files: [],
            versions: {},
        };
        const environment = createFakeEnvironment(state);
        // Rebuild the environment's answers once the installer has run by
        // swapping the tables the fake reads from.
        const installed = createFakeEnvironment({
            directories: { [PREFIX]: ["26.9.3"] },
            files: [MANAGED_BINARY],
            versions: { [MANAGED_BINARY]: versionLine("26.9.3") },
        });

        const runner = createFakeRunner([
            "==> Downloading",
            "==> Unpacking into /Users/mzinner/.local/share/mariadb-shell",
        ]);
        const progress = createRecordingProgress();

        const live = {
            ...environment,
            pathExists: (target: string) => {
                return runner.calls.length > 0
                    ? installed.pathExists(target)
                    : environment.pathExists(target);
            },
            listDirectories: (target: string) => {
                return runner.calls.length > 0
                    ? installed.listDirectories(target)
                    : environment.listDirectories(target);
            },
            probeVersion: (binaryPath: string) => {
                return runner.calls.length > 0
                    ? installed.probeVersion(binaryPath)
                    : environment.probeVersion(binaryPath);
            },
        };

        const log = createRecordingLog();
        const result = await ensureShell({
            environment: live,
            runner,
            progress,
            log,
            minimumVersion: MINIMUM,
        });

        expect(runner.calls).toEqual([
            buildInstallCommand("darwin", MINIMUM),
        ]);
        expect(result.installed).toBe(true);
        expect(result.location.binaryPath).toBe(MANAGED_BINARY);
        expect(progress.titles)
            .toEqual(["Installing MariaDB Shell 26.9.3"]);
        expect(progress.messages).toContain("Downloading");
        expect(progress.messages).toContain(
            "Unpacking into /Users/mzinner/.local/share/mariadb-shell",
        );
        expect(log.lines.join("\n"))
            .toContain("No MariaDB Shell 26.9.3 or newer");
    });

    it("installs the shell when the PATH version is too old", async () => {
        const environment = createFakeEnvironment({
            versions: { "mariadb-shell": versionLine("26.8.0") },
        });
        const runner = createFakeRunner();

        await expect(ensureShell({
            environment,
            runner,
            progress: createRecordingProgress(),
            log: createRecordingLog(),
            minimumVersion: MINIMUM,
        })).rejects.toThrow(/no MariaDB Shell 26.9.3 or newer could be found/);

        expect(runner.calls).toEqual([
            buildInstallCommand("darwin", MINIMUM),
        ]);
    });

    it("reports a failing installer", async () => {
        const environment = createFakeEnvironment();
        const runner = createFakeRunner(["install.sh: no curl"], 127);

        await expect(ensureShell({
            environment,
            runner,
            progress: createRecordingProgress(),
            log: createRecordingLog(),
            minimumVersion: MINIMUM,
        })).rejects.toThrow(
            "MariaDB Shell 26.9.3 could not be installed: no curl "
            + "(the installer exited with code 127).",
        );
    });

    it("says in the log where it looked and why nothing there would do",
        async () => {
            const environment = createFakeEnvironment({
                env: { PATH: "/usr/bin:/opt/homebrew/bin" },
                directories: { [PREFIX]: ["26.8.0", "26.9.3"] },
                files: [MANAGED_BINARY],
                versions: { "mariadb-shell": versionLine("26.9.1") },
            });
            const log = createRecordingLog();

            await expect(ensureShell({
                environment,
                runner: createFakeRunner([], 0),
                progress: createRecordingProgress(),
                log,
                minimumVersion: MINIMUM,
            })).rejects.toThrow(/could be found afterwards in/);

            const text = log.lines.join("\n");
            expect(text).toContain(
                "Looking for mariadb-shell on the PATH: "
                + "/usr/bin:/opt/homebrew/bin");
            expect(text).toContain("mariadb-shell on the PATH is 26.9.1, "
                + "older than the 26.9.3 this extension needs; not used.");
            expect(text).toContain("26.8.0 is older than 26.9.3; not used.");
            expect(text).toContain(`${MANAGED_BINARY} could not be run; `
                + "not used.");
            expect(text).toContain("The installer exited with code 0");
        });

    it("says when it starts the installer", async () => {
        const phases: string[] = [];

        await expect(ensureShell({
            environment: createFakeEnvironment(),
            runner: createFakeRunner([], 1),
            progress: createRecordingProgress(),
            log: createRecordingLog(),
            minimumVersion: MINIMUM,
            onInstalling: () => { phases.push("installing"); },
        })).rejects.toThrow();

        expect(phases).toEqual(["installing"]);
    });

    it("uses the PowerShell installer on Windows", async () => {
        const environment = createFakeEnvironment({
            platform: "win32",
            homeDir: "C:\\Users\\mzinner",
            env: { LOCALAPPDATA: "C:\\Users\\mzinner\\AppData\\Local" },
        });
        const runner = createFakeRunner();

        await expect(ensureShell({
            environment,
            runner,
            progress: createRecordingProgress(),
            log: createRecordingLog(),
            minimumVersion: MINIMUM,
        })).rejects.toThrow();

        expect(runner.calls[0].command).toBe("powershell.exe");
    });

    it("defaults to the shipped minimum version", async () => {
        const environment = createFakeEnvironment();
        const runner = createFakeRunner();

        await expect(ensureShell({
            environment,
            runner,
            progress: createRecordingProgress(),
            log: createRecordingLog(),
        })).rejects.toThrow();

        expect(runner.calls[0].args.at(-1))
            .toContain("MARIADB_SHELL_TAG=v26.9.4");
    });
});
