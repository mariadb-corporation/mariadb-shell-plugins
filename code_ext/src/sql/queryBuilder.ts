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

import type { IColumnDetails } from "../mcp/types.js";
import type {
    IGeneratedStatement,
    RowChange,
} from "../webview/protocol.js";
import { literalKind } from "./dataTypes.js";

/**
 * Quotes an identifier for use in a statement.
 *
 * @param name The identifier.
 *
 * @returns The identifier in back quotes, with any of its own doubled.
 */
export const quoteIdentifier = (name: string): string => {
    return `\`${name.replaceAll("`", "``")}\``;
};

/**
 * Escapes the characters that may not appear raw inside a quoted string.
 *
 * @param value The string to escape.
 *
 * @returns The escaped string, without the surrounding quotes.
 */
export const escapeString = (value: string): string => {
    return value
        .replaceAll("\\", "\\\\")
        .replaceAll("'", "''")
        .replaceAll("\n", "\\n")
        .replaceAll("\r", "\\r")
        .replaceAll("\t", "\\t")
        .replaceAll("\0", "\\0");
};

/**
 * Builds the INSERT, UPDATE and DELETE statements that write an edited
 * result set back to its table.
 *
 * Modelled on the MySQL Shell's QueryBuilder: a row is addressed by its
 * primary key, auto-generated columns are left out of an INSERT, and each
 * value is rendered according to its column's type.
 */
export class QueryBuilder {
    readonly #columns = new Map<string, IColumnDetails>();
    readonly #keyColumns: IColumnDetails[];
    readonly #qualifiedName: string;

    public constructor(
        schema: string | undefined,
        table: string,
        columns: IColumnDetails[],
    ) {
        for (const column of columns) {
            this.#columns.set(column.name, column);
        }

        this.#keyColumns = columns.filter((column) => {
            return Boolean(column.is_primary);
        });

        this.#qualifiedName = schema === undefined
            ? quoteIdentifier(table)
            : `${quoteIdentifier(schema)}.${quoteIdentifier(table)}`;
    }

    /**
     * A result set without a primary key cannot be addressed row by row, so
     * it stays read only however simple the query was.
     *
     * @returns True if rows can be written back.
     */
    public get isUpdatable(): boolean {
        return this.#keyColumns.length > 0;
    }

    /**
     * @returns The names of the primary key columns.
     */
    public get keyColumnNames(): string[] {
        return this.#keyColumns.map((column) => {
            return column.name;
        });
    }

    /**
     * Renders one value as a SQL literal.
     *
     * @param columnName The column the value belongs to.
     * @param value The value to render.
     *
     * @returns The literal to put into the statement.
     */
    public formatValue(columnName: string, value: unknown): string {
        if (value === null || value === undefined) {
            return "NULL";
        }

        const column = this.#columns.get(columnName);
        const kind = column ? literalKind(column.datatype) : "string";
        const text = String(value);

        switch (kind) {
            case "numeric": {
                // A value that is not a number would silently become 0 if it
                // were written unquoted, so it is quoted and left to the
                // server to reject.
                return /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(text)
                    ? text
                    : `'${escapeString(text)}'`;
            }

            case "binary": {
                // Binary values arrive hex encoded from the server, and go
                // back the same way.
                return /^[0-9a-f]*$/i.test(text)
                    ? `0x${text.length === 0 ? "00" : text}`
                    : `'${escapeString(text)}'`;
            }

            case "bit": {
                return /^[01]+$/.test(text)
                    ? `b'${text}'`
                    : `'${escapeString(text)}'`;
            }

            default: {
                return `'${escapeString(text)}'`;
            }
        }
    }

    /**
     * Builds the WHERE clause that addresses exactly one row.
     *
     * @param keys The row's primary key values, by column name.
     *
     * @returns The WHERE clause, including the keyword.
     */
    public whereClause(keys: Record<string, unknown>): string {
        if (!this.isUpdatable) {
            throw new Error(
                `${this.#qualifiedName} has no primary key, so its rows `
                + "cannot be addressed individually.",
            );
        }

        const conditions = this.#keyColumns.map((column) => {
            const value = keys[column.name];
            if (value === null || value === undefined) {
                return `${quoteIdentifier(column.name)} IS NULL`;
            }

            return `${quoteIdentifier(column.name)} = `
                + `${this.formatValue(column.name, value)}`;
        });

        return `WHERE ${conditions.join(" AND ")}`;
    }

    /**
     * Builds the UPDATE for a row whose cells were edited.
     *
     * @param keys The row's primary key values, as they were before the
     *             edit - they are what still addresses the stored row.
     * @param values The changed columns and their new values.
     *
     * @returns The UPDATE statement.
     */
    public buildUpdate(
        keys: Record<string, unknown>,
        values: Record<string, unknown>,
    ): string {
        const assignments = Object.entries(values).map(([name, value]) => {
            return `${quoteIdentifier(name)} = `
                + `${this.formatValue(name, value)}`;
        });

        if (assignments.length === 0) {
            throw new Error("An update needs at least one changed column.");
        }

        return `UPDATE ${this.#qualifiedName} SET ${assignments.join(", ")} `
            + this.whereClause(keys);
    }

    /**
     * Builds the INSERT for a row that was added.
     *
     * Columns the server fills in itself - auto-increment and generated
     * ones - are left out, so the server assigns them rather than being
     * handed the placeholder the grid showed.
     *
     * @param values The new row's values, by column name.
     *
     * @returns The INSERT statement.
     */
    public buildInsert(values: Record<string, unknown>): string {
        const names: string[] = [];
        const literals: string[] = [];

        for (const [name, value] of Object.entries(values)) {
            const column = this.#columns.get(name);
            if (column?.id_generation === "auto_inc"
                || Boolean(column?.is_generated)) {
                continue;
            }

            names.push(quoteIdentifier(name));
            literals.push(this.formatValue(name, value));
        }

        if (names.length === 0) {
            return `INSERT INTO ${this.#qualifiedName} () VALUES ()`;
        }

        return `INSERT INTO ${this.#qualifiedName} (${names.join(", ")}) `
            + `VALUES (${literals.join(", ")})`;
    }

    /**
     * Builds the DELETE for a row that was removed.
     *
     * @param keys The row's primary key values.
     *
     * @returns The DELETE statement.
     */
    public buildDelete(keys: Record<string, unknown>): string {
        return `DELETE FROM ${this.#qualifiedName} `
            + this.whereClause(keys);
    }

    /**
     * Turns a set of row changes into the statements that apply them.
     *
     * Deletes run last, so a row that was edited and then removed is not
     * updated after it is gone.
     *
     * Each statement keeps the grid row it came from, so the SQL preview
     * can take the user back to that row.
     *
     * @param changes The changes to apply.
     *
     * @returns One statement per change, in the order to run them.
     */
    public buildStatements(changes: RowChange[]): IGeneratedStatement[] {
        const updates: IGeneratedStatement[] = [];
        const inserts: IGeneratedStatement[] = [];
        const deletes: IGeneratedStatement[] = [];

        for (const change of changes) {
            switch (change.kind) {
                case "update": {
                    updates.push({
                        rowIndex: change.rowIndex,
                        sql: this.buildUpdate(change.keys, change.values),
                    });
                    break;
                }

                case "insert": {
                    inserts.push({
                        rowIndex: change.rowIndex,
                        sql: this.buildInsert(change.values),
                    });
                    break;
                }

                case "delete": {
                    deletes.push({
                        rowIndex: change.rowIndex,
                        sql: this.buildDelete(change.keys),
                    });
                    break;
                }
            }
        }

        return [...updates, ...inserts, ...deletes];
    }
}
