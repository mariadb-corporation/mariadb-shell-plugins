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

import {
    ConnectionManager,
    UI_BACKEND_SESSION,
} from "../../connections/connectionManager.js";
import {
    ConnectionsTreeProvider,
    CONNECTIONS_VIEW_STATE_CONTEXT_KEY,
    CONNECTIONS_VIEW_ID,
} from "../../tree/connectionsTreeProvider.js";
import type { IconResolver } from "../../tree/treeItems.js";
import type {
    IServerStatus,
    ServerPhase,
} from "../../mcp/serverStarter.js";
import {
    createFakeApi,
    createFakeSettings,
    createRecordingLog,
} from "../helpers.js";
import {
    contextKeys,
    errorMessages,
    resetVscodeMock,
    ThemeIcon,
    Uri,
} from "../mocks/vscode.js";

const resolveIcon: IconResolver = (name: string) => {
    return {
        light: Uri.file(`/ext/images/light/${name}`),
        dark: Uri.file(`/ext/images/dark/${name}`),
    } as never;
};

/**
 * @returns A server status whose phase a test moves by hand.
 */
const createStatus = (): IServerStatus & {
    set(phase: ServerPhase): void;
} => {
    const listeners = new Set<(phase: ServerPhase) => void>();
    const status = {
        phase: "stopped" as ServerPhase,
        onDidChangePhase: (listener: (phase: ServerPhase) => void) => {
            listeners.add(listener);

            return () => { listeners.delete(listener); };
        },
        set: (phase: ServerPhase) => {
            status.phase = phase;
            for (const listener of listeners) {
                listener(phase);
            }
        },
    };

    return status;
};

/**
 * @param connectOnOpen Whether expanding a closed connection opens it.
 * @param status The server startup the view follows, if any.
 *
 * @returns A provider over a fake server with one connection.
 */
const createProvider = (connectOnOpen = false, status?: IServerStatus) => {
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
        connections, resolveIcon, log, () => { return connectOnOpen; },
        status);

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
            expandable: false,
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
        await connections.connect("dba@localhost:3310", UI_BACKEND_SESSION);
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

        await connections.connect("dba@localhost:3310", UI_BACKEND_SESSION);

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
            expandable: true,
        } as const;

        provider.refresh(node);

        expect(fired).toEqual([node]);

        provider.dispose();
    });

    it("says nothing is configured only once it has asked", async () => {
        const { api, provider } = createProvider();
        // Both lists are asked for at once, so one gate holds them both.
        let answer: () => void = () => { /* set below */ };
        const listed = new Promise<void>((resolve) => {
            answer = resolve;
        });
        api.listConnections = async () => {
            await listed;

            return [];
        };

        const roots = provider.getChildren();

        // The list comes from a server that has to be started first, so
        // an empty tree means "not asked yet" until this comes back -
        // and the welcome content says so rather than claiming there is
        // nothing configured.
        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("looking");

        answer();
        await expect(roots).resolves.toEqual([]);

        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("listed");

        provider.dispose();
    });

    it("says the listing failed rather than that nothing is there",
        async () => {
            const { api, provider } = createProvider();
            api.listConnections = () => {
                return Promise.reject(new Error("the shell is not running"));
            };

            await provider.getChildren();

            // A view left looking for ever would be as wrong as one
            // saying nothing is there.
            expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
                .toBe("failed");

            provider.dispose();
        });

    it("says the shell is being installed while it is", () => {
        const status = createStatus();
        const { provider } = createProvider(false, status);

        status.set("locating");
        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("looking");

        status.set("installing");
        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("installing");

        status.set("starting");
        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("looking");

        provider.dispose();
    });

    it("says a server that did not start failed", () => {
        const status = createStatus();
        const { provider } = createProvider(false, status);

        status.set("locating");
        status.set("failed");

        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("failed");

        provider.dispose();
    });

    it("goes back to looking when a failed start is retried", async () => {
        const status = createStatus();
        const { api, provider } = createProvider(false, status);
        api.listConnections = () => {
            return Promise.reject(new Error("the shell is not running"));
        };
        await provider.getChildren();

        status.set("locating");

        expect(contextKeys.get(CONNECTIONS_VIEW_STATE_CONTEXT_KEY))
            .toBe("looking");

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
                .toContain("Failed to list the connections");

            provider.dispose();
        });

    it("opens a connection the user expands", async () => {
        const { connections, provider } = createProvider(true);
        const [root] = await provider.getChildren();

        await provider.expanded(root);

        expect(connections.isConnected("dba@localhost:3310")).toBe(true);
        // The children come from the refresh that opening fires, which is
        // why expanding answers with nothing itself.
        expect(await provider.getChildren(root)).toHaveLength(1);

        provider.dispose();
    });

    it("opens its own connection on a connection something else opened",
        async () => {
            // The explicit mode is about not connecting to a server
            // behind the user's back, and an editor has already
            // connected to this one; without the tree's own connection
            // the row would expand to nothing and stay that way.
            const { connections, provider } = createProvider();
            await connections.connect("dba@localhost:3310");
            const [root] = await provider.getChildren();

            await provider.expanded(root);

            expect(connections.isConnected(
                "dba@localhost:3310", UI_BACKEND_SESSION)).toBe(true);
            expect(await provider.getChildren(root)).toHaveLength(1);

            provider.dispose();
        });

    it("leaves a connection closed on expand in the explicit mode",
        async () => {
            const { connections, provider } = createProvider();
            const [root] = await provider.getChildren();

            await provider.expanded(root);

            expect(connections.isConnected("dba@localhost:3310")).toBe(false);

            provider.dispose();
        });

    it("says under the row why a connection would not open", async () => {
        const { api, connections, provider, log } = createProvider(true);
        const [root] = await provider.getChildren();
        api.connect = () => {
            return Promise.reject(new Error("access denied\nmore detail"));
        };

        await provider.expanded(root);

        expect(connections.isConnected("dba@localhost:3310")).toBe(false);
        const [status] = await provider.getChildren(root);
        expect(status).toMatchObject({
            kind: "connectionStatus",
            state: "failed",
            message: "access denied\nmore detail",
        });
        const item = provider.getTreeItem(status!);
        expect(item.label).toBe("access denied");
        expect(item.tooltip).toBe("access denied\nmore detail");
        expect(item.contextValue).toBe("mariadbConnectionStatus.failed");
        // Under the row the user is looking at, not in a notification too.
        expect(errorMessages).toEqual([]);
        expect(log.lines.join("\n"))
            .toContain("Failed to open 'dba@localhost:3310': access denied");

        provider.dispose();
    });

    it("shows a spinner under the row while the connection opens",
        async () => {
            const { api, provider } = createProvider(true);
            const [root] = await provider.getChildren();
            const connect = api.connect.bind(api);
            let release = (): void => { /* set below */ };
            api.connect = (...args: Parameters<typeof connect>) => {
                return new Promise((resolve, reject) => {
                    release = () => { connect(...args).then(resolve, reject); };
                });
            };
            const redrawn: unknown[] = [];
            provider.onDidChangeTreeData((node) => { redrawn.push(node); });

            const opening = provider.expanded(root!);

            // Redrawn at once, not when the server answers.
            expect(redrawn).toEqual([root]);
            const [status] = await provider.getChildren(root);
            expect(status).toMatchObject({
                kind: "connectionStatus", state: "connecting",
            });
            const item = provider.getTreeItem(status!);
            expect(item.label).toBe("Connecting...");
            expect(item.iconPath).toEqual(new ThemeIcon("loading~spin"));

            // A second expand while it is under way does not open another.
            await provider.expanded(root!);

            release();
            await opening;

            const children = await provider.getChildren(root);
            expect(children).toHaveLength(1);
            expect(children[0]).toMatchObject({ kind: "schema" });

            provider.dispose();
        });

    it("opens the connection on a retry of a failed attempt", async () => {
        const { api, connections, provider } = createProvider(true);
        const [root] = await provider.getChildren();
        const connect = api.connect.bind(api);
        api.connect = () => {
            return Promise.reject(new Error("the server is down"));
        };
        await provider.expanded(root!);
        const [status] = await provider.getChildren(root);

        api.connect = connect;
        await provider.retry(status as never);

        expect(connections.isConnected(
            "dba@localhost:3310", UI_BACKEND_SESSION)).toBe(true);
        expect(await provider.getChildren(root)).toMatchObject([
            { kind: "schema", schema: "world" },
        ]);

        provider.dispose();
    });

    it("ignores the expansion of anything but a connection", async () => {
        const { connections, provider } = createProvider(true);

        await provider.expanded({
            kind: "objectGroup",
            uri: "dba@localhost:3310",
            schema: "world",
            objectType: "table",
        });

        expect(connections.isConnected("dba@localhost:3310")).toBe(false);

        provider.dispose();
    });

    it("stops listening once disposed", async () => {
        const { connections, provider } = createProvider();
        const fired: unknown[] = [];
        provider.onDidChangeTreeData((node) => {
            fired.push(node);
        });

        provider.dispose();
        await connections.connect("dba@localhost:3310", UI_BACKEND_SESSION);

        expect(fired).toEqual([]);
    });
});
