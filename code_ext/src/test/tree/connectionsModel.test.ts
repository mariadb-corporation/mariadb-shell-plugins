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

import { ConnectionManager } from "../../connections/connectionManager.js";
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
 *
 * @returns A model over a fake server holding one schema with one table.
 */
const createModel = (defaultConnection?: string) => {
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

    return { api, manager, model: new ConnectionsModel(manager) };
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
            const model = new ConnectionsModel(new ConnectionManager(
                () => { return Promise.resolve(api); },
                createFakeSettings(),
            ));

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
            },
            {
                kind: "connection",
                uri: "app@localhost:3311",
                connected: false,
                isDefault: false,
                connectionKind: "mcp",
            },
        ]);
    });

    it("marks the default connection", async () => {
        const { model } = createModel("app@localhost:3311");

        const roots = await model.getRoots();

        expect(roots.map((node) => {
            return node.isDefault;
        })).toEqual([false, true]);
    });

    it("marks an open connection", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310");

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
        };
    };

    it("gives a closed connection no children", async () => {
        const { model } = createModel();

        await expect(model.getChildren(connectionNode(false)))
            .resolves.toEqual([]);
    });

    it("lists the schemas of an open connection", async () => {
        const { manager, model } = createModel();
        await manager.connect("dba@localhost:3310");

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
            await manager.connect("dba@localhost:3310");
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
        await manager.connect("dba@localhost:3310");
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
        await manager.connect("dba@localhost:3310");

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
