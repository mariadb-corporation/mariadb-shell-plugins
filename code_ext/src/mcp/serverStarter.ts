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

import type { IMariaDbApi } from "./types.js";

/**
 * Where getting a server up has got to.
 *
 * - `stopped`: nothing has asked for one yet, or it was stopped.
 * - `locating`: looking for a shell that is new enough.
 * - `installing`: none was found, and the installer is running.
 * - `starting`: a shell is there and the MCP server is coming up on it.
 * - `ready`: the server answers.
 * - `failed`: the last attempt failed; the next request tries again.
 */
export type ServerPhase =
    | "stopped"
    | "locating"
    | "installing"
    | "starting"
    | "ready"
    | "failed";

/** The phases the steps themselves report; the rest are the starter's. */
export type ServerStepPhase = "installing" | "starting";

/**
 * Finds or installs a shell and starts the server on it, saying which step
 * it is on as it goes.
 */
export type ServerStartSteps = (
    onPhase: (phase: ServerStepPhase) => void,
) => Promise<IMariaDbApi>;

/** What the Connections view needs to know about the server's startup. */
export interface IServerStatus {
    readonly phase: ServerPhase;

    /**
     * @param listener Called with every phase change.
     *
     * @returns A function that stops the listening.
     */
    onDidChangePhase(listener: (phase: ServerPhase) => void): () => void;
}

/**
 * Gets the MCP server up, once, however many callers ask at the same time.
 *
 * The tree, the editor toolbar and the result panel all reach for the API
 * on their own, often in the same moment the window opens. Without this
 * each of them would look for a shell - and, finding none, each would run
 * the installer into the same directory. `McpSession` serializes the
 * server start, but that is the last step; this covers the whole way.
 */
export class ServerStarter implements IServerStatus {
    #phase: ServerPhase = "stopped";
    #starting?: Promise<IMariaDbApi>;
    readonly #listeners = new Set<(phase: ServerPhase) => void>();

    /**
     * @param steps How a server is found or installed, and started.
     * @param log Where a failed start is recorded.
     */
    public constructor(
        private readonly steps: ServerStartSteps,
        private readonly log: (message: string) => void,
    ) { }

    /**
     * @returns Where getting a server up has got to.
     */
    public get phase(): ServerPhase {
        return this.#phase;
    }

    /**
     * @param listener Called with every phase change.
     *
     * @returns A function that stops the listening.
     */
    public onDidChangePhase(
        listener: (phase: ServerPhase) => void,
    ): () => void {
        this.#listeners.add(listener);

        return () => {
            this.#listeners.delete(listener);
        };
    }

    /**
     * Starts the server, or joins the start already under way.
     *
     * @returns The API of the server that came up.
     */
    public async start(): Promise<IMariaDbApi> {
        this.#starting ??= this.#run().finally(() => {
            this.#starting = undefined;
        });

        return await this.#starting;
    }

    /**
     * Records that the server was stopped from outside.
     *
     * @returns Nothing.
     */
    public stopped(): void {
        this.#set("stopped");
    }

    /**
     * Runs the steps once, tracking the phase and logging a failure.
     *
     * @returns The API of the server that came up.
     */
    async #run(): Promise<IMariaDbApi> {
        this.#set("locating");
        try {
            const api = await this.steps((phase) => {
                this.#set(phase);
            });
            this.#set("ready");

            return api;
        } catch (error) {
            const message = error instanceof Error
                ? error.message
                : String(error);
            this.log(`The MCP server could not be started: ${message}`);
            this.#set("failed");
            throw error;
        }
    }

    /**
     * @param phase The phase to move to.
     *
     * @returns Nothing.
     */
    #set(phase: ServerPhase): void {
        if (this.#phase === phase) {
            return;
        }

        this.#phase = phase;
        for (const listener of [...this.#listeners]) {
            listener(phase);
        }
    }
}
