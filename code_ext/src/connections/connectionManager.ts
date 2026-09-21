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
import {
    createLoggingApi,
    type ActivityReporter,
} from "./connectionActivity.js";
import {
    listConnections as listStored,
    type IStoredConnection,
} from "./connectionStore.js";

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

/**
 * What the Connections view browses on.
 *
 * It keeps one connection of its own, open for as long as the view shows
 * the connection expanded, so that walking the schema tree neither waits
 * for whatever an editor is running nor disturbs its session state. It is
 * named rather than numbered because it is not one of the user's: the
 * actions grid says "UI Backend" where an editor's connection says "1".
 */
export const UI_BACKEND_SESSION = "UI Backend";

/**
 * One connection open on a configured connection URI.
 *
 * Several can be open on one URI at once, so the UUID the server handed
 * out is not enough to talk about one: the label is what the result view
 * shows and filters on.
 */
export interface IOpenConnection {
    /** The configured connection it was opened from. */
    uri: string;
    /** `1`, `2`, or a name like `UI Backend`. */
    label: string;
    /** The UUID the MCP server handed out. */
    connectionId: string;
}

/** A listener on the manager's state. */
export type ConnectionChangeListener = () => void;

/**
 * Tracks which connections are open on which of the configured URIs, and
 * which URI is the default.
 *
 * A configured connection is identified to the outside by its URI, which
 * is stable and is what the settings and the tree work with. More than one
 * connection can be open on it - the Connections view browses on its own
 * one while an editor runs on another - and each carries a label saying
 * which it is. The UUIDs the MCP server hands out live only here, for as
 * long as the connections are open.
 */
export class ConnectionManager {
    readonly #open: IOpenConnection[] = [];
    readonly #listeners = new Set<ConnectionChangeListener>();
    /** The API last handed out, with the one built over it. */
    #logged?: { plain: IMariaDbApi; logging: IMariaDbApi };

    /**
     * @param apiProvider Starts the server if need be and hands out the API.
     * @param settings Where the default connection is remembered.
     * @param report Where to report what happens on an open connection.
     *               Left out where nothing is listening - in tests, and
     *               before the result view exists.
     */
    public constructor(
        private readonly apiProvider: () => Promise<IMariaDbApi>,
        private readonly settings: IConnectionSettings,
        private readonly report?: ActivityReporter,
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
     * place that decides when it is started - and one place that reports
     * what is done with it, since what comes back is wrapped so that every
     * call on an open connection reaches the result view.
     *
     * @returns The typed `db.*` API.
     */
    public async api(): Promise<IMariaDbApi> {
        const plain = await this.apiProvider();
        if (this.report === undefined) {
            return plain;
        }

        // The server can be restarted under us, which hands out a new API
        // to wrap; anything else would go on calling the dead one.
        if (this.#logged?.plain !== plain) {
            this.#logged = {
                plain,
                logging: createLoggingApi(
                    plain,
                    (connectionId) => { return this.#sessionOf(connectionId); },
                    (event) => { this.report?.(event); },
                ),
            };
        }

        return this.#logged.logging;
    }

    /**
     * Lists every configured connection, from both lists.
     *
     * Both, because a connection in the shared MCP list is as usable from
     * the editor as one of the extension's own - the checkbox says who ELSE
     * may open it, not whether this extension can.
     *
     * @returns One entry per configured connection, with the list it is in.
     */
    public async listStoredConnections(): Promise<IStoredConnection[]> {
        return await listStored(await this.api());
    }

    /**
     * Lists the configured connection URIs, from both lists.
     *
     * @returns One URI per configured connection.
     */
    public async listConnections(): Promise<string[]> {
        const stored = await this.listStoredConnections();

        return stored.map((connection) => { return connection.uri; });
    }

    /**
     * @param uri The connection to check.
     * @param name Which connection open on it, or undefined for any.
     *
     * @returns True if such a connection is currently open.
     */
    public isConnected(uri: string, name?: string): boolean {
        return this.#find(uri, name) !== undefined;
    }

    /**
     * @returns The URIs that have at least one connection open, in the
     *          order they were first opened.
     */
    public get openConnections(): string[] {
        return [...new Set(this.#open.map((open) => { return open.uri; }))];
    }

    /**
     * The connections open on one URI, oldest first.
     *
     * @param uri The configured connection to look up.
     *
     * @returns Its open connections.
     */
    public sessionsOf(uri: string): IOpenConnection[] {
        return this.#open.filter((open) => { return open.uri === uri; });
    }

    /**
     * What a connection opened now would be called, before it is opened.
     *
     * The result view puts a run up the moment the user asks for it, which
     * is before the connection it runs on has been opened, so the label
     * has to be knowable in advance. It is: a named connection is called
     * what it is named, and an unnamed one reuses the lowest numbered
     * connection open on the URI, or takes the lowest number free.
     *
     * @param uri The connection to open.
     * @param name What to call it, for a connection that has a name.
     *
     * @returns The label `connect` would use.
     */
    public labelFor(uri: string, name?: string): string {
        if (name !== undefined) {
            return name;
        }

        const numbered = this.sessionsOf(uri)
            .map((open) => { return Number(open.label); })
            .filter((index) => { return Number.isInteger(index); })
            .sort((first, second) => { return first - second; });

        // The one already open is reused, so an editor keeps running on
        // the connection it has been running on.
        if (numbered.length > 0) {
            return String(numbered[0]);
        }

        return "1";
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
     * Opens a connection, or returns the UUID of the matching one already
     * open.
     *
     * @param uri The connection to open.
     * @param name What to call it. Connections that have a name are one
     *             per name and per URI; unnamed ones are numbered, and
     *             asking for one again reuses the one already open.
     *
     * @returns The UUID identifying the open connection.
     */
    public async connect(uri: string, name?: string): Promise<string> {
        const label = this.labelFor(uri, name);
        const existing = this.#find(uri, label);
        if (existing !== undefined) {
            return existing.connectionId;
        }

        const api = await this.apiProvider();
        const when = new Date();
        const startedMs = Date.now();
        const connectionId = await api.connect(uri);
        this.#open.push({ uri, label, connectionId });
        this.report?.({
            connection: uri,
            label,
            call: `db.connect(${uri})`,
            message: `Opened Session ${label} for ${uri}`,
            when,
            elapsedMs: Date.now() - startedMs,
        });
        this.#notify();

        return connectionId;
    }

    /**
     * Closes what is open on a connection.
     *
     * @param uri The connection to close.
     * @param name Which connection open on it, or undefined for every one
     *             of them - which is what disconnecting in the tree means.
     *
     * @returns Nothing.
     */
    public async disconnect(uri: string, name?: string): Promise<void> {
        const closing = name === undefined
            ? this.sessionsOf(uri)
            : this.#open.filter((open) => {
                return open.uri === uri && open.label === name;
            });
        if (closing.length === 0) {
            return;
        }

        // Dropped from the list before the calls, so a server that refuses
        // to close one does not leave the tree showing it as open forever.
        for (const open of closing) {
            this.#open.splice(this.#open.indexOf(open), 1);
        }
        this.#notify();

        const api = await this.apiProvider();
        for (const open of closing) {
            const when = new Date();
            const startedMs = Date.now();
            await api.close(open.connectionId);
            this.report?.({
                connection: open.uri,
                label: open.label,
                call: "db.close()",
                message: `Closed Session ${open.label} for ${open.uri}`,
                when,
                elapsedMs: Date.now() - startedMs,
            });
        }
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
     * @param name Which connection open on it, or undefined for the first.
     *
     * @returns Its UUID, if it is open.
     */
    public connectionIdFor(uri: string, name?: string): string | undefined {
        return this.#find(uri, name)?.connectionId;
    }

    /**
     * @param uri The connection to look up.
     * @param name Which connection open on it, or undefined for the first.
     *
     * @returns The open connection, if there is one.
     */
    #find(uri: string, name?: string): IOpenConnection | undefined {
        return this.#open.find((open) => {
            return open.uri === uri
                && (name === undefined || open.label === name);
        });
    }

    /**
     * @param connectionId The UUID to resolve.
     *
     * @returns The connection it identifies, if it is still open.
     */
    #sessionOf(connectionId: string): IOpenConnection | undefined {
        return this.#open.find((open) => {
            return open.connectionId === connectionId;
        });
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
