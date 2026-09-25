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

import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type {
    IConnectionNode,
    IObjectNode,
} from "../../tree/connectionsModel.js";
import {
    FolderTreeItem,
    connectionIconFor,
    createTreeItem,
    type IconResolver,
} from "../../tree/treeItems.js";
import {
    ThemeColor,
    ThemeIcon,
    TreeItemCollapsibleState,
    Uri,
} from "../mocks/vscode.js";

/** The extension's own folder, where `images/` is. */
const EXTENSION_ROOT = join(
    dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/** Resolves an icon name to a recognisable pair of paths. */
const resolveIcon: IconResolver = (name: string) => {
    return {
        light: Uri.file(`/ext/images/light/${name}`),
        dark: Uri.file(`/ext/images/dark/${name}`),
    } as never;
};

/**
 * @param overrides The fields that differ from a plain closed connection.
 *
 * @returns A connection node.
 */
const connection = (
    overrides: Partial<IConnectionNode> = {},
): IConnectionNode => {
    return {
        kind: "connection",
        uri: "dba@localhost:3310",
        connected: false,
        isDefault: false,
        connectionKind: "gui",
        expandable: false,
        ...overrides,
    };
};

describe("createTreeItem for a connection", () => {
    it("shows a URI without a scheme with the MariaDB icon", () => {
        // One stored before the scheme was kept, which means mariadb://.
        const item = createTreeItem(connection(), resolveIcon);

        expect(item.label).toBe("dba@localhost:3310");
        expect(item.iconPath).toEqual({
            light: Uri.file("/ext/images/light/connectionMariaDB.svg"),
            dark: Uri.file("/ext/images/dark/connectionMariaDB.svg"),
        });
    });

    it("picks the icon by scheme and leaves the scheme out of the label",
        () => {
            for (const [scheme, icon] of [
                ["mariadb", "connectionMariaDB.svg"],
                ["mariadb+ssh", "connectionMariaDBSSH.svg"],
                ["mysql", "connectionMySQL.svg"],
                ["mysql+ssh", "connectionMySQLSSH.svg"],
                ["mysqlx", "connectionMySQL.svg"],
                ["MySQL+SSH", "connectionMySQLSSH.svg"],
            ]) {
                const item = createTreeItem(
                    connection({ uri: `${scheme}://dba@localhost:3310` }),
                    resolveIcon,
                );

                expect(item.label).toBe("dba@localhost:3310");
                expect(item.iconPath).toEqual({
                    light: Uri.file(`/ext/images/light/${icon}`),
                    dark: Uri.file(`/ext/images/dark/${icon}`),
                });
            }
        });

    it("shows the port and schema but not the options", () => {
        const uri = "mariadb+ssh://dba@db:3310/world"
            + "?ssh-host=bastion&ssl-mode=REQUIRED";
        const item = createTreeItem(connection({ uri }), resolveIcon);

        expect(item.label).toBe("dba@db:3310/world");
        // The tooltip is where the whole of it is still to be read.
        expect(item.tooltip).toBe(uri);
    });

    it("has no twistie where the model says it has nothing to show", () => {
        expect(createTreeItem(connection(), resolveIcon).collapsibleState)
            .toBe(TreeItemCollapsibleState.None);
    });

    it("has one where the model says so, open or not", () => {
        // A closed connection is expandable where expanding it is what
        // opens it, which is the model's call and not this one's.
        for (const node of [
            connection({ connected: true, expandable: true }),
            connection({ connected: false, expandable: true }),
        ]) {
            expect(createTreeItem(node, resolveIcon).collapsibleState)
                .toBe(TreeItemCollapsibleState.Collapsed);
        }
    });

    it("carries its state in the context value, for the menus", () => {
        expect(createTreeItem(connection(), resolveIcon).contextValue)
            .toBe("mariadbConnection.disconnected.notDefault");
        expect(createTreeItem(
            connection({ connected: true, isDefault: true }), resolveIcon,
        ).contextValue).toBe("mariadbConnection.connected.default");
    });

    it("marks a connection the MCP list holds", () => {
        // The difference the checkbox makes is worth seeing at a glance:
        // every MCP client on this machine can open this one, not just the
        // extension.
        const item = createTreeItem(
            connection({ connectionKind: "mcp" }), resolveIcon);

        expect(item.description).toBe("MCP");
        expect(item.tooltip).toContain("MCP access allowed");
    });

    it("leaves one of the extension's own unmarked", () => {
        const item = createTreeItem(
            connection({ connectionKind: "gui" }), resolveIcon);

        expect(item.description).toBeUndefined();
        expect(item.tooltip).toBe("dba@localhost:3310");
    });

    it("shows both marks when a connection is MCP and the default", () => {
        const item = createTreeItem(
            connection({ connectionKind: "mcp", isDefault: true }),
            resolveIcon,
        );

        expect(item.description).toBe("MCP, default");
        expect(item.tooltip)
            .toBe("dba@localhost:3310 (MCP access allowed, default connection)");
    });

    it("keeps the list out of the context value", () => {
        // The `when` clauses anchor on the third segment (`/notDefault$/`),
        // so a fourth would break the menus. Edit and delete are handed the
        // node itself, which carries the kind.
        for (const connectionKind of ["mcp", "gui"] as const) {
            expect(createTreeItem(connection({ connectionKind }), resolveIcon)
                .contextValue).toBe("mariadbConnection.disconnected.notDefault");
        }
    });

    it("marks the default connection in the tree", () => {
        const item = createTreeItem(
            connection({ isDefault: true }), resolveIcon);

        expect(item.description).toBe("default");
        expect(item.tooltip).toContain("default connection");
        expect(item.iconPath).toEqual({
            light: Uri.file("/ext/images/light/connectionMariaDBDefault.svg"),
            dark: Uri.file("/ext/images/dark/connectionMariaDBDefault.svg"),
        });
    });

    it("gives every scheme's default its own icon", () => {
        for (const [scheme, icon] of [
            ["mariadb", "connectionMariaDBDefault.svg"],
            ["mariadb+ssh", "connectionMariaDBSSHDefault.svg"],
            ["mysql", "connectionMySQLDefault.svg"],
            ["mysql+ssh", "connectionMySQLSSHDefault.svg"],
            ["mysqlx", "connectionMySQLDefault.svg"],
        ]) {
            const item = createTreeItem(connection({
                uri: `${scheme}://dba@localhost:3310`,
                isDefault: true,
            }), resolveIcon);

            expect(item.iconPath).toEqual({
                light: Uri.file(`/ext/images/light/${icon}`),
                dark: Uri.file(`/ext/images/dark/${icon}`),
            });
        }
    });

    it("ships every icon a connection can be shown with", () => {
        const schemes = ["mariadb", "mariadb+ssh", "mysql", "mysql+ssh",
            "mysqlx", "unknown"];
        for (const scheme of schemes) {
            for (const isDefault of [false, true]) {
                const file = connectionIconFor(
                    `${scheme}://dba@h`, isDefault);
                for (const theme of ["light", "dark"]) {
                    expect(existsSync(join(EXTENSION_ROOT, "images", theme,
                        file)), `${theme}/${file}`).toBe(true);
                }
            }
        }
    });

    it("leaves a non-default connection undecorated", () => {
        expect(createTreeItem(connection(), resolveIcon).description)
            .toBeUndefined();
    });
});

describe("createTreeItem for a schema", () => {
    it("leaves a user schema undecorated, it being nearly every row", () => {
        const item = createTreeItem({
            kind: "schema",
            uri: "dba@h",
            schema: "world",
            schemaType: "User Schema",
            comment: "the sample",
        }, resolveIcon);

        expect(item.label).toBe("world");
        expect(item.description).toBeUndefined();
        // The type is still there to be read, just not down the tree.
        expect(item.tooltip).toBe("the sample");
        expect(item.contextValue).toBe("mariadbSchema");
        expect(item.collapsibleState)
            .toBe(TreeItemCollapsibleState.Collapsed);
    });

    it.each([
        "System Schema",
        "System Information Schema",
    ])("says of %s what it is", (schemaType) => {
        const item = createTreeItem({
            kind: "schema",
            uri: "dba@h",
            schema: "mysql",
            schemaType,
            comment: "",
        }, resolveIcon);

        expect(item.description).toBe(schemaType);
        expect(item.tooltip).toBe(`mysql (${schemaType})`);
    });
});

describe("createTreeItem for an object group", () => {
    it.each([
        ["table", "Tables", "schemaTables.svg"],
        ["view", "Views", "schemaViews.svg"],
        ["function", "Functions", "schemaFunctions.svg"],
        ["procedure", "Procedures", "schemaProcedures.svg"],
        ["trigger", "Triggers", "schemaTableTriggers.svg"],
        ["event", "Events", "schemaEvents.svg"],
    ] as const)("shows %s as %s", (objectType, label, icon) => {
        const item = createTreeItem({
            kind: "objectGroup",
            uri: "dba@h",
            schema: "world",
            objectType,
        }, resolveIcon);

        expect(item.label).toBe(label);
        expect(item.contextValue).toBe(`mariadbObjectGroup.${objectType}`);
        expect(item.iconPath).toEqual({
            light: Uri.file(`/ext/images/light/${icon}`),
            dark: Uri.file(`/ext/images/dark/${icon}`),
        });
    });

    it("falls back to a codicon where there is no icon", () => {
        // The upstream icon set has no sequence icon.
        const item = createTreeItem({
            kind: "objectGroup",
            uri: "dba@h",
            schema: "world",
            objectType: "sequence",
        }, resolveIcon);

        expect(item.iconPath).toBeInstanceOf(ThemeIcon);
    });
});

describe("createTreeItem for an object", () => {
    const object = (overrides: Partial<IObjectNode> = {}): IObjectNode => {
        return {
            kind: "object",
            uri: "dba@h",
            schema: "world",
            objectType: "table",
            name: "city",
            ...overrides,
        };
    };

    it("is a leaf", () => {
        expect(createTreeItem(object(), resolveIcon).collapsibleState)
            .toBe(TreeItemCollapsibleState.None);
    });

    it("shows the object name with its type's icon", () => {
        const item = createTreeItem(object(), resolveIcon);

        expect(item.label).toBe("city");
        expect(item.contextValue).toBe("mariadbObject.table");
        expect(item.iconPath).toEqual({
            light: Uri.file("/ext/images/light/schemaTable.svg"),
            dark: Uri.file("/ext/images/dark/schemaTable.svg"),
        });
    });

    it("shows a comment as the tooltip", () => {
        expect(createTreeItem(object({ comment: "Cities" }), resolveIcon)
            .tooltip).toBe("Cities");
        expect(createTreeItem(object({ comment: "" }), resolveIcon).tooltip)
            .toBeUndefined();
    });
});

describe("createTreeItem for a connection's status", () => {
    const parent = connection({ connected: false, expandable: true });

    it("spins while the connection opens", () => {
        const item = createTreeItem({
            kind: "connectionStatus",
            uri: parent.uri,
            parent,
            state: "connecting",
        }, resolveIcon);

        expect(item.label).toBe("Connecting...");
        expect(item.iconPath).toEqual(new ThemeIcon("loading~spin"));
        expect(item.collapsibleState).toBe(TreeItemCollapsibleState.None);
        expect(item.contextValue).toBe("mariadbConnectionStatus.connecting");
    });

    it("shows the reason's first line with the error icon", () => {
        const item = createTreeItem({
            kind: "connectionStatus",
            uri: parent.uri,
            parent,
            state: "failed",
            message: "  Access denied for user 'dba'  \nat line 2",
        }, resolveIcon);

        expect(item.label).toBe("Access denied for user 'dba'");
        expect(item.tooltip)
            .toBe("  Access denied for user 'dba'  \nat line 2");
        expect(item.iconPath).toEqual(
            new ThemeIcon("error", new ThemeColor("errorForeground")));
        // What puts the Retry button beside it.
        expect(item.contextValue).toBe("mariadbConnectionStatus.failed");
    });
});

describe("createTreeItem for a folder", () => {
    it("shows its name, open, with the folder icon", () => {
        const item = createTreeItem({
            kind: "folder", path: "/Sandboxes/note_app", name: "note_app",
            empty: false,
        }, resolveIcon);

        expect(item.label).toBe("note_app");
        expect(item.tooltip).toBe("/Sandboxes/note_app");
        expect(item.iconPath).toEqual(new ThemeIcon("folder-opened"));
        expect(item.collapsibleState).toBe(TreeItemCollapsibleState.Expanded);
        expect(item.contextValue).toBe("mariadbFolder");
        // Unique, so VS Code tells folders apart by it across refreshes.
        expect(item.id).toBe("folder:/Sandboxes/note_app");
    });

    it("shows a closed folder closed", () => {
        const item = new FolderTreeItem({
            kind: "folder", path: "/Sandboxes", name: "Sandboxes", empty: false,
        }, false);

        expect(item.iconPath).toEqual(new ThemeIcon("folder"));
        expect(item.collapsibleState)
            .toBe(TreeItemCollapsibleState.Collapsed);
    });

    it("says it is empty, which is what offers Remove Folder", () => {
        const item = createTreeItem({
            kind: "folder", path: "/New", name: "New", empty: true,
        }, resolveIcon);

        expect(item.contextValue).toBe("mariadbFolder.empty");
    });
});
