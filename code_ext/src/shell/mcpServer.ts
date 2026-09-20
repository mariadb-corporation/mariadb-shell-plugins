/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { MCP_SERVER_ARGS } from "./constants.js";
import type { ShellLocation } from "./locator.js";

export interface McpServerCommand {
    command: string;
    args: string[];
}

/**
 * A running MCP server, reduced to what the extension needs to own it.
 */
export interface McpServerProcess {
    /** Stops the server. */
    kill(): void;
    /** Resolves with the exit code once the server is gone. */
    exited: Promise<number | null>;
}

export interface McpServerSpawner {
    /**
     * Starts the MCP server.
     *
     * @param command The command to run.
     * @param onOutput Called once per line the server writes to stderr.
     *
     * @returns A handle on the running process.
     */
    spawn(
        command: McpServerCommand,
        onOutput: (line: string) => void,
    ): McpServerProcess;
}

/**
 * Builds the command that makes a shell host the MCP server over stdio.
 *
 * @param location The shell to run.
 *
 * @returns The command to spawn.
 */
export const buildMcpServerCommand = (
    location: ShellLocation,
): McpServerCommand => {
    return { command: location.binaryPath, args: [...MCP_SERVER_ARGS] };
};

/**
 * Owns the MCP server process for the lifetime of the extension.
 */
export class McpServerController {
    #process?: McpServerProcess;

    public constructor(
        private readonly spawner: McpServerSpawner,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * @returns True while a server process is running.
     */
    public get isRunning(): boolean {
        return this.#process !== undefined;
    }

    /**
     * Starts the MCP server, replacing an already running one.
     *
     * @param location The shell to host the server.
     *
     * @returns The command that was spawned.
     */
    public start(location: ShellLocation): McpServerCommand {
        this.stop();

        const command = buildMcpServerCommand(location);
        this.log(`Starting MCP server: ${command.command} `
            + `${command.args.join(" ")}`);

        const child = this.spawner.spawn(command, (line) => {
            this.log(line);
        });
        this.#process = child;

        void child.exited.then((code) => {
            // Only clear the handle if this very process is still the
            // current one; a restart may already have replaced it.
            if (this.#process === child) {
                this.#process = undefined;
            }
            this.log(`MCP server exited with code ${code}.`);
        });

        return command;
    }

    /**
     * Stops the MCP server if one is running.
     *
     * @returns Nothing.
     */
    public stop(): void {
        const child = this.#process;
        if (!child) {
            return;
        }

        this.#process = undefined;
        this.log("Stopping MCP server.");
        child.kill();
    }
}
