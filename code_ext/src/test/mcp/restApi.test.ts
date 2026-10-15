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
import {
    RESTD_LIFECYCLE_TIMEOUT_MS,
    RESTD_SETUP_TIMEOUT_MS,
    RestApi,
} from "../../mcp/restApi.js";

interface ICall {
    name: string;
    args: Record<string, unknown>;
    timeoutMs?: number;
}

const DEPLOYMENT = {
    directory: "/s/3311/mariadb-restd", host: "127.0.0.1", server_port: 3311,
    port: 33110, url: "https://localhost:33110", status: "running", pid: 42,
    log_path: "/s/3311/mariadb-restd/log/mariadb-restd.log",
    metadata_schema: "mariadb_rest_service",
    connection_uri: "mariadb://root@127.0.0.1:3311", sandbox_port: 3311,
};

const createCaller = (
    answers: Record<string, IToolResult> = {},
): IToolCaller & { calls: ICall[] } => {
    const calls: ICall[] = [];

    return {
        calls,
        callTool: (name, args, timeoutMs) => {
            calls.push({ name, args, timeoutMs });

            return Promise.resolve(answers[name] ?? {
                content: [{ type: "text", text: JSON.stringify(DEPLOYMENT) }],
            });
        },
    };
};

describe("RestApi", () => {
    it("lists the deployments, one text item each", async () => {
        const caller = createCaller({
            "rest.get_restd_status": {
                content: [{ type: "text", text: JSON.stringify(DEPLOYMENT) }],
            },
        });

        expect(await new RestApi(caller).getStatus()).toEqual([DEPLOYMENT]);
    });

    it("sets a sandbox's daemon up with the server's names, and time to install",
        async () => {
            const caller = createCaller();

            await new RestApi(caller).configure({
                connectionId: "c1", password: "pw", sandboxPort: 3311,
                port: 9443,
            });

            expect(caller.calls).toEqual([{
                name: "rest.configure_restd",
                args: {
                    connection_id: "c1", password: "pw", sandbox_port: 3311,
                    port: 9443,
                },
                timeoutMs: RESTD_SETUP_TIMEOUT_MS,
            }]);
        });

    it("leaves out what was not given, so the server decides", async () => {
        const caller = createCaller();

        await new RestApi(caller).configure({ connectionId: "c1" });

        expect(caller.calls[0]?.args).toEqual({ connection_id: "c1" });
    });

    it("starts and stops by folder", async () => {
        const caller = createCaller();
        const api = new RestApi(caller);

        expect(await api.start(DEPLOYMENT.directory)).toEqual(DEPLOYMENT);
        await api.stop(DEPLOYMENT.directory);

        expect(caller.calls.map((call) => {
            return [call.name, call.args, call.timeoutMs];
        })).toEqual([
            ["rest.start_restd", { directory: DEPLOYMENT.directory },
                RESTD_LIFECYCLE_TIMEOUT_MS],
            ["rest.stop_restd", { directory: DEPLOYMENT.directory },
                RESTD_LIFECYCLE_TIMEOUT_MS],
        ]);
    });

    it("drops the account with a connection, else deletes the folder only",
        async () => {
            const caller = createCaller({
                "rest.delete_restd_config": {
                    content: [{ type: "text", text: "Deleted" }],
                    structuredContent: { result: "Deleted" },
                },
            });
            const api = new RestApi(caller);

            await api.delete("/d", "c1");
            await api.delete("/d");

            expect(caller.calls.map((call) => { return call.args; })).toEqual([
                { directory: "/d", connection_id: "c1" },
                { directory: "/d", drop_accounts: false },
            ]);
        });
});
