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

import {
    GENERAL_ACTIONS,
    type IActivityEvent,
} from "../../connections/connectionActivity.js";
import type { ISandboxApi } from "../../mcp/sandboxApi.js";
import { createLoggingSandboxApi } from "../../sandboxes/sandboxActivity.js";

const api: ISandboxApi = {
    listInstances: () => {
        return Promise.resolve([
            { port: 3310, version: "12.3.2", status: "running" },
            { port: 3320, version: null, status: "stopped" },
        ]);
    },
    listAvailableVersions: () => { return Promise.resolve(["12.3.2"]); },
    deploy: () => { return Promise.resolve("Sandbox deployed."); },
    start: (port) => { return Promise.resolve(`Started ${port}.`); },
    stop: (port) => { return Promise.resolve(`Stopped ${port}.`); },
    delete: () => { return Promise.reject(new Error("It is running.")); },
};

const logging = (on = true): {
    logged: ISandboxApi;
    events: IActivityEvent[];
} => {
    const events: IActivityEvent[] = [];

    return {
        logged: createLoggingSandboxApi(api, (event) => { events.push(event); },
            () => { return on; }),
        events,
    };
};

describe("createLoggingSandboxApi", () => {
    it("files every call under General Actions", async () => {
        const { logged, events } = logging();

        await logged.listInstances();
        await logged.listInstances(3310);
        await logged.listAvailableVersions();
        await logged.listAvailableVersions("11.8");
        await logged.start(3310);
        await logged.stop(3310);

        expect(events.map((event) => {
            return [event.connection, event.label, event.call, event.message];
        })).toEqual([
            [GENERAL_ACTIONS, "", "sandbox.list_instances()",
                "Listed 2 sandboxes"],
            [GENERAL_ACTIONS, "", "sandbox.list_instances(port=3310)",
                "Listed 2 sandboxes"],
            [GENERAL_ACTIONS, "", "sandbox.list_available_versions()",
                "Listed 1 version"],
            [GENERAL_ACTIONS, "", "sandbox.list_available_versions(series=11.8)",
                "Listed 1 version"],
            [GENERAL_ACTIONS, "", "sandbox.start(port=3310)", "Started 3310."],
            [GENERAL_ACTIONS, "", "sandbox.stop(port=3310)", "Stopped 3310."],
        ]);
    });

    it("never shows the root password", async () => {
        const { logged, events } = logging();

        await logged.deploy({
            port: 3310,
            password: "s3cret",
            serverVersion: "11.8",
            mariadbdOptions: ["a=1", "b=2"],
            mcpAccess: false,
        });

        expect(events[0]?.call).toBe("sandbox.deploy(port=3310, password=***, "
            + "server_version=11.8, mariadbd_options=a=1 b=2, "
            + "mcp_access=false)");
        expect(JSON.stringify(events)).not.toContain("s3cret");
    });

    it("reports a failed call, and lets the failure through", async () => {
        const { logged, events } = logging();

        await expect(logged.delete(3310)).rejects.toThrow("It is running.");

        expect(events[0]).toMatchObject({
            call: "sandbox.delete(port=3310)",
            error: "It is running.",
        });
    });

    it("reports nothing while the setting is off", async () => {
        const { logged, events } = logging(false);

        await logged.listInstances();
        await logged.start(3310);

        expect(events).toEqual([]);
    });

    it("says one sandbox in the singular", async () => {
        const events: IActivityEvent[] = [];
        const one = createLoggingSandboxApi({
            ...api,
            listInstances: () => {
                return Promise.resolve([
                    { port: 3310, version: null, status: "stopped" },
                ]);
            },
        }, (event) => { events.push(event); }, () => { return true; });

        await one.listInstances();

        expect(events[0]?.message).toBe("Listed 1 sandbox");
    });
});
