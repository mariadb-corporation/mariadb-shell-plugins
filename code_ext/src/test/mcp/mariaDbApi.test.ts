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

import { MariaDbApi, type IToolCaller } from "../../mcp/mariaDbApi.js";
import type { IToolResult } from "../../mcp/protocol.js";

/** Records the calls made, and answers from a canned table. */
const createCaller = (
    answers: Record<string, IToolResult>,
): IToolCaller & {
    calls: Array<{ name: string; args: Record<string, unknown> }>;
} => {
    const calls: Array<{ name: string; args: Record<string, unknown> }> = [];

    return {
        calls,
        callTool: (name, args) => {
            calls.push({ name, args });

            return Promise.resolve(answers[name] ?? { content: [] });
        },
    };
};

describe("MariaDbApi", () => {
    let caller: ReturnType<typeof createCaller>;
    let api: MariaDbApi;

    beforeEach(() => {
        caller = createCaller({
            "db.list_connections": {
                content: [{ type: "text", text: "dba@localhost:3310" }],
            },
            "db.add_connection": {
                content: [{ type: "text", text: "dba@localhost:3310" }],
                structuredContent: { result: "dba@localhost:3310" },
            },
            "db.delete_connection": {
                content: [{ type: "text", text: "dba@localhost:3310" }],
                structuredContent: { result: "dba@localhost:3310" },
            },
            "db.connect": {
                content: [{ type: "text", text: "uuid-1" }],
                structuredContent: { result: "uuid-1" },
            },
            "db.list_schemas": {
                content: [{
                    type: "text",
                    text: '{"schema_name":"sys","schema_type":"User Schema",'
                        + '"schema_comment":""}',
                }],
            },
            "db.list_objects": {
                content: [{
                    type: "text",
                    text: '{"name":"city","comment":"BASE TABLE"}',
                }],
            },
            "db.get_object_details": {
                content: [{
                    type: "text",
                    text: '{"basic":{"schema":"world","name":"city",'
                        + '"type":"table"},"columns":[]}',
                }],
            },
            "db.execute_sql_script": {
                content: [{
                    type: "text",
                    text: '{"affected_items_count":0,"warnings_count":0,'
                        + '"columns":["a"],"rows":[{"a":1}]}',
                }],
            },
        });
        api = new MariaDbApi(caller);
    });

    it("names the connection list only when asked to", async () => {
        // A server that is not in GUI mode has no `kind` argument, so sending
        // one would fail a call that works perfectly well without it.
        await expect(api.listConnections())
            .resolves.toEqual(["dba@localhost:3310"]);
        expect(caller.calls[0]!.args).toEqual({});

        await api.listConnections("gui");
        expect(caller.calls[1]!.args).toEqual({ kind: "gui" });
    });

    it("adds a connection, leaving out what it was not told", async () => {
        await expect(api.addConnection("dba@localhost:3310", "pw"))
            .resolves.toBe("dba@localhost:3310");
        expect(caller.calls[0]).toEqual({
            name: "db.add_connection",
            args: { uri: "dba@localhost:3310", password: "pw" },
        });

        await api.addConnection("dba@localhost:3310", "pw", "gui", false);
        expect(caller.calls[1]!.args).toEqual({
            uri: "dba@localhost:3310",
            password: "pw",
            kind: "gui",
            verify: false,
        });
    });

    it("reads each connection's folder, and a bare URI as top level",
        async () => {
            const folders = createCaller({
                "db.list_connections": {
                    content: [
                        {
                            type: "text",
                            text: '{"uri":"a@b:1","path":"/Sandboxes/x"}',
                        },
                        // What a server that predates folders answers with.
                        { type: "text", text: "c@d:2" },
                    ],
                },
            });
            const withFolders = new MariaDbApi(folders);

            await expect(withFolders.listConnectionEntries("gui"))
                .resolves.toEqual([
                    { uri: "a@b:1", path: "/Sandboxes/x" },
                    { uri: "c@d:2", path: "/" },
                ]);
            await expect(withFolders.listConnections())
                .resolves.toEqual(["a@b:1", "c@d:2"]);
        });

    it("reads both lists in one call, keeping each entry's list", async () => {
        const both = new MariaDbApi(createCaller({
            "db.list_connections": {
                content: [
                    { type: "text", text: '{"uri":"a@b:1","path":"/","kind":"mcp"}' },
                    { type: "text", text: '{"uri":"c@d:2","path":"/X","kind":"gui"}' },
                    { type: "text", text: '{"uri":"e@f:3","path":"/","kind":"odd"}' },
                ],
            },
        }));

        await expect(both.listConnectionEntries("all")).resolves.toEqual([
            { uri: "a@b:1", path: "/", kind: "mcp" },
            { uri: "c@d:2", path: "/X", kind: "gui" },
            // A kind this does not know is no kind at all.
            { uri: "e@f:3", path: "/" },
        ]);
    });

    it("sends a folder only when there is one to send", async () => {
        await api.addConnection("a@b:1", "pw", "gui", false, "/Sandboxes");
        expect(caller.calls[0]!.args).toEqual({
            uri: "a@b:1",
            password: "pw",
            kind: "gui",
            verify: false,
            path: "/Sandboxes",
        });

        // Only what was sent matters; this fake has no answer to decode.
        await api.updateConnection(
            "a@b:1", undefined, "gui", undefined, undefined, "/",
        ).catch(() => { /* nothing to decode */ });
        expect(caller.calls[1]).toEqual({
            name: "db.update_connection",
            args: { uri: "a@b:1", kind: "gui", new_path: "/" },
        });
    });

    it("deletes a connection from the list it names", async () => {
        await expect(api.deleteConnection("dba@localhost:3310", "gui"))
            .resolves.toBe("dba@localhost:3310");
        expect(caller.calls[0]).toEqual({
            name: "db.delete_connection",
            args: { uri: "dba@localhost:3310", kind: "gui" },
        });

        await api.deleteConnection("dba@localhost:3310");
        expect(caller.calls[1]!.args).toEqual({ uri: "dba@localhost:3310" });
    });

    it("lists connections", async () => {
        await expect(api.listConnections())
            .resolves.toEqual(["dba@localhost:3310"]);
        expect(caller.calls[0]).toEqual({
            name: "db.list_connections",
            args: {},
        });
    });

    it("connects and returns the UUID", async () => {
        await expect(api.connect("dba@localhost:3310"))
            .resolves.toBe("uuid-1");
        expect(caller.calls[0].args).toEqual({ uri: "dba@localhost:3310" });
    });

    it("closes by connection id", async () => {
        await api.close("uuid-1");

        expect(caller.calls[0]).toEqual({
            name: "db.close",
            args: { connection_id: "uuid-1" },
        });
    });

    it("lists schemas", async () => {
        await expect(api.listSchemas("uuid-1")).resolves.toEqual([{
            schema_name: "sys",
            schema_type: "User Schema",
            schema_comment: "",
        }]);
    });

    it("lists objects of one type", async () => {
        await expect(api.listObjects("uuid-1", "world", "table"))
            .resolves.toEqual([{ name: "city", comment: "BASE TABLE" }]);
        expect(caller.calls[0].args).toEqual({
            connection_id: "uuid-1",
            schema_name: "world",
            object_type: "table",
        });
    });

    it("describes an object", async () => {
        const details = await api.getObjectDetails(
            "uuid-1", "world", "city", "table");

        expect(details.basic.name).toBe("city");
        expect(caller.calls[0].args).toEqual({
            connection_id: "uuid-1",
            schema_name: "world",
            object_name: "city",
            object_type: "table",
        });
    });

    it("runs a script and returns one result per statement", async () => {
        await expect(api.executeScript("uuid-1", "SELECT 1 AS a;"))
            .resolves.toEqual([{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["a"],
                rows: [{ a: 1 }],
            }]);
        expect(caller.calls[0].args).toEqual({
            connection_id: "uuid-1",
            sql_script: "SELECT 1 AS a;",
        });
    });

    it("turns a reported tool error into a rejection", async () => {
        const failing = new MariaDbApi(createCaller({
            "db.connect": {
                content: [{
                    type: "text",
                    text: "'nope' is not a configured connection.",
                }],
                isError: true,
            },
        }));

        await expect(failing.connect("nope"))
            .rejects.toThrow(/not a configured connection/);
    });
});
