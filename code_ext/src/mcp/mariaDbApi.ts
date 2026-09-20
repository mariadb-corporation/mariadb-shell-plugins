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
     * Lists the connection URIs configured with `mcp.setup`.
     *
     * @returns One URI per configured connection.
     */
    public async listConnections(): Promise<string[]> {
        const name = "db.list_connections";

        return decodeList<string>(
            name,
            await this.caller.callTool(name, {}),
        );
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
