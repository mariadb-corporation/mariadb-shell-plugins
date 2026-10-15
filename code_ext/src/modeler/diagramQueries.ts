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

import type { IStatementResult } from "../mcp/types.js";
import { escapeString, quoteIdentifier } from "../sql/queryBuilder.js";
import { DIAGRAMS_VIEW, type IDiagramRow } from "./modelFormat.js";

/**
 * The statements that read a schema's diagrams out of its
 * `msm_schema_diagrams` view, and what their results mean. Pure, so both
 * the Connections tree and the diagram viewer use them and tests can check
 * them without a server.
 */

/**
 * Whether a schema has the view: asked when the schema is expanded, so it
 * is one look into the data dictionary rather than a listing of all views.
 *
 * @param schema The schema.
 *
 * @returns The statement; it returns a row where the view is there.
 */
export const hasDiagramsSql = (schema: string): string => {
    return "SELECT 1 FROM information_schema.TABLES "
        + `WHERE TABLE_SCHEMA = '${escapeString(schema)}' `
        + `AND TABLE_NAME = '${DIAGRAMS_VIEW}' AND TABLE_TYPE = 'VIEW'`;
};

/**
 * The names of a schema's diagrams, as the view lists them; the schema
 * definition's row is left out by its format.
 *
 * @param schema The schema.
 *
 * @returns The statement.
 */
export const listDiagramsSql = (schema: string): string => {
    return `SELECT \`name\` FROM ${quoteIdentifier(schema)}.`
        + `${quoteIdentifier(DIAGRAMS_VIEW)} WHERE \`format\` LIKE '%diagram%'`;
};

/**
 * Every document of a schema's diagrams: the schema definition and each
 * diagram.
 *
 * @param schema The schema.
 *
 * @returns The statement.
 */
export const readDiagramsSql = (schema: string): string => {
    return `SELECT \`name\`, \`format\`, \`document\` FROM `
        + `${quoteIdentifier(schema)}.${quoteIdentifier(DIAGRAMS_VIEW)}`;
};

/**
 * @param result A statement's result.
 *
 * @returns Its first result set's rows.
 * @throws If the statement failed.
 */
const rowsOf = (result: IStatementResult): Array<Record<string, unknown>> => {
    if (result.error !== undefined) {
        throw new Error(result.error);
    }

    return result.result_sets?.[0]?.rows ?? [];
};

/**
 * @param result The result of `hasDiagramsSql`.
 *
 * @returns Whether the schema has the view.
 */
export const hasDiagrams = (result: IStatementResult): boolean => {
    return rowsOf(result).length > 0;
};

/**
 * @param result The result of `listDiagramsSql`.
 *
 * @returns The diagrams' names.
 */
export const diagramNames = (result: IStatementResult): string[] => {
    return rowsOf(result).map((row) => { return String(row.name); });
};

/**
 * @param result The result of `readDiagramsSql`.
 *
 * @returns The view's rows.
 */
export const diagramRows = (result: IStatementResult): IDiagramRow[] => {
    return rowsOf(result).map((row) => {
        return {
            name: String(row.name),
            format: String(row.format),
            document: row.document,
        };
    });
};
