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
    dropLeadingComments,
    ExecutionService,
    mapColumns,
} from "../../sql/executionService.js";
import { createFakeApi } from "../helpers.js";

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
        // A run reads as a block: what is about to happen, each
        // statement, then how it went.
        expect(report.output.map((row) => {
            return [row.role, row.message];
        })).toEqual([
            ["start", "Running 1 statement on dba@h"],
            ["statement", "Query OK, 1 row affected"],
            ["finish", "Finished 1 statement successfully"],
        ]);
        expect(report.output[1]).toMatchObject({
            id: "run1-0",
            connection: "dba@h",
            statement: "CREATE SCHEMA demo",
            kind: "info",
            rows: 1,
        });
        expect(report.connection).toBe("dba@h");
        expect(report.output[0].time).toMatch(/^\d\d:\d\d:\d\d\.\d\d\d$/);
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
        // Every statement gets an output row between the run's opening
        // and closing lines, and the two that returned rows link to the
        // tab they produced.
        const statementRows = report.output.filter((row) => {
            return row.role === "statement";
        });
        expect(statementRows).toHaveLength(3);
        expect(statementRows.map((row) => {
            return row.resultId;
        })).toEqual([
            "run1-result-0",
            undefined,
            "run1-result-1",
        ]);
        expect(statementRows.map((row) => {
            return row.rows;
        })).toEqual([1, 0, 0]);
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
        expect(report.resultSets[0].readOnlyReason)
            .toContain("could not be looked up");
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
        const failure = report.output.find((row) => {
            return row.kind === "error" && row.role === "statement";
        });
        expect(failure?.message).toContain("doesn't exist");

        // The closing line reports the failure and points back at it.
        const finish = report.output.at(-1);
        expect(finish?.role).toBe("finish");
        expect(finish?.kind).toBe("error");
        expect(finish?.jumpToRowId).toBe(failure?.id);
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

        expect(report.output.map((row) => {
            return row.role;
        })).toEqual(["start", "finish"]);
        expect(report.output[0].message)
            .toBe("Running 0 statements on dba@h");
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

        const statementRows = report.output.filter((row) => {
            return row.role === "statement";
        });
        // Each statement's own time, not the run's repeated.
        expect(statementRows.map((row) => {
            return row.elapsedMs;
        })).toEqual([4, 250]);
        // Only the closing line carries the run's time.
        expect(report.output.at(-1)?.role).toBe("finish");
        expect(report.output.at(-1)?.elapsedMs).toBe(report.elapsedMs);
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

        const failure = report.output.find((row) => {
            return row.kind === "error" && row.role === "statement";
        });
        expect(failure?.message).toContain("Duplicate entry");
        // The second statement, so the second line of the source.
        expect(failure?.source).toEqual({
            uri: "file:///work/query.sql",
            line: 26,
            character: 0,
        });

        const finish = report.output.at(-1);
        expect(finish?.message)
            .toBe("Finished with 1 error, stopped after 2 of 3");
        // The closing line carries the run to its first error, in the
        // output and in the editor.
        expect(finish?.jumpToRowId).toBe(failure?.id);
        expect(finish?.source).toEqual(failure?.source);
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
        const errors = report.output.filter((row) => {
            return row.kind === "error" && row.role === "statement";
        });
        expect(errors).toHaveLength(2);
        const finish = report.output.at(-1);
        // Nothing was skipped, so it does not claim it stopped early.
        expect(finish?.message).toBe("Finished with 2 errors");
        expect(finish?.jumpToRowId).toBe(errors[0].id);
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
        expect(report.output.filter((row) => {
            return row.role === "statement";
        }).map((row) => {
            return row.source?.line;
        })).toEqual([0, 10]);
        // The opening line points at the first statement.
        expect(report.output[0].source?.line).toBe(0);
        // The closing line has nowhere to point on a run that worked:
        // its source is the first error, and there was none.
        expect(report.output.at(-1)?.source).toBeUndefined();
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

        expect(report.output.every((row) => {
            return row.source === undefined;
        })).toBe(true);
    });

    it("names the run in its opening and closing lines", async () => {
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

        expect(report.output[0].message)
            .toBe("Running the selection on dba@h");
        expect(report.output.at(-1)?.message)
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

        expect(report.output.map((row) => {
            return row.role;
        })).toEqual(["start", "statement", "finish"]);
        expect(report.output.at(-1)?.message)
            .toBe("Execution failed: the connection went away");
        expect(report.output.at(-1)?.jumpToRowId).toBe("run1-error");
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

        expect(report.output.map((row) => {
            return row.kind;
        })).toEqual(["info", "info", "warning", "warning"]);
        // The closing line says so too, rather than claiming success.
        expect(report.output.at(-1)?.message)
            .toBe("Finished 2 statements successfully with 1 warning");
    });

    it("lets an error outrank a warning on the closing line", async () => {
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

        expect(report.output.at(-1)?.kind).toBe("error");
        expect(report.output.at(-1)?.message).toBe("Finished with 1 error");
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

        expect(report.output.every((row) => {
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

        expect(report.output.map((row) => {
            return [row.role, row.message];
        })).toEqual([
            ["start", "Running 1 statement on dba@h"],
            ["statement", "1 row in set"],
            ["finish", "Finished 1 statement successfully"],
        ]);
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
            expect(report.output[1]).toMatchObject({
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

    it("points the run's own lines at its first real statement", async () => {
        // Not at the header comment in front of it, which is where they
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

        expect(report.output[0].source).toEqual({
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
            expect(report.output.at(-1)?.message)
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

        expect(report.output.map((row) => {
            return [row.role, row.message];
        })).toEqual([
            ["start", "Running 0 statements on dba@h"],
            ["finish", "Finished 0 statements successfully"],
        ]);
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
