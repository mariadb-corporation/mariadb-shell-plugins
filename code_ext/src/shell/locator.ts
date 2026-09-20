/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import * as path from "node:path";

import { SHELL_BINARY_NAME } from "./constants.js";
import {
    compareVersions,
    formatVersion,
    meetsMinimum,
    parseShellVersionOutput,
    parseVersion,
    type ShellVersion,
} from "./version.js";

/** Where a usable shell binary was found. */
export type ShellSource = "path" | "managed";

export interface ShellLocation {
    /** The executable to spawn. */
    binaryPath: string;
    /** The version that executable reported. */
    version: ShellVersion;
    /** Whether the binary came from the PATH or from a managed install. */
    source: ShellSource;
}

/**
 * Everything the locator needs from the outside world. Keeping this behind
 * an interface is what lets the lookup rules be tested for every platform
 * from a single host.
 */
export interface ShellEnvironment {
    /** The `process.platform` value to resolve paths for. */
    platform: NodeJS.Platform;
    /** The user's home directory. */
    homeDir: string;
    /** The process environment. */
    env: Record<string, string | undefined>;

    /**
     * Runs `<binaryPath> --version`.
     *
     * @param binaryPath The executable to probe.
     *
     * @returns The captured output, or undefined if it could not be run.
     */
    probeVersion(binaryPath: string): Promise<string | undefined>;

    /**
     * @param target A file system path.
     *
     * @returns True if something exists at that path.
     */
    pathExists(target: string): Promise<boolean>;

    /**
     * @param target A directory.
     *
     * @returns The names of the directories directly below it, empty if the
     *          directory does not exist.
     */
    listDirectories(target: string): Promise<string[]>;
}

/**
 * Picks the path flavour for the target platform rather than the host's, so
 * that Windows layouts can be exercised from a POSIX machine and back.
 *
 * @param platform The platform to resolve paths for.
 *
 * @returns The matching path implementation.
 */
const pathFor = (platform: NodeJS.Platform): path.PlatformPath => {
    return platform === "win32" ? path.win32 : path.posix;
};

/**
 * The directory the installer unpacks versioned shell installations into.
 * This mirrors the defaults of install.sh and install.ps1, including the
 * MARIADB_SHELL_PREFIX override both of them honour.
 *
 * @param environment The environment to resolve against.
 *
 * @returns The absolute install prefix.
 */
export const installPrefix = (environment: ShellEnvironment): string => {
    const override = environment.env.MARIADB_SHELL_PREFIX;
    if (override) {
        return override;
    }

    const p = pathFor(environment.platform);
    if (environment.platform === "win32") {
        const localAppData =
            environment.env.LOCALAPPDATA ??
            p.join(environment.homeDir, "AppData", "Local");

        return p.join(localAppData, "Programs", "mariadb-shell");
    }

    return p.join(environment.homeDir, ".local", "share", "mariadb-shell");
};

/**
 * The file name of the shell executable on the given platform.
 *
 * @param platform The target platform.
 *
 * @returns `mariadb-shell.exe` on Windows, `mariadb-shell` elsewhere.
 */
export const shellBinaryName = (platform: NodeJS.Platform): string => {
    return platform === "win32"
        ? `${SHELL_BINARY_NAME}.exe`
        : SHELL_BINARY_NAME;
};

/**
 * The executable inside one versioned installation below the prefix.
 *
 * @param environment The environment to resolve against.
 * @param version The version directory name.
 *
 * @returns The absolute path of the shell binary.
 */
export const managedBinaryPath = (
    environment: ShellEnvironment,
    version: string,
): string => {
    const p = pathFor(environment.platform);

    return p.join(
        installPrefix(environment),
        version,
        "bin",
        shellBinaryName(environment.platform),
    );
};

/**
 * Probes a binary and returns its version if it is new enough.
 *
 * @param environment The environment to probe with.
 * @param binaryPath The executable to probe.
 * @param minimum The lowest acceptable version.
 *
 * @returns The reported version, or undefined if the binary is missing,
 *          unreadable or too old.
 */
const probeIfUsable = async (
    environment: ShellEnvironment,
    binaryPath: string,
    minimum: ShellVersion,
): Promise<ShellVersion | undefined> => {
    const output = await environment.probeVersion(binaryPath);
    if (output === undefined) {
        return undefined;
    }

    const version = parseShellVersionOutput(output);
    if (!version || !meetsMinimum(version, minimum)) {
        return undefined;
    }

    return version;
};

/**
 * Looks for a shell on the PATH that meets the minimum version.
 *
 * @param environment The environment to search in.
 * @param minimum The lowest acceptable version.
 *
 * @returns The location, or undefined if the PATH holds no usable shell.
 */
export const findShellOnPath = async (
    environment: ShellEnvironment,
    minimum: ShellVersion,
): Promise<ShellLocation | undefined> => {
    const binaryPath = shellBinaryName(environment.platform);
    const version = await probeIfUsable(environment, binaryPath, minimum);
    if (!version) {
        return undefined;
    }

    return { binaryPath, version, source: "path" };
};

/**
 * Looks for a usable shell among the versioned installations the installer
 * maintains, newest first.
 *
 * @param environment The environment to search in.
 * @param minimum The lowest acceptable version.
 *
 * @returns The location, or undefined if no installed version qualifies.
 */
export const findManagedShell = async (
    environment: ShellEnvironment,
    minimum: ShellVersion,
): Promise<ShellLocation | undefined> => {
    const prefix = installPrefix(environment);
    const entries = await environment.listDirectories(prefix);

    // Only directories that name a version are ours; anything else below the
    // prefix belongs to something we did not put there.
    const candidates = entries
        .map((name) => {
            return { name, version: parseVersion(name) };
        })
        .filter((entry): entry is { name: string; version: ShellVersion } => {
            return entry.version !== undefined
                && meetsMinimum(entry.version, minimum);
        })
        .sort((a, b) => {
            return compareVersions(b.version, a.version);
        });

    for (const candidate of candidates) {
        const binaryPath = managedBinaryPath(environment, candidate.name);
        if (!await environment.pathExists(binaryPath)) {
            continue;
        }

        // The directory name is only a hint - what the binary reports wins,
        // and a binary that cannot be run at all is skipped rather than
        // handed on to the MCP server.
        const version = await probeIfUsable(environment, binaryPath, minimum);
        if (!version) {
            continue;
        }

        return { binaryPath, version, source: "managed" };
    }

    return undefined;
};

/**
 * Finds a MariaDB Shell that is new enough to host the MCP server, looking
 * at the PATH first and at the managed installations second.
 *
 * @param environment The environment to search in.
 * @param minimumVersion The lowest acceptable version.
 *
 * @returns The location, or undefined if the shell has to be installed.
 */
export const locateShell = async (
    environment: ShellEnvironment,
    minimumVersion: string,
): Promise<ShellLocation | undefined> => {
    const minimum = parseVersion(minimumVersion);
    if (!minimum) {
        throw new Error(
            `Invalid minimum shell version: ${minimumVersion}`,
        );
    }

    return await findShellOnPath(environment, minimum)
        ?? await findManagedShell(environment, minimum);
};

/**
 * Renders a location for a log line.
 *
 * @param location The location to describe.
 *
 * @returns A human readable one liner.
 */
export const describeLocation = (location: ShellLocation): string => {
    const origin = location.source === "path"
        ? "found on the PATH"
        : "found in the local installation";

    return `MariaDB Shell ${formatVersion(location.version)} `
        + `${origin} at ${location.binaryPath}`;
};
