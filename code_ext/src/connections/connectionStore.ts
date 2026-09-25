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
    ALL_CONNECTION_KINDS,
    type ConnectionKind,
    type IMariaDbApi,
} from "../mcp/types.js";
import {
    ROOT_FOLDER,
    folderProblem,
    normalizeFolder,
} from "./connectionFolders.js";
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
 * * **The folder is not part of the identity.** It is where the connection
 *   is filed and nothing more, so moving one only sends the folder, and one
 *   left at the top level sends none - which keeps a server that predates
 *   folders working for everything else.
 * * **Saving does not verify.** The editor has a Test Connection button for
 *   that, as the MySQL Shell's editor does; a server that happens to be down
 *   must not stop its connection being configured.
 */

/** Which of the two lists a connection belongs to. */
export const MCP_KIND: ConnectionKind = "mcp";
export const GUI_KIND: ConnectionKind = "gui";

/**
 * A connection as the extension knows it: its URI, which list it is in and
 * the folder it is filed in.
 */
export interface IStoredConnection {
    uri: string;
    kind: ConnectionKind;
    /**
     * `/` for the top level, otherwise `/Folder/Subfolder`. Optional where a
     * connection is only being identified - editing and deleting go by URI and
     * kind - and read as the top level when left out.
     */
    path?: string;
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
    /** The folder to file it in, as typed; empty or `/` is the top level. */
    path?: string;
    /** The connection being edited, or undefined when adding a new one. */
    original?: IStoredConnection;
}

/** What a save answers with. */
export interface ISaveResult {
    /** The URI it is configured under, as the server normalized it. */
    uri?: string;
    kind?: ConnectionKind;
    /** The folder it is filed in. */
    path?: string;
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
 * In one call, `kind: "all"`, whose entries each name the list they are in:
 * the kind is half of what identifies a connection, and the tree, the editor
 * and the delete all need it. A server that predates `"all"` refuses it -
 * every shell released before it - and is asked twice instead, once per
 * list, which is how it was always done.
 *
 * @param api The database API.
 *
 * @returns The connections, MCP ones first, each with the list it is in and
 *          its folder.
 */
export const listConnections = async (
    api: IMariaDbApi,
): Promise<IStoredConnection[]> => {
    try {
        const all = await api.listConnectionEntries(ALL_CONNECTION_KINDS);
        // Every entry has to say which list it is in, or it cannot be
        // edited or deleted - one that does not came from a server that
        // did not understand the question.
        if (all.every((entry) => { return entry.kind !== undefined; })) {
            return all.map((entry): IStoredConnection => {
                return {
                    uri: entry.uri,
                    kind: entry.kind!,
                    path: normalizeFolder(entry.path),
                };
            });
        }
    } catch {
        // Refused as an unknown kind by a server that predates it; the two
        // calls below are what such a server answers, and if they fail too,
        // that is the error worth reporting.
    }

    const [mcp, gui] = await Promise.all([
        api.listConnectionEntries(MCP_KIND),
        api.listConnectionEntries(GUI_KIND),
    ]);

    return [
        ...mcp.map((entry): IStoredConnection => {
            return {
                uri: entry.uri, kind: MCP_KIND, path: normalizeFolder(entry.path),
            };
        }),
        ...gui.map((entry): IStoredConnection => {
            return {
                uri: entry.uri, kind: GUI_KIND, path: normalizeFolder(entry.path),
            };
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

    const problem = folderProblem(request.path ?? "");
    if (problem !== undefined) {
        return { error: problem };
    }

    const uri = built.uri!;
    const kind = kindFor(request.mcpAccess);
    const path = normalizeFolder(request.path ?? "");

    if (request.original === undefined) {
        // A new connection has no stored password to fall back on, so an
        // omitted one is an empty one - which is what a server with no
        // password for that account wants.
        //
        // `verify` is false on purpose: Test Connection is a button of its
        // own, and a server that is down must not block configuring it.
        const stored = await api.addConnection(
            uri, request.password ?? "", kind, false,
            path === ROOT_FOLDER ? undefined : path,
        );

        return { uri: stored, kind, path };
    }

    const stored = await api.updateConnection(
        request.original.uri,
        uri === request.original.uri ? undefined : uri,
        request.original.kind,
        kind === request.original.kind ? undefined : kind,
        request.password,
        path === normalizeFolder(request.original.path ?? ROOT_FOLDER)
            ? undefined
            : path,
    );

    return { uri: stored, kind, path };
};

/**
 * Files connections in a folder, keeping everything else about them.
 *
 * What a drop in the Connections view does. Each is moved with its own
 * `db.update_connection`, so a failure part way leaves the ones before it
 * moved and the rest where they were - never a connection in neither place.
 * One already in the folder is left alone.
 *
 * @param api The database API.
 * @param connections The connections to move, with the list and folder
 *                    each is in now.
 * @param path The folder to file them in; `/` is the top level.
 *
 * @returns The connections that were moved.
 */
export const moveConnections = async (
    api: IMariaDbApi,
    connections: IStoredConnection[],
    path: string,
): Promise<IStoredConnection[]> => {
    return await fileConnections(api, connections.map((connection) => {
        return { connection, path };
    }));
};

/** One connection and the folder it is to be filed in. */
export interface IFiling {
    connection: IStoredConnection;
    /** The folder; `/` is the top level. */
    path: string;
}

/**
 * Files each connection in its own folder - what moving a folder does to
 * the connections in and below it. Otherwise as `moveConnections`: one
 * update each, those already in place left alone.
 *
 * @param api The database API.
 * @param filings Each connection with the folder it goes in.
 *
 * @returns The connections that were moved, each with its new folder.
 */
export const fileConnections = async (
    api: IMariaDbApi,
    filings: IFiling[],
): Promise<IStoredConnection[]> => {
    const moved: IStoredConnection[] = [];

    for (const { connection, path } of filings) {
        const folder = normalizeFolder(path);
        if (normalizeFolder(connection.path ?? ROOT_FOLDER) === folder) {
            continue;
        }

        await api.updateConnection(
            connection.uri, undefined, connection.kind, undefined, undefined,
            folder,
        );
        moved.push({ ...connection, path: folder });
    }

    return moved;
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
 * @returns Its fields, whether MCP clients may open it and its folder.
 */
export const fieldsOf = (
    connection: IStoredConnection,
): { fields: IConnectionFields; mcpAccess: boolean; path: string } => {
    return {
        fields: parseConnectionUri(connection.uri),
        mcpAccess: connection.kind === MCP_KIND,
        path: normalizeFolder(connection.path ?? ROOT_FOLDER),
    };
};
