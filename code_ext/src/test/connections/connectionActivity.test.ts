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
    activityRow,
    createLoggingApi,
    type IActivityEvent,
} from "../../connections/connectionActivity.js";
import { createFakeApi } from "../helpers.js";

/**
 * @param overrides The fields that differ from a plain listing.
 *
 * @returns One event.
 */
const event = (
    overrides: Partial<IActivityEvent> = {},
): IActivityEvent => {
    return {
        connection: "dba@localhost:3310",
        label: "UI Backend",
        call: "db.list_schemas()",
        message: "Listed 2 schemas",
        when: new Date(2026, 8, 21, 12, 0, 0, 123),
        elapsedMs: 7,
        ...overrides,
    };
};

/**
 * @param logAll Whether the calls on no connection are reported too.
 *
 * @returns A logging API over a fake server, with what it reported.
 */
const createLogging = (logAll = false) => {
    const api = createFakeApi({
        connectionIds: { "dba@localhost:3310": "uuid-dba" },
        schemas: [
            {
                schema_name: "world",
                schema_type: "User Schema",
                schema_comment: "",
            },
        ],
        objects: {
            "world/table": [{ name: "city" }, { name: "country" }],
        },
        details: {
            "world.city": {
                basic: { schema: "world", name: "city", type: "table" },
                columns: [],
            },
        },
    });
    const reported: IActivityEvent[] = [];
    const logging = createLoggingApi(
        api,
        (connectionId) => {
            return connectionId === "uuid-dba"
                ? { uri: "dba@localhost:3310", label: "UI Backend" }
                : undefined;
        },
        (reportedEvent) => { reported.push(reportedEvent); },
        () => { return logAll; },
    );

    return { api, logging, reported };
};

describe("activityRow", () => {
    it("shows what happened, with the call beside it", () => {
        expect(activityRow(event(), "event1")).toEqual({
            id: "event1",
            time: "12:00:00.123",
            connection: "dba@localhost:3310",
            connectionLabel: "UI Backend",
            role: "event",
            // The Information column, where a statement row holds SQL.
            statement: "db.list_schemas()",
            message: "Listed 2 schemas",
            kind: "info",
            elapsedMs: 7,
        });
    });

    it("marks a call that failed, and says why instead", () => {
        const row = activityRow(
            event({ error: "the connection went away" }), "event2");

        expect(row.kind).toBe("error");
        expect(row.message).toBe("the connection went away");
    });
});

describe("createLoggingApi", () => {
    it("reports a listing of the schemas", async () => {
        const { logging, reported } = createLogging();

        await logging.listSchemas("uuid-dba");

        expect(reported).toHaveLength(1);
        expect(reported[0]).toMatchObject({
            connection: "dba@localhost:3310",
            label: "UI Backend",
            call: "db.list_schemas()",
            // The count is in the message: the Output column is where
            // the grid reads it, so no column repeats it.
            message: "Listed 1 schema",
        });
    });

    it("reports a listing of one kind of object", async () => {
        const { logging, reported } = createLogging();

        await logging.listObjects("uuid-dba", "world", "table");

        expect(reported[0]).toMatchObject({
            call: "db.list_objects(world, table)",
            message: "Listed 2 tables in world",
        });
    });

    it("reports an object being described", async () => {
        const { logging, reported } = createLogging();

        await logging.getObjectDetails("uuid-dba", "world", "city", "table");

        expect(reported[0]).toMatchObject({
            call: "db.get_object_details(world, city, table)",
            message: "Described the table world.city",
        });
    });

    it("reports a call that failed, and still raises it", async () => {
        const { api, logging, reported } = createLogging();
        api.getObjectDetails = () => {
            return Promise.reject(new Error("The connection was closed."));
        };

        await expect(logging.getObjectDetails(
            "uuid-dba", "world", "city", "table")).rejects.toThrow();

        expect(reported[0].error).toBe("The connection was closed.");
    });

    it("reports no such object as the answer it is, not as an error",
        async () => {
            // How a SELECT on a view - mysql.user - is found out to be no
            // table: the lookup is how the question is asked.
            const { logging, reported } = createLogging();

            await expect(logging.getObjectDetails(
                "uuid-dba", "world", "nope", "table")).rejects.toThrow(
                "No table 'nope' found in schema 'world'");

            expect(reported).toHaveLength(1);
            expect(reported[0]!.error).toBeUndefined();
            expect(reported[0]!.message).toBe("No table world.nope");
            expect(activityRow(reported[0]!, "e1").kind).toBe("info");
        });

    it("times what it reports", async () => {
        const { logging, reported } = createLogging();

        await logging.listSchemas("uuid-dba");

        expect(reported[0].elapsedMs).toBeGreaterThanOrEqual(0);
        expect(reported[0].when).toBeInstanceOf(Date);
    });

    it("says nothing about a connection it does not know", async () => {
        const { logging, reported } = createLogging();

        // A UUID from before the extension's own bookkeeping, or one
        // closed while the call was in flight: there is no connection to
        // put the row under.
        await logging.listSchemas("uuid-somebody-else");

        expect(reported).toEqual([]);
    });

    it("leaves the connection list alone", async () => {
        const { logging, reported } = createLogging();

        await logging.listConnections();
        await logging.testConnection("dba@localhost:3310", "secret");

        // These work on the configured list, not on anything open, so
        // they are no part of a connection's log.
        expect(reported).toEqual([]);
    });

    it("reports the calls on no connection under General Actions when asked",
        async () => {
            const { logging, reported } = createLogging(true);

            await logging.listConnections("gui");
            await logging.addConnection(
                "a@b:1", "secret", "gui", false, "/Sandboxes");
            await logging.updateConnection(
                "a@b:1", undefined, "gui", "mcp", "secret", "/");
            await logging.deleteConnection("a@b:1", "mcp");
            await logging.testConnection("a@b:1", "secret");

            expect(reported.map((row) => {
                return [row.connection, row.label, row.call, row.message];
            })).toEqual([
                ["General Actions", "", "db.list_connections(kind=gui)",
                    "Listed 0 connections"],
                ["General Actions", "",
                    "db.add_connection(a@b:1, kind=gui, verify=false, "
                    + "path=/Sandboxes)",
                    "Stored a@b:1"],
                ["General Actions", "",
                    "db.update_connection(a@b:1, kind=gui, new_kind=mcp, "
                    + "password=***, new_path=/)",
                    "Updated a@b:1"],
                ["General Actions", "", "db.delete_connection(a@b:1, kind=mcp)",
                    "Deleted a@b:1"],
                ["General Actions", "", "db.test_connection(a@b:1)",
                    "Connected to 'a@b:1' successfully."],
            ]);
            // Whatever else is reported, a password never is.
            expect(JSON.stringify(reported)).not.toContain("secret");
        });

    it("reports a call on no connection that failed, and still throws",
        async () => {
            const { api, logging, reported } = createLogging(true);
            api.deleteConnection = () => {
                return Promise.reject(new Error("no such connection"));
            };

            await expect(logging.deleteConnection("a@b:1"))
                .rejects.toThrow("no such connection");

            expect(reported).toMatchObject([{
                connection: "General Actions",
                call: "db.delete_connection(a@b:1)",
                error: "no such connection",
            }]);
        });

    it("leaves running SQL to report itself", async () => {
        const { logging, reported } = createLogging();

        await logging.executeScript("uuid-dba", "SELECT 1;");

        // A run is a tree of rows the execution service builds; a
        // second row for the same call would only double it.
        expect(reported).toEqual([]);
    });

    it("passes every call through to the server", async () => {
        const { api, logging } = createLogging();

        await logging.connect("dba@localhost:3310");
        await logging.close("uuid-dba");
        await logging.addConnection("app@localhost:3311", "secret", "gui");
        await logging.updateConnection("app@localhost:3311", "app@host");
        await logging.deleteConnection("app@host", "gui");
        await logging.executeScript("uuid-dba", "SELECT 1;");

        expect(api.closed).toEqual(["uuid-dba"]);
        expect(api.added).toEqual([{
            uri: "app@localhost:3311",
            password: "secret",
            kind: "gui",
        }]);
        expect(api.updated).toEqual([{
            uri: "app@localhost:3311",
            newUri: "app@host",
            kind: undefined,
            newKind: undefined,
            password: undefined,
        }]);
        expect(api.deleted).toEqual([{ uri: "app@host", kind: "gui" }]);
        expect(api.scripts).toEqual(["SELECT 1;"]);
    });
});
