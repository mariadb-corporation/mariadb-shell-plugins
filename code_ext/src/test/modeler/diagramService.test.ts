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

import type { IMariaDbApi, IStatementResult } from "../../mcp/types.js";
import {
    hasDiagrams,
    hasDiagramsSql,
    listDiagramsSql,
    readDiagramsSql,
} from "../../modeler/diagramQueries.js";
import {
    DIAGRAM_SESSION,
    DiagramService,
} from "../../modeler/diagramService.js";
import type { IActionRow } from "../../webview/protocol.js";

describe("the diagram queries", () => {
    it("quote the schema", () => {
        expect(listDiagramsSql("my`db")).toBe("SELECT `name` FROM "
            + "`my``db`.`msm_schema_diagrams` WHERE `format` LIKE '%diagram%'");
        expect(readDiagramsSql("x")).toBe("SELECT `name`, `format`, "
            + "`document` FROM `x`.`msm_schema_diagrams`");
        expect(hasDiagramsSql("it's")).toContain("TABLE_SCHEMA = 'it''s'");
    });

    it("tell a schema with the view by a row", () => {
        expect(hasDiagrams({ result_sets: [{ columns: ["1"], rows: [{}] }] }))
            .toBe(true);
        expect(hasDiagrams({ result_sets: [{ columns: ["1"], rows: [] }] }))
            .toBe(false);
        expect(() => { return hasDiagrams({ error: "denied" }); })
            .toThrow("denied");
    });
});

describe("DiagramService", () => {
    const service = (answer: IStatementResult | Error) => {
        const rows: IActionRow[] = [];
        const sessions: string[] = [];
        const api = {
            executeSql: () => {
                return answer instanceof Error
                    ? Promise.reject(answer)
                    : Promise.resolve(answer);
            },
        } as unknown as IMariaDbApi;

        return {
            rows,
            sessions,
            service: new DiagramService({
                connect: (_uri, name) => {
                    sessions.push(name);

                    return Promise.resolve("id-1");
                },
                api: () => { return Promise.resolve(api); },
                put: (_connection, row) => { rows.push(row); },
            }),
        };
    };

    it("reads the rows on the Diagram session, reporting what it read",
        async () => {
            const { rows, sessions, service: diagrams } = service({
                result_sets: [{
                    columns: ["name", "format", "document"],
                    rows: [{ name: "MRS", format: "mwb-diagram/1",
                        document: "{}" }],
                }],
            });

            await expect(diagrams.readDiagrams("u", "world")).resolves
                .toEqual([{ name: "MRS", format: "mwb-diagram/1",
                    document: "{}" }]);
            expect(sessions).toEqual([DIAGRAM_SESSION]);
            expect(rows[0]).toMatchObject({ kind: "pending",
                message: "Reading the diagrams of world…" });
            expect(rows[1]).toMatchObject({ kind: "info",
                message: "Read 1 document of world's diagrams",
                statement: readDiagramsSql("world") });
        });

    it("reports a failed query once, as an error, and throws it", async () => {
        const { rows, service: diagrams } = service({ error: "no such view" });

        await expect(diagrams.listDiagrams("u", "world")).rejects
            .toThrow("no such view");
        expect(rows.map((row) => { return row.kind; }))
            .toEqual(["pending", "error"]);
        expect(rows[1].message).toBe("no such view");
    });

    it("reports a call that broke off, too", async () => {
        const { rows, service: diagrams } = service(new Error("gone"));

        await expect(diagrams.listDiagrams("u", "world")).rejects
            .toThrow("gone");
        expect(rows[1]).toMatchObject({ kind: "error", message: "gone" });
    });
});
