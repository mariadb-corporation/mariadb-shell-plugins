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

import type { IResultSet, RowChange } from "./protocol.js";

/**
 * A row as the grid holds it while it is being edited.
 *
 * The original values are kept beside the current ones because they are
 * what still addresses the stored row: an edit to a primary key column
 * has to be written with the key the database still has.
 */
export interface IEditableRow {
    original: Record<string, unknown>;
    current: Record<string, unknown>;
    /** True for a row the user added. */
    added: boolean;
    /** True for a row the user marked for deletion. */
    deleted: boolean;
}

/**
 * Builds the editing state for a result set's rows.
 *
 * @param resultSet The result set to start from.
 *
 * @returns One editable row per result row.
 */
export const initialRows = (resultSet: IResultSet): IEditableRow[] => {
    return resultSet.rows.map((row) => {
        return {
            original: { ...row },
            current: { ...row },
            added: false,
            deleted: false,
        };
    });
};

/**
 * Builds a blank row for the columns of a result set.
 *
 * @param resultSet The result set to add a row to.
 *
 * @returns The new row, with every column null.
 */
export const blankRow = (resultSet: IResultSet): IEditableRow => {
    const values = Object.fromEntries(resultSet.columns.map((column) => {
        return [column.name, null];
    }));

    return {
        original: values,
        current: { ...values },
        added: true,
        deleted: false,
    };
};

/**
 * Collects the changes the user made, as the extension needs them.
 *
 * @param resultSet The result set being edited.
 * @param rows The rows in their current state.
 *
 * @returns One change per changed row, in row order.
 */
export const collectChanges = (
    resultSet: IResultSet,
    rows: IEditableRow[],
): RowChange[] => {
    const keyNames = resultSet.columns
        .filter((column) => {
            return column.isPrimary;
        })
        .map((column) => {
            return column.name;
        });

    /**
     * @param row The row to take the key of.
     *
     * @returns Its primary key values, as they were before editing.
     */
    const keysOf = (row: IEditableRow): Record<string, unknown> => {
        return Object.fromEntries(keyNames.map((name) => {
            return [name, row.original[name]];
        }));
    };

    const changes: RowChange[] = [];
    for (const [rowIndex, row] of rows.entries()) {
        if (row.deleted) {
            // A row that was added and then removed never reached the
            // database, so there is nothing to delete.
            if (!row.added) {
                changes.push({
                    kind: "delete",
                    rowIndex,
                    keys: keysOf(row),
                });
            }
            continue;
        }

        if (row.added) {
            changes.push({
                kind: "insert",
                rowIndex,
                values: { ...row.current },
            });
            continue;
        }

        const values: Record<string, unknown> = {};
        for (const column of resultSet.columns) {
            const before = row.original[column.name];
            const after = row.current[column.name];
            if (!Object.is(before, after)) {
                values[column.name] = after;
            }
        }

        if (Object.keys(values).length > 0) {
            changes.push({
                kind: "update",
                rowIndex,
                keys: keysOf(row),
                values,
            });
        }
    }

    return changes;
};
