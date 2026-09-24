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

import * as vscode from "vscode";

import { connectionLabel, schemeOf } from "../connections/connectionUri.js";
import type { ObjectType } from "../mcp/types.js";
import {
    OBJECT_GROUP_LABELS,
    type ConnectionsNode,
    type IConnectionNode,
    type IObjectGroupNode,
    type IObjectNode,
    type ISchemaNode,
} from "./connectionsModel.js";

/** Resolves an icon name to the light and dark files to show for it. */
export type IconResolver = (name: string) => {
    light: vscode.Uri;
    dark: vscode.Uri;
};

/**
 * Builds an icon resolver over the extension's `images` folder, which holds
 * a `light` and a `dark` variant of every icon.
 *
 * @param extensionUri The root of the installed extension.
 *
 * @returns The resolver.
 */
export const createIconResolver = (
    extensionUri: vscode.Uri,
): IconResolver => {
    return (name: string) => {
        return {
            light: vscode.Uri.joinPath(extensionUri, "images", "light", name),
            dark: vscode.Uri.joinPath(extensionUri, "images", "dark", name),
        };
    };
};

/** The icon shown for a group of objects of one type. */
const GROUP_ICONS: Record<ObjectType, string | vscode.ThemeIcon> = {
    table: "schemaTables.svg",
    view: "schemaViews.svg",
    function: "schemaFunctions.svg",
    procedure: "schemaProcedures.svg",
    // The upstream icon set has no sequence icon, so this one falls back to
    // a codicon rather than borrowing an unrelated picture.
    sequence: new vscode.ThemeIcon("symbol-numeric"),
    trigger: "schemaTableTriggers.svg",
    event: "schemaEvents.svg",
};

/** The icon shown for a single object. */
const OBJECT_ICONS: Record<ObjectType, string | vscode.ThemeIcon> = {
    table: "schemaTable.svg",
    view: "schemaView.svg",
    function: "schemaFunction.svg",
    procedure: "schemaProcedure.svg",
    sequence: new vscode.ThemeIcon("symbol-numeric"),
    trigger: "schemaTableTrigger.svg",
    event: "schemaEvent.svg",
};

/**
 * The icon shown for a connection, by the scheme of its URI. `mysqlx` has
 * no picture of its own; it is MySQL's protocol, so it borrows MySQL's.
 */
const CONNECTION_ICONS: Record<string, string> = {
    "mariadb": "connectionMariaDB.svg",
    "mariadb+ssh": "connectionMariaDBSSH.svg",
    "mysql": "connectionMySQL.svg",
    "mysql+ssh": "connectionMySQLSSH.svg",
    "mysqlx": "connectionMySQL.svg",
};

/**
 * The base of every item in the Connections tree, holding the node it
 * stands for and the icon lookup they all share.
 */
export class ConnectionBaseTreeItem<T extends ConnectionsNode>
    extends vscode.TreeItem {

    public constructor(
        public readonly node: T,
        label: string,
        icon: string | vscode.ThemeIcon,
        hasChildren: boolean,
        resolveIcon: IconResolver,
    ) {
        super(
            label,
            hasChildren
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None,
        );

        this.iconPath = typeof icon === "string" ? resolveIcon(icon) : icon;
    }
}

/**
 * A configured connection.
 *
 * Its context value carries both whether it is open and whether it is the
 * default, because that is what the context menu switches its entries on.
 * Which LIST the connection is in is deliberately not in there: the commands
 * that need it - edit and delete - are handed the node itself, and adding a
 * fourth segment would break the `when` clauses that anchor on the third
 * (`/notDefault$/` and `/\.default$/`).
 */
export class ConnectionTreeItem
    extends ConnectionBaseTreeItem<IConnectionNode> {

    public constructor(node: IConnectionNode, resolveIcon: IconResolver) {
        // The label leaves out what the icon and the tooltip already say -
        // the scheme and the options - so the row stays short.
        super(node, connectionLabel(node.uri),
            CONNECTION_ICONS[schemeOf(node.uri)] ?? "connectionMariaDB.svg",
            node.expandable, resolveIcon);

        this.contextValue = [
            "mariadbConnection",
            node.connected ? "connected" : "disconnected",
            node.isDefault ? "default" : "notDefault",
        ].join(".");

        // "MCP" marks a connection in the shared list, which every MCP client
        // on this machine can open - not just this extension. That is worth
        // seeing at a glance, since it is the difference the checkbox makes.
        const marks: string[] = [];
        if (node.connectionKind === "mcp") {
            marks.push("MCP");
        }
        if (node.isDefault) {
            marks.push("default");
        }

        this.description = marks.length > 0 ? marks.join(", ") : undefined;

        const notes: string[] = [];
        if (node.connectionKind === "mcp") {
            notes.push("MCP access allowed");
        }
        if (node.isDefault) {
            notes.push("default connection");
        }
        this.tooltip = notes.length > 0
            ? `${node.uri} (${notes.join(", ")})`
            : node.uri;
    }
}

/**
 * What `db.list_schemas` calls a schema that is none of the server's own.
 * It is what nearly every row is, so it is the one type left unsaid.
 */
const USER_SCHEMA = "User Schema";

/** A schema of an open connection. */
export class SchemaTreeItem extends ConnectionBaseTreeItem<ISchemaNode> {
    public override contextValue = "mariadbSchema";

    public constructor(node: ISchemaNode, resolveIcon: IconResolver) {
        super(node, node.schema, "schema.svg", true, resolveIcon);

        // Only a system schema says what it is: a column of "User Schema"
        // down the tree is what the user came for and tells them nothing.
        this.description = node.schemaType === USER_SCHEMA
            ? undefined
            : node.schemaType;
        this.tooltip = node.comment || `${node.schema} (${node.schemaType})`;
    }
}

/** The folder holding all objects of one type in a schema. */
export class ObjectGroupTreeItem
    extends ConnectionBaseTreeItem<IObjectGroupNode> {

    public constructor(node: IObjectGroupNode, resolveIcon: IconResolver) {
        super(node, OBJECT_GROUP_LABELS[node.objectType],
            GROUP_ICONS[node.objectType], true, resolveIcon);

        this.contextValue = `mariadbObjectGroup.${node.objectType}`;
    }
}

/** A single database object. */
export class ObjectTreeItem extends ConnectionBaseTreeItem<IObjectNode> {
    public constructor(node: IObjectNode, resolveIcon: IconResolver) {
        super(node, node.name, OBJECT_ICONS[node.objectType], false,
            resolveIcon);

        this.contextValue = `mariadbObject.${node.objectType}`;
        this.tooltip = node.comment || undefined;
    }
}

/**
 * Builds the tree item for a node.
 *
 * @param node The node to show.
 * @param resolveIcon The icon lookup to use.
 *
 * @returns The item to put into the tree.
 */
export const createTreeItem = (
    node: ConnectionsNode,
    resolveIcon: IconResolver,
): vscode.TreeItem => {
    switch (node.kind) {
        case "connection": {
            return new ConnectionTreeItem(node, resolveIcon);
        }

        case "schema": {
            return new SchemaTreeItem(node, resolveIcon);
        }

        case "objectGroup": {
            return new ObjectGroupTreeItem(node, resolveIcon);
        }

        default: {
            return new ObjectTreeItem(node, resolveIcon);
        }
    }
};
