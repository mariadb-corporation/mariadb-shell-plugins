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

import type { ConnectionManager } from "../connections/connectionManager.js";
import { OBJECT_TYPES, type ConnectionKind, type ObjectType }
    from "../mcp/types.js";

/** A connection, shown at the root of the tree. */
export interface IConnectionNode {
    kind: "connection";
    uri: string;
    connected: boolean;
    isDefault: boolean;
    /**
     * Which list it is configured in. Half of what identifies it, so editing
     * and deleting both need it - and it is what the "MCP" marker shows.
     */
    connectionKind: ConnectionKind;
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
    | IConnectionNode
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
    public constructor(private readonly connections: ConnectionManager) { }

    /**
     * The root of the tree: one node per configured connection.
     *
     * @returns The connection nodes.
     */
    public async getRoots(): Promise<IConnectionNode[]> {
        const stored = await this.connections.listStoredConnections();
        const defaultUri = this.connections.defaultConnection;

        return stored.map((connection) => {
            return {
                kind: "connection",
                uri: connection.uri,
                connected: this.connections.isConnected(connection.uri),
                isDefault: connection.uri === defaultUri,
                connectionKind: connection.kind,
            };
        });
    }

    /**
     * The children of a node.
     *
     * A connection that is not open has no children rather than opening
     * itself: expanding a node in a tree should not make a network
     * connection the user did not ask for.
     *
     * @param node The node to expand.
     *
     * @returns Its children, empty when it has none.
     */
    public async getChildren(
        node: ConnectionsNode,
    ): Promise<ConnectionsNode[]> {
        switch (node.kind) {
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
     * @returns Its schema nodes.
     */
    async #schemasOf(node: IConnectionNode): Promise<ISchemaNode[]> {
        const connectionId = this.connections.connectionIdFor(node.uri);
        if (connectionId === undefined) {
            return [];
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
        const connectionId = this.connections.connectionIdFor(node.uri);
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
