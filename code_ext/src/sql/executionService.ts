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
    IMariaDbApi,
    IStatementResult,
} from "../mcp/types.js";
import type {
    IExecutionReport,
    IActionRow,
    IResultColumn,
    IResultSet,
    IStatementSource,
    ActionSeverity,
    RowChange,
} from "../webview/protocol.js";
import { createQueryBuilder } from "./resultSetQueryBuilder.js";
import { splitStatements } from "./splitStatements.js";
import { findUpdatableTarget } from "./statementTarget.js";

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
        ? `, ${warningCount} warning${warningCount === 1 ? "" : "s"}`
        : "";

    if (result.rows) {
        const count = result.rows.length;

        return `${count} row${count === 1 ? "" : "s"} in set${warnings}`;
    }

    const affected = result.affected_items_count ?? 0;

    return `Query OK, ${affected} row${affected === 1 ? "" : "s"} affected`
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
 * The same phrase, worked out from the script itself.
 *
 * This is what a caller uses to open the run's row before it has a
 * connection to run on, which is the whole point of the row: it appears
 * the moment the user asks for it, not once the server answers.
 *
 * @param script The SQL about to be run.
 * @param label What the caller wants the run called instead.
 *
 * @returns The phrase the run's row is built around.
 */
export const describeRun = (script: string, label?: string): string => {
    const executable = splitStatements(script).filter((statement) => {
        return statement.executable;
    });

    return describeStatementCount(executable.length, label);
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
 *
 * @returns The grid columns.
 */
export const mapColumns = (
    labels: string[],
    details?: IColumnDetails[],
): IResultColumn[] => {
    const byName = new Map(details?.map((column) => {
        return [column.name, column];
    }));

    return labels.map((name) => {
        const column = byName.get(name);
        if (!column) {
            return { name };
        }

        return {
            name,
            datatype: column.datatype,
            isPrimary: Boolean(column.is_primary),
            isGenerated: Boolean(column.is_generated)
                || column.id_generation === "auto_inc",
            nullable: !column.not_null,
        };
    });
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
    /** Where the script came from, for the jump-to-statement links. */
    source?: IScriptSource;
    /** Whether a failing statement ends the script. */
    stopOnError?: boolean;
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
        const statements = splitStatements(script);
        // A comment is one of the statements the server splits out and
        // takes up an index among them, but it is not one the server runs
        // and there is no result for it. Counting it would make a file
        // that opens with a connection header - which every generated one
        // does - report one statement more than it has.
        const executable = statements.filter((statement) => {
            return statement.executable;
        });
        const children: IActionRow[] = [];
        const resultSets: IResultSet[] = [];

        const startedAt = formatTime(new Date());
        const startedMs = Date.now();

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
                connectionId, script, options.stopOnError);
        } catch (error) {
            // The call itself failed - a closed connection, a shell that
            // went away - so nothing ran and there is no statement to
            // blame.
            const message = error instanceof Error
                ? error.message
                : String(error);
            const failure: IActionRow = {
                id: `${runId}-error`,
                time: startedAt,
                connection: connectionUri,
                connectionLabel,
                role: "statement",
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
                    id,
                    time: startedAt,
                    connection: connectionUri,
                    connectionLabel,
                    role: "statement",
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
            const warnings = warningRowsOf(result, id, {
                time: startedAt,
                connection: connectionUri,
                connectionLabel,
                source,
            });

            if (!result.columns) {
                children.push({
                    id,
                    time: startedAt,
                    connection: connectionUri,
                    connectionLabel,
                    role: "statement",
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
                result,
                resultSets.length,
                describeResult(result),
                currentSchema,
                runId,
            );
            resultSets.push(resultSet);

            children.push({
                id,
                time: startedAt,
                connection: connectionUri,
                connectionLabel,
                role: "statement",
                statement: captionFor(statement),
                message: describeResult(result),
                kind,
                elapsedMs,
                source,
                resultId: resultSet.id,
                ...(warnings.length > 0 ? { children: warnings } : {}),
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
                    : ` with ${warningCount} warning`
                    + `${warningCount === 1 ? "" : "s"}`)
                : `Finished with ${errorCount} error`
                + `${errorCount === 1 ? "" : "s"}`
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
     * Turns one result into a grid, looking up the source table's columns
     * where the statement allows the grid to be edited.
     *
     * @param connectionId The UUID to look the table up on.
     * @param statement The statement that produced the result.
     * @param result The result itself.
     * @param ordinal The index of this result set among the others.
     * @param status The status line for it.
     *
     * @returns The result set to show.
     */
    async #buildResultSet(
        connectionId: string,
        statement: string,
        result: IStatementResult,
        ordinal: number,
        status: string,
        currentSchema: () => Promise<string | undefined>,
        runId: string,
    ): Promise<IResultSet> {
        const labels = result.columns ?? [];
        const base: IResultSet = {
            id: `${runId}-result-${ordinal}`,
            caption: `Result #${ordinal + 1}`,
            statement,
            columns: mapColumns(labels),
            rows: result.rows ?? [],
            editable: false,
            status,
        };

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

        let details;
        try {
            details = await this.api.getObjectDetails(
                connectionId,
                schema,
                target.table,
                "table",
            );
        } catch {
            // A view, a temporary table or a table in another schema than
            // the one guessed: the grid simply stays read only.
            return {
                ...base,
                readOnlyReason:
                    `Read only: the columns of ${target.table} could not `
                    + "be looked up.",
            };
        }

        const columns = mapColumns(labels, details.columns);
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
            const value = results[0]?.rows?.[0]?.schema;

            return typeof value === "string" && value.length > 0
                ? value
                : undefined;
        } catch {
            return undefined;
        }
    }
}
