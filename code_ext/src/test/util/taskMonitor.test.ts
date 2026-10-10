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

import { beforeEach, describe, expect, it } from "vitest";

import type {
    IUtilApi,
    IUtilTaskState,
    UtilTaskStatus,
} from "../../mcp/utilApi.js";
import { TaskMonitor } from "../../util/taskMonitor.js";
import {
    TaskTreeItem,
    TasksTreeProvider,
    taskDescription,
    taskTooltip,
} from "../../util/tasksTreeProvider.js";
import {
    errorMessages,
    informationMessages,
    MockOutputChannel,
    resetVscodeMock,
    warningMessages,
    withProgressCalls,
} from "../mocks/vscode.js";

const state = (overrides: Partial<IUtilTaskState>): IUtilTaskState => {
    return {
        task_id: "t1", kind: "dump_schemas", title: "Dump shop to Disk",
        connection_id: "c1", status: "running", cancel_requested: false,
        created_at: "2026-10-10T10:00:00Z", started_at: "2026-10-10T10:00:00Z",
        finished_at: null, stage: null, progress: null, stages: [],
        messages: [], messages_dropped: 0, next_since: 0, result: null,
        error: null, ...overrides,
    };
};

/** An API answering getTask with the states given, one per call. */
const fakeApi = (states: Array<IUtilTaskState | Error>): {
    api: IUtilApi; since: number[]; cancelled: string[];
} => {
    const since: number[] = [];
    const cancelled: string[] = [];
    const api = {
        getTask: (_taskId: string, from = 0): Promise<IUtilTaskState> => {
            since.push(from);
            const next = states.shift();
            if (next instanceof Error) {
                return Promise.reject(next);
            }

            return Promise.resolve(next ?? state({ status: "completed" }));
        },
        cancelTask: (taskId: string): Promise<IUtilTaskState> => {
            cancelled.push(taskId);

            return Promise.resolve(state({ cancel_requested: true }));
        },
    } as unknown as IUtilApi;

    return { api, since, cancelled };
};

const finished = (status: UtilTaskStatus, extra: Partial<IUtilTaskState> = {}):
    IUtilTaskState => {
    return state({ status, finished_at: "2026-10-10T10:01:00Z", ...extra });
};

describe("TaskMonitor", () => {
    let output: MockOutputChannel;
    const logs: string[] = [];

    beforeEach(() => {
        resetVscodeMock();
        output = new MockOutputChannel("MariaDB Tasks");
        logs.length = 0;
    });

    const monitorWith = (api: IUtilApi): TaskMonitor => {
        return new TaskMonitor(() => { return Promise.resolve(api); }, output,
            (message) => { logs.push(message); });
    };

    it("follows a task to its end, with its messages and progress", async () => {
        const { api, since } = fakeApi([
            state({
                stage: "Writing DDL", next_since: 1,
                progress: { current: 1, total: 2, percent: 50, total_known: true },
                messages: [{ seq: 1, time: "t", level: "status", text: "Writing DDL..." }],
            }),
            state({
                stage: "Dumping data", next_since: 2,
                progress: { current: 30, total: 100, percent: 30, items: "rows" },
                messages: [{ seq: 2, time: "t", level: "warning", text: "Table t has no PK" }],
            }),
            finished("completed", { next_since: 2, result: { output_url: "/d" } }),
        ]);
        const monitor = monitorWith(api);

        const end = await monitor.follow({ task_id: "t1", status: "running" },
            { title: "Dump shop to Disk", connection: "root@h" });

        expect(end.status).toBe("completed");
        // each call asks only for the messages after the last
        expect(since).toEqual([0, 1, 2]);
        expect(output.lines).toEqual([
            "[Dump shop to Disk] Started.",
            "[Dump shop to Disk] Writing DDL...",
            "[Dump shop to Disk] WARNING: Table t has no PK",
            "[Dump shop to Disk] Completed.",
        ]);
        expect(withProgressCalls[0].options).toMatchObject({
            title: "Dump shop to Disk", cancellable: true,
        });
        // a counting stage shows its name; a stage moving data its percent
        expect(withProgressCalls[0].reported).toEqual([
            "Writing DDL", "Dumping data: 30%",
        ]);
        expect(informationMessages).toEqual(["Dump shop to Disk completed."]);
        expect(monitor.tasks.map((task) => { return task.state.status; }))
            .toEqual(["completed"]);
    });

    it("cancels a task from its notification", async () => {
        const { api, cancelled } = fakeApi([
            state({ stage: "Dumping data" }),
            finished("cancelled", { error: "Interrupted by user" }),
        ]);
        const monitor = monitorWith(api);
        const following = monitor.follow({ task_id: "t1", status: "running" },
            { title: "Dump", connection: "root@h" });
        withProgressCalls[0].cancel();

        expect((await following).status).toBe("cancelled");
        expect(cancelled).toEqual(["t1"]);
        expect(warningMessages).toEqual(["Dump was cancelled."]);
    });

    it("reports a failure, and calls back once it ended", async () => {
        const { api } = fakeApi([finished("failed", { error: "Access denied" })]);
        const done: string[] = [];

        await monitorWith(api).follow({ task_id: "t1", status: "running" },
            { title: "Load", connection: "root@h",
                onDone: (end) => { done.push(end.status); } });

        expect(errorMessages).toEqual(["Load failed: Access denied"]);
        expect(output.lines.at(-1)).toBe("[Load] Failed: Access denied");
        expect(done).toEqual(["failed"]);
    });

    it("gives up on a task the server no longer knows", async () => {
        const { api } = fakeApi([new Error("There is no task with the id 't1'.")]);

        const end = await monitorWith(api).follow(
            { task_id: "t1", status: "running" },
            { title: "Load", connection: "root@h" });

        expect(end.status).toBe("failed");
        expect(end.error).toContain("Lost track of the task");
        expect(logs[0]).toContain("Lost track of the task Load");
    });

    it("removes only finished tasks from the list", async () => {
        const { api } = fakeApi([finished("completed")]);
        const monitor = monitorWith(api);
        await monitor.follow({ task_id: "t1", status: "running" },
            { title: "Dump", connection: "root@h" });

        monitor.remove("t1");
        expect(monitor.tasks).toEqual([]);
    });
});

describe("the Tasks view", () => {
    beforeEach(() => { resetVscodeMock(); });

    it("describes a task by its state and progress", () => {
        const running = { connection: "root@h", cancelling: false,
            state: state({ stage: "Dumping data",
                progress: { current: 1, total: 4, percent: 25, items: "bytes" } }) };
        expect(taskDescription(running)).toBe("Dumping data 25%");
        expect(taskDescription({ ...running, cancelling: true }))
            .toBe("cancelling...");
        expect(taskDescription({ ...running, state: finished("failed") }))
            .toBe("failed");

        const item = new TaskTreeItem(running);
        expect(item.contextValue).toBe("mariadbTask.running");
        expect(new TaskTreeItem({ ...running, state: finished("completed") })
            .contextValue).toBe("mariadbTask.finished");
    });

    it("lists the stages in the tooltip", () => {
        const tracked = { connection: "root@h", cancelling: false,
            state: finished("failed", {
                stages: [{ name: "Writing DDL", status: "completed", seconds: 0.25 },
                    { name: "Dumping data", status: "failed", seconds: null }],
                error: "Disk full",
            }) };
        expect(taskTooltip(tracked)).toBe(["Dump shop to Disk", "root@h",
            "✓ Writing DDL (0.3 s)", "• Dumping data", "Disk full"].join("\n"));
    });

    it("lists the monitor's tasks, the newest first", async () => {
        const output = new MockOutputChannel("t");
        const { api } = fakeApi([finished("completed"),
            finished("completed", { task_id: "t2" })]);
        const monitor = new TaskMonitor(() => { return Promise.resolve(api); },
            output, () => { /* not needed */ });
        const tree = new TasksTreeProvider(monitor);
        await monitor.follow({ task_id: "t1", status: "running" },
            { title: "First", connection: "c" });
        await monitor.follow({ task_id: "t2", status: "running" },
            { title: "Second", connection: "c" });

        expect(tree.getChildren().map((task) => { return task.state.task_id; }))
            .toEqual(["t2", "t1"]);
    });
});
