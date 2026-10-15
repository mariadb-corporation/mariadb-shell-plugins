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

import { describe, expect, it } from "vitest";

import type { IUtilTaskMessage, IUtilTaskState } from "../../mcp/utilApi.js";
import {
    MAX_TASK_ACTION_CHILDREN,
    resultText,
    TaskActionLog,
    taskSummary,
} from "../../util/taskActions.js";
import type { ITrackedTask } from "../../util/taskMonitor.js";
import type { IActionRow } from "../../webview/protocol.js";

const tracked = (state: Partial<IUtilTaskState> = {},
    extra: Partial<ITrackedTask> = {}): ITrackedTask => {
    return {
        connection: "mariadb://root@h:3306",
        call: "util.dump_schemas(shop, /d)",
        cancelling: false,
        state: {
            task_id: "t1", kind: "dump_schemas", title: "Dump shop to Disk",
            connection_id: "c1", status: "running", cancel_requested: false,
            created_at: "2026-10-10T10:00:00Z",
            started_at: "2026-10-10T10:00:00Z", finished_at: null,
            stage: null, progress: null, stages: [], messages: [],
            next_since: 0, result: null, error: null, ...state,
        },
        ...extra,
    };
};

const message = (seq: number, text: string, level = "status"):
    IUtilTaskMessage => {
    return { seq, time: "2026-10-10T10:00:01Z", level, text };
};

/** A log whose rows are recorded, on a clock a second per look. */
const recordingLog = (): { log: TaskActionLog; put: IActionRow[] } => {
    const put: IActionRow[] = [];
    let tick = 0;
    const log = new TaskActionLog((connection, row) => {
        expect(connection).toBe("mariadb://root@h:3306");
        put.push(row);
    }, "UI Backend", () => {
        tick += 1;

        return new Date(2026, 9, 10, 12, 0, tick, 250);
    });

    return { log, put };
};

describe("tasks in the Actions tab", () => {
    it("offers a failed task's log on its row, and only a failed one's",
        () => {
            const put: IActionRow[] = [];
            const log = new TaskActionLog((_connection, row) => {
                put.push(row);
            }, "UI Backend", undefined, (taskId) => {
                return `/logs/${taskId}.log`;
            });

            log.report({ tracked: tracked({ task_id: "ok", status: "completed",
                finished_at: "2026-10-10T10:00:02Z" }), messages: [],
                first: true });
            log.report({ tracked: tracked({ task_id: "bad", status: "failed",
                error: "Access denied", finished_at: "2026-10-10T10:00:02Z" }),
                messages: [], first: true });

            expect(put[0]).not.toHaveProperty("logPath");
            expect(put[1]).toMatchObject({ kind: "error",
                logPath: "/logs/bad.log" });
        });

    it("puts up a task that did not start, its error under it", () => {
        const { log, put } = recordingLog();

        log.startFailed("mariadb://root@h:3306", "Dump shop to Disk",
            "util.dump_schemas(shop, /tmp/d)", "Access denied");

        expect(put).toHaveLength(1);
        expect(put[0]).toMatchObject({
            role: "run", kind: "error", message: "Dump shop to Disk",
            summary: "Did not start", connectionLabel: "UI Backend",
            jumpToRowId: `${put[0]!.id}-error`,
        });
        expect(put[0]!.children).toEqual([expect.objectContaining({
            id: `${put[0]!.id}-error`, kind: "error", message: "Access denied",
            statement: "util.dump_schemas(shop, /tmp/d)",
        })]);
    });

    it("puts up a task's row as it starts", () => {
        const { log, put } = recordingLog();

        log.report({ tracked: tracked(), messages: [], first: true });

        expect(put).toHaveLength(1);
        expect(put[0]).toMatchObject({
            id: "task-t1", role: "run", message: "Dump shop to Disk",
            summary: "Starting\u2026", kind: "pending", time: "12:00:01.250",
            connection: "mariadb://root@h:3306", connectionLabel: "UI Backend",
            children: [],
        });
        expect(put[0]).not.toHaveProperty("elapsedMs");
    });

    it("puts what it came to under it, not the steps along the way", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked({
                stage: "Dumping data",
                progress: { current: 4, total: 10, percent: 40,
                    items: "rows" },
            }),
            messages: [message(1, "Writing DDL..."),
                message(2, "Acquiring global read lock"),
                message(3, "Table t has no PK", "warning")],
            first: false,
        });

        expect(put[1].summary).toBe("Dumping data 40%\u2026");
        expect(put[1].children?.map((row) => {
            return [row.message, row.kind];
        })).toEqual([["Table t has no PK", "warning"]]);

        log.report({
            tracked: tracked({ status: "completed" }),
            messages: [message(4, "Dump duration: 00:00:05s"),
                message(5, "Total duration: 00:00:05s"),
                message(6, "Rows written: 4"),
                message(7, "Average throughput: 42.00 B/s")],
            first: false,
        });

        // the run's own marker takes the warning its children saw, and its
        // time is the extension's own, to the millisecond
        expect(put[2]).toMatchObject({ summary: "Completed",
            kind: "warning", elapsedMs: 2000 });
        // the totals are one row, as tall as its lines
        expect(put[2].children).toMatchObject([
            { message: "Table t has no PK", kind: "warning" },
            { id: "task-t1-results", kind: "info", multiline: true,
                message: "Total duration: 00:00:05s\nRows written: 4" },
        ]);
    });

    it("tells a total from a step, a copy's halves included", () => {
        expect(resultText(message(1, "SRC: Rows written: 20006")))
            .toBe("Rows written: 20006");
        expect(resultText(message(1, "TGT: 4 chunks (20.01K rows, 2.13 MB) "
            + "for 4 tables in 1 schemas were loaded in 0 sec")))
            .toMatch(/^4 chunks/u);
        expect(resultText(message(1, "0 warnings were reported during the "
            + "load."))).toBeDefined();
        expect(resultText(message(1, "Total rows affected in s.t: Records: 2"
            + "  Deleted: 0"))).toBeDefined();
        expect(resultText(message(1, "SRC: Writing global DDL files")))
            .toBeUndefined();
        expect(resultText(message(1, "Starting data load"))).toBeUndefined();
    });

    it("shows a copy's total duration once, from either half", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked({ status: "completed" }),
            messages: [message(1, "SRC: Total duration: 00:00:00s"),
                message(2, "TGT: Total duration: 00:00:00s")],
            first: false,
        });

        expect(put[1].children?.[0].message)
            .toBe("Total duration: 00:00:00s");
    });

    it("adds to the totals as they come over several looks", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({ tracked: tracked(),
            messages: [message(1, "Schemas dumped: 1")], first: false });
        log.report({ tracked: tracked({ status: "completed" }),
            messages: [message(2, "Rows written: 4")], first: false });

        expect(put[2].children).toHaveLength(1);
        expect(put[2].children?.[0].message)
            .toBe("Schemas dumped: 1\nRows written: 4");
    });

    it("shows a failure with its reason as the row under it", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked({ status: "failed", error: "Access denied" }),
            messages: [message(1, "Writing DDL...")],
            first: false,
        });

        expect(put[1]).toMatchObject({ kind: "error", summary: "Failed",
            jumpToRowId: "task-t1-error" });
        expect(put[1].children).toMatchObject([{
            id: "task-t1-error", message: "Access denied", kind: "error",
        }]);
    });

    it("does not repeat an error the shell already reported last", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked({ status: "failed", error: "Access denied" }),
            messages: [message(1, "Access denied", "error")],
            first: false,
        });

        expect(put[1].children).toHaveLength(1);
        expect(put[1].jumpToRowId).toBe("task-t1-1");
    });

    it("keeps the newest of very many warnings", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked(),
            messages: Array.from({ length: MAX_TASK_ACTION_CHILDREN + 5 },
                (_, i) => {
                    return message(i + 1, `warning ${String(i + 1)}`,
                        "warning");
                }),
            first: false,
        });

        const children = put[1].children ?? [];
        expect(children).toHaveLength(MAX_TASK_ACTION_CHILDREN);
        expect(children.at(-1)?.message)
            .toBe(`warning ${String(MAX_TASK_ACTION_CHILDREN + 5)}`);
    });

    it("says what a task is doing, or what it came to", () => {
        expect(taskSummary(tracked({ stage: "Gathering information" })))
            .toBe("Gathering information\u2026");
        expect(taskSummary(tracked({ stage: "Dumping data" },
            { cancelling: true }))).toBe("Dumping data, cancelling\u2026");
        expect(taskSummary(tracked({ status: "cancelled" })))
            .toBe("Cancelled");
        expect(taskSummary(tracked({}, { interrupted: true })))
            .toBe("Stopped when its window closed or reloaded");
    });
});
