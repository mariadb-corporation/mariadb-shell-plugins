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

/**
 * The messages and payloads exchanged between the extension and the result
 * panel's webview. Both sides import this file, so the shapes cannot drift
 * apart.
 */

import type { ValueDisplay } from "../sql/dataTypes.js";
import type { IEditableRow } from "./changes.js";

/** How a value is held in the grid, which decides how it is opened. */
export type ValueKind = "text" | "json" | "binary";

/** One column of a result set, with what the grid needs to edit it. */
export interface IResultColumn {
    name: string;
    /** The MariaDB column type, where it is known. */
    datatype?: string;
    /**
     * How its values are shown where that is not as text: hex, or an
     * icon standing for a BLOB, a spatial value or a vector.
     */
    display?: ValueDisplay;
    /** True when the column is part of the primary key. */
    isPrimary?: boolean;
    /**
     * True for a generated (virtual or stored) column, which the server
     * computes and refuses to be given a value for.
     */
    isGenerated?: boolean;
    /**
     * True for an AUTO_INCREMENT column. Unlike a generated one it can be
     * written: edited like any column, and left out of an INSERT only
     * where the new row leaves it empty, for the server to assign.
     */
    isAutoIncrement?: boolean;
    nullable?: boolean;
}

/** The table a result set can be written back to. */
export interface IResultTarget {
    schema?: string;
    table: string;
}

/**
 * Which page of its statement's rows a result set holds. Present only on
 * one the server paged - a SELECT without a LIMIT of its own - so a
 * result set without it holds every row there is.
 */
export interface IResultPage {
    /** Which page, from 0. */
    index: number;
    /** How many rows a page holds. */
    size: number;
    /** True when there are rows after this page. */
    hasMore: boolean;
    /**
     * Counts the times the page was fetched. The same page fetched again
     * - after an apply - is new rows, and the grid's edits are rebuilt
     * from it.
     */
    loads: number;
}

/** One result set, shown as one tab. */
export interface IResultSet {
    /** Stable within one execution, used as the tab key. */
    id: string;
    caption: string;
    /** The statement that produced it. */
    statement: string;
    columns: IResultColumn[];
    rows: Array<Record<string, unknown>>;
    /** True when the grid may be edited and written back. */
    editable: boolean;
    /** Why editing is off, shown in the grid's header. */
    readOnlyReason?: string;
    target?: IResultTarget;
    status: string;
    /** Which page of the rows this is, where the server paged them. */
    page?: IResultPage;
}

/** Where a statement sits in the file it came from. */
export interface IStatementSource {
    /** The document, as `Uri.toString()` renders it. */
    uri: string;
    /** Zero based line of the statement's first character. */
    line: number;
    /** Zero based character on that line. */
    character: number;
}

/**
 * What an action row stands for: one execution, one statement of it, or
 * one other thing that happened on the connection.
 *
 * The grid is a tree of the first over the second, so a run reads as one
 * line that can be opened rather than as a block of lines to be picked
 * apart by eye. An `event` - a connection opened, a schema listed - has
 * nothing under it and is a line of its own.
 */
export type ActionRole = "run" | "statement" | "event" | "warning";

/**
 * How an action row is marked. `pending` is a run that has been started
 * and has not reported back yet; the other three are the Problems
 * panel's own levels.
 */
export type ActionSeverity = "pending" | "info" | "warning" | "error";

/**
 * One row of the actions grid: one execution, one statement of one, or
 * one other event on the connection.
 *
 * Actions accumulate across executions, so a row has to carry enough to
 * stand on its own once the run that produced it is long past.
 */
export interface IActionRow {
    /** Unique across every run, so the grid can key on it. */
    id: string;
    /** When the run started, to the millisecond. */
    time: string;
    /** The connection the statement ran on. */
    connection: string;
    /**
     * Which of the connections open on that URI it ran on: an index
     * (`1`, `2`) or a name (`UI Backend`). This is what the Conn column
     * shows, and what the session picker filters on, so a statement row
     * carries the same label its run does.
     */
    connectionLabel?: string;
    /** The statement, shortened to fit. Empty on a run row. */
    statement: string;
    /** What the server said: a status line, or an error. */
    message: string;
    /**
     * What a run came to, shown in the Information column where a
     * statement row shows its statement. Set on a run row only.
     */
    summary?: string;
    /**
     * How the row is marked, with the same levels the Problems panel
     * uses. A statement that succeeded but raised warnings is a warning,
     * not plain information, and a run row takes the worst of what its
     * statements saw.
     */
    kind: ActionSeverity;
    /**
     * Whether this is a run, one of its statements, one of a statement's
     * warnings, or an event.
     */
    role: ActionRole;
    /**
     * The rows nested under this one: the statements of a run, and the
     * warnings of a statement. A run that has not reported back yet
     * carries an empty array rather than nothing, so its row keeps the
     * expander - and its place in the column - once the statements
     * arrive.
     */
    children?: IActionRow[];
    /**
     * How long this took, in milliseconds: the statement's own time for a
     * statement row, and the whole run's for a run row. Absent where the
     * server did not report one, and while a run is still pending.
     */
    elapsedMs?: number;
    /**
     * Where the statement is in the file, so the row can take the user
     * there. Absent when the run had no editor behind it.
     */
    source?: IStatementSource;
    /**
     * An action row to scroll to as well as jumping to the source: set on
     * the row of a run that failed, pointing at its first error. The
     * grid opens the run to get there.
     */
    jumpToRowId?: string;
    /**
     * The result set this statement produced. The tab may since have
     * been replaced by a later run, which is why the grid checks before
     * offering to jump to it.
     */
    resultId?: string;
}

/**
 * One of the connections open on a connection URI, as the session picker
 * offers it.
 *
 * A connection that has been closed stays in the list for as long as its
 * actions do: the log outlives the connection it was gathered on.
 */
export interface IConnectionSession {
    /** `1`, `2`, or a name like `UI Backend`. */
    label: string;
    /** False once it has been closed, its actions still being there. */
    open: boolean;
}

/** Everything one execution produced. */
export interface IExecutionReport {
    /** The connection the script ran on. */
    connection: string;
    /** When the execution started, to the millisecond. */
    startedAt: string;
    /** How long the whole execution took, in milliseconds. */
    elapsedMs: number;
    /**
     * The run's own row, with its statements as children. It carries the
     * id the pending row was started under, so it replaces it rather
     * than being appended beside it.
     */
    actions: IActionRow[];
    resultSets: IResultSet[];
}

/** What the view shows for one connection. */
export interface IViewState {
    /** Every connection to choose from, for the picker in the page. */
    connections: string[];
    /** The connection whose actions and results are on show. */
    connection: string;
    /**
     * The connections open on it, for the second picker. Several can be
     * open at once on one URI - the Connections view browses on its own
     * one while an editor runs on another.
     */
    sessions: IConnectionSession[];
    /**
     * Which of them is on show, or undefined for all of them together -
     * which is what the view opens on, and the only case in which the
     * actions name a connection per row.
     */
    session?: string;
    /**
     * What happened on it, newest first: a row per run, each holding
     * its statements, and a row per other event. Already filtered to
     * the chosen connection, so the page shows what it is given.
     */
    actions: IActionRow[];
    /** Its result sets, replaced by each execution. */
    resultSets: IResultSet[];
    /**
     * True in a result set's own editor tab, which holds that one result
     * set and none of the view's pickers or tabs.
     */
    maximized?: boolean;
    /**
     * Whether a result set's primary key columns start out frozen - the
     * extension's setting. Each result set can switch it for itself.
     */
    freezeKeyColumns?: boolean;
}

/**
 * A change the user made to one row of an editable result set.
 *
 * `rowIndex` is the row's position in the grid. It travels with the change
 * so that a statement shown in the SQL preview can be traced back to the
 * row that produced it, which is what makes the preview's lines clickable.
 */
export type RowChange =
    | {
        kind: "update";
        rowIndex: number;
        keys: Record<string, unknown>;
        values: Record<string, unknown>;
    }
    | { kind: "insert"; rowIndex: number; values: Record<string, unknown> }
    | { kind: "delete"; rowIndex: number; keys: Record<string, unknown> };

/** One statement the pending changes would run, and where it came from. */
export interface IGeneratedStatement {
    /** The grid row this statement was generated from. */
    rowIndex: number;
    sql: string;
}

/** Messages the extension sends to the webview. */
export type HostMessage =
    | {
        type: "state";
        state: IViewState;
        /**
         * Pending edits to start a result set from, by its id, in place of
         * its rows as they came: what a result set moved between the
         * panel and an editor tab was part way through.
         */
        editing?: Record<string, IEditableRow[]>;
    }
    | {
        type: "applied";
        resultId: string;
        statements: string[];
        /** Set when the apply failed, shown under the failing statement. */
        error?: string;
        /** The index of the statement that failed, where it is known. */
        failedIndex?: number;
    }
    /** Another page could not be fetched; the one on show stays. */
    | { type: "pageFailed"; resultId: string; error: string }
    /**
     * The file asked for by `loadValue`, as hex. Neither `value` nor
     * `error` means the user cancelled.
     */
    | {
        type: "valueLoaded";
        requestId: string;
        value?: string;
        error?: string;
    }
    /**
     * A value opened in an editor was saved: put it into its cell, and
     * answer with `valueEditResult`. `pageKey` is the page the value was
     * opened from, so a cell whose page has since changed is refused.
     */
    | {
        type: "valueEdited";
        requestId: string;
        resultId: string;
        rowIndex: number;
        column: string;
        pageKey: string;
        value: string;
    };

/** Messages the webview sends to the extension. */
export type WebviewMessage =
    | { type: "ready" }
    | { type: "applyChanges"; resultId: string; changes: RowChange[] }
    | { type: "refresh"; resultId: string }
    /**
     * Move a result set out of the panel into an editor tab of its own,
     * with the edits it has pending.
     */
    | { type: "maximize"; resultId: string; rows: IEditableRow[] }
    /** Fetch another page of a result set's rows, from 0. */
    | { type: "page"; resultId: string; page: number }
    /** Close a result set: its tab in the panel, or its editor tab. */
    | { type: "closeResult"; resultId: string }
    /** Put a maximized result set back into the panel. */
    | { type: "minimize"; resultId: string; rows: IEditableRow[] }
    /** Put the cursor on the statement an action row came from. */
    | { type: "revealStatement"; source: IStatementSource }
    /** Show another connection's actions and results. */
    | { type: "selectConnection"; connection: string }
    /**
     * Narrow the actions to one of the connections open on it, or show
     * all of them together when the session is left out.
     */
    | { type: "selectSession"; session?: string }
    | { type: "copyToClipboard"; text: string }
    /**
     * Save a cell's value to a file the user picks. `value` is as the
     * grid holds it - hex for a binary value - and `name` what the file
     * is called, without an extension; one is guessed from the content.
     */
    | { type: "saveValue"; value: string; name: string }
    /** Ask the user for a file, answered by `valueLoaded`. */
    | { type: "loadValue"; requestId: string }
    /**
     * Open a cell's value in an editor: beside a maximized result set, or
     * as a tab of its own from the panel. `value` is as the grid holds it.
     */
    | {
        type: "openValue";
        resultId: string;
        rowIndex: number;
        column: string;
        pageKey: string;
        value: string | null;
        kind: ValueKind;
        name: string;
        readOnly: boolean;
    }
    /** Whether a `valueEdited` could be put into its cell, and why not. */
    | { type: "valueEditResult"; requestId: string; error?: string };
