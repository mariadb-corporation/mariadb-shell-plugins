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

import {
    UI_BACKEND_SESSION,
    type ConnectionManager,
} from "../connections/connectionManager.js";
import {
    ROOT_FOLDER,
    allFolders,
    folderNames,
} from "../connections/connectionFolders.js";
import type { IStoredConnection } from "../connections/connectionStore.js";
import { withDefaultScheme } from "../connections/connectionUri.js";
import { OBJECT_TYPES, type ConnectionKind, type ObjectType }
    from "../mcp/types.js";

/**
 * A folder of connections. Folders exist as the paths connections are filed
 * under, plus the ones the user made with New Folder and has not filled yet,
 * which the extension keeps for itself (see `FolderSet`).
 */
export interface IFolderNode {
    kind: "folder";
    /** The whole path, `/Sandboxes/note_app`. */
    path: string;
    /** The last name in it, which is what the row shows: `note_app`. */
    name: string;
    /**
     * Whether no connection is filed in it or below it - one the user made
     * with New Folder and has not moved anything into yet. Only such a
     * folder can be removed: a folder with connections in it is not a thing
     * of its own but where they are filed.
     */
    empty: boolean;
}

/** A connection, shown at the root of the tree or in its folder. */
export interface IConnectionNode {
    kind: "connection";
    uri: string;
    /** The folder it is filed in; `/` for the top level. */
    path?: string;
    connected: boolean;
    isDefault: boolean;
    /**
     * Which list it is configured in. Half of what identifies it, so editing
     * and deleting both need it - and it is what the "MCP" marker shows.
     */
    connectionKind: ConnectionKind;
    /**
     * Whether the row has a twistie. An open connection always has one;
     * a closed one only where opening it is what expanding the row does,
     * since a twistie that can never show anything is a dead end.
     */
    expandable: boolean;
}

/**
 * How the tree's attempt to open a connection is going, while it is not
 * open: `connecting` until the server answers, `failed` with the reason
 * once it has refused.
 */
export interface IOpenAttempt {
    state: "connecting" | "failed";
    /** Why it failed; set only when it did. */
    message?: string;
}

/**
 * The one row a connection shows while its opening is under way or has
 * failed, standing in for the schemas it does not have yet.
 */
export interface IConnectionStatusNode extends IOpenAttempt {
    kind: "connectionStatus";
    uri: string;
    /**
     * The row it sits under, as the tree holds it. A retry redraws that
     * row, and VS Code knows a node by identity, not by value.
     */
    parent: IConnectionNode;
}

/** A schema of an open connection. */
export interface ISchemaNode {
    kind: "schema";
    uri: string;
    schema: string;
    schemaType: string;
    comment: string;
}

/** The folder holding all objects of one type in a schema. */
export interface IObjectGroupNode {
    kind: "objectGroup";
    uri: string;
    schema: string;
    objectType: ObjectType;
}

/** One database object. */
export interface IObjectNode {
    kind: "object";
    uri: string;
    schema: string;
    objectType: ObjectType;
    name: string;
    comment?: string;
}

export type ConnectionsNode =
    | IFolderNode
    | IConnectionNode
    | IConnectionStatusNode
    | ISchemaNode
    | IObjectGroupNode
    | IObjectNode;

/** The caption each object group is shown under. */
export const OBJECT_GROUP_LABELS: Record<ObjectType, string> = {
    table: "Tables",
    view: "Views",
    function: "Functions",
    procedure: "Procedures",
    sequence: "Sequences",
    trigger: "Triggers",
    event: "Events",
};

/**
 * The contents of the Connections view, as plain data.
 *
 * Nothing here touches VS Code, so the whole shape of the tree - what has
 * children, what a node is called, what happens when a connection is not
 * open - is testable against a fake server.
 */
export class ConnectionsModel {
    /**
     * @param connections The open connections and the default one.
     * @param connectOnOpen Whether expanding a closed connection opens it.
     *   Read on every call, because the setting behind it can change while
     *   the tree is up.
     * @param openAttempt How the tree's attempt to open a connection is
     *   going, by URI; undefined where none is under way or failed.
     * @param customFolders The folders the user made that the connections
     *   may not imply yet. Read on every call.
     */
    public constructor(
        private readonly connections: ConnectionManager,
        private readonly connectOnOpen: () => boolean,
        private readonly openAttempt:
            (uri: string) => IOpenAttempt | undefined = () => {
                return undefined;
            },
        private readonly customFolders: () => string[] = () => { return []; },
    ) { }

    /**
     * The connections as last listed for the roots. A folder's children come
     * from the same listing, so a folder and the roots it sits in cannot
     * disagree - and opening a folder costs no call to the server.
     */
    #listing?: Promise<IStoredConnection[]>;

    /**
     * The root of the tree: the top-level folders, then the connections filed
     * at the top level.
     *
     * @returns The root nodes.
     */
    public async getRoots(): Promise<Array<IFolderNode | IConnectionNode>> {
        this.#listing = this.connections.listStoredConnections();

        return this.#contentsOf(ROOT_FOLDER, await this.#listing);
    }

    /**
     * What one folder holds: its subfolders, sorted by name, then the
     * connections filed directly in it, in listing order.
     *
     * @param folder The folder; `/` for the top level.
     * @param stored Every configured connection.
     *
     * @returns Its nodes.
     */
    #contentsOf(
        folder: string,
        stored: IStoredConnection[],
    ): Array<IFolderNode | IConnectionNode> {
        const depth = folderNames(folder).length;
        const filedIn = stored.map((connection) => {
            return connection.path ?? ROOT_FOLDER;
        });
        const subfolders = allFolders([
            ...filedIn,
            ...this.customFolders(),
        ]).filter((path) => {
            const names = folderNames(path);

            return names.length === depth + 1
                && (depth === 0 || path.startsWith(`${folder}/`));
        }).map((path): IFolderNode => {
            return {
                kind: "folder",
                path,
                name: folderNames(path).at(-1)!,
                empty: !filedIn.some((filed) => {
                    return filed === path || filed.startsWith(`${path}/`);
                }),
            };
        });

        const filed = stored.filter((connection) => {
            return (connection.path ?? ROOT_FOLDER) === folder;
        });

        return [...subfolders, ...this.#connectionNodes(filed)];
    }

    /**
     * Turns configured connections into their rows.
     *
     * @param stored The connections.
     *
     * @returns The connection nodes.
     */
    #connectionNodes(stored: IStoredConnection[]): IConnectionNode[] {
        // The setting may have been written before connections carried their
        // scheme, and what is listed now always does. Both sides go through
        // the same fill-in, or the default connection loses its marker.
        const defaultUri = this.connections.defaultConnection === undefined
            ? undefined
            : withDefaultScheme(this.connections.defaultConnection);

        return stored.map((connection) => {
            // Any connection open on it, not only the one the tree
            // browses with: what the row says, and what disconnecting it
            // closes, is the whole of what is open on the connection.
            const connected = this.connections.isConnected(connection.uri);

            return {
                kind: "connection",
                uri: connection.uri,
                path: connection.path ?? ROOT_FOLDER,
                connected,
                isDefault: withDefaultScheme(connection.uri) === defaultUri,
                connectionKind: connection.kind,
                expandable: connected || this.connectOnOpen(),
            };
        });
    }

    /**
     * The children of a node.
     *
     * A connection that is not open has no children. Opening it is not
     * this method's job even where expanding the row is what opens it:
     * the tree asks a node for its children again on every refresh, and a
     * closed connection that answers by opening itself could never be
     * disconnected - the refresh that follows would open it straight back
     * up. Only an expand the user performed opens a connection, which is
     * why that lives on the tree provider.
     *
     * @param node The node to expand.
     *
     * @returns Its children, empty when it has none.
     */
    public async getChildren(
        node: ConnectionsNode,
    ): Promise<ConnectionsNode[]> {
        switch (node.kind) {
            case "folder": {
                this.#listing ??= this.connections.listStoredConnections();

                return this.#contentsOf(node.path, await this.#listing);
            }

            case "connection": {
                return await this.#schemasOf(node);
            }

            case "schema": {
                return this.#groupsOf(node);
            }

            case "objectGroup": {
                return await this.#objectsOf(node);
            }

            default: {
                return [];
            }
        }
    }

    /**
     * Lists the schemas of an open connection.
     *
     * @param node The connection node.
     *
     * @returns Its schema nodes, or the status of opening it while it is
     *          not open.
     */
    async #schemasOf(
        node: IConnectionNode,
    ): Promise<Array<ISchemaNode | IConnectionStatusNode>> {
        const connectionId = this.connections.connectionIdFor(
            node.uri, UI_BACKEND_SESSION);
        if (connectionId === undefined) {
            // Not open yet: an attempt under way, or one that failed, is
            // shown in place of the schemas - an expanded row with nothing
            // under it looks like nothing is happening.
            const attempt = this.openAttempt(node.uri);

            return attempt === undefined
                ? []
                : [{
                    kind: "connectionStatus",
                    uri: node.uri,
                    parent: node,
                    ...attempt,
                }];
        }

        const api = await this.connections.api();
        const schemas = await api.listSchemas(connectionId);

        return schemas.map((schema) => {
            return {
                kind: "schema",
                uri: node.uri,
                schema: schema.schema_name,
                schemaType: schema.schema_type,
                comment: schema.schema_comment,
            };
        });
    }

    /**
     * The object type folders of a schema. They are listed unconditionally,
     * rather than only where the schema holds objects of that type, so that
     * expanding a schema costs no queries at all.
     *
     * @param node The schema node.
     *
     * @returns One group node per object type.
     */
    #groupsOf(node: ISchemaNode): IObjectGroupNode[] {
        return OBJECT_TYPES.map((objectType) => {
            return {
                kind: "objectGroup",
                uri: node.uri,
                schema: node.schema,
                objectType,
            };
        });
    }

    /**
     * Lists the objects of one group.
     *
     * @param node The group node.
     *
     * @returns Its object nodes.
     */
    async #objectsOf(node: IObjectGroupNode): Promise<IObjectNode[]> {
        const connectionId = this.connections.connectionIdFor(
            node.uri, UI_BACKEND_SESSION);
        if (connectionId === undefined) {
            return [];
        }

        const api = await this.connections.api();
        const objects = await api.listObjects(
            connectionId,
            node.schema,
            node.objectType,
        );

        return objects.map((object) => {
            return {
                kind: "object",
                uri: node.uri,
                schema: node.schema,
                objectType: node.objectType,
                name: object.name,
                comment: object.comment,
            };
        });
    }

}
