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

import type {
    ISandboxApi,
    ISandboxInstance,
} from "../../mcp/sandboxApi.js";
import { SandboxStore } from "../../sandboxes/sandboxStore.js";

interface IFakeApi extends ISandboxApi {
    calls: string[];
    instances: ISandboxInstance[];
    /** Rejects the named calls. */
    failing: Set<string>;
    /** Holds each listing until released. */
    held?: Array<() => void>;
    /** Answers every sandbox whatever port is asked, as an old server does. */
    ignoresPort?: boolean;
}

const createApi = (instances: ISandboxInstance[] = []): IFakeApi => {
    const call = (name: string, answer: string): Promise<string> => {
        api.calls.push(name);

        return api.failing.has(name)
            ? Promise.reject(new Error(`${name} failed`))
            : Promise.resolve(answer);
    };
    const api: IFakeApi = {
        calls: [],
        instances,
        failing: new Set(),
        listInstances: async (port) => {
            api.calls.push(port === undefined ? "list" : `list ${port}`);
            const answer = port === undefined || api.ignoresPort
                ? api.instances
                : api.instances.filter((instance) => {
                    return instance.port === port;
                });
            if (api.held !== undefined) {
                await new Promise<void>((resolve) => {
                    api.held!.push(resolve);
                });
            }
            if (api.failing.has(port === undefined ? "list" : "list one")) {
                throw new Error("list failed");
            }

            return answer;
        },
        listAvailableVersions: async () => {
            await call("versions", "");

            return ["11.8.9", "12.3.2"];
        },
        deploy: (options) => { return call("deploy", `deployed ${options.port}`); },
        start: (port) => { return call("start", `started ${port}`); },
        stop: (port) => { return call("stop", `stopped ${port}`); },
        delete: (port) => { return call("delete", `deleted ${port}`); },
    };

    return api;
};

const running: ISandboxInstance = {
    port: 3310, version: "12.3.2", status: "running",
};
const stopped: ISandboxInstance = {
    port: 3320, version: "11.8.9", status: "stopped",
};

const storeOver = (api: IFakeApi): SandboxStore => {
    return new SandboxStore(() => { return Promise.resolve(api); });
};

const lists = (api: IFakeApi): number => {
    return api.calls.filter((call) => { return call === "list"; }).length;
};

describe("SandboxStore", () => {
    it("lists once and keeps the answer", async () => {
        const api = createApi([running]);
        const store = storeOver(api);

        expect(await store.listInstances()).toEqual([running]);
        expect(await store.listInstances()).toEqual([running]);

        expect(lists(api)).toBe(1);
        expect(store.ports).toEqual([3310]);
    });

    it("has concurrent callers share one listing", async () => {
        const api = createApi([running]);
        const store = storeOver(api);

        await Promise.all([store.listInstances(), store.listInstances()]);

        expect(lists(api)).toBe(1);
    });

    it("lists again after invalidate", async () => {
        const api = createApi([running]);
        const store = storeOver(api);
        await store.listInstances();

        api.instances = [running, stopped];
        store.invalidate();

        expect(await store.listInstances()).toEqual([running, stopped]);
        expect(lists(api)).toBe(2);
    });

    it("keeps no failed listing, so the next look tries again", async () => {
        const api = createApi([running]);
        api.failing.add("list");
        const store = storeOver(api);

        await expect(store.listInstances()).rejects.toThrow("list failed");
        api.failing.clear();

        expect(await store.listInstances()).toEqual([running]);
        expect(lists(api)).toBe(2);
    });

    it("hands out copies, so a caller cannot change what is kept",
        async () => {
            const store = storeOver(createApi([running]));

            (await store.listInstances()).pop();

            expect(await store.listInstances()).toEqual([running]);
        });

    it("updates the kept list after a start, stop or delete", async () => {
        const api = createApi([running, stopped]);
        const store = storeOver(api);
        await store.listInstances();

        expect(await store.stop(3310)).toBe("stopped 3310");
        await store.start(3320);
        expect(await store.listInstances()).toEqual([
            { ...running, status: "stopped" },
            { ...stopped, status: "running" },
        ]);

        await store.delete(3310);
        expect(await store.listInstances()).toEqual([
            { ...stopped, status: "running" },
        ]);
        expect(store.ports).toEqual([3320]);
        expect(lists(api)).toBe(1);
    });

    it("asks about the one sandbox after a change that failed", async () => {
        const api = createApi([running, stopped]);
        api.failing.add("stop");
        const store = storeOver(api);
        await store.listInstances();
        // The stop timed out, but got there.
        api.instances = [{ ...running, status: "stopped" }, stopped];

        await expect(store.stop(3310)).rejects.toThrow("stop failed");

        expect(await store.listInstances()).toEqual([
            { ...running, status: "stopped" }, stopped,
        ]);
        expect(api.calls.filter((call) => { return call.startsWith("list"); }))
            .toEqual(["list", "list 3310"]);
    });

    it("drops the list when the one sandbox cannot be asked about either",
        async () => {
            const api = createApi([running]);
            api.failing.add("stop");
            api.failing.add("list one");
            const store = storeOver(api);
            await store.listInstances();

            // The stop's failure is the one reported, not the lookup's.
            await expect(store.stop(3310)).rejects.toThrow("stop failed");
            await store.listInstances();

            expect(lists(api)).toBe(2);
        });

    it("lets a listing in flight bring its own answer", async () => {
        const api = createApi([running]);
        api.held = [];
        const store = storeOver(api);

        const listing = store.listInstances();
        await Promise.resolve();
        await store.stop(3310);
        api.held.shift()!();

        // The listing that was under way is what the store keeps, not a
        // copy the stop patched before it had anything to patch.
        expect(await listing).toEqual([running]);
        expect(await store.listInstances()).toEqual([running]);
        expect(lists(api)).toBe(1);
    });

    it("asks about the new sandbox after a deploy, not about all of them",
        async () => {
            const api = createApi([stopped]);
            const store = storeOver(api);
            await store.listInstances();
            api.instances = [running, stopped];

            expect(await store.deploy({ port: 3310, password: "" }))
                .toBe("deployed 3310");

            // Its version is known now, and it sorts in by port.
            expect(await store.listInstances()).toEqual([running, stopped]);
            expect(store.ports).toEqual([3310, 3320]);
            expect(api.calls.filter((call) => { return call.startsWith("list"); }))
                .toEqual(["list", "list 3310"]);
        });

    it("asks about the port after a failed deploy too", async () => {
        const api = createApi([]);
        api.failing.add("deploy");
        const store = storeOver(api);
        await store.listInstances();
        // It failed late: the instance was made, only the rest went wrong.
        api.instances = [running];

        await expect(store.deploy({ port: 3310, password: "" }))
            .rejects.toThrow("deploy failed");

        expect(await store.listInstances()).toEqual([running]);
    });

    it("takes out a sandbox that is no longer there", async () => {
        const api = createApi([running, stopped]);
        const store = storeOver(api);
        await store.listInstances();
        api.instances = [stopped];

        expect(await store.refreshInstance(3310)).toBeUndefined();

        expect(await store.listInstances()).toEqual([stopped]);
    });

    it("picks the one sandbox out of an old server's whole answer",
        async () => {
            // An older plugin drops the argument it does not know and lists
            // every sandbox.
            const api = createApi([running, stopped]);
            api.ignoresPort = true;
            const store = storeOver(api);
            await store.listInstances();
            api.instances = [running, { ...stopped, status: "running" }];

            expect(await store.refreshInstance(3320))
                .toEqual({ ...stopped, status: "running" });
            expect(await store.listInstances()).toEqual([
                running, { ...stopped, status: "running" },
            ]);
        });

    it("reads the available versions once, for good", async () => {
        const api = createApi();
        const store = storeOver(api);

        expect(await store.availableVersions()).toEqual(["11.8.9", "12.3.2"]);
        store.invalidate();
        await store.availableVersions();

        expect(api.calls.filter((call) => { return call === "versions"; }))
            .toHaveLength(1);
    });

    it("keeps no failed version listing", async () => {
        const api = createApi();
        api.failing.add("versions");
        const store = storeOver(api);

        await expect(store.availableVersions()).rejects.toThrow();
        api.failing.clear();

        expect(await store.availableVersions()).toHaveLength(2);
    });
});
