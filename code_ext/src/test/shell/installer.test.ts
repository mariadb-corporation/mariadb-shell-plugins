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

import { createFakeRunner } from "../helpers.js";
import {
    buildInstallCommand,
    installerProgressMessage,
    installShell,
    type ProgressHost,
} from "../../shell/installer.js";

/**
 * Builds a progress host that records the titles and messages it was given.
 *
 * @returns The host and the recordings it collects.
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
            task: (report: (message: string) => void) => Promise<T>,
        ) => {
            titles.push(title);

            return await task((message) => {
                messages.push(message);
            });
        },
    };
};

describe("buildInstallCommand", () => {
    it("builds the documented curl-into-bash line on macOS", () => {
        const command = buildInstallCommand("darwin", "26.9.2");

        expect(command.command).toBe("/bin/sh");
        expect(command.args[0]).toBe("-c");
        expect(command.args[1]).toBe(
            "curl -fsSL https://github.com/mariadb-corporation/"
            + "mariadb-shell/raw/main/install.sh "
            + "| MARIADB_SHELL_TAG=v26.9.2 bash",
        );
    });

    it("builds the same line on Linux", () => {
        expect(buildInstallCommand("linux", "26.9.2"))
            .toEqual(buildInstallCommand("darwin", "26.9.2"));
    });

    it("builds the PowerShell line on Windows", () => {
        const command = buildInstallCommand("win32", "26.9.2");

        expect(command.command).toBe("powershell.exe");
        expect(command.args).toContain("-NoProfile");
        expect(command.args).toContain("-ExecutionPolicy");
        expect(command.args.at(-1)).toBe(
            "$env:MARIADB_SHELL_TAG = 'v26.9.2'; "
            + "irm https://github.com/mariadb-corporation/mariadb-shell"
            + "/raw/main/install.ps1 | iex",
        );
    });

    it("pins the tag that was asked for", () => {
        expect(buildInstallCommand("darwin", "27.0.0").args[1])
            .toContain("MARIADB_SHELL_TAG=v27.0.0");
    });
});

describe("installerProgressMessage", () => {
    it("picks up the installer's own progress markers", () => {
        expect(installerProgressMessage("==> Downloading"))
            .toBe("Downloading");
        expect(installerProgressMessage("==> Unpacking into /opt/x"))
            .toBe("Unpacking into /opt/x");
        expect(installerProgressMessage("  ==> Verifying checksum  "))
            .toBe("Verifying checksum");
    });

    it("ignores everything else", () => {
        expect(installerProgressMessage("plain output")).toBeUndefined();
        expect(installerProgressMessage("")).toBeUndefined();
        expect(installerProgressMessage("install.sh: failed"))
            .toBeUndefined();
    });
});

describe("installShell", () => {
    const transcript = [
        "==> Detected: macos 15, arm64",
        "==> Fetching package list from the v26.9.2 release",
        "some noise that is not a progress marker",
        "==> Downloading",
        "==> Verifying checksum",
        "==> Unpacking into /Users/mzinner/.local/share/mariadb-shell",
    ];

    it("runs the installer for the current platform", async () => {
        const runner = createFakeRunner(transcript);
        const progress = createRecordingProgress();

        await installShell(
            { platform: "darwin", runner, progress },
            "26.9.2",
        );

        expect(runner.calls).toHaveLength(1);
        expect(runner.calls[0]).toEqual(
            buildInstallCommand("darwin", "26.9.2"),
        );
    });

    it("reports download and extraction progress", async () => {
        const runner = createFakeRunner(transcript);
        const progress = createRecordingProgress();

        await installShell(
            { platform: "darwin", runner, progress },
            "26.9.2",
        );

        expect(progress.titles).toEqual(["Installing MariaDB Shell 26.9.2"]);
        expect(progress.messages).toEqual([
            "Starting the installer...",
            "Detected: macos 15, arm64",
            "Fetching package list from the v26.9.2 release",
            "Downloading",
            "Verifying checksum",
            "Unpacking into /Users/mzinner/.local/share/mariadb-shell",
        ]);
    });

    it("throws when the installer fails", async () => {
        const runner = createFakeRunner(
            ["install.sh: required command not found: curl"],
            1,
        );
        const progress = createRecordingProgress();

        await expect(installShell(
            { platform: "darwin", runner, progress },
            "26.9.2",
        )).rejects.toThrow("The MariaDB Shell installer exited with code 1.");
    });
});
