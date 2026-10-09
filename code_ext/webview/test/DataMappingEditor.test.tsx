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

import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { posted } from "./setup.js";
import { MrsDialog } from "../src/mrs/MrsDialog.js";
import {
    buildDocument,
    findField,
    type IMappingDocument,
    type IMappingField,
} from "../../src/mrs/dataMapping.js";
import {
    objectDefaults,
    objectStatements,
    type IObjectContext,
    type IObjectDialogValues,
} from "../../src/mrs/mrsDialogs.js";
import type { MrsHostMessage } from "../../src/mrs/mrsDialogProtocol.js";
import type {
    IMrsColumns,
    IMrsReferenceMapping,
    IMrsTableColumn,
} from "../../src/mrs/mrsTypes.js";

/**
 * The Data Mapping tab of the REST Object dialog, driven inside the dialog
 * the host loads, so every edit is read back from what OK saves - the
 * document as the host would get it.
 */

let host: HTMLDivElement;

const tick = async (): Promise<void> => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
};

const send = async (message: MrsHostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await tick();
        await tick();
    });
};

const CONTEXT: IObjectContext = {
    services: ["/myService"],
    schemas: { "/myService": ["/sakila"] },
};

const column = (
    position: number,
    name: string,
    datatype: string,
    primary = false,
): IMrsTableColumn => {
    return {
        position, name, reference_mapping: null,
        db_column: { name, datatype, ...(primary ? { is_primary: true } : {}) },
    };
};

const reference = (
    position: number,
    name: string,
    kind: IMrsReferenceMapping["kind"],
    table: string,
    on: string,
): IMrsTableColumn => {
    return {
        position, name, db_column: null,
        reference_mapping: {
            kind, to_many: kind === "1:n", referenced_schema: "sakila",
            referenced_table: table,
            column_mapping: [{ base: on, ref: on }],
        },
    };
};

const CITY: IMrsColumns = {
    schema: "sakila",
    name: "city",
    type: "TABLE",
    columns: [
        column(1, "city_id", "smallint", true),
        column(2, "city", "varchar(50)"),
        column(3, "country_id", "smallint"),
        reference(4, "country", "n:1", "country", "country_id"),
        reference(5, "address", "1:n", "address", "city_id"),
    ],
};

const COUNTRY: IMrsColumns = {
    schema: "sakila",
    name: "country",
    type: "TABLE",
    columns: [
        column(1, "country_id", "smallint", true),
        column(2, "country", "varchar(50)"),
        // Back to where it came from: left out.
        reference(3, "city", "1:n", "city", "country_id"),
    ],
};

const ADDRESS: IMrsColumns = {
    schema: "sakila",
    name: "address",
    type: "TABLE",
    columns: [
        column(1, "address_id", "smallint", true),
        column(2, "address", "varchar(50)"),
        column(3, "postal_code", "varchar(10)"),
    ],
};

const tableValues = (): IObjectDialogValues => {
    return objectDefaults({ name: "city", object_type: "TABLE" },
        buildDocument("TABLE", "sakila", "city", CITY, [],
            "MyServiceSakilaCity"),
        "/myService", "/sakila");
};

const routineValues = (
    objectType: "PROCEDURE" | "FUNCTION",
): IObjectDialogValues => {
    return objectDefaults({ name: "film_in_stock", object_type: objectType },
        buildDocument(objectType, "sakila", "film_in_stock", {
            schema: "sakila", name: "film_in_stock", type: objectType,
            parameters: [
                {
                    position: 1, name: "p_film_id", mode: "IN",
                    datatype: "int",
                },
                {
                    position: 2, name: "p_film_count", mode: "OUT",
                    datatype: "int",
                },
                {
                    position: 3, name: "p_note", mode: "INOUT",
                    datatype: "text",
                },
            ],
            return_type: "decimal(5,2)",
        }, [], "FilmInStock"),
        "/myService", "/sakila");
};

const load = async (values: IObjectDialogValues): Promise<void> => {
    await act(async () => {
        render(<MrsDialog />, host);
        await Promise.resolve();
    });
    await send({
        type: "load", dialog: "object", title: "REST Object", values,
        context: CONTEXT,
    });
};

const act0 = async (action: () => void): Promise<void> => {
    await act(async () => {
        action();
        await tick();
    });
};

const rowOf = (name: string): HTMLElement => {
    const row = host.querySelector<HTMLElement>(
        `.mrs-row[data-field='${name}']`);
    if (!row) {
        const have = [...host.querySelectorAll<HTMLElement>(".mrs-row")]
            .map((node) => { return node.dataset.field; });
        throw new Error(`No row '${name}'. Have: ${have.join(", ")}`);
    }

    return row;
};

const rowNames = (): string[] => {
    return [...host.querySelectorAll<HTMLElement>(".mrs-row")].map((node) => {
        return node.dataset.field ?? "";
    });
};

const byLabel = <T extends HTMLElement>(
    label: string,
    within: ParentNode = host,
): T => {
    const element = within.querySelector<T>(`[aria-label='${label}']`);
    if (!element) {
        throw new Error(`Nothing labelled '${label}'.`);
    }

    return element;
};

const clickOn = async (element: HTMLElement): Promise<void> => {
    await act0(() => { element.click(); });
};

const typeInto = async (
    input: HTMLInputElement,
    value: string,
): Promise<void> => {
    await act0(() => {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
};

const pick = async (
    select: HTMLSelectElement,
    value: string,
): Promise<void> => {
    await act0(() => {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
    });
};

const buttonOf = (label: string, within: ParentNode = host):
HTMLButtonElement | undefined => {
    return [...within.querySelectorAll("button")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
};

const pill = (label: string, within: ParentNode): HTMLButtonElement => {
    const button = [...within.querySelectorAll<HTMLButtonElement>(
        ".mrs-pill")].find((node) => {
        return node.textContent === label;
    });
    if (!button) {
        throw new Error(`No pill '${label}'.`);
    }

    return button;
};

const pills = (within: ParentNode): string[] => {
    return [...within.querySelectorAll(".mrs-pill")].map((node) => {
        return node.textContent ?? "";
    });
};

const flag = (name: string, tooltip: string): HTMLButtonElement => {
    return byLabel<HTMLButtonElement>(tooltip, rowOf(name));
};

const toolbar = (): HTMLElement => {
    return host.querySelector<HTMLElement>(".mrs-mapping-toolbar")!;
};

const postedOf = (kind: string): Array<Record<string, unknown>> => {
    return posted.filter((message) => {
        return message.type === kind;
    }) as Array<Record<string, unknown>>;
};

/** Presses OK and returns the document saved. */
const saved = async (): Promise<IMappingDocument> => {
    posted.length = 0;
    await clickOn(host.querySelector<HTMLButtonElement>(
        ".editor-footer button.primary")!);
    const save = postedOf("save").at(-1);
    if (save === undefined) {
        throw new Error("Nothing was saved.");
    }

    return (save.values as IObjectDialogValues).document;
};

const fieldNamed = (
    fields: IMappingField[],
    name: string,
): IMappingField => {
    const walk = (level: IMappingField[]): IMappingField | undefined => {
        for (const field of level) {
            if (field.name === name) {
                return field;
            }
            const found = walk(field.reference?.children ?? []);
            if (found !== undefined) {
                return found;
            }
        }

        return undefined;
    };
    const found = walk(fields);
    if (found === undefined) {
        throw new Error(`No field '${name}'.`);
    }

    return found;
};

/** Opens a reference and answers its `loadColumns` with the columns. */
const openReference = async (
    name: string,
    columns: IMrsColumns,
): Promise<void> => {
    await clickOn(byLabel("Expand", rowOf(name)));
    const request = postedOf("loadColumns").at(-1)!;
    await send({
        type: "columns", requestId: request.requestId as number, columns,
    });
};

beforeEach(() => {
    posted.length = 0;
    host = document.createElement("div");
    document.body.append(host);
});

afterEach(() => {
    render(null, host);
    host.remove();
});

describe("DataMappingEditor of a table", () => {
    it("lists the columns and references", async () => {
        await load(tableValues());

        expect(rowNames()).toEqual(["cityId", "city", "countryId", "country",
            "address"]);
        expect(rowOf("cityId").textContent).toContain("smallint");
        expect(rowOf("country").textContent).toContain("sakila.country");
        expect(rowOf("country").textContent).toContain("n:1");
        expect(rowOf("country").className).toContain("disabled");
        expect(byLabel<HTMLInputElement>("Class name").value)
            .toBe("MyServiceSakilaCity");
    });

    it("toggles a field with its checkbox", async () => {
        await load(tableValues());

        await clickOn(byLabel("Include city", rowOf("city")));

        expect(rowOf("city").className).toContain("disabled");
        expect(fieldNamed((await saved()).fields, "city").enabled).toBe(false);

        await clickOn(byLabel("Include city", rowOf("city")));
        expect(fieldNamed((await saved()).fields, "city").enabled).toBe(true);
    });

    it("renames a field", async () => {
        await load(tableValues());

        await typeInto(byLabel("JSON field name", rowOf("city")), "cityName");

        expect(rowNames()).toContain("cityName");
        const document = await saved();
        expect(fieldNamed(document.fields, "cityName").column?.name)
            .toBe("city");
    });

    it("toggles the flags of a field", async () => {
        await load(tableValues());

        const key = flag("city", "Include field in object composite key");
        expect(key.getAttribute("aria-pressed")).toBe("false");
        await clickOn(key);
        expect(flag("city", "Include field in object composite key")
            .getAttribute("aria-pressed")).toBe("true");
        await clickOn(flag("city",
            "Allow sorting operations using this field"));
        await clickOn(flag("city",
            "Prevent filtering operations on this field"));
        await clickOn(flag("city",
            "Prevent updates on this field"));
        await clickOn(flag("city",
            "Exclude this field from ETAG calculations"));

        expect(fieldNamed((await saved()).fields, "city")).toMatchObject({
            isKey: true,
            allowSorting: true,
            allowFiltering: false,
            noUpdate: true,
            noCheck: true,
        });
        expect(flag("city", "Prevent filtering operations on this field")
            .getAttribute("aria-pressed")).toBe("true");
    });

    it("lets one field per level own the rows", async () => {
        await load(tableValues());

        await clickOn(flag("cityId", "Set as row ownership field"));
        await clickOn(flag("city", "Set as row ownership field"));

        let document = await saved();
        expect(fieldNamed(document.fields, "cityId").rowOwnership).toBe(false);
        expect(fieldNamed(document.fields, "city").rowOwnership).toBe(true);

        await clickOn(flag("city", "Set as row ownership field"));
        document = await saved();
        expect(document.fields.some((field) => {
            return field.rowOwnership;
        })).toBe(false);
    });

    it("toggles the object's CRUD pills", async () => {
        await load(tableValues());

        expect(pills(toolbar())).toEqual(["INSERT", "UPDATE", "DELETE",
            "NOCHECK"]);
        await clickOn(pill("INSERT", toolbar()));
        await clickOn(pill("DELETE", toolbar()));
        await clickOn(pill("NOCHECK", toolbar()));

        expect(pill("INSERT", toolbar()).getAttribute("aria-pressed"))
            .toBe("true");
        expect((await saved()).crud).toEqual({
            insert: true, update: false, delete: true, noCheck: true,
        });
    });

    it("edits the class name", async () => {
        await load(tableValues());

        await typeInto(byLabel("Class name"), "City");

        expect((await saved()).className).toBe("City");
    });

    it("shows the class name problem once it was cleared", async () => {
        await load(tableValues());

        await typeInto(byLabel("Class name"), "");

        expect(host.querySelector(".mrs-mapping .message.error")?.textContent)
            .toBe("The object name must not be empty.");
    });

    it("selects and deselects all fields but the references", async () => {
        await load(tableValues());

        await clickOn(byLabel("Deselect all fields"));
        let document = await saved();
        expect(document.fields.map((field) => { return field.enabled; }))
            .toEqual([false, false, false, false, false]);

        await openReference("country", COUNTRY);
        await clickOn(byLabel("Select all fields"));
        document = await saved();
        expect(document.fields.map((field) => { return field.enabled; }))
            .toEqual([true, true, true, true, false]);
    });

    it("loads a reference's columns when it is first opened", async () => {
        await load(tableValues());

        await clickOn(byLabel("Expand", rowOf("country")));
        expect(postedOf("loadColumns").at(-1)).toMatchObject({
            type: "loadColumns", schema: "sakila", table: "country",
        });
        expect(rowNames()).not.toContain("countryId2");

        const request = postedOf("loadColumns").at(-1)!;
        await send({
            type: "columns", requestId: request.requestId as number,
            columns: COUNTRY,
        });

        // The back reference to city is left out.
        expect(rowNames()).toEqual(["cityId", "city", "countryId", "country",
            "countryId", "country", "address"]);
        expect(rowOf("country").className).not.toContain("disabled");
        expect(byLabel("Collapse", rowOf("country"))).toBeDefined();
        const children = host.querySelectorAll<HTMLElement>(".mrs-json");
        expect(children[4].style.paddingLeft).toBe("1.25rem");

        const document = await saved();
        const country = fieldNamed(document.fields, "country");
        expect(country.enabled).toBe(true);
        expect(country.reference?.loaded).toBe(true);
        expect(country.reference?.children.map((child) => {
            return child.name;
        })).toEqual(["countryId", "country"]);
    });

    it("collapses and reopens a loaded reference without loading again",
        async () => {
            await load(tableValues());
            await openReference("country", COUNTRY);
            const requests = postedOf("loadColumns").length;

            await clickOn(byLabel("Collapse", rowOf("country")));
            expect(rowNames()).toHaveLength(5);
            await clickOn(byLabel("Expand", rowOf("country")));

            expect(rowNames()).toHaveLength(7);
            expect(postedOf("loadColumns")).toHaveLength(requests);
        });

    it("loads a reference when its checkbox is ticked", async () => {
        await load(tableValues());

        await clickOn(byLabel("Include country", rowOf("country")));

        const request = postedOf("loadColumns").at(-1)!;
        expect(request.table).toBe("country");
        await send({
            type: "columns", requestId: request.requestId as number,
            columns: COUNTRY,
        });
        expect(rowNames()).toHaveLength(7);
    });

    it("shows why a reference's columns could not be loaded", async () => {
        await load(tableValues());

        await clickOn(byLabel("Expand", rowOf("country")));
        const request = postedOf("loadColumns").at(-1)!;
        await send({
            type: "columns", requestId: request.requestId as number,
            error: "Table not found.",
        });

        expect(host.querySelector(".mrs-mapping .message.error")?.textContent)
            .toBe("Table not found.");
        expect(rowNames()).toHaveLength(5);
        expect(fieldNamed((await saved()).fields, "country").enabled)
            .toBe(false);
    });

    it("keeps an edit made while a reference was loading", async () => {
        await load(tableValues());

        await clickOn(byLabel("Expand", rowOf("country")));
        await typeInto(byLabel("JSON field name", rowOf("city")), "cityName");
        const request = postedOf("loadColumns").at(-1)!;
        await send({
            type: "columns", requestId: request.requestId as number,
            columns: COUNTRY,
        });

        expect(rowNames()).toContain("cityName");
    });

    it("offers UPDATE and NOCHECK only on an n:1 reference", async () => {
        await load(tableValues());
        expect(pills(rowOf("country"))).toEqual([]);

        await openReference("country", COUNTRY);

        const row = rowOf("country");
        expect(pills(row)).toEqual(["UPDATE", "NOCHECK", "UNNEST"]);
        await clickOn(pill("UPDATE", rowOf("country")));
        await clickOn(pill("NOCHECK", rowOf("country")));
        await clickOn(pill("UNNEST", rowOf("country")));

        const country = fieldNamed((await saved()).fields, "country");
        expect(country.reference?.crud).toEqual({
            insert: false, update: true, delete: false, noCheck: true,
        });
        expect(country.reference?.unnest).toBe(true);
    });

    it("offers all CRUD pills and reduce-to on a 1:n reference",
        async () => {
            await load(tableValues());
            await openReference("address", ADDRESS);

            const row = rowOf("address");
            expect(pills(row)).toEqual(["INSERT", "UPDATE", "DELETE",
                "NOCHECK"]);
            await clickOn(pill("INSERT", row));
            const reduce = byLabel<HTMLSelectElement>("Reduce to field",
                rowOf("address"));
            expect([...reduce.options].map((option) => {
                return option.textContent;
            })).toEqual(["Unnest field ...", "addressId", "address",
                "postalCode"]);

            let document = await saved();
            const target = fieldNamed(document.fields, "postalCode").key;
            await pick(byLabel<HTMLSelectElement>("Reduce to field",
                rowOf("address")), target);

            document = await saved();
            const address = document.fields.find((field) => {
                return field.name === "address";
            })!;
            expect(address.reference).toMatchObject({
                reduceTo: target, unnest: true,
                crud: { insert: true },
            });
            expect(address.reference?.children.map((child) => {
                return child.enabled;
            })).toEqual([false, false, true]);
            // The others are reduced away and cannot be ticked.
            const boxes = [...host.querySelectorAll<HTMLInputElement>(
                "[aria-label='Include addressId']")];
            expect(boxes.at(-1)?.disabled).toBe(true);

            await pick(byLabel<HTMLSelectElement>("Reduce to field",
                rowOf("address")), "");
            document = await saved();
            const undone = findField(document.fields, address.key)!;
            expect(undone.reference?.reduceTo).toBeUndefined();
            expect(undone.reference?.unnest).toBe(false);
        });

    it("shows the SQL preview, and copies it", async () => {
        const values = tableValues();
        await load(values);

        const preview = [...host.querySelectorAll("label.checkbox")]
            .find((node) => {
                return node.textContent === "SQL Preview";
            })!.querySelector("input")!;
        await clickOn(preview);

        const sql = objectStatements(values, CONTEXT)[0];
        expect(host.querySelector(".mrs-preview")?.textContent).toBe(sql);
        expect(host.querySelector(".mrs-mapping-rows")).toBeNull();

        await clickOn(byLabel("Copy SQL to Clipboard"));
        expect(postedOf("copy")).toEqual([{ type: "copy", text: sql }]);

        await clickOn(preview);
        expect(host.querySelector(".mrs-preview")).toBeNull();
        expect(rowNames()).toHaveLength(5);
    });

    it("previews the edits made", async () => {
        await load(tableValues());

        await typeInto(byLabel("Class name"), "Town");
        await clickOn(byLabel("Include city", rowOf("city")));
        await clickOn(byLabel("Copy SQL to Clipboard"));

        const sql = String(postedOf("copy").at(-1)?.text);
        expect(sql).toContain("CLASS `Town`");
        expect(sql).not.toContain("`city`: `city`");
    });

    it("says when there are no fields", async () => {
        await load({
            ...tableValues(),
            document: { ...tableValues().document, fields: [] },
        });

        expect(host.querySelector(".mrs-mapping-rows")?.textContent)
            .toBe("No fields.");
    });

    it("opens what is stored, and marks a dropped column", async () => {
        const values = tableValues();
        const document = values.document;
        const country = document.fields[3];
        await load({
            ...values,
            document: {
                ...document,
                fields: [
                    ...document.fields.slice(0, 3),
                    {
                        ...country,
                        enabled: true,
                        reference: {
                            ...country.reference!,
                            loaded: true,
                            children: [{
                                ...document.fields[0], key: "c1", name: "id",
                            }],
                        },
                    },
                    {
                        ...document.fields[1], key: "gone", name: "oldName",
                        missing: true, enabled: false,
                    },
                ],
            },
        });

        expect(rowNames()).toEqual(["cityId", "city", "countryId", "country",
            "id", "oldName"]);
        expect(rowOf("oldName").textContent).toContain("column dropped");
        expect(byLabel<HTMLInputElement>("Include oldName").disabled)
            .toBe(true);
    });
});

describe("DataMappingEditor of a procedure", () => {
    const mappings = (): string[] => {
        return [...byLabel<HTMLSelectElement>("Data mapping").options]
            .map((option) => { return option.textContent ?? ""; });
    };

    const mappingName = (): HTMLInputElement => {
        return byLabel<HTMLInputElement>("Data mapping name");
    };

    it("shows the parameters with their modes", async () => {
        await load(routineValues("PROCEDURE"));

        expect(mappings()).toEqual(["Parameters"]);
        expect(mappingName().value).toBe("FilmInStockParams");
        expect(rowNames()).toEqual(["pFilmId", "pFilmCount", "pNote"]);
        expect(rowOf("pFilmId").querySelector(".mrs-mode")?.textContent)
            .toBe("IN");
        expect(rowOf("pFilmCount").querySelector(".mrs-mode")?.textContent)
            .toBe("OUT");
        expect(rowOf("pNote").querySelector(".mrs-mode")?.textContent)
            .toBe("INOUT");
        expect(host.querySelector("[aria-label='Class name']")).toBeNull();
        expect(host.querySelector(".mrs-flag")).toBeNull();
        expect(host.querySelector("[aria-label='Delete field']")).toBeNull();
        expect(buttonOf("Remove Result")).toBeUndefined();
        expect(buttonOf("Add Field")).toBeUndefined();
    });

    it("renames the parameters mapping", async () => {
        await load(routineValues("PROCEDURE"));

        await typeInto(mappingName(), "StockParams");

        expect((await saved()).parameters?.name).toBe("StockParams");
    });

    it("adds, fills and removes results", async () => {
        await load(routineValues("PROCEDURE"));

        await clickOn(buttonOf("Add Result")!);

        expect(mappings()).toEqual(["Parameters", "Result 1"]);
        expect(byLabel<HTMLSelectElement>("Data mapping").value)
            .not.toBe("parameters");
        expect(mappingName().value).toBe("FilmInStock");
        expect(host.querySelector(".mrs-mapping-rows")?.textContent)
            .toBe("No fields.");

        await clickOn(buttonOf("Add Field")!);
        expect(rowNames()).toEqual(["newField"]);
        const row = rowOf("newField");
        expect(byLabel<HTMLInputElement>("Column name", row).value)
            .toBe("new_field");
        expect(byLabel<HTMLInputElement>("Datatype", row).value)
            .toBe("VARCHAR(255)");

        await typeInto(byLabel("Datatype", rowOf("newField")), "INT");
        await typeInto(byLabel("Column name", rowOf("newField")), "total");
        await typeInto(byLabel("JSON field name", rowOf("newField")), "total");
        await typeInto(mappingName(), "Totals");

        let document = await saved();
        expect(document.results).toHaveLength(1);
        expect(document.results![0].name).toBe("Totals");
        expect(document.results![0].fields[0]).toMatchObject({
            name: "total",
            column: { name: "total", datatype: "INT" },
        });

        await clickOn(buttonOf("Add Result")!);
        expect(mappings()).toEqual(["Parameters", "Result 1", "Result 2"]);
        expect(mappingName().value).toBe("FilmInStock2");

        await clickOn(buttonOf("Remove Result")!);
        expect(mappings()).toEqual(["Parameters", "Result 1"]);
        expect(mappingName().value).toBe("FilmInStockParams");
        expect(buttonOf("Remove Result")).toBeUndefined();

        document = await saved();
        expect(document.results?.map((result) => {
            return result.name;
        })).toEqual(["Totals"]);
    });

    it("switches between the parameters and a result", async () => {
        await load(routineValues("PROCEDURE"));
        await clickOn(buttonOf("Add Result")!);
        await clickOn(buttonOf("Add Field")!);
        const select = byLabel<HTMLSelectElement>("Data mapping");
        const resultKey = select.value;

        await pick(byLabel("Data mapping"), "parameters");
        expect(rowNames()).toEqual(["pFilmId", "pFilmCount", "pNote"]);
        expect(buttonOf("Remove Result")).toBeUndefined();

        await pick(byLabel("Data mapping"), resultKey);
        expect(rowNames()).toEqual(["newField"]);
        expect(buttonOf("Remove Result")).toBeDefined();
    });

    it("deletes a result field", async () => {
        await load(routineValues("PROCEDURE"));
        await clickOn(buttonOf("Add Result")!);
        await clickOn(buttonOf("Add Field")!);
        await clickOn(buttonOf("Add Field")!);
        expect(rowNames()).toEqual(["newField", "newField"]);

        await clickOn(byLabel("Delete field",
            host.querySelectorAll(".mrs-row")[0] as HTMLElement));

        expect(rowNames()).toEqual(["newField"]);
        expect((await saved()).results![0].fields).toHaveLength(1);
    });

    it("previews the procedure's SQL with its results", async () => {
        const values = routineValues("PROCEDURE");
        await load(values);
        await clickOn(buttonOf("Add Result")!);
        await clickOn(buttonOf("Add Field")!);
        await clickOn(byLabel("Copy SQL to Clipboard"));

        const sql = String(postedOf("copy").at(-1)?.text);
        expect(sql).toContain("CREATE REST PROCEDURE");
        expect(sql).toContain("PARAMETERS `FilmInStockParams`");
        expect(sql).toContain("RESULT `FilmInStock`");
        expect(sql).toContain("@DATATYPE('VARCHAR(255)')");
    });
});

describe("DataMappingEditor of a function", () => {
    it("has one result, and no adding or deleting", async () => {
        await load(routineValues("FUNCTION"));

        const select = byLabel<HTMLSelectElement>("Data mapping");
        expect([...select.options].map((option) => {
            return option.textContent;
        })).toEqual(["Parameters", "Result 1"]);
        expect(buttonOf("Add Result")).toBeUndefined();

        await pick(select, select.options[1].value);

        expect(rowNames()).toEqual(["result"]);
        expect(byLabel<HTMLInputElement>("Data mapping name").value)
            .toBe("FilmInStockResult");
        expect(buttonOf("Add Result")).toBeUndefined();
        expect(buttonOf("Remove Result")).toBeUndefined();
        expect(buttonOf("Add Field")).toBeUndefined();
        expect(host.querySelector("[aria-label='Delete field']")).toBeNull();
        const datatype = byLabel<HTMLInputElement>("Datatype", rowOf("result"));
        expect(datatype.value).toBe("decimal(5,2)");

        await typeInto(datatype, "decimal(10,2)");
        await typeInto(byLabel("Data mapping name"), "Balance");

        const document = await saved();
        expect(document.results![0].name).toBe("Balance");
        expect(document.results![0].fields[0].column?.datatype)
            .toBe("decimal(10,2)");
    });
});
