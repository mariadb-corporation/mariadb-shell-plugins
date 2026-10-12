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

import type { ConnectionManager } from "./connectionManager.js";

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
     * Connection URI -> whether it has ANSI_QUOTES. A promise while the
     * first read is under way, so two asking at once share one query.
     */
    readonly #reading = new Map<string, Promise<boolean>>();
    /** Connection URI -> the answer, once there is one. */
    readonly #known = new Map<string, boolean>();
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
            for (const uri of [...this.#known.keys(),
                ...this.#reading.keys()]) {
                if (!connections.isConnected(uri)) {
                    this.#known.delete(uri);
                    this.#reading.delete(uri);
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
        return this.#known.get(uri);
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
        const known = this.#known.get(uri);
        if (known !== undefined) {
            return Promise.resolve(known);
        }
        if (!this.connections.isConnected(uri)) {
            return Promise.resolve(false);
        }

        let reading = this.#reading.get(uri);
        if (!reading) {
            reading = this.#read(uri);
            this.#reading.set(uri, reading);
        }

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
        this.#reading.delete(uri);
        if (this.#known.get(uri) === ansiQuotes) {
            return;
        }
        this.#known.set(uri, ansiQuotes);
        for (const listener of this.#listeners) {
            listener(uri);
        }
    }

    /**
     * Asks a connection for its `sql_mode`.
     *
     * @param uri The connection.
     *
     * @returns Whether it has ANSI_QUOTES.
     */
    async #read(uri: string): Promise<boolean> {
        try {
            const connectionId = this.connections.connectionIdFor(uri);
            if (connectionId === undefined) {
                this.#reading.delete(uri);

                return false;
            }
            const api = await this.connections.api();
            const result = await api.executeSql(connectionId,
                "SELECT @@sql_mode");
            const row = result.result_sets?.[0]?.rows[0];
            const value = row === undefined ? undefined : Object.values(row)[0];
            const ansiQuotes = typeof value === "string"
                && hasAnsiQuotes(value);
            // A run may have reported the mode while this was under way;
            // what it said is newer.
            if (this.#reading.has(uri)) {
                this.#set(uri, ansiQuotes);
            }

            return this.#known.get(uri) ?? ansiQuotes;
        } catch (error) {
            this.#reading.delete(uri);
            this.log(`Could not read sql_mode on ${uri}: `
                + `${error instanceof Error ? error.message : String(error)}`);

            return false;
        }
    }
}
