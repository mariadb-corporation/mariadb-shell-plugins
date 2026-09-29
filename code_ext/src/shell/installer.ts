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
     * @param signal Ends the command, and everything it started, when
     *               aborted. The returned promise then rejects.
     *
     * @returns The exit code.
     */
    run(
        command: InstallCommand,
        onOutput: (line: string) => void,
        signal?: AbortSignal,
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
     *             message, and a signal that is aborted when the user
     *             cancels the notification.
     *
     * @returns Whatever the task returned.
     */
    withProgress<T>(
        title: string,
        task: (
            report: (message: string) => void,
            signal: AbortSignal,
        ) => Promise<T>,
    ): Promise<T>;
}

/**
 * Builds the command that installs a pinned MariaDB Shell release.
 *
 * On macOS and Linux this is the documented curl-into-bash one liner with
 * MARIADB_SHELL_TAG pinned, split in two so that a failed download fails
 * the command; on Windows the PowerShell equivalent. Both are
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

    // Fetched first rather than piped straight into bash: a pipe's status
    // is its last command's, so a curl that fails - offline, say - would
    // hand bash an empty script, which succeeds, and the reason curl gave
    // would end up behind "no shell could be found afterwards". POSIX sh
    // has no pipefail to rely on, so the failure is caught here instead.
    const script = `script=$(curl -fsSL ${INSTALL_SCRIPT_URL_POSIX}) `
        + "|| exit $?; "
        + `printf '%s\\n' "$script" | MARIADB_SHELL_TAG=${tag} bash`;

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

/**
 * Reduces one line of installer output to what is worth keeping.
 *
 * curl draws its progress bar by rewriting one line with carriage returns,
 * so a line can hold dozens of redraws; only the last one is what the
 * terminal would show, and a bare bar says nothing a log reader needs.
 *
 * @param line One line as the process runner delivered it.
 *
 * @returns The line to keep, or undefined if there is nothing in it.
 */
export const cleanInstallerLine = (line: string): string | undefined => {
    const shown = line.split("\r").at(-1)?.trim() ?? "";
    if (shown.length === 0 || /^[#O=\-\s]*(\d+(\.\d+)?%)?$/.test(shown)) {
        return undefined;
    }

    return shown;
};

/** How both installers begin the message they die with. */
const INSTALLER_ERROR_PATTERN = /^\s*install\.(sh|ps1):\s*/;

/** The transport's own complaint, e.g. `curl: (6) Could not resolve host`. */
const CURL_ERROR_PATTERN = /^\s*curl:\s*\(\d+\)/;

/** What PowerShell and the tools below it say when something went wrong. */
const GENERIC_ERROR_PATTERN =
    /\b(error|failed|could not|cannot|unable|denied|not found)\b/i;

/**
 * Picks the reason a failed installer run gave, out of everything it
 * printed that was not a progress marker.
 *
 * Both installers end on one message of their own, `install.sh: ...` or
 * `install.ps1: ...`, which can run over a line or two before the advice
 * that follows it; the transport's error, if there was one, is printed
 * above it. Either is more use in a notification than an exit code.
 *
 * @param lines The installer's non-progress output, in order.
 *
 * @returns A one line reason, or undefined if the output holds none.
 */
export const summarizeInstallerFailure = (
    lines: readonly string[],
): string | undefined => {
    const curl = lines.findLast((line) => {
        return CURL_ERROR_PATTERN.test(line);
    })?.trim();

    const start = lines.findLastIndex((line) => {
        return INSTALLER_ERROR_PATTERN.test(line);
    });
    if (start !== -1) {
        let message = lines[start].replace(INSTALLER_ERROR_PATTERN, "");
        // A message is wrapped onto indented lines until its sentence ends.
        for (let i = start + 1; i < lines.length && i <= start + 2; ++i) {
            if (/[.?!:]$/.test(message.trim())
                || !/^\s/.test(lines[i])) {
                break;
            }
            message += ` ${lines[i].trim()}`;
        }
        message = message.replace(/\s+/g, " ").trim();

        return curl ? `${message} (${curl})` : message;
    }

    return curl ?? lines.findLast((line) => {
        return GENERIC_ERROR_PATTERN.test(line);
    })?.trim();
};

/**
 * Renders a command for the log.
 *
 * @param command The command to render.
 *
 * @returns The command and its arguments on one line.
 */
const formatCommand = (command: InstallCommand): string => {
    return [command.command, ...command.args].map((part) => {
        return /\s/.test(part) ? JSON.stringify(part) : part;
    }).join(" ");
};

export interface InstallDependencies {
    platform: NodeJS.Platform;
    runner: ProcessRunner;
    progress: ProgressHost;
    /** Receives the command, every line it prints and how it ended. */
    log?: (message: string) => void;
}

/**
 * Downloads and unpacks the pinned MariaDB Shell release, reporting the
 * installer's own progress messages into a notification.
 *
 * Everything the installer prints goes to the log, since a failure is only
 * ever explained there; the reason it gave is also what the error says.
 *
 * @param dependencies The platform, process runner, progress UI and log.
 * @param version The release to install, without the leading `v`.
 *
 * @returns Nothing; it throws if the installer fails or is cancelled.
 */
export const installShell = async (
    dependencies: InstallDependencies,
    version: string,
): Promise<void> => {
    const log = dependencies.log ?? (() => { /* not wanted */ });
    const command = buildInstallCommand(dependencies.platform, version);
    const diagnostics: string[] = [];
    const started = Date.now();
    let cancelled = false;

    log(`Installing MariaDB Shell ${version}: ${formatCommand(command)}`);

    let exitCode: number;
    try {
        exitCode = await dependencies.progress.withProgress(
            `Installing MariaDB Shell ${version}`,
            async (report, signal) => {
                signal.addEventListener("abort", () => {
                    cancelled = true;
                });
                report("Starting the installer...");

                return await dependencies.runner.run(command, (raw) => {
                    const line = cleanInstallerLine(raw);
                    if (line === undefined) {
                        return;
                    }

                    log(`  installer: ${line}`);
                    const message = installerProgressMessage(line);
                    if (message) {
                        report(message);
                    } else {
                        diagnostics.push(raw.split("\r").at(-1) ?? raw);
                    }
                }, signal);
            },
        );
    } catch (error) {
        if (cancelled) {
            log("The installation was cancelled.");
            throw new Error(
                `Installing MariaDB Shell ${version} was cancelled.`,
            );
        }

        const message = error instanceof Error
            ? error.message
            : String(error);
        log(`The installer could not be run: ${message}`);
        throw new Error(
            `The MariaDB Shell installer could not be run `
            + `(${command.command}): ${message}`,
        );
    }

    const seconds = ((Date.now() - started) / 1000).toFixed(1);
    log(`The installer exited with code ${exitCode} after ${seconds}s.`);

    if (exitCode !== 0) {
        const reason = summarizeInstallerFailure(diagnostics);
        throw new Error(
            `MariaDB Shell ${version} could not be installed: `
            + (reason
                ? `${reason} (the installer exited with code ${exitCode}).`
                : `the installer exited with code ${exitCode}.`),
        );
    }
};
