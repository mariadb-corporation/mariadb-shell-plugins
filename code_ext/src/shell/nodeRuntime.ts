/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { execFile, spawn } from "node:child_process";
import * as fs from "node:fs/promises";
import * as os from "node:os";

import type { InstallCommand, ProcessRunner } from "./installer.js";
import { LineReader } from "./lineReader.js";
import type {
    McpServerCommand,
    McpServerProcess,
    McpServerSpawner,
} from "./mcpServer.js";
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

/**
 * Starts the MCP server as a child process.
 *
 * Its stdin and stdout carry the MCP protocol and are left as pipes for the
 * client to own; only stderr is surfaced as log output.
 *
 * @returns An MCP server spawner backed by `child_process.spawn`.
 */
export const createNodeMcpServerSpawner = (): McpServerSpawner => {
    return {
        spawn: (
            command: McpServerCommand,
            onOutput: (line: string) => void,
        ): McpServerProcess => {
            const child = spawn(command.command, command.args, {
                stdio: ["pipe", "pipe", "pipe"],
                windowsHide: true,
            });

            const reader = new LineReader(onOutput);
            child.stderr?.setEncoding("utf8");
            child.stderr?.on("data", (chunk: string) => {
                reader.push(chunk);
            });
            child.on("error", (error: Error) => {
                onOutput(`Failed to start the MCP server: ${error.message}`);
            });

            const exited = new Promise<number | null>((resolve) => {
                child.on("close", (code) => {
                    reader.flush();
                    resolve(code);
                });
            });

            return {
                kill: () => {
                    child.kill();
                },
                exited,
            };
        },
    };
};
