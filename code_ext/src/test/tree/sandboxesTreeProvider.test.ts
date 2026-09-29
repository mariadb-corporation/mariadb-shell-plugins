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
    ISandboxApi,
    ISandboxInstance,
} from "../../mcp/sandboxApi.js";
import type { IServerStatus, ServerPhase } from "../../mcp/serverStarter.js";
import { SandboxStore } from "../../sandboxes/sandboxStore.js";
import {
    SandboxesTreeProvider,
    SandboxTreeItem,
    SANDBOXES_VIEW_STATE_CONTEXT_KEY,
    type ISandboxNode,
} from "../../tree/sandboxesTreeProvider.js";
import {
    contextKeys,
    resetVscodeMock,
    ThemeIcon,
} from "../mocks/vscode.js";

interface IFakeSandboxApi extends ISandboxApi {
    instances: ISandboxInstance[];
    calls: string[];
    /** Makes the named call reject with its name. */
    failing?: string;
    /** Holds every start until released, to look at the row meanwhile. */
    hold?: Promise<void>;
}

const createApi = (instances: ISandboxInstance[] = []): IFakeSandboxApi => {
    const api: IFakeSandboxApi = {
        instances,
        calls: [],
        listInstances: () => {
            api.calls.push("list");

            return api.failing === "list"
                ? Promise.reject(new Error("Unknown tool"))
                : Promise.resolve(api.instances);
        },
        listAvailableVersions: () => { return Promise.resolve([]); },
        deploy: () => { return Promise.resolve("deployed"); },
        start: async (port) => {
            api.calls.push(`start ${port}`);
            await api.hold;

            return `started ${port}`;
        },
        stop: (port) => {
            api.calls.push(`stop ${port}`);

            return api.failing === "stop"
                ? Promise.reject(new Error("stop failed"))
                : Promise.resolve(`stopped ${port}`);
        },
        delete: (port) => {
            api.calls.push(`delete ${port}`);

            return Promise.resolve(`deleted ${port}`);
        },
    };

    return api;
};

/** A server startup whose phase a test moves by hand. */
const createStatus = (): IServerStatus & { set(phase: ServerPhase): void } => {
    const listeners = new Set<(phase: ServerPhase) => void>();
    const status = {
        phase: "stopped" as ServerPhase,
        onDidChangePhase: (listener: (phase: ServerPhase) => void) => {
            listeners.add(listener);

            return () => { listeners.delete(listener); };
        },
        set: (phase: ServerPhase) => {
            status.phase = phase;
            for (const listener of listeners) {
                listener(phase);
            }
        },
    };

    return status;
};

const running: ISandboxInstance = {
    port: 3310, version: "12.3.2", status: "running",
};
const stopped: ISandboxInstance = {
    port: 3320, version: "11.8.9", status: "stopped",
};

const nodeOf = (instance: ISandboxInstance): ISandboxNode => {
    return { kind: "sandbox", ...instance };
};

describe("SandboxesTreeProvider", () => {
    let logs: string[];

    beforeEach(() => {
        resetVscodeMock();
        logs = [];
    });

    const create = (
        api: IFakeSandboxApi,
        status?: IServerStatus,
    ): SandboxesTreeProvider => {
        return new SandboxesTreeProvider(
            new SandboxStore(() => { return Promise.resolve(api); }),
            (message) => { logs.push(message); },
            status,
        );
    };

    it("lists the sandboxes as rows", async () => {
        const provider = create(createApi([running, stopped]));

        expect(await provider.getChildren()).toEqual([
            nodeOf(running), nodeOf(stopped),
        ]);
        // A row has nothing under it.
        expect(await provider.getChildren(nodeOf(running))).toEqual([]);
    });

    it("redraws from the list it has, and reads it again only on reload",
        async () => {
            const api = createApi([running]);
            const provider = create(api);
            let redraws = 0;
            provider.onDidChangeTreeData(() => { redraws += 1; });

            await provider.getChildren();
            provider.refresh();
            await provider.getChildren();
            expect(api.calls).toEqual(["list"]);

            api.instances = [running, stopped];
            provider.reload();
            expect(redraws).toBe(2);
            expect(await provider.getChildren()).toHaveLength(2);
            expect(api.calls).toEqual(["list", "list"]);
        });

    it("shows what an action did without listing again", async () => {
        const api = createApi([running, stopped]);
        const provider = create(api);
        await provider.getChildren();

        await provider.stop(nodeOf(running));
        await provider.start(nodeOf(stopped));
        await provider.delete(nodeOf(stopped));

        expect(await provider.getChildren()).toEqual([
            nodeOf({ ...running, status: "stopped" }),
        ]);
        expect(api.calls.filter((call) => { return call === "list"; }))
            .toHaveLength(1);
    });

    it("says it is looking until the list comes back", async () => {
        const provider = create(createApi());
        expect(contextKeys.get(SANDBOXES_VIEW_STATE_CONTEXT_KEY))
            .toBe("looking");

        await provider.getChildren();

        expect(contextKeys.get(SANDBOXES_VIEW_STATE_CONTEXT_KEY))
            .toBe("listed");
    });

    it("says the listing failed, logging why, and tries again on a new "
        + "start", async () => {
        const api = createApi();
        api.failing = "list";
        const status = createStatus();
        const provider = create(api, status);

        expect(await provider.getChildren()).toEqual([]);

        expect(provider.state).toBe("failed");
        expect(contextKeys.get(SANDBOXES_VIEW_STATE_CONTEXT_KEY))
            .toBe("failed");
        expect(logs).toEqual(["Failed to list the sandboxes: Unknown tool"]);

        status.set("locating");
        expect(provider.state).toBe("looking");
    });

    it("follows the server's startup while it has no rows", () => {
        const status = createStatus();
        const provider = create(createApi(), status);

        status.set("installing");
        expect(contextKeys.get(SANDBOXES_VIEW_STATE_CONTEXT_KEY))
            .toBe("installing");

        status.set("failed");
        expect(provider.state).toBe("failed");
    });

    it("shows a start on the row while it runs", async () => {
        const api = createApi([stopped]);
        let release = (): void => { /* set below */ };
        api.hold = new Promise((resolve) => { release = resolve; });
        const provider = create(api);
        let redraws = 0;
        provider.onDidChangeTreeData(() => { redraws += 1; });

        const starting = provider.start(nodeOf(stopped));
        await Promise.resolve();

        const [row] = await provider.getChildren();
        expect(row?.action).toBe("starting");
        const item = new SandboxTreeItem(row!);
        expect(item.contextValue).toBe("mariadbSandbox.busy");
        expect(item.description).toBe("11.8.9 · starting…");
        expect((item.iconPath as ThemeIcon).id).toBe("loading~spin");
        // A second click is refused, not queued behind the first.
        await expect(provider.stop(nodeOf(stopped)))
            .rejects.toThrow("already starting…");

        release();
        expect(await starting).toBe("started 3320");
        expect(await provider.getChildren()).toEqual([
            nodeOf({ ...stopped, status: "running" }),
        ]);
        expect(redraws).toBe(2);
        expect(logs).toContain("started 3320");
    });

    it("clears the action when it fails, and lets the failure through",
        async () => {
            const api = createApi([running]);
            api.failing = "stop";
            const provider = create(api);

            await provider.getChildren();

            await expect(provider.stop(nodeOf(running)))
                .rejects.toThrow("stop failed");

            expect((await provider.getChildren())[0]?.action).toBeUndefined();
            // A failed stop may still have stopped it, so the server is
            // asked where it is - once more, for the one sandbox.
            expect(api.calls.filter((call) => { return call === "list"; }))
                .toHaveLength(2);
            expect((await provider.getChildren())).toEqual([nodeOf(running)]);
        });

    it("stops a running sandbox before deleting it", async () => {
        const api = createApi();
        const provider = create(api);

        await provider.delete(nodeOf(running));
        await provider.delete(nodeOf(stopped));

        expect(api.calls).toEqual(["stop 3310", "delete 3310", "delete 3320"]);
    });
});

describe("SandboxTreeItem", () => {
    it("draws a running sandbox with its version and state", () => {
        const item = new SandboxTreeItem(nodeOf(running));

        expect(item.label).toBe("localhost:3310");
        expect(item.description).toBe("12.3.2 · running");
        expect(item.contextValue).toBe("mariadbSandbox.running");
        expect((item.iconPath as ThemeIcon).id).toBe("vm-running");
        expect(item.id).toBe("sandbox:3310");
    });

    it("draws a stopped one, and one whose version is unknown", () => {
        const item = new SandboxTreeItem(nodeOf({
            port: 3330, version: null, status: "stopped",
        }));

        expect(item.description).toBe("unknown version · stopped");
        expect(item.contextValue).toBe("mariadbSandbox.stopped");
        expect((item.iconPath as ThemeIcon).id).toBe("vm-outline");
        expect(item.tooltip).toContain("Server version: unknown version");
    });
});
