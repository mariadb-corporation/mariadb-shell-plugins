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
import type { ValueDisplay } from "../../src/sql/dataTypes.js";
import type {
    IResultColumn,
    IResultSet,
} from "../../src/webview/protocol.js";
import { openContextMenu } from "./contextMenu.js";

/** The field Tabulator keys a row on, hidden from the user. */
export const ROW_INDEX_FIELD = "__mariadbRowIndex";

/** How a row is shown in the grid, driven by the editing state. */
export interface IGridCallbacks {
    onCellEdited(rowIndex: number, column: string, value: unknown): void;
    onToggleDeleted(rowIndex: number): void;
    onSelectionChanged(rowIndex: number | undefined): void;
    /** Saves a cell's value to a file the user picks. */
    onSaveValue?(rowIndex: number, column: string): void;
    /** Loads a file the user picks into a cell. */
    onLoadValue?(rowIndex: number, column: string): void;
    /** Opens a cell's value in an editor. */
    onOpenValue?(rowIndex: number, column: string): void;
}

/**
 * The text length past which a value gets the Open in Editor button over
 * it: long enough that a cell shows only its start.
 */
export const LONG_TEXT = 80;

/** What a cell of the grid can have done to its value, and whether. */
export interface ICellActions {
    /** A BLOB with a value: there is something to save. */
    canSave: boolean;
    /** A BLOB of an editable row: a file can be loaded into it. */
    canLoad: boolean;
    /** An editable, nullable cell that is not NULL already. */
    canSetNull: boolean;
    /**
     * Whether a value opened in an editor is read only there: a result
     * that cannot be edited, a generated column, a row marked for
     * deletion, or a spatial value or vector, which cannot be written
     * back from the hex they are held as. Every value can be opened.
     */
    openReadOnly: boolean;
}

/**
 * Works out what can be done with one cell's value, the rules the MySQL
 * Shell's cell menu follows: saving and loading a file are for BLOBs, and
 * anything that changes the value needs a row that can be edited.
 *
 * @param resultSet The result set the cell is in.
 * @param column The cell's column.
 * @param value The cell's value.
 * @param deleted Whether its row is marked for deletion.
 *
 * @returns What its menu and its overlay offer.
 */
export const cellActionsOf = (
    resultSet: IResultSet,
    column: IResultColumn,
    value: unknown,
    deleted: boolean,
): ICellActions => {
    const blob = column.display === "blob";
    const changeable = resultSet.editable && !column.isGenerated && !deleted;
    const isNull = value === null || value === undefined;

    return {
        canSave: blob && !isNull,
        canLoad: blob && changeable,
        canSetNull: changeable && column.nullable !== false && !isNull,
        openReadOnly: !changeable || !editableAsText(column.display),
    };
};

/**
 * Whether a cell's value is worth an Open in Editor button over it: a
 * JSON value, or text a cell cannot show whole - several lines of it, or
 * more than `LONG_TEXT` characters.
 *
 * @param value The cell's value.
 * @param display How its column's values are shown.
 *
 * @returns True for such a value.
 */
export const worthOpening = (
    value: unknown,
    display?: ValueDisplay,
): boolean => {
    if (value === null || value === undefined) {
        return false;
    }

    const text = String(value);

    return display === "json" || text.includes("\n")
        || text.length > LONG_TEXT;
};

/**
 * A small button over a BLOB cell. It keeps its clicks to itself: they
 * would otherwise reach the cell, and open its editor or select its row.
 *
 * @param icon The class that draws its icon.
 * @param title What it does.
 * @param onClick What happens on a click.
 *
 * @returns The button.
 */
const overlayButton = (
    icon: string,
    title: string,
    onClick: () => void,
): HTMLButtonElement => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `cellOverlayButton ${icon}`;
    button.title = title;
    button.setAttribute("aria-label", title);
    for (const kind of ["mousedown", "dblclick"]) {
        button.addEventListener(kind, (event) => {
            event.stopPropagation();
        });
    }
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        event.preventDefault();
        onClick();
    });

    return button;
};

/**
 * Renders a BLOB cell: its icon, and the Open, Save and Load buttons that
 * show over it while the pointer is on it.
 *
 * @param value The cell's value.
 * @param actions What can be done with it.
 * @param onSave Saves it to a file.
 * @param onLoad Loads a file into it.
 * @param onOpen Opens it in an editor.
 *
 * @returns The cell's content.
 */
export const blobCell = (
    value: unknown,
    actions: ICellActions,
    onSave: () => void,
    onLoad: () => void,
    onOpen?: () => void,
): HTMLElement => {
    const host = document.createElement("span");
    host.className = "blobCell";
    host.append(formatValue(value, "blob") as HTMLElement);

    const openable = onOpen !== undefined && value !== null
        && value !== undefined;
    if (actions.canSave || actions.canLoad || openable) {
        const overlay = document.createElement("span");
        overlay.className = "cellOverlay";
        if (openable) {
            overlay.append(overlayButton("openIcon",
                "Open Value in Editor", onOpen));
        }
        if (actions.canSave) {
            overlay.append(overlayButton("saveIcon",
                "Save Value to File...", onSave));
        }
        if (actions.canLoad) {
            overlay.append(overlayButton("loadIcon",
                "Load Value from File...", onLoad));
        }
        host.append(overlay);
    }

    return host;
};

/**
 * Renders a text cell that holds more than it can show, with the Open in
 * Editor button over it while the pointer is on it.
 *
 * @param value The cell's value.
 * @param display How its column's values are shown.
 * @param onOpen Opens it in an editor.
 *
 * @returns The cell's content.
 */
export const openableCell = (
    value: unknown,
    display: ValueDisplay | undefined,
    onOpen: () => void,
): HTMLElement => {
    const host = document.createElement("span");
    host.className = "blobCell openableCell";
    const text = document.createElement("span");
    text.className = "cellText";
    text.textContent = formatValue(value, display) as string;
    host.append(text);

    const overlay = document.createElement("span");
    overlay.className = "cellOverlay";
    overlay.append(overlayButton("openIcon", "Open Value in Editor", onOpen));
    host.append(overlay);

    return host;
};

interface IResultGridProperties extends IGridCallbacks {
    resultSet: IResultSet;
    rows: IEditableRow[];
    /** Scroll to and highlight this row, e.g. from the SQL preview. */
    selectedRowIndex?: number;
    /**
     * Bumped by Start Editing: each new value opens the first editable
     * cell of the first row not marked for deletion.
     */
    editRequest?: number;
    /** Whether the primary key columns are frozen at the left. */
    freezeKeys?: boolean;
}

/** How many hex digits of a binary value are shown before it is cut. */
export const BINARY_DIGITS_SHOWN = 64;

/** The icon each kind of value that is not shown as text stands under. */
const VALUE_ICONS: Partial<Record<ValueDisplay, string>> = {
    blob: "BLOB",
    geometry: "GEOMETRY",
    vector: "VECTOR",
};

/**
 * A marker drawn as one of the MySQL Shell's data icons. The word it
 * stands for stays in the cell as its text, so it is still what is copied
 * and what a screen reader says; the stylesheet draws the icon over it.
 *
 * @param className The class that picks the icon.
 * @param text The word it stands for.
 *
 * @returns The element.
 */
const valueIcon = (className: string, text: string): HTMLElement => {
    const span = document.createElement("span");
    span.className = `dataIcon ${className}`;
    span.textContent = text;

    return span;
};

/**
 * Renders a value for the grid, as the MySQL Shell's result view does.
 *
 * NULL gets a marker of its own, because an empty cell would be
 * indistinguishable from an empty string. A binary value - which arrives
 * as hex - is shown as `0x` and its first 64 digits; a BLOB, a spatial
 * value or a vector by an icon standing for it.
 *
 * @param value The value to show.
 * @param display How the column's values are shown, if not as text.
 *
 * @returns The HTML for the cell.
 */
export const formatValue = (
    value: unknown,
    display?: ValueDisplay,
): string | HTMLElement => {
    if (value === null || value === undefined) {
        const span = valueIcon("nullValue", "NULL");

        return span;
    }

    const icon = display === undefined ? undefined : VALUE_ICONS[display];
    if (icon !== undefined) {
        return valueIcon(`${display}Value`, icon);
    }

    const text = typeof value === "object"
        ? JSON.stringify(value)
        : String(value);

    if (display === "binary") {
        return text.length > BINARY_DIGITS_SHOWN
            ? `0x${text.slice(0, BINARY_DIGITS_SHOWN)}\u2026`
            : `0x${text}`;
    }

    return text;
};

/**
 * Renders a cell of a column shown as text.
 *
 * @param cell The cell.
 *
 * @returns The HTML for the cell.
 */
export const formatCell = (cell: CellComponent): string | HTMLElement => {
    return formatValue(cell.getValue() as unknown);
};

/**
 * Whether a column's values can be edited as text in the grid. A spatial
 * value or a vector arrives as hex and would be written back as a quoted
 * string, which is not the value it was; binary and BLOB values go back
 * as hex, so those can.
 *
 * @param display How the column's values are shown.
 *
 * @returns True where the grid may open an editor on it.
 */
export const editableAsText = (display?: ValueDisplay): boolean => {
    return display !== "geometry" && display !== "vector";
};

/**
 * Builds the Tabulator column definitions for a result set.
 *
 * @param resultSet The result set to show.
 * @param callbacks What the grid reports back.
 *
 * @returns The column definitions, including the row header.
 */
/**
 * The columns in the order the grid shows them: as the result set has
 * them, or - with its primary key frozen - the key columns first. The
 * grid can freeze columns only at its left-hand edge, so a key column
 * that comes later in the result is moved there to be frozen.
 *
 * @param resultSet The result set.
 * @param freezeKeys Whether its primary key columns are frozen.
 *
 * @returns Its columns, each with whether it is frozen.
 */
export const orderColumns = (
    resultSet: IResultSet,
    freezeKeys: boolean,
): Array<{ column: IResultColumn; frozen: boolean }> => {
    const keys = freezeKeys
        ? resultSet.columns.filter((column) => { return column.isPrimary; })
        : [];
    const rest = resultSet.columns.filter((column) => {
        return !keys.includes(column);
    });

    return [
        ...keys.map((column) => { return { column, frozen: true }; }),
        ...rest.map((column) => { return { column, frozen: false }; }),
    ];
};

export const buildColumns = (
    resultSet: IResultSet,
    callbacks: IGridCallbacks,
    freezeKeys = false,
): ColumnDefinition[] => {
    const columns: ColumnDefinition[] = [];

    for (const { column, frozen } of orderColumns(resultSet, freezeKeys)) {
        columns.push({
            title: column.name,
            field: column.name,
            headerTooltip: column.datatype ?? column.name,
            cssClass: column.isPrimary ? "pkColumn" : undefined,
            frozen,
            formatter: (cell: CellComponent) => {
                const value = cell.getValue() as unknown;
                const row = cell.getRow().getData() as Record<string, unknown>;
                const index = row[ROW_INDEX_FIELD] as number;
                const open = (): void => {
                    callbacks.onOpenValue?.(index, column.name);
                };

                if (column.display === "blob") {
                    return blobCell(
                        value,
                        cellActionsOf(resultSet, column, value,
                            Boolean(row.__deleted)),
                        () => { callbacks.onSaveValue?.(index, column.name); },
                        () => { callbacks.onLoadValue?.(index, column.name); },
                        open,
                    );
                }

                return worthOpening(value, column.display)
                    ? openableCell(value, column.display, open)
                    : formatValue(value, column.display);
            },
            // The MySQL Shell's cell menu, as far as it applies here.
            cellContext: (event: UIEvent, cell: CellComponent) => {
                const value = cell.getValue() as unknown;
                const row = cell.getRow().getData() as Record<string, unknown>;
                const index = row[ROW_INDEX_FIELD] as number;
                const actions = cellActionsOf(resultSet, column, value,
                    Boolean(row.__deleted));

                openContextMenu(event as MouseEvent, [
                    {
                        label: "Open Value in Editor",
                        onClick: () => {
                            callbacks.onOpenValue?.(index, column.name);
                        },
                    },
                    {
                        label: "Set Field to Null",
                        disabled: !actions.canSetNull,
                        onClick: () => {
                            callbacks.onCellEdited(index, column.name, null);
                        },
                    },
                    {},
                    {
                        label: "Save Value to File...",
                        disabled: !actions.canSave,
                        onClick: () => {
                            callbacks.onSaveValue?.(index, column.name);
                        },
                    },
                    {
                        label: "Load Value from File...",
                        disabled: !actions.canLoad,
                        onClick: () => {
                            callbacks.onLoadValue?.(index, column.name);
                        },
                    },
                    {},
                    {
                        // The same item takes the mark back off.
                        label: row.__deleted ? "Restore Row" : "Delete Row",
                        disabled: !resultSet.editable,
                        onClick: () => {
                            callbacks.onToggleDeleted(index);
                        },
                    },
                ]);
            },
            editor: resultSet.editable && !column.isGenerated
                && editableAsText(column.display)
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
    const {
        resultSet,
        rows,
        selectedRowIndex,
        editRequest,
        freezeKeys = false,
    } = props;
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
                onSaveValue: (index, column) => {
                    callbacks.current.onSaveValue?.(index, column);
                },
                onLoadValue: (index, column) => {
                    callbacks.current.onLoadValue?.(index, column);
                },
                onOpenValue: (index, column) => {
                    callbacks.current.onOpenValue?.(index, column);
                },
                onSelectionChanged: (index) => {
                    callbacks.current.onSelectionChanged(index);
                },
            }, freezeKeys),
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
        // Rebuilt only when the result set itself changes, or which of its
        // columns are frozen; the rows are pushed in by the effect below.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [resultSet, freezeKeys]);

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

    useEffect(() => {
        const instance = table.current;
        if (!instance || !built.current || !editRequest) {
            return;
        }

        const row = instance.getRows().find((candidate) => {
            return !(candidate.getData() as Record<string, unknown>).__deleted;
        });
        const cell = row?.getCells().find((candidate) => {
            return candidate.getColumn().getDefinition().editor !== undefined;
        });
        if (row && cell) {
            void instance.scrollToRow(row, "top", false);
            cell.edit(true);
        }
    }, [editRequest]);

    return <div class="resultGridHost" ref={host} />;
};
