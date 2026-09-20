/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import {
    INSTALL_SCRIPT_URL_POSIX,
    INSTALL_SCRIPT_URL_WINDOWS,
} from "./constants.js";

export interface InstallCommand {
    /** The interpreter to spawn. */
    command: string;
    /** Its arguments, already split - nothing is passed through a shell. */
    args: string[];
}

/**
 * A spawned command, reduced to what the installer needs.
 */
export interface ProcessRunner {
    /**
     * Runs a command to completion, streaming its output line by line.
     *
     * @param command The command to run.
     * @param onOutput Called once per line of stdout or stderr.
     *
     * @returns The exit code.
     */
    run(
        command: InstallCommand,
        onOutput: (line: string) => void,
    ): Promise<number>;
}

/**
 * The progress UI, kept behind an interface so the install flow can be
 * tested without an extension host.
 */
export interface ProgressHost {
    /**
     * Shows a progress notification for the lifetime of a task.
     *
     * @param title The notification title.
     * @param task Receives a callback that updates the notification's
     *             message.
     *
     * @returns Whatever the task returned.
     */
    withProgress<T>(
        title: string,
        task: (report: (message: string) => void) => Promise<T>,
    ): Promise<T>;
}

/**
 * Builds the command that installs a pinned MariaDB Shell release.
 *
 * On macOS and Linux this is the documented curl-into-bash one liner with
 * MARIADB_SHELL_TAG pinned; on Windows the PowerShell equivalent. Both are
 * handed to an interpreter as a single script argument, so no part of them
 * is re-split by an intermediate shell.
 *
 * @param platform The platform to install on.
 * @param version The release to install, without the leading `v`.
 *
 * @returns The command to run.
 */
export const buildInstallCommand = (
    platform: NodeJS.Platform,
    version: string,
): InstallCommand => {
    const tag = `v${version}`;

    if (platform === "win32") {
        const script = `$env:MARIADB_SHELL_TAG = '${tag}'; `
            + `irm ${INSTALL_SCRIPT_URL_WINDOWS} | iex`;

        return {
            command: "powershell.exe",
            args: [
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
        };
    }

    const script = `curl -fsSL ${INSTALL_SCRIPT_URL_POSIX} `
        + `| MARIADB_SHELL_TAG=${tag} bash`;

    return { command: "/bin/sh", args: ["-c", script] };
};

/**
 * Both installers prefix their progress messages with `==> `. Anything else
 * they print is noise for a notification, so only the marked lines are
 * surfaced.
 *
 * @param line One line of installer output.
 *
 * @returns The message to show, or undefined if the line is not one.
 */
export const installerProgressMessage = (
    line: string,
): string | undefined => {
    const match = /^\s*==>\s*(.+?)\s*$/.exec(line);

    return match ? match[1] : undefined;
};

export interface InstallDependencies {
    platform: NodeJS.Platform;
    runner: ProcessRunner;
    progress: ProgressHost;
}

/**
 * Downloads and unpacks the pinned MariaDB Shell release, reporting the
 * installer's own progress messages into a notification.
 *
 * @param dependencies The platform, process runner and progress UI to use.
 * @param version The release to install, without the leading `v`.
 *
 * @returns Nothing; it throws if the installer fails.
 */
export const installShell = async (
    dependencies: InstallDependencies,
    version: string,
): Promise<void> => {
    const command = buildInstallCommand(dependencies.platform, version);

    const exitCode = await dependencies.progress.withProgress(
        `Installing MariaDB Shell ${version}`,
        async (report) => {
            report("Starting the installer...");

            return await dependencies.runner.run(command, (line) => {
                const message = installerProgressMessage(line);
                if (message) {
                    report(message);
                }
            });
        },
    );

    if (exitCode !== 0) {
        throw new Error(
            `The MariaDB Shell installer exited with code ${exitCode}.`,
        );
    }
};
