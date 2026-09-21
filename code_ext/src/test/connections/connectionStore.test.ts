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
    deleteConnection,
    fieldsOf,
    kindFor,
    listConnections,
    saveConnection,
    testConnection,
} from "../../connections/connectionStore.js";
import {
    emptyConnectionFields,
    type IConnectionFields,
} from "../../connections/connectionUri.js";
import { createFakeApi, type FakeApiOptions } from "../helpers.js";

/** The editor's defaults with the given fields overridden. */
const fields = (
    overrides: Partial<IConnectionFields> = {},
): IConnectionFields => {
    return { ...emptyConnectionFields(), ...overrides };
};

/** An API answering from the given tables. */
const api = (options: FakeApiOptions = {}) => {
    return createFakeApi(options);
};

describe("kindFor", () => {
    it("maps the MCP access checkbox onto the two lists", () => {
        expect(kindFor(true)).toBe("mcp");
        expect(kindFor(false)).toBe("gui");
    });
});

describe("listConnections", () => {
    it("reports both lists, each entry carrying which one it is in", async () => {
        // The kind is half of what identifies a connection, so it has to
        // survive the listing: editing and deleting both need it.
        const fake = api({
            connections: ["shared@localhost:3306"],
            guiConnections: ["mine@localhost:3307"],
        });

        await expect(listConnections(fake)).resolves.toEqual([
            { uri: "shared@localhost:3306", kind: "mcp" },
            { uri: "mine@localhost:3307", kind: "gui" },
        ]);
    });

    it("copes with either list being empty", async () => {
        await expect(listConnections(api({}))).resolves.toEqual([]);
        await expect(listConnections(api({ guiConnections: ["a@b:1"] })))
            .resolves.toEqual([{ uri: "a@b:1", kind: "gui" }]);
    });
});

describe("saveConnection", () => {
    it("adds a new connection to the list the checkbox picks", async () => {
        const fake = api();

        const result = await saveConnection(fake, {
            fields: fields({ user: "dba" }),
            password: "pw",
            mcpAccess: false,
        });

        expect(result).toEqual({ uri: "dba@localhost:3306", kind: "gui" });
        expect(fake.added).toEqual([{
            uri: "dba@localhost:3306", password: "pw", kind: "gui",
        }]);
        expect(fake.updated).toEqual([]);
    });

    it("puts a connection in the MCP list when the box is ticked", async () => {
        const fake = api();

        await saveConnection(fake, {
            fields: fields({ user: "dba" }), password: "pw", mcpAccess: true,
        });

        expect(fake.added[0]!.kind).toBe("mcp");
    });

    it("stores a new connection without verifying it", async () => {
        // Test Connection is a button of its own, as in the MySQL Shell's
        // editor. A server that happens to be down must not stop its
        // connection being configured.
        const fake = api();

        await saveConnection(fake, {
            fields: fields({ user: "dba" }), password: "pw", mcpAccess: false,
        });

        expect(fake.tested).toEqual([]);
    });

    it("treats no password on a new connection as an empty one", async () => {
        // There is nothing stored to keep, and an account with no password
        // has an empty one.
        const fake = api();

        await saveConnection(fake, {
            fields: fields({ user: "dba" }), mcpAccess: false,
        });

        expect(fake.added[0]!.password).toBe("");
    });

    it("refuses fields that name no connection, before calling anything",
        async () => {
            const fake = api();

            await expect(saveConnection(fake, {
                fields: fields({ user: "" }), mcpAccess: false,
            })).resolves.toEqual({ error: "A user name is required." });

            expect(fake.added).toEqual([]);
            expect(fake.updated).toEqual([]);
        });

    it("re-keys an edited connection rather than adding a second", async () => {
        // The URI is the key, so changing the host is a move. It goes
        // through updateConnection precisely so the password does not have
        // to come back out to be re-stored.
        const fake = api();

        const result = await saveConnection(fake, {
            fields: fields({ user: "dba", host: "db.example.com" }),
            mcpAccess: false,
            original: { uri: "dba@localhost:3306", kind: "gui" },
        });

        expect(fake.added).toEqual([]);
        expect(fake.updated).toEqual([{
            uri: "dba@localhost:3306",
            newUri: "dba@db.example.com:3306",
            kind: "gui",
            newKind: undefined,
            password: undefined,
        }]);
        expect(result.uri).toBe("dba@db.example.com:3306");
    });

    it("leaves the URI out of the update when it did not change", async () => {
        // So the server keeps what it has rather than being told to re-key
        // a connection onto itself.
        const fake = api();

        await saveConnection(fake, {
            fields: fields({ user: "dba" }),
            mcpAccess: false,
            original: { uri: "dba@localhost:3306", kind: "gui" },
        });

        expect(fake.updated[0]).toMatchObject({
            uri: "dba@localhost:3306", newUri: undefined,
        });
    });

    it("moves a connection between the lists when the box is toggled",
        async () => {
            const fake = api();

            await saveConnection(fake, {
                fields: fields({ user: "dba" }),
                mcpAccess: true,
                original: { uri: "dba@localhost:3306", kind: "gui" },
            });

            expect(fake.updated[0]).toMatchObject({
                kind: "gui", newKind: "mcp", newUri: undefined,
            });

            // And back the other way, which is unticking it.
            const other = api();
            await saveConnection(other, {
                fields: fields({ user: "dba" }),
                mcpAccess: false,
                original: { uri: "dba@localhost:3306", kind: "mcp" },
            });

            expect(other.updated[0]).toMatchObject({
                kind: "mcp", newKind: "gui",
            });
        });

    it("keeps the stored password unless a new one was typed", async () => {
        // undefined is not "": nothing here can read a stored password, so
        // an omitted one has to mean "leave it alone" all the way down.
        const fake = api();
        const original = { uri: "dba@localhost:3306", kind: "gui" as const };

        await saveConnection(fake, {
            fields: fields({ user: "dba" }), mcpAccess: false, original,
        });
        expect(fake.updated[0]!.password).toBeUndefined();

        await saveConnection(fake, {
            fields: fields({ user: "dba" }),
            password: "new",
            mcpAccess: false,
            original,
        });
        expect(fake.updated[1]!.password).toBe("new");

        // An explicitly empty password is a real change, not an omission.
        await saveConnection(fake, {
            fields: fields({ user: "dba" }),
            password: "",
            mcpAccess: false,
            original,
        });
        expect(fake.updated[2]!.password).toBe("");
    });
});

describe("testConnection", () => {
    it("tries the fields as a URI without storing anything", async () => {
        const fake = api();

        await expect(testConnection(fake, fields({ user: "dba" }), "pw"))
            .resolves.toContain("dba@localhost:3306");

        expect(fake.tested).toEqual([
            { uri: "dba@localhost:3306", password: "pw" },
        ]);
        expect(fake.added).toEqual([]);
        expect(fake.updated).toEqual([]);
    });

    it("sends no password when the stored one should be used", async () => {
        const fake = api();

        await testConnection(fake, fields({ user: "dba" }));

        expect(fake.tested[0]!.password).toBeUndefined();
    });

    it("passes the server's reason on when it fails", async () => {
        const fake = api({ testFailure: "Access denied for user 'dba'" });

        await expect(testConnection(fake, fields({ user: "dba" }), "wrong"))
            .rejects.toThrow("Access denied");
    });

    it("refuses fields that name no connection", async () => {
        const fake = api();

        await expect(testConnection(fake, fields({ user: "" }), "pw"))
            .rejects.toThrow("A user name is required.");
        expect(fake.tested).toEqual([]);
    });
});

describe("deleteConnection", () => {
    it("deletes from the list the connection is in", async () => {
        const fake = api();

        await deleteConnection(fake, {
            uri: "dba@localhost:3306", kind: "mcp",
        });

        expect(fake.deleted).toEqual([
            { uri: "dba@localhost:3306", kind: "mcp" },
        ]);
    });
});

describe("fieldsOf", () => {
    it("opens the editor on an existing connection", () => {
        expect(fieldsOf({
            uri: "dba@db.example.com:3307/world?ssl-mode=REQUIRED",
            kind: "mcp",
        })).toEqual({
            mcpAccess: true,
            fields: fields({
                user: "dba",
                host: "db.example.com",
                port: "3307",
                schema: "world",
                sslMode: "REQUIRED",
            }),
        });
    });

    it("leaves the MCP box unticked for one of the extension's own", () => {
        expect(fieldsOf({ uri: "dba@localhost:3306", kind: "gui" }).mcpAccess)
            .toBe(false);
    });
});
