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

import type { IMariaDbApi } from "../mcp/types.js";

/**
 * Where the default connection is remembered. Backed by the extension's
 * settings in the running extension, and by a plain object in tests.
 */
export interface IConnectionSettings {
    /**
     * @returns The URI of the default connection, if one is set.
     */
    getDefaultConnection(): string | undefined;

    /**
     * @param uri The connection to remember, or undefined to forget it.
     *
     * @returns Nothing.
     */
    setDefaultConnection(uri: string | undefined): Promise<void>;
}

/** A listener on the manager's state. */
export type ConnectionChangeListener = () => void;

/**
 * Tracks which of the configured connections are open, and which one is the
 * default.
 *
 * A connection is identified to the outside by its URI, which is stable and
 * is what the settings and the tree work with. The UUID the MCP server hands
 * out lives only here, for as long as the connection is open.
 */
export class ConnectionManager {
    readonly #open = new Map<string, string>();
    readonly #listeners = new Set<ConnectionChangeListener>();

    public constructor(
        private readonly apiProvider: () => Promise<IMariaDbApi>,
        private readonly settings: IConnectionSettings,
    ) { }

    /**
     * Registers a listener, called whenever a connection opens or closes or
     * the default changes.
     *
     * @param listener The listener to add.
     *
     * @returns A function that removes it again.
     */
    public onDidChange(listener: ConnectionChangeListener): () => void {
        this.#listeners.add(listener);

        return () => {
            this.#listeners.delete(listener);
        };
    }

    /**
     * The server API, starting the MCP server if it is not up yet.
     *
     * Everything that needs the server goes through here, so there is one
     * place that decides when it is started.
     *
     * @returns The typed `db.*` API.
     */
    public async api(): Promise<IMariaDbApi> {
        return await this.apiProvider();
    }

    /**
     * Lists the configured connection URIs.
     *
     * @returns One URI per configured connection.
     */
    public async listConnections(): Promise<string[]> {
        const api = await this.apiProvider();

        return await api.listConnections();
    }

    /**
     * @param uri The connection to check.
     *
     * @returns True if that connection is currently open.
     */
    public isConnected(uri: string): boolean {
        return this.#open.has(uri);
    }

    /**
     * @returns The URIs of all open connections.
     */
    public get openConnections(): string[] {
        return [...this.#open.keys()];
    }

    /**
     * @returns The URI of the default connection, if one is set.
     */
    public get defaultConnection(): string | undefined {
        return this.settings.getDefaultConnection();
    }

    /**
     * Makes a connection the default, or clears the default.
     *
     * @param uri The connection to default to, or undefined to clear it.
     *
     * @returns Nothing.
     */
    public async setDefaultConnection(uri: string | undefined): Promise<void> {
        await this.settings.setDefaultConnection(uri);
        this.#notify();
    }

    /**
     * Opens a connection, or returns the UUID of the one already open.
     *
     * @param uri The connection to open.
     *
     * @returns The UUID identifying the open connection.
     */
    public async connect(uri: string): Promise<string> {
        const existing = this.#open.get(uri);
        if (existing !== undefined) {
            return existing;
        }

        const api = await this.apiProvider();
        const connectionId = await api.connect(uri);
        this.#open.set(uri, connectionId);
        this.#notify();

        return connectionId;
    }

    /**
     * Closes a connection if it is open.
     *
     * @param uri The connection to close.
     *
     * @returns Nothing.
     */
    public async disconnect(uri: string): Promise<void> {
        const connectionId = this.#open.get(uri);
        if (connectionId === undefined) {
            return;
        }

        // Dropped from the map before the call, so a server that refuses to
        // close it does not leave the tree showing it as open forever.
        this.#open.delete(uri);
        this.#notify();

        const api = await this.apiProvider();
        await api.close(connectionId);
    }

    /**
     * Closes every open connection, ignoring failures - this runs while the
     * extension is shutting down, where there is nobody left to tell.
     *
     * @returns Nothing.
     */
    public async disconnectAll(): Promise<void> {
        const uris = this.openConnections;
        await Promise.all(uris.map(async (uri) => {
            try {
                await this.disconnect(uri);
            } catch {
                // The server is going away with us.
            }
        }));
    }

    /**
     * @param uri The connection to look up.
     *
     * @returns Its UUID, if it is open.
     */
    public connectionIdFor(uri: string): string | undefined {
        return this.#open.get(uri);
    }

    /**
     * Tells every listener that something changed.
     *
     * @returns Nothing.
     */
    #notify(): void {
        for (const listener of this.#listeners) {
            listener();
        }
    }
}
