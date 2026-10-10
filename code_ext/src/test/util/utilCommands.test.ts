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

import type { IUtilApi, IUtilTaskState } from "../../mcp/utilApi.js";
import type { IObjectNode, ISchemaNode } from "../../tree/connectionsModel.js";
import { TaskMonitor } from "../../util/taskMonitor.js";
import { UtilCommands, dumpFolderName } from "../../util/utilCommands.js";
import { UTIL_DIALOG_VIEW_TYPE } from "../../util/utilDialogPanel.js";
import type {
    IUtilLoadMessage,
    UtilHostMessage,
    UtilWebviewMessage,
} from "../../util/utilProtocol.js";
import {
    fileDialogs,
    MockOutputChannel,
    registeredCommands,
    resetVscodeMock,
    Uri,
    webviewPanels,
    type MockWebviewPanel,
} from "../mocks/vscode.js";

const URI = "root@localhost:3306";

interface IRecorded {
    monitor?: TaskMonitor;
    connected: string[];
    started: Array<{ name: string; args: unknown[] }>;
    refreshed: number;
    logs: string[];
}

const done = (taskId: string): IUtilTaskState => {
    return {
        task_id: taskId, kind: "dump_schemas", title: "", connection_id: "c1",
        status: "completed", cancel_requested: false, created_at: "",
        started_at: "", finished_at: "", stage: null, progress: null,
        stages: [], messages: [], next_since: 0, result: null, error: null,
    };
};

/** Commands over a fake API whose every task completes at once. */
const setUp = (failStart?: string): IRecorded => {
    const recorded: IRecorded = { connected: [], started: [], refreshed: 0, logs: [] };
    const start = (name: string) => {
        return (...args: unknown[]) => {
            if (failStart !== undefined) {
                return Promise.reject(new Error(failStart));
            }
            recorded.started.push({ name, args });

            return Promise.resolve({ task_id: `t${String(recorded.started.length)}`,
                status: "running" });
        };
    };
    const api = {
        dumpInstance: start("dumpInstance"), dumpSchemas: start("dumpSchemas"),
        dumpTables: start("dumpTables"), exportTable: start("exportTable"),
        loadDump: start("loadDump"), importTable: start("importTable"),
        copyInstance: start("copyInstance"), copySchemas: start("copySchemas"),
        copyTables: start("copyTables"),
        getTask: (taskId: string) => { return Promise.resolve(done(taskId)); },
        cancelTask: (taskId: string) => { return Promise.resolve(done(taskId)); },
        listTasks: () => { return Promise.resolve([]); },
    } as unknown as IUtilApi;
    const monitor = new TaskMonitor(() => { return Promise.resolve(api); },
        new MockOutputChannel("MariaDB Tasks"), (message) => {
            recorded.logs.push(message);
        });

    recorded.monitor = monitor;
    new UtilCommands({
        extensionUri: Uri.file("/ext") as never,
        connect: (uri) => {
            recorded.connected.push(uri);

            return Promise.resolve(`id-of-${uri}`);
        },
        connectionUris: () => {
            return Promise.resolve([URI, "root@other:3306"]);
        },
        utilApi: () => { return Promise.resolve(api); },
        monitor,
        refresh: () => { recorded.refreshed += 1; },
        defaultFolder: () => { return "/home/me"; },
        log: (message) => { recorded.logs.push(message); },
    }).register();

    return recorded;
};

const monitorOf = (recorded: IRecorded): TaskMonitor => {
    return recorded.monitor!;
};

const panel = (): MockWebviewPanel => {
    return webviewPanels[webviewPanels.length - 1]!;
};

const posted = (): UtilHostMessage[] => {
    return panel().webview.posted as UtilHostMessage[];
};

const settle = async (): Promise<void> => {
    for (let i = 0; i < 5; i++) {
        await new Promise((resolve) => { setTimeout(resolve, 0); });
    }
};

const receive = async (message: UtilWebviewMessage): Promise<void> => {
    panel().webview.receive(message);
    await settle();
};

const run = async (command: string, ...args: unknown[]): Promise<void> => {
    registeredCommands.get(command)!(...args);
    await settle();
};

const loaded = async (): Promise<IUtilLoadMessage> => {
    await receive({ type: "ready" });

    return posted().find((message) => {
        return message.type === "load";
    }) as IUtilLoadMessage;
};

const schema = (name: string): ISchemaNode => {
    return { kind: "schema", uri: URI, schema: name, schemaType: "user", comment: "" };
};

const table = (name: string, schemaName = "shop"): IObjectNode => {
    return { kind: "object", uri: URI, schema: schemaName, objectType: "table", name };
};

describe("the dump and load commands", () => {
    beforeEach(() => { resetVscodeMock(); });

    it("dumps the selected schemas, from the dialog", async () => {
        const recorded = setUp();
        await run("mariadb.dumpSchemas", schema("shop"),
            [schema("shop"), schema("crm"), table("orders")]);

        expect(panel().viewType).toBe(UTIL_DIALOG_VIEW_TYPE);
        const load = await loaded();
        expect(load.spec.title).toBe("Dump shop, crm to Disk");
        expect(String(load.values.path)).toMatch(/^\/home\/me\/schemas-\d{4}-/u);

        await receive({ type: "start", values: { ...load.values, path: "/d",
            threads: "8" } });

        expect(recorded.connected).toEqual([URI]);
        expect(recorded.started).toEqual([{ name: "dumpSchemas",
            args: [`id-of-${URI}`, ["shop", "crm"], "/d", { threads: 8 }] }]);
        expect(panel().disposed).toBe(true);
        // a dump changes nothing the tree shows
        expect(recorded.refreshed).toBe(0);
    });

    it("reads the tree again after a load", async () => {
        const recorded = setUp();
        await run("mariadb.loadDump", { kind: "connection", uri: URI,
            connected: true, isDefault: false });
        const load = await loaded();
        expect(load.values.path).toBe("");

        await receive({ type: "start", values: { ...load.values, path: "/dump",
            schema: "copy_of_shop" } });

        expect(recorded.started[0]).toEqual({ name: "loadDump",
            args: [`id-of-${URI}`, "/dump", { schema: "copy_of_shop" }] });
        expect(recorded.refreshed).toBe(1);
    });

    it("resumes a load that stopped, without starting over", async () => {
        const recorded = setUp();
        await run("mariadb.loadDump", { kind: "connection", uri: URI,
            connected: true, isDefault: false });
        const load = await loaded();
        await receive({ type: "start", values: { ...load.values, path: "/dump",
            resetProgress: true, threads: "2" } });
        const [first] = monitorOf(recorded).tasks;
        expect(first.resume).toEqual({ operation: "loadDump", uri: URI,
            url: "/dump", options: { threads: 2 } });

        // as a reload leaves it: still running when the window closed
        first.interrupted = true;
        first.state = { ...first.state, status: "running" };
        await run("mariadb.resumeTask", first);

        expect(recorded.started[1]).toEqual({ name: "loadDump",
            args: [`id-of-${URI}`, "/dump", { threads: 2 }] });
        // the resumed load takes the stopped one's place
        expect(monitorOf(recorded).tasks.map((task) => { return task.state.task_id; }))
            .toEqual(["t2"]);
    });

    it("does not resume what cannot be", async () => {
        const recorded = setUp();
        await run("mariadb.dumpSchemas", schema("shop"));
        const load = await loaded();
        await receive({ type: "start", values: load.values });
        const [dump] = monitorOf(recorded).tasks;

        await run("mariadb.resumeTask", { ...dump, interrupted: true });

        expect(recorded.started).toHaveLength(1);
    });

    it("copies to another connection, opened for the task", async () => {
        const recorded = setUp();
        await run("mariadb.copyTables", table("orders"),
            [table("orders"), table("items"), table("x", "other")]);
        const load = await loaded();
        // the source is not offered as the target
        expect(load.targets).toEqual(["root@other:3306"]);

        await receive({ type: "start", values: { ...load.values,
            target: "root@other:3306" } });

        expect(recorded.connected).toEqual([URI, "root@other:3306"]);
        expect(recorded.started[0]).toEqual({ name: "copyTables",
            args: [`id-of-${URI}`, "shop", ["orders", "items"],
                "id-of-root@other:3306", {}] });
    });

    it("exports a table under a quoted name, and imports into it", async () => {
        const recorded = setUp();
        await run("mariadb.exportTable", table("order lines"));
        let load = await loaded();
        expect(load.values.path).toBe("/home/me/shop.order lines.tsv");
        await receive({ type: "start", values: load.values });
        expect(recorded.started[0].args.slice(1, 3))
            .toEqual(["shop.`order lines`", "/home/me/shop.order lines.tsv"]);

        await run("mariadb.importTable", table("orders"));
        load = await loaded();
        await receive({ type: "start", values: { ...load.values,
            path: "/in/a.tsv, /in/b.tsv", skipRows: "1" } });
        expect(recorded.started[1]).toEqual({ name: "importTable",
            args: [`id-of-${URI}`, ["/in/a.tsv", "/in/b.tsv"],
                { schema: "shop", table: "orders", skipRows: 1 }] });
    });

    it("fills the path in from Browse", async () => {
        setUp();
        await run("mariadb.dumpInstance", { kind: "connection", uri: URI,
            connected: true, isDefault: false });
        await loaded();
        fileDialogs.openAnswer = [Uri.file("/picked") as never];

        await receive({ type: "browse", current: "" });

        expect(posted().at(-1)).toEqual({ type: "browsed", path: "/picked" });
        expect(fileDialogs.openCalls[0]).toMatchObject({ canSelectFolders: true,
            canSelectMany: false });
    });

    it("keeps the dialog open with the reason when the work does not start",
        async () => {
            const recorded = setUp("Access denied for user 'root'");
            await run("mariadb.dumpSchemas", schema("shop"));
            const load = await loaded();

            await receive({ type: "start", values: load.values });

            expect(panel().disposed).toBe(false);
            expect(posted()).toContainEqual({ type: "startError",
                message: "Access denied for user 'root'" });
            expect(recorded.logs.at(-1)).toContain("did not start");
        });

    it("refuses values the dialog should not have sent", async () => {
        const recorded = setUp();
        await run("mariadb.dumpSchemas", schema("shop"));
        const load = await loaded();

        await receive({ type: "start", values: { ...load.values, path: "" } });

        expect(recorded.started).toEqual([]);
        expect(posted()).toContainEqual({ type: "startError", field: "path",
            message: "Output Folder must be given." });
    });

    it("ignores rows it does not apply to", async () => {
        const recorded = setUp();
        await run("mariadb.dumpSchemas", table("orders"));
        await run("mariadb.exportTable", { ...table("v"), objectType: "procedure" });
        await run("mariadb.dumpInstance");

        expect(webviewPanels).toEqual([]);
        expect(recorded.started).toEqual([]);
    });
});

describe("dumpFolderName", () => {
    it("names the folder by what is dumped and when", () => {
        expect(dumpFolderName("shop/x y", new Date(2026, 9, 10, 9, 5)))
            .toBe("shop_x_y-2026-10-10-0905");
    });
});
