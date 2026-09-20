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
} from "tabulator-tables";

import type { IOutputRow } from "../../src/webview/protocol.js";

interface IOutputGridProperties {
    rows: IOutputRow[];
    /** The result sets whose tabs are still on show. */
    availableResultIds: ReadonlySet<string>;
    /** Switches to the tab of the result set a row produced. */
    onJumpToResult(resultId: string): void;
    /** Puts the cursor on the statement a row came from. */
    onGoToStatement(row: IOutputRow): void;
    /** A row to scroll into view, e.g. a run's first error. */
    scrollToRowId?: string;
}

/** The codicon each severity is drawn with, as the Problems panel does. */
const SEVERITY_ICONS = {
    info: "codicon-info",
    warning: "codicon-warning",
    error: "codicon-error",
} as const;

/**
 * Renders the severity cell: the same glyph the Problems panel marks a
 * line with, which is what tells the rows apart now that they all share
 * one background.
 *
 * @param cell The cell to render.
 *
 * @returns The cell's content.
 */
export const formatSeverityCell = (
    cell: CellComponent,
): HTMLElement => {
    const row = cell.getRow().getData() as IOutputRow;
    const icon = document.createElement("span");
    icon.className =
        `markerIcon ${row.kind} codicon ${SEVERITY_ICONS[row.kind]}`;
    icon.title = row.kind;

    return icon;
};

/**
 * Renders the jump cell: an arrow, but only where the result set that
 * row produced is still one of the open tabs. A later run replaces the
 * tabs, so most older rows have nothing to jump to.
 *
 * @param cell The cell to render.
 * @param available The result sets still on show.
 *
 * @returns The cell's content.
 */
export const formatJumpCell = (
    cell: CellComponent,
    available: ReadonlySet<string>,
): string | HTMLElement => {
    const row = cell.getRow().getData() as IOutputRow;
    if (row.resultId === undefined || !available.has(row.resultId)) {
        return "";
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "jumpToResult";
    button.textContent = "→";
    button.title = "Show this result set";

    return button;
};

/**
 * Renders the go-to-statement cell: an arrow for any row that knows
 * where in the file its statement is.
 *
 * On the closing row of a failed run it also carries the run to its
 * first error, which is why its title differs.
 *
 * @param cell The cell to render.
 *
 * @returns The cell's content.
 */
export const formatGoToCell = (
    cell: CellComponent,
): string | HTMLElement => {
    const row = cell.getRow().getData() as IOutputRow;
    if (!row.source) {
        return "";
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "goToStatement";
    button.textContent = "\u2197";
    button.title = row.jumpToRowId === undefined
        ? "Go to this statement in the editor"
        : "Go to the first error of this run";

    return button;
};

/**
 * @param value The number of milliseconds.
 *
 * @returns The duration, in the largest unit that stays readable.
 */
export const formatElapsed = (value: number): string => {
    if (value < 1000) {
        return `${value} ms`;
    }

    return `${(value / 1000).toFixed(3)} s`;
};

/**
 * Builds the output grid's columns.
 *
 * @param available The result sets still on show.
 * @param onJump Switches to a result set's tab.
 *
 * @returns The column definitions.
 */
export const buildOutputColumns = (
    available: ReadonlySet<string>,
    onJump: (resultId: string) => void,
    onGoTo: (row: IOutputRow) => void,
): ColumnDefinition[] => {
    return [
        {
            title: "",
            field: "kind",
            width: 24,
            hozAlign: "center",
            headerSort: false,
            resizable: false,
            cssClass: "outputSeverity",
            formatter: formatSeverityCell,
        },
        {
            title: "Output",
            field: "message",
            headerSort: false,
            widthGrow: 3,
            cssClass: "outputMessage",
        },
        {
            title: "Time",
            field: "time",
            width: 104,
            headerSort: false,
            cssClass: "outputTime",
        },
        {
            title: "Elapsed",
            field: "elapsedMs",
            width: 84,
            hozAlign: "right",
            headerSort: false,
            cssClass: "outputElapsed",
            // The server runs a script in one call and reports no
            // per-statement timing, so this is the whole run's time.
            headerTooltip: "How long the whole execution took",
            formatter: (cell) => {
                return formatElapsed(cell.getValue() as number);
            },
        },
        {
            title: "Rows",
            field: "rows",
            width: 68,
            hozAlign: "right",
            headerSort: false,
            cssClass: "outputRows",
            headerTooltip: "Rows returned, or rows affected",
            formatter: (cell) => {
                const value = cell.getValue() as number | undefined;

                return value === undefined ? "" : String(value);
            },
        },
        {
            title: "",
            field: "resultId",
            width: 34,
            hozAlign: "center",
            headerSort: false,
            resizable: false,
            cssClass: "outputJump",
            formatter: (cell) => {
                return formatJumpCell(cell, available);
            },
            cellClick: (_event, cell) => {
                const row = cell.getRow().getData() as IOutputRow;
                if (row.resultId !== undefined
                    && available.has(row.resultId)) {
                    onJump(row.resultId);
                }
            },
        },
        {
            title: "",
            field: "source",
            width: 34,
            hozAlign: "center",
            headerSort: false,
            resizable: false,
            cssClass: "outputGoTo",
            formatter: formatGoToCell,
            cellClick: (_event, cell) => {
                const row = cell.getRow().getData() as IOutputRow;
                if (row.source) {
                    onGoTo(row);
                }
            },
        },
        {
            title: "Statement",
            field: "statement",
            width: 260,
            headerSort: false,
            cssClass: "outputStatement",
        },
    ];
};

/**
 * The output tab: one row per statement of every execution, oldest
 * first.
 *
 * Unlike the result grids, this one accumulates - it is the log of what
 * has been run on this connection - so it scrolls to the newest row as
 * rows arrive.
 *
 * @param props The rows to show.
 *
 * @returns The rendered grid.
 */
export const OutputGrid = (props: IOutputGridProperties): JSX.Element => {
    const { rows, availableResultIds, scrollToRowId } = props;
    const host = useRef<HTMLDivElement>(null);
    const table = useRef<Tabulator | undefined>(undefined);
    const built = useRef(false);
    const pendingRows = useRef<IOutputRow[] | undefined>(undefined);
    // Held in a ref so the callbacks Tabulator keeps never go stale.
    const callbacks = useRef(props);
    callbacks.current = props;

    useLayoutEffect(() => {
        if (!host.current) {
            return;
        }

        const instance = new Tabulator(host.current, {
            data: [...rows],
            columns: buildOutputColumns(
                availableResultIds,
                (resultId) => {
                    callbacks.current.onJumpToResult(resultId);
                },
                (outputRow) => {
                    callbacks.current.onGoToStatement(outputRow);
                },
            ),
            index: "id",
            layout: "fitColumns",
            height: "100%",
            placeholder: "Nothing has been run on this connection yet.",
            rowFormatter: (row) => {
                const data = row.getData() as IOutputRow;
                const element = row.getElement();
                // Only the text colour and the marker icon vary; every
                // row shares one background.
                element.classList.toggle("errorRow", data.kind === "error");
                element.classList.toggle(
                    "warningRow", data.kind === "warning");
                element.classList.toggle("runRow", data.role !== "statement");
            },
        });

        instance.on("tableBuilt", () => {
            built.current = true;
            const rowsToApply = pendingRows.current;
            pendingRows.current = undefined;
            if (rowsToApply) {
                void instance.replaceData([...rowsToApply]);
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
        // The columns close over which result sets are still available,
        // so the table is rebuilt when that changes.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [availableResultIds]);

    useEffect(() => {
        const instance = table.current;
        if (!instance || !built.current || scrollToRowId === undefined) {
            return;
        }

        try {
            void instance.scrollToRow(scrollToRowId, "center", false);
        } catch {
            // The row has scrolled out of the capped history.
        }
    }, [scrollToRowId]);

    useEffect(() => {
        const instance = table.current;
        if (!instance) {
            return;
        }

        if (!built.current) {
            pendingRows.current = rows;

            return;
        }

        void instance.replaceData([...rows]).then(() => {
            // The newest line is the one worth seeing.
            const last = rows.at(-1);
            if (last) {
                try {
                    void instance.scrollToRow(last.id, "bottom", false);
                } catch {
                    // The row is gone already, which is not worth
                    // reporting.
                }
            }
        });
    }, [rows]);

    return <div class="outputGridHost" ref={host} />;
};
