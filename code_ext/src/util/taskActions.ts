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
 * what it came to under it - the totals the shell ends with, and every
 * warning and error. The steps it reports along the way are left to the
 * task's log.
 */

/**
 * The most messages kept under one task's row; the newest are kept. The
 * panel keeps 2000 rows per connection in all, and the whole of a task's
 * messages is in its log file.
 */
export const MAX_TASK_ACTION_CHILDREN = 200;

/**
 * @param message A message of a task.
 *
 * @returns How its row is marked.
 */
const severityOf = (message: IUtilTaskMessage): ActionSeverity => {
    return message.level === "error" ? "error"
        : message.level === "warning" ? "warning" : "info";
};

/** The lines of a task's end that say what it came to. */
const RESULT = new RegExp("^(?:Total duration|Schemas dumped|Tables dumped"
    + "|Rows written|Data size|Uncompressed data size):"
    + "|^Total rows affected|\\bwere loaded in\\b"
    + "|\\bwarnings? (?:were|was) reported\\b", "iu");

/**
 * @param message A message of a task.
 *
 * @returns Its text, where it is a warning, an error or one of the totals
 *          a task ends with; undefined for the steps along the way. A
 *          copy's "SRC:" / "TGT:" is left off.
 */
export const resultText = (message: IUtilTaskMessage): string | undefined => {
    const text = message.text.replace(/^(?:SRC|TGT):\s*/u, "").trim();
    if (message.level === "warning" || message.level === "error") {
        return text;
    }

    return RESULT.test(text) ? text : undefined;
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
    time: string,
): IActionRow[] => {
    const rows: IActionRow[] = [];
    for (const message of messages) {
        const text = resultText(message);
        // A copy's two halves each end with a total duration.
        if (text === undefined || rows.some((row) => {
            return row.message === text;
        })) {
            continue;
        }
        const kind = severityOf(message);
        rows.push({
            id: `task-${tracked.state.task_id}-${String(message.seq)}`,
            time,
            connection: tracked.connection,
            role: "statement",
            statement: "",
            message: text,
            kind,
        });
    }

    return rows;
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
            return "Failed";
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
    timing: { started: Date; elapsedMs?: number },
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
    return {
        id: `task-${state.task_id}`,
        time: formatTime(timing.started),
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
        // A failure points at its error, as a failed SQL run does.
        ...(kind === "error" && children.some((row) => {
            return row.kind === "error";
        })
            ? { jumpToRowId: [...children].reverse().find((row) => {
                return row.kind === "error";
            })!.id }
            : {}),
        ...(timing.elapsedMs === undefined
            ? {} : { elapsedMs: timing.elapsedMs }),
    };
};

/**
 * Keeps each task's row: gathers its messages as they come and hands the
 * whole row on after every look at the task, to replace the one before.
 */
export class TaskActionLog {
    #tasks = new Map<string, { children: IActionRow[]; started: Date }>();

    /**
     * @param put Puts a row up, or replaces the one with its id.
     * @param label Which connection open on a URI the tasks are filed
     *              under: the one the commands open them with.
     * @param now The time; the server's has whole seconds only.
     */
    public constructor(
        private readonly put: (connection: string, row: IActionRow) => void,
        private readonly label: string,
        private readonly now: () => Date = () => { return new Date(); },
    ) { }

    /**
     * Takes in a look at a task.
     *
     * @param report The task and the messages that came.
     */
    public report(report: ITaskReport): void {
        const { tracked, messages } = report;
        const taskId = tracked.state.task_id;
        const now = this.now();
        let task = this.#tasks.get(taskId);
        if (task === undefined) {
            task = { children: [], started: now };
            this.#tasks.set(taskId, task);
        }
        const { children } = task;
        const time = formatTime(now);
        children.push(...taskMessageRows(tracked, messages, time).filter(
            (row) => {
                return !children.some((kept) => {
                    return kept.message === row.message;
                });
            }));
        // A failure ends with its error as a row of its own, as a SQL
        // script's run does - unless the shell's last word already was it.
        const { status, error } = tracked.state;
        if (status === "failed" && error !== null
            && children.at(-1)?.message !== error) {
            children.push({
                id: `task-${taskId}-error`,
                time,
                connection: tracked.connection,
                role: "statement",
                statement: "",
                message: error,
                kind: "error",
            });
        }
        if (children.length > MAX_TASK_ACTION_CHILDREN) {
            children.splice(0, children.length - MAX_TASK_ACTION_CHILDREN);
        }

        const finished = isFinished(status);
        this.put(tracked.connection, taskActionRow(tracked, [...children],
            this.label, {
                started: task.started,
                ...(finished
                    ? { elapsedMs: now.getTime() - task.started.getTime() }
                    : {}),
            }));
        if (finished) {
            this.#tasks.delete(taskId);
        }
    }
}
