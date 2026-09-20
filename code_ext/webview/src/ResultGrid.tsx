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

import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef } from "preact/hooks";
import {
    TabulatorFull as Tabulator,
    type CellComponent,
    type ColumnDefinition,
    type RowComponent,
} from "tabulator-tables";

import type { IEditableRow } from "../../src/webview/changes.js";
import type { IResultSet } from "../../src/webview/protocol.js";

/** The field Tabulator keys a row on, hidden from the user. */
export const ROW_INDEX_FIELD = "__mariadbRowIndex";

/** How a row is shown in the grid, driven by the editing state. */
export interface IGridCallbacks {
    onCellEdited(rowIndex: number, column: string, value: unknown): void;
    onToggleDeleted(rowIndex: number): void;
    onSelectionChanged(rowIndex: number | undefined): void;
}

interface IResultGridProperties extends IGridCallbacks {
    resultSet: IResultSet;
    rows: IEditableRow[];
    /** Scroll to and highlight this row, e.g. from the SQL preview. */
    selectedRowIndex?: number;
}

/**
 * Renders a value for the grid. NULL gets a marker of its own, because an
 * empty cell would be indistinguishable from an empty string.
 *
 * @param value The value to show.
 *
 * @returns The HTML for the cell.
 */
export const formatCell = (cell: CellComponent): string | HTMLElement => {
    const value = cell.getValue() as unknown;
    if (value === null || value === undefined) {
        const span = document.createElement("span");
        span.className = "nullValue";
        span.textContent = "NULL";

        return span;
    }

    return typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
};

/**
 * Builds the Tabulator column definitions for a result set.
 *
 * @param resultSet The result set to show.
 * @param callbacks What the grid reports back.
 *
 * @returns The column definitions, including the row header.
 */
export const buildColumns = (
    resultSet: IResultSet,
    callbacks: IGridCallbacks,
): ColumnDefinition[] => {
    const columns: ColumnDefinition[] = [];

    if (resultSet.editable) {
        // The row header carries the delete toggle and the row state.
        columns.push({
            title: "",
            field: ROW_INDEX_FIELD,
            width: 34,
            hozAlign: "center",
            headerSort: false,
            resizable: false,
            frozen: true,
            cssClass: "rowHeaderCell",
            formatter: (cell) => {
                const row = cell.getRow().getData() as Record<string, unknown>;
                const deleted = Boolean(row.__deleted);
                const button = document.createElement("button");
                button.type = "button";
                button.className = "rowDeleteToggle";
                button.textContent = deleted ? "↺" : "✕";
                button.title = deleted
                    ? "Keep this row"
                    : "Mark this row for deletion";

                return button;
            },
            cellClick: (_event, cell) => {
                const index = cell.getRow().getData()[ROW_INDEX_FIELD] as
                    number;
                callbacks.onToggleDeleted(index);
            },
        });
    }

    for (const column of resultSet.columns) {
        columns.push({
            title: column.name,
            field: column.name,
            headerTooltip: column.datatype ?? column.name,
            cssClass: column.isPrimary ? "pkColumn" : undefined,
            formatter: formatCell,
            editor: resultSet.editable && !column.isGenerated
                ? "input"
                : undefined,
            // Tabulator's own edit check runs per cell, which is where a
            // row marked for deletion is frozen.
            editable: (cell: CellComponent) => {
                const row = cell.getRow().getData() as Record<string, unknown>;

                return resultSet.editable && !Boolean(row.__deleted);
            },
            cellEdited: (cell: CellComponent) => {
                const data = cell.getRow().getData() as
                    Record<string, unknown>;
                callbacks.onCellEdited(
                    data[ROW_INDEX_FIELD] as number,
                    column.name,
                    cell.getValue() as unknown,
                );
            },
        });
    }

    return columns;
};

/**
 * Flattens the editing state into the rows Tabulator renders.
 *
 * @param rows The rows in their current state.
 *
 * @returns One plain object per row, carrying its state markers.
 */
export const toTableData = (
    rows: IEditableRow[],
): Array<Record<string, unknown>> => {
    return rows.map((row, index) => {
        const dirty = Object.keys(row.current).filter((name) => {
            return !Object.is(row.current[name], row.original[name]);
        });

        return {
            ...row.current,
            [ROW_INDEX_FIELD]: index,
            __deleted: row.deleted,
            __added: row.added,
            __dirty: dirty,
        };
    });
};

/**
 * An editable result grid, built on Tabulator.
 *
 * Tabulator owns the DOM below its container, so the component holds it in
 * a ref and feeds it data rather than re-rendering it through Preact.
 *
 * @param props The result set and the rows to show.
 *
 * @returns The rendered grid.
 */
export const ResultGrid = (props: IResultGridProperties): JSX.Element => {
    const { resultSet, rows, selectedRowIndex } = props;
    const host = useRef<HTMLDivElement>(null);
    const table = useRef<Tabulator | undefined>(undefined);
    // Tabulator builds itself asynchronously, and every call that touches
    // its data before that throws. Nothing is pushed in until it has
    // reported `tableBuilt`.
    const built = useRef(false);
    const pendingRows = useRef<IEditableRow[] | undefined>(undefined);
    // Held in a ref so the callbacks Tabulator keeps never go stale.
    const callbacks = useRef<IGridCallbacks>(props);
    callbacks.current = props;

    useLayoutEffect(() => {
        if (!host.current) {
            return;
        }

        const instance = new Tabulator(host.current, {
            data: toTableData(rows),
            columns: buildColumns(resultSet, {
                onCellEdited: (index, column, value) => {
                    callbacks.current.onCellEdited(index, column, value);
                },
                onToggleDeleted: (index) => {
                    callbacks.current.onToggleDeleted(index);
                },
                onSelectionChanged: (index) => {
                    callbacks.current.onSelectionChanged(index);
                },
            }),
            index: ROW_INDEX_FIELD,
            layout: "fitDataStretch",
            height: "100%",
            selectableRows: true,
            resizableColumnFit: false,
            movableColumns: true,
            placeholder: "The query returned no rows.",
            rowFormatter: (row: RowComponent) => {
                const data = row.getData() as Record<string, unknown>;
                const element = row.getElement();
                element.classList.toggle("deletedRow",
                    Boolean(data.__deleted));
                element.classList.toggle("addedRow", Boolean(data.__added));

                const dirty = (data.__dirty ?? []) as string[];
                for (const cell of row.getCells()) {
                    cell.getElement().classList.toggle(
                        "dirtyCell",
                        dirty.includes(cell.getField()),
                    );
                }
            },
        });

        instance.on("rowSelectionChanged", (_data, selected) => {
            const first = selected[0]?.getData() as
                Record<string, unknown> | undefined;
            callbacks.current.onSelectionChanged(
                first?.[ROW_INDEX_FIELD] as number | undefined,
            );
        });

        instance.on("tableBuilt", () => {
            built.current = true;
            const rowsToApply = pendingRows.current;
            pendingRows.current = undefined;
            if (rowsToApply) {
                void instance.replaceData(toTableData(rowsToApply));
            }
        });

        built.current = false;
        pendingRows.current = undefined;
        table.current = instance;

        return () => {
            built.current = false;
            pendingRows.current = undefined;
            table.current = undefined;
            try {
                instance.destroy();
            } catch {
                // Destroying a table that never finished building throws
                // rather than being a no-op.
            }
        };
        // Rebuilt only when the result set itself changes; the rows are
        // pushed in by the effect below.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [resultSet]);

    useEffect(() => {
        const instance = table.current;
        if (!instance) {
            return;
        }

        if (!built.current) {
            // Applied by the tableBuilt handler instead.
            pendingRows.current = rows;

            return;
        }

        // replaceData keeps the scroll position and the column widths,
        // which setData would throw away on every keystroke.
        void instance.replaceData(toTableData(rows));
    }, [rows]);

    useEffect(() => {
        const instance = table.current;
        if (!instance || !built.current || selectedRowIndex === undefined) {
            return;
        }

        try {
            void instance.scrollToRow(selectedRowIndex, "center", false);
            instance.deselectRow();
            instance.selectRow([selectedRowIndex]);
        } catch {
            // The row is gone, which is not worth reporting.
        }
    }, [selectedRowIndex]);

    return <div class="resultGridHost" ref={host} />;
};
