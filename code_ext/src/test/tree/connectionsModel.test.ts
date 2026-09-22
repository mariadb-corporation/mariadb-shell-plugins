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
    ConnectionManager,
    UI_BACKEND_SESSION,
} from "../../connections/connectionManager.js";
import {
    ConnectionsModel,
    OBJECT_GROUP_LABELS,
    type IConnectionNode,
    type IObjectGroupNode,
    type ISchemaNode,
} from "../../tree/connectionsModel.js";
import { createFakeApi, createFakeSettings } from "../helpers.js";

/**
 * @param defaultConnection The default connection, if any.
 * @param connectOnOpen Whether expanding a closed connection opens it.
 *
 * @returns A model over a fake server holding one schema with one table.
 */
const createModel = (defaultConnection?: string, connectOnOpen = false) => {
    const api = createFakeApi({
        connections: ["dba@localhost:3310", "app@localhost:3311"],
        connectionIds: { "dba@localhost:3310": "uuid-dba" },
        schemas: [
            {
                schema_name: "world",
                schema_type: "User Schema",
                schema_comment: "the sample",
            },
            {
                schema_name: "mysql",
                schema_type: "System Schema",
                schema_comment: "",
            },
        ],
        objects: {
            "world/table": [
                { name: "city", comment: "BASE TABLE" },
                { name: "country" },
            ],
            "world/view": [{ name: "city_view", comment: "VIEW" }],
        },
    });
    const manager = new ConnectionManager(
        () => {
            return Promise.resolve(api);
        },
        createFakeSettings(defaultConnection),
    );

    return {
        api,
        manager,
        model: new ConnectionsModel(manager, () => { return connectOnOpen; }),
    };
};

describe("ConnectionsModel.getRoots", () => {
    it("lists both connection lists, each knowing which it came from",
        async () => {
            // A connection in the shared MCP list is as usable from the
            // editor as one of the extension's own, so the tree shows both -
            // and has to remember which is which, since that is half of what
            // identifies a connection.
            const api = createFakeApi({
                connections: ["shared@localhost:3306"],
                guiConnections: ["mine@localhost:3307"],
            });
            const model = new ConnectionsModel(
                new ConnectionManager(
                    () => { return Promise.resolve(api); },
                    createFakeSettings(),
                ),
                () => { return false; },
            );

            expect((await model.getRoots()).map((node) => {
                return [node.uri, node.connectionKind];
            })).toEqual([
                ["shared@localhost:3306", "mcp"],
                ["mine@localhost:3307", "gui"],
            ]);
        });

    it("lists one node per configured connection", async () => {
        const { model } = createModel();

        await expect(model.getRoots()).resolves.toEqual([
            {
                kind: "connection",
                uri: "dba@localhost:3310",
                connected: false,
                isDefault: false,
                connectionKind: "mcp",
                expandable: false,
            },
            {
                kind: "connection",
                uri: "app@localhost:3311",
                connected: false,
                isDefault: false,
                connectionKind: "mcp",
                expandable: false,
            },
        ]);
    });

    it("gives a closed connection a twistie only where opening it connects",
        async () => {
            // The twistie is the whole gesture in that mode, and a twistie
            // that can never show anything is a dead end in the other.
            const explicit = await createModel().model.getRoots();
            const onOpen = await createModel(undefined, true).model.getRoots();

            expect(explicit.map((node) => { return node.expandable; }))
                .toEqual([false, false]);
            expect(onOpen.map((node) => { return node.expandable; }))
                .toEqual([true, true]);
        });

    it("gives an open connection a twistie in either mode", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);

        expect((await model.getRoots())[0].expandable).toBe(true);
    });

    it("marks the default connection", async () => {
        const { model } = createModel("app@localhost:3311");

        const roots = await model.getRoots();

        expect(roots.map((node) => {
            return node.isDefault;
        })).toEqual([false, true]);
    });

    it("marks it whether or not the setting names its scheme", async () => {
        // The setting was written before connections carried one, and what
        // db.list_connections reports now always does. They are one
        // connection, so the marker has to survive the difference - either
        // way round, since a setting written now carries the scheme and a
        // connection stored before the change is still listed without one.
        const withScheme = await createModel(
            "mariadb://app@localhost:3311",
        ).model.getRoots();

        expect(withScheme.map((node) => { return node.isDefault; }))
            .toEqual([false, true]);

        const api = createFakeApi({
            connections: ["mariadb://app@localhost:3311"],
        });
        const listed = new ConnectionsModel(
            new ConnectionManager(
                () => { return Promise.resolve(api); },
                createFakeSettings("app@localhost:3311"),
            ),
            () => { return false; },
        );

        expect((await listed.getRoots())[0].isDefault).toBe(true);
    });

    it("marks an open connection", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);

        const roots = await model.getRoots();

        expect(roots[0].connected).toBe(true);
        expect(roots[1].connected).toBe(false);
    });
});

describe("ConnectionsModel.getChildren", () => {
    const connectionNode = (connected: boolean): IConnectionNode => {
        return {
            kind: "connection",
            uri: "dba@localhost:3310",
            connected,
            isDefault: false,
            connectionKind: "gui",
            expandable: connected,
        };
    };

    it("gives a closed connection no children", async () => {
        const { model } = createModel();

        await expect(model.getChildren(connectionNode(false)))
            .resolves.toEqual([]);
    });

    it("does not open a closed connection to answer for its children",
        async () => {
            // Even where expanding the row is what opens a connection: the
            // tree asks again on every refresh, so a connection that opened
            // itself here could never be disconnected.
            const { manager, model } = createModel(undefined, true);

            await expect(model.getChildren(connectionNode(false)))
                .resolves.toEqual([]);
            expect(manager.isConnected("dba@localhost:3310")).toBe(false);
        });

    it("marks a connection an editor has open, though the tree has not",
        async () => {
            const { manager, model } = createModel();
            await manager.connect("dba@localhost:3310");

            const [node] = await model.getRoots();

            // What the row says, and what disconnecting it closes, is
            // the whole of what is open on the connection.
            expect(node.connected).toBe(true);
            // The tree browses on its own connection, which is not open
            // yet, so it has nothing to show until it is.
            await expect(model.getChildren(node)).resolves.toEqual([]);
        });

    it("lists the schemas of an open connection", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);

        await expect(model.getChildren(connectionNode(true)))
            .resolves.toEqual([
                {
                    kind: "schema",
                    uri: "dba@localhost:3310",
                    schema: "world",
                    schemaType: "User Schema",
                    comment: "the sample",
                },
                {
                    kind: "schema",
                    uri: "dba@localhost:3310",
                    schema: "mysql",
                    schemaType: "System Schema",
                    comment: "",
                },
            ]);
    });

    it("gives a schema one folder per object type, without querying",
        async () => {
            const { api, manager, model } = createModel();
            await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);
            const schema: ISchemaNode = {
                kind: "schema",
                uri: "dba@localhost:3310",
                schema: "world",
                schemaType: "User Schema",
                comment: "",
            };

            const groups = await model.getChildren(schema);

            expect(groups.map((node) => {
                return (node as IObjectGroupNode).objectType;
            })).toEqual([
                "table", "view", "function", "procedure",
                "sequence", "trigger", "event",
            ]);
            // Expanding a schema costs nothing until a folder is opened.
            expect(api.scripts).toEqual([]);
        });

    it("names every object group", () => {
        expect(Object.values(OBJECT_GROUP_LABELS)).toEqual([
            "Tables", "Views", "Functions", "Procedures",
            "Sequences", "Triggers", "Events",
        ]);
    });

    it("lists the objects of a group", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);
        const group: IObjectGroupNode = {
            kind: "objectGroup",
            uri: "dba@localhost:3310",
            schema: "world",
            objectType: "table",
        };

        await expect(model.getChildren(group)).resolves.toEqual([
            {
                kind: "object",
                uri: "dba@localhost:3310",
                schema: "world",
                objectType: "table",
                name: "city",
                comment: "BASE TABLE",
            },
            {
                kind: "object",
                uri: "dba@localhost:3310",
                schema: "world",
                objectType: "table",
                name: "country",
                comment: undefined,
            },
        ]);
    });

    it("gives an empty group no children", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310", UI_BACKEND_SESSION);

        await expect(model.getChildren({
            kind: "objectGroup",
            uri: "dba@localhost:3310",
            schema: "world",
            objectType: "event",
        })).resolves.toEqual([]);
    });

    it("gives a group of a closed connection no children", async () => {
        const { model } = createModel();

        await expect(model.getChildren({
            kind: "objectGroup",
            uri: "dba@localhost:3310",
            schema: "world",
            objectType: "table",
        })).resolves.toEqual([]);
    });

    it("gives an object no children", async () => {
        const { model } = createModel();

        await expect(model.getChildren({
            kind: "object",
            uri: "dba@localhost:3310",
            schema: "world",
            objectType: "table",
            name: "city",
        })).resolves.toEqual([]);
    });
});
