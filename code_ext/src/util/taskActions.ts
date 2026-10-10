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

import { isFinished, type IUtilTaskMessage } from "../mcp/utilApi.js";
import { formatTime } from "../sql/executionService.js";
import type { ActionSeverity, IActionRow } from "../webview/protocol.js";
import type { ITaskReport, ITrackedTask } from "./taskMonitor.js";

/**
 * The `util.*` tasks in the Actions tab of the MariaDB panel: one row per
 * task, on the connection it works on, kept up to date while it runs, with
 * the messages the shell reported for it - its stages, warnings, errors and
 * the summary it ends with - as the rows under it.
 */

/**
 * The most messages kept under one task's row; the newest are kept. The
 * panel keeps 2000 rows per connection in all, and the whole of a task's
 * messages is in its log file.
 */
export const MAX_TASK_ACTION_CHILDREN = 200;

/**
 * @param time A timestamp the server sent.
 *
 * @returns It as the Time column shows it, or as it came if it is not one.
 */
const timeOf = (time: string | null | undefined): string => {
    const when = time === null || time === undefined ? NaN : Date.parse(time);

    return Number.isNaN(when) ? time ?? "" : formatTime(new Date(when));
};

/**
 * @param message A message of a task.
 *
 * @returns How its row is marked.
 */
const severityOf = (message: IUtilTaskMessage): ActionSeverity => {
    return message.level === "error" ? "error"
        : message.level === "warning" ? "warning" : "info";
};

/**
 * Turns messages of a task into the rows under its own.
 *
 * A warning or an error says so where a statement row shows its SQL, as a
 * statement's warnings do; everything else leaves that column empty.
 *
 * @param tracked The task.
 * @param messages Its messages.
 *
 * @returns One row each.
 */
export const taskMessageRows = (
    tracked: ITrackedTask,
    messages: IUtilTaskMessage[],
): IActionRow[] => {
    return messages.map((message): IActionRow => {
        const kind = severityOf(message);

        return {
            id: `task-${tracked.state.task_id}-${String(message.seq)}`,
            time: timeOf(message.time),
            connection: tracked.connection,
            role: kind === "info" ? "statement" : "warning",
            statement: kind === "error" ? "Error"
                : kind === "warning" ? "Warning" : "",
            message: message.text,
            kind,
        };
    });
};

/**
 * @param tracked A task.
 *
 * @returns What it came to, or where it is, for the Information column.
 */
export const taskSummary = (tracked: ITrackedTask): string => {
    const { state } = tracked;
    if (tracked.interrupted === true) {
        return "Stopped when its window closed or reloaded";
    }
    switch (state.status) {
        case "completed": {
            return "Completed";
        }

        case "cancelled": {
            return "Cancelled";
        }

        case "failed": {
            return `Failed: ${state.error ?? "no reason given"}`;
        }

        default: {
            const percent = state.progress?.percent;
            const measured = state.progress?.items !== undefined
                && typeof percent === "number";

            return `${state.stage ?? "Starting"}`
                + (measured ? ` ${String(Math.floor(percent))}%` : "")
                + (tracked.cancelling ? ", cancelling…" : "…");
        }
    }
};

/**
 * Builds a task's row.
 *
 * @param tracked The task.
 * @param children The rows under it: the call, then its messages.
 * @param label Which connection open on the URI it is filed under.
 *
 * @returns The row.
 */
export const taskActionRow = (
    tracked: ITrackedTask,
    children: IActionRow[],
    label: string,
): IActionRow => {
    const { state } = tracked;
    const finished = isFinished(state.status) || tracked.interrupted === true;
    const worst = children.some((row) => { return row.kind === "error"; })
        ? "error"
        : children.some((row) => { return row.kind === "warning"; })
            ? "warning" : "info";
    const kind: ActionSeverity = !finished ? "pending"
        : state.status === "failed" || tracked.interrupted === true ? "error"
            : state.status === "cancelled" ? "warning"
                : worst === "error" ? "warning" : worst;
    const started = Date.parse(state.started_at ?? state.created_at);
    const ended = state.finished_at === null ? NaN
        : Date.parse(state.finished_at);

    return {
        id: `task-${state.task_id}`,
        time: timeOf(state.created_at),
        connection: tracked.connection,
        connectionLabel: label,
        role: "run",
        statement: "",
        message: state.title,
        summary: taskSummary(tracked),
        kind,
        children: children.map((row) => {
            return { ...row, connectionLabel: label };
        }),
        ...(Number.isNaN(started) || Number.isNaN(ended)
            ? {} : { elapsedMs: Math.max(0, ended - started) }),
    };
};

/**
 * Keeps each task's row: gathers its messages as they come and hands the
 * whole row on after every look at the task, to replace the one before.
 */
export class TaskActionLog {
    #children = new Map<string, IActionRow[]>();

    /**
     * @param put Puts a row up, or replaces the one with its id.
     * @param label Which connection open on a URI the tasks are filed
     *              under: the one the commands open them with.
     */
    public constructor(
        private readonly put: (connection: string, row: IActionRow) => void,
        private readonly label: string,
    ) { }

    /**
     * Takes in a look at a task.
     *
     * @param report The task and the messages that came.
     */
    public report(report: ITaskReport): void {
        const { tracked, messages } = report;
        const taskId = tracked.state.task_id;
        let children = this.#children.get(taskId);
        if (children === undefined) {
            // The call it was started with comes first, where a run's
            // first statement would.
            children = tracked.call === undefined ? [] : [{
                id: `task-${taskId}-call`,
                time: timeOf(tracked.state.created_at),
                connection: tracked.connection,
                role: "statement",
                statement: tracked.call,
                message: "Started",
                kind: "info",
            }];
            this.#children.set(taskId, children);
        }
        children.push(...taskMessageRows(tracked, messages));
        if (children.length > MAX_TASK_ACTION_CHILDREN) {
            children.splice(0, children.length - MAX_TASK_ACTION_CHILDREN);
        }

        this.put(tracked.connection,
            taskActionRow(tracked, [...children], this.label));
        if (isFinished(tracked.state.status)) {
            this.#children.delete(taskId);
        }
    }
}
