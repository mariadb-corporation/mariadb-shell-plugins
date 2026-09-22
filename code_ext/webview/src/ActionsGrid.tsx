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

import type { IActionRow } from "../../src/webview/protocol.js";
import {
    attachOverflowPopup,
    closeOverflowPopup,
} from "./overflowPopup.js";

interface IActionsGridProperties {
    /** One row per run, newest first, each holding its statements. */
    rows: IActionRow[];
    /**
     * Whether to name the connection each row happened on. Only when the
     * rows of several are on show together: with one picked, a column
     * repeating its name the whole way down says nothing.
     */
    showConnection?: boolean;
    /** The result sets whose tabs are still on show. */
    availableResultIds: ReadonlySet<string>;
    /** Switches to the tab of the result set a row produced. */
    onJumpToResult(resultId: string): void;
    /** Puts the cursor on the statement a row came from. */
    onGoToStatement(row: IActionRow): void;
    /** Puts a cut-off cell's full text on the clipboard. */
    onCopyText(text: string): void;
    /** A row to scroll into view, e.g. a run's first error. */
    scrollToRowId?: string;
}

/**
 * The codicon each severity is drawn with, as the Problems panel does -
 * plus the spinner a run is marked with until it reports back, which is
 * the same glyph VS Code spins for work in progress.
 */
const SEVERITY_ICONS = {
    pending: "codicon-loading codicon-modifier-spin",
    info: "codicon-info",
    warning: "codicon-warning",
    error: "codicon-error",
} as const;

/** What the marker's tooltip says, where the kind is not the word. */
const SEVERITY_TITLES = {
    pending: "running",
    info: "info",
    warning: "warning",
    error: "error",
} as const;

/**
 * The twistie a run is opened and closed with. VS Code's own chevrons,
 * supplied to Tabulator in place of the boxed +/- its theme draws in
 * hard coded greys.
 */
const EXPAND_ELEMENT =
    "<span class=\"treeToggle codicon codicon-chevron-right\"></span>";

/** Its counterpart on an open run. */
const COLLAPSE_ELEMENT =
    "<span class=\"treeToggle codicon codicon-chevron-down\"></span>";

/**
 * How far a statement is indented under its run, in pixels.
 *
 * Tabulator gives a child's branch element 7px of width and 5px of
 * margin on top of this, so 20 puts a statement's marker at 32 - one
 * clear step of `TREE_ELEMENT_WIDTH` in from the 16 its run's marker
 * sits at. The markers of the rows that stand on their own line up;
 * the ones that belong to a run are meant not to.
 */
const CHILD_INDENT = 20;

/**
 * The room a run's twistie takes, which a row that has neither twistie
 * nor branch - an event - has to be given, or its marker would sit
 * where every other row's message does.
 */
const TREE_ELEMENT_WIDTH = 16;

/**
 * Builds the severity marker: the same glyph the Problems panel marks a
 * line with, which is what tells the rows apart now that they all share
 * one background.
 *
 * @param row The row to mark.
 *
 * @returns The marker.
 */
export const createSeverityIcon = (row: IActionRow): HTMLElement => {
    const icon = document.createElement("span");
    icon.className =
        `markerIcon ${row.kind} codicon ${SEVERITY_ICONS[row.kind]}`;
    icon.title = SEVERITY_TITLES[row.kind];

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
    row: IActionRow,
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
 * On the row of a failed run it also opens the run and carries it to
 * its first error, which is why its title differs.
 *
 * @param row The row the cell belongs to.
 *
 * @returns The button, or undefined where the row has no source.
 */
export const createGoToButton = (
    row: IActionRow,
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
 * Renders the message cell: what happened, with both arrows at the end
 * of it.
 *
 * They ride here rather than in columns of their own because a column of
 * arrows is width the message could be using instead, and the panel area
 * is short of it. The message truncates before them: they are laid out
 * beside it rather than over it, so neither can cover the other.
 *
 * @param cell The cell to render.
 * @param available The result sets still on show.
 *
 * @returns The cell's content.
 */
export const formatMessageCell = (
    cell: CellComponent,
    available: ReadonlySet<string>,
    onCopy: (text: string) => void,
): HTMLElement => {
    const row = cell.getRow().getData() as IActionRow;
    const host = document.createElement("div");
    host.className = "actionMessageCell";

    // Tabulator has already put the twistie of a run, or the branch of
    // a statement, in front of this. An event has neither, so it is
    // given the room they take: the markers stay in one column.
    if (row.role === "event") {
        const spacer = document.createElement("span");
        spacer.className = "treeSpacer";
        spacer.style.width = `${TREE_ELEMENT_WIDTH}px`;
        host.appendChild(spacer);
    }

    host.appendChild(createSeverityIcon(row));

    const content = document.createElement("div");
    content.className = "actionMessageContent";
    content.textContent = row.message;
    // An error wraps instead - the whole of it is already on screen, and
    // there is nothing for a popup to add.
    if (row.kind !== "error") {
        attachOverflowPopup(content, { text: row.message, onCopy });
    }
    host.appendChild(content);

    const actions = document.createElement("span");
    actions.className = "actionArrows";
    const jump = createJumpButton(row, available);
    if (jump) {
        actions.appendChild(jump);
    }
    const goTo = createGoToButton(row);
    if (goTo) {
        actions.appendChild(goTo);
    }
    if (actions.childElementCount > 0) {
        host.appendChild(actions);
    }

    return host;
};

/**
 * What the Information column says: a statement shows the statement it
 * ran, and a run what it came to.
 *
 * @param row The row to describe.
 *
 * @returns The text for the cell.
 */
export const informationOf = (row: IActionRow): string => {
    return row.role === "run" ? row.summary ?? "" : row.statement;
};

/**
 * Renders the Information cell.
 *
 * It is built as an element rather than returned as a string because
 * Tabulator writes a formatter's string into the cell as HTML, and this
 * column holds SQL and server messages.
 *
 * @param cell The cell to render.
 *
 * @returns The cell's content.
 */
export const formatInformationCell = (
    cell: CellComponent,
    onCopy: (text: string) => void,
): HTMLElement => {
    const row = cell.getRow().getData() as IActionRow;
    const content = document.createElement("span");
    content.className = "actionInformationContent";
    content.textContent = informationOf(row);
    attachOverflowPopup(content, { text: informationOf(row), onCopy });

    return content;
};

/**
 * Tabulator reports a click on the whole row, but the jump arrow shares
 * it with everything else and means something narrower.
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

/** What a click on a row of the actions asks for. */
export type ActionClick = "jump" | "goTo" | undefined;

/**
 * What clicking a row means.
 *
 * The whole row is the target, as it is in the Problems panel: a row of
 * the log stands for a statement, and the thing wanted on reading one is
 * to be taken to it. The arrows stay because they say so, and because a
 * run's arrow has a title of its own; clicking one is simply a click on
 * the row, except for the jump, which goes somewhere else.
 *
 * Tabulator's twistie stops the click reaching here, so opening a run is
 * still only opening it.
 *
 * @param event The click Tabulator reported.
 * @param row The row it landed on.
 * @param available The result sets whose tabs are still on show.
 *
 * @returns What to do, or nothing where the row knows nowhere to go.
 */
export const actionClick = (
    event: unknown,
    row: IActionRow,
    available: ReadonlySet<string>,
): ActionClick => {
    if (row.resultId !== undefined
        && available.has(row.resultId)
        && clickedOn(event, ".jumpToResult")) {
        return "jump";
    }

    return row.source ? "goTo" : undefined;
};

/**
 * @param value The number of milliseconds.
 *
 * @returns The duration, in the largest unit that stays readable. Tight
 *          against its unit, because it is shown in brackets after a
 *          time of day and the column is narrow.
 */
export const formatElapsed = (value: number): string => {
    if (value < 1000) {
        return `${value}ms`;
    }

    return `${(value / 1000).toFixed(3)}s`;
};

/**
 * What the Time column says: when it started and, in brackets, how long
 * it took - `20:44:13.431 (3ms)`.
 *
 * The two belong together: one column of the pair is dead space on
 * every row that has no duration, and a run's start and its length are
 * read as one fact.
 *
 * @param row The row to describe.
 *
 * @returns The text for the cell.
 */
export const timeOf = (row: IActionRow): string => {
    // Nothing in brackets while a run is still under way: it has no
    // time yet, and a 0ms would claim it had.
    return row.elapsedMs === undefined
        ? row.time
        : `${row.time} (${formatElapsed(row.elapsedMs)})`;
};

/**
 * Builds the actions grid's columns.
 *
 * @param available The result sets still on show.
 * @param onJump Switches to a result set's tab.
 * @param onGoTo Puts the cursor on the statement a row came from.
 * @param showConnection Whether to name the connection each row is on.
 *
 * @returns The column definitions.
 */
export const buildActionColumns = (
    available: ReadonlySet<string>,
    onCopy: (text: string) => void,
    showConnection = false,
): ColumnDefinition[] => {
    return [
        {
            title: "Actions",
            field: "message",
            headerSort: false,
            widthGrow: 3,
            cssClass: "actionMessage",
            // An error is the one thing that is not cut off: the row
            // grows to hold it, which is what variableHeight is for.
            variableHeight: true,
            formatter: (cell) => {
                return formatMessageCell(cell, available, onCopy);
            },
        },
        {
            title: "Time",
            field: "time",
            width: 152,
            vertAlign: "middle",
            headerSort: false,
            cssClass: "actionTime",
            headerTooltip:
                "When it started and, in brackets, how long it took - the "
                + "statement's own time, or the whole run's on its own row",
            formatter: (cell) => {
                return timeOf(cell.getRow().getData() as IActionRow);
            },
        },
        {
            title: "Information",
            field: "statement",
            width: 260,
            vertAlign: "middle",
            headerSort: false,
            cssClass: "actionInformation",
            headerTooltip:
                "The statement that ran, or what the run came to",
            formatter: (cell) => {
                return formatInformationCell(cell, onCopy);
            },
        },
        // Last, where it labels the row without standing between the
        // marker and what it says.
        ...(showConnection
            ? [{
                title: "Conn",
                field: "connectionLabel",
                width: 76,
                vertAlign: "middle",
                headerSort: false,
                cssClass: "actionConnection",
                headerTooltip:
                    "Which connection open on this URI the row is from",
            } satisfies ColumnDefinition]
            : []),
    ];
};

/**
 * Which rows of the actions start out open.
 *
 * The newest run, and inside it every statement carrying warnings. A
 * warning the reader has to go looking for is a warning nobody reads,
 * and the count in the statement's own message is no use without the
 * text behind it. Nothing else opens, so a run still closes behind the
 * one that follows it.
 *
 * The newest run is looked for rather than taken from the front of the
 * list, because the row in front may be an event - the connection a run
 * opened on the way is reported after the run's own row went up, and so
 * sits above it.
 *
 * @param rows The actions, newest first.
 *
 * @returns The ids to open, empty where nothing has run.
 */
export const expandedIdsOf = (rows: IActionRow[]): Set<string> => {
    const latest = rows.find((row) => {
        return row.role === "run";
    });
    if (!latest) {
        return new Set();
    }

    const open = new Set([latest.id]);
    for (const statement of latest.children ?? []) {
        if ((statement.children?.length ?? 0) > 0) {
            open.add(statement.id);
        }
    }

    return open;
};

/**
 * Opens the rows above, and closes the rest.
 *
 * Tabulator asks this for every row it builds, and it rebuilds them all
 * on every data change, so the answer is read fresh each time.
 *
 * @param rowId The row Tabulator is building.
 * @param expandedIds What `expandedIdsOf` said to open.
 *
 * @returns Whether that row starts out open.
 */
export const startsExpanded = (
    rowId: unknown,
    expandedIds: ReadonlySet<string>,
): boolean => {
    return typeof rowId === "string" && expandedIds.has(rowId);
};

/**
 * @param rows The actions, newest first.
 * @param rowId The statement row to place.
 *
 * @returns The id of the run holding it, if any run does.
 */
export const runHolding = (
    rows: IActionRow[],
    rowId: string,
): string | undefined => {
    return rows.find((run) => {
        return run.children?.some((child) => {
            return child.id === rowId;
        }) ?? false;
    })?.id;
};

/**
 * Finds a row of the tree, opening the run it belongs to.
 *
 * Tabulator looks rows up by index among the top level only, so a
 * statement cannot be asked for by its own id: it is reached through its
 * run, which has to be open for it to be a row at all.
 *
 * @param instance The table to look in.
 * @param runId The run the row belongs to, or is.
 * @param rowId The row to find.
 *
 * @returns The row, if the tree still holds it.
 */
const openTo = (
    instance: Tabulator,
    runId: string,
    rowId: string,
): RowComponent | undefined => {
    const run = instance.getRows().find((candidate) => {
        return candidate.getIndex() === runId;
    });
    if (!run || rowId === runId) {
        return run;
    }

    run.treeExpand();

    return run.getTreeChildren().find((candidate) => {
        return candidate.getIndex() === rowId;
    });
};

/**
 * The Actions tab: one row per execution, newest first, holding a row
 * per statement of it.
 *
 * Unlike the result grids, this one accumulates - it is the log of
 * what has happened on this connection - so what has just happened is
 * put at the top, where the view already is, and only the newest run
 * is left open.
 *
 * @param props The rows to show.
 *
 * @returns The rendered grid.
 */
export const ActionsGrid = (props: IActionsGridProperties): JSX.Element => {
    const { rows, availableResultIds, scrollToRowId, showConnection } = props;
    const host = useRef<HTMLDivElement>(null);
    const table = useRef<Tabulator | undefined>(undefined);
    const built = useRef(false);
    const pendingRows = useRef<IActionRow[] | undefined>(undefined);
    // Held in a ref so the callbacks Tabulator keeps never go stale.
    const callbacks = useRef(props);
    callbacks.current = props;
    // Read by Tabulator as it builds each row, long after this render.
    const expandedIds = useRef<ReadonlySet<string>>(new Set());
    expandedIds.current = expandedIdsOf(rows);

    useLayoutEffect(() => {
        if (!host.current) {
            return;
        }

        const instance = new Tabulator(host.current, {
            data: [...rows],
            columns: buildActionColumns(
                availableResultIds,
                (text) => {
                    callbacks.current.onCopyText(text);
                },
                showConnection,
            ),
            index: "id",
            layout: "fitColumns",
            // No header: the columns are self evident from what is in
            // them, and the panel area is short enough that a row of
            // titles is a row of the log not shown.
            headerVisible: false,
            // Less the 2px of margin above it, or the grid would be
            // that much taller than the room it has.
            height: "calc(100% - 2px)",
            placeholder: "Nothing has been run on this connection yet.",
            dataTree: true,
            dataTreeChildField: "children",
            // Everything a row leads with is in this one cell: the
            // twistie or the branch Tabulator puts in front of it, then
            // the marker, then what happened.
            dataTreeElementColumn: "message",
            dataTreeChildIndent: CHILD_INDENT,
            dataTreeExpandElement: EXPAND_ELEMENT,
            dataTreeCollapseElement: COLLAPSE_ELEMENT,
            dataTreeStartExpanded: (row) => {
                return startsExpanded(row.getIndex(), expandedIds.current);
            },
            rowFormatter: (row) => {
                const data = row.getData() as IActionRow;
                const element = row.getElement();
                // Only the text colour and the marker icon vary; every
                // row shares one background.
                element.classList.toggle("errorRow", data.kind === "error");
                element.classList.toggle(
                    "warningRow", data.kind === "warning");
                element.classList.toggle("runRow", data.role === "run");
                // Only a row that knows where to go says so by the
                // cursor; the rest are text.
                element.classList.toggle("goesSomewhere",
                    data.source !== undefined);
            },
        });

        // On the row rather than on a column's `cellClick`, so that any
        // part of it is the target - the time and the statement as much
        // as the message.
        instance.on("rowClick", (event, row) => {
            const data = row.getData() as IActionRow;
            switch (actionClick(event, data, availableResultIds)) {
                case "jump": {
                    callbacks.current.onJumpToResult(data.resultId as string);
                    break;
                }

                case "goTo": {
                    callbacks.current.onGoToStatement(data);
                    break;
                }
            }
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
            // The popup is a child of the body, not of the cell it
            // points at, so it would outlive the grid it belongs to.
            closeOverflowPopup();
            try {
                instance.destroy();
            } catch {
                // Destroying a table that never finished building throws
                // rather than being a no-op.
            }
        };
        // The columns close over which result sets are still available
        // and over whether the connection is named, so the table is
        // rebuilt when either changes.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [availableResultIds, showConnection]);

    useEffect(() => {
        const instance = table.current;
        if (!instance || !built.current || scrollToRowId === undefined) {
            return;
        }

        // The run asked about here is usually a closed one - it is the
        // error of an older run that is being pointed at - so it is
        // opened on the way.
        const runId = runHolding(rows, scrollToRowId);
        if (runId === undefined) {
            return;
        }

        try {
            const row = openTo(instance, runId, scrollToRowId);
            if (row) {
                void instance.scrollToRow(row, "center", false);
            }
        } catch {
            // The run has scrolled out of the capped history.
        }
        // The run to open is looked up in the actions as they now stand.
        // eslint-disable-next-line react-hooks/exhaustive-deps
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

        // Nothing is scrolled to. The newest row is the first one, and
        // the view opens at the top: a reader who has not gone looking
        // through the older rows is already where the new ones arrive,
        // and one who has is not taken away from what they are
        // reading.
        void instance.replaceData([...rows]);
    }, [rows]);

    return <div class="actionsGridHost" ref={host} />;
};
