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

/** A log whose rows are recorded, the newest put last. */
const recordingLog = (): { log: TaskActionLog; put: IActionRow[] } => {
    const put: IActionRow[] = [];
    const log = new TaskActionLog((connection, row) => {
        expect(connection).toBe("mariadb://root@h:3306");
        put.push(row);
    }, "UI Backend");

    return { log, put };
};

describe("tasks in the Actions tab", () => {
    it("puts up a task's row as it starts, the call under it", () => {
        const { log, put } = recordingLog();

        log.report({ tracked: tracked(), messages: [], first: true });

        expect(put).toHaveLength(1);
        expect(put[0]).toMatchObject({
            id: "task-t1", role: "run", message: "Dump shop to Disk",
            summary: "Starting…", kind: "pending",
            connection: "mariadb://root@h:3306", connectionLabel: "UI Backend",
        });
        expect(put[0].children).toMatchObject([{
            role: "statement", statement: "util.dump_schemas(shop, /d)",
            message: "Started", connectionLabel: "UI Backend",
        }]);
    });

    it("adds the messages under it as they come, and marks how it ended",
        () => {
            const { log, put } = recordingLog();
            log.report({ tracked: tracked(), messages: [], first: true });
            log.report({
                tracked: tracked({
                    stage: "Dumping data",
                    progress: { current: 4, total: 10, percent: 40,
                        items: "rows" },
                }),
                messages: [message(1, "Writing DDL..."),
                    message(2, "Table t has no PK", "warning")],
                first: false,
            });

            expect(put[1].summary).toBe("Dumping data 40%…");
            expect(put[1].children?.map((row) => {
                return [row.role, row.statement, row.message, row.kind];
            })).toEqual([
                ["statement", "util.dump_schemas(shop, /d)", "Started", "info"],
                ["statement", "", "Writing DDL...", "info"],
                ["warning", "Warning", "Table t has no PK", "warning"],
            ]);

            log.report({
                tracked: tracked({ status: "completed",
                    finished_at: "2026-10-10T10:00:05Z" }),
                messages: [message(3, "Total duration: 00:00:05s")],
                first: false,
            });

            // the run's own marker takes the warning its children saw
            expect(put[2]).toMatchObject({ summary: "Completed",
                kind: "warning", elapsedMs: 5000 });
            expect(put[2].children?.at(-1)?.message)
                .toBe("Total duration: 00:00:05s");
        });

    it("shows a failure with its reason", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked({ status: "failed", error: "Access denied",
                finished_at: "2026-10-10T10:00:01Z" }),
            messages: [message(1, "Access denied", "error")],
            first: false,
        });

        expect(put[1]).toMatchObject({ kind: "error",
            summary: "Failed: Access denied" });
        expect(put[1].children?.at(-1)).toMatchObject({ role: "warning",
            statement: "Error", kind: "error" });
    });

    it("keeps the newest messages of a long task", () => {
        const { log, put } = recordingLog();
        log.report({ tracked: tracked(), messages: [], first: true });
        log.report({
            tracked: tracked(),
            messages: Array.from({ length: MAX_TASK_ACTION_CHILDREN + 5 },
                (_, i) => { return message(i + 1, `line ${String(i + 1)}`); }),
            first: false,
        });

        const children = put[1].children ?? [];
        expect(children).toHaveLength(MAX_TASK_ACTION_CHILDREN);
        expect(children.at(-1)?.message)
            .toBe(`line ${String(MAX_TASK_ACTION_CHILDREN + 5)}`);
    });

    it("says what a task is doing, or what it came to", () => {
        expect(taskSummary(tracked({ stage: "Gathering information" })))
            .toBe("Gathering information…");
        expect(taskSummary(tracked({ stage: "Dumping data" },
            { cancelling: true }))).toBe("Dumping data, cancelling…");
        expect(taskSummary(tracked({ status: "cancelled" })))
            .toBe("Cancelled");
        expect(taskSummary(tracked({}, { interrupted: true })))
            .toBe("Stopped when its window closed or reloaded");
    });
});
