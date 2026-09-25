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
import { McpToolError, type IToolResult } from "../../mcp/protocol.js";
import {
    DEPLOY_TIMEOUT_MS,
    LIFECYCLE_TIMEOUT_MS,
    SandboxApi,
} from "../../mcp/sandboxApi.js";

interface ICall {
    name: string;
    args: Record<string, unknown>;
    timeoutMs?: number;
}

/** Records the calls made, and answers from a canned table. */
const createCaller = (
    answers: Record<string, IToolResult> = {},
): IToolCaller & { calls: ICall[] } => {
    const calls: ICall[] = [];

    return {
        calls,
        callTool: (name, args, timeoutMs) => {
            calls.push({ name, args, timeoutMs });

            return Promise.resolve(answers[name] ?? {
                content: [{ type: "text", text: "ok" }],
                structuredContent: { result: "ok" },
            });
        },
    };
};

describe("SandboxApi", () => {
    it("lists the instances, one text item per instance", async () => {
        // What FastMCP renders a list of dicts as: one JSON item each.
        const caller = createCaller({
            "sandbox.list_instances": {
                content: [
                    {
                        type: "text",
                        text: '{"port": 3310, "version": "12.3.2", '
                            + '"status": "running"}',
                    },
                    {
                        type: "text",
                        text: '{"port": 3320, "version": null, '
                            + '"status": "stopped"}',
                    },
                ],
            },
        });

        const instances = await new SandboxApi(caller).listInstances();

        expect(instances).toEqual([
            { port: 3310, version: "12.3.2", status: "running" },
            { port: 3320, version: null, status: "stopped" },
        ]);
        expect(caller.calls).toEqual([{
            name: "sandbox.list_instances", args: {}, timeoutMs: undefined,
        }]);
    });

    it("asks about one instance by its port", async () => {
        const caller = createCaller({
            "sandbox.list_instances": {
                content: [{
                    type: "text",
                    text: '{"port": 3310, "version": "12.3.2", '
                        + '"status": "running"}',
                }],
            },
        });

        const instances = await new SandboxApi(caller).listInstances(3310);

        expect(instances).toEqual([
            { port: 3310, version: "12.3.2", status: "running" },
        ]);
        expect(caller.calls[0]?.args).toEqual({ port: 3310 });
    });

    it("reads no instances as an empty list", async () => {
        const caller = createCaller({
            "sandbox.list_instances": { content: [] },
        });

        expect(await new SandboxApi(caller).listInstances()).toEqual([]);
    });

    it("says why a listing failed", async () => {
        // A shell whose plugin predates the tool answers this way.
        const caller = createCaller({
            "sandbox.list_instances": {
                content: [{ type: "text", text: "Unknown tool" }],
                isError: true,
            },
        });

        await expect(new SandboxApi(caller).listInstances())
            .rejects.toThrow(McpToolError);
    });

    it("sends a series only when one is asked about", async () => {
        const caller = createCaller({
            "sandbox.list_available_versions": {
                content: [
                    { type: "text", text: "11.8.9" },
                    { type: "text", text: "12.3.2" },
                ],
            },
        });
        const api = new SandboxApi(caller);

        expect(await api.listAvailableVersions()).toEqual(["11.8.9", "12.3.2"]);
        await api.listAvailableVersions("11.8");

        expect(caller.calls.map((call) => { return call.args; }))
            .toEqual([{}, { series: "11.8" }]);
    });

    it("deploys with only the options that were given", async () => {
        const caller = createCaller();

        await new SandboxApi(caller).deploy({ port: 3310, password: "" });

        // The empty password is sent: the shell refuses a deploy without
        // one, and an empty one is a real choice for a local sandbox.
        expect(caller.calls).toEqual([{
            name: "sandbox.deploy",
            args: { port: 3310, password: "" },
            timeoutMs: DEPLOY_TIMEOUT_MS,
        }]);
    });

    it("deploys with every option spelled as the server takes it", async () => {
        const caller = createCaller();

        await new SandboxApi(caller).deploy({
            port: 3310,
            password: "secret",
            serverVersion: "11.8",
            allowRootFrom: "",
            serverId: 7,
            ssl: true,
            mariadbdOptions: ["innodb_buffer_pool_size=64M"],
            timeout: 120,
            mcpAccess: false,
        });

        expect(caller.calls[0]?.args).toEqual({
            port: 3310,
            password: "secret",
            server_version: "11.8",
            // Empty is a real value here: no remote root account at all.
            allow_root_from: "",
            server_id: 7,
            ssl: true,
            mariadbd_options: ["innodb_buffer_pool_size=64M"],
            timeout: 120,
            mcp_access: false,
        });
    });

    it("leaves out an empty list of server options", async () => {
        const caller = createCaller();

        await new SandboxApi(caller).deploy({
            port: 3310, password: "", mariadbdOptions: [],
        });

        expect(caller.calls[0]?.args).toEqual({ port: 3310, password: "" });
    });

    it("gives start, stop and delete room above the shell's own wait",
        async () => {
            const caller = createCaller();
            const api = new SandboxApi(caller);

            await api.start(3310);
            await api.stop(3310);
            await api.delete(3310);

            expect(caller.calls).toEqual([
                { name: "sandbox.start", args: { port: 3310 },
                    timeoutMs: LIFECYCLE_TIMEOUT_MS },
                { name: "sandbox.stop", args: { port: 3310 },
                    timeoutMs: LIFECYCLE_TIMEOUT_MS },
                { name: "sandbox.delete", args: { port: 3310 },
                    timeoutMs: LIFECYCLE_TIMEOUT_MS },
            ]);
            expect(LIFECYCLE_TIMEOUT_MS).toBeGreaterThan(60_000);
        });

    it("returns the server's confirmation", async () => {
        const caller = createCaller({
            "sandbox.start": {
                content: [{
                    type: "text", text: "Sandbox instance on port 3310 started.",
                }],
                structuredContent: {
                    result: "Sandbox instance on port 3310 started.",
                },
            },
        });

        expect(await new SandboxApi(caller).start(3310))
            .toBe("Sandbox instance on port 3310 started.");
    });
});
