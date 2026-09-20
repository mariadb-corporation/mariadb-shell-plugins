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
    IOutputRow,
    IResultColumn,
    IResultSet,
    IStatementSource,
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
 * Formats a wall-clock time to the millisecond.
 *
 * The output grid keeps rows from every run, so a row has to say when it
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
    script: string;
    /**
     * Distinguishes this run's rows and result sets from every other
     * run's, so an older output row cannot link to a tab a later run
     * replaced.
     */
    runId: string;
    /** Where the script came from, for the jump-to-statement links. */
    source?: IScriptSource;
    /** Whether a failing statement ends the script. */
    stopOnError?: boolean;
    /** What to call this run in the opening and closing lines. */
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
     * The output opens with a line saying what is about to run and closes
     * with one saying how it went, so a run reads as a block in a log
     * that keeps growing. Each line in between is one statement, timed by
     * the server and carrying where in the file it came from.
     *
     * @param options What to run, and where it came from.
     *
     * @returns The report to send to the webview.
     */
    public async execute(
        options: IExecutionOptions,
    ): Promise<IExecutionReport> {
        const { connectionUri, connectionId, script, runId } = options;
        const statements = splitStatements(script);
        // A comment is one of the statements the server splits out and
        // takes up an index among them, but it is not one the server runs
        // and there is no result for it. Counting it would make a file
        // that opens with a connection header - which every generated one
        // does - report one statement more than it has.
        const executable = statements.filter((statement) => {
            return statement.executable;
        });
        const output: IOutputRow[] = [];
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

        const what = options.label
            ?? (executable.length === 1
                ? "1 statement"
                : `${executable.length} statements`);
        output.push({
            id: `${runId}-start`,
            time: startedAt,
            connection: connectionUri,
            role: "start",
            statement: "",
            message: `Running ${what} on ${connectionUri}`,
            kind: "info",
            source: runSource,
        });

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
            const failure: IOutputRow = {
                id: `${runId}-error`,
                time: startedAt,
                connection: connectionUri,
                role: "statement",
                statement: captionFor(script),
                message,
                kind: "error",
                source: runSource,
            };
            output.push(failure, {
                id: `${runId}-finish`,
                time: formatTime(new Date()),
                connection: connectionUri,
                role: "finish",
                statement: "",
                message: `Execution failed: ${message}`,
                kind: "error",
                elapsedMs: Date.now() - startedMs,
                source: failure.source,
                jumpToRowId: failure.id,
            });

            return {
                connection: connectionUri,
                startedAt,
                elapsedMs: Date.now() - startedMs,
                output,
                resultSets,
            };
        }

        let firstErrorId: string | undefined;
        let firstErrorSource: IStatementSource | undefined;
        let errorCount = 0;

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
                output.push({
                    id,
                    time: startedAt,
                    connection: connectionUri,
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

            if (!result.columns) {
                output.push({
                    id,
                    time: startedAt,
                    connection: connectionUri,
                    role: "statement",
                    statement: captionFor(statement),
                    message: describeResult(result),
                    kind,
                    rows: result.affected_items_count,
                    elapsedMs,
                    source,
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

            output.push({
                id,
                time: startedAt,
                connection: connectionUri,
                role: "statement",
                statement: captionFor(statement),
                message: describeResult(result),
                kind,
                rows: resultSet.rows.length,
                elapsedMs,
                source,
                resultId: resultSet.id,
            });
        }

        const elapsedMs = Date.now() - startedMs;
        const ran = results.length;
        const stoppedEarly = errorCount > 0 && ran < executable.length;
        const warningCount = output.filter((row) => {
            return row.kind === "warning";
        }).length;

        output.push({
            id: `${runId}-finish`,
            time: formatTime(new Date()),
            connection: connectionUri,
            role: "finish",
            statement: "",
            message: errorCount === 0
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
            kind: errorCount > 0
                ? "error"
                : warningCount > 0
                    ? "warning"
                    : "info",
            // Only the closing line carries the run's own time; the lines
            // above it each carry their statement's.
            elapsedMs,
            source: firstErrorSource,
            jumpToRowId: firstErrorId,
        });

        return {
            connection: connectionUri,
            startedAt,
            elapsedMs,
            output,
            resultSets,
        };
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
