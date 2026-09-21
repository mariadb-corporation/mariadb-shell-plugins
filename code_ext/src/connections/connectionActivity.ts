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
 * @param api The API to wrap.
 * @param sessionOf Resolves a connection UUID to the connection it is.
 * @param report Where to send what happened.
 *
 * @returns The same API, reporting as it goes.
 */
export const createLoggingApi = (
    api: IMariaDbApi,
    sessionOf: SessionLookup,
    report: ActivityReporter,
): IMariaDbApi => {
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
        // on anything that is open, so they are no part of a
        // connection's log.
        listConnections: (kind) => { return api.listConnections(kind); },
        addConnection: (uri, password, kind, verify) => {
            return api.addConnection(uri, password, kind, verify);
        },
        deleteConnection: (uri, kind) => {
            return api.deleteConnection(uri, kind);
        },
        testConnection: (uri, password) => {
            return api.testConnection(uri, password);
        },
        updateConnection: (uri, newUri, kind, newKind, password) => {
            return api.updateConnection(uri, newUri, kind, newKind, password);
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
