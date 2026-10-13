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
        throw new Error(`No row '${name}'. Have: ${rowNames().join(", ")}`);
    }

    return row;
};

/** The field rows, by their JSON names; not the brackets, not the copies. */
const rowNames = (): string[] => {
    return [...host.querySelectorAll<HTMLElement>(".mrs-row[data-field]")]
        .map((node) => { return node.dataset.field ?? ""; });
};

/** The copies an unnested reference shows of its fields. */
const copyNames = (): string[] => {
    return [...host.querySelectorAll<HTMLElement>(".mrs-row[data-copy]")]
        .map((node) => { return node.dataset.copy ?? ""; });
};

const topRow = (): HTMLElement => {
    return host.querySelector<HTMLElement>(".mrs-row.topRow")!;
};

const query = <T extends HTMLElement>(
    label: string,
    within: ParentNode = host,
): T | null => {
    return within.querySelector<T>(`[aria-label='${label}']`);
};

const byLabel = <T extends HTMLElement>(
    label: string,
    within: ParentNode = host,
): T => {
    const element = query<T>(label, within);
    if (!element) {
        throw new Error(`Nothing labelled '${label}'.`);
    }

    return element;
};

const clickOn = async (element: HTMLElement): Promise<void> => {
    await act0(() => { element.click(); });
};

const doubleClick = async (element: HTMLElement): Promise<void> => {
    await act0(() => {
        element.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
};

const press = async (element: HTMLElement, key: string): Promise<void> => {
    await act0(() => {
        element.dispatchEvent(new KeyboardEvent("keydown",
            { key, bubbles: true, cancelable: true }));
    });
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

/** Waits out the wait of a single click on a reference row. */
const clickWait = async (): Promise<void> => {
    await act(async () => {
        await new Promise((resolve) => { setTimeout(resolve, 250); });
        await tick();
    });
};

/**
 * Edits a name as the user does: a double click on it opens the inline
 * editor, the text is typed, Enter keeps it.
 */
const rename = async (
    target: HTMLElement,
    label: string,
    value: string,
): Promise<void> => {
    await doubleClick(target);
    const input = byLabel<HTMLInputElement>(label);
    await typeInto(input, value);
    await press(byLabel(label), "Enter");
};

const fieldName = (name: string): HTMLElement => {
    return rowOf(name).querySelector<HTMLElement>(".fieldName")!;
};

const className = (): HTMLElement => {
    return topRow().querySelector<HTMLElement>(".mrsObjectName")!;
};

const buttonOf = (label: string, within: ParentNode = host):
HTMLButtonElement | undefined => {
    return [...within.querySelectorAll("button")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
};

/** The switches of a row: its CRUD pills and its Unnest. */
const pills = (within: ParentNode): string[] => {
    return [...within.querySelectorAll("[role='switch']")].map((node) => {
        return node.textContent ?? "";
    });
};

const pill = (label: string, within: ParentNode): HTMLElement => {
    const found = [...within.querySelectorAll<HTMLElement>(
        "[role='switch']")].find((node) => {
        return node.textContent === label;
    });
    if (!found) {
        throw new Error(`No pill '${label}'. Have: ${pills(within).join(", ")}`);
    }

    return found;
};

const flag = (name: string, tooltip: string): HTMLElement => {
    return byLabel<HTMLElement>(tooltip, rowOf(name));
};

const checkbox = (name: string): HTMLElement => {
    return byLabel<HTMLElement>(`Include ${name}`, rowOf(name));
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

/** Answers the last `loadColumns` request. */
const answer = async (columns: IMrsColumns): Promise<void> => {
    const request = postedOf("loadColumns").at(-1)!;
    await send({
        type: "columns", requestId: request.requestId as number, columns,
    });
};

/** Opens a reference and answers its `loadColumns` with the columns. */
const openReference = async (
    name: string,
    columns: IMrsColumns,
): Promise<void> => {
    await clickOn(byLabel("Expand", rowOf(name)));
    await answer(columns);
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
    it("lists the columns and references under the class", async () => {
        await load(tableValues());

        expect(rowNames()).toEqual(["cityId", "city", "countryId", "country",
            "address"]);
        expect(className().textContent).toBe("MyServiceSakilaCity");
        expect(topRow().textContent).toContain("sakila.city");
        expect([...host.querySelectorAll(".bracket")].map((node) => {
            return node.textContent;
        })).toEqual(["{", "}"]);
        expect(rowOf("cityId").textContent).toContain("city_id");
        expect(byLabel("Primary key column", rowOf("cityId"))).toBeDefined();
        expect(byLabel("Table column", rowOf("city"))).toBeDefined();
        expect(rowOf("country").textContent).toContain("sakila.country");
        expect(byLabel("Table with a n:1 relationship", rowOf("country")))
            .toBeDefined();
        expect(rowOf("country").className).toContain("disabled");
        expect(checkbox("country").getAttribute("aria-checked")).toBe("false");
    });

    it("draws the icons as masks, not as buttons", async () => {
        await load(tableValues());

        const icon = flag("city", "Allow sorting operations using this field");
        expect(icon.tagName).toBe("DIV");
        expect(icon.className).toContain("mrs-icon");
        expect(icon.style.maskImage).toContain("url(");
        expect(rowOf("city").querySelector(".fieldOptions button")).toBeNull();
    });

    it("shows the flags that are on, the others only on hover", async () => {
        await load(tableValues());

        // The primary key is the object's key, so that flag is on.
        expect(flag("cityId", "Include field in object composite key")
            .className).toContain("selected");
        const off = flag("city", "Include field in object composite key");
        expect(off.className).toContain("notSelected");
        // The … stands in for them until the pointer is over the cell.
        expect(rowOf("city").querySelector(".fieldOptions .label")
            ?.textContent).toBe("…");
        // Select and deselect all are actions, shown on hover only.
        expect(byLabel("Select all fields", topRow()).className)
            .toContain("action");
    });

    it("toggles a field with its checkbox", async () => {
        await load(tableValues());

        await clickOn(checkbox("city"));

        expect(rowOf("city").className).toContain("disabled");
        expect(checkbox("city").getAttribute("aria-checked")).toBe("false");
        expect(rowOf("city").querySelector(".jsonFieldDisabled")).not.toBeNull();
        expect(fieldNamed((await saved()).fields, "city").enabled).toBe(false);

        await press(checkbox("city"), " ");
        expect(fieldNamed((await saved()).fields, "city").enabled).toBe(true);
    });

    it("renames a field with a double click and Enter", async () => {
        await load(tableValues());

        await doubleClick(fieldName("city"));
        const input = byLabel<HTMLInputElement>("JSON field name");
        expect(input.value).toBe("city");
        expect(globalThis.document.activeElement).toBe(input);
        // No inputs until then: the name is text.
        expect(rowOf("cityId").querySelector("input")).toBeNull();

        await typeInto(input, "cityName");
        await press(byLabel("JSON field name"), "Enter");

        expect(query("JSON field name")).toBeNull();
        expect(rowNames()).toContain("cityName");
        const document = await saved();
        expect(fieldNamed(document.fields, "cityName").column?.name)
            .toBe("city");
    });

    it("drops an edit on Escape or when the focus leaves", async () => {
        await load(tableValues());

        await doubleClick(fieldName("city"));
        await typeInto(byLabel("JSON field name"), "gone");
        await press(byLabel("JSON field name"), "Escape");
        expect(query("JSON field name")).toBeNull();
        expect(rowNames()).toContain("city");

        await doubleClick(fieldName("city"));
        await typeInto(byLabel("JSON field name"), "gone");
        await act0(() => {
            byLabel("JSON field name").dispatchEvent(new FocusEvent("blur"));
        });
        expect(query("JSON field name")).toBeNull();
        expect(rowNames()).not.toContain("gone");
    });

    it("keeps an emptied field name", async () => {
        await load(tableValues());

        await rename(fieldName("city"), "JSON field name", "  ");

        expect(rowNames()).toContain("city");
    });

    it("toggles the flags of a field", async () => {
        await load(tableValues());

        const key = flag("city", "Include field in object composite key");
        expect(key.getAttribute("aria-pressed")).toBe("false");
        await clickOn(key);
        expect(flag("city", "Include field in object composite key")
            .getAttribute("aria-pressed")).toBe("true");
        expect(flag("city", "Include field in object composite key")
            .className).toContain("selected");
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

    it("toggles the object's CRUD pills and its ETAG check", async () => {
        await load(tableValues());

        expect(pills(topRow())).toEqual(["INSERT", "UPDATE", "DELETE"]);
        await clickOn(pill("INSERT", topRow()));
        await clickOn(pill("DELETE", topRow()));
        await clickOn(byLabel("Disable ETAG calculations for this table.",
            topRow()));

        expect(pill("INSERT", topRow()).getAttribute("aria-checked"))
            .toBe("true");
        expect(pill("INSERT", topRow()).className).toBe("activated");
        expect(pill("UPDATE", topRow()).className).toBe("deactivated");
        expect((await saved()).crud).toEqual({
            insert: true, update: false, delete: true, noCheck: true,
        });
    });

    it("edits the class name with a double click", async () => {
        await load(tableValues());

        await rename(className(), "Class name", "City");

        expect(className().textContent).toBe("City");
        expect((await saved()).className).toBe("City");
    });

    it("shows the class name problem once it was cleared", async () => {
        await load(tableValues());

        await rename(className(), "Class name", "");

        expect(host.querySelector(".mrsObjectFieldEditor .message.error")
            ?.textContent).toBe("The object name must not be empty.");
    });

    it("selects and deselects all fields but the references", async () => {
        await load(tableValues());

        await clickOn(byLabel("Deselect all fields", topRow()));
        let document = await saved();
        expect(document.fields.map((field) => { return field.enabled; }))
            .toEqual([false, false, false, false, false]);

        await openReference("country", COUNTRY);
        await clickOn(byLabel("Select all fields", topRow()));
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
        expect(host.textContent).toContain("Loading...");
        expect(checkbox("country").getAttribute("aria-disabled")).toBe("true");

        await answer(COUNTRY);

        // The back reference to city is left out.
        expect(rowNames()).toEqual(["cityId", "city", "countryId", "country",
            "countryId", "country", "address"]);
        expect(host.textContent).not.toContain("Loading...");
        expect(rowOf("country").className).not.toContain("disabled");
        expect(rowOf("country").getAttribute("aria-expanded")).toBe("true");
        expect(byLabel("Collapse", rowOf("country"))).toBeDefined();
        // A list of its own, one level in.
        expect([...host.querySelectorAll(".bracket")].map((node) => {
            return node.textContent;
        })).toEqual(["{", "{", "}", "}"]);
        const levels = [...host.querySelectorAll<HTMLElement>(
            ".mrs-row[data-field]")].map((node) => {
            return node.getAttribute("aria-level");
        });
        expect(levels).toEqual(["2", "2", "2", "2", "3", "3", "2"]);

        const document = await saved();
        const country = fieldNamed(document.fields, "country");
        expect(country.enabled).toBe(true);
        expect(country.reference?.loaded).toBe(true);
        expect(country.reference?.children.map((child) => {
            return child.name;
        })).toEqual(["countryId", "country"]);
    });

    it("opens and closes a reference with a single click on its row",
        async () => {
            await load(tableValues());

            await clickOn(rowOf("country"));
            // Not at once: it might be the first click of a double click.
            expect(postedOf("loadColumns")).toHaveLength(0);
            await clickWait();
            expect(postedOf("loadColumns")).toHaveLength(1);
            await answer(COUNTRY);
            expect(rowNames()).toHaveLength(7);

            await clickOn(rowOf("country"));
            await clickWait();
            expect(rowNames()).toHaveLength(5);
        });

    it("does not open a reference on a double click", async () => {
        await load(tableValues());

        await clickOn(rowOf("country"));
        await doubleClick(rowOf("country"));
        await clickWait();

        expect(postedOf("loadColumns")).toHaveLength(0);
    });

    it("renames a reference with a double click, without opening it",
        async () => {
            await load(tableValues());

            await clickOn(fieldName("country"));
            await rename(fieldName("country"), "JSON field name", "nation");
            await clickWait();

            expect(rowNames()).toContain("nation");
            expect(postedOf("loadColumns")).toHaveLength(0);
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

        await clickOn(checkbox("country"));

        expect(postedOf("loadColumns").at(-1)?.table).toBe("country");
        await answer(COUNTRY);
        expect(rowNames()).toHaveLength(7);
        expect(checkbox("country").getAttribute("aria-checked")).toBe("true");
    });

    it("closes a reference and forgets its table when it is unticked",
        async () => {
            await load(tableValues());
            await openReference("country", COUNTRY);

            await clickOn(checkbox("country"));

            expect(rowNames()).toHaveLength(5);
            const country = fieldNamed((await saved()).fields, "country");
            expect(country.enabled).toBe(false);
            expect(country.reference).toMatchObject({
                loaded: false, children: [],
            });
        });

    it("shows why a reference's columns could not be loaded", async () => {
        await load(tableValues());

        await clickOn(byLabel("Expand", rowOf("country")));
        const request = postedOf("loadColumns").at(-1)!;
        await send({
            type: "columns", requestId: request.requestId as number,
            error: "Table not found.",
        });

        expect(host.querySelector(".mrsObjectFieldEditor .message.error")
            ?.textContent).toBe("Table not found.");
        expect(rowNames()).toHaveLength(5);
        expect(host.textContent).not.toContain("Loading...");
        expect(fieldNamed((await saved()).fields, "country").enabled)
            .toBe(false);
    });

    it("keeps an edit made while a reference was loading", async () => {
        await load(tableValues());

        await clickOn(byLabel("Expand", rowOf("country")));
        await rename(fieldName("city"), "JSON field name", "cityName");
        await answer(COUNTRY);

        expect(rowNames()).toContain("cityName");
    });

    it("offers Unnest and UPDATE only on an n:1 reference", async () => {
        await load(tableValues());
        expect(pills(rowOf("country"))).toEqual(["Unnest"]);

        await openReference("country", COUNTRY);

        const row = rowOf("country");
        expect(pills(row)).toEqual(["Unnest", "UPDATE"]);
        await clickOn(pill("UPDATE", rowOf("country")));
        await clickOn(byLabel("Disable ETAG calculations for this table.",
            rowOf("country")));

        const country = fieldNamed((await saved()).fields, "country");
        expect(country.reference?.crud).toEqual({
            insert: false, update: true, delete: false, noCheck: true,
        });
    });

    it("unnests a reference into copies of its fields, and back",
        async () => {
            await load(tableValues());
            await openReference("country", COUNTRY);

            await clickOn(pill("Unnest", rowOf("country")));

            expect(pill("Unnest", rowOf("country")).getAttribute("aria-checked"))
                .toBe("true");
            expect(checkbox("country").getAttribute("aria-checked"))
                .toBe("mixed");
            expect(checkbox("country").getAttribute("aria-disabled"))
                .toBe("true");
            // No list of its own any more: its fields, marked, at its level.
            expect(rowNames()).toEqual(["cityId", "city", "countryId",
                "country", "address"]);
            expect(copyNames()).toEqual(["countryId", "country"]);
            expect(host.querySelector(".unnestedCopy")?.textContent)
                .toContain("country )");
            expect(byLabel("Expand", rowOf("country")).className)
                .toContain("disabled");
            expect(fieldNamed((await saved()).fields, "country").reference
                ?.unnest).toBe(true);

            // A click on the row does not open it now.
            await clickOn(rowOf("country"));
            await clickWait();
            expect(copyNames()).toHaveLength(2);

            await clickOn(pill("Unnest", rowOf("country")));
            expect(copyNames()).toEqual([]);
            expect(rowNames()).toHaveLength(7);
            expect(checkbox("country").getAttribute("aria-checked"))
                .toBe("true");
        });

    it("loads a closed reference to unnest it", async () => {
        await load(tableValues());

        await clickOn(pill("Unnest", rowOf("country")));
        expect(postedOf("loadColumns").at(-1)?.table).toBe("country");
        await answer(COUNTRY);

        expect(copyNames()).toEqual(["countryId", "country"]);
        const country = fieldNamed((await saved()).fields, "country");
        expect(country).toMatchObject({
            enabled: true, reference: { unnest: true, loaded: true },
        });
    });

    it("offers all CRUD pills and reduce-to on a 1:n reference",
        async () => {
            await load(tableValues());
            expect(query("Reduce to field", rowOf("address"))).toBeNull();
            await openReference("address", ADDRESS);

            const row = rowOf("address");
            expect(pills(row)).toEqual(["INSERT", "UPDATE", "DELETE"]);
            await clickOn(pill("INSERT", row));
            const reduce = byLabel<HTMLSelectElement>("Reduce to field",
                rowOf("address"));
            expect([...reduce.options].map((option) => {
                return option.textContent?.trim();
            })).toEqual(["Unnest field ...", "address_id", "address",
                "postal_code"]);

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
            // Still a list, its other fields reduced away and locked.
            expect(rowNames()).toContain("postalCode");
            expect(copyNames()).toEqual([]);
            expect(byLabel("Include addressId").getAttribute("aria-disabled"))
                .toBe("true");
            await clickOn(byLabel("Include addressId"));
            await clickOn(pill("DELETE", rowOf("address")));
            document = await saved();
            expect(fieldNamed(document.fields, "addressId").enabled).toBe(false);
            expect(findField(document.fields, address.key)?.reference?.crud
                .delete).toBe(false);
            // There is no unnesting a list.
            expect(pills(rowOf("address"))).not.toContain("Unnest");

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

        const preview = buttonOf("SQL Preview")!;
        expect(preview.getAttribute("aria-pressed")).toBe("false");
        await clickOn(preview);

        const sql = objectStatements(values, CONTEXT)[0];
        expect(buttonOf("SQL Preview")?.className).toContain("activated");
        expect(host.querySelector(".mrs-preview")?.textContent).toBe(sql);
        expect(host.querySelector(".mrsObjectTreeGrid")).toBeNull();

        await clickOn(byLabel("Copy SQL to Clipboard"));
        expect(postedOf("copy")).toEqual([{ type: "copy", text: sql }]);

        await clickOn(buttonOf("SQL Preview")!);
        expect(host.querySelector(".mrs-preview")).toBeNull();
        expect(rowNames()).toHaveLength(5);
    });

    it("previews the edits made", async () => {
        await load(tableValues());

        await rename(className(), "Class name", "Town");
        await clickOn(checkbox("city"));
        await clickOn(byLabel("Copy SQL to Clipboard"));

        const sql = String(postedOf("copy").at(-1)?.text);
        expect(sql).toContain("CLASS `Town`");
        expect(sql).not.toContain("`city`: `city`");
    });

    it("shows the database object it maps, read only", async () => {
        await load(tableValues());

        const input = byLabel<HTMLInputElement>("DB Object");
        expect(input.value).toBe("sakila.city");
        expect(input.readOnly).toBe(true);
    });

    it("shows the brackets alone when there are no fields", async () => {
        await load({
            ...tableValues(),
            document: { ...tableValues().document, fields: [] },
        });

        expect(rowNames()).toEqual([]);
        expect([...host.querySelectorAll(".bracket")].map((node) => {
            return node.textContent;
        })).toEqual(["{", "}"]);
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
        expect(rowOf("oldName").className).toContain("deleted");
        expect(checkbox("oldName").getAttribute("aria-disabled")).toBe("true");
    });
});

describe("DataMappingEditor of a procedure", () => {
    const mappings = (): string[] => {
        return [...byLabel<HTMLSelectElement>("Data mapping").options]
            .map((option) => { return option.textContent?.trim() ?? ""; });
    };

    /** Adds a field to the result shown; it opens with its name to type. */
    const addField = async (): Promise<void> => {
        await clickOn(byLabel("Add Field"));
        await press(byLabel("JSON field name"), "Escape");
    };

    it("shows the parameters with their modes", async () => {
        await load(routineValues("PROCEDURE"));

        expect(mappings()).toEqual(["Parameters"]);
        expect(className().textContent).toBe("FilmInStockParams");
        expect(rowNames()).toEqual(["pFilmId", "pFilmCount", "pNote"]);
        expect(byLabel("IN parameter", rowOf("pFilmId")).className)
            .toContain("selected");
        expect(query("OUT parameter", rowOf("pFilmCount"))).not.toBeNull();
        expect(query("INOUT parameter", rowOf("pNote"))).not.toBeNull();
        expect(rowOf("pNote").textContent).toContain("text");
        // Only what a parameter can have.
        expect(query("Set as row ownership field", rowOf("pFilmId")))
            .not.toBeNull();
        expect(query("Include field in object composite key")).toBeNull();
        expect(query("Delete field")).toBeNull();
        expect(query("Remove Result")).toBeNull();
        expect(query("Add Field")).toBeNull();
        expect(pills(topRow())).toEqual([]);
    });

    it("renames the parameters mapping", async () => {
        await load(routineValues("PROCEDURE"));

        await rename(className(), "Data mapping name", "StockParams");

        expect((await saved()).parameters?.name).toBe("StockParams");
    });

    it("adds, fills and removes results", async () => {
        await load(routineValues("PROCEDURE"));

        await clickOn(buttonOf("Add Result")!);

        expect(mappings()).toEqual(["Parameters", "Result 1"]);
        expect(byLabel<HTMLSelectElement>("Data mapping").value)
            .not.toBe("parameters");
        expect(className().textContent).toBe("FilmInStock");
        expect(rowNames()).toEqual([]);

        // A new field opens with all of its name selected, to type over.
        await clickOn(byLabel("Add Field"));
        const name = byLabel<HTMLInputElement>("JSON field name");
        expect(name.value).toBe("newField");
        expect(document.activeElement).toBe(name);
        expect([name.selectionStart, name.selectionEnd]).toEqual([0, 8]);
        await typeInto(name, "total");
        await press(byLabel("JSON field name"), "Enter");

        const row = rowOf("total");
        const column = row.querySelector<HTMLElement>(".columnName")!;
        expect(column.textContent).toBe("new_field: VARCHAR(255)");

        // The column's `name: type`, the caret put before the colon.
        await doubleClick(column);
        const edit = byLabel<HTMLInputElement>("Column name and datatype");
        expect(edit.value).toBe("new_field: VARCHAR(255)");
        expect(edit.selectionStart).toBe(9);
        await typeInto(edit, "total: INT");
        await press(byLabel("Column name and datatype"), "Enter");
        expect(rowOf("total").querySelector(".columnName")?.textContent)
            .toBe("total: INT");

        await rename(className(), "Data mapping name", "Totals");

        let saved1 = await saved();
        expect(saved1.results).toHaveLength(1);
        expect(saved1.results![0].name).toBe("Totals");
        expect(saved1.results![0].fields[0]).toMatchObject({
            name: "total",
            column: { name: "total", datatype: "INT" },
        });

        await clickOn(buttonOf("Add Result")!);
        expect(mappings()).toEqual(["Parameters", "Result 1", "Result 2"]);
        expect(className().textContent).toBe("FilmInStock2");

        await clickOn(byLabel("Remove Result"));
        expect(mappings()).toEqual(["Parameters", "Result 1"]);
        expect(className().textContent).toBe("FilmInStockParams");
        expect(query("Remove Result")).toBeNull();

        saved1 = await saved();
        expect(saved1.results?.map((result) => {
            return result.name;
        })).toEqual(["Totals"]);
    });

    it("keeps a column's name or type when only the other is given",
        async () => {
            await load(routineValues("PROCEDURE"));
            await clickOn(buttonOf("Add Result")!);
            await addField();

            const column = (): HTMLElement => {
                return rowOf("newField").querySelector<HTMLElement>(
                    ".columnName")!;
            };
            await rename(column(), "Column name and datatype", "amount");
            expect(column().textContent).toBe("amount: VARCHAR(255)");
            await rename(column(), "Column name and datatype", ": DECIMAL(8,2)");
            expect(column().textContent).toBe("amount: DECIMAL(8,2)");
        });

    it("does not edit a parameter's column", async () => {
        await load(routineValues("PROCEDURE"));

        await doubleClick(rowOf("pFilmId").querySelector<HTMLElement>(
            ".columnName")!);

        expect(query("Column name and datatype")).toBeNull();
    });

    it("switches between the parameters and a result", async () => {
        await load(routineValues("PROCEDURE"));
        await clickOn(buttonOf("Add Result")!);
        await addField();
        const select = byLabel<HTMLSelectElement>("Data mapping");
        const resultKey = select.value;

        await pick(byLabel("Data mapping"), "parameters");
        expect(rowNames()).toEqual(["pFilmId", "pFilmCount", "pNote"]);
        expect(query("Remove Result")).toBeNull();

        await pick(byLabel("Data mapping"), resultKey);
        expect(rowNames()).toEqual(["newField"]);
        expect(query("Remove Result")).not.toBeNull();
    });

    it("deletes a result field", async () => {
        await load(routineValues("PROCEDURE"));
        await clickOn(buttonOf("Add Result")!);
        await addField();
        await addField();
        expect(rowNames()).toEqual(["newField", "newField"]);

        const first = host.querySelectorAll<HTMLElement>(
            ".mrs-row[data-field]")[0];
        const remove = byLabel("Delete field", first);
        expect(remove.className).toContain("action");
        await clickOn(remove);

        expect(rowNames()).toEqual(["newField"]);
        expect((await saved()).results![0].fields).toHaveLength(1);
    });

    it("previews the procedure's SQL with its results", async () => {
        const values = routineValues("PROCEDURE");
        await load(values);
        await clickOn(buttonOf("Add Result")!);
        await addField();
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
            return option.textContent?.trim();
        })).toEqual(["Parameters", "Result 1"]);
        expect(buttonOf("Add Result")).toBeUndefined();
        // A function's parameters are all IN: no mode shown.
        expect(query("IN parameter")).toBeNull();

        await pick(select, select.options[1].value);

        expect(rowNames()).toEqual(["result"]);
        expect(className().textContent).toBe("FilmInStockResult");
        expect(buttonOf("Add Result")).toBeUndefined();
        expect(query("Remove Result")).toBeNull();
        expect(query("Add Field")).toBeNull();
        expect(query("Delete field")).toBeNull();
        const column = rowOf("result").querySelector<HTMLElement>(
            ".columnName")!;
        expect(column.textContent).toContain(": decimal(5,2)");

        await doubleClick(column);
        const edit = byLabel<HTMLInputElement>("Column name and datatype");
        await typeInto(edit, edit.value.replace(/:.*/, ": decimal(10,2)"));
        await press(byLabel("Column name and datatype"), "Enter");
        await rename(className(), "Data mapping name", "Balance");

        const document = await saved();
        expect(document.results![0].name).toBe("Balance");
        expect(document.results![0].fields[0].column?.datatype)
            .toBe("decimal(10,2)");
    });
});
