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

import type { IToolCaller } from "../../mcp/mariaDbApi.js";
import type { IToolResult } from "../../mcp/protocol.js";
import { START_TIMEOUT_MS, UtilApi, isFinished } from "../../mcp/utilApi.js";

interface ICall {
    name: string;
    args: Record<string, unknown>;
    timeoutMs?: number;
}

const caller = (answer: unknown[]): { caller: IToolCaller; calls: ICall[] } => {
    const calls: ICall[] = [];

    return {
        calls,
        caller: {
            callTool: (name, args, timeoutMs) => {
                calls.push({ name, args, ...(timeoutMs === undefined ? {}
                    : { timeoutMs }) });
                const result: IToolResult = {
                    content: answer.map((value) => {
                        return { type: "text", text: JSON.stringify(value) };
                    }),
                };

                return Promise.resolve(result);
            },
        },
    };
};

const STARTED = { task_id: "t1", status: "running" };

describe("UtilApi", () => {
    it("starts every utility with its arguments, and options only when given",
        async () => {
            const { caller: c, calls } = caller([STARTED]);
            const api = new UtilApi(c);

            expect(await api.dumpInstance("c1", "/d")).toEqual(STARTED);
            await api.dumpSchemas("c1", ["a"], "/d", { threads: 2 });
            await api.dumpTables("c1", "a", ["t"], "/d", {});
            await api.exportTable("c1", "a.t", "/f.tsv");
            await api.loadDump("c1", "/d", { schema: "b" });
            await api.importTable("c1", ["/a", "/b"]);
            await api.copyInstance("c1", "c2");
            await api.copySchemas("c1", ["a"], "c2");
            await api.copyTables("c1", "a", ["t"], "c2", { schema: "z" });

            expect(calls).toEqual([
                { name: "util.dump_instance", args: { connection_id: "c1", output_url: "/d" }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.dump_schemas", args: { connection_id: "c1", schemas: ["a"], output_url: "/d", options: { threads: 2 } }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.dump_tables", args: { connection_id: "c1", schema: "a", tables: ["t"], output_url: "/d" }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.export_table", args: { connection_id: "c1", table: "a.t", output_url: "/f.tsv" }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.load_dump", args: { connection_id: "c1", url: "/d", options: { schema: "b" } }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.import_table", args: { connection_id: "c1", urls: ["/a", "/b"] }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.copy_instance", args: { connection_id: "c1", target_connection_id: "c2" }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.copy_schemas", args: { connection_id: "c1", schemas: ["a"], target_connection_id: "c2" }, timeoutMs: START_TIMEOUT_MS },
                { name: "util.copy_tables", args: { connection_id: "c1", schema: "a", tables: ["t"], target_connection_id: "c2", options: { schema: "z" } }, timeoutMs: START_TIMEOUT_MS },
            ]);
        });

    it("waits longer than the server for a task", async () => {
        const state = { task_id: "t1", status: "running", next_since: 3 };
        const { caller: c, calls } = caller([state]);

        expect(await new UtilApi(c).getTask("t1", 2, 2000)).toEqual(state);
        expect(calls).toEqual([{
            name: "util.get_task",
            args: { task_id: "t1", since: 2, wait_ms: 2000 },
            timeoutMs: 32_000,
        }]);
    });

    it("lists and cancels tasks", async () => {
        const { caller: c, calls } = caller([{ task_id: "a" }, { task_id: "b" }]);
        const api = new UtilApi(c);

        expect(await api.listTasks()).toEqual([{ task_id: "a" }, { task_id: "b" }]);
        await api.cancelTask("a");
        expect(calls.map((call) => { return call.name; }))
            .toEqual(["util.list_tasks", "util.cancel_task"]);
        expect(calls[1].args).toEqual({ task_id: "a" });
    });

    it("tells finished states apart", () => {
        expect(["completed", "failed", "cancelled"].every((status) => {
            return isFinished(status as never);
        })).toBe(true);
        expect(isFinished("running")).toBe(false);
        expect(isFinished("pending")).toBe(false);
    });
});
