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
    type ActivityReporter,
    GENERAL_ACTIONS,
    type SessionLookup,
} from "../connections/connectionActivity.js";
import { formatTime } from "../sql/executionService.js";
import type { IActionRow } from "../webview/protocol.js";
import type { IMrsAction } from "./mrsActions.js";

/**
 * What the MariaDB REST Service does, in the Actions of the connection it
 * was done on: one row per action, saying what was done and, where it
 * says more, what it came to. Work on files only, made on no connection,
 * goes under General Actions where that log is kept.
 */

/** Where the MRS actions go. */
export interface IMrsActivityTarget {
    /** Resolves a connection's id to what it is called. */
    sessionOf: SessionLookup;
    /** Puts up a row. */
    report: ActivityReporter;
    /** Puts up a row with rows under it: a failed action and its error. */
    put(connection: string, row: IActionRow): void;
    /** Whether the work made on no connection is reported. */
    logAllCalls(): boolean;
}

/** Makes the ids of failed actions' rows unique. */
let failures = 0;

/**
 * Reports one MRS action as a row of the Actions tab.
 *
 * @param target Where it goes.
 * @param action What was done.
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
    const label = session?.label ?? "";
    if (action.error !== undefined) {
        // What was being done stays the row's message, marked as failed,
        // and the error is the row under it - where the error bar and a
        // reader both look for it.
        failures += 1;
        const id = `mrs-failed-${String(failures)}`;
        const time = formatTime(action.when);
        target.put(connection, {
            id, time, connection, connectionLabel: label,
            role: "run", statement: "", message: action.message,
            // As a SQL script's run says it, and pointing at its error.
            summary: "Finished with 1 error", kind: "error",
            elapsedMs: action.elapsedMs, jumpToRowId: `${id}-error`,
            children: [{
                id: `${id}-error`, time, connection, connectionLabel: label,
                role: "statement", statement: "", message: action.error,
                kind: "error",
            }],
        });

        return;
    }

    target.report({
        connection,
        label,
        // The Information column, where an event names its call.
        call: action.detail ?? "",
        when: action.when,
        elapsedMs: action.elapsedMs,
        message: action.message,
    });
};
