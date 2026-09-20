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

/** One column of a result set, with what the grid needs to edit it. */
export interface IResultColumn {
    name: string;
    /** The MariaDB column type, where it is known. */
    datatype?: string;
    /** True when the column is part of the primary key. */
    isPrimary?: boolean;
    /** True when the server fills the column in itself. */
    isGenerated?: boolean;
    nullable?: boolean;
}

/** The table a result set can be written back to. */
export interface IResultTarget {
    schema?: string;
    table: string;
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

/** What an output row stands for. */
export type OutputRowRole = "start" | "statement" | "finish";

/** How an output row is marked. */
export type OutputSeverity = "info" | "warning" | "error";

/**
 * One row of the output grid: what one statement of one execution did.
 *
 * Output accumulates across executions, so a row has to carry enough to
 * stand on its own once the run that produced it is long past.
 */
export interface IOutputRow {
    /** Unique across every run, so the grid can key on it. */
    id: string;
    /** When the run started, to the millisecond. */
    time: string;
    /** The connection the statement ran on. */
    connection: string;
    /** The statement, shortened to fit. */
    statement: string;
    /** What the server said: a status line, or an error. */
    message: string;
    /**
     * How the row is marked, with the same three levels the Problems
     * panel uses. A statement that succeeded but raised warnings is a
     * warning, not plain information.
     */
    kind: OutputSeverity;
    /** Whether this is the run's opening line, a statement, or its last. */
    role: OutputRowRole;
    /** Rows returned by a query, or rows affected by anything else. */
    rows?: number;
    /**
     * How long this took, in milliseconds: the statement's own time for a
     * statement row, and the whole run's for the closing row. Absent
     * where the server did not report one.
     */
    elapsedMs?: number;
    /**
     * Where the statement is in the file, so the row can take the user
     * there. Absent when the run had no editor behind it.
     */
    source?: IStatementSource;
    /**
     * An output row to scroll to as well as jumping to the source: set on
     * the closing row of a run that failed, pointing at its first error.
     */
    jumpToRowId?: string;
    /**
     * The result set this statement produced. The tab may since have
     * been replaced by a later run, which is why the grid checks before
     * offering to jump to it.
     */
    resultId?: string;
}

/** Everything one execution produced. */
export interface IExecutionReport {
    /** The connection the script ran on. */
    connection: string;
    /** When the execution started, to the millisecond. */
    startedAt: string;
    /** How long the whole execution took, in milliseconds. */
    elapsedMs: number;
    output: IOutputRow[];
    resultSets: IResultSet[];
}

/** What the view shows for one connection. */
export interface IViewState {
    /** Every connection to choose from, for the picker in the page. */
    connections: string[];
    /** The connection whose output and results are on show. */
    connection: string;
    /** Every output row for it, oldest first, across all its runs. */
    output: IOutputRow[];
    /** Its result sets, replaced by each execution. */
    resultSets: IResultSet[];
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
    | { type: "state"; state: IViewState }
    | { type: "running"; connection: string }
    | {
        type: "applied";
        resultId: string;
        statements: string[];
        /** Set when the apply failed, shown under the failing statement. */
        error?: string;
        /** The index of the statement that failed, where it is known. */
        failedIndex?: number;
    };

/** Messages the webview sends to the extension. */
export type WebviewMessage =
    | { type: "ready" }
    | { type: "applyChanges"; resultId: string; changes: RowChange[] }
    | { type: "refresh"; resultId: string }
    /** Put the cursor on the statement an output row came from. */
    | { type: "revealStatement"; source: IStatementSource }
    /** Show another connection's output and results. */
    | { type: "selectConnection"; connection: string }
    | { type: "copyToClipboard"; text: string };
