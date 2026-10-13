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

import { describe, expect, it, vi } from "vitest";

import {
    buildDocument,
    camelCase,
    crudOperations,
    defaultClassName,
    fieldsFromColumns,
    findField,
    isRoutine,
    loadReference,
    mappingText,
    newKey,
    objectSql,
    parametersFrom,
    pascalCase,
    reduceReference,
    referencesToLoad,
    setAllEnabled,
    siblingsOf,
    snakeCase,
    toggleRowOwnership,
    updateField,
    type ICrudFlags,
    type IMappingDocument,
    type IMappingField,
} from "../../mrs/dataMapping.js";
import {
    ENABLED_STATE,
    type IMrsColumns,
    type IMrsDataMapping,
    type IMrsDataMappingField,
    type IMrsDbColumn,
    type IMrsReferenceMapping,
    type IMrsTableColumn,
} from "../../mrs/mrsTypes.js";
import type { IObjectSettings } from "../../mrs/restSql.js";

const NEW_KEY = /^new-[a-z0-9]+-\d+$/;

const NO_CRUD: ICrudFlags = {
    insert: false, update: false, delete: false, noCheck: false,
};

let position = 0;

/** A column of SHOW REST COLUMNS. */
const column = (
    name: string,
    db: Partial<IMrsDbColumn> = {},
): IMrsTableColumn => {
    position += 1;

    return {
        position,
        name,
        db_column: { name, datatype: "int", ...db },
        reference_mapping: null,
    };
};

const mappingTo = (
    table: string,
    schema = "sakila",
    base = `${table}_id`,
): IMrsReferenceMapping => {
    return {
        kind: "n:1",
        to_many: false,
        referenced_schema: schema,
        referenced_table: table,
        column_mapping: [{ base, ref: base }],
    };
};

/** A reference of SHOW REST COLUMNS. */
const reference = (
    name: string,
    mapping: IMrsReferenceMapping,
): IMrsTableColumn => {
    position += 1;

    return { position, name, db_column: null, reference_mapping: mapping };
};

/** A stored data mapping field. */
const stored = (
    overrides: Partial<IMrsDataMappingField> & { id: string },
): IMrsDataMappingField => {
    return {
        parent_reference_id: null,
        represents_reference_id: null,
        name: overrides.id,
        position: 0,
        db_column: null,
        enabled: true,
        allow_filtering: true,
        allow_sorting: false,
        no_check: false,
        no_update: false,
        data_mapping_reference: null,
        ...overrides,
    };
};

/** A stored reference field. */
const storedReference = (
    id: string,
    referenceId: string,
    mapping: IMrsReferenceMapping,
    overrides: Partial<IMrsDataMappingField> = {},
    reductions: Partial<NonNullable<
        IMrsDataMappingField["data_mapping_reference"]>> = {},
): IMrsDataMappingField => {
    return stored({
        id,
        represents_reference_id: referenceId,
        data_mapping_reference: {
            id: referenceId,
            reduce_to_value_of_field_id: null,
            row_ownership_field_id: null,
            reference_mapping: mapping,
            unnest: false,
            options: null,
            ...reductions,
        },
        ...overrides,
    });
};

/** An editor field. */
const field = (
    key: string,
    overrides: Partial<IMappingField> = {},
): IMappingField => {
    return {
        key,
        name: key,
        column: { name: key },
        enabled: true,
        allowFiltering: true,
        allowSorting: false,
        noCheck: false,
        noUpdate: false,
        isKey: false,
        rowOwnership: false,
        ...overrides,
    };
};

/** An editor reference field. */
const refField = (
    key: string,
    children: IMappingField[],
    overrides: Partial<IMappingField> = {},
    crud: ICrudFlags = NO_CRUD,
): IMappingField => {
    return {
        ...field(key, overrides),
        column: undefined,
        reference: {
            mapping: mappingTo(key),
            crud,
            unnest: false,
            loaded: true,
            children,
        },
    };
};

const settings = (
    overrides: Partial<IObjectSettings> = {},
): IObjectSettings => {
    return {
        enabled: ENABLED_STATE.enabled,
        requiresAuth: true,
        itemsPerPage: null,
        comments: "",
        mediaType: "",
        autoDetectMediaType: false,
        format: "FEED",
        authStoredProcedure: "",
        options: null,
        metadata: null,
        ...overrides,
    };
};

const viewDocument = (
    overrides: Partial<IMappingDocument> = {},
): IMappingDocument => {
    return {
        objectType: "TABLE",
        dbSchema: "sakila",
        dbObject: "city",
        className: "MyCity",
        crud: NO_CRUD,
        fields: [field("id", { isKey: true })],
        ...overrides,
    };
};

describe("names", () => {
    it("makes camelCase", () => {
        expect(camelCase("first_name")).toBe("firstName");
        expect(camelCase("First Name")).toBe("firstName");
        expect(camelCase("kebab-case-x")).toBe("kebabCaseX");
        expect(camelCase("__a__b__")).toBe("aB");
        expect(camelCase("city")).toBe("city");
        expect(camelCase("lastUpdate")).toBe("lastUpdate");
        expect(camelCase("")).toBe("");
    });

    it("makes PascalCase", () => {
        expect(pascalCase("first_name")).toBe("FirstName");
        expect(pascalCase("/sakila")).toBe("Sakila");
        expect(pascalCase("")).toBe("");
    });

    it("makes snake_case", () => {
        expect(snakeCase("firstName")).toBe("first_name");
        expect(snakeCase("LastUpdate")).toBe("last_update");
        expect(snakeCase("address2Line")).toBe("address2_line");
        expect(snakeCase("plain")).toBe("plain");
    });

    it("names a new object's class after its service, schema and name",
        () => {
            expect(defaultClassName("/svc", "/sakila", "city"))
                .toBe("SvcSakilaCity");
            expect(defaultClassName("/my-svc", "/sakila", "film_actor"))
                .toBe("MySvcSakilaFilmActor");
            expect(defaultClassName("mike,'a@b.com'@/svc", "/sakila", "city"))
                .toBe("SvcSakilaCity");
        });

    it("makes unique keys", () => {
        const first = newKey();
        const second = newKey();
        expect(first).toMatch(NEW_KEY);
        expect(second).toMatch(NEW_KEY);
        expect(first).not.toBe(second);
    });

    it("makes keys that do not clash with another copy's", async () => {
        // The extension builds a table's fields and the dialog a
        // reference's, each with its own copy of the module.
        const mine = newKey();
        vi.resetModules();
        const other = await import("../../mrs/dataMapping.js");
        const theirs = other.newKey();
        expect(theirs).toMatch(NEW_KEY);
        expect(theirs).not.toBe(mine);
        expect(theirs.replace(/\d+$/, "")).not.toBe(mine.replace(/\d+$/, ""));
    });

    it("tells routines apart", () => {
        expect(isRoutine("PROCEDURE")).toBe(true);
        expect(isRoutine("FUNCTION")).toBe(true);
        expect(isRoutine("TABLE")).toBe(false);
        expect(isRoutine("VIEW")).toBe(false);
    });
});

describe("fieldsFromColumns", () => {
    it("gives new columns their defaults", () => {
        const fields = fieldsFromColumns([
            column("city_id", { is_primary: true }),
            column("email", { is_unique: true }),
            column("last_update"),
        ], [], null, null, ["sakila.city"]);

        expect(fields).toEqual([
            {
                key: expect.stringMatching(NEW_KEY),
                name: "cityId",
                column: { name: "city_id", datatype: "int", is_primary: true },
                enabled: true,
                allowFiltering: true,
                allowSorting: true,
                noCheck: false,
                noUpdate: false,
                isKey: true,
                rowOwnership: false,
            },
            expect.objectContaining({
                name: "email", allowSorting: true, isKey: false,
            }),
            expect.objectContaining({
                name: "lastUpdate", allowSorting: false, isKey: false,
            }),
        ]);
        expect(fields[0]).not.toHaveProperty("jsonSchema");
        expect(fields[0]).not.toHaveProperty("missing");
    });

    it("keeps what is stored for a column", () => {
        const fields = fieldsFromColumns([
            column("name"),
            column("email", { is_primary: true }),
        ], [
            stored({
                id: "f-name",
                name: "fullName",
                db_column: { name: "name" },
                enabled: false,
                allow_filtering: false,
                allow_sorting: true,
                no_check: true,
                no_update: true,
                json_schema: { type: "string" },
            }),
            stored({
                id: "f-email",
                name: "mail",
                db_column: { name: "email", is_primary: false },
            }),
        ], null, "f-email", ["sakila.customer"]);

        expect(fields).toEqual([
            {
                key: "f-name",
                name: "fullName",
                column: { name: "name", datatype: "int" },
                enabled: false,
                allowFiltering: false,
                allowSorting: true,
                noCheck: true,
                noUpdate: true,
                isKey: false,
                rowOwnership: false,
                jsonSchema: { type: "string" },
            },
            {
                key: "f-email",
                name: "mail",
                column: { name: "email", datatype: "int", is_primary: true },
                enabled: true,
                allowFiltering: true,
                allowSorting: false,
                noCheck: false,
                noUpdate: false,
                // What is stored wins over the column.
                isKey: false,
                rowOwnership: true,
            },
        ]);
    });

    it("falls back to the column for the key where nothing says", () => {
        const [id] = fieldsFromColumns([column("id", { is_primary: true })], [
            stored({ id: "f-id", db_column: { name: "id" } }),
        ], null, null, ["s.t"]);
        expect(id.isKey).toBe(true);
    });

    it("only takes the stored fields of its own level", () => {
        const [name] = fieldsFromColumns([column("name")], [
            stored({
                id: "nested",
                name: "nested",
                parent_reference_id: "r1",
                db_column: { name: "name" },
            }),
        ], null, null, ["s.t"]);
        expect(name.key).toMatch(NEW_KEY);
        expect(name.name).toBe("name");
    });

    it("adds a reference disabled, unless stored", () => {
        const store = mappingTo("store");
        const address = mappingTo("address");
        const fields = fieldsFromColumns([
            reference("store", store),
            reference("address", address),
        ], [
            storedReference("f-store", "r-store", store, {
                name: "theStore",
                enabled: true,
            }, {
                unnest: true,
                reduce_to_value_of_field_id: "f-child",
                options: {
                    dataMappingViewInsert: true,
                    dataMappingViewNoCheck: true,
                },
            }),
        ], null, null, ["sakila.customer"]);

        expect(fields).toEqual([
            {
                key: "f-store",
                name: "theStore",
                reference: {
                    mapping: store,
                    crud: {
                        insert: true, update: false, delete: false,
                        noCheck: true,
                    },
                    unnest: true,
                    loaded: false,
                    children: [],
                    reduceTo: "f-child",
                },
                enabled: true,
                allowFiltering: true,
                allowSorting: false,
                noCheck: false,
                noUpdate: false,
                isKey: false,
                rowOwnership: false,
            },
            {
                key: expect.stringMatching(NEW_KEY),
                name: "address",
                reference: {
                    mapping: address,
                    crud: NO_CRUD,
                    unnest: false,
                    loaded: false,
                    children: [],
                },
                enabled: false,
                allowFiltering: true,
                allowSorting: false,
                noCheck: false,
                noUpdate: false,
                isKey: false,
                rowOwnership: false,
            },
        ]);
    });

    it("matches a stored reference by its whole mapping", () => {
        const [field] = fieldsFromColumns([
            reference("store", mappingTo("store", "sakila", "a")),
        ], [
            storedReference("f-store", "r", mappingTo("store", "sakila", "b"),
                { enabled: true }),
        ], null, null, ["s.t"]);
        expect(field.key).toMatch(NEW_KEY);
        expect(field.enabled).toBe(false);
    });

    it("leaves out a reference back up the chain", () => {
        const fields = fieldsFromColumns([
            column("id"),
            reference("city", mappingTo("city")),
            reference("country", mappingTo("country")),
            reference("film", mappingTo("film")),
        ], [], null, null, ["sakila.country", "sakila.city"]);
        expect(fields.map((f) => { return f.name; }))
            .toEqual(["id", "film"]);
    });

    it("marks a stored field whose column is gone", () => {
        const fields = fieldsFromColumns([column("id")], [
            stored({
                id: "f-gone",
                name: "gone",
                db_column: { name: "gone", is_primary: true },
                allow_filtering: false,
                allow_sorting: true,
                no_check: true,
                no_update: true,
            }),
            // A reference that is gone, or a field without a column, is
            // not shown.
            storedReference("f-ref", "r", mappingTo("x")),
            stored({ id: "f-nocol" }),
        ], null, "f-gone", ["s.t"]);

        expect(fields).toHaveLength(2);
        expect(fields[1]).toEqual({
            key: "f-gone",
            name: "gone",
            column: { name: "gone", is_primary: true },
            enabled: false,
            allowFiltering: false,
            allowSorting: true,
            noCheck: true,
            noUpdate: true,
            isKey: false,
            rowOwnership: false,
            missing: true,
        });
    });

    it("suffixes names that are taken", () => {
        const fields = fieldsFromColumns([
            column("first_name"),
            column("firstName"),
            reference("first-name", mappingTo("x")),
        ], [
            stored({
                id: "f-gone", name: "firstName", db_column: { name: "gone" },
            }),
        ], null, null, ["s.t"]);
        expect(fields.map((f) => { return f.name; })).toEqual([
            "firstName", "firstName2", "firstName3", "firstName4",
        ]);
    });
});

describe("loadReference", () => {
    const store = mappingTo("store");
    const storedFields = [
        storedReference("f-store", "r-store", store, { enabled: true }),
        stored({
            id: "c-id", name: "storeId", parent_reference_id: "r-store",
            db_column: { name: "store_id" },
        }),
        stored({
            id: "c-mgr", name: "manager", parent_reference_id: "r-store",
            db_column: { name: "manager_staff_id" },
            enabled: false,
        }),
    ];
    const storeColumns = [
        column("store_id", { is_primary: true }),
        column("manager_staff_id"),
        reference("customers", mappingTo("customer")),
        reference("selfies", mappingTo("store")),
        reference("staff", mappingTo("staff")),
    ];

    it("fills the children from the stored level below the reference", () => {
        const [top] = fieldsFromColumns([reference("store", store)],
            storedFields, null, null, ["sakila.customer"]);
        const loaded = loadReference(top, storeColumns, storedFields,
            ["sakila.customer"], "c-mgr");

        expect(loaded).not.toBe(top);
        expect(top.reference!.loaded).toBe(false);
        expect(loaded.reference!.loaded).toBe(true);
        const children = loaded.reference!.children;
        expect(children.map((child) => {
            return [child.key, child.name, child.enabled, child.rowOwnership];
        })).toEqual([
            ["c-id", "storeId", true, false],
            ["c-mgr", "manager", false, true],
            [expect.stringMatching(NEW_KEY), "staff", false, false],
        ]);
    });

    it("keeps only the field a reference is reduced to", () => {
        const [top] = fieldsFromColumns([reference("store", store)], [
            storedReference("f-store", "r-store", store, { enabled: true },
                { reduce_to_value_of_field_id: "c-mgr" }),
            ...storedFields.slice(1),
        ], null, null, ["sakila.customer"]);
        expect(top.reference!.reduceTo).toBe("c-mgr");
        const loaded = loadReference(top, storeColumns, storedFields,
            ["sakila.customer"]);
        expect(loaded.reference!.children.map((child) => {
            return child.enabled;
        })).toEqual([false, true, false]);
    });

    it("gives the children of an unstored reference their defaults", () => {
        const [top] = fieldsFromColumns([reference("store", store)], [],
            null, null, ["sakila.customer"]);
        const loaded = loadReference(top, storeColumns, storedFields,
            ["sakila.customer"]);
        expect(loaded.reference!.children.map((child) => {
            return [child.name, child.enabled];
        })).toEqual([
            ["storeId", true],
            ["managerStaffId", true],
            ["staff", false],
        ]);
        expect(loaded.reference!.children[0].key).toMatch(NEW_KEY);
    });
});

describe("buildDocument", () => {
    const tableColumns: IMrsColumns = {
        schema: "sakila",
        name: "city",
        type: "TABLE",
        columns: [
            column("city_id", { is_primary: true }),
            column("city"),
            reference("country", mappingTo("country")),
            reference("addresses", mappingTo("city")),
        ],
    };

    it("builds a new table's document", () => {
        const document = buildDocument("TABLE", "sakila", "city", tableColumns,
            [], "SvcSakilaCity");
        expect(document.objectType).toBe("TABLE");
        expect(document.dbSchema).toBe("sakila");
        expect(document.dbObject).toBe("city");
        expect(document.className).toBe("SvcSakilaCity");
        expect(document.crud).toEqual(NO_CRUD);
        // The reference back to the table itself is left out.
        expect(document.fields.map((f) => { return f.name; }))
            .toEqual(["cityId", "city", "country"]);
        expect(document.parameters).toBeUndefined();
        expect(document.results).toBeUndefined();
    });

    it("builds a stored view's document", () => {
        const result: IMrsDataMapping = {
            id: "m1",
            name: "MyCity",
            kind: "RESULT",
            position: 0,
            row_ownership_field_id: "f-city",
            options: {
                dataMappingViewInsert: true,
                dataMappingViewUpdate: true,
                dataMappingViewDelete: true,
                dataMappingViewNoCheck: true,
            },
            fields: [stored({
                id: "f-city", name: "town", db_column: { name: "city" },
            })],
        };
        const document = buildDocument("VIEW", "sakila", "city", tableColumns,
            [result], "Ignored");
        expect(document.className).toBe("MyCity");
        expect(document.crud).toEqual({
            insert: true, update: true, delete: true, noCheck: true,
        });
        expect(document.fields[1]).toMatchObject({
            key: "f-city", name: "town", rowOwnership: true,
        });
    });

    it("builds a document without columns", () => {
        const document = buildDocument("VIEW", "s", "v",
            { schema: "s", name: "v", type: "VIEW" }, [], "C");
        expect(document.fields).toEqual([]);
    });

    const procedureColumns: IMrsColumns = {
        schema: "sakila",
        name: "proc",
        type: "PROCEDURE",
        parameters: [
            { position: 1, name: "p_in", mode: "IN", datatype: "int" },
            {
                position: 2, name: "p_out", mode: "OUT",
                datatype: "varchar(10)", charset: "utf8mb4",
                collation: "utf8mb4_bin",
            },
            { position: 3, name: "p_io", mode: "INOUT", datatype: "text" },
        ],
    };

    it("builds a new procedure's document", () => {
        const document = buildDocument("PROCEDURE", "sakila", "proc",
            procedureColumns, [], "Proc");
        expect(document.className).toBe("Proc");
        expect(document.fields).toEqual([]);
        expect(document.results).toEqual([]);
        expect(document.parameters!.key).toMatch(NEW_KEY);
        expect(document.parameters!.name).toBe("ProcParams");
        expect(document.parameters!.fields).toEqual([
            {
                key: expect.stringMatching(NEW_KEY),
                name: "pIn",
                column: {
                    name: "p_in", datatype: "int", in: true, out: false,
                },
                enabled: true,
                allowFiltering: true,
                allowSorting: false,
                noCheck: false,
                noUpdate: false,
                isKey: false,
                rowOwnership: false,
            },
            expect.objectContaining({
                name: "pOut",
                column: {
                    name: "p_out", datatype: "varchar(10)", in: false,
                    out: true, charset: "utf8mb4", collation: "utf8mb4_bin",
                },
            }),
            expect.objectContaining({
                name: "pIo",
                column: {
                    name: "p_io", datatype: "text", in: true, out: true,
                },
            }),
        ]);
    });

    it("merges a procedure's parameters with what is stored", () => {
        const parameters: IMrsDataMapping = {
            id: "pm",
            name: "MyParams",
            kind: "PARAMETERS",
            position: 0,
            row_ownership_field_id: null,
            options: null,
            fields: [stored({
                id: "f-in",
                name: "input",
                enabled: false,
                allow_sorting: true,
                no_check: true,
                json_schema: { type: "integer" },
                db_column: { name: "p_in", datatype: "bigint" },
            }), stored({
                id: "f-dropped", name: "dropped",
                db_column: { name: "p_dropped" },
            })],
        };
        const result = (
            id: string,
            name: string,
            at: number,
        ): IMrsDataMapping => {
            return {
                id,
                name,
                kind: "RESULT",
                position: at,
                row_ownership_field_id: null,
                options: null,
                fields: [
                    stored({
                        id: `${id}-a`, name: "amountDue",
                        db_column: { name: "amount", datatype: "decimal" },
                    }),
                    stored({ id: `${id}-b`, name: "totalCount" }),
                    stored({
                        id: `${id}-c`, name: "nested",
                        parent_reference_id: "r",
                        db_column: { name: "nested" },
                    }),
                ],
            };
        };
        const document = buildDocument("PROCEDURE", "sakila", "proc",
            procedureColumns,
            [result("r2", "Second", 2), parameters, result("r1", "First", 1)],
            "Proc");

        expect(document.parameters!.key).toBe("pm");
        expect(document.parameters!.name).toBe("MyParams");
        expect(document.parameters!.fields).toHaveLength(3);
        expect(document.parameters!.fields[0]).toEqual({
            key: "f-in",
            name: "input",
            column: {
                name: "p_in", datatype: "bigint", in: true, out: false,
            },
            enabled: false,
            allowFiltering: true,
            allowSorting: true,
            noCheck: true,
            noUpdate: false,
            isKey: false,
            rowOwnership: false,
            jsonSchema: { type: "integer" },
        });
        expect(document.parameters!.fields[1].key).toMatch(NEW_KEY);

        expect(document.results!.map((r) => { return [r.key, r.name]; }))
            .toEqual([["r1", "First"], ["r2", "Second"]]);
        expect(document.results![0].fields).toEqual([
            expect.objectContaining({
                key: "r1-a", name: "amountDue",
                column: { name: "amount", datatype: "decimal" },
            }),
            expect.objectContaining({
                key: "r1-b", name: "totalCount",
                column: { name: "total_count" },
            }),
        ]);
    });

    it("names a new function's result after its class", () => {
        const document = buildDocument("FUNCTION", "sakila", "fn", {
            schema: "sakila", name: "fn", type: "FUNCTION",
            parameters: [],
            return_type: "decimal(5,2)",
        }, [], "Fn");
        expect(document.parameters!.fields).toEqual([]);
        expect(document.results).toEqual([{
            key: expect.stringMatching(NEW_KEY),
            name: "FnResult",
            fields: [expect.objectContaining({
                name: "result",
                column: { name: "result", datatype: "decimal(5,2)" },
                enabled: true,
            })],
        }]);
    });

    it("types a function's result as text where nothing says", () => {
        const document = buildDocument("FUNCTION", "s", "fn",
            { schema: "s", name: "fn", type: "FUNCTION" }, [], "Fn");
        expect(document.parameters!.fields).toEqual([]);
        expect(document.results![0].fields[0].column!.datatype).toBe("text");
    });

    it("keeps a function's stored result", () => {
        const document = buildDocument("FUNCTION", "s", "fn",
            { schema: "s", name: "fn", type: "FUNCTION", return_type: "int" },
            [{
                id: "res", name: "Stored", kind: "RESULT", position: 0,
                row_ownership_field_id: null, options: null,
                fields: [stored({
                    id: "f", name: "value", db_column: { name: "result" },
                })],
            }], "Fn");
        expect(document.results).toHaveLength(1);
        expect(document.results![0].name).toBe("Stored");
    });

    it("merges parameters without stored ones", () => {
        expect(parametersFrom({ schema: "s", name: "p", type: "PROCEDURE" },
            undefined)).toEqual([]);
    });
});

describe("walking the tree", () => {
    const deep = field("deep");
    const inner = refField("inner", [deep]);
    const leaf = field("leaf");
    const tree = [field("a"), refField("outer", [leaf, inner]), field("b")];

    it("finds a field anywhere", () => {
        expect(findField(tree, "a")).toBe(tree[0]);
        expect(findField(tree, "deep")).toBe(deep);
        expect(findField(tree, "nope")).toBeUndefined();
    });

    it("finds a field's level", () => {
        expect(siblingsOf(tree, "b")).toBe(tree);
        expect(siblingsOf(tree, "leaf")).toBe(tree[1].reference!.children);
        expect(siblingsOf(tree, "deep")).toBe(inner.reference!.children);
        expect(siblingsOf(tree, "nope")).toBeUndefined();
    });

    it("replaces one field, sharing what is untouched", () => {
        const changed = updateField(tree, "deep", (f) => {
            return { ...f, name: "changed" };
        });
        expect(changed).not.toBe(tree);
        expect(changed[0]).toBe(tree[0]);
        expect(changed[2]).toBe(tree[2]);
        expect(changed[1]).not.toBe(tree[1]);
        expect(changed[1].reference!.children[0]).toBe(leaf);
        expect(findField(changed, "deep")!.name).toBe("changed");
        expect(deep.name).toBe("deep");
    });

    it("shares a reference branch the change does not reach", () => {
        // A reference whose subtree does not hold the key is untouched, so
        // it is to be shared rather than copied.
        const same = updateField(tree, "a", (f) => { return f; });
        expect(same[1]).toBe(tree[1]);
        expect(updateField(tree, "nope", (f) => { return f; })[1])
            .toBe(tree[1]);
    });

    it("lists the enabled references still to load", () => {
        const unloaded = (key: string, enabled = true): IMappingField => {
            const f = refField(key, [], { enabled });

            return { ...f, reference: { ...f.reference!, loaded: false } };
        };
        expect(referencesToLoad([
            field("a"),
            unloaded("one"),
            unloaded("off", false),
            refField("loaded", [unloaded("two"), field("c")]),
            refField("disabled", [unloaded("three")], { enabled: false }),
        ]).map((f) => { return f.key; })).toEqual(["one", "two"]);
    });
});

describe("toggleRowOwnership", () => {
    const tree = [
        field("a", { rowOwnership: true }),
        field("b"),
        refField("r", [field("c", { rowOwnership: true }), field("d")]),
    ];
    const owners = (fields: IMappingField[]): string[] => {
        const keys: string[] = [];
        const walk = (level: IMappingField[]): void => {
            for (const f of level) {
                if (f.rowOwnership) {
                    keys.push(f.key);
                }
                walk(f.reference?.children ?? []);
            }
        };
        walk(fields);

        return keys;
    };

    it("moves ownership within the level only", () => {
        expect(owners(toggleRowOwnership(tree, "b"))).toEqual(["b", "c"]);
        expect(owners(toggleRowOwnership(tree, "d"))).toEqual(["a", "d"]);
    });

    it("clears it on the owning field", () => {
        expect(owners(toggleRowOwnership(tree, "a"))).toEqual(["c"]);
        expect(owners(toggleRowOwnership(tree, "c"))).toEqual(["a"]);
    });

    it("leaves the tree alone for an unknown field", () => {
        expect(toggleRowOwnership(tree, "nope")).toBe(tree);
    });
});

describe("reduceReference", () => {
    const tree = [refField("r", [
        field("x", { enabled: false }),
        field("y"),
        field("z"),
    ], { enabled: false })];

    it("reduces a reference to one field", () => {
        const [reduced] = reduceReference(tree, "r", "x");
        expect(reduced.enabled).toBe(true);
        expect(reduced.reference!.reduceTo).toBe("x");
        expect(reduced.reference!.unnest).toBe(true);
        expect(reduced.reference!.children.map((c) => { return c.enabled; }))
            .toEqual([true, false, false]);
    });

    it("undoes the reduction", () => {
        const reduced = reduceReference(tree, "r", "x");
        const [undone] = reduceReference(reduced, "r", undefined);
        expect(undone.enabled).toBe(true);
        expect(undone.reference).not.toHaveProperty("reduceTo");
        expect(undone.reference!.unnest).toBe(false);
        expect(undone.reference!.children.map((c) => { return c.enabled; }))
            .toEqual([true, true, true]);
    });
});

describe("setAllEnabled", () => {
    const tree = [
        field("a", { enabled: false }),
        refField("r", [field("c"), refField("rr", [], { enabled: false })],
            { enabled: false }),
        field("b"),
    ];

    it("sets a top level's columns, not its references", () => {
        const on = setAllEnabled(tree, undefined, true);
        expect(on.map((f) => { return f.enabled; }))
            .toEqual([true, false, true]);
        expect(on[1]).toBe(tree[1]);
        const off = setAllEnabled(tree, undefined, false);
        expect(off.map((f) => { return f.enabled; }))
            .toEqual([false, false, false]);
    });

    it("sets a reference's children", () => {
        const off = setAllEnabled(tree, "r", false);
        expect(off[0]).toBe(tree[0]);
        expect(off[1].enabled).toBe(false);
        expect(off[1].reference!.children.map((f) => { return f.enabled; }))
            .toEqual([false, false]);
        const on = setAllEnabled(tree, "r", true);
        expect(on[1].reference!.children.map((f) => { return f.enabled; }))
            .toEqual([true, false]);
    });
});

describe("crudOperations", () => {
    it("lets a routine be called", () => {
        expect(crudOperations(viewDocument({ objectType: "PROCEDURE" })))
            .toEqual(["CREATE"]);
        expect(crudOperations(viewDocument({ objectType: "FUNCTION" })))
            .toEqual(["CREATE"]);
    });

    it("reads a view and adds what its flags allow", () => {
        expect(crudOperations(viewDocument())).toEqual(["READ"]);
        expect(crudOperations(viewDocument({
            crud: { insert: true, update: false, delete: true, noCheck: true },
        }))).toEqual(["CREATE", "READ", "DELETE"]);
        expect(crudOperations(viewDocument({
            crud: { insert: true, update: true, delete: true, noCheck: false },
        }))).toEqual(["CREATE", "READ", "UPDATE", "DELETE"]);
    });

    it("updates where an enabled reference may change", () => {
        const insert = { ...NO_CRUD, insert: true };
        expect(crudOperations(viewDocument({
            fields: [refField("r", [], {}, insert)],
        }))).toEqual(["READ", "UPDATE"]);
        expect(crudOperations(viewDocument({
            fields: [refField("r", [], { enabled: false }, insert)],
        }))).toEqual(["READ"]);
        expect(crudOperations(viewDocument({
            fields: [refField("r", [], {}, { ...NO_CRUD, noCheck: true })],
        }))).toEqual(["READ"]);
        expect(crudOperations(viewDocument({
            fields: [field("a"), refField("r", [
                refField("rr", [], {}, { ...NO_CRUD, delete: true }),
            ])],
        }))).toEqual(["READ", "UPDATE"]);
        expect(crudOperations(viewDocument({
            fields: [refField("r", [], {}, { ...NO_CRUD, update: true })],
        }))).toEqual(["READ", "UPDATE"]);
    });
});

describe("mappingText", () => {
    it("writes an empty level as {}", () => {
        expect(mappingText([], 0)).toBe("{}");
        expect(mappingText([
            field("off", { enabled: false }),
            field("gone", { missing: true }),
        ], 4)).toBe("{}");
    });

    it("writes a plain column", () => {
        expect(mappingText([field("cityId", { column: { name: "city_id" } })],
            0)).toBe("{\n    `cityId`: `city_id`\n}");
    });

    it("writes the annotations in order", () => {
        const all = field("c", {
            column: { name: "c", datatype: "int", in: true },
            noCheck: true,
            allowSorting: true,
            allowFiltering: false,
            rowOwnership: true,
            isKey: true,
            noUpdate: true,
            jsonSchema: { type: "string" },
        });
        expect(mappingText([all], 0, true)).toBe("{\n    `c`: `c` @IN "
            + "@NOCHECK @SORTABLE @NOFILTERING @ROWOWNERSHIP @KEY "
            + "@DATATYPE('int') @NOUPDATE "
            + "JSON SCHEMA {\"type\":\"string\"}\n}");
        // The data type only for a routine's result.
        expect(mappingText([all], 0)).not.toContain("@DATATYPE");
    });

    it("writes the parameter modes", () => {
        expect(mappingText([
            field("i", { column: { name: "i", in: true, out: false } }),
            field("o", { column: { name: "o", in: false, out: true } }),
            field("io", { column: { name: "io", in: true, out: true } }),
        ], 0)).toBe([
            "{",
            "    `i`: `i` @IN,",
            "    `o`: `o` @OUT,",
            "    `io`: `io` @INOUT",
            "}",
        ].join("\n"));
    });

    it("writes no data type where there is none", () => {
        expect(mappingText([
            field("a", { column: { name: "a" } }),
            field("b", { column: { name: "b", datatype: "" } }),
        ], 0, true)).toBe("{\n    `a`: `a`,\n    `b`: `b`\n}");
    });

    it("writes references with explicit CRUD flags", () => {
        const store = refField("store", [
            field("id", { isKey: true }),
            field("off", { enabled: false }),
        ]);
        store.name = "theStore";
        store.reference!.unnest = true;
        store.reference!.crud = {
            insert: true, update: false, delete: false, noCheck: true,
        };
        const empty = refField("plain", [], {}, {
            insert: false, update: true, delete: true, noCheck: false,
        });
        expect(mappingText([
            store,
            empty,
            refField("disabled", [field("x")], { enabled: false }),
        ], 4)).toBe([
            "{",
            "        `theStore`: `sakila`.`store` @UNNEST @INSERT "
            + "@NOUPDATE @NODELETE @NOCHECK {",
            "            `id`: `id` @KEY",
            "        },",
            "        `plain`: `sakila`.`plain` @NOINSERT @UPDATE "
            + "@DELETE {}",
            "    }",
        ].join("\n"));
    });

    it("passes the data type down to a reference's children", () => {
        const r = refField("r", [field("x", {
            column: { name: "x", datatype: "int" },
        })]);
        expect(mappingText([r], 0, true))
            .toContain("`x`: `x` @DATATYPE('int')");
    });
});

describe("objectSql", () => {
    const place = {
        servicePath: "/svc", schemaPath: "/sakila", requestPath: "/city",
    };

    it("creates a view", () => {
        expect(objectSql(viewDocument({
            crud: { insert: true, update: false, delete: true, noCheck: true },
        }), place, settings({ options: { a: 1 } }))).toBe([
            "CREATE REST VIEW /city",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    AS `sakila`.`city` CLASS `MyCity` @INSERT @DELETE @NOCHECK {",
            "        `id`: `id` @KEY",
            "    }",
            "    ENABLED",
            "    AUTHENTICATION REQUIRED",
            "    ITEMS PER PAGE 25",
            "    COMMENT ''",
            "    FORMAT FEED",
            "    OPTIONS {",
            "        \"a\": 1",
            "    };",
        ].join("\n"));
    });

    it("creates a view without CRUD flags or fields", () => {
        expect(objectSql(viewDocument({ fields: [] }), {
            servicePath: "m,'a@b'@/s-v",
            schemaPath: "/sa.kila",
            requestPath: "/ci-ty",
        }, settings()).split("\n").slice(0, 3)).toEqual([
            "CREATE REST VIEW `/ci-ty`",
            "    ON SERVICE m,'a@b'@`/s-v` SCHEMA `/sa.kila`",
            "    AS `sakila`.`city` CLASS `MyCity` {}",
        ]);
    });

    it("writes a view's update flag", () => {
        expect(objectSql(viewDocument({
            fields: [],
            crud: { ...NO_CRUD, update: true },
        }), place, settings()).split("\n")[2])
            .toBe("    AS `sakila`.`city` CLASS `MyCity` @UPDATE {}");
    });

    it("writes a routine without parameters or results", () => {
        expect(objectSql(viewDocument({
            objectType: "PROCEDURE", dbObject: "p", fields: [],
        }), { ...place, requestPath: "/p" }, settings())).toBe([
            "CREATE REST PROCEDURE /p",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    AS `sakila`.`p`",
            "    ENABLED",
            "    AUTHENTICATION REQUIRED",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("alters a view in place", () => {
        expect(objectSql(viewDocument(), place, settings(), "/city")
            .split("\n").slice(0, 6)).toEqual([
            "ALTER REST VIEW /city",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    CLASS `MyCity` {",
            "        `id`: `id` @KEY",
            "    }",
            "    ENABLED",
        ]);
    });

    it("moves a view to a new path", () => {
        expect(objectSql(viewDocument(), place, settings(), "/old")
            .split("\n").slice(0, 4)).toEqual([
            "ALTER REST VIEW /old",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    NEW REQUEST PATH /city",
            "    CLASS `MyCity` {",
        ]);
    });

    const procedure = viewDocument({
        objectType: "PROCEDURE",
        dbObject: "proc",
        fields: [],
        parameters: {
            key: "p",
            name: "ProcParams",
            fields: [field("a", { column: { name: "a", in: true } })],
        },
        results: [{
            key: "r",
            name: "ProcResult",
            fields: [field("x", { column: { name: "x", datatype: "int" } })],
        }, {
            key: "r2",
            name: "Empty",
            fields: [],
        }],
    });
    const procPlace = { ...place, requestPath: "/proc" };

    it("creates a procedure with its parameters and results", () => {
        expect(objectSql(procedure, procPlace, settings({
            itemsPerPage: 5, format: "ITEM",
        }))).toBe([
            "CREATE REST PROCEDURE /proc",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    AS `sakila`.`proc`",
            "    PARAMETERS `ProcParams` {",
            "        `a`: `a` @IN",
            "    }",
            "    RESULT `ProcResult` {",
            "        `x`: `x` @DATATYPE('int')",
            "    }",
            "    RESULT `Empty` {}",
            "    ENABLED",
            "    AUTHENTICATION REQUIRED",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("alters a function, moving it", () => {
        const fn = viewDocument({
            objectType: "FUNCTION",
            dbObject: "fn",
            fields: [],
            results: [{
                key: "r", name: "FnResult",
                fields: [field("result", {
                    column: { name: "result", datatype: "int" },
                })],
            }],
        });
        expect(objectSql(fn, { ...place, requestPath: "/fn2" }, settings(),
            "/fn")).toBe([
            "ALTER REST FUNCTION /fn",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    NEW REQUEST PATH /fn2",
            "    RESULT `FnResult` {",
            "        `result`: `result` @DATATYPE('int')",
            "    }",
            "    ENABLED",
            "    AUTHENTICATION REQUIRED",
            "    COMMENT ''",
            "    OPTIONS {};",
        ].join("\n"));
    });

    it("alters a procedure in place", () => {
        const sql = objectSql(procedure, procPlace, settings(), "/proc");
        expect(sql.split("\n").slice(0, 3)).toEqual([
            "ALTER REST PROCEDURE /proc",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    PARAMETERS `ProcParams` {",
        ]);
        expect(sql).not.toContain("NEW REQUEST PATH");
        expect(sql).not.toContain("CLASS");
    });
});
