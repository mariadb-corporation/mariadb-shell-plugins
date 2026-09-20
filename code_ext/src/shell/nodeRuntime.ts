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

import { execFile, spawn } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";

import type { InstallCommand, ProcessRunner } from "./installer.js";
import { LineReader } from "./lineReader.js";
import type { ShellEnvironment } from "./locator.js";

/** How long a `--version` probe may take before it is given up on. */
const VERSION_PROBE_TIMEOUT_MS = 10_000;

/**
 * The real environment, backed by the file system and child processes.
 *
 * @returns A shell environment for the host this extension runs on.
 */
export const createNodeShellEnvironment = (): ShellEnvironment => {
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
        ) => {
            return await new Promise<number>((resolve, reject) => {
                const child = spawn(command.command, command.args, {
                    windowsHide: true,
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

                child.on("error", reject);
                child.on("close", (code) => {
                    reader.flush();
                    resolve(code ?? -1);
                });
            });
        },
    };
};
