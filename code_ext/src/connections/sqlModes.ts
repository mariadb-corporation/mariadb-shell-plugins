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

/**
 * What each open connection's `sql_mode` says about double quotes.
 *
 * Three things need it and must agree: the highlighting of `"..."`, the
 * split a run is paired by, and the gutter's statement markers. It is
 * read with one `SELECT @@sql_mode` the first time a connection is asked
 * about, and after that taken from what every script run reports - the
 * session's mode once the script is over - so a script that changed it is
 * followed without a query of its own.
 */

import { firstCell } from "../mcp/types.js";
import { errorText } from "../text.js";
import type { ConnectionManager } from "./connectionManager.js";

/** What a connection is asked, the first time, for its sql_mode. */
export const SQL_MODE_QUERY = "SELECT @@sql_mode";

/**
 * Whether a `sql_mode` value has `ANSI_QUOTES` in it.
 *
 * @param sqlMode What `SELECT @@sql_mode` returned. The server spells the
 *                combination modes out - `ANSI` comes back with
 *                `ANSI_QUOTES` among its parts - so the one name is enough.
 *
 * @returns True when double quotes quote identifiers.
 */
export const hasAnsiQuotes = (sqlMode: string): boolean => {
    return sqlMode.split(",").some((mode) => {
        return mode.trim().toUpperCase() === "ANSI_QUOTES";
    });
};

/** Called when what is known about a connection changed. */
export type SqlModeChangeListener = (uri: string) => void;

/** Whether each open connection reads `"..."` as an identifier. */
export class SqlModes {
    /**
     * Connection URI -> whether it has ANSI_QUOTES, or the first read of
     * it while that is under way, so two asking at once share one query.
     * A read only stores its answer while its own promise is still the
     * entry: a run's report that came in meanwhile is newer.
     */
    readonly #modes = new Map<string, boolean | Promise<boolean>>();
    readonly #listeners = new Set<SqlModeChangeListener>();
    readonly #unsubscribe: () => void;

    /**
     * @param connections The open connections, asked for the session to
     *                    read `sql_mode` on.
     * @param log Where to note a read that failed.
     */
    public constructor(
        private readonly connections: ConnectionManager,
        private readonly log: (message: string) => void,
    ) {
        this.#unsubscribe = connections.onDidChange(() => {
            // A closed connection may be reopened with another sql_mode;
            // what it had is not kept.
            for (const uri of this.#modes.keys()) {
                if (!connections.isConnected(uri)) {
                    this.#modes.delete(uri);
                }
            }
        });
    }

    /**
     * Listens for a connection whose mode became known or changed.
     *
     * @param listener Called with the connection's URI.
     *
     * @returns A function that stops listening.
     */
    public onDidChange(listener: SqlModeChangeListener): () => void {
        this.#listeners.add(listener);

        return () => {
            this.#listeners.delete(listener);
        };
    }

    /**
     * What is known now, without asking.
     *
     * @param uri The connection.
     *
     * @returns Whether it has ANSI_QUOTES, or undefined until read.
     */
    public known(uri: string): boolean | undefined {
        const mode = this.#modes.get(uri);

        return typeof mode === "boolean" ? mode : undefined;
    }

    /**
     * Whether a connection has ANSI_QUOTES, read from it the first time.
     *
     * @param uri The connection.
     *
     * @returns True for ANSI_QUOTES; false for a connection not open, and
     *          when the read fails, which is logged and tried again the
     *          next time.
     */
    public ansiQuotes(uri: string): Promise<boolean> {
        const mode = this.#modes.get(uri);
        if (mode !== undefined) {
            return Promise.resolve(mode);
        }
        if (!this.connections.isConnected(uri)) {
            return Promise.resolve(false);
        }

        const reading = this.#read(uri);
        this.#modes.set(uri, reading);

        return reading;
    }

    /**
     * Takes a connection's mode as a script run reported it.
     *
     * @param uri The connection.
     * @param sqlMode Its `sql_mode`, as the server spelled it.
     *
     * @returns Nothing.
     */
    public record(uri: string, sqlMode: string): void {
        this.#set(uri, hasAnsiQuotes(sqlMode));
    }

    public dispose(): void {
        this.#unsubscribe();
        this.#listeners.clear();
    }

    /**
     * Stores an answer, and says so when it is news.
     *
     * @param uri The connection.
     * @param ansiQuotes What it has.
     *
     * @returns Nothing.
     */
    #set(uri: string, ansiQuotes: boolean): void {
        if (this.#modes.get(uri) === ansiQuotes) {
            return;
        }
        this.#modes.set(uri, ansiQuotes);
        for (const listener of this.#listeners) {
            listener(uri);
        }
    }

    /**
     * Asks a connection for its `sql_mode`.
     *
     * @param uri The connection.
     *
     * @returns Whether it has ANSI_QUOTES - or what a run reported while
     *          the read was under way, which is newer.
     */
    async #read(uri: string): Promise<boolean> {
        let ansiQuotes = false;
        try {
            const connectionId = this.connections.connectionIdFor(uri);
            if (connectionId !== undefined) {
                const api = await this.connections.api();
                const value = firstCell(
                    await api.executeSql(connectionId, SQL_MODE_QUERY));
                ansiQuotes = typeof value === "string" && hasAnsiQuotes(value);
            }
        } catch (error) {
            this.log(`Could not read sql_mode on ${uri}: ${errorText(error)}`);
            if (this.#modes.get(uri) instanceof Promise) {
                // Asked again next time.
                this.#modes.delete(uri);
            }

            return this.known(uri) ?? false;
        }

        if (this.#modes.get(uri) instanceof Promise) {
            this.#set(uri, ansiQuotes);
        }

        return this.known(uri) ?? ansiQuotes;
    }
}
