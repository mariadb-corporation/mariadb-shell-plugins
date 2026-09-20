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

/** A configured connection, as `db.list_connections` reports it. */
export interface IConnectionUri {
    uri: string;
}

/** One entry of `db.list_schemas`. */
export interface ISchemaInfo {
    schema_name: string;
    schema_type: string;
    schema_comment: string;
}

/** The object types `db.list_objects` understands. */
export const OBJECT_TYPES = [
    "table",
    "view",
    "function",
    "procedure",
    "sequence",
    "trigger",
    "event",
] as const;

export type ObjectType = (typeof OBJECT_TYPES)[number];

/** One entry of `db.list_objects`. */
export interface IObjectInfo {
    name: string;
    /** Tables and views carry a comment. */
    comment?: string;
    /** Sequences carry their value data type. */
    datatype?: string;
}

/** One column of a table, as `db.get_object_details` describes it. */
export interface IColumnDetails {
    name: string;
    datatype: string;
    /** 1 when the column is NOT NULL. */
    not_null: number;
    /** 1 when the column is part of the primary key. */
    is_primary: number;
    is_unique: number;
    is_generated: number;
    /** "auto_inc", "rev_uuid" or null. */
    id_generation: string | null;
    comment: string;
    column_default: string | null;
}

/** What `db.get_object_details` returns for a table. */
export interface IObjectDetails {
    basic: {
        schema: string;
        name: string;
        type: string;
        comment?: string;
    };
    columns?: IColumnDetails[];
}

/**
 * One statement's result, as `db.execute_sql` and `db.execute_sql_script`
 * report it. `columns` and `rows` are absent for statements that return no
 * result set.
 *
 * The fields below `rows` were added to the MCP plugin for this extension
 * and are all optional, because a shell old enough to predate them still
 * satisfies the minimum version. Everything that reads them falls back to
 * what can be worked out without them.
 */
export interface IStatementResult {
    affected_items_count?: number;
    warnings_count?: number;
    session_restarted?: boolean;
    columns?: string[];
    rows?: Array<Record<string, unknown>>;

    /** Its position among the script's non-empty statements, from 0. */
    statement_index?: number;
    /** How long the statement took, in seconds. */
    execution_time?: number;
    /**
     * Set instead of a result when the statement failed. The script stops
     * here unless it was run with `stopOnError` false.
     */
    error?: string;
    /** The failing statement's text, abbreviated. */
    statement?: string;
}

/**
 * The database side of the MCP server, as the extension uses it. Keeping it
 * behind an interface is what lets the tree, the editor toolbar and the
 * result panel be tested without a shell.
 */
export interface IMariaDbApi {
    listConnections(): Promise<string[]>;
    connect(uri: string): Promise<string>;
    close(connectionId: string): Promise<void>;
    listSchemas(connectionId: string): Promise<ISchemaInfo[]>;
    listObjects(
        connectionId: string,
        schemaName: string,
        objectType: ObjectType,
    ): Promise<IObjectInfo[]>;
    getObjectDetails(
        connectionId: string,
        schemaName: string,
        objectName: string,
        objectType: ObjectType,
    ): Promise<IObjectDetails>;
    executeScript(
        connectionId: string,
        sqlScript: string,
        stopOnError?: boolean,
    ): Promise<IStatementResult[]>;
}
