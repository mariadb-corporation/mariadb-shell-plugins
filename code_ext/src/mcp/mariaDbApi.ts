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
    decodeList,
    decodeObject,
    decodeScalar,
    decodeVoid,
    type IToolResult,
} from "./protocol.js";
import type {
    ConnectionKind,
    IMariaDbApi,
    IObjectDetails,
    IObjectInfo,
    ISchemaInfo,
    IStatementResult,
    ObjectType,
} from "./types.js";

/**
 * Calls one MCP tool. This is the whole of what the API needs from a
 * transport, which keeps the SDK out of everything below it.
 */
export interface IToolCaller {
    callTool(
        name: string,
        args: Record<string, unknown>,
    ): Promise<IToolResult>;
}

/**
 * The `db.*` tools of the MariaDB MCP server, as typed calls.
 */
export class MariaDbApi implements IMariaDbApi {
    public constructor(private readonly caller: IToolCaller) { }

    /**
     * Lists the configured connection URIs of one kind.
     *
     * @param kind Which list to read. Left out entirely when not given, so a
     *             server that predates the two lists - or one not started
     *             with `--gui` - is not handed an argument it does not know.
     *             Either way it answers with the shared `mcp` list.
     *
     * @returns One URI per configured connection of that kind.
     */
    public async listConnections(kind?: ConnectionKind): Promise<string[]> {
        const name = "db.list_connections";

        return decodeList<string>(
            name,
            await this.caller.callTool(name, kind === undefined ? {} : { kind }),
        );
    }

    /**
     * Stores a connection and its password, and reports the spelling it was
     * stored under.
     *
     * Served only by a server started with `--gui`. Storing a connection that
     * is already in that list replaces its password, which is how a password
     * entered wrongly is corrected.
     *
     * @param uri The connection to store. It must not carry a password: the
     *            server strips one while normalizing and refuses rather than
     *            storing a connection without one.
     * @param password The password to store, in the OS secret store.
     * @param kind Which list to store it in. Defaults to the server's own
     *             default, the shared `mcp` list.
     * @param verify Whether the server opens a session with the credentials
     *               first and stores them only if that works. Defaults to the
     *               server's own default, which is to verify.
     *
     * @returns The normalized URI the connection was stored under, which is
     *          the spelling `listConnections` then reports.
     */
    public async addConnection(
        uri: string,
        password: string,
        kind?: ConnectionKind,
        verify?: boolean,
    ): Promise<string> {
        const name = "db.add_connection";

        return decodeScalar(name, await this.caller.callTool(name, {
            uri,
            password,
            ...(kind === undefined ? {} : { kind }),
            ...(verify === undefined ? {} : { verify }),
        }));
    }

    /**
     * Deletes a stored connection and its password, closing whatever is open
     * on it.
     *
     * Served only by a server started with `--gui`. The connection is deleted
     * from the named list alone, even where the other list holds the same URI.
     *
     * @param uri The connection to delete. It need not be spelled exactly as
     *            `listConnections` reports it, only name the same connection.
     * @param kind Which list to delete it from. Defaults to the server's own
     *             default, the shared `mcp` list.
     *
     * @returns The URI that was deleted, as it was stored.
     */
    public async deleteConnection(
        uri: string,
        kind?: ConnectionKind,
    ): Promise<string> {
        const name = "db.delete_connection";

        return decodeScalar(name, await this.caller.callTool(name, {
            uri,
            ...(kind === undefined ? {} : { kind }),
        }));
    }

    /**
     * Re-keys a configured connection, keeping its password.
     *
     * Served only by a server started with `--gui`. A connection is keyed by
     * its URI and by which list it is in, so changing the host, the user or
     * the MCP checkbox means a new key - and `addConnection` would need the
     * password for that, which nothing can read back. This moves the stored
     * secret without it passing through the extension.
     *
     * @param uri The connection as it is configured now.
     * @param newUri The URI to move it to, or undefined to keep it.
     * @param kind The list it is in now.
     * @param newKind The list to move it to, or undefined to keep it.
     * @param password A new password, or undefined to keep the stored one.
     *
     * @returns The URI the connection is now configured under.
     */
    public async updateConnection(
        uri: string,
        newUri?: string,
        kind?: ConnectionKind,
        newKind?: ConnectionKind,
        password?: string,
    ): Promise<string> {
        const name = "db.update_connection";

        return decodeScalar(name, await this.caller.callTool(name, {
            uri,
            // Each one left out when not given, so the server keeps what it
            // has rather than being told to set it to nothing.
            ...(newUri === undefined ? {} : { new_uri: newUri }),
            ...(kind === undefined ? {} : { kind }),
            ...(newKind === undefined ? {} : { new_kind: newKind }),
            ...(password === undefined ? {} : { password }),
        }));
    }

    /**
     * Checks that a URI and password open a session, storing nothing.
     *
     * Served only by a server started with `--gui`. This is what the
     * editor's Test button needs: `connect` only opens connections that are
     * already configured, and `addConnection` stores on success, so neither
     * can answer the question before the connection exists.
     *
     * @param uri The connection to try. As with `addConnection` it must not
     *            carry a password.
     * @param password The password to try, or undefined to use the one
     *                 already stored - which is how an existing connection is
     *                 tested without the user retyping it. The URI must then
     *                 name a configured connection.
     *
     * @returns The server's confirmation message. It rejects instead when
     *          the connection could not be opened, carrying the shell's own
     *          reason - which is the useful half of the answer.
     */
    public async testConnection(
        uri: string,
        password?: string,
    ): Promise<string> {
        const name = "db.test_connection";

        return decodeScalar(name, await this.caller.callTool(name, {
            uri,
            ...(password === undefined ? {} : { password }),
        }));
    }

    /**
     * Opens one of the configured connections.
     *
     * @param uri The connection to open.
     *
     * @returns The UUID identifying the open connection.
     */
    public async connect(uri: string): Promise<string> {
        const name = "db.connect";

        return decodeScalar(name, await this.caller.callTool(name, { uri }));
    }

    /**
     * Closes an open connection.
     *
     * @param connectionId The UUID returned by `connect`.
     *
     * @returns Nothing.
     */
    public async close(connectionId: string): Promise<void> {
        const name = "db.close";
        decodeVoid(
            name,
            await this.caller.callTool(name, { connection_id: connectionId }),
        );
    }

    /**
     * Lists the schemas reachable on an open connection.
     *
     * @param connectionId The UUID returned by `connect`.
     *
     * @returns One entry per schema.
     */
    public async listSchemas(connectionId: string): Promise<ISchemaInfo[]> {
        const name = "db.list_schemas";

        return decodeList<ISchemaInfo>(
            name,
            await this.caller.callTool(name, { connection_id: connectionId }),
        );
    }

    /**
     * Lists the objects of one type in a schema.
     *
     * @param connectionId The UUID returned by `connect`.
     * @param schemaName The schema to look in.
     * @param objectType The type of object to list.
     *
     * @returns One entry per object.
     */
    public async listObjects(
        connectionId: string,
        schemaName: string,
        objectType: ObjectType,
    ): Promise<IObjectInfo[]> {
        const name = "db.list_objects";

        return decodeList<IObjectInfo>(name, await this.caller.callTool(name, {
            connection_id: connectionId,
            schema_name: schemaName,
            object_type: objectType,
        }));
    }

    /**
     * Describes one object in detail. The result panel uses this to learn a
     * table's columns and primary key, which is what makes a result set
     * editable.
     *
     * @param connectionId The UUID returned by `connect`.
     * @param schemaName The schema holding the object.
     * @param objectName The object to describe.
     * @param objectType The type of the object.
     *
     * @returns The object's description.
     */
    public async getObjectDetails(
        connectionId: string,
        schemaName: string,
        objectName: string,
        objectType: ObjectType,
    ): Promise<IObjectDetails> {
        const name = "db.get_object_details";

        return decodeObject<IObjectDetails>(
            name,
            await this.caller.callTool(name, {
                connection_id: connectionId,
                schema_name: schemaName,
                object_name: objectName,
                object_type: objectType,
            }),
        );
    }

    /**
     * Runs a multi-statement SQL script.
     *
     * A failing statement does not raise: its entry carries `error`
     * instead of a result set, and the script stops there unless
     * `stopOnError` is false. Only a failure of the call itself - a
     * closed connection, say - rejects.
     *
     * @param connectionId The UUID returned by `connect`.
     * @param sqlScript One or more semicolon separated statements.
     * @param stopOnError Whether a failing statement ends the script.
     *                    Defaults to the server's own default, which is
     *                    to stop.
     *
     * @returns One result per statement that ran, in order.
     */
    public async executeScript(
        connectionId: string,
        sqlScript: string,
        stopOnError?: boolean,
    ): Promise<IStatementResult[]> {
        const name = "db.execute_sql_script";

        return decodeList<IStatementResult>(
            name,
            await this.caller.callTool(name, {
                connection_id: connectionId,
                sql_script: sqlScript,
                // Left out entirely when not set, so a shell that does
                // not know the argument is not handed it.
                ...(stopOnError === undefined
                    ? {}
                    : { stop_on_error: stopOnError }),
            }),
        );
    }
}
