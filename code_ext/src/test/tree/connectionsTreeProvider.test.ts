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

import { ConnectionManager } from "../../connections/connectionManager.js";
import {
    ConnectionsTreeProvider,
    CONNECTIONS_VIEW_ID,
} from "../../tree/connectionsTreeProvider.js";
import type { IconResolver } from "../../tree/treeItems.js";
import {
    createFakeApi,
    createFakeSettings,
    createRecordingLog,
} from "../helpers.js";
import { errorMessages, resetVscodeMock, Uri } from "../mocks/vscode.js";

const resolveIcon: IconResolver = (name: string) => {
    return {
        light: Uri.file(`/ext/images/light/${name}`),
        dark: Uri.file(`/ext/images/dark/${name}`),
    } as never;
};

/**
 * @returns A provider over a fake server with one connection.
 */
const createProvider = () => {
    const api = createFakeApi({
        connections: ["dba@localhost:3310"],
        connectionIds: { "dba@localhost:3310": "uuid-dba" },
        schemas: [{
            schema_name: "world",
            schema_type: "User Schema",
            schema_comment: "",
        }],
    });
    const connections = new ConnectionManager(
        () => {
            return Promise.resolve(api);
        },
        createFakeSettings(),
    );
    const log = createRecordingLog();
    const provider = new ConnectionsTreeProvider(
        connections, resolveIcon, log);

    return { api, connections, provider, log };
};

describe("ConnectionsTreeProvider", () => {
    beforeEach(() => {
        resetVscodeMock();
    });

    it("is contributed under the documented view id", () => {
        expect(CONNECTIONS_VIEW_ID).toBe("mariadb.connections");
    });

    it("lists the connections at the root", async () => {
        const { provider } = createProvider();

        const roots = await provider.getChildren();

        expect(roots).toEqual([{
            kind: "connection",
            uri: "dba@localhost:3310",
            connected: false,
            isDefault: false,
            connectionKind: "mcp",
        }]);

        provider.dispose();
    });

    it("turns a node into a tree item", async () => {
        const { provider } = createProvider();
        const [root] = await provider.getChildren();

        const item = provider.getTreeItem(root);

        expect(item.label).toBe("dba@localhost:3310");
        expect(item.contextValue)
            .toBe("mariadbConnection.disconnected.notDefault");

        provider.dispose();
    });

    it("expands an open connection", async () => {
        const { connections, provider } = createProvider();
        await connections.connect("dba@localhost:3310");
        const [root] = await provider.getChildren();

        const children = await provider.getChildren(root);

        expect(children).toHaveLength(1);
        expect(provider.getTreeItem(children[0]).label).toBe("world");

        provider.dispose();
    });

    it("redraws when a connection opens", async () => {
        const { connections, provider } = createProvider();
        const fired: unknown[] = [];
        provider.onDidChangeTreeData((node) => {
            fired.push(node);
        });

        await connections.connect("dba@localhost:3310");

        expect(fired).toEqual([undefined]);

        provider.dispose();
    });

    it("redraws one subtree on request", () => {
        const { provider } = createProvider();
        const fired: unknown[] = [];
        provider.onDidChangeTreeData((node) => {
            fired.push(node);
        });
        const node = {
            kind: "connection",
            uri: "dba@localhost:3310",
            connected: true,
            isDefault: false,
            connectionKind: "gui",
        } as const;

        provider.refresh(node);

        expect(fired).toEqual([node]);

        provider.dispose();
    });

    it("shows an empty branch and says why when the server fails",
        async () => {
            const { api, provider, log } = createProvider();
            api.listConnections = () => {
                return Promise.reject(new Error("the shell is not running"));
            };

            // A tree that throws shows nothing and explains nothing, so
            // the branch comes back empty and the reason is surfaced.
            await expect(provider.getChildren()).resolves.toEqual([]);
            expect(errorMessages)
                .toEqual(["MariaDB: the shell is not running"]);
            expect(log.lines.join("\n"))
                .toContain("Failed to populate the Connections view");

            provider.dispose();
        });

    it("stops listening once disposed", async () => {
        const { connections, provider } = createProvider();
        const fired: unknown[] = [];
        provider.onDidChangeTreeData((node) => {
            fired.push(node);
        });

        provider.dispose();
        await connections.connect("dba@localhost:3310");

        expect(fired).toEqual([]);
    });
});
