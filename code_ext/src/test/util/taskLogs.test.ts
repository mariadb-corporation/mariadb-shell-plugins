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

import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { IUtilTaskState } from "../../mcp/utilApi.js";
import { TaskLogs } from "../../util/taskLogs.js";
import type { ITrackedTask } from "../../util/taskMonitor.js";

const tracked = (taskId: string, state: Partial<IUtilTaskState> = {}):
    ITrackedTask => {
    return {
        connection: "mariadb://root@h:3306",
        call: "util.load_dump(/d)",
        cancelling: false,
        state: {
            task_id: taskId, kind: "load_dump", title: "Load a Dump into h",
            connection_id: "c1", status: "running", cancel_requested: false,
            created_at: "2026-10-10T10:00:00Z", started_at: null,
            finished_at: null, stage: null, progress: null, stages: [],
            next_since: 0, result: null, error: null, ...state,
        },
    };
};

describe("task logs", () => {
    it("writes a task's log as it goes, from the call to how it ended",
        async () => {
            const folder = join(mkdtempSync(join(tmpdir(), "task-logs-")),
                "logs");
            const logs = new TaskLogs(folder, () => { /* not needed */ });

            await logs.report({ tracked: tracked("t1"), messages: [],
                first: true });
            await logs.report({
                tracked: tracked("t1"),
                messages: [
                    { seq: 1, time: "2026-10-10T10:00:01Z", level: "status",
                        text: "Loading DDL..." },
                    { seq: 2, time: "2026-10-10T10:00:02Z", level: "error",
                        text: "Duplicate key" },
                ],
                first: false,
            });
            // read while it runs: what came so far is there
            expect(readFileSync(logs.pathOf("t1"), "utf8"))
                .toContain("Loading DDL...");

            await logs.report({
                tracked: tracked("t1", { status: "failed",
                    error: "Duplicate key",
                    finished_at: "2026-10-10T10:00:03Z" }),
                messages: [],
                first: false,
            });

            expect(readFileSync(logs.pathOf("t1"), "utf8")).toBe([
                "Load a Dump into h",
                "Connection: mariadb://root@h:3306",
                "Call: util.load_dump(/d)",
                "Started: 2026-10-10T10:00:00Z",
                "",
                "2026-10-10T10:00:01Z Loading DDL...",
                "2026-10-10T10:00:02Z ERROR: Duplicate key",
                "2026-10-10T10:00:03Z FAILED: Duplicate key",
                "",
            ].join("\n"));
        });

    it("deletes the logs of the tasks no longer listed", async () => {
        const folder = mkdtempSync(join(tmpdir(), "task-logs-"));
        const logs = new TaskLogs(folder, () => { /* not needed */ });
        for (const taskId of ["t1", "t2"]) {
            await logs.report({ tracked: tracked(taskId), messages: [],
                first: true });
        }
        writeFileSync(join(folder, "notes.txt"), "not a log");

        await logs.prune(["t2"]);

        expect(existsSync(logs.pathOf("t1"))).toBe(false);
        expect(existsSync(logs.pathOf("t2"))).toBe(true);
        expect(existsSync(join(folder, "notes.txt"))).toBe(true);
    });

    it("tells a log it could not write, and goes on", async () => {
        const file = join(mkdtempSync(join(tmpdir(), "task-logs-")), "file");
        writeFileSync(file, "");
        const told: string[] = [];
        // a file where the folder should be
        const logs = new TaskLogs(file, (text) => { told.push(text); });

        await logs.report({ tracked: tracked("t1"), messages: [],
            first: true });

        expect(told[0]).toContain("could not be written");
    });
});
