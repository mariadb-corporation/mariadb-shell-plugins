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

import type {
    IColumnDetails,
    IColumnMetadata,
    IMariaDbApi,
    IResultSetData,
    IStatementResult,
} from "../mcp/types.js";
import type {
    IExecutionReport,
    IActionRow,
    IResultColumn,
    IResultPage,
    IResultSet,
    IStatementSource,
    ActionSeverity,
    RowChange,
} from "../webview/protocol.js";
import { ObjectNotFoundError } from "../mcp/mariaDbApi.js";
import { valueDisplayOf } from "./dataTypes.js";
import { createQueryBuilder } from "./resultSetQueryBuilder.js";
import { type ISqlStatement, splitStatements } from "./splitStatements.js";
import { findUpdatableTarget } from "./statementTarget.js";
import { counted, errorText } from "../text.js";

/**
 * Describes one statement's outcome in the words the client would use.
 *
 * @param result The statement's result.
 *
 * @returns The status line for it.
 */
export const describeResult = (result: IStatementResult): string => {
    const warningCount = result.warnings_count ?? 0;
    const warnings = warningCount > 0
        ? `, ${counted(warningCount, "warning")}`
        : "";

    // Each set has a row of its own under this one saying what is in it.
    const sets = result.result_sets ?? [];
    if (sets.length > 1) {
        return `${sets.length} result sets${warnings}`;
    }

    if (sets.length === 1) {
        return `${rowsInSet(sets[0].rows.length)}${warnings}`;
    }

    const affected = result.affected_items_count ?? 0;

    return `Query OK, ${counted(affected, "row")} affected`
        + warnings;
};

/**
 * Turns the warnings a statement produced into rows under its own.
 *
 * @param result The statement's result.
 * @param statementId The id of the row they hang under, which its own
 *                    ids are built from so they are unique across runs.
 * @param row What every row of this run carries: when, where and on what.
 *
 * @returns One row per warning, or an empty array where there were none.
 *
 * A shell that reports `warnings_count` but not the texts - which is
 * every shell that predates them - produces nothing here, and the
 * statement row keeps its count in its message and loses only the
 * expander.
 */
export const warningRowsOf = (
    result: IStatementResult,
    statementId: string,
    row: Pick<IActionRow, "time" | "connection" | "connectionLabel"
        | "source">,
): IActionRow[] => {
    return (result.warnings ?? []).map((warning, position): IActionRow => {
        return {
            ...row,
            id: `${statementId}-warning-${position}`,
            role: "warning",
            // The level is the server's own word for it and goes where a
            // statement puts its SQL, so the message column is left to
            // the text - which is the thing being read.
            statement: `${warning.level} ${warning.code}`,
            message: warning.message,
            kind: warning.level.toLowerCase() === "error"
                ? "error"
                : "warning",
        };
    });
};

/**
 * @param count How many rows a result set has.
 *
 * @returns `1 row in set`, `4 rows in set`.
 */
const rowsInSet = (count: number): string => {
    return `${counted(count, "row")} in set`;
};

/**
 * What a result set's own bar says about it: its rows, and which page
 * they are where there is more than one.
 *
 * @param count How many rows it holds.
 * @param page Which page they are, where the server paged them.
 *
 * @returns `200 rows in set`, `200 rows in set (page 2)`.
 */
export const pageStatus = (count: number, page?: IResultPage): string => {
    return page !== undefined && (page.index > 0 || page.hasMore)
        ? `${rowsInSet(count)} (page ${page.index + 1})`
        : rowsInSet(count);
};

/**
 * Whether a statement calls a stored procedure, whose result sets are
 * whatever it chose to SELECT: none of them is a table's rows to edit.
 *
 * @param statement The statement, comments and all.
 *
 * @returns True for a CALL.
 */
export const isProcedureCall = (statement: string): boolean => {
    return /^call\b/i.test(dropLeadingComments(statement).trimStart());
};

/**
 * Formats a wall-clock time to the millisecond.
 *
 * The actions grid keeps rows from every run, so a row has to say when it
 * happened precisely enough to tell two runs a second apart apart.
 *
 * @param when The moment to format.
 *
 * @returns The time as `HH:MM:SS.mmm`.
 */
export const formatTime = (when: Date): string => {
    const pad = (value: number, width = 2): string => {
        return String(value).padStart(width, "0");
    };

    return `${pad(when.getHours())}:${pad(when.getMinutes())}`
        + `:${pad(when.getSeconds())}.${pad(when.getMilliseconds(), 3)}`;
};

/**
 * Names what a run is about to do, for the run's own row.
 *
 * @param count How many statements will actually be run.
 * @param label What the caller wants the run called instead.
 *
 * @returns The phrase the run's row is built around.
 */
export const describeStatementCount = (
    count: number,
    label?: string,
): string => {
    if (label !== undefined) {
        return label;
    }

    return count === 1 ? "1 statement" : `${count} statements`;
};

/**
 * The statements of a script the server actually runs.
 *
 * A comment is one of the statements the server splits out and takes up
 * an index among them, but it is not one the server runs and there is no
 * result for it. Counting it would make a file that opens with a
 * connection header - which every generated one does - report one
 * statement more than it has.
 *
 * @param statements The script, split.
 *
 * @returns The ones with SQL in them.
 */
const executableOf = (statements: ISqlStatement[]): ISqlStatement[] => {
    return statements.filter((statement) => {
        return statement.executable;
    });
};

/**
 * The same phrase, worked out from the split script.
 *
 * This is what a caller uses to open the run's row before it has a
 * connection to run on, which is the whole point of the row: it appears
 * the moment the user asks for it, not once the server answers.
 *
 * @param statements The SQL about to be run, split.
 * @param label What the caller wants the run called instead.
 *
 * @returns The phrase the run's row is built around.
 */
export const describeStatements = (
    statements: ISqlStatement[],
    label?: string,
): string => {
    return describeStatementCount(executableOf(statements).length, label);
};

/**
 * The same phrase, worked out from the script itself.
 *
 * @param script The SQL about to be run.
 * @param label What the caller wants the run called instead.
 *
 * @returns The phrase the run's row is built around.
 */
export const describeRun = (script: string, label?: string): string => {
    return describeStatements(splitStatements(script), label);
};

/**
 * Builds the row a run is shown by while it is still running.
 *
 * It carries the run's id, so the finished run replaces it rather than
 * being appended beside it, and an empty child array, so the row already
 * has its expander and its text does not shift when the statements
 * arrive under it.
 *
 * @param options The run this row stands for.
 *
 * @returns The row to put in the actions.
 */
export const pendingRunRow = (options: {
    runId: string;
    connectionUri: string;
    /** Which connection open on it the run is on: `1`, `UI Backend`. */
    connectionLabel?: string;
    /** What the run is called, from `describeRun()`. */
    what: string;
    /** When it started; now, unless a test says otherwise. */
    when?: Date;
}): IActionRow => {
    return {
        id: options.runId,
        time: formatTime(options.when ?? new Date()),
        connection: options.connectionUri,
        connectionLabel: options.connectionLabel,
        role: "run",
        statement: "",
        message: `Running ${options.what} on ${options.connectionUri}`,
        summary: "Running\u2026",
        kind: "pending",
        children: [],
    };
};

/**
 * Drops the comments a statement opens with.
 *
 * A caption is there to say which statement a tab holds, and a leading
 * comment says nothing about that - a file opened from the Connections
 * view starts with one naming its connection, which would otherwise be
 * the caption of its first result.
 *
 * @param statement The statement text.
 *
 * @returns The statement from its first line of SQL onwards.
 */
export const dropLeadingComments = (statement: string): string => {
    let rest = statement.trimStart();

    for (;;) {
        if (/^--[\s]/.test(rest) || rest.startsWith("#")) {
            const end = rest.indexOf("\n");
            if (end === -1) {
                return "";
            }
            rest = rest.slice(end + 1).trimStart();
            continue;
        }

        if (rest.startsWith("/*")) {
            const end = rest.indexOf("*/");
            if (end === -1) {
                return "";
            }
            rest = rest.slice(end + 2).trimStart();
            continue;
        }

        return rest;
    }
};

/**
 * Shortens a statement so it fits on a tab.
 *
 * @param statement The statement text.
 * @param limit The longest caption to produce.
 *
 * @returns The statement on one line, truncated with an ellipsis.
 */
export const captionFor = (statement: string, limit = 40): string => {
    // A statement that is nothing but comments keeps them, since an
    // empty caption would say even less.
    const body = dropLeadingComments(statement) || statement;
    const oneLine = body.replace(/\s+/g, " ").trim();

    return oneLine.length <= limit
        ? oneLine
        : `${oneLine.slice(0, limit - 1)}…`;
};

/**
 * Maps a table's column details onto the grid's column descriptions.
 *
 * Only the columns the result set actually selected are kept, in the order
 * the result set has them, so a `SELECT a, b` over a wider table still
 * lines up with its rows.
 *
 * @param labels The result set's column labels, in order.
 * @param details The table's columns.
 * @param metadata Each column's metadata as the server reported it, in
 *                 order, which is what decides how a value is shown
 *                 where the table's columns are not known.
 *
 * @returns The grid columns.
 */
export const mapColumns = (
    labels: string[],
    details?: IColumnDetails[],
    metadata?: IColumnMetadata[],
): IResultColumn[] => {
    const byName = new Map(details?.map((column) => {
        return [column.name, column];
    }));

    return labels.map((name, position) => {
        const column = byName.get(name);
        const display = valueDisplayOf(
            metadata?.[position], column?.datatype);
        const typeName = column?.datatype
            ?? metadata?.[position]?.type?.toLowerCase();
        const shown = {
            ...display === undefined ? {} : { display },
            ...typeName === undefined ? {} : { typeName },
        };
        if (!column) {
            return { name, ...shown };
        }

        return {
            name,
            ...shown,
            datatype: column.datatype,
            isPrimary: Boolean(column.is_primary),
            // Kept apart: a generated column cannot be written, an
            // auto-increment one - a primary key, usually - can.
            isGenerated: Boolean(column.is_generated),
            isAutoIncrement: column.id_generation === "auto_inc",
            nullable: !column.not_null,
        };
    });
};

/** Why a procedure's result sets cannot be edited. */
const PROCEDURE_READ_ONLY =
    "Read only: the result set of a stored procedure.";

/**
 * A result set as it is shown before anything is known about where its
 * rows came from: read only, captioned by its place among the run's.
 *
 * @param statement The statement that produced it.
 * @param set Its columns and rows.
 * @param ordinal The index of this result set among the run's.
 * @param runId The run it belongs to.
 * @param status The status line for it.
 * @param page Which page of the rows it holds, where the server paged
 *             them.
 *
 * @returns The result set to show, or to build an editable one on.
 */
const plainResultSet = (
    statement: string,
    set: IResultSetData,
    ordinal: number,
    runId: string,
    status: string,
    page?: IResultPage,
): IResultSet => {
    return {
        id: `${runId}-result-${ordinal}`,
        caption: `Result #${ordinal + 1}`,
        statement,
        columns: mapColumns(set.columns, undefined, set.column_metadata),
        rows: set.rows,
        editable: false,
        status,
        ...(page === undefined ? {} : { page }),
    };
};

/** Where a script came from, so its statements can be jumped to. */
export interface IScriptSource {
    /** The document, as `Uri.toString()` renders it. */
    uri: string;

    /**
     * @param offsetInScript An offset within the script that was run.
     *
     * @returns Where that is in the document.
     */
    positionAt(offsetInScript: number): { line: number; character: number };
}

/** One execution. */
export interface IExecutionOptions {
    connectionUri: string;
    connectionId: string;
    /**
     * Which connection open on that URI it runs on. Every row the run
     * produces carries it, which is what the result view groups and
     * filters the actions by.
     */
    connectionLabel?: string;
    script: string;
    /**
     * Distinguishes this run's rows and result sets from every other
     * run's, so an older action row cannot link to a tab a later run
     * replaced.
     */
    runId: string;
    /**
     * The script, split, where the caller split it already - to put the
     * run's row up - so it is not split a second time. Left out, it is
     * split here.
     */
    statements?: ISqlStatement[];
    /** Where the script came from, for the jump-to-statement links. */
    source?: IScriptSource;
    /** Whether a failing statement ends the script. */
    stopOnError?: boolean;
    /**
     * How many rows a result set holds at a time. A SELECT without a
     * LIMIT of its own comes back one page long, and can be paged on.
     * Left out, every row comes back.
     */
    pageSize?: number;
    /** What to call this run in its own row. */
    label?: string;
}

/**
 * Runs SQL scripts and turns what comes back into what the result panel
 * shows.
 *
 * The heavy lifting that makes a grid editable happens here: a result set
 * that came from a plain single table SELECT has that table's columns
 * looked up, so the grid knows the primary key and can address a row.
 */
export class ExecutionService {
    public constructor(private readonly api: IMariaDbApi) { }

    /**
     * Runs a script and collects everything the view needs to show it.
     *
     * What comes back is one row for the run with a child row per
     * statement under it: the run's row says what ran, where, how long
     * all of it took and what it came to, and its children carry the
     * server's word on each statement - that statement's own time, its
     * rows and where in the file it came from.
     *
     * The row carries the run's id, so it takes the place of the pending
     * row the caller put up when the user asked for the run.
     *
     * @param options What to run, and where it came from.
     *
     * @returns The report to send to the webview.
     */
    public async execute(
        options: IExecutionOptions,
    ): Promise<IExecutionReport> {
        const { connectionUri, connectionId, script, runId } = options;
        const connectionLabel = options.connectionLabel;
        const statements = options.statements ?? splitStatements(script);
        const executable = executableOf(statements);
        const children: IActionRow[] = [];
        const resultSets: IResultSet[] = [];

        const startedAt = formatTime(new Date());
        const startedMs = Date.now();

        // What every statement row of this run carries: when, where and
        // on what. Each adds what the server said about its statement.
        const base = {
            time: startedAt,
            connection: connectionUri,
            connectionLabel,
            role: "statement" as const,
        };

        /**
         * @param index The statement's position in the script.
         *
         * @returns Where it is in the file, if the run has one behind it.
         */
        const sourceOf = (index: number): IStatementSource | undefined => {
            const statement = statements[index];
            if (!options.source || !statement) {
                return undefined;
            }

            const position = options.source.positionAt(statement.offset);

            return { uri: options.source.uri, ...position };
        };

        // Where a run as a whole points: its first real statement, not a
        // comment standing in front of it.
        const runSource = sourceOf(executable[0]?.index ?? 0);

        const what = describeStatementCount(executable.length, options.label);

        let firstErrorId: string | undefined;
        let firstErrorSource: IStatementSource | undefined;
        let errorCount = 0;

        /**
         * Closes the run off: the statements gathered so far become the
         * children of one row saying how the whole thing went.
         *
         * @param outcome What it came to, for the Information column.
         * @param kind How the run's row is marked.
         *
         * @returns The report to send to the webview.
         */
        const finish = (
            outcome: string,
            kind: ActionSeverity,
        ): IExecutionReport => {
            const elapsedMs = Date.now() - startedMs;

            return {
                connection: connectionUri,
                startedAt,
                elapsedMs,
                actions: [{
                    id: runId,
                    time: startedAt,
                    connection: connectionUri,
                    connectionLabel,
                    role: "run",
                    statement: "",
                    message: `Ran ${what} on ${connectionUri}`,
                    summary: outcome,
                    kind,
                    children,
                    // The run's own time. Each child carries its
                    // statement's, as the server measured it.
                    elapsedMs,
                    // A run that failed points at its first error; one
                    // that did not points at where it began.
                    source: firstErrorSource ?? runSource,
                    jumpToRowId: firstErrorId,
                }],
                resultSets,
            };
        };

        // Asked for at most once per execution, and only when an
        // unqualified table name actually has to be resolved.
        let currentSchemaPromise: Promise<string | undefined> | undefined;
        const currentSchema = (): Promise<string | undefined> => {
            currentSchemaPromise ??= this.#currentSchema(connectionId);

            return currentSchemaPromise;
        };

        let results: IStatementResult[];
        try {
            results = await this.api.executeScript(
                connectionId, script, options.stopOnError, options.pageSize);
        } catch (error) {
            // The call itself failed - a closed connection, a shell that
            // went away - so nothing ran and there is no statement to
            // blame.
            const message = errorText(error);
            const failure: IActionRow = {
                ...base,
                id: `${runId}-error`,
                statement: captionFor(script),
                message,
                kind: "error",
                source: runSource,
            };
            children.push(failure);
            firstErrorId = failure.id;
            firstErrorSource = failure.source;

            return finish(`Execution failed: ${message}`, "error");
        }

        for (const [position, result] of results.entries()) {
            // The server reports which statement each result belongs to.
            // Falling back to the position keeps a shell that predates
            // that working, where the two splits agree.
            const index = result.statement_index ?? position;
            const statement = statements[index]?.text ?? "";
            const source = sourceOf(index);
            const id = `${runId}-${index}`;
            const elapsedMs = result.execution_time === undefined
                ? undefined
                : Math.round(result.execution_time * 1000);

            if (result.error !== undefined) {
                errorCount += 1;
                firstErrorId ??= id;
                firstErrorSource ??= source;
                children.push({
                    ...base,
                    id,
                    statement: captionFor(statement || result.statement || ""),
                    message: result.error,
                    kind: "error",
                    elapsedMs,
                    source,
                });
                continue;
            }

            // A statement that raised warnings is worth marking, the
            // way the Problems panel marks one.
            const kind = (result.warnings_count ?? 0) > 0
                ? "warning" as const
                : "info" as const;
            // And what each of them said hangs under it, so the count in
            // the message is a row away from the text behind it.
            const warnings = warningRowsOf(result, id, { ...base, source });

            const sets = result.result_sets ?? [];
            if (sets.length === 0) {
                children.push({
                    ...base,
                    id,
                    statement: captionFor(statement),
                    message: describeResult(result),
                    kind,
                    elapsedMs,
                    source,
                    ...(warnings.length > 0 ? { children: warnings } : {}),
                });
                continue;
            }

            const resultSet = await this.#buildResultSet(
                connectionId,
                statement,
                sets[0],
                resultSets.length,
                describeResult(result),
                currentSchema,
                runId,
                options.pageSize,
            );
            resultSets.push(resultSet);

            // A procedure's other result sets: a tab each, and a row each
            // under the statement's, beside its warnings, saying what is in
            // it and jumping to it. The first set gets one too, so every
            // set is a row away.
            const setRows: IActionRow[] = [];
            if (sets.length > 1) {
                for (const [position, set] of sets.entries()) {
                    const shown = position === 0
                        ? resultSet
                        : this.#procedureResultSet(statement, set,
                            resultSets.length, runId);
                    if (position > 0) {
                        resultSets.push(shown);
                    }
                    setRows.push({
                        ...base,
                        id: `${id}-set-${position}`,
                        statement: shown.caption,
                        message: rowsInSet(set.rows.length),
                        kind: "info",
                        source,
                        resultId: shown.id,
                    });
                }
            }
            const nested = [...setRows, ...warnings];

            children.push({
                ...base,
                id,
                statement: captionFor(statement),
                message: describeResult(result),
                kind,
                elapsedMs,
                source,
                resultId: resultSet.id,
                ...(nested.length > 0 ? { children: nested } : {}),
            });
        }

        const ran = results.length;
        const stoppedEarly = errorCount > 0 && ran < executable.length;
        const warningCount = children.filter((row) => {
            return row.kind === "warning";
        }).length;

        return finish(
            errorCount === 0
                ? `Finished ${what} successfully`
                + (warningCount === 0
                    ? ""
                    : ` with ${counted(warningCount, "warning")}`)
                : `Finished with ${counted(errorCount, "error")}`
                + (stoppedEarly
                    ? `, stopped after ${ran} of ${executable.length}`
                    : ""),
            errorCount > 0
                ? "error"
                : warningCount > 0
                    ? "warning"
                    : "info",
        );
    }

    /**
     * Writes a grid's edits back to its table.
     *
     * @param connectionId The UUID to run on.
     * @param resultSet The result set that was edited.
     * @param changes The changes the user made.
     *
     * @returns The statements that were run.
     */
    public async applyChanges(
        connectionId: string,
        resultSet: IResultSet,
        changes: RowChange[],
    ): Promise<string[]> {
        const builder = createQueryBuilder(resultSet);
        if (!builder) {
            throw new Error(
                "This result set is not bound to a single table, so its "
                + "changes cannot be written back.",
            );
        }

        const statements = builder.buildStatements(changes).map((entry) => {
            return entry.sql;
        });
        if (statements.length === 0) {
            return [];
        }

        await this.api.executeScript(
            connectionId,
            `${statements.join(";\n")};`,
        );

        return statements;
    }

    /**
     * Fetches another page of a result set's rows.
     *
     * Only the rows change: the columns, the table it writes back to and
     * whether it can be edited are the statement's, and so the same on
     * every page.
     *
     * @param connectionId The UUID to run on.
     * @param resultSet The result set to page, which the server paged.
     * @param index Which page to fetch, from 0.
     *
     * @returns The result set holding that page.
     */
    public async fetchPage(
        connectionId: string,
        resultSet: IResultSet,
        index: number,
    ): Promise<IResultSet> {
        const current = resultSet.page;
        if (current === undefined) {
            throw new Error("This result set holds all of its rows.");
        }

        const page = Math.max(0, index);
        const result = await this.api.executeSql(
            connectionId,
            resultSet.statement,
            { limit: current.size, offset: page * current.size },
        );
        const set = result.result_sets?.[0];
        const rows = set?.rows ?? [];
        const next: IResultPage = {
            index: page,
            size: current.size,
            hasMore: set?.has_more_pages ?? false,
            loads: current.loads + 1,
        };

        return {
            ...resultSet,
            rows,
            page: next,
            status: pageStatus(rows.length, next),
        };
    }

    /**
     * Turns one result into a grid, looking up the source table's columns
     * where the statement allows the grid to be edited.
     *
     * @param connectionId The UUID to look the table up on.
     * @param statement The statement that produced the result.
     * @param set Its first result set - the only one a SELECT has.
     * @param ordinal The index of this result set among the others.
     * @param status The status line for it.
     * @param currentSchema Answers which schema the connection is using,
     *                      for a table named without one.
     * @param runId The run it belongs to.
     * @param pageSize How many rows the server was asked for at a time.
     *
     * @returns The result set to show.
     */
    async #buildResultSet(
        connectionId: string,
        statement: string,
        set: IResultSetData,
        ordinal: number,
        status: string,
        currentSchema: () => Promise<string | undefined>,
        runId: string,
        pageSize?: number,
    ): Promise<IResultSet> {
        const labels = set.columns;
        const rows = set.rows;
        // Paged only where the server added the limit; anything else came
        // back whole.
        const page: IResultPage | undefined =
            set.has_more_pages === undefined || pageSize === undefined
                ? undefined
                : {
                    index: 0,
                    size: pageSize,
                    hasMore: set.has_more_pages,
                    loads: 1,
                };
        const base = plainResultSet(
            statement,
            set,
            ordinal,
            runId,
            page === undefined ? status : pageStatus(rows.length, page),
            page,
        );

        if (isProcedureCall(statement)) {
            return { ...base, readOnlyReason: PROCEDURE_READ_ONLY };
        }

        const target = findUpdatableTarget(statement);
        if (!target) {
            return {
                ...base,
                readOnlyReason:
                    "Read only: the result does not map onto the rows of a "
                    + "single table.",
            };
        }

        const schema = target.schema ?? await currentSchema();
        if (schema === undefined) {
            return {
                ...base,
                readOnlyReason:
                    "Read only: the table is not schema qualified and the "
                    + "connection has no default schema.",
            };
        }

        // Asked as a table outright: the common case costs one call. A view
        // - mysql.user is one - is found out by the answer being no such
        // table, which the activity log reports as that answer rather than
        // as an error. Nothing more is asked about a view: its columns
        // would only give the header tooltips their types.
        let details;
        try {
            details = await this.api.getObjectDetails(
                connectionId,
                schema,
                target.table,
                "table",
            );
        } catch (error) {
            return {
                ...base,
                readOnlyReason: error instanceof ObjectNotFoundError
                    ? `Read only: ${schema}.${target.table} is not a table - `
                    + "a view, say."
                    : `Read only: the columns of ${target.table} could not `
                    + "be looked up.",
            };
        }

        const columns = mapColumns(
            labels, details.columns, set.column_metadata);

        const hasKey = columns.some((column) => {
            return column.isPrimary;
        });

        if (!hasKey) {
            return {
                ...base,
                columns,
                target: { schema, table: target.table },
                readOnlyReason:
                    "Read only: the result does not include a primary key.",
            };
        }

        return {
            ...base,
            columns,
            target: { schema, table: target.table },
            editable: true,
        };
    }

    /**
     * A result set a procedure returned after its first: read only, and
     * captioned as the next result along.
     *
     * @param statement The CALL that returned it.
     * @param set Its columns and rows.
     * @param ordinal The index of this result set among the run's.
     * @param runId The run it belongs to.
     *
     * @returns The result set to show.
     */
    #procedureResultSet(
        statement: string,
        set: IResultSetData,
        ordinal: number,
        runId: string,
    ): IResultSet {
        return {
            ...plainResultSet(
                statement, set, ordinal, runId, rowsInSet(set.rows.length)),
            readOnlyReason: PROCEDURE_READ_ONLY,
        };
    }

    /**
     * Asks the connection which schema it is currently using, so a table
     * named without a schema can still be looked up.
     *
     * @param connectionId The UUID to ask on.
     *
     * @returns The current schema, or undefined if none is selected.
     */
    async #currentSchema(connectionId: string): Promise<string | undefined> {
        try {
            const results = await this.api.executeScript(
                connectionId,
                "SELECT DATABASE() AS `schema`;",
            );
            const value = results[0]?.result_sets?.[0]?.rows[0]?.schema;

            return typeof value === "string" && value.length > 0
                ? value
                : undefined;
        } catch {
            return undefined;
        }
    }
}
