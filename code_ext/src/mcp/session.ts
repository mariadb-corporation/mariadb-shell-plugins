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

import type { McpServerCommand } from "../shell/mcpServer.js";
import { MariaDbApi, type IToolCaller } from "./mariaDbApi.js";
import type { IMariaDbApi } from "./types.js";

/**
 * A live connection to a running MCP server: it answers tool calls and can
 * be closed, which also ends the server process.
 */
export interface IMcpConnection extends IToolCaller {
    close(): Promise<void>;
}

/**
 * Starts an MCP server and connects to it. Behind an interface so the
 * session's lifecycle can be tested without spawning a shell.
 */
export interface IMcpConnector {
    /**
     * @param command The shell command hosting the MCP server.
     * @param onLog Called once per line the server writes to stderr.
     *
     * @returns The connection, once the handshake has completed.
     */
    open(
        command: McpServerCommand,
        onLog: (line: string) => void,
    ): Promise<IMcpConnection>;
}

/**
 * Owns the MCP server connection for the lifetime of the extension, and
 * hands out the typed `db.*` API on top of it.
 *
 * Starting is serialized on a single promise: the tree, the editor toolbar
 * and the result panel all reach for the API independently, and without
 * this the first three of them would each start a server of their own.
 */
export class McpSession {
    #connection?: IMcpConnection;
    #starting?: Promise<IMariaDbApi>;
    #api?: IMariaDbApi;

    public constructor(
        private readonly connector: IMcpConnector,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * @returns True while a server connection is up.
     */
    public get isRunning(): boolean {
        return this.#connection !== undefined;
    }

    /**
     * @returns The API, if a server is already running, else undefined.
     */
    public get api(): IMariaDbApi | undefined {
        return this.#api;
    }

    /**
     * Starts the MCP server if it is not already running.
     *
     * @param command The shell command hosting the MCP server.
     *
     * @returns The typed API to call the server with.
     */
    public async start(command: McpServerCommand): Promise<IMariaDbApi> {
        if (this.#api) {
            return this.#api;
        }

        this.#starting ??= this.#open(command);

        try {
            return await this.#starting;
        } finally {
            this.#starting = undefined;
        }
    }

    /**
     * Stops the MCP server if one is running.
     *
     * @returns Nothing.
     */
    public async stop(): Promise<void> {
        const connection = this.#connection;
        this.#connection = undefined;
        this.#api = undefined;
        if (!connection) {
            return;
        }

        this.log("Stopping MCP server.");
        try {
            await connection.close();
        } catch (error) {
            this.log(`Failed to stop the MCP server cleanly: `
                + `${error instanceof Error ? error.message : String(error)}`);
        }
    }

    /**
     * Opens the connection and builds the API on it.
     *
     * @param command The shell command hosting the MCP server.
     *
     * @returns The typed API.
     */
    async #open(command: McpServerCommand): Promise<IMariaDbApi> {
        this.log(`Starting MCP server: ${command.command} `
            + `${command.args.join(" ")}`);

        const connection = await this.connector.open(command, (line) => {
            this.log(line);
        });
        this.#connection = connection;
        this.#api = new MariaDbApi(connection);
        this.log("MCP server ready.");

        return this.#api;
    }
}
