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

import {
    buildUtilOptions,
    initialUtilValues,
    splitList,
    utilOperationSpec,
    type UtilOperation,
} from "../../util/utilFields.js";

const OPERATIONS: UtilOperation[] = [
    "dumpInstance", "dumpSchemas", "dumpTables", "exportTable", "loadDump",
    "importTable", "copyInstance", "copySchemas", "copyTables",
];

const subject = {
    connection: "root@localhost:3306", schemas: ["shop"], tables: ["orders"],
};

describe("utilOperationSpec", () => {
    it("has a dialog for every operation", () => {
        for (const operation of OPERATIONS) {
            const spec = utilOperationSpec(operation, subject);
            expect(spec.operation).toBe(operation);
            expect(spec.title).not.toBe("");
            // a copy asks for a target, everything else for a path
            expect(spec.target === true).toBe(operation.startsWith("copy"));
            expect(spec.path === undefined).toBe(operation.startsWith("copy"));
            const names = spec.options.map((option) => { return option.name; });
            expect(new Set(names).size).toBe(names.length);
        }
    });

    it("names what it works on in the title", () => {
        expect(utilOperationSpec("dumpSchemas", { connection: "c",
            schemas: ["a", "b"] }).title).toBe("Dump a, b to Disk");
        expect(utilOperationSpec("dumpInstance", subject).title)
            .toBe("Dump root@localhost:3306 to Disk");
        expect(utilOperationSpec("exportTable", subject).title)
            .toBe("Export shop.orders to a File");
    });

    it("offers a schema to copy into only for one schema", () => {
        const names = (schemas: string[]): string[] => {
            return utilOperationSpec("copySchemas", { connection: "c", schemas })
                .options.map((option) => { return option.name; });
        };
        expect(names(["a"])).toContain("schema");
        expect(names(["a", "b"])).not.toContain("schema");
    });

    it("leaves compression and chunks out of a copy", () => {
        const names = utilOperationSpec("copyInstance", subject).options
            .map((option) => { return option.name; });
        expect(names).not.toContain("compression");
        expect(names).not.toContain("bytesPerChunk");
        expect(names).toContain("dropExistingObjects");
    });
});

describe("buildUtilOptions", () => {
    it("sends no option left at its default", () => {
        for (const operation of OPERATIONS) {
            const spec = utilOperationSpec(operation, subject);
            const values = { ...initialUtilValues(spec, "/tmp/dump"),
                target: "root@other:3306" };
            const built = buildUtilOptions(spec, values);
            expect("problem" in built).toBe(false);
            if (!("problem" in built)) {
                // the import needs to be told its schema and table
                expect(built.options).toEqual(operation === "importTable"
                    ? { schema: "shop", table: "orders" } : {});
            }
        }
    });

    it("sends what was changed, with its type", () => {
        const spec = utilOperationSpec("dumpSchemas", subject);
        const built = buildUtilOptions(spec, {
            ...initialUtilValues(spec, "/tmp/dump"),
            threads: "8", consistent: false, compression: "gzip",
            excludeTables: "shop.a, shop.b\nshop.c", maxRate: " 10M ",
        });
        expect(built).toEqual({
            options: {
                threads: 8, consistent: false, compression: "gzip",
                excludeTables: ["shop.a", "shop.b", "shop.c"], maxRate: "10M",
            },
            paths: ["/tmp/dump"],
        });
    });

    it("needs a path, or a target for a copy", () => {
        const dump = utilOperationSpec("dumpInstance", subject);
        expect(buildUtilOptions(dump, initialUtilValues(dump, "  ")))
            .toEqual({ problem: { field: "path",
                message: "Output Folder must be given." } });

        const copy = utilOperationSpec("copyInstance", subject);
        expect(buildUtilOptions(copy, initialUtilValues(copy)))
            .toEqual({ problem: { field: "target",
                message: "Choose the connection to copy to." } });
        expect(buildUtilOptions(copy, { ...initialUtilValues(copy),
            target: "root@b:3306" })).toEqual({
            options: {}, paths: [], target: "root@b:3306",
        });
    });

    it("splits the files of an import", () => {
        const spec = utilOperationSpec("importTable", subject);
        const built = buildUtilOptions(spec, initialUtilValues(spec,
            "/in/a.tsv, /in/b-*.tsv"));
        expect(built).toMatchObject({ paths: ["/in/a.tsv", "/in/b-*.tsv"] });
    });

    it("refuses a number that is not one", () => {
        const spec = utilOperationSpec("loadDump", subject);
        expect(buildUtilOptions(spec, { ...initialUtilValues(spec, "/d"),
            threads: "four" })).toEqual({ problem: { field: "threads",
            message: "Threads must be a whole number." } });
    });

    it("refuses options that contradict each other", () => {
        const dump = utilOperationSpec("dumpSchemas", subject);
        expect(buildUtilOptions(dump, { ...initialUtilValues(dump, "/d"),
            ddlOnly: true, dataOnly: true })).toMatchObject({
            problem: { field: "dataOnly" } });

        const load = utilOperationSpec("loadDump", subject);
        expect(buildUtilOptions(load, { ...initialUtilValues(load, "/d"),
            dropExistingObjects: true, ignoreExistingObjects: true }))
            .toMatchObject({ problem: { field: "ignoreExistingObjects" } });
    });
});

describe("splitList", () => {
    it("splits on commas and lines and drops empty entries", () => {
        expect(splitList(" a, b ,,\nc\n")).toEqual(["a", "b", "c"]);
        expect(splitList("")).toEqual([]);
    });
});
