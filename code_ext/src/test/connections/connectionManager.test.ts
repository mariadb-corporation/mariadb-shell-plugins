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

import { describe, expect, it, vi } from "vitest";

import { ConnectionManager } from "../../connections/connectionManager.js";
import { createFakeApi, createFakeSettings } from "../helpers.js";

/**
 * @param defaultConnection The default to start with.
 *
 * @returns A manager over a fake server with two configured connections.
 */
const createManager = (defaultConnection?: string) => {
    const api = createFakeApi({
        connections: ["dba@localhost:3310", "app@localhost:3311"],
        connectionIds: {
            "dba@localhost:3310": "uuid-dba",
            "app@localhost:3311": "uuid-app",
        },
    });
    const settings = createFakeSettings(defaultConnection);
    const manager = new ConnectionManager(() => {
        return Promise.resolve(api);
    }, settings);

    return { api, settings, manager };
};

describe("ConnectionManager", () => {
    it("lists the configured connections", async () => {
        const { manager } = createManager();

        await expect(manager.listConnections()).resolves.toEqual([
            "dba@localhost:3310",
            "app@localhost:3311",
        ]);
    });

    it("opens a connection and remembers its UUID", async () => {
        const { manager } = createManager();

        await expect(manager.connect("dba@localhost:3310"))
            .resolves.toBe("uuid-dba");
        expect(manager.isConnected("dba@localhost:3310")).toBe(true);
        expect(manager.connectionIdFor("dba@localhost:3310"))
            .toBe("uuid-dba");
        expect(manager.openConnections).toEqual(["dba@localhost:3310"]);
    });

    it("opens a connection only once", async () => {
        const { api, manager } = createManager();
        const connect = vi.spyOn(api, "connect");

        await manager.connect("dba@localhost:3310");
        await manager.connect("dba@localhost:3310");

        expect(connect).toHaveBeenCalledTimes(1);
    });

    it("reports an unknown connection", async () => {
        const { manager } = createManager();

        await expect(manager.connect("nope"))
            .rejects.toThrow(/not a configured connection/);
        expect(manager.isConnected("nope")).toBe(false);
    });

    it("closes a connection", async () => {
        const { api, manager } = createManager();

        await manager.connect("dba@localhost:3310");
        await manager.disconnect("dba@localhost:3310");

        expect(api.closed).toEqual(["uuid-dba"]);
        expect(manager.isConnected("dba@localhost:3310")).toBe(false);
    });

    it("is a no-op when closing something that is not open", async () => {
        const { api, manager } = createManager();

        await manager.disconnect("dba@localhost:3310");

        expect(api.closed).toEqual([]);
    });

    it("forgets a connection even if the server refuses to close it",
        async () => {
            const { api, manager } = createManager();
            await manager.connect("dba@localhost:3310");
            api.close = () => {
                return Promise.reject(new Error("already gone"));
            };

            await expect(manager.disconnect("dba@localhost:3310"))
                .rejects.toThrow("already gone");
            // The tree must not keep showing it as open.
            expect(manager.isConnected("dba@localhost:3310")).toBe(false);
        });

    it("closes every connection at once", async () => {
        const { api, manager } = createManager();

        await manager.connect("dba@localhost:3310");
        await manager.connect("app@localhost:3311");
        await manager.disconnectAll();

        expect(api.closed.sort()).toEqual(["uuid-app", "uuid-dba"]);
        expect(manager.openConnections).toEqual([]);
    });

    it("swallows failures while closing everything", async () => {
        const { api, manager } = createManager();
        await manager.connect("dba@localhost:3310");
        api.close = () => {
            return Promise.reject(new Error("the server is going down"));
        };

        await expect(manager.disconnectAll()).resolves.toBeUndefined();
    });

    it("reads the default connection from the settings", () => {
        const { manager } = createManager("app@localhost:3311");

        expect(manager.defaultConnection).toBe("app@localhost:3311");
    });

    it("writes the default connection to the settings", async () => {
        const { settings, manager } = createManager();

        await manager.setDefaultConnection("dba@localhost:3310");

        expect(settings.value).toBe("dba@localhost:3310");
        expect(manager.defaultConnection).toBe("dba@localhost:3310");
    });

    it("clears the default connection", async () => {
        const { settings, manager } = createManager("dba@localhost:3310");

        await manager.setDefaultConnection(undefined);

        expect(settings.value).toBeUndefined();
        expect(manager.defaultConnection).toBeUndefined();
    });

    it("tells listeners when a connection opens or closes", async () => {
        const { manager } = createManager();
        const listener = vi.fn();
        manager.onDidChange(listener);

        await manager.connect("dba@localhost:3310");
        expect(listener).toHaveBeenCalledTimes(1);

        await manager.disconnect("dba@localhost:3310");
        expect(listener).toHaveBeenCalledTimes(2);
    });

    it("tells listeners when the default changes", async () => {
        const { manager } = createManager();
        const listener = vi.fn();
        manager.onDidChange(listener);

        await manager.setDefaultConnection("dba@localhost:3310");

        expect(listener).toHaveBeenCalledTimes(1);
    });

    it("stops telling a listener that unsubscribed", async () => {
        const { manager } = createManager();
        const listener = vi.fn();
        const unsubscribe = manager.onDidChange(listener);

        unsubscribe();
        await manager.connect("dba@localhost:3310");

        expect(listener).not.toHaveBeenCalled();
    });
});
