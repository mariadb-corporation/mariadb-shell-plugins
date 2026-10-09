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
import { buildDocument } from "../../src/mrs/dataMapping.js";
import {
    APP_BASE_CLASSES,
    authAppDefaults,
    configureContextOf,
    configureDefaults,
    contentSetDefaults,
    DEFAULT_IGNORE_LIST,
    objectDefaults,
    schemaDefaults,
    SDK_LANGUAGES,
    serviceDefaults,
    userDefaults,
    type IAuthAppContext,
    type IContentSetContext,
    type IObjectContext,
    type ISchemaContext,
    type ISdkExportValues,
    type IServiceContext,
    type IUserContext,
    type MrsDialogKind,
} from "../../src/mrs/mrsDialogs.js";
import type { MrsHostMessage } from "../../src/mrs/mrsDialogProtocol.js";
import {
    MARIADB_VENDOR_ID,
    MRS_VENDOR_ID,
    type IMrsColumns,
    type IMrsStatus,
} from "../../src/mrs/mrsTypes.js";

/**
 * The MRS dialogs' webview: one frame, {@link MrsDialog}, showing whichever
 * dialog the host's `load` names. Each dialog is driven as a user would,
 * and what reaches the host - `save`, `cancel`, `browse`, `analyzeFolder`
 * - is read from what the page posted.
 */

let host: HTMLDivElement;

const tick = async (): Promise<void> => {
    await new Promise((resolve) => { setTimeout(resolve, 0); });
};

const mount = async (): Promise<void> => {
    await act(async () => {
        render(<MrsDialog />, host);
        await Promise.resolve();
    });
};

const send = async (message: MrsHostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await tick();
    });
};

const load = async (
    dialog: MrsDialogKind,
    values: unknown,
    context: unknown,
    title = "Dialog",
): Promise<void> => {
    await mount();
    await send({ type: "load", dialog, title, values, context });
};

const buttonOf = (label: string): HTMLButtonElement => {
    const button = [...host.querySelectorAll("button")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
    if (!button) {
        throw new Error(`No button '${label}'.`);
    }

    return button;
};

const click = async (label: string): Promise<void> => {
    const button = buttonOf(label);
    await act(async () => {
        button.click();
        await tick();
    });
};

const okButton = (): HTMLButtonElement => {
    return host.querySelector<HTMLButtonElement>(
        ".editor-footer button.primary")!;
};

const pressOk = async (): Promise<void> => {
    await act(async () => {
        okButton().click();
        await tick();
    });
};

/** The field under the given caption. */
const fieldOf = (caption: string): HTMLLabelElement => {
    const field = [...host.querySelectorAll("label.field")].find((node) => {
        return node.querySelector(".field-caption")?.textContent === caption;
    }) as HTMLLabelElement | undefined;
    if (!field) {
        const have = [...host.querySelectorAll("label.field .field-caption")]
            .map((node) => { return node.textContent; });
        throw new Error(`No field '${caption}'. Have: ${have.join(", ")}`);
    }

    return field;
};

const hasField = (caption: string): boolean => {
    return [...host.querySelectorAll("label.field")].some((node) => {
        return node.querySelector(".field-caption")?.textContent === caption;
    });
};

const inputOf = (caption: string): HTMLInputElement => {
    return fieldOf(caption).querySelector("input, textarea")!;
};

const selectOf = (caption: string): HTMLSelectElement => {
    return fieldOf(caption).querySelector("select")!;
};

const hintOf = (caption: string): string | undefined => {
    return fieldOf(caption).querySelector(".field-hint")?.textContent
        ?? undefined;
};

/** Types into an input or textarea. */
const typeInto = async (
    input: HTMLInputElement | HTMLTextAreaElement,
    value: string,
): Promise<void> => {
    await act(async () => {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        await tick();
    });
};

const type = async (caption: string, value: string): Promise<void> => {
    await typeInto(inputOf(caption), value);
};

/** Picks the option with the given text. */
const choose = async (caption: string, label: string): Promise<void> => {
    const select = selectOf(caption);
    const index = [...select.options].findIndex((option) => {
        return option.textContent === label;
    });
    if (index < 0) {
        throw new Error(`No choice '${label}' in '${caption}'.`);
    }
    await act(async () => {
        select.selectedIndex = index;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await tick();
    });
};

const choices = (caption: string): string[] => {
    return [...selectOf(caption).options].map((option) => {
        return option.textContent ?? "";
    });
};

/** The checkbox labelled so. */
const checkboxOf = (label: string): HTMLInputElement => {
    const box = [...host.querySelectorAll("label.checkbox")].find((node) => {
        return node.querySelector("span")?.textContent === label;
    });
    if (!box) {
        throw new Error(`No checkbox '${label}'.`);
    }

    return box.querySelector("input")!;
};

const check = async (label: string): Promise<void> => {
    const box = checkboxOf(label);
    await act(async () => {
        box.click();
        await tick();
    });
};

const tabs = (): string[] => {
    return [...host.querySelectorAll("[role=tab]")].map((node) => {
        return node.textContent ?? "";
    });
};

const currentTab = (): string | undefined => {
    return host.querySelector("[role=tab][aria-selected=true]")
        ?.textContent ?? undefined;
};

const selectTab = async (name: string): Promise<void> => {
    const tab = [...host.querySelectorAll<HTMLButtonElement>("[role=tab]")]
        .find((node) => { return node.textContent === name; });
    if (!tab) {
        throw new Error(`No tab '${name}'. Have: ${tabs().join(", ")}`);
    }
    await act(async () => {
        tab.click();
        await tick();
    });
};

const title = (): string => {
    return host.querySelector("h1")?.textContent ?? "";
};

const messages = (): string[] => {
    return [...host.querySelectorAll("p.message")].map((node) => {
        return node.textContent ?? "";
    });
};

const postedOf = (kind: string): Array<Record<string, unknown>> => {
    return posted.filter((message) => {
        return message.type === kind;
    }) as Array<Record<string, unknown>>;
};

/** The values of the last `save`. */
const saved = (): Record<string, unknown> => {
    const saves = postedOf("save");
    if (saves.length === 0) {
        throw new Error("Nothing was saved.");
    }

    return saves.at(-1)!.values as Record<string, unknown>;
};

/** Presses OK and returns what was saved. */
const save = async (): Promise<Record<string, unknown>> => {
    await pressOk();

    return saved();
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

// --- fixtures ---------------------------------------------------------

const STATUS: IMrsStatus = {
    service_configured: true,
    service_enabled: true,
    service_upgradeable: false,
    service_upgrade_ignored: false,
    service_count: 1,
    service_being_upgraded: false,
    major_upgrade_required: false,
    current_metadata_version: "5.0.0",
    available_metadata_version: "5.0.0",
    required_rest_daemon_version: "9.0.0",
    metadata_version: 12,
    metadata_schema: "mariadb_rest_service",
    configuration_options: {
        authentication: {
            throttling: {
                perAccount: { minimumTimeBetweenRequestsInMs: 1500 },
                blockWhenAttemptsExceededInSeconds: 120,
            },
        },
        responseCache: { maxCacheSize: "1M" },
        custom: { kept: true },
    },
};

const INIT_STATUS: IMrsStatus = {
    ...STATUS,
    service_configured: false,
    service_enabled: false,
    current_metadata_version: null,
    metadata_version: null,
    configuration_options: {},
};

const OAUTH_VENDOR_ID = "32000000-0000-0000-0000-000000000000";

const VENDORS = [
    { id: MRS_VENDOR_ID, name: "MRS" },
    { id: MARIADB_VENDOR_ID, name: "MariaDB" },
    { id: OAUTH_VENDOR_ID, name: "Google" },
];

const ROLES = [
    { id: "r1", caption: "Full Access" },
    { id: "r2", caption: "Read Only" },
];

const OBJECT_CONTEXT: IObjectContext = {
    services: ["/myService", "/other"],
    schemas: { "/myService": ["/sakila"], "/other": ["/world"] },
};

const CITY_COLUMNS: IMrsColumns = {
    schema: "sakila",
    name: "city",
    type: "TABLE",
    columns: [
        {
            position: 1, name: "city_id", reference_mapping: null,
            db_column: {
                name: "city_id", datatype: "smallint", is_primary: true,
            },
        },
        {
            position: 2, name: "city", reference_mapping: null,
            db_column: { name: "city", datatype: "varchar(50)" },
        },
        {
            position: 3, name: "country", db_column: null,
            reference_mapping: {
                kind: "n:1", to_many: false, referenced_schema: "sakila",
                referenced_table: "country",
                column_mapping: [{ base: "country_id", ref: "country_id" }],
            },
        },
    ],
};

const tableObject = (): Record<string, unknown> => {
    return { ...objectDefaults({ name: "city", object_type: "TABLE" },
        buildDocument("TABLE", "sakila", "city", CITY_COLUMNS, [],
            "MyServiceSakilaCity"),
        "/myService", "/sakila") };
};

const routineObject = (
    objectType: "PROCEDURE" | "FUNCTION",
): Record<string, unknown> => {
    return { ...objectDefaults({
        name: "film_in_stock", object_type: objectType,
    },
        buildDocument(objectType, "sakila", "film_in_stock", {
            schema: "sakila", name: "film_in_stock", type: objectType,
            parameters: [{
                position: 1, name: "p_film_id", mode: "IN", datatype: "int",
            }],
            return_type: "int",
        }, [], "FilmInStock"),
        "/myService", "/sakila") };
};

// --- the frame --------------------------------------------------------

describe("MrsDialog frame", () => {
    it("says it is ready and shows nothing until loaded", async () => {
        await mount();

        expect(postedOf("ready")).toHaveLength(1);
        expect(host.querySelector(".editor")).not.toBeNull();
        expect(host.querySelector("h1")).toBeNull();
    });

    it("posts cancel on Cancel", async () => {
        await load("sdkExport", {
            directory: "", serviceUrl: "", sdkLanguage: "TypeScript",
            addAppBaseClass: "", header: "",
        }, { languages: SDK_LANGUAGES, baseClasses: APP_BASE_CLASSES });

        await click("Cancel");

        expect(postedOf("cancel")).toHaveLength(1);
        expect(postedOf("save")).toHaveLength(0);
    });

    it("disables the buttons and fields while busy", async () => {
        await load("schema", schemaDefaults([], undefined, "sakila",
            "/myService"), { services: ["/myService"] });

        await send({ type: "busy", busy: true });

        expect(buttonOf("Cancel").disabled).toBe(true);
        expect(okButton().disabled).toBe(true);
        expect(inputOf("REST Schema Path").disabled).toBe(true);
        expect(messages()).toContain("Saving...");

        await send({ type: "busy", busy: false });

        expect(buttonOf("Cancel").disabled).toBe(false);
        expect(okButton().disabled).toBe(false);
        expect(messages()).not.toContain("Saving...");
    });

    it("shows a progress bar while the metadata is deployed", async () => {
        await load("configure", configureDefaults(INIT_STATUS),
            configureContextOf(INIT_STATUS));
        expect(document.querySelector("[role='progressbar']")).toBeNull();

        await send({ type: "busy", busy: true });

        const bar = document.querySelector("[role='progressbar']");
        expect(bar?.getAttribute("aria-label")).toBe(
            "Deploying the MariaDB REST Service metadata schema...");
        expect(messages()).toContain(
            "Deploying the MariaDB REST Service metadata schema...");
        expect(buttonOf("Cancel").disabled).toBe(true);
        expect(okButton().disabled).toBe(true);

        await send({ type: "busy", busy: false });

        expect(document.querySelector("[role='progressbar']")).toBeNull();
    });

    it("shows a save error until a field is changed", async () => {
        await load("schema", schemaDefaults([], undefined, "sakila",
            "/myService"), { services: ["/myService"] });

        await send({ type: "saveError", message: "Duplicate path." });
        expect(messages()).toEqual(["Duplicate path."]);

        await type("REST Schema Path", "/other");
        expect(messages()).toEqual([]);
    });

    it("starts over on a second load", async () => {
        await load("schema", schemaDefaults([], undefined, "",
            "/myService"), { services: ["/myService"] });
        await pressOk();
        await send({ type: "saveError", message: "Failed." });
        expect(messages()).not.toEqual([]);

        await send({
            type: "load", dialog: "schema", title: "Again",
            values: schemaDefaults([], undefined, "sakila", "/myService"),
            context: { services: ["/myService"] },
        });

        expect(title()).toBe("Again");
        expect(messages()).toEqual([]);
        expect(currentTab()).toBe("Settings");
    });
});

// --- configure --------------------------------------------------------

describe("configure dialog", () => {
    const loadInit = async (): Promise<void> => {
        await load("configure", configureDefaults(INIT_STATUS),
            configureContextOf(INIT_STATUS),
            "Configure MariaDB REST Service");
    };

    const loadConfig = async (status = STATUS): Promise<void> => {
        await load("configure", configureDefaults(status),
            configureContextOf(status), "MariaDB REST Service Configuration");
    };

    it("shows the deployment fields and the Authentication tab in init mode",
        async () => {
            await loadInit();

            expect(title()).toBe("Configure MariaDB REST Service");
            expect(hasField("MariaDB REST Service Status")).toBe(true);
            expect(inputOf("Metadata Schema").value)
                .toBe("mariadb_rest_service");
            expect(tabs()).toEqual(["Authentication"]);
            expect(checkboxOf("Create default REST authentication app")
                .checked).toBe(true);
            expect(hasField("REST User Name")).toBe(true);
            expect(inputOf("REST User Password").type).toBe("password");
            expect(host.textContent).not.toContain("Current Version");
        });

    it("refuses a deployment without the auth app user, then saves it",
        async () => {
            await loadInit();

            expect(host.querySelector(".mrs-problem")).toBeNull();
            await pressOk();

            expect(postedOf("save")).toHaveLength(0);
            expect(host.querySelector(".mrs-problem")?.textContent)
                .toContain("Please specify a REST user name");

            await type("REST User Name", "admin");
            await type("REST User Password", "short");
            expect(hintOf("REST User Password")).toContain(
                "minimum authentication string length is 8");
            await type("REST User Password", "Secret1!");
            await choose("MariaDB REST Service Status", "Disabled");

            const values = await save();
            expect(values).toMatchObject({
                enabled: false,
                metadataSchema: "mariadb_rest_service",
                createAuthApp: true,
                authAppUser: "admin",
                authAppPassword: "Secret1!",
            });
        });

    it("checks the metadata schema name once it was typed in", async () => {
        await loadInit();
        await check("Create default REST authentication app");

        await type("Metadata Schema", "rest");

        expect(hintOf("Metadata Schema")).toContain(
            "The name must be mariadb_rest_service");
        await pressOk();
        expect(postedOf("save")).toHaveLength(0);

        await type("Metadata Schema", "app_mariadb_rest_service_v2");
        expect((await save()).createAuthApp).toBe(false);
    });

    it("shows the version and the four tabs in config mode", async () => {
        await loadConfig({
            ...STATUS,
            current_metadata_version: "4.1.0",
            available_metadata_version: "5.0.0",
            service_upgradeable: true,
        });

        expect(host.querySelector(".mrs-version")?.textContent)
            .toContain("4.1.0");
        expect(host.textContent).toContain("Version 5.0.0 is available.");
        expect(hasField("Metadata Schema")).toBe(false);
        expect(tabs()).toEqual(["Authentication Throttling", "Caches",
            "Redirects & Static Content", "Options"]);
        expect(inputOf("Per Account: Minimum Time Between Requests").value)
            .toBe("1500");
        expect(inputOf("Block Timeout").value).toBe("120");

        await check("Update to version 5.0.0");
        expect((await save()).update).toBe(true);
    });

    it("says the schema is up to date, or that the update is skipped",
        async () => {
            await loadConfig();
            expect(host.textContent).toContain("schema is up to date.");
            expect(host.textContent).not.toContain("Update to version");

            render(null, host);
            await loadConfig({ ...STATUS, service_upgrade_ignored: true });
            expect(host.textContent).toContain("update is being skipped.");
        });

    it("shows a disabled service as Disabled", async () => {
        await loadConfig({ ...STATUS, service_enabled: false });

        expect(selectOf("MariaDB REST Service Status").value).toBe("false");
    });

    it("edits every tab and saves what was typed", async () => {
        await loadConfig();

        await type("Per Host: Maximum Attempts Per Minute", "7");
        await selectTab("Caches");
        await type("Static File Cache", "2M");
        await check("GTID Cache");
        await type("Refresh Rate", "5");
        await selectTab("Redirects & Static Content");
        await type("Directory Index", "index.html, main.html");
        await type("Default Redirects", "{\"a\": \"/b\"}");
        await selectTab("Options");
        expect(inputOf("Options").value).toContain("\"kept\": true");

        expect(await save()).toMatchObject({
            perHostMaximumAttempts: "7",
            fileCacheSize: "2M",
            gtidCache: true,
            gtidRefreshRate: "5",
            directoryIndex: "index.html, main.html",
            defaultRedirects: "{\"a\": \"/b\"}",
        });
    });

    it("shows a number problem once typed in", async () => {
        await loadConfig();

        await type("Block Timeout", "soon");

        expect(hintOf("Block Timeout")).toBe("Please enter a whole number.");
        expect(inputOf("Block Timeout").className).toBe("invalid");
        expect(inputOf("Block Timeout").getAttribute("aria-invalid"))
            .toBe("true");
    });

    it("jumps to the tab of the first problem on OK", async () => {
        await loadConfig();
        await selectTab("Options");
        await type("Options", "{ nope");
        await selectTab("Caches");

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("Options");
        expect(hintOf("Options")).toBe("Please provide a valid JSON object.");
        expect(messages()).toContain("Please provide a valid JSON object.");
    });
});

// --- service ----------------------------------------------------------

describe("service dialog", () => {
    const context: IServiceContext = {
        allAuthApps: ["MRS", "Google"], linkedAuthApps: [],
    };

    const loadNew = async (): Promise<void> => {
        await load("service", serviceDefaults(undefined, [], ["MRS", "Google"]),
            context, "New REST Service");
    };

    it("shows the path, flags and tabs of a new service", async () => {
        await loadNew();

        expect(title()).toBe("New REST Service");
        expect(inputOf("REST Service Path").value).toBe("/myService");
        expect(checkboxOf("Enabled").checked).toBe(true);
        expect(checkboxOf("Default").checked).toBe(true);
        expect(checkboxOf("Published").checked).toBe(false);
        expect(tabs()).toEqual(["Settings", "Options",
            "Authentication Details", "Advanced"]);
        expect(checkboxOf("MRS").checked).toBe(true);
        expect(checkboxOf("Google").checked).toBe(false);
    });

    it("saves the flags, linked apps and every tab's fields", async () => {
        await loadNew();

        await type("REST Service Path", "/shop");
        await check("Published");
        await check("Default");
        await check("Google");
        await check("MRS");
        await type("Comments", "The shop.");
        await selectTab("Authentication Details");
        await type("Authentication Path", "/auth");
        await type("Redirection URL", "https://example.com/done");
        await selectTab("Advanced");
        await choose("Supported Protocols", "HTTP");

        expect(await save()).toMatchObject({
            path: "/shop",
            published: true,
            makeCurrent: false,
            authApps: ["Google"],
            comments: "The shop.",
            authPath: "/auth",
            authCompletedUrl: "https://example.com/done",
            protocol: "HTTP",
        });
    });

    it("shows a path problem only once it was typed in", async () => {
        await load("service", {
            ...serviceDefaults(undefined, [], []), path: "",
        }, context);

        expect(hintOf("REST Service Path")).toContain("URL context root");

        await type("REST Service Path", "/MRS");
        expect(hintOf("REST Service Path")).toBe(
            "The request path `/MRS` is reserved and cannot be used.");

        await type("REST Service Path", "dev@shop");
        expect(hintOf("REST Service Path"))
            .toBe("The request path must start with /.");

        await type("REST Service Path", "dev@/shop");
        expect(hintOf("REST Service Path")).toContain("URL context root");
    });

    it("refuses an empty path on OK", async () => {
        await load("service", {
            ...serviceDefaults(undefined, [], []), path: "",
        }, { allAuthApps: [], linkedAuthApps: [] });

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(hintOf("REST Service Path"))
            .toBe("The service path must not be empty.");
        expect(host.textContent).toContain("None defined.");
    });

    it("jumps to the Options tab for bad metadata", async () => {
        await loadNew();
        await selectTab("Options");
        await type("Metadata", "[1]");
        await selectTab("Advanced");

        await pressOk();

        expect(currentTab()).toBe("Options");
        expect(hintOf("Metadata")).toBe("Please provide a valid JSON object.");
        expect(postedOf("save")).toHaveLength(0);
    });

    it("saves an edited service as it was loaded", async () => {
        const values = {
            ...serviceDefaults(undefined, [], ["MRS"]),
            path: "/shop", authApps: ["MRS"], protocol: "HTTP",
        };
        await load("service", values, {
            ...context, existingPath: "/shop", linkedAuthApps: ["MRS"],
        }, "REST Service /shop");

        expect(title()).toBe("REST Service /shop");
        await selectTab("Advanced");
        expect(selectOf("Supported Protocols").value).toBe("HTTP");
        expect(await save()).toEqual(values);
    });
});

// --- schema -----------------------------------------------------------

describe("schema dialog", () => {
    const context: ISchemaContext = { services: ["/myService", "/other"] };

    const loadNew = async (dbSchema = "sakila"): Promise<void> => {
        await load("schema", schemaDefaults([], undefined, dbSchema,
            "/myService"), context, "New REST Schema");
    };

    it("shows its main fields and tabs", async () => {
        await loadNew();

        expect(title()).toBe("New REST Schema");
        expect(choices("REST Service Path")).toEqual(["/myService", "/other"]);
        expect(selectOf("REST Service Path").value).toBe("/myService");
        expect(selectOf("REST Service Path").disabled).toBe(false);
        expect(inputOf("REST Schema Path").value).toBe("/sakila");
        expect(selectOf("Access").value).toBe("1");
        expect(checkboxOf("Auth. Required").checked).toBe(false);
        expect(tabs()).toEqual(["Settings", "Options", "Metadata"]);
        expect(inputOf("Database Schema Name").value).toBe("sakila");
    });

    it("saves the access, the flags and the settings", async () => {
        await loadNew();

        await choose("REST Service Path", "/other");
        await choose("Access", "PRIVATE Access Only");
        await check("Auth. Required");
        await type("Items per Page", "50");
        await selectTab("Metadata");
        await type("Metadata", "{\"a\": 1}");

        expect(await save()).toMatchObject({
            servicePath: "/other",
            enabled: 2,
            requiresAuth: true,
            itemsPerPage: "50",
            metadata: "{\"a\": 1}",
        });
    });

    it("jumps to Settings for an empty database schema", async () => {
        await loadNew("");
        await selectTab("Options");

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("Settings");
        expect(hintOf("Database Schema Name"))
            .toBe("The database schema name must not be empty.");
    });

    it("checks the items per page once typed in", async () => {
        await loadNew();

        await type("Items per Page", "0");

        expect(hintOf("Items per Page"))
            .toBe("The items per page must be a positive number.");
    });

    it("keeps the service of an edited schema", async () => {
        await load("schema", schemaDefaults([], undefined, "sakila",
            "/myService"), { ...context, existingPath: "/sakila" });

        expect(selectOf("REST Service Path").disabled).toBe(true);
    });

    it("keeps text typed into a field as text", async () => {
        await loadNew();

        await type("Comments", "true");

        expect((await save()).comments).toBe("true");
    });
});

// --- object -----------------------------------------------------------

describe("object dialog", () => {
    it("shows the main fields and tabs of a table", async () => {
        await load("object", tableObject(), OBJECT_CONTEXT, "New REST Object");

        expect(title()).toBe("New REST Object");
        expect(selectOf("REST Service Path").value).toBe("/myService");
        expect(choices("REST Schema Path")).toEqual(["/sakila"]);
        expect(inputOf("REST Object Path").value).toBe("/city");
        expect(selectOf("Access").value).toBe("1");
        expect(checkboxOf("Auth. Required").checked).toBe(true);
        expect(tabs()).toEqual(["Data Mapping", "Settings", "Authorization",
            "Options"]);
        expect(host.querySelector("[data-field=country]")).not.toBeNull();
    });

    it("saves the settings, authorization and options", async () => {
        await load("object", tableObject(), OBJECT_CONTEXT);

        await choose("Access", "Access DISABLED");
        await check("Auth. Required");
        await selectTab("Settings");
        await choose("Result Format", "ITEM");
        await type("Media Type", "application/json");
        await check("Automatically Detect Media Type");
        await selectTab("Authorization");
        await type("Custom Stored Procedure used for Authorization",
            "auth.check");
        await selectTab("Options");
        await type("Options", "{\"cache\": 1}");

        expect(await save()).toMatchObject({
            enabled: 0,
            requiresAuth: false,
            format: "ITEM",
            mediaType: "application/json",
            autoDetectMediaType: true,
            authStoredProcedure: "auth.check",
            options: "{\"cache\": 1}",
        });
    });

    it("jumps to Settings for a bad items per page", async () => {
        await load("object", { ...tableObject(), itemsPerPage: "x" },
            OBJECT_CONTEXT);

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("Settings");
        expect(hintOf("Items per Page"))
            .toBe("The items per page must be a positive number.");
    });

    it("jumps to the Data Mapping tab for an empty class name", async () => {
        await load("object", tableObject(), OBJECT_CONTEXT);
        const name = host.querySelector<HTMLInputElement>(
            "[aria-label='Class name']")!;
        await typeInto(name, "");
        await selectTab("Options");

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("Data Mapping");
        expect(messages()).toContain("The object name must not be empty.");
    });

    it("shows a request path problem in the main part", async () => {
        await load("object", tableObject(), OBJECT_CONTEXT);

        await type("REST Object Path", "city");

        expect(hintOf("REST Object Path"))
            .toBe("The request path must start with /.");
        await pressOk();
        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("Data Mapping");
    });

    it("offers the schemas of the service picked", async () => {
        await load("object", tableObject(), OBJECT_CONTEXT);

        await choose("REST Service Path", "/other");

        expect(choices("REST Schema Path")).toEqual(["/world"]);
        // What is shown is what is saved.
        expect((await save()).schemaPath)
            .toBe(selectOf("REST Schema Path").value);
    });

    it("keeps service and schema of an edited object", async () => {
        await load("object", tableObject(), {
            ...OBJECT_CONTEXT, existingPath: "/city",
        });

        expect(selectOf("REST Service Path").disabled).toBe(true);
        expect(selectOf("REST Schema Path").disabled).toBe(true);
        expect((await save()).requestPath).toBe("/city");
    });

    it("needs no class name for a procedure", async () => {
        const values = routineObject("PROCEDURE");
        await load("object", values, OBJECT_CONTEXT);

        expect(tabs()).toEqual(["Data Mapping", "Settings", "Authorization",
            "Options"]);
        expect(host.querySelector("[aria-label='Data mapping']"))
            .not.toBeNull();
        expect(buttonOf("Add Result")).toBeDefined();

        expect(await save()).toEqual(values);
    });

    it("maps a function's parameters and its one result", async () => {
        await load("object", routineObject("FUNCTION"), OBJECT_CONTEXT);

        const mappings = [...host.querySelectorAll<HTMLOptionElement>(
            "[aria-label='Data mapping'] option")].map((option) => {
            return option.textContent;
        });
        expect(mappings).toEqual(["Parameters", "Result 1"]);
        expect(host.querySelector("[data-field=pFilmId]")).not.toBeNull();
        expect(host.textContent).not.toContain("Add Result");

        await selectTab("Settings");
        await type("Comments", "Stock.");
        expect((await save()).comments).toBe("Stock.");
    });
});

// --- content set ------------------------------------------------------

describe("content set dialog", () => {
    const context: IContentSetContext = { services: ["/myService"] };

    const loadNew = async (directory = ""): Promise<void> => {
        await load("contentSet", contentSetDefaults([], undefined,
            "/myService", directory), context, "New Content Set");
    };

    it("shows the fields of a new content set", async () => {
        await loadNew();

        expect(title()).toBe("New Content Set");
        expect(inputOf("Request Path").value).toBe("/content");
        expect(selectOf("REST Service Path").value).toBe("/myService");
        expect(inputOf("Folder to upload").value).toBe("");
        expect(inputOf("Files to ignore").value).toBe(DEFAULT_IGNORE_LIST);
        expect(tabs()).toEqual(["Settings", "Options"]);
        expect(postedOf("analyzeFolder")).toHaveLength(0);
    });

    it("refuses a missing folder on OK", async () => {
        await loadNew();
        await selectTab("Options");

        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(hintOf("Folder to upload"))
            .toBe("Please select the folder to upload.");
        expect(inputOf("Folder to upload").className).toBe("invalid");
        expect(currentTab()).toBe("Options");
    });

    it("posts browse, and fills the folder from browsed", async () => {
        await loadNew();

        await click("Browse...");
        expect(postedOf("browse")).toEqual([{
            type: "browse", field: "directory", folders: true,
        }]);

        await send({ type: "browsed", field: "directory", path: "/w/app" });
        expect(inputOf("Folder to upload").value).toBe("/w/app");
        expect(postedOf("analyzeFolder").at(-1)).toEqual({
            type: "analyzeFolder", directory: "/w/app",
            ignoreList: DEFAULT_IGNORE_LIST,
        });

        await type("Comments", "App.");
        expect(await save()).toMatchObject({
            directory: "/w/app", comments: "App.",
        });
    });

    it("analyzes the folder whenever it or the ignore list changes",
        async () => {
            await loadNew("/w/site");
            expect(postedOf("analyzeFolder")).toEqual([{
                type: "analyzeFolder", directory: "/w/site",
                ignoreList: DEFAULT_IGNORE_LIST,
            }]);

            await type("Folder to upload", "/w/other");
            await type("Files to ignore", "*.map");

            expect(postedOf("analyzeFolder").slice(1)).toEqual([
                {
                    type: "analyzeFolder", directory: "/w/other",
                    ignoreList: DEFAULT_IGNORE_LIST,
                },
                {
                    type: "analyzeFolder", directory: "/w/other",
                    ignoreList: "*.map",
                },
            ]);
        });

    it("adds the MRS Scripts tab for a folder with scripts", async () => {
        await loadNew("/w/site");

        await send({
            type: "scripts", directory: "/w/site", language: "TypeScript",
            definitions: {
                build_folder: "out",
                script_modules: [{ name: "Shop" }],
                errors: [
                    { kind: "WARNING", message: "Unused." },
                    { message: "Broken." },
                ],
            },
        });

        expect(tabs()).toEqual(["Settings", "MRS Scripts", "Options"]);
        await check("Enable MRS Scripts");

        await selectTab("MRS Scripts");
        const text = host.querySelector(".tab-body")?.textContent ?? "";
        expect(text).toContain("TypeScript");
        expect(text).toContain("out");
        expect(text).toContain("\"name\": \"Shop\"");
        const pres = [...host.querySelectorAll("pre")].map((node) => {
            return node.textContent;
        });
        expect(pres).toContain("WARNING: Unused.\nERROR: Broken.");

        expect((await save()).loadScripts).toBe(true);
    });

    it("ignores scripts of a folder no longer picked", async () => {
        await loadNew("/w/site");

        await send({
            type: "scripts", directory: "/w/elsewhere", language: "TypeScript",
        });

        expect(tabs()).toEqual(["Settings", "Options"]);
    });

    it("shows why a folder could not be analyzed", async () => {
        await loadNew("/w/site");

        await send({
            type: "scripts", directory: "/w/site", error: "No access.",
        });

        expect(tabs()).toEqual(["Settings", "Options"]);
        expect(host.querySelector(".tab-body .note")?.textContent)
            .toBe("No access.");
    });

    it("has no folder to upload when edited", async () => {
        const values = {
            ...contentSetDefaults([], undefined, "/myService"),
            requestPath: "/site",
        };
        await load("contentSet", values, {
            ...context, existingPath: "/site",
        }, "Content Set /site");

        expect(hasField("Folder to upload")).toBe(false);
        expect(hasField("Files to ignore")).toBe(false);
        expect(selectOf("REST Service Path").disabled).toBe(true);
        expect(postedOf("analyzeFolder")).toHaveLength(0);

        await choose("Access", "Access DISABLED");
        expect(await save()).toEqual({ ...values, enabled: 0 });
    });
});

// --- auth app ---------------------------------------------------------

describe("auth app dialog", () => {
    const context: IAuthAppContext = {
        vendors: VENDORS,
        roles: ROLES.map((role) => { return role.caption; }),
        hasSecret: false,
    };

    const loadNew = async (): Promise<void> => {
        await load("authApp", authAppDefaults(undefined, ROLES), context,
            "New REST Authentication App");
    };

    it("shows an MRS app with its Settings tab only", async () => {
        await loadNew();

        expect(title()).toBe("New REST Authentication App");
        expect(choices("Vendor")).toEqual(["MRS", "MariaDB", "Google"]);
        expect(selectOf("Vendor").value).toBe("MRS");
        expect(checkboxOf("Enabled").checked).toBe(true);
        expect(checkboxOf("Limit to registered users").checked).toBe(true);
        expect(tabs()).toEqual(["Settings"]);
        expect(selectOf("Default Role").value).toBe("Full Access");
        expect(choices("Default Role")).toEqual(["", "Full Access",
            "Read Only"]);
    });

    it("needs a name, shown in place on OK", async () => {
        await loadNew();

        expect(hintOf("Name")).toBe("The name of the authentication app");
        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(hintOf("Name")).toBe("The name must not be empty.");
        expect(currentTab()).toBe("Settings");

        await type("Name", "Shop");
        await check("Limit to registered users");
        await type("Description", "The shop's app.");
        await choose("Default Role", "");

        expect(await save()).toMatchObject({
            vendorName: "MRS",
            name: "Shop",
            limitToRegisteredUsers: false,
            description: "The shop's app.",
            defaultRole: "",
        });
    });

    it("adds the OAuth2 Settings tab for an OAuth2 vendor", async () => {
        await loadNew();
        await type("Name", "Shop");

        await choose("Vendor", "Google");

        expect(tabs()).toEqual(["OAuth2 Settings", "Settings"]);
        await selectTab("Settings");
        await pressOk();

        expect(postedOf("save")).toHaveLength(0);
        expect(currentTab()).toBe("OAuth2 Settings");
        expect(hintOf("Custom URL"))
            .toBe("The App URL must not be empty for OAuth2 auth apps.");
        expect(hintOf("App ID"))
            .toBe("The App ID must not be empty for OAuth2 auth apps.");
        expect(inputOf("App Secret").type).toBe("password");

        await type("Custom URL", "https://accounts.google.com");
        await type("App ID", "id-1");
        await type("App Secret", "s3cret");

        expect(await save()).toMatchObject({
            vendorName: "Google",
            url: "https://accounts.google.com",
            appId: "id-1",
            appSecret: "s3cret",
        });
    });

    it("keeps a stored secret of an edited app", async () => {
        await load("authApp", {
            ...authAppDefaults(undefined, ROLES),
            vendorName: "Google", name: "Shop",
            url: "https://g", appId: "id",
        }, { ...context, hasSecret: true, existingName: "Shop" });

        expect(selectOf("Vendor").disabled).toBe(true);
        expect(hintOf("App Secret")).toBe("Stored. Leave empty to keep it.");
        expect((await save()).appSecret).toBe("");
    });
});

// --- user -------------------------------------------------------------

describe("user dialog", () => {
    const context: IUserContext = {
        authApp: "MRS",
        authAppVendorId: MRS_VENDOR_ID,
        allRoles: ["Full Access", "Read Only"],
        existingRoles: [],
        hasPassword: false,
    };

    const loadNew = async (): Promise<void> => {
        await load("user", userDefaults(undefined, "Full Access"), context,
            "New REST User");
    };

    it("shows its fields and tabs", async () => {
        await loadNew();

        expect(title()).toBe("New REST User");
        expect(inputOf("User Name").disabled).toBe(false);
        expect(inputOf("User Password").type).toBe("password");
        expect(host.querySelector(".mrs-version")?.textContent)
            .toContain("MRS");
        expect(checkboxOf("Full Access").checked).toBe(true);
        expect(checkboxOf("Read Only").checked).toBe(false);
        expect(checkboxOf("Permit Login").checked).toBe(true);
        expect(tabs()).toEqual(["Options", "Auth App Settings"]);
    });

    it("needs a strong password, then saves the user", async () => {
        await loadNew();
        await type("User Name", "jane");

        await pressOk();
        expect(postedOf("save")).toHaveLength(0);
        expect(hintOf("User Password"))
            .toBe("The authentication string is required for this app.");

        await type("User Password", "abcdefgh");
        expect(hintOf("User Password")).toContain("at least one uppercase");

        await type("User Password", "Abcdefg1!");
        await type("Email", "jane@example.com");
        await check("Read Only");
        await check("Full Access");
        await check("Permit Login");
        await type("User Options", "{\"a\": 1}");

        expect(await save()).toMatchObject({
            name: "jane",
            password: "Abcdefg1!",
            email: "jane@example.com",
            roles: ["Read Only"],
            loginPermitted: false,
            options: "{\"a\": 1}",
        });
    });

    it("jumps to Auth App Settings for bad application options",
        async () => {
            await loadNew();
            await type("User Name", "jane");
            await type("User Password", "Abcdefg1!");
            await selectTab("Auth App Settings");
            await type("Application Options", "nope");
            await type("Vendor User Id", "v-1");
            await type("Mapped User Id", "m-1");
            await selectTab("Options");

            await pressOk();

            expect(postedOf("save")).toHaveLength(0);
            expect(currentTab()).toBe("Auth App Settings");
            expect(hintOf("Application Options"))
                .toBe("Please provide a valid JSON object.");

            await type("Application Options", "");
            expect(await save()).toMatchObject({
                vendorUserId: "v-1", mappedUserId: "m-1", appOptions: "",
            });
        });

    it("keeps the name and stored password of an edited user", async () => {
        await load("user", { ...userDefaults(undefined, undefined),
            name: "jane" }, {
            ...context, existingName: "jane", hasPassword: true,
        });

        expect(inputOf("User Name").disabled).toBe(true);
        expect(hintOf("User Password"))
            .toBe("Stored. Leave empty to keep it.");
        expect((await save()).password).toBe("");
    });
});

// --- SDK export -------------------------------------------------------

describe("SDK export dialog", () => {
    const values: ISdkExportValues = {
        directory: "",
        serviceUrl: "https://localhost:8443/myService",
        sdkLanguage: "TypeScript",
        addAppBaseClass: "",
        header: "",
    };
    const context = {
        languages: SDK_LANGUAGES, baseClasses: APP_BASE_CLASSES,
    };

    it("shows its fields, no tabs, and an Export button", async () => {
        await load("sdkExport", values, context, "Export MRS SDK");

        expect(title()).toBe("Export MRS SDK");
        expect(host.querySelector("[role=tablist]")).toBeNull();
        expect(okButton().textContent).toBe("Export");
        expect(choices("SDK Client API Language"))
            .toEqual(["TypeScript", "Python"]);
        expect(choices("Include AppBaseClass"))
            .toEqual(["", "MrsBaseAppPreact.ts"]);
    });

    it("needs a directory, and takes it from Browse", async () => {
        await load("sdkExport", values, context);

        await pressOk();
        expect(postedOf("save")).toHaveLength(0);
        expect(hintOf("Directory")).toBe("Please specify a directory.");
        expect(messages()).toEqual(["Please specify a directory."]);

        await click("Browse...");
        expect(postedOf("browse")).toEqual([{
            type: "browse", field: "directory", folders: true,
        }]);
        await send({ type: "browsed", field: "directory", path: "/w/sdk" });

        await choose("Include AppBaseClass", "MrsBaseAppPreact.ts");
        await type("SDK File Header", "// Generated");
        expect(await save()).toEqual({
            ...values,
            directory: "/w/sdk",
            addAppBaseClass: "MrsBaseAppPreact.ts",
            header: "// Generated",
        });
    });

    it("checks the service URL once it was cleared", async () => {
        await load("sdkExport", values, context);

        await type("REST Service URL", " ");

        expect(hintOf("REST Service URL"))
            .toBe("The REST service URL must not be empty.");
    });

    it("drops a base class the language picked does not have", async () => {
        await load("sdkExport", {
            ...values, directory: "/w/sdk",
            addAppBaseClass: "MrsBaseAppPreact.ts",
        }, context);

        await choose("SDK Client API Language", "Python");

        expect(choices("Include AppBaseClass")).toEqual([""]);
        // What is shown is what is saved.
        expect((await save()).addAppBaseClass)
            .toBe(selectOf("Include AppBaseClass").value);
    });
});

// --- the entry point --------------------------------------------------

describe("mrs entry point", () => {
    it("renders the dialog into #root", async () => {
        const root = document.createElement("div");
        root.id = "root";
        document.body.append(root);
        posted.length = 0;

        await act(async () => {
            await import("../src/mrs.js");
        });

        expect(root.querySelector(".editor")).not.toBeNull();
        expect(postedOf("ready")).toHaveLength(1);
        render(null, root);
        root.remove();
    });
});
