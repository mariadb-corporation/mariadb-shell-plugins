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

import { describe, expect, it } from "vitest";

import type { IColumnDetails, IObjectDetails } from "../../mcp/types.js";
import {
    captionFor,
    describeResult,
    describeRun,
    dropLeadingComments,
    ExecutionService,
    isProcedureCall,
    mapColumns,
    pendingRunRow,
} from "../../sql/executionService.js";
import type {
    IExecutionReport,
    IActionRow,
} from "../../webview/protocol.js";
import { createFakeApi } from "../helpers.js";

/**
 * @param report The report to read.
 *
 * @returns The run's own row, which is the whole of the output.
 */
const runOf = (report: IExecutionReport): IActionRow => {
    return report.actions[0];
};

/**
 * @param report The report to read.
 *
 * @returns The rows of its statements, under the run.
 */
const statementsOf = (report: IExecutionReport): IActionRow[] => {
    return runOf(report).children ?? [];
};

/**
 * @param overrides The fields that differ from a plain nullable column.
 *
 * @returns A column description.
 */
const column = (overrides: Partial<IColumnDetails>): IColumnDetails => {
    return {
        name: "c",
        datatype: "varchar(50)",
        not_null: 0,
        is_primary: 0,
        is_unique: 0,
        is_generated: 0,
        id_generation: null,
        comment: "",
        column_default: null,
        ...overrides,
    };
};

const CITY: IObjectDetails = {
    basic: { schema: "world", name: "city", type: "table" },
    columns: [
        column({
            name: "ID",
            datatype: "int(11)",
            not_null: 1,
            is_primary: 1,
            id_generation: "auto_inc",
        }),
        column({ name: "Name", datatype: "char(35)", not_null: 1 }),
    ],
};

describe("describeResult", () => {
    it("counts the rows of a result set", () => {
        expect(describeResult({
            affected_items_count: 0,
            warnings_count: 0,
            columns: ["a"],
            rows: [{ a: 1 }, { a: 2 }],
        })).toBe("2 rows in set");
    });

    it("uses the singular for one row", () => {
        expect(describeResult({
            affected_items_count: 0,
            warnings_count: 0,
            columns: ["a"],
            rows: [{ a: 1 }],
        })).toBe("1 row in set");
    });

    it("reports affected rows where there is no result set", () => {
        expect(describeResult({
            affected_items_count: 3,
            warnings_count: 0,
        })).toBe("Query OK, 3 rows affected");
    });

    it("mentions warnings", () => {
        expect(describeResult({
            affected_items_count: 1,
            warnings_count: 1,
        })).toBe("Query OK, 1 row affected, 1 warning");
        expect(describeResult({
            affected_items_count: 0,
            warnings_count: 2,
            columns: [],
            rows: [],
        })).toBe("0 rows in set, 2 warnings");
    });
});

describe("captionFor", () => {
    it("puts a statement on one line", () => {
        expect(captionFor("SELECT 1,\n  2")).toBe("SELECT 1, 2");
    });

    it("truncates a long statement", () => {
        expect(captionFor("SELECT " + "x".repeat(60), 20))
            .toBe("SELECT xxxxxxxxxxxx…");
    });

    it("skips the header a generated file starts with", () => {
        expect(captionFor(
            "-- MariaDB connection: dba@localhost:3310\n"
            + "SELECT ID FROM world.city",
        )).toBe("SELECT ID FROM world.city");
    });

    it("keeps a statement that is nothing but a comment", () => {
        expect(captionFor("-- just a note")).toBe("-- just a note");
    });
});

describe("dropLeadingComments", () => {
    it.each([
        ["-- a note\nSELECT 1", "SELECT 1"],
        ["# a note\nSELECT 1", "SELECT 1"],
        ["/* a note */ SELECT 1", "SELECT 1"],
        ["/* one */\n-- two\n# three\nSELECT 1", "SELECT 1"],
        ["  \n-- a note\n\nSELECT 1", "SELECT 1"],
        ["SELECT 1 -- trailing", "SELECT 1 -- trailing"],
    ])("reduces %s", (statement, expected) => {
        expect(dropLeadingComments(statement)).toBe(expected);
    });

    it("returns nothing for a statement that is all comment", () => {
        expect(dropLeadingComments("-- a note")).toBe("");
        expect(dropLeadingComments("/* unterminated")).toBe("");
    });

    it("leaves a bare -- alone, which is a subtraction", () => {
        expect(dropLeadingComments("--2 + 3")).toBe("--2 + 3");
    });
});

describe("isProcedureCall", () => {
    it("knows a CALL, in any case and after comments", () => {
        expect(isProcedureCall("CALL p()")).toBe(true);
        expect(isProcedureCall("call world.p")).toBe(true);
        expect(isProcedureCall("-- run it\n/* twice */ CALL p()")).toBe(true);
    });

    it("knows what is not one", () => {
        expect(isProcedureCall("SELECT caller FROM t")).toBe(false);
        expect(isProcedureCall("CALLER()")).toBe(false);
    });
});

describe("mapColumns", () => {
    it("returns bare columns without details", () => {
        expect(mapColumns(["a", "b"]))
            .toEqual([{ name: "a" }, { name: "b" }]);
    });

    it("keeps the result set's order and selection", () => {
        expect(mapColumns(["Name", "ID"], CITY.columns)).toEqual([
            {
                name: "Name",
                datatype: "char(35)",
                isPrimary: false,
                isGenerated: false,
                nullable: false,
            },
            {
                name: "ID",
                datatype: "int(11)",
                isPrimary: true,
                isGenerated: true,
                nullable: false,
            },
        ]);
    });

    it("leaves a computed column undescribed", () => {
        expect(mapColumns(["total"], CITY.columns))
            .toEqual([{ name: "total" }]);
    });
});

describe("ExecutionService.execute", () => {
    it("puts a statement without a result set on the output tab", async () => {
        const api = createFakeApi({
            defaultResults: [{ affected_items_count: 1, warnings_count: 0 }],
        });

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "CREATE SCHEMA demo;",
                runId: "run1",
            });

        expect(report.resultSets).toEqual([]);
        // The run is one row - keyed on the run's own id, so it replaces
        // the pending row it was started under - with a row per
        // statement under it.
        expect(report.actions).toHaveLength(1);
        expect(runOf(report)).toMatchObject({
            id: "run1",
            role: "run",
            connection: "dba@h",
            message: "Ran 1 statement on dba@h",
            summary: "Finished 1 statement successfully",
            kind: "info",
        });
        expect(statementsOf(report).map((row) => {
            return [row.role, row.message];
        })).toEqual([["statement", "Query OK, 1 row affected"]]);
        expect(statementsOf(report)[0]).toMatchObject({
            id: "run1-0",
            connection: "dba@h",
            statement: "CREATE SCHEMA demo",
            kind: "info",
        });
        expect(report.connection).toBe("dba@h");
        expect(runOf(report).time).toMatch(/^\d\d:\d\d:\d\d\.\d\d\d$/);
    });

    it("keeps the run's id, so it replaces the row it went up as",
        async () => {
            const api = createFakeApi({
                defaultResults: [
                    { affected_items_count: 1, warnings_count: 0 },
                    { affected_items_count: 1, warnings_count: 0 },
                ],
            });

            const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT 1;\nSELECT 2;",
                runId: "run7",
            });

            expect(runOf(report).id).toBe("run7");
            expect(statementsOf(report).map((row) => {
                return row.id;
            })).toEqual(["run7-0", "run7-1"]);
        });

    it("makes one tab per result set", async () => {
        const api = createFakeApi({
            defaultResults: [
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["a"],
                    rows: [{ a: 1 }],
                },
                { affected_items_count: 0, warnings_count: 0 },
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["b"],
                    rows: [],
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT 1 AS a; SET @x = 1; SELECT 2 AS b;",
                runId: "run1",
            });

        expect(report.resultSets.map((set) => {
            return set.caption;
        })).toEqual(["Result #1", "Result #2"]);
        expect(report.resultSets[0].statement).toBe("SELECT 1 AS a");
        expect(report.resultSets[1].statement).toBe("SELECT 2 AS b");
        // Every statement gets a row under the run's, and the two that
        // returned rows link to the tab they produced.
        const statementRows = statementsOf(report);
        expect(statementRows).toHaveLength(3);
        expect(statementRows.map((row) => {
            return row.resultId;
        })).toEqual([
            "run1-result-0",
            undefined,
            "run1-result-1",
        ]);
    });

    it("hangs a statement's warnings under it, one row each", async () => {
        const api = createFakeApi({
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 1,
                warnings: [{
                    level: "Warning",
                    code: 1292,
                    message:
                        "Truncated incorrect INTEGER value: 'not-a-number'",
                }],
                columns: ["n"],
                rows: [{ n: 0 }],
            }, {
                affected_items_count: 0,
                warnings_count: 2,
                warnings: [
                    { level: "Note", code: 1051, message: "Unknown table 'a'" },
                    { level: "Note", code: 1051, message: "Unknown table 'b'" },
                ],
            }],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT CAST('not-a-number' AS UNSIGNED) AS n;"
                + "DROP TABLE IF EXISTS a, b;",
            runId: "run1",
        });

        const [query, drop] = statementsOf(report);
        // The statement keeps its count in its message; the texts are
        // the rows under it, which is what the count was no use without.
        expect(query.message).toBe("1 row in set, 1 warning");
        expect(query.kind).toBe("warning");
        expect(query.children).toEqual([{
            id: "run1-0-warning-0",
            time: query.time,
            connection: "dba@h",
            connectionLabel: query.connectionLabel,
            source: query.source,
            role: "warning",
            statement: "Warning 1292",
            message: "Truncated incorrect INTEGER value: 'not-a-number'",
            kind: "warning",
        }]);

        // One row per warning, whether or not the statement returned
        // anything, and each keyed apart from the others.
        expect(drop.children?.map((row) => {
            return [row.id, row.statement, row.message];
        })).toEqual([
            ["run1-1-warning-0", "Note 1051", "Unknown table 'a'"],
            ["run1-1-warning-1", "Note 1051", "Unknown table 'b'"],
        ]);
    });

    it("leaves a statement that warned about nothing childless", async () => {
        const api = createFakeApi({
            defaultResults: [{ affected_items_count: 1, warnings_count: 0 }],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;",
            runId: "run1",
        });

        // No expander on a row with nothing behind it.
        expect(statementsOf(report)[0].children).toBeUndefined();
    });

    it("keeps the count where the shell reports no warning texts",
        async () => {
            // Every shell before this feature: warnings_count has always
            // been there, the texts have not. The count still shows and
            // the row simply has nothing to open.
            const api = createFakeApi({
                defaultResults: [{
                    affected_items_count: 1,
                    warnings_count: 3,
                }],
            });

            const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "INSERT INTO t VALUES (1);",
                runId: "run1",
            });

            const [statement] = statementsOf(report);
            expect(statement.message)
                .toBe("Query OK, 1 row affected, 3 warnings");
            expect(statement.kind).toBe("warning");
            expect(statement.children).toBeUndefined();
        });

    it("marks a derived result set read only", async () => {
        const api = createFakeApi({
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["n"],
                rows: [{ n: 4079 }],
            }],
        });

        const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT count(*) AS n FROM world.city;",
                runId: "run1",
            });

        expect(report.resultSets[0].editable).toBe(false);
        expect(report.resultSets[0].readOnlyReason)
            .toContain("does not map onto the rows of a single table");
    });

    it("makes a single table SELECT editable", async () => {
        const api = createFakeApi({
            details: { "world.city": CITY },
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["ID", "Name"],
                rows: [{ ID: 1, Name: "Kabul" }],
            }],
        });

        const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT ID, Name FROM world.city;",
                runId: "run1",
            });

        const [set] = report.resultSets;
        expect(set.editable).toBe(true);
        expect(set.target).toEqual({ schema: "world", table: "city" });
        expect(set.columns[0].isPrimary).toBe(true);
        expect(set.status).toBe("1 row in set");
    });

    it("resolves an unqualified table against the current schema",
        async () => {
            const api = createFakeApi({
                details: { "world.city": CITY },
                results: {
                    "SELECT DATABASE() AS `schema`;": [{
                        affected_items_count: 0,
                        warnings_count: 0,
                        columns: ["schema"],
                        rows: [{ schema: "world" }],
                    }],
                },
                defaultResults: [{
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["ID"],
                    rows: [{ ID: 1 }],
                }],
            });

            const report = await new ExecutionService(api)
                .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT ID FROM city;",
                runId: "run1",
            });

            expect(report.resultSets[0].editable).toBe(true);
            expect(report.resultSets[0].target)
                .toEqual({ schema: "world", table: "city" });
        });

    it("stays read only when there is no current schema", async () => {
        const api = createFakeApi({
            results: {
                "SELECT DATABASE() AS `schema`;": [{
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["schema"],
                    rows: [{ schema: null }],
                }],
            },
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["ID"],
                rows: [{ ID: 1 }],
            }],
        });

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT ID FROM city;",
                runId: "run1",
            });

        expect(report.resultSets[0].editable).toBe(false);
        expect(report.resultSets[0].readOnlyReason)
            .toContain("no default schema");
    });

    it("stays read only when the table cannot be described", async () => {
        const api = createFakeApi({
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["a"],
                rows: [{ a: 1 }],
            }],
        });

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT a FROM world.mystery;",
                runId: "run1",
            });

        expect(report.resultSets[0].editable).toBe(false);
        // No such table, as the server words it: a view, or gone.
        expect(report.resultSets[0].readOnlyReason)
            .toBe("Read only: world.mystery is not a table - a view, say.");
    });

    it("stays read only when the primary key was not selected", async () => {
        const api = createFakeApi({
            details: { "world.city": CITY },
            defaultResults: [{
                affected_items_count: 0,
                warnings_count: 0,
                columns: ["Name"],
                rows: [{ Name: "Kabul" }],
            }],
        });

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT Name FROM world.city;",
                runId: "run1",
            });

        expect(report.resultSets[0].editable).toBe(false);
        expect(report.resultSets[0].readOnlyReason)
            .toContain("primary key");
    });

    it("asks for the current schema at most once", async () => {
        const api = createFakeApi({
            details: { "world.city": CITY },
            results: {
                "SELECT DATABASE() AS `schema`;": [{
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["schema"],
                    rows: [{ schema: "world" }],
                }],
            },
            defaultResults: [
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["ID"],
                    rows: [],
                },
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    columns: ["ID"],
                    rows: [],
                },
            ],
        });

        await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT ID FROM city; SELECT ID FROM city;",
                runId: "run1",
            });

        const lookups = api.scripts.filter((script) => {
            return script.includes("DATABASE()");
        });
        expect(lookups).toHaveLength(1);
    });

    it("reports a failed script on the output tab", async () => {
        const api = createFakeApi();
        api.executeScript = () => {
            return Promise.reject(new Error(
                "MySQL Error (1146): Table 'nope.nope' doesn't exist"));
        };

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "SELECT * FROM nope.nope;",
                runId: "run1",
            });

        expect(report.resultSets).toEqual([]);
        const failure = statementsOf(report).find((row) => {
            return row.kind === "error";
        });
        expect(failure?.message).toContain("doesn't exist");

        // The run's own row reports the failure and points back at it.
        expect(runOf(report)).toMatchObject({
            role: "run",
            kind: "error",
            summary: "Execution failed: MySQL Error (1146): Table "
                + "'nope.nope' doesn't exist",
            jumpToRowId: failure?.id,
        });
    });

    it("says so when the script held no statements", async () => {
        const api = createFakeApi({ defaultResults: [] });

        const report = await new ExecutionService(api)
            .execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "   ",
                runId: "run1",
            });

        expect(runOf(report)).toMatchObject({
            role: "run",
            message: "Ran 0 statements on dba@h",
            summary: "Finished 0 statements successfully",
        });
        expect(statementsOf(report)).toEqual([]);
    });
});

describe("ExecutionService with the server's statement reporting", () => {
    /**
     * @param uri The document the script came from.
     *
     * @returns A source that maps every offset to its own line.
     */
    const lineSource = (uri = "file:///work/query.sql") => {
        return {
            uri,
            positionAt: (offset: number) => {
                return { line: offset, character: 0 };
            },
        };
    };

    it("times each statement from what the server measured", async () => {
        const api = createFakeApi({
            defaultResults: [
                {
                    affected_items_count: 1,
                    warnings_count: 0,
                    statement_index: 0,
                    execution_time: 0.004,
                },
                {
                    affected_items_count: 2,
                    warnings_count: 0,
                    statement_index: 1,
                    execution_time: 0.25,
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "INSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);",
            runId: "run1",
        });

        // Each statement's own time, not the run's repeated.
        expect(statementsOf(report).map((row) => {
            return row.elapsedMs;
        })).toEqual([4, 250]);
        // Only the run's own row carries the run's time.
        expect(runOf(report).elapsedMs).toBe(report.elapsedMs);
    });

    it("blames the statement the server says failed", async () => {
        const api = createFakeApi({
            defaultResults: [
                {
                    affected_items_count: 1,
                    warnings_count: 0,
                    statement_index: 0,
                    execution_time: 0.001,
                },
                {
                    statement_index: 1,
                    execution_time: 0.002,
                    statement: "INSERT INTO t VALUES (1)",
                    error: "Duplicate entry '1' for key 'PRIMARY'",
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "INSERT INTO t VALUES (0);\nINSERT INTO t VALUES (1);\n"
                + "INSERT INTO t VALUES (2);",
            runId: "run1",
            source: lineSource(),
        });

        const failure = statementsOf(report).find((row) => {
            return row.kind === "error";
        });
        expect(failure?.message).toContain("Duplicate entry");
        // The second statement, so the second line of the source.
        expect(failure?.source).toEqual({
            uri: "file:///work/query.sql",
            line: 26,
            character: 0,
        });

        const run = runOf(report);
        expect(run.summary)
            .toBe("Finished with 1 error, stopped after 2 of 3");
        // The run's row carries it to its first error, in the output and
        // in the editor.
        expect(run.jumpToRowId).toBe(failure?.id);
        expect(run.source).toEqual(failure?.source);
    });

    it("reports every failure when the script ran on", async () => {
        const api = createFakeApi({
            defaultResults: [
                {
                    statement_index: 0,
                    error: "first failed",
                    statement: "A",
                },
                {
                    affected_items_count: 1,
                    warnings_count: 0,
                    statement_index: 1,
                },
                {
                    statement_index: 2,
                    error: "third failed",
                    statement: "C",
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "A;\nB;\nC;",
            runId: "run1",
            stopOnError: false,
        });

        expect(api.stopOnError).toBe(false);
        const errors = statementsOf(report).filter((row) => {
            return row.kind === "error";
        });
        expect(errors).toHaveLength(2);
        // Nothing was skipped, so it does not claim it stopped early.
        expect(runOf(report).summary).toBe("Finished with 2 errors");
        expect(runOf(report).jumpToRowId).toBe(errors[0].id);
    });

    it("gives every statement row a place in the file", async () => {
        const api = createFakeApi({
            defaultResults: [
                { affected_items_count: 0, warnings_count: 0 },
                { affected_items_count: 0, warnings_count: 0 },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;\nSELECT 2;",
            runId: "run1",
            source: lineSource(),
        });

        // Offsets 0 and 10, which this source maps to lines 0 and 10.
        expect(statementsOf(report).map((row) => {
            return row.source?.line;
        })).toEqual([0, 10]);
        // A run that worked points at where it began; one that failed
        // points at its first error instead.
        expect(runOf(report).source?.line).toBe(0);
        expect(runOf(report).jumpToRowId).toBeUndefined();
    });

    it("leaves the source out when nothing was behind the run", async () => {
        const api = createFakeApi({
            defaultResults: [{ affected_items_count: 0, warnings_count: 0 }],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;",
            runId: "run1",
        });

        expect([runOf(report), ...statementsOf(report)].every((row) => {
            return row.source === undefined;
        })).toBe(true);
    });

    it("names the run in its own row", async () => {
        const api = createFakeApi({
            defaultResults: [{ affected_items_count: 0, warnings_count: 0 }],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;",
            runId: "run1",
            label: "the selection",
        });

        expect(runOf(report).message).toBe("Ran the selection on dba@h");
        expect(runOf(report).summary)
            .toBe("Finished the selection successfully");
    });

    it("frames a call that failed outright", async () => {
        const api = createFakeApi();
        api.executeScript = () => {
            return Promise.reject(new Error("the connection went away"));
        };

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;",
            runId: "run1",
        });

        // Nothing ran, so the one row under the run is the call itself.
        expect(statementsOf(report).map((row) => {
            return [row.role, row.message];
        })).toEqual([["statement", "the connection went away"]]);
        expect(runOf(report).summary)
            .toBe("Execution failed: the connection went away");
        expect(runOf(report).jumpToRowId).toBe("run1-error");
    });
});

describe("output row severity", () => {
    it("marks a statement that raised warnings", async () => {
        const api = createFakeApi({
            defaultResults: [
                { affected_items_count: 1, warnings_count: 0 },
                { affected_items_count: 1, warnings_count: 2 },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "INSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);",
            runId: "run1",
        });

        expect(statementsOf(report).map((row) => {
            return row.kind;
        })).toEqual(["info", "warning"]);
        // The run takes the worst of what its statements saw, and says
        // so rather than claiming plain success.
        expect(runOf(report).kind).toBe("warning");
        expect(runOf(report).summary)
            .toBe("Finished 2 statements successfully with 1 warning");
    });

    it("lets an error outrank a warning on the run's row", async () => {
        const api = createFakeApi({
            defaultResults: [
                { affected_items_count: 1, warnings_count: 3 },
                { statement_index: 1, error: "it broke", statement: "B" },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "A;\nB;",
            runId: "run1",
        });

        expect(runOf(report).kind).toBe("error");
        expect(runOf(report).summary).toBe("Finished with 1 error");
    });

    it("stays plain information when nothing was amiss", async () => {
        const api = createFakeApi({
            defaultResults: [{ affected_items_count: 1, warnings_count: 0 }],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "SELECT 1;",
            runId: "run1",
        });

        expect([runOf(report), ...statementsOf(report)].every((row) => {
            return row.kind === "info";
        })).toBe(true);
    });
});

describe("ExecutionService with a comment among the statements", () => {
    /**
     * @returns A source that maps every offset to its own line.
     */
    const lineSource = () => {
        return {
            uri: "file:///work/query.sql",
            positionAt: (offset: number) => {
                return { line: offset, character: 0 };
            },
        };
    };

    // What the Connections view's New SQL Editor button writes at the top
    // of every file it generates, followed by a query.
    const GENERATED = "-- MariaDB connection: dba@h\n\nSELECT 1;";

    it("does not count a comment among the statements being run", async () => {
        // The server splits the comment out and numbers it, but does not
        // run it, so there is one result and it is numbered 1.
        const api = createFakeApi({
            defaultResults: [
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    statement_index: 1,
                    execution_time: 0.001,
                    columns: ["a"],
                    rows: [{ a: 1 }],
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: GENERATED,
            runId: "run1",
        });

        expect(runOf(report).message).toBe("Ran 1 statement on dba@h");
        expect(runOf(report).summary)
            .toBe("Finished 1 statement successfully");
        expect(statementsOf(report).map((row) => {
            return [row.role, row.message];
        })).toEqual([["statement", "1 row in set"]]);
    });

    it("pairs the one result with the statement, not the comment",
        async () => {
            const api = createFakeApi({
                defaultResults: [
                    {
                        affected_items_count: 0,
                        warnings_count: 0,
                        statement_index: 1,
                        execution_time: 0.001,
                        columns: ["a"],
                        rows: [{ a: 1 }],
                    },
                ],
            });

            const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: GENERATED,
                runId: "run1",
                source: lineSource(),
            });

            // The index the server sent is what picks the statement out,
            // which is why the comment has to keep its place in the split.
            expect(statementsOf(report)[0]).toMatchObject({
                id: "run1-1",
                statement: "SELECT 1",
                // Offset 30: the query, past the header and the blank line.
                source: {
                    uri: "file:///work/query.sql",
                    line: 30,
                    character: 0,
                },
            });
        });

    it("points the run's own row at its first real statement", async () => {
        // Not at the header comment in front of it, which is where it
        // pointed while the comment counted as statement zero.
        const api = createFakeApi({
            defaultResults: [
                {
                    affected_items_count: 0,
                    warnings_count: 0,
                    statement_index: 1,
                    execution_time: 0.001,
                },
            ],
        });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: GENERATED,
            runId: "run1",
            source: lineSource(),
        });

        expect(runOf(report).source).toEqual({
            uri: "file:///work/query.sql",
            line: 30,
            character: 0,
        });
    });

    it("counts only the statements being run when one stops the script",
        async () => {
            const api = createFakeApi({
                defaultResults: [
                    {
                        statement_index: 1,
                        execution_time: 0.001,
                        statement: "INSERT INTO t VALUES (1)",
                        error: "Duplicate entry '1' for key 'PRIMARY'",
                    },
                ],
            });

            const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "-- a note\nINSERT INTO t VALUES (1);\nSELECT 1;",
                runId: "run1",
            });

            // Two statements to run, not three: "1 of 2".
            expect(runOf(report).summary)
                .toBe("Finished with 1 error, stopped after 1 of 2");
        });

    it("says a script of nothing but comments runs nothing", async () => {
        const api = createFakeApi({ defaultResults: [] });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "-- one\n-- two\n",
            runId: "run1",
        });

        expect(runOf(report).message).toBe("Ran 0 statements on dba@h");
        expect(runOf(report).summary)
            .toBe("Finished 0 statements successfully");
        expect(statementsOf(report)).toEqual([]);
    });
});

describe("describeRun", () => {
    it("counts the statements that will be run", () => {
        expect(describeRun("SELECT 1;")).toBe("1 statement");
        expect(describeRun("SELECT 1;\nSELECT 2;")).toBe("2 statements");
        expect(describeRun("   ")).toBe("0 statements");
    });

    it("does not count a comment among them", () => {
        // The same rule `execute()` follows, so the row a run goes up
        // with says what the row it is replaced by will say.
        expect(describeRun("-- a note\nSELECT 1;")).toBe("1 statement");
    });

    it("takes the caller's own name for the run instead", () => {
        expect(describeRun("SELECT 1;\nSELECT 2;", "the selection"))
            .toBe("the selection");
    });
});

describe("pendingRunRow", () => {
    it("says what is being run, and marks it as still running", () => {
        const row = pendingRunRow({
            runId: "run7",
            connectionUri: "dba@h",
            what: "3 statements",
            when: new Date(2026, 8, 21, 14, 5, 6, 78),
        });

        expect(row).toEqual({
            // The run's own id, so the finished run replaces this row
            // rather than being appended beside it.
            id: "run7",
            time: "14:05:06.078",
            connection: "dba@h",
            role: "run",
            statement: "",
            message: "Running 3 statements on dba@h",
            summary: "Running\u2026",
            kind: "pending",
            // Empty rather than absent: the row keeps its expander, so
            // its message does not shift when the statements arrive.
            children: [],
        });
    });

    it("has no time of its own to report yet", () => {
        expect(pendingRunRow({
            runId: "run1",
            connectionUri: "dba@h",
            what: "1 statement",
        }).elapsedMs).toBeUndefined();
    });
});

describe("ExecutionService.applyChanges", () => {
    /**
     * @returns An editable result set over world.city.
     */
    const editableSet = () => {
        return {
            id: "run1-result-0",
            caption: "Result #1",
            statement: "SELECT ID, Name FROM world.city",
            columns: [
                {
                    name: "ID",
                    datatype: "int(11)",
                    isPrimary: true,
                    isGenerated: true,
                    nullable: false,
                },
                {
                    name: "Name",
                    datatype: "char(35)",
                    isPrimary: false,
                    isGenerated: false,
                    nullable: false,
                },
            ],
            rows: [{ ID: 1, Name: "Kabul" }],
            editable: true,
            target: { schema: "world", table: "city" },
            status: "1 row in set",
        };
    };

    it("runs the generated statements on the same connection", async () => {
        const api = createFakeApi();

        const statements = await new ExecutionService(api).applyChanges(
            "id",
            editableSet(),
            [{
                kind: "update",
                rowIndex: 0,
                keys: { ID: 1 },
                values: { Name: "Kabul City" },
            }],
        );

        expect(statements).toEqual([
            "UPDATE `world`.`city` SET `Name` = 'Kabul City' "
            + "WHERE `ID` = 1",
        ]);
        expect(api.scripts).toEqual([
            "UPDATE `world`.`city` SET `Name` = 'Kabul City' "
            + "WHERE `ID` = 1;",
        ]);
    });

    it("runs nothing when there is nothing to apply", async () => {
        const api = createFakeApi();

        await expect(new ExecutionService(api)
            .applyChanges("id", editableSet(), [])).resolves.toEqual([]);
        expect(api.scripts).toEqual([]);
    });

    it("refuses a result set with no table behind it", async () => {
        const api = createFakeApi();
        const set = { ...editableSet(), target: undefined };

        await expect(new ExecutionService(api).applyChanges("id", set, [
            { kind: "delete", rowIndex: 0, keys: { ID: 1 } },
        ])).rejects.toThrow(/not bound to a single table/);
    });
});


describe("ExecutionService looking up where a SELECT read from", () => {
    const rows = {
        affected_items_count: 0,
        warnings_count: 0,
        columns: ["Host", "User"],
        rows: [{ Host: "localhost", User: "root" }],
    };

    const run = async (
        api: ReturnType<typeof createFakeApi>,
        script: string,
    ): Promise<IExecutionReport> => {
        return await new ExecutionService(api).execute({
            connectionUri: "mariadb://root@127.0.0.1:3311",
            connectionId: "id",
            script,
            runId: "run1",
        });
    };

    it("finds a view out by asking for a table, in one call", async () => {
        // mysql.user has been a view since MariaDB 10.4.
        const api = createFakeApi({ defaultResults: [rows] });

        const [set] = (await run(api, "SELECT * FROM mysql.user;")).resultSets;

        expect(api.lookups).toEqual(["mysql.user:table"]);
        expect(set!.editable).toBe(false);
        expect(set!.readOnlyReason)
            .toBe("Read only: mysql.user is not a table - a view, say.");
        expect(set!.columns.map((col) => { return col.name; }))
            .toEqual(["Host", "User"]);
        // Nothing else was asked on the way.
        expect(api.scripts.some((script) => {
            return script.includes("information_schema");
        })).toBe(false);
    });

    it("says it could not look a table up when the lookup itself failed",
        async () => {
            const api = createFakeApi({ defaultResults: [rows] });
            api.getObjectDetails = () => {
                return Promise.reject(new Error("The connection was closed."));
            };

            const [set] = (await run(api, "SELECT * FROM world.city;"))
                .resultSets;

            expect(set!.readOnlyReason)
                .toBe("Read only: the columns of city could not be looked up.");
        });
});

describe("ExecutionService with a stored procedure", () => {
    const call = (
        extra: Array<{ columns: string[]; rows: Array<Record<string, unknown>> }>,
    ) => {
        return [{
            affected_items_count: 0,
            warnings_count: 0,
            statement_index: 0,
            columns: ["id"],
            rows: [{ id: 1 }, { id: 2 }],
            ...(extra.length > 0 ? { additional_result_sets: extra } : {}),
        }];
    };

    it("shows every result set a CALL returned, each a row away",
        async () => {
            const api = createFakeApi({
                defaultResults: call([
                    { columns: ["letter"], rows: [{ letter: "x" }] },
                    { columns: ["n"], rows: [] },
                ]),
            });

            const report = await new ExecutionService(api).execute({
                connectionUri: "dba@h",
                connectionId: "id",
                script: "CALL world.three_sets();",
                runId: "run1",
            });

            expect(report.resultSets.map((set) => {
                return [set.caption, set.rows.length, set.editable];
            })).toEqual([
                ["Result #1", 2, false],
                ["Result #2", 1, false],
                ["Result #3", 0, false],
            ]);
            expect(new Set(report.resultSets.map((set) => {
                return set.readOnlyReason;
            }))).toEqual(new Set([
                "Read only: the result set of a stored procedure.",
            ]));

            const statement = report.actions[0]!.children![0]!;
            expect(statement.message).toBe("3 result sets");
            expect(statement.resultId).toBe(report.resultSets[0]!.id);
            expect(statement.children!.map((row) => {
                return [row.statement, row.message, row.resultId];
            })).toEqual(report.resultSets.map((set) => {
                return [set.caption, `${set.rows.length} row`
                    + `${set.rows.length === 1 ? "" : "s"} in set`, set.id];
            }));

            // No table was looked for, and so nothing failed on the way.
            expect(api.lookups).toEqual([]);
            expect(api.scripts.some((script) => {
                return script.includes("information_schema");
            })).toBe(false);
        });

    it("keeps a CALL with one result set to a single row", async () => {
        const api = createFakeApi({ defaultResults: call([]) });

        const report = await new ExecutionService(api).execute({
            connectionUri: "dba@h",
            connectionId: "id",
            script: "CALL world.one_set();",
            runId: "run1",
        });

        expect(report.resultSets).toHaveLength(1);
        expect(report.resultSets[0]!.readOnlyReason)
            .toBe("Read only: the result set of a stored procedure.");
        const statement = report.actions[0]!.children![0]!;
        expect(statement.message).toBe("2 rows in set");
        expect(statement.children).toBeUndefined();
    });
});
