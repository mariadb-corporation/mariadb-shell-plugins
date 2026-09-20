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
 * Builds the arrow that switches to the result set a row produced, or
 * nothing where that result set is no longer one of the open tabs: a
 * later run replaces the tabs, so most older rows have nothing to jump
 * to.
 *
 * @param row The row the cell belongs to.
 * @param available The result sets still on show.
 *
 * @returns The button, or undefined where there is nothing to jump to.
 */
export const createJumpButton = (
    row: IOutputRow,
    available: ReadonlySet<string>,
): HTMLButtonElement | undefined => {
    if (row.resultId === undefined || !available.has(row.resultId)) {
        return undefined;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "jumpToResult";
    button.textContent = "\u2192";
    button.title = "Show this result set";

    return button;
};

/**
 * Builds the arrow that puts the cursor on the statement a row came
 * from, for any row that knows where in the file that statement is.
 *
 * On the closing row of a failed run it also carries the run to its
 * first error, which is why its title differs.
 *
 * @param row The row the cell belongs to.
 *
 * @returns The button, or undefined where the row has no source.
 */
export const createGoToButton = (
    row: IOutputRow,
): HTMLButtonElement | undefined => {
    if (!row.source) {
        return undefined;
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
 * Renders the message cell: the message, with the go-to-statement arrow
 * pinned to the cell's top right corner.
 *
 * The arrow rides here rather than in a column of its own because a
 * column of arrows is width the message could be using instead, and the
 * panel area is short of it.
 *
 * @param cell The cell to render.
 *
 * @returns The cell's content.
 */
export const formatMessageCell = (cell: CellComponent): HTMLElement => {
    const row = cell.getRow().getData() as IOutputRow;
    const content = document.createElement("div");
    content.className = "outputMessageContent";
    content.textContent = row.message;

    const button = createGoToButton(row);
    if (button) {
        // The message is kept clear of the corner the arrow sits in.
        content.classList.add("hasGoTo");
        content.appendChild(button);
    }

    return content;
};

/**
 * Renders the row count, with the jump-to-result arrow beside it - for
 * the same reason the go-to arrow rides in the message cell.
 *
 * The arrow's place is held whether or not there is one to draw, so the
 * counts stay in a column.
 *
 * @param cell The cell to render.
 * @param available The result sets still on show.
 *
 * @returns The cell's content.
 */
export const formatRowsCell = (
    cell: CellComponent,
    available: ReadonlySet<string>,
): HTMLElement => {
    const row = cell.getRow().getData() as IOutputRow;
    const content = document.createElement("div");
    content.className = "outputRowsContent";

    const count = document.createElement("span");
    count.textContent = row.rows === undefined ? "" : String(row.rows);
    content.appendChild(count);

    const slot = document.createElement("span");
    slot.className = "outputJumpSlot";
    const button = createJumpButton(row, available);
    if (button) {
        slot.appendChild(button);
    }
    content.appendChild(slot);

    return content;
};

/**
 * Tabulator reports a click on the whole cell, but both arrows share
 * their cell with the value beside them.
 *
 * @param event The click Tabulator reported.
 * @param selector The control the click has to have landed on.
 *
 * @returns Whether it did.
 */
export const clickedOn = (event: unknown, selector: string): boolean => {
    const target = (event as Event | undefined)?.target;

    return target instanceof Element && target.closest(selector) !== null;
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
            formatter: formatMessageCell,
            cellClick: (event, cell) => {
                const row = cell.getRow().getData() as IOutputRow;
                if (row.source && clickedOn(event, ".goToStatement")) {
                    onGoTo(row);
                }
            },
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
            width: 90,
            headerSort: false,
            cssClass: "outputRows",
            headerTooltip: "Rows returned, or rows affected",
            formatter: (cell) => {
                return formatRowsCell(cell, available);
            },
            cellClick: (event, cell) => {
                const row = cell.getRow().getData() as IOutputRow;
                if (row.resultId !== undefined
                    && available.has(row.resultId)
                    && clickedOn(event, ".jumpToResult")) {
                    onJump(row.resultId);
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
