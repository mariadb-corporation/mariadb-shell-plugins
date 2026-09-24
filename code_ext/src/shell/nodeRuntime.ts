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

import { execFile, spawn, type ExecFileException } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";

import type { InstallCommand, ProcessRunner } from "./installer.js";
import { LineReader } from "./lineReader.js";
import type { ShellEnvironment } from "./locator.js";

/** How long a `--version` probe may take before it is given up on. */
const VERSION_PROBE_TIMEOUT_MS = 10_000;

/**
 * Says why a `--version` probe produced nothing, in words a log reader can
 * act on.
 *
 * @param binaryPath The executable that was probed.
 * @param error What `execFile` failed with.
 * @param stderr What the binary wrote to stderr, if it ran at all.
 *
 * @returns One log line.
 */
const describeProbeFailure = (
    binaryPath: string,
    error: ExecFileException,
    stderr: string,
): string => {
    const probe = `"${binaryPath} --version"`;
    if (error.code === "ENOENT") {
        return `${probe}: not found.`;
    }

    if (error.code === "EACCES") {
        return `${probe}: not executable (EACCES).`;
    }

    if (error.killed) {
        return `${probe}: gave no answer within `
            + `${VERSION_PROBE_TIMEOUT_MS / 1000}s and was stopped.`;
    }

    const detail = stderr.trim().split("\n")[0];

    return `${probe} failed: ${error.message.trim()}`
        + (detail ? ` - ${detail}` : "");
};

/**
 * The real environment, backed by the file system and child processes.
 *
 * @param log Where to say why a binary that was probed could not be run.
 *            The locator only learns that it could not; the reason - not
 *            there, not executable, hung - is what a log reader needs.
 *
 * @returns A shell environment for the host this extension runs on.
 */
export const createNodeShellEnvironment = (
    log: (message: string) => void = () => { /* not wanted */ },
): ShellEnvironment => {
    return {
        platform: process.platform,
        homeDir: os.homedir(),
        env: process.env,

        probeVersion: async (binaryPath: string) => {
            return await new Promise<string | undefined>((resolve) => {
                execFile(
                    binaryPath,
                    ["--version"],
                    { timeout: VERSION_PROBE_TIMEOUT_MS, windowsHide: true },
                    (error, stdout, stderr) => {
                        // A binary that is missing from the PATH, is not
                        // executable or hangs is simply "not there" as far
                        // as the locator is concerned.
                        if (error) {
                            log(describeProbeFailure(
                                binaryPath, error, String(stderr)));
                            resolve(undefined);

                            return;
                        }

                        resolve(`${stdout}${stderr}`);
                    },
                );
            });
        },

        pathExists: async (target: string) => {
            try {
                await fs.access(target);

                return true;
            } catch {
                return false;
            }
        },

        listDirectories: async (target: string) => {
            try {
                const entries = await fs.readdir(target, {
                    withFileTypes: true,
                });

                return entries
                    .filter((entry) => {
                        return entry.isDirectory() || entry.isSymbolicLink();
                    })
                    .map((entry) => {
                        return entry.name;
                    });
            } catch {
                return [];
            }
        },
    };
};

/**
 * Runs installer commands as child processes.
 *
 * @returns A process runner backed by `child_process.spawn`.
 */
export const createNodeProcessRunner = (): ProcessRunner => {
    return {
        run: async (
            command: InstallCommand,
            onOutput: (line: string) => void,
            signal?: AbortSignal,
        ) => {
            return await new Promise<number>((resolve, reject) => {
                const posix = process.platform !== "win32";
                const child = spawn(command.command, command.args, {
                    windowsHide: true,
                    // Its own process group on POSIX, so that cancelling
                    // ends the curl and bash the shell started as well as
                    // the shell - killing the shell alone would leave the
                    // download running with nobody reading its output.
                    detached: posix,
                });

                const reader = new LineReader(onOutput);
                child.stdout?.setEncoding("utf8");
                child.stderr?.setEncoding("utf8");
                child.stdout?.on("data", (chunk: string) => {
                    reader.push(chunk);
                });
                child.stderr?.on("data", (chunk: string) => {
                    reader.push(chunk);
                });

                const abort = (): void => {
                    try {
                        if (posix && child.pid !== undefined) {
                            process.kill(-child.pid, "SIGTERM");
                        } else {
                            child.kill();
                        }
                    } catch {
                        // Already gone, which is what was wanted.
                    }
                };
                signal?.addEventListener("abort", abort, { once: true });

                child.on("error", (error) => {
                    signal?.removeEventListener("abort", abort);
                    reject(error);
                });
                child.on("close", (code) => {
                    signal?.removeEventListener("abort", abort);
                    reader.flush();
                    if (signal?.aborted) {
                        reject(new Error("Cancelled."));

                        return;
                    }
                    resolve(code ?? -1);
                });
            });
        },
    };
};
