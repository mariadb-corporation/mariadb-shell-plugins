/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import type { InstallCommand, ProcessRunner } from "../shell/installer.js";
import type { ShellEnvironment } from "../shell/locator.js";
import type {
    McpServerCommand,
    McpServerProcess,
    McpServerSpawner,
} from "../shell/mcpServer.js";

export interface FakeEnvironmentOptions {
    platform?: NodeJS.Platform;
    homeDir?: string;
    env?: Record<string, string | undefined>;
    /** Maps a binary path to the `--version` output it answers with. */
    versions?: Record<string, string>;
    /** Paths that exist on the fake file system. */
    files?: string[];
    /** Maps a directory to the sub directory names it holds. */
    directories?: Record<string, string[]>;
}

export interface FakeEnvironment extends ShellEnvironment {
    /** The binaries that were probed, in order. */
    probed: string[];
}

/**
 * Builds a shell environment that answers from plain in-memory tables, so
 * lookup rules can be exercised for any platform from any host.
 *
 * @param options What the fake should report.
 *
 * @returns The fake environment.
 */
export const createFakeEnvironment = (
    options: FakeEnvironmentOptions = {},
): FakeEnvironment => {
    const versions = options.versions ?? {};
    const files = new Set(options.files ?? []);
    const directories = options.directories ?? {};
    const probed: string[] = [];

    return {
        platform: options.platform ?? "darwin",
        homeDir: options.homeDir ?? "/Users/mzinner",
        env: options.env ?? {},
        probed,

        probeVersion: (binaryPath: string) => {
            probed.push(binaryPath);

            return Promise.resolve(versions[binaryPath]);
        },

        pathExists: (target: string) => {
            return Promise.resolve(files.has(target));
        },

        listDirectories: (target: string) => {
            return Promise.resolve(directories[target] ?? []);
        },
    };
};

export interface FakeRunner extends ProcessRunner {
    /** The commands that were run, in order. */
    calls: InstallCommand[];
}

/**
 * Builds a process runner that emits a canned transcript and exit code.
 *
 * @param output The lines the command should print.
 * @param exitCode The exit code it should end with.
 *
 * @returns The fake runner.
 */
export const createFakeRunner = (
    output: string[] = [],
    exitCode = 0,
): FakeRunner => {
    const calls: InstallCommand[] = [];

    return {
        calls,
        run: (command, onOutput) => {
            calls.push(command);
            for (const line of output) {
                onOutput(line);
            }

            return Promise.resolve(exitCode);
        },
    };
};

export interface FakeMcpProcess extends McpServerProcess {
    killed: boolean;
    /** Ends the fake process with the given exit code. */
    finish(code: number | null): void;
    emit(line: string): void;
}

export interface FakeSpawner extends McpServerSpawner {
    /** The commands that were spawned, in order. */
    commands: McpServerCommand[];
    /** The processes that were handed out, in order. */
    processes: FakeMcpProcess[];
}

/**
 * Builds an MCP server spawner whose processes are driven by the test.
 *
 * @returns The fake spawner.
 */
export const createFakeSpawner = (): FakeSpawner => {
    const commands: McpServerCommand[] = [];
    const processes: FakeMcpProcess[] = [];

    return {
        commands,
        processes,
        spawn: (command, onOutput) => {
            commands.push(command);

            let resolveExit: (code: number | null) => void = () => {
                // Replaced synchronously by the promise executor below.
            };
            const exited = new Promise<number | null>((resolve) => {
                resolveExit = resolve;
            });

            const child: FakeMcpProcess = {
                killed: false,
                exited,
                kill: () => {
                    child.killed = true;
                    resolveExit(null);
                },
                finish: (code) => {
                    resolveExit(code);
                },
                emit: (line) => {
                    onOutput(line);
                },
            };
            processes.push(child);

            return child;
        },
    };
};

export interface RecordingLog {
    (message: string): void;
    lines: string[];
}

/**
 * Builds a log function that keeps every line it was given.
 *
 * @returns The recording log.
 */
export const createRecordingLog = (): RecordingLog => {
    const lines: string[] = [];
    const log = ((message: string) => {
        lines.push(message);
    }) as RecordingLog;
    log.lines = lines;

    return log;
};
