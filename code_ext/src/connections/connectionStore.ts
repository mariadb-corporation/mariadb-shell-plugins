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

import type { ConnectionKind, IMariaDbApi } from "../mcp/types.js";
import {
    buildConnectionUri,
    parseConnectionUri,
    type IConnectionFields,
} from "./connectionUri.js";

/**
 * Adding, changing and removing configured connections.
 *
 * This is the whole of what the connection editor does once the user presses
 * a button, kept away from the webview so that it can be exercised without
 * one. The rules it encodes are not obvious from the tool names:
 *
 * * **Which list a connection is in is a property of the connection**, not of
 *   the call. The "Allow MCP access" checkbox picks between the shared `mcp`
 *   list, which every MCP client on this machine can open, and the `gui` list
 *   that belongs to this extension - and toggling it on a connection that
 *   already exists has to MOVE it rather than copy it.
 * * **A connection is keyed by its URI**, so editing the host, the port or
 *   the user is a re-key, not an update in place. `db.update_connection` does
 *   that server-side precisely so the password does not have to come back out
 *   to be re-stored - nothing can read a stored password, by design.
 * * **Saving does not verify.** The editor has a Test Connection button for
 *   that, as the MySQL Shell's editor does; a server that happens to be down
 *   must not stop its connection being configured.
 */

/** Which of the two lists a connection belongs to. */
export const MCP_KIND: ConnectionKind = "mcp";
export const GUI_KIND: ConnectionKind = "gui";

/** A connection as the extension knows it: its URI and which list it is in. */
export interface IStoredConnection {
    uri: string;
    kind: ConnectionKind;
}

/** What the editor asks to be saved. */
export interface ISaveRequest {
    fields: IConnectionFields;
    /**
     * A new password, or undefined to keep the stored one. Undefined is only
     * valid when editing: a new connection has no stored password to keep.
     */
    password?: string;
    /** Whether the connection goes in the shared MCP list. */
    mcpAccess: boolean;
    /** The connection being edited, or undefined when adding a new one. */
    original?: IStoredConnection;
}

/** What a save answers with. */
export interface ISaveResult {
    /** The URI it is configured under, as the server normalized it. */
    uri?: string;
    kind?: ConnectionKind;
    /** Set instead when the save could not be made. */
    error?: string;
}

/**
 * The list a connection belongs in, given the MCP access checkbox.
 *
 * @param mcpAccess Whether MCP clients may open it.
 *
 * @returns The connection kind.
 */
export const kindFor = (mcpAccess: boolean): ConnectionKind => {
    return mcpAccess ? MCP_KIND : GUI_KIND;
};

/**
 * Lists every configured connection, from both lists.
 *
 * The two are asked for separately because `db.list_connections` reports one
 * kind per call, and the kind has to survive into the result: it is half of
 * what identifies a connection, and the tree, the editor and the delete all
 * need it.
 *
 * @param api The database API.
 *
 * @returns The connections, MCP ones first, each with the list it is in.
 */
export const listConnections = async (
    api: IMariaDbApi,
): Promise<IStoredConnection[]> => {
    const [mcp, gui] = await Promise.all([
        api.listConnections(MCP_KIND),
        api.listConnections(GUI_KIND),
    ]);

    return [
        ...mcp.map((uri): IStoredConnection => {
            return { uri, kind: MCP_KIND };
        }),
        ...gui.map((uri): IStoredConnection => {
            return { uri, kind: GUI_KIND };
        }),
    ];
};

/**
 * Tries a connection's credentials without storing anything.
 *
 * @param api The database API.
 * @param fields The editor's fields.
 * @param password The password to try, or undefined to use the stored
 *                 one - the server reads it, nothing here ever sees it.
 *
 * @returns The server's confirmation message.
 *
 * @throws When the fields name no connection, or the server could not open
 *         one - the shell's own reason is the useful part and is passed on.
 */
export const testConnection = async (
    api: IMariaDbApi,
    fields: IConnectionFields,
    password?: string,
): Promise<string> => {
    const built = buildConnectionUri(fields);
    if (built.error !== undefined) {
        throw new Error(built.error);
    }

    return await api.testConnection(built.uri!, password);
};

/**
 * Saves a connection, adding it or changing the one being edited.
 *
 * @param api The database API.
 * @param request What to save.
 *
 * @returns Where it ended up, or the reason it could not be saved.
 */
export const saveConnection = async (
    api: IMariaDbApi,
    request: ISaveRequest,
): Promise<ISaveResult> => {
    const built = buildConnectionUri(request.fields);
    if (built.error !== undefined) {
        return { error: built.error };
    }

    const uri = built.uri!;
    const kind = kindFor(request.mcpAccess);

    if (request.original === undefined) {
        // A new connection has no stored password to fall back on, so an
        // omitted one is an empty one - which is what a server with no
        // password for that account wants.
        //
        // `verify` is false on purpose: Test Connection is a button of its
        // own, and a server that is down must not block configuring it.
        const stored = await api.addConnection(
            uri, request.password ?? "", kind, false,
        );

        return { uri: stored, kind };
    }

    const stored = await api.updateConnection(
        request.original.uri,
        uri === request.original.uri ? undefined : uri,
        request.original.kind,
        kind === request.original.kind ? undefined : kind,
        request.password,
    );

    return { uri: stored, kind };
};

/**
 * Removes a configured connection.
 *
 * @param api The database API.
 * @param connection The connection to remove, with the list it is in.
 *
 * @returns The URI that was removed.
 */
export const deleteConnection = async (
    api: IMariaDbApi,
    connection: IStoredConnection,
): Promise<string> => {
    return await api.deleteConnection(connection.uri, connection.kind);
};

/**
 * The editor's starting state for a connection that already exists.
 *
 * @param connection The connection to edit.
 *
 * @returns Its fields and whether MCP clients may open it.
 */
export const fieldsOf = (
    connection: IStoredConnection,
): { fields: IConnectionFields; mcpAccess: boolean } => {
    return {
        fields: parseConnectionUri(connection.uri),
        mcpAccess: connection.kind === MCP_KIND,
    };
};
