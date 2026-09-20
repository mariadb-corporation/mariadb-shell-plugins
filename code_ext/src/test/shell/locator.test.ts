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

import { createFakeEnvironment } from "../helpers.js";
import {
    describeLocation,
    findManagedShell,
    findShellOnPath,
    installPrefix,
    locateShell,
    managedBinaryPath,
    shellBinaryName,
} from "../../shell/locator.js";
import { parseVersion } from "../../shell/version.js";

const MINIMUM = "26.9.2";
const MINIMUM_VERSION = parseVersion(MINIMUM)!;

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

describe("installPrefix", () => {
    it("uses ~/.local/share/mariadb-shell on macOS", () => {
        const environment = createFakeEnvironment({
            platform: "darwin",
            homeDir: "/Users/mzinner",
        });

        expect(installPrefix(environment))
            .toBe("/Users/mzinner/.local/share/mariadb-shell");
    });

    it("uses ~/.local/share/mariadb-shell on Linux", () => {
        const environment = createFakeEnvironment({
            platform: "linux",
            homeDir: "/home/mzinner",
        });

        expect(installPrefix(environment))
            .toBe("/home/mzinner/.local/share/mariadb-shell");
    });

    it("uses LOCALAPPDATA\\Programs on Windows", () => {
        const environment = createFakeEnvironment({
            platform: "win32",
            homeDir: "C:\\Users\\mzinner",
            env: { LOCALAPPDATA: "C:\\Users\\mzinner\\AppData\\Local" },
        });

        expect(installPrefix(environment)).toBe(
            "C:\\Users\\mzinner\\AppData\\Local\\Programs\\mariadb-shell",
        );
    });

    it("falls back to the home directory when LOCALAPPDATA is unset", () => {
        const environment = createFakeEnvironment({
            platform: "win32",
            homeDir: "C:\\Users\\mzinner",
        });

        expect(installPrefix(environment)).toBe(
            "C:\\Users\\mzinner\\AppData\\Local\\Programs\\mariadb-shell",
        );
    });

    it("honours MARIADB_SHELL_PREFIX", () => {
        const environment = createFakeEnvironment({
            env: { MARIADB_SHELL_PREFIX: "/opt/mariadb-shell" },
        });

        expect(installPrefix(environment)).toBe("/opt/mariadb-shell");
    });
});

describe("shellBinaryName and managedBinaryPath", () => {
    it("appends .exe only on Windows", () => {
        expect(shellBinaryName("darwin")).toBe("mariadb-shell");
        expect(shellBinaryName("linux")).toBe("mariadb-shell");
        expect(shellBinaryName("win32")).toBe("mariadb-shell.exe");
    });

    it("points at bin/ below the versioned directory", () => {
        const environment = createFakeEnvironment({
            platform: "darwin",
            homeDir: "/Users/mzinner",
        });

        expect(managedBinaryPath(environment, "26.9.2")).toBe(
            "/Users/mzinner/.local/share/mariadb-shell/26.9.2/bin"
            + "/mariadb-shell",
        );
    });

    it("builds a Windows path", () => {
        const environment = createFakeEnvironment({
            platform: "win32",
            homeDir: "C:\\Users\\mzinner",
            env: { LOCALAPPDATA: "C:\\Users\\mzinner\\AppData\\Local" },
        });

        expect(managedBinaryPath(environment, "26.9.2")).toBe(
            "C:\\Users\\mzinner\\AppData\\Local\\Programs\\mariadb-shell"
            + "\\26.9.2\\bin\\mariadb-shell.exe",
        );
    });
});

describe("findShellOnPath", () => {
    it("accepts a shell on the PATH that meets the minimum", async () => {
        const environment = createFakeEnvironment({
            versions: { "mariadb-shell": versionLine("26.9.2") },
        });

        await expect(findShellOnPath(environment, MINIMUM_VERSION))
            .resolves.toEqual({
                binaryPath: "mariadb-shell",
                version: { major: 26, minor: 9, patch: 2 },
                source: "path",
            });
    });

    it("rejects a shell on the PATH that is too old", async () => {
        const environment = createFakeEnvironment({
            versions: { "mariadb-shell": versionLine("26.8.0") },
        });

        await expect(findShellOnPath(environment, MINIMUM_VERSION))
            .resolves.toBeUndefined();
    });

    it("rejects a shell that cannot be run", async () => {
        const environment = createFakeEnvironment();

        await expect(findShellOnPath(environment, MINIMUM_VERSION))
            .resolves.toBeUndefined();
    });

    it("probes mariadb-shell.exe on Windows", async () => {
        const environment = createFakeEnvironment({
            platform: "win32",
            env: { LOCALAPPDATA: "C:\\Local" },
            versions: { "mariadb-shell.exe": versionLine("26.9.2") },
        });

        const found = await findShellOnPath(environment, MINIMUM_VERSION);

        expect(found?.binaryPath).toBe("mariadb-shell.exe");
    });
});

describe("findManagedShell", () => {
    const prefix = "/Users/mzinner/.local/share/mariadb-shell";

    it("finds an installed version", async () => {
        const binary = `${prefix}/26.9.2/bin/mariadb-shell`;
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["26.9.2"] },
            files: [binary],
            versions: { [binary]: versionLine("26.9.2") },
        });

        await expect(findManagedShell(environment, MINIMUM_VERSION))
            .resolves.toEqual({
                binaryPath: binary,
                version: { major: 26, minor: 9, patch: 2 },
                source: "managed",
            });
    });

    it("prefers the newest installed version", async () => {
        const older = `${prefix}/26.9.2/bin/mariadb-shell`;
        const newer = `${prefix}/26.10.1/bin/mariadb-shell`;
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["26.9.2", "26.10.1", "26.8.0"] },
            files: [older, newer],
            versions: {
                [older]: versionLine("26.9.2"),
                [newer]: versionLine("26.10.1"),
            },
        });

        const found = await findManagedShell(environment, MINIMUM_VERSION);

        expect(found?.binaryPath).toBe(newer);
        expect(environment.probed).toEqual([newer]);
    });

    it("skips versions below the minimum without probing them", async () => {
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["26.8.0"] },
            files: [`${prefix}/26.8.0/bin/mariadb-shell`],
            versions: {
                [`${prefix}/26.8.0/bin/mariadb-shell`]:
                    versionLine("26.8.0"),
            },
        });

        await expect(findManagedShell(environment, MINIMUM_VERSION))
            .resolves.toBeUndefined();
        expect(environment.probed).toEqual([]);
    });

    it("falls through to an older directory whose binary is missing",
        async () => {
            const good = `${prefix}/26.9.2/bin/mariadb-shell`;
            const environment = createFakeEnvironment({
                directories: { [prefix]: ["26.9.2", "26.10.1"] },
                // 26.10.1 was left half installed - no binary below it.
                files: [good],
                versions: { [good]: versionLine("26.9.2") },
            });

            const found = await findManagedShell(
                environment,
                MINIMUM_VERSION,
            );

            expect(found?.binaryPath).toBe(good);
        });

    it("distrusts a directory name that the binary contradicts",
        async () => {
            const binary = `${prefix}/26.9.9/bin/mariadb-shell`;
            const environment = createFakeEnvironment({
                directories: { [prefix]: ["26.9.9"] },
                files: [binary],
                // The directory claims 26.9.9 but the binary is 26.8.0.
                versions: { [binary]: versionLine("26.8.0") },
            });

            await expect(findManagedShell(environment, MINIMUM_VERSION))
                .resolves.toBeUndefined();
        });

    it("ignores entries that are not version directories", async () => {
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["current", "README", ".DS_Store"] },
        });

        await expect(findManagedShell(environment, MINIMUM_VERSION))
            .resolves.toBeUndefined();
    });

    it("returns undefined when the prefix does not exist", async () => {
        const environment = createFakeEnvironment();

        await expect(findManagedShell(environment, MINIMUM_VERSION))
            .resolves.toBeUndefined();
    });
});

describe("locateShell", () => {
    const prefix = "/Users/mzinner/.local/share/mariadb-shell";

    it("prefers the PATH over a local installation", async () => {
        const managed = `${prefix}/26.10.0/bin/mariadb-shell`;
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["26.10.0"] },
            files: [managed],
            versions: {
                "mariadb-shell": versionLine("26.9.2"),
                [managed]: versionLine("26.10.0"),
            },
        });

        const found = await locateShell(environment, MINIMUM);

        expect(found?.source).toBe("path");
    });

    it("falls back to the local installation when the PATH shell is "
        + "too old", async () => {
        const managed = `${prefix}/26.9.2/bin/mariadb-shell`;
        const environment = createFakeEnvironment({
            directories: { [prefix]: ["26.9.2"] },
            files: [managed],
            versions: {
                "mariadb-shell": versionLine("26.8.0"),
                [managed]: versionLine("26.9.2"),
            },
        });

        const found = await locateShell(environment, MINIMUM);

        expect(found).toEqual({
            binaryPath: managed,
            version: { major: 26, minor: 9, patch: 2 },
            source: "managed",
        });
    });

    it("returns undefined when nothing usable is installed", async () => {
        const environment = createFakeEnvironment();

        await expect(locateShell(environment, MINIMUM))
            .resolves.toBeUndefined();
    });

    it("rejects a malformed minimum version", async () => {
        const environment = createFakeEnvironment();

        await expect(locateShell(environment, "not-a-version"))
            .rejects.toThrow(/Invalid minimum shell version/);
    });
});

describe("describeLocation", () => {
    it("names where the shell came from", () => {
        expect(describeLocation({
            binaryPath: "/usr/local/bin/mariadb-shell",
            version: { major: 26, minor: 9, patch: 2 },
            source: "path",
        })).toBe(
            "MariaDB Shell 26.9.2 found on the PATH at "
            + "/usr/local/bin/mariadb-shell",
        );

        expect(describeLocation({
            binaryPath: "/opt/26.9.2/bin/mariadb-shell",
            version: { major: 26, minor: 9, patch: 2 },
            source: "managed",
        })).toContain("found in the local installation");
    });
});
