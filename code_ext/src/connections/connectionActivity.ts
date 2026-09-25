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

import { formatTime } from "../sql/executionService.js";
import type { IMariaDbApi } from "../mcp/types.js";
import type { IActionRow } from "../webview/protocol.js";

/**
 * Something that happened on one open connection: a `db.*` call that was
 * made on it, or its being opened or closed.
 *
 * The actions grid is the log of a connection's life, not only of the SQL
 * run on it, and this is what everything other than a run is reported as.
 */
export interface IActivityEvent {
    /** The connection URI it happened on. */
    connection: string;
    /** Which connection open on that URI: `1`, `2`, `UI Backend`. */
    label: string;
    /** The `db.*` tool that ran, with the arguments worth naming. */
    call: string;
    /** What it did, in the words the Actions column shows. */
    message: string;
    /** When it started. */
    when: Date;
    /** How long it took, in milliseconds. */
    elapsedMs: number;
    /** What went wrong, where the call failed. It replaces the message. */
    error?: string;
}

/** Where activity is reported to. */
export type ActivityReporter = (event: IActivityEvent) => void;

/**
 * What the calls made on no open connection are filed under - listing,
 * adding, editing, deleting and testing connections. It stands where a
 * connection URI would, which is how it becomes an entry of the panel's
 * connection picker; they are only reported where the user asked for it
 * (`mariadb.actions.logAllCalls`).
 */
export const GENERAL_ACTIONS = "General Actions";

/**
 * Turns an event into the row the actions grid shows it as.
 *
 * An event is a row of its own with nothing under it, which is what
 * `role: "event"` says: no twistie, and the Information column holds the
 * call rather than a statement or a run's summary.
 *
 * @param event What happened.
 * @param id An id no other action row has.
 *
 * @returns The row to put in the actions.
 */
export const activityRow = (
    event: IActivityEvent,
    id: string,
): IActionRow => {
    return {
        id,
        time: formatTime(event.when),
        connection: event.connection,
        connectionLabel: event.label,
        role: "event",
        // The Information column, where a statement row holds its SQL.
        statement: event.call,
        message: event.error ?? event.message,
        kind: event.error === undefined ? "info" : "error",
        elapsedMs: event.elapsedMs,
    };
};

/**
 * @param count How many of something came back.
 * @param singular What one of them is called.
 *
 * @returns `1 schema`, `4 schemas`.
 */
const counted = (count: number, singular: string): string => {
    return `${count} ${singular}${count === 1 ? "" : "s"}`;
};

/**
 * The arguments of a call as the Information column shows them, the ones
 * not given left out: `uri, kind=gui`. A `uri` is shown bare, as the first
 * argument of the tools that take one.
 *
 * @param args The arguments, by name.
 *
 * @returns Them, joined.
 */
const arguments_ = (args: Record<string, unknown>): string => {
    return Object.entries(args)
        .filter(([, value]) => { return value !== undefined; })
        .map(([name, value]) => {
            return name === "uri" ? String(value) : `${name}=${String(value)}`;
        })
        .join(", ");
};

/** What resolves an open connection's UUID back to what it is called. */
export type SessionLookup = (
    connectionId: string,
) => { uri: string; label: string } | undefined;

/**
 * Wraps the database API so that every call made on an open connection is
 * reported as it happens.
 *
 * Only the calls that browse a connection are wrapped. The two that open
 * and close one are reported by the `ConnectionManager`, which is what
 * knows the connection by name at that point, and running SQL reports
 * itself: a run is a whole tree of rows - the statements, their times and
 * what each one came to - which no wrapper could produce from a call and
 * its return value.
 *
 * The calls made on no open connection - the connection list and what
 * changes it - are reported too where `logAllCalls` says so, under
 * {@link GENERAL_ACTIONS}. They are off by default because the list is read
 * often: every tree refresh and every look at the panel's picker. A
 * password never appears in what is reported.
 *
 * @param api The API to wrap.
 * @param sessionOf Resolves a connection UUID to the connection it is.
 * @param report Where to send what happened.
 * @param logAllCalls Whether the calls on no connection are reported. Read
 *                    on every call, since the setting behind it can change.
 *
 * @returns The same API, reporting as it goes.
 */
export const createLoggingApi = (
    api: IMariaDbApi,
    sessionOf: SessionLookup,
    report: ActivityReporter,
    logAllCalls: () => boolean = () => { return false; },
): IMariaDbApi => {
    /**
     * Times one call made on no connection and reports it under
     * {@link GENERAL_ACTIONS}, where that is asked for.
     *
     * @param call The tool and its arguments, never a password.
     * @param describe Says what the answer was.
     * @param work The call itself.
     *
     * @returns Whatever the call returned.
     */
    const watchGeneral = async <T>(
        call: string,
        describe: (value: T) => string,
        work: () => Promise<T>,
    ): Promise<T> => {
        if (!logAllCalls()) {
            return await work();
        }

        const when = new Date();
        const startedMs = Date.now();
        const event = { connection: GENERAL_ACTIONS, label: "", call, when };

        try {
            const value = await work();
            report({
                ...event,
                elapsedMs: Date.now() - startedMs,
                message: describe(value),
            });

            return value;
        } catch (error) {
            report({
                ...event,
                elapsedMs: Date.now() - startedMs,
                message: "",
                error: error instanceof Error ? error.message : String(error),
            });

            throw error;
        }
    };

    /**
     * Times one call and reports what it did.
     *
     * The connection is resolved before the call rather than after it,
     * because a call that closes it - or one that outlives a close
     * running beside it - would otherwise have nothing left to report on.
     *
     * @param connectionId The connection the call is made on.
     * @param call The `db.*` tool, as the Information column shows it.
     * @param describe Says what the answer was, once there is one.
     * @param work The call itself.
     *
     * @returns Whatever the call returned.
     */
    const watch = async <T>(
        connectionId: string,
        call: string,
        describe: (value: T) => string,
        work: () => Promise<T>,
    ): Promise<T> => {
        const session = sessionOf(connectionId);
        const when = new Date();
        const startedMs = Date.now();

        try {
            const value = await work();
            if (session) {
                report({
                    connection: session.uri,
                    label: session.label,
                    call,
                    when,
                    elapsedMs: Date.now() - startedMs,
                    message: describe(value),
                });
            }

            return value;
        } catch (error) {
            if (session) {
                report({
                    connection: session.uri,
                    label: session.label,
                    call,
                    when,
                    elapsedMs: Date.now() - startedMs,
                    message: "",
                    error: error instanceof Error
                        ? error.message
                        : String(error),
                });
            }

            throw error;
        }
    };

    return {
        // Not connection scoped: these work on the configured list, not
        // on anything that is open, so they are no part of a connection's
        // log - only of the general one, where that is kept.
        listConnections: (kind) => {
            return watchGeneral(
                `db.list_connections(${arguments_({ kind })})`,
                (uris) => {
                    return `Listed ${counted(uris.length, "connection")}`;
                },
                () => { return api.listConnections(kind); },
            );
        },
        listConnectionEntries: (kind) => {
            // `kind=all` for both lists at once.
            return watchGeneral(
                `db.list_connections(${arguments_({ kind })})`,
                (entries) => {
                    return `Listed ${counted(entries.length, "connection")}`;
                },
                () => { return api.listConnectionEntries(kind); },
            );
        },
        addConnection: (uri, password, kind, verify, path) => {
            return watchGeneral(
                `db.add_connection(${arguments_({ uri, kind, verify, path })})`,
                (stored) => { return `Stored ${stored}`; },
                () => {
                    return api.addConnection(uri, password, kind, verify, path);
                },
            );
        },
        deleteConnection: (uri, kind) => {
            return watchGeneral(
                `db.delete_connection(${arguments_({ uri, kind })})`,
                (deleted) => { return `Deleted ${deleted}`; },
                () => { return api.deleteConnection(uri, kind); },
            );
        },
        testConnection: (uri, password) => {
            return watchGeneral(
                `db.test_connection(${arguments_({ uri })})`,
                (answer) => { return answer; },
                () => { return api.testConnection(uri, password); },
            );
        },
        updateConnection: (uri, newUri, kind, newKind, password, newPath) => {
            return watchGeneral(
                `db.update_connection(${arguments_({
                    uri,
                    new_uri: newUri,
                    kind,
                    new_kind: newKind,
                    // Whether one was given, never what it is.
                    password: password === undefined ? undefined : "***",
                    new_path: newPath,
                })})`,
                (updated) => { return `Updated ${updated}`; },
                () => {
                    return api.updateConnection(
                        uri, newUri, kind, newKind, password, newPath);
                },
            );
        },

        // Reported by the manager, which knows what the connection is
        // called: `connect` before there is a UUID to look one up by, and
        // `close` after there is no longer a connection to look up.
        connect: (uri) => { return api.connect(uri); },
        close: (connectionId) => { return api.close(connectionId); },

        listSchemas: (connectionId) => {
            return watch(
                connectionId,
                "db.list_schemas()",
                (schemas) => {
                    return `Listed ${counted(schemas.length, "schema")}`;
                },
                () => { return api.listSchemas(connectionId); },
            );
        },

        listObjects: (connectionId, schemaName, objectType) => {
            return watch(
                connectionId,
                `db.list_objects(${schemaName}, ${objectType})`,
                (objects) => {
                    // Every object type pluralizes with an s.
                    return `Listed ${counted(objects.length, objectType)}`
                        + ` in ${schemaName}`;
                },
                () => {
                    return api.listObjects(connectionId, schemaName,
                        objectType);
                },
            );
        },

        getObjectDetails: (
            connectionId,
            schemaName,
            objectName,
            objectType,
        ) => {
            return watch(
                connectionId,
                `db.get_object_details(${schemaName}, ${objectName}, `
                + `${objectType})`,
                () => {
                    return `Described the ${objectType} `
                        + `${schemaName}.${objectName}`;
                },
                () => {
                    return api.getObjectDetails(connectionId, schemaName,
                        objectName, objectType);
                },
            );
        },

        // Reported as a run, by the execution service.
        executeScript: (connectionId, sqlScript, stopOnError) => {
            return api.executeScript(connectionId, sqlScript, stopOnError);
        },
    };
};
