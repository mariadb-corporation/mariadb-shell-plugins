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

import { ActionGroup } from "../../rest/actionGroup.js";
import type { IActionRow } from "../../webview/protocol.js";

/** Collects what goes up, keeping the latest row of each id. */
const collect = (): {
    put: (connection: string, row: IActionRow) => void;
    rows: Map<string, IActionRow>;
    last: () => IActionRow;
} => {
    const rows = new Map<string, IActionRow>();
    let last: IActionRow | undefined;

    return {
        put: (_connection, row) => { rows.set(row.id, row); last = row; },
        rows,
        last: () => { return last!; },
    };
};

const WORDS = { doing: "Creating Sandbox on port 3311",
    done: "Created Sandbox on port 3311" };

describe("ActionGroup", () => {
    it("is pending until finished, a row for each step under it", async () => {
        const rows = collect();
        const group = new ActionGroup(rows.put, "General", WORDS);
        expect(rows.last()).toMatchObject({ role: "run", kind: "pending",
            message: "Creating Sandbox on port 3311…", children: [] });

        await group.step({ doing: "Deploying", done: "Deployed" }, "call()",
            () => { return Promise.resolve("deployed"); },
            { detail: (said) => { return said; } });
        expect(rows.last().kind).toBe("pending");
        expect(rows.last().children).toMatchObject([
            { role: "statement", kind: "info", message: "Deployed",
                statement: "deployed" },
        ]);

        group.finish("https://localhost:33110/myService/v1");
        expect(rows.last()).toMatchObject({
            kind: "info", message: "Created Sandbox on port 3311",
            summary: "https://localhost:33110/myService/v1",
        });
        // One action: every put replaced the one row.
        expect(rows.rows.size).toBe(1);
    });

    it("shows a step's spinner while it runs", async () => {
        const rows = collect();
        const group = new ActionGroup(rows.put, "General", WORDS);
        let release!: () => void;
        const running = group.step({ doing: "Waiting", done: "Waited" }, "w()",
            () => { return new Promise<void>((resolve) => { release = resolve; }); });

        expect(rows.last().children?.[0]).toMatchObject({
            kind: "pending", message: "Waiting…" });
        release();
        await running;
    });

    it("fails with the step's error to jump to, and its log", async () => {
        const rows = collect();
        const group = new ActionGroup(rows.put, "General", WORDS);

        await expect(group.step({ doing: "Setting up", done: "Set up" },
            "rest.configure_restd()",
            () => { return Promise.reject(new Error("Access denied")); },
            { logOf: () => { return Promise.resolve("/logs/setup.log"); } }))
            .rejects.toThrow("Access denied");
        group.finish();

        const row = rows.last();
        const failed = row.children![0]!;
        expect(failed).toMatchObject({ kind: "error",
            message: "Setting up failed: Access denied",
            statement: "rest.configure_restd()", logPath: "/logs/setup.log" });
        expect(row).toMatchObject({ kind: "error",
            message: "Creating Sandbox on port 3311 failed",
            summary: "Finished with 1 error", jumpToRowId: failed.id,
            logPath: "/logs/setup.log" });
        expect(group.failed).toBe(true);
    });
});
