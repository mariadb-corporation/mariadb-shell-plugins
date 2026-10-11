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

import {
    GENERAL_ACTIONS,
    type SessionLookup,
} from "../connections/connectionActivity.js";
import { formatTime } from "../sql/executionService.js";
import { counted } from "../text.js";
import type { IActionRow } from "../webview/protocol.js";
import type { IMrsAction } from "./mrsActions.js";

/**
 * What the MariaDB REST Service does, in the Actions of the connection it
 * is done on: a row per action, put up as it starts - "Creating REST
 * Service '/myService'…", with the spinner a running SQL script has - and
 * replaced once it ended, by what was done or why it failed. One REST SQL
 * statement is shown beside it; several are the rows under it, each saying
 * what it does beside the statement, so the REST SQL can be learned by
 * reading. Success shows as it does for SQL: no error marker, and no row
 * with an error under it. Work on files
 * only, made on no connection, goes under General Actions where that log
 * is kept.
 */

/** Where the MRS actions go. */
export interface IMrsActivityTarget {
    /** Resolves a connection's id to what it is called. */
    sessionOf: SessionLookup;
    /** Puts up a row, or replaces the one with its id. */
    put(connection: string, row: IActionRow): void;
    /** Whether every call is logged, the work on no connection with it. */
    logAllCalls(): boolean;
}

/**
 * Reports one MRS action, as it starts or as it ended.
 *
 * @param target Where it goes.
 * @param action What is being done, or was.
 */
export const reportMrsAction = (
    target: IMrsActivityTarget,
    action: IMrsAction,
): void => {
    const session = action.connectionId === undefined ? undefined
        : target.sessionOf(action.connectionId);
    // On a connection closed since, there is no log to put it in.
    if (action.connectionId !== undefined && session === undefined) {
        return;
    }
    if (session === undefined && !target.logAllCalls()) {
        return;
    }

    const connection = session?.uri ?? GENERAL_ACTIONS;
    target.put(connection, mrsActionRow(action, connection,
        session?.label ?? ""));
};

/**
 * Builds an action's row.
 *
 * @param action The action, as it starts or as it ended.
 * @param connection Where it is filed.
 * @param label Which connection open there it was done on.
 *
 * @returns The row.
 */
export const mrsActionRow = (
    action: IMrsAction,
    connection: string,
    label: string,
): IActionRow => {
    const time = formatTime(action.when);
    const base = {
        id: action.id, time, connection, connectionLabel: label,
        message: action.message,
    };

    // Still running: the spinner, as a running SQL script has.
    if (action.pending === true) {
        return { ...base, role: "event", statement: "", kind: "pending" };
    }

    const children: IActionRow[] = (action.statements ?? []).map(
        (statement, index): IActionRow => {
            return {
                id: `${action.id}-${String(index)}`, time, connection,
                connectionLabel: label, role: "statement",
                statement: statement.sql, message: statement.message,
                kind: statement.error === undefined ? "info" : "error",
                ...(statement.elapsedMs === undefined
                    ? {} : { elapsedMs: statement.elapsedMs }),
            };
        });

    if (action.error !== undefined) {
        // What was being done stays the message, and the error is the row
        // under it, as a failed SQL script shows it: the failed statement,
        // or a row of its own where no statement failed.
        const failed = children.find((row) => { return row.kind === "error"; });
        const errorRow: IActionRow = failed ?? {
            id: `${action.id}-error`, time, connection,
            connectionLabel: label, role: "statement", statement: "",
            message: action.error, kind: "error",
        };

        return {
            ...base, role: "run", statement: "",
            summary: "Finished with 1 error", kind: "error",
            elapsedMs: action.elapsedMs, jumpToRowId: errorRow.id,
            children: failed === undefined ? [...children, errorRow]
                : children,
        };
    }

    // One statement is the row's own: its REST SQL where an event names
    // its call. Rows under it only where there are several.
    if (children.length === 1) {
        return {
            ...base, role: "event", statement: children[0].statement,
            kind: "info", elapsedMs: action.elapsedMs,
        };
    }

    if (children.length > 1) {
        return {
            ...base, role: "run", statement: "",
            summary: action.detail ?? `Finished ${counted(children.length,
                "statement")} successfully`,
            kind: "info", elapsedMs: action.elapsedMs, children,
        };
    }

    return {
        ...base, role: "event",
        // The Information column, where an event names its call.
        statement: action.detail ?? "",
        kind: "info", elapsedMs: action.elapsedMs,
    };
};
