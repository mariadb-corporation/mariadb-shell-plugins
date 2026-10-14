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
 * The most warnings and errors kept under one task's row; the newest are
 * kept. The panel keeps 2000 rows per connection in all, and the whole of
 * a task's messages is in its log file.
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
 * Sorts messages of a task into what goes under its row: each warning and
 * error a row of its own, and the totals gathered into one.
 *
 * @param tracked The task.
 * @param messages Its messages.
 * @param time When they came.
 *
 * @returns The warnings' and errors' rows, and the totals' lines.
 */
export const sortMessages = (
    tracked: ITrackedTask,
    messages: IUtilTaskMessage[],
    time: string,
): { issues: IActionRow[]; results: string[] } => {
    const issues: IActionRow[] = [];
    const results: string[] = [];
    for (const message of messages) {
        const text = resultText(message);
        if (text === undefined) {
            continue;
        }
        const kind = severityOf(message);
        if (kind === "info") {
            results.push(text);
        } else {
            issues.push({
                id: `task-${tracked.state.task_id}-${String(message.seq)}`,
                time,
                connection: tracked.connection,
                role: "statement",
                statement: "",
                message: text,
                kind,
            });
        }
    }

    return { issues, results };
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
 * @param timing When it started and, once over, how long it took.
 * @param logPath The task's log, which a failed task's row offers to
 *                open: it holds every message, where the row keeps the
 *                last few.
 *
 * @returns The row.
 */
export const taskActionRow = (
    tracked: ITrackedTask,
    children: IActionRow[],
    label: string,
    timing: { started: Date; elapsedMs?: number },
    logPath?: string,
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
        ...(kind === "error" && logPath !== undefined ? { logPath } : {}),
    };
};

/**
 * Keeps each task's row: gathers its messages as they come and hands the
 * whole row on after every look at the task, to replace the one before.
 */
export class TaskActionLog {
    #tasks = new Map<string, {
        issues: IActionRow[];
        results: string[];
        resultsTime?: string;
        started: Date;
    }>();

    /**
     * @param put Puts a row up, or replaces the one with its id.
     * @param label Which connection open on a URI the tasks are filed
     *              under: the one the commands open them with.
     * @param now The time; the server's has whole seconds only.
     * @param logPathOf Where a task's log is, for a failed task's row to
     *                  open.
     */
    public constructor(
        private readonly put: (connection: string, row: IActionRow) => void,
        private readonly label: string,
        private readonly now: () => Date = () => { return new Date(); },
        private readonly logPathOf?: (taskId: string) => string,
    ) { }

    /** Makes the ids of starts that failed unique. */
    #failedStarts = 0;

    /**
     * Puts up a task that never started - the call itself was refused, so
     * there is no task to follow - as a failed task's row, its error the
     * row under it.
     *
     * @param connection The connection it was to run on.
     * @param title The task's title, "Dump shop to Disk".
     * @param call The call, as the task's row would show it.
     * @param error Why it did not start.
     */
    public startFailed(
        connection: string,
        title: string,
        call: string,
        error: string,
    ): void {
        this.#failedStarts += 1;
        const id = `task-start-${String(this.#failedStarts)}`;
        const time = formatTime(this.now());
        const errorRow: IActionRow = {
            id: `${id}-error`, time, connection, connectionLabel: this.label,
            role: "statement", statement: call, message: error, kind: "error",
        };
        this.put(connection, {
            id, time, connection, connectionLabel: this.label, role: "run",
            statement: "", message: title, summary: "Did not start",
            kind: "error", jumpToRowId: errorRow.id, children: [errorRow],
        });
    }

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
            task = { issues: [], results: [], started: now };
            this.#tasks.set(taskId, task);
        }
        const time = formatTime(now);
        const sorted = sortMessages(tracked, messages, time);
        for (const issue of sorted.issues) {
            if (!task.issues.some((kept) => {
                return kept.message === issue.message;
            })) {
                task.issues.push(issue);
            }
        }
        // A copy's two halves each end with a total duration.
        for (const line of sorted.results) {
            if (!task.results.includes(line)) {
                task.results.push(line);
                task.resultsTime ??= time;
            }
        }
        if (task.issues.length > MAX_TASK_ACTION_CHILDREN) {
            task.issues.splice(0,
                task.issues.length - MAX_TASK_ACTION_CHILDREN);
        }

        const children = [...task.issues];
        // What it came to is one row, as tall as its lines.
        if (task.results.length > 0) {
            children.push({
                id: `task-${taskId}-results`,
                time: task.resultsTime ?? time,
                connection: tracked.connection,
                role: "statement",
                statement: "",
                message: task.results.join("\n"),
                kind: "info",
                multiline: true,
            });
        }
        // A failure ends with its error as a row of its own, as a SQL
        // script's run does - unless the shell already reported it.
        const { status, error } = tracked.state;
        if (status === "failed" && error !== null
            && !task.issues.some((row) => { return row.message === error; })) {
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

        const finished = isFinished(status);
        this.put(tracked.connection, taskActionRow(tracked, children,
            this.label, {
                started: task.started,
                ...(finished
                    ? { elapsedMs: now.getTime() - task.started.getTime() }
                    : {}),
            }, this.logPathOf?.(taskId)));
        if (finished) {
            this.#tasks.delete(taskId);
        }
    }
}
