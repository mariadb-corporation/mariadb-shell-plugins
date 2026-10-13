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

import type { IMappingDocument } from "../../mrs/dataMapping.js";
import {
    APP_BASE_CLASSES,
    DEFAULT_AUTH_APP,
    DEFAULT_IGNORE_LIST,
    DEFAULT_METADATA_SCHEMA,
    DEFAULT_SERVICE_OPTIONS,
    GENERATED_CONTENT_SET_OPTIONS,
    JSON_PROBLEM,
    SDK_LANGUAGES,
    authAppDefaults,
    authAppStatements,
    configureContextOf,
    configureDefaults,
    configureOptions,
    configureStatements,
    contentSetDefaults,
    contentSetPathFor,
    contentSetStatements,
    objectDefaults,
    objectStatements,
    parseItemsPerPage,
    parseJsonObject,
    passwordProblems,
    prettyJson,
    requestPathProblems,
    schemaDefaults,
    schemaStatements,
    serviceDefaults,
    serviceStatements,
    userDefaults,
    userStatements,
    validateAuthApp,
    validateConfigure,
    validateContentSet,
    validateDialog,
    validateObject,
    validateSchema,
    validateSdkExport,
    validateService,
    validateUser,
    vendorIdOf,
    type IAuthAppContext,
    type IAuthAppDialogValues,
    type IConfigureContext,
    type IConfigureValues,
    type IContentSetContext,
    type IContentSetDialogValues,
    type IObjectDialogValues,
    type ISchemaDialogValues,
    type IServiceDialogValues,
    type IUserContext,
    type IUserDialogValues,
} from "../../mrs/mrsDialogs.js";
import {
    ENABLED_STATE,
    MARIADB_VENDOR_ID,
    MRS_VENDOR_ID,
    type IMrsAuthApp,
    type IMrsContentSet,
    type IMrsSchema,
    type IMrsService,
    type IMrsStatus,
    type IMrsUser,
} from "../../mrs/mrsTypes.js";

const GOOGLE_VENDOR_ID = "32000000-0000-0000-0000-000000000000";

const LENGTH_PROBLEM = "The minimum authentication string length is 8 "
    + "characters.";
const COMPLEXITY_PROBLEM = "The authentication string needs to contain at "
    + "least one uppercase, lowercase, a special and a numeric character.";

const status = (overrides: Partial<IMrsStatus> = {}): IMrsStatus => {
    return {
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
        ...overrides,
    };
};

const INIT: IConfigureContext = {
    init: true,
    currentVersion: null,
    availableVersion: "5.0.0",
    upgradeable: false,
    upgradeIgnored: false,
};

const CONFIGURED: IConfigureContext = {
    ...INIT,
    init: false,
    currentVersion: "5.0.0",
};

const configure = (
    overrides: Partial<IConfigureValues> = {},
): IConfigureValues => {
    return {
        ...configureDefaults(status()),
        ...overrides,
    };
};

const mrsService = (overrides: Partial<IMrsService> = {}): IMrsService => {
    return {
        id: "s1",
        url_context_root: "/svc",
        full_service_path: "/svc",
        name: "svc",
        enabled: 1,
        published: false,
        comments: null,
        options: null,
        metadata: null,
        auth_path: null,
        auth_completed_url: null,
        auth_completed_url_validation: null,
        auth_completed_page_content: null,
        in_development: null,
        ...overrides,
    };
};

const serviceValues = (
    overrides: Partial<IServiceDialogValues> = {},
): IServiceDialogValues => {
    return { ...serviceDefaults(undefined, [], []), ...overrides };
};

const schemaValues = (
    overrides: Partial<ISchemaDialogValues> = {},
): ISchemaDialogValues => {
    return {
        ...schemaDefaults([mrsService()], undefined, "sakila"),
        ...overrides,
    };
};

const viewDocument: IMappingDocument = {
    objectType: "TABLE",
    dbSchema: "sakila",
    dbObject: "city",
    className: "MyCity",
    crud: { insert: false, update: false, delete: false, noCheck: false },
    fields: [],
};

const objectValues = (
    overrides: Partial<IObjectDialogValues> = {},
): IObjectDialogValues => {
    return {
        ...objectDefaults({ name: "city", object_type: "TABLE" },
            viewDocument, "/svc", "/sakila"),
        ...overrides,
    };
};

const contentSetValues = (
    overrides: Partial<IContentSetDialogValues> = {},
): IContentSetDialogValues => {
    return {
        ...contentSetDefaults([mrsService()], undefined, undefined,
            "/home/me/app/dist"),
        ...overrides,
    };
};

const mrsContentSet = (
    overrides: Partial<IMrsContentSet> = {},
): IMrsContentSet => {
    return {
        id: "c1",
        service_id: "s1",
        content_type: "STATIC",
        request_path: "/web",
        requires_auth: false,
        enabled: ENABLED_STATE.enabled,
        comments: null,
        options: null,
        ...overrides,
    };
};

const VENDORS = [
    { id: MRS_VENDOR_ID, name: "MRS" },
    { id: MARIADB_VENDOR_ID, name: "MariaDB Internal" },
    { id: GOOGLE_VENDOR_ID, name: "Google" },
];

const appContext = (
    overrides: Partial<IAuthAppContext> = {},
): IAuthAppContext => {
    return { vendors: VENDORS, roles: [], hasSecret: false, ...overrides };
};

const appValues = (
    overrides: Partial<IAuthAppDialogValues> = {},
): IAuthAppDialogValues => {
    return { ...authAppDefaults(undefined, []), name: "app", ...overrides };
};

const userContext = (
    overrides: Partial<IUserContext> = {},
): IUserContext => {
    return {
        authApp: "MRS",
        authAppVendorId: MRS_VENDOR_ID,
        allRoles: ["Full Access", "Editor"],
        existingRoles: [],
        hasPassword: false,
        ...overrides,
    };
};

const userValues = (
    overrides: Partial<IUserDialogValues> = {},
): IUserDialogValues => {
    return {
        ...userDefaults(undefined, undefined),
        name: "anna",
        ...overrides,
    };
};

describe("JSON fields", () => {
    it("pretty prints an object, and nothing for none", () => {
        expect(prettyJson(null)).toBe("");
        expect(prettyJson(undefined)).toBe("");
        expect(prettyJson({})).toBe("");
        expect(prettyJson({ a: [1] }))
            .toBe("{\n    \"a\": [\n        1\n    ]\n}");
    });

    it("parses an object, null for an empty field", () => {
        expect(parseJsonObject("")).toBeNull();
        expect(parseJsonObject("  \n")).toBeNull();
        expect(parseJsonObject("{}")).toEqual({});
        expect(parseJsonObject(" {\"a\": 1} ")).toEqual({ a: 1 });
    });

    it("parses anything else as undefined", () => {
        expect(parseJsonObject("[1]")).toBeUndefined();
        expect(parseJsonObject("null")).toBeUndefined();
        expect(parseJsonObject("1")).toBeUndefined();
        expect(parseJsonObject("\"a\"")).toBeUndefined();
        expect(parseJsonObject("{bad")).toBeUndefined();
    });
});

describe("field checks", () => {
    it("checks a request path", () => {
        expect(requestPathProblems("/a")).toEqual([]);
        expect(requestPathProblems("")).toEqual([{
            field: "requestPath",
            message: "The request path must not be empty.",
        }]);
        expect(requestPathProblems("   ", "path")).toEqual([{
            field: "path", message: "The request path must not be empty.",
        }]);
        expect(requestPathProblems("a", "path")).toEqual([{
            field: "path", message: "The request path must start with /.",
        }]);
    });

    it("checks a password", () => {
        expect(passwordProblems("Abcdef1!", "pw")).toEqual([]);
        expect(passwordProblems("Abcde1!", "pw"))
            .toEqual([{ field: "pw", message: LENGTH_PROBLEM }]);
        expect(passwordProblems("", "pw"))
            .toEqual([{ field: "pw", message: LENGTH_PROBLEM }]);
        for (const weak of ["abcdefg1!", "ABCDEFG1!", "Abcdefgh!",
            "Abcdefg12", "Abcdefg1_"]) {
            expect(passwordProblems(weak, "pw"))
                .toEqual([{ field: "pw", message: COMPLEXITY_PROBLEM }]);
        }
    });

    it("parses the items per page", () => {
        expect(parseItemsPerPage("")).toBeNull();
        expect(parseItemsPerPage("  ")).toBeNull();
        expect(parseItemsPerPage(" 10 ")).toBe(10);
        expect(parseItemsPerPage("0")).toBe(0);
        expect(parseItemsPerPage("1.5")).toBeNaN();
        expect(parseItemsPerPage("-1")).toBeNaN();
        expect(parseItemsPerPage("ten")).toBeNaN();
    });
});

describe("the configuration dialog", () => {
    const options = {
        authentication: {
            throttling: {
                perAccount: {
                    minimumTimeBetweenRequestsInMs: 1500,
                    maximumAttemptsPerMinute: 5,
                },
                perHost: {
                    minimumTimeBetweenRequestsInMs: 1000,
                    maximumAttemptsPerMinute: 100,
                },
                blockWhenAttemptsExceededInSeconds: 120,
            },
            other: true,
        },
        responseCache: { maxCacheSize: "1M" },
        fileCache: { maxCacheSize: "2M" },
        gtid: {
            cache: {
                enable: true, refreshRate: 5, refreshWhenIncreasesBy: 500,
            },
        },
        directoryIndexDirective: ["index.html", "default.html"],
        defaultStaticContent: { "index.html": "x" },
        defaultRedirects: { "/": "/index.html" },
        custom: { keep: 1 },
    };

    it("tells what it was opened on", () => {
        expect(configureContextOf(status({
            service_configured: false,
            current_metadata_version: null,
            available_metadata_version: "5.1.0",
            service_upgradeable: true,
            service_upgrade_ignored: true,
        }))).toEqual({
            init: true,
            currentVersion: null,
            availableVersion: "5.1.0",
            upgradeable: true,
            upgradeIgnored: true,
        });
        expect(configureContextOf(status()).init).toBe(false);
    });

    it("starts a first deployment enabled, creating the auth app", () => {
        expect(configureDefaults(status({
            service_configured: false,
            service_enabled: false,
            metadata_schema: "",
        }))).toEqual({
            enabled: true,
            metadataSchema: DEFAULT_METADATA_SCHEMA,
            update: false,
            createAuthApp: true,
            authAppUser: "",
            authAppPassword: "",
            perAccountMinimumTime: "",
            perAccountMaximumAttempts: "",
            perHostMinimumTime: "",
            perHostMaximumAttempts: "",
            blockTimeout: "",
            responseCacheSize: "",
            fileCacheSize: "",
            gtidCache: false,
            gtidRefreshRate: "",
            gtidRefreshWhenIncreasedBy: "",
            directoryIndex: "index.html",
            defaultStaticContent: "",
            defaultRedirects: "",
            options: "",
        });
    });

    it("splits the known options out of the configuration", () => {
        const values = configureDefaults(status({
            service_enabled: false,
            metadata_schema: "x_mariadb_rest_service",
            configuration_options: options,
        }));
        expect(values).toEqual({
            enabled: false,
            metadataSchema: "x_mariadb_rest_service",
            update: false,
            createAuthApp: false,
            authAppUser: "",
            authAppPassword: "",
            perAccountMinimumTime: "1500",
            perAccountMaximumAttempts: "5",
            perHostMinimumTime: "1000",
            perHostMaximumAttempts: "100",
            blockTimeout: "120",
            responseCacheSize: "1M",
            fileCacheSize: "2M",
            gtidCache: true,
            gtidRefreshRate: "5",
            gtidRefreshWhenIncreasedBy: "500",
            directoryIndex: "index.html, default.html",
            defaultStaticContent: prettyJson({ "index.html": "x" }),
            defaultRedirects: prettyJson({ "/": "/index.html" }),
            options: prettyJson({
                authentication: { other: true },
                custom: { keep: 1 },
            }),
        });
        // The status itself is left as it was.
        expect(options.gtid.cache.enable).toBe(true);
    });

    it("puts the options back together", () => {
        const values = configureDefaults(status({
            configuration_options: options,
        }));
        expect(configureOptions(values)).toEqual(options);
    });

    it("keeps options of an unexpected shape", () => {
        const odd = { gtid: 5, responseCache: { maxCacheSize: null } };
        const values = configureDefaults(status({
            configuration_options: odd,
        }));
        expect(values.gtidRefreshRate).toBe("");
        expect(values.responseCacheSize).toBe("");
        expect(parseJsonObject(values.options)).toEqual({ gtid: 5 });
    });

    it("builds the options from the fields", () => {
        expect(configureOptions(configure({
            perAccountMinimumTime: " 10 ",
            fileCacheSize: "3M",
            gtidCache: false,
            directoryIndex: " a.html , ,b.html ",
            defaultStaticContent: "",
            defaultRedirects: "{\"/x\": \"/y\"}",
            options: "{\"fileCache\": {\"other\": 1}}",
        }))).toEqual({
            authentication: {
                throttling: {
                    perAccount: { minimumTimeBetweenRequestsInMs: 10 },
                },
            },
            fileCache: { other: 1, maxCacheSize: "3M" },
            directoryIndexDirective: ["a.html", "b.html"],
            defaultRedirects: { "/x": "/y" },
        });
        expect(configureOptions(configure({ directoryIndex: " , " })))
            .toEqual({});
    });

    it("checks a first deployment's schema name", () => {
        const problem = {
            field: "metadataSchema",
            message: "The name must be mariadb_rest_service, optionally "
                + "with a prefix and a postfix starting with _.",
        };
        for (const name of ["mariadb_rest_service", "x_mariadb_rest_service",
            "mariadb_rest_service_2", "abcmariadb_rest_service_a_b"]) {
            expect(validateConfigure(configure({
                metadataSchema: name, createAuthApp: false,
            }), INIT)).toEqual([]);
        }
        for (const name of ["", "rest", "mariadb_rest_service2",
            "1mariadb_rest_service", "mariadb_rest_service_"]) {
            expect(validateConfigure(configure({
                metadataSchema: name, createAuthApp: false,
            }), INIT)).toEqual([problem]);
        }
    });

    it("checks the auth app a first deployment creates", () => {
        expect(validateConfigure(configure({ createAuthApp: true }), INIT))
            .toEqual([{
                field: "createAuthApp",
                message: "Please specify a REST user name or disable this "
                    + "option to skip the creation of the default REST "
                    + "authentication app.",
            }]);
        expect(validateConfigure(configure({
            createAuthApp: true, authAppUser: "admin",
        }), INIT)).toEqual([{
            field: "authAppPassword",
            message: "Please specify a password for the MRS Admin User.",
        }]);
        expect(validateConfigure(configure({
            createAuthApp: true, authAppUser: "admin", authAppPassword: "weak",
        }), INIT)).toEqual([
            { field: "authAppPassword", message: LENGTH_PROBLEM },
        ]);
        expect(validateConfigure(configure({
            createAuthApp: true, authAppUser: "admin",
            authAppPassword: "Secret1!",
        }), INIT)).toEqual([]);
    });

    it("checks a first deployment's schema name, not the options", () => {
        expect(validateConfigure(configure({
            createAuthApp: false, options: "{bad", blockTimeout: "x",
        }), INIT)).toEqual([]);
    });

    it("checks the numbers and the JSON of a configuration", () => {
        expect(validateConfigure(configure(), CONFIGURED)).toEqual([]);
        expect(validateConfigure(configure({
            perAccountMinimumTime: " 12 ",
            responseCacheSize: "not checked",
        }), CONFIGURED)).toEqual([]);
        expect(validateConfigure(configure({
            perAccountMinimumTime: "x",
            perHostMaximumAttempts: "-1",
            gtidRefreshWhenIncreasedBy: "1.5",
            defaultStaticContent: "[]",
            defaultRedirects: "{",
            options: "1",
        }), CONFIGURED)).toEqual([
            { field: "perAccountMinimumTime",
                message: "Please enter a whole number." },
            { field: "perHostMaximumAttempts",
                message: "Please enter a whole number." },
            { field: "gtidRefreshWhenIncreasedBy",
                message: "Please enter a whole number." },
            { field: "defaultStaticContent", message: JSON_PROBLEM },
            { field: "defaultRedirects", message: JSON_PROBLEM },
            { field: "options", message: JSON_PROBLEM },
        ]);
    });

    it("deploys the metadata and creates the auth app", () => {
        expect(configureStatements(configure({
            metadataSchema: DEFAULT_METADATA_SCHEMA,
            createAuthApp: true,
            authAppUser: "admin",
            authAppPassword: "Secret1!",
        }), INIT)).toEqual([
            "CONFIGURE REST METADATA\n    ENABLED;",
            `CREATE REST AUTH APP IF NOT EXISTS '${DEFAULT_AUTH_APP}' VENDOR `
                + "MRS COMMENT 'MRS Auth App' DO NOT ALLOW NEW USERS TO "
                + "REGISTER DEFAULT ROLE 'Full Access';",
            "CREATE REST USER 'admin'@'MRS' IDENTIFIED BY 'Secret1!'\n"
                + "    ACCOUNT UNLOCK\n"
                + "    OPTIONS {\n"
                + "        \"email\": null,\n"
                + "        \"vendor_user_id\": null,\n"
                + "        \"mapped_user_id\": null\n"
                + "    };",
        ]);
    });

    it("deploys a named metadata schema without an auth app", () => {
        expect(configureStatements(configure({
            enabled: false,
            metadataSchema: "x_mariadb_rest_service",
            createAuthApp: false,
            authAppUser: "admin",
        }), INIT)).toEqual([
            "CONFIGURE REST METADATA\n"
                + "    SCHEMA `x_mariadb_rest_service`\n"
                + "    DISABLED;",
        ]);
        expect(configureStatements(configure({
            createAuthApp: true, authAppUser: "",
        }), INIT)).toHaveLength(1);
    });

    it("configures deployed metadata with its options", () => {
        expect(configureStatements(configure({
            update: true,
            directoryIndex: "",
            blockTimeout: "60",
        }), CONFIGURED)).toEqual([
            "CONFIGURE REST METADATA\n"
                + "    ENABLED\n"
                + "    OPTIONS {\n"
                + "        \"authentication\": {\n"
                + "            \"throttling\": {\n"
                + "                \"blockWhenAttemptsExceededInSeconds\": 60\n"
                + "            }\n"
                + "        }\n"
                + "    }\n"
                + "    UPDATE IF AVAILABLE;",
        ]);
    });
});

describe("the service dialog", () => {
    it("starts a new service with the built-in app", () => {
        expect(serviceDefaults(undefined, ["x"], ["Google", "MariaDB", "MRS"]))
            .toEqual({
                path: "/myService/v1",
                enabled: true,
                published: false,
                makeCurrent: true,
                protocol: "HTTPS",
                comments: "",
                options: prettyJson(DEFAULT_SERVICE_OPTIONS),
                metadata: "",
                authPath: "/authentication",
                authCompletedUrl: "",
                authCompletedUrlValidation: "",
                authCompletedPageContent: "",
                authApps: ["MRS"],
            });
        expect(serviceDefaults(undefined, [], ["MariaDB"]).authApps)
            .toEqual(["MariaDB"]);
        expect(serviceDefaults(undefined, [], ["Google"]).authApps)
            .toEqual([]);
    });

    it("starts from what a service has", () => {
        const linked = ["A"];
        const values = serviceDefaults(mrsService({
            full_service_path: "mike@/svc",
            enabled: 0,
            published: 1,
            is_current: true,
            url_protocol: ["HTTP"],
            comments: "c",
            options: { o: 1 },
            metadata: { m: 1 },
            auth_path: "/a",
            auth_completed_url: "u",
            auth_completed_url_validation: "v",
            auth_completed_page_content: "p",
        }), linked, ["A", "B"]);
        expect(values).toEqual({
            path: "mike@/svc",
            enabled: false,
            published: true,
            makeCurrent: true,
            protocol: "HTTP",
            comments: "c",
            options: prettyJson({ o: 1 }),
            metadata: prettyJson({ m: 1 }),
            authPath: "/a",
            authCompletedUrl: "u",
            authCompletedUrlValidation: "v",
            authCompletedPageContent: "p",
            authApps: ["A"],
        });
        expect(values.authApps).not.toBe(linked);
    });

    it("reads the protocol in each form", () => {
        const protocol = (url_protocol: IMrsService["url_protocol"]) => {
            return serviceDefaults(mrsService({ url_protocol }), [], [])
                .protocol;
        };
        expect(protocol("HTTPS, HTTP")).toBe("HTTP");
        expect(protocol("HTTPS")).toBe("HTTPS");
        expect(protocol(["HTTPS"])).toBe("HTTPS");
        expect(protocol(null)).toBe("HTTPS");
        expect(protocol(undefined)).toBe("HTTPS");
        const values = serviceDefaults(mrsService({ published: true }), [], []);
        expect(values.published).toBe(true);
        expect(values.enabled).toBe(true);
        expect(values.makeCurrent).toBe(false);
        expect(values.authPath).toBe("");
    });

    it("checks the path and the JSON", () => {
        expect(validateService(serviceValues())).toEqual([]);
        expect(validateService(serviceValues({ path: "mike,'a@b'@/svc" })))
            .toEqual([]);
        expect(validateService(serviceValues({ path: " " }))).toEqual([{
            field: "path", message: "The service path must not be empty.",
        }]);
        for (const path of ["svc", "mike@svc"]) {
            expect(validateService(serviceValues({ path }))).toEqual([{
                field: "path", message: "The request path must start with /.",
            }]);
        }
        expect(validateService(serviceValues({ path: "mike@/MRS" })))
            .toEqual([{
                field: "path",
                message: "The request path `/MRS` is reserved and cannot be "
                    + "used.",
            }]);
        expect(validateService(serviceValues({
            options: "[", metadata: "2",
        }))).toEqual([
            { field: "options", message: JSON_PROBLEM },
            { field: "metadata", message: JSON_PROBLEM },
        ]);
    });

    it("creates a service, linking its apps and using it", () => {
        const statements = serviceStatements(serviceValues({
            path: " /svc ",
            options: "",
            authApps: ["MRS"],
        }), { allAuthApps: ["MRS"], linkedAuthApps: [] });
        expect(statements).toHaveLength(2);
        expect(statements[0]).toMatch(/^CREATE REST SERVICE \/svc\n/);
        expect(statements[0]).toContain("    OPTIONS {}\n");
        expect(statements[0]).toContain("    ADD AUTH APP 'MRS';");
        expect(statements[1]).toBe("USE REST SERVICE /svc;");
        expect(serviceStatements(serviceValues({ makeCurrent: false }),
            { allAuthApps: [], linkedAuthApps: [] })).toHaveLength(1);
    });

    it("alters a service, linking and unlinking the difference", () => {
        const statements = serviceStatements(serviceValues({
            path: "/new",
            makeCurrent: false,
            metadata: "{\"m\": 1}",
            authApps: ["B", "C"],
        }), {
            allAuthApps: ["A", "B", "C"],
            existingPath: "/svc",
            linkedAuthApps: ["A", "B"],
        });
        expect(statements).toHaveLength(1);
        const lines = statements[0].split("\n");
        expect(lines.slice(0, 2)).toEqual([
            "ALTER REST SERVICE /svc", "    NEW REQUEST PATH /new",
        ]);
        expect(statements[0])
            .toContain("    METADATA {\n        \"m\": 1\n    }");
        expect(lines.slice(-2)).toEqual([
            "    REMOVE AUTH APP 'A' IF EXISTS",
            "    ADD AUTH APP 'C';",
        ]);
    });

    it("uses an altered service by its new path", () => {
        expect(serviceStatements(serviceValues({ path: "/new" }), {
            allAuthApps: [], existingPath: "/svc", linkedAuthApps: [],
        })[1]).toBe("USE REST SERVICE /new;");
    });
});

describe("the schema dialog", () => {
    const services = [
        mrsService({ full_service_path: "/first" }),
        mrsService({ full_service_path: "/current", is_current: true }),
    ];

    it("starts a new schema on the current service", () => {
        expect(schemaDefaults(services, undefined, "sakila_db")).toEqual({
            servicePath: "/current",
            requestPath: "/sakilaDb",
            dbSchema: "sakila_db",
            enabled: ENABLED_STATE.enabled,
            requiresAuth: false,
            itemsPerPage: "",
            comments: "",
            options: "",
            metadata: "",
        });
        expect(schemaDefaults([services[0]], undefined, "").servicePath)
            .toBe("/first");
        expect(schemaDefaults([], undefined, "").servicePath).toBe("");
        expect(schemaDefaults([], undefined, "").requestPath).toBe("/schema");
        expect(schemaDefaults(services, undefined, "s", "/given").servicePath)
            .toBe("/given");
    });

    it("starts from what a schema has", () => {
        const schema: IMrsSchema = {
            id: "x",
            service_id: "s",
            name: "sakila",
            schema_type: "DATABASE_SCHEMA",
            request_path: "/sak",
            requires_auth: true,
            enabled: ENABLED_STATE.private,
            items_per_page: 10,
            comments: "c",
            options: { o: 1 },
            metadata: { m: 1 },
        };
        expect(schemaDefaults(services, schema, "ignored", "/svc")).toEqual({
            servicePath: "/svc",
            requestPath: "/sak",
            dbSchema: "sakila",
            enabled: ENABLED_STATE.private,
            requiresAuth: true,
            itemsPerPage: "10",
            comments: "c",
            options: prettyJson({ o: 1 }),
            metadata: prettyJson({ m: 1 }),
        });
        expect(schemaDefaults(services, { ...schema, items_per_page: null },
            "").itemsPerPage).toBe("");
    });

    it("checks every field", () => {
        expect(validateSchema(schemaValues())).toEqual([]);
        expect(validateSchema(schemaValues({ itemsPerPage: " 5 " })))
            .toEqual([]);
        expect(validateSchema(schemaValues({
            servicePath: "",
            requestPath: "x",
            dbSchema: " ",
            itemsPerPage: "0",
            options: "[]",
            metadata: "x",
        }))).toEqual([
            { field: "servicePath", message: "Please select a REST service." },
            { field: "requestPath",
                message: "The request path must start with /." },
            { field: "dbSchema",
                message: "The database schema name must not be empty." },
            { field: "itemsPerPage",
                message: "The items per page must be a positive number." },
            { field: "options", message: JSON_PROBLEM },
            { field: "metadata", message: JSON_PROBLEM },
        ]);
        expect(validateSchema(schemaValues({ itemsPerPage: "x" })))
            .toEqual([{ field: "itemsPerPage",
                message: "The items per page must be a positive number." }]);
    });

    it("creates or alters the schema", () => {
        expect(schemaStatements(schemaValues({
            requestPath: " /sakila ",
            dbSchema: " sakila ",
            itemsPerPage: "7",
            options: "{\"a\": 1}",
        }), { services: ["/svc"] })).toEqual([[
            "CREATE REST SCHEMA /sakila ON SERVICE /svc FROM `sakila`",
            "    ENABLED",
            "    AUTHENTICATION NOT REQUIRED",
            "    ITEMS PER PAGE 7",
            "    COMMENT ''",
            "    OPTIONS {",
            "        \"a\": 1",
            "    };",
        ].join("\n")]);
        const [alter] = schemaStatements(schemaValues(), {
            services: ["/svc"], existingPath: "/old",
        });
        expect(alter.split("\n").slice(0, 2)).toEqual([
            "ALTER REST SCHEMA /old ON SERVICE /svc",
            "    NEW REQUEST PATH /sakila",
        ]);
        expect(alter).toContain("ITEMS PER PAGE 25");
    });
});

describe("the object dialog", () => {
    it("starts a new object", () => {
        expect(objectDefaults({ name: "film_actor", object_type: "TABLE" },
            viewDocument, "/svc", "/sakila")).toEqual({
            servicePath: "/svc",
            schemaPath: "/sakila",
            requestPath: "/filmActor",
            enabled: ENABLED_STATE.enabled,
            requiresAuth: true,
            itemsPerPage: "",
            comments: "",
            mediaType: "",
            autoDetectMediaType: false,
            format: "FEED",
            authStoredProcedure: "",
            options: "",
            metadata: "",
            document: viewDocument,
        });
    });

    it("starts from what an object has", () => {
        expect(objectDefaults({
            name: "city",
            object_type: "VIEW",
            request_path: "/town",
            enabled: ENABLED_STATE.disabled,
            requires_auth: false,
            items_per_page: 3,
            comments: "c",
            media_type: "image/png",
            auto_detect_media_type: true,
            format: "MEDIA",
            auth_stored_procedure: "s.p",
            options: { o: 1 },
            metadata: { m: 1 },
        }, viewDocument, "/svc", "/sakila")).toMatchObject({
            requestPath: "/town",
            enabled: ENABLED_STATE.disabled,
            requiresAuth: false,
            itemsPerPage: "3",
            comments: "c",
            mediaType: "image/png",
            autoDetectMediaType: true,
            format: "MEDIA",
            authStoredProcedure: "s.p",
            options: prettyJson({ o: 1 }),
            metadata: prettyJson({ m: 1 }),
        });
        expect(objectDefaults({
            name: "c", object_type: "VIEW", items_per_page: null,
            media_type: null, comments: null, auth_stored_procedure: null,
        }, viewDocument, "/s", "/t")).toMatchObject({
            itemsPerPage: "", mediaType: "", comments: "",
            authStoredProcedure: "",
        });
    });

    it("checks every field", () => {
        expect(validateObject(objectValues())).toEqual([]);
        expect(validateObject(objectValues({
            servicePath: "",
            schemaPath: "",
            requestPath: "",
            document: { ...viewDocument, className: " " },
            itemsPerPage: "-3",
            options: "[",
            metadata: "[",
        }))).toEqual([
            { field: "servicePath", message: "Please select a REST service." },
            { field: "schemaPath", message: "Please select a REST schema." },
            { field: "requestPath",
                message: "The request path must not be empty." },
            { field: "className",
                message: "The object name must not be empty." },
            { field: "itemsPerPage",
                message: "The items per page must be a positive number." },
            { field: "options",
                message: "The options must contain a valid JSON string." },
            { field: "metadata",
                message: "The metadata field must contain a valid JSON "
                    + "string." },
        ]);
    });

    it("needs no class name for a routine", () => {
        expect(validateObject(objectValues({
            document: { ...viewDocument, objectType: "PROCEDURE",
                className: "" },
        }))).toEqual([]);
    });

    it("creates or alters the object", () => {
        const values = objectValues({
            requestPath: " /city ",
            itemsPerPage: "4",
            comments: "c",
            mediaType: "text/plain",
            format: "ITEM",
            authStoredProcedure: "s.p",
            options: "{\"a\": 1}",
            metadata: "{\"m\": 2}",
        });
        const [create] = objectStatements(values, {
            services: ["/svc"], schemas: { "/svc": ["/sakila"] },
        });
        expect(create).toBe([
            "CREATE REST VIEW /city",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    AS `sakila`.`city` CLASS `MyCity` {}",
            "    ENABLED",
            "    AUTHENTICATION REQUIRED",
            "    ITEMS PER PAGE 4",
            "    COMMENT 'c'",
            "    MEDIA TYPE 'text/plain'",
            "    FORMAT ITEM",
            "    AUTHENTICATION PROCEDURE `s`.`p`",
            "    OPTIONS {",
            "        \"a\": 1",
            "    }",
            "    METADATA {",
            "        \"m\": 2",
            "    };",
        ].join("\n"));
        const [alter] = objectStatements(values, {
            services: [], schemas: {}, existingPath: "/old",
        });
        expect(alter.split("\n").slice(0, 3)).toEqual([
            "ALTER REST VIEW /old",
            "    ON SERVICE /svc SCHEMA /sakila",
            "    NEW REQUEST PATH /city",
        ]);
    });
});

describe("the content set dialog", () => {
    it("suggests a path after the folder", () => {
        expect(contentSetPathFor("/home/me/my-app")).toBe("/myAppContent");
        expect(contentSetPathFor("/home/me/my-app/")).toBe("/myAppContent");
        expect(contentSetPathFor("/home/me/app/dist")).toBe("/appContent");
        expect(contentSetPathFor("C:\\work\\shop\\Build"))
            .toBe("/shopContent");
        expect(contentSetPathFor("out")).toBe("/outContent");
        expect(contentSetPathFor("/web/static")).toBe("/staticContent");
        expect(contentSetPathFor("")).toBe("/content");
        expect(contentSetPathFor("/")).toBe("/content");
    });

    it("starts a new content set", () => {
        expect(contentSetDefaults([
            mrsService({ full_service_path: "/a" }),
            mrsService({ full_service_path: "/b", is_current: true }),
        ], undefined, undefined, "/x/site")).toEqual({
            servicePath: "/b",
            requestPath: "/siteContent",
            enabled: ENABLED_STATE.enabled,
            requiresAuth: false,
            comments: "",
            options: "",
            directory: "/x/site",
            ignoreList: DEFAULT_IGNORE_LIST,
            loadScripts: false,
        });
        expect(contentSetDefaults([], undefined)).toMatchObject({
            servicePath: "", requestPath: "/content", directory: "",
        });
        expect(contentSetDefaults([mrsService()], undefined, "/given")
            .servicePath).toBe("/given");
    });

    it("starts from what a content set has, less the generated options",
        () => {
            const contentSet = mrsContentSet({
                content_type: "SCRIPTS",
                requires_auth: true,
                enabled: ENABLED_STATE.private,
                comments: "c",
                options: {
                    contains_mrs_scripts: true,
                    mrs_scripting_language: "TypeScript",
                    script_module_files: [],
                    script_definitions: {},
                    keep: 1,
                },
            });
            expect(contentSetDefaults([mrsService()], contentSet))
                .toMatchObject({
                    servicePath: "/svc",
                    requestPath: "/web",
                    enabled: ENABLED_STATE.private,
                    requiresAuth: true,
                    comments: "c",
                    options: prettyJson({ keep: 1 }),
                    loadScripts: true,
                });
            expect(contentSet.options).toHaveProperty("contains_mrs_scripts");
            expect(GENERATED_CONTENT_SET_OPTIONS).toHaveLength(4);
        });

    it("checks every field", () => {
        const context: IContentSetContext = { services: ["/svc"] };
        expect(validateContentSet(contentSetValues(), context)).toEqual([]);
        expect(validateContentSet(contentSetValues({
            servicePath: "",
            requestPath: "web",
            directory: " ",
            options: "[",
        }), context)).toEqual([
            { field: "servicePath", message: "Please select a REST service." },
            { field: "requestPath",
                message: "The request path must start with /." },
            { field: "directory",
                message: "Please select the folder to upload." },
            { field: "options",
                message: "The options need to conform to JSON format." },
        ]);
        // An existing content set is not uploaded again.
        expect(validateContentSet(contentSetValues({ directory: "" }),
            { ...context, existingPath: "/web" })).toEqual([]);
    });

    it("applies the settings, keeping the generated options", () => {
        const existing = mrsContentSet({
            options: {
                contains_mrs_scripts: true,
                script_definitions: { a: 1 },
                dropped: "user edited this away",
            },
        });
        const [sql] = contentSetStatements(contentSetValues({
            requestPath: " /web2 ",
            options: "{\"keep\": 2, \"script_definitions\": {\"b\": 2}}",
            loadScripts: true,
        }), { services: [], existingPath: "/web" }, existing);
        expect(sql).toBe([
            "ALTER REST CONTENT SET /web ON SERVICE /svc",
            "    NEW REQUEST PATH /web2",
            "    ENABLED",
            "    AUTHENTICATION NOT REQUIRED",
            "    COMMENT ''",
            "    OPTIONS {",
            "        \"contains_mrs_scripts\": true,",
            "        \"script_definitions\": {",
            "            \"b\": 2",
            "        },",
            "        \"keep\": 2",
            "    }",
            "    LOAD TYPESCRIPT SCRIPTS;",
        ].join("\n"));
    });

    it("loads scripts only into an existing set that has none", () => {
        const loads = (
            context: IContentSetContext,
            existing?: IMrsContentSet,
            loadScripts = true,
        ): boolean => {
            return contentSetStatements(contentSetValues({ loadScripts }),
                context, existing)[0].includes("LOAD TYPESCRIPT SCRIPTS");
        };
        const editing = { services: [], existingPath: "/appContent" };
        expect(loads(editing, mrsContentSet())).toBe(true);
        expect(loads(editing, undefined)).toBe(true);
        expect(loads(editing, mrsContentSet({ content_type: "SCRIPTS" })))
            .toBe(false);
        expect(loads(editing, mrsContentSet(), false)).toBe(false);
        expect(loads({ services: [] }, undefined)).toBe(false);
    });

    it("gives a just uploaded set its settings at its own path", () => {
        const [sql] = contentSetStatements(contentSetValues(),
            { services: [] });
        expect(sql.split("\n")[0])
            .toBe("ALTER REST CONTENT SET /appContent ON SERVICE /svc");
        expect(sql).not.toContain("NEW REQUEST PATH");
        expect(sql).toContain("    OPTIONS {};");
    });
});

describe("the auth app dialog", () => {
    const roles = [
        { id: "r1", caption: "Full Access" },
        { id: "r2", caption: "Reader" },
    ];
    const app: IMrsAuthApp = {
        id: "a1",
        auth_vendor_id: GOOGLE_VENDOR_ID,
        auth_vendor: "Google",
        name: "g",
        description: "d",
        url: "https://g",
        app_id: "id",
        has_app_secret: true,
        enabled: false,
        limit_to_registered_users: false,
        default_role_id: "r2",
    };

    it("starts a new app as an MRS app with full access", () => {
        expect(authAppDefaults(undefined, roles)).toEqual({
            vendorName: "MRS",
            name: "",
            enabled: true,
            limitToRegisteredUsers: true,
            description: "",
            defaultRole: "Full Access",
            url: "",
            appId: "",
            appSecret: "",
        });
    });

    it("starts from what an app has, never its secret", () => {
        expect(authAppDefaults(app, roles)).toEqual({
            vendorName: "Google",
            name: "g",
            enabled: false,
            limitToRegisteredUsers: false,
            description: "d",
            defaultRole: "Reader",
            url: "https://g",
            appId: "id",
            appSecret: "",
        });
        expect(authAppDefaults({
            ...app, default_role_id: null, description: null, url: null,
            app_id: null,
        }, roles)).toMatchObject({
            defaultRole: "", description: "", url: "", appId: "",
        });
    });

    it("finds the vendor by name", () => {
        expect(vendorIdOf(appValues({ vendorName: "Google" }), appContext()))
            .toBe(GOOGLE_VENDOR_ID);
        expect(vendorIdOf(appValues({ vendorName: "Nope" }), appContext()))
            .toBeUndefined();
    });

    it("checks the name and the vendor", () => {
        expect(validateAuthApp(appValues(), appContext())).toEqual([]);
        expect(validateAuthApp(appValues({ vendorName: "", name: " " }),
            appContext())).toEqual([
            { field: "vendorName",
                message: "The vendor name must not be empty." },
            { field: "name", message: "The name must not be empty." },
        ]);
    });

    it("needs the OAuth2 settings for an OAuth2 vendor only", () => {
        const oauth = appValues({ vendorName: "Google" });
        expect(validateAuthApp(oauth, appContext())).toEqual([
            { field: "url",
                message: "The App URL must not be empty for OAuth2 auth "
                    + "apps." },
            { field: "appId",
                message: "The App ID must not be empty for OAuth2 auth apps." },
            { field: "appSecret",
                message: "The App Secret must not be empty for OAuth2 auth "
                    + "apps." },
        ]);
        expect(validateAuthApp({
            ...oauth, url: "https://g", appId: "id",
        }, appContext({ hasSecret: true }))).toEqual([]);
        expect(validateAuthApp({
            ...oauth, url: "https://g", appId: "id", appSecret: "s",
        }, appContext())).toEqual([]);
        expect(validateAuthApp(appValues({ vendorName: "MariaDB Internal" }),
            appContext())).toEqual([]);
        expect(validateAuthApp(appValues({ vendorName: "Unknown" }),
            appContext())).toEqual([]);
    });

    it("creates an app, linking it to a service", () => {
        expect(authAppStatements(appValues({ name: " app " }),
            appContext({ linkToService: "/svc" }))).toEqual([
            "CREATE REST AUTH APP 'app' VENDOR MRS\n"
                + "    ENABLED\n"
                + "    COMMENT ''\n"
                + "    DO NOT ALLOW NEW USERS TO REGISTER\n"
                + "    DEFAULT ROLE 'Full Access';",
            "ALTER REST SERVICE /svc ADD AUTH APP 'app';",
        ]);
        expect(authAppStatements(appValues({ vendorName: "MariaDB Internal" }),
            appContext())).toEqual([
            "CREATE REST AUTH APP 'app' VENDOR MARIADB\n"
                + "    ENABLED\n"
                + "    COMMENT ''\n"
                + "    DO NOT ALLOW NEW USERS TO REGISTER\n"
                + "    DEFAULT ROLE 'Full Access';",
        ]);
        expect(authAppStatements(appValues({ vendorName: "Some Vendor" }),
            appContext())[0])
            .toMatch(/^CREATE REST AUTH APP 'app' VENDOR 'Some Vendor'\n/);
    });

    it("alters an app", () => {
        expect(authAppStatements(appValues({
            name: "new",
            vendorName: "Google",
            appSecret: "",
            appId: "id",
            url: "https://g",
            defaultRole: "",
        }), appContext({ existingName: "old" }))).toEqual([
            "ALTER REST AUTH APP 'old'\n"
                + "    NEW NAME 'new'\n"
                + "    ENABLED\n"
                + "    COMMENT ''\n"
                + "    DO NOT ALLOW NEW USERS TO REGISTER\n"
                + "    APP ID 'id'\n"
                + "    URL 'https://g';",
        ]);
    });
});

describe("the user dialog", () => {
    const user: IMrsUser = {
        id: "u1",
        auth_app_id: "a1",
        auth_app_name: "MRS",
        name: "anna",
        email: "a@b.c",
        vendor_user_id: "v",
        mapped_user_id: "m",
        login_permitted: false,
        has_password: true,
        app_options: { x: 1 },
        options: {
            email: "a@b.c", vendor_user_id: "v", mapped_user_id: "m", keep: 1,
        },
        roles: [{ role_id: "r1", caption: "Full Access" }],
    };

    it("starts a new user with the app's default role", () => {
        expect(userDefaults(undefined, "Full Access")).toEqual({
            name: "",
            password: "",
            email: "",
            loginPermitted: true,
            roles: ["Full Access"],
            options: "",
            appOptions: "",
            vendorUserId: "",
            mappedUserId: "",
        });
        expect(userDefaults(undefined, undefined).roles).toEqual([]);
    });

    it("starts from what a user has, the ids out of its options", () => {
        expect(userDefaults(user, "Ignored")).toEqual({
            name: "anna",
            password: "",
            email: "a@b.c",
            loginPermitted: false,
            roles: ["Full Access"],
            options: prettyJson({ keep: 1 }),
            appOptions: prettyJson({ x: 1 }),
            vendorUserId: "v",
            mappedUserId: "m",
        });
        expect(user.options).toHaveProperty("email");
        expect(userDefaults({
            ...user, name: null, email: null, vendor_user_id: null,
            mapped_user_id: null, options: null, app_options: null,
        }, undefined)).toMatchObject({
            name: "", email: "", vendorUserId: "", mappedUserId: "",
            options: "", appOptions: "",
        });
    });

    it("checks the name and the JSON", () => {
        expect(validateUser(userValues({ password: "Secret1!" }),
            userContext())).toEqual([]);
        expect(validateUser(userValues({
            name: " ", options: "[", appOptions: "x",
        }), userContext({ authAppVendorId: GOOGLE_VENDOR_ID }))).toEqual([
            { field: "name", message: "The user name must not be empty." },
            { field: "options", message: JSON_PROBLEM },
            { field: "appOptions", message: JSON_PROBLEM },
        ]);
    });

    it("needs a password for an MRS app's user only", () => {
        expect(validateUser(userValues(), userContext())).toEqual([{
            field: "password",
            message: "The authentication string is required for this app.",
        }]);
        expect(validateUser(userValues(), userContext({ hasPassword: true })))
            .toEqual([]);
        expect(validateUser(userValues({ password: "weakpassword" }),
            userContext({ hasPassword: true }))).toEqual([
            { field: "password", message: COMPLEXITY_PROBLEM },
        ]);
        expect(validateUser(userValues(),
            userContext({ authAppVendorId: MARIADB_VENDOR_ID }))).toEqual([]);
        expect(validateUser(userValues({ password: "x" }),
            userContext({ authAppVendorId: GOOGLE_VENDOR_ID }))).toEqual([]);
    });

    it("creates a user and grants its roles", () => {
        expect(userStatements(userValues({
            name: " anna ",
            password: "Secret1!",
            email: " a@b.c ",
            vendorUserId: " v ",
            mappedUserId: " m ",
            loginPermitted: false,
            roles: ["Full Access", "Editor"],
            options: "{\"keep\": 1}",
            appOptions: "{\"x\": 2}",
        }), userContext({ roleServices: { Editor: "/svc" } }))).toEqual([
            "CREATE REST USER 'anna'@'MRS' IDENTIFIED BY 'Secret1!'\n"
                + "    ACCOUNT LOCK\n"
                + "    APP OPTIONS {\n"
                + "        \"x\": 2\n"
                + "    }\n"
                + "    OPTIONS {\n"
                + "        \"keep\": 1,\n"
                + "        \"email\": \"a@b.c\",\n"
                + "        \"vendor_user_id\": \"v\",\n"
                + "        \"mapped_user_id\": \"m\"\n"
                + "    };",
            "GRANT REST ROLE 'Full Access' ON ANY SERVICE TO 'anna'@'MRS';",
            "GRANT REST ROLE 'Editor' ON SERVICE /svc TO 'anna'@'MRS';",
        ]);
    });

    it("alters a user, revoking then granting the difference", () => {
        const statements = userStatements(userValues({
            roles: ["Editor", "New"],
        }), userContext({
            authApp: "App",
            existingName: "anna",
            existingRoles: ["Full Access", "Editor", "Scoped"],
            roleServices: { "Full Access": null, Scoped: "m@/s-v" },
        }));
        expect(statements[0]).toMatch(/^ALTER REST USER 'anna'@'App'\n/);
        expect(statements[0]).not.toContain("IDENTIFIED BY");
        expect(statements.slice(1)).toEqual([
            "REVOKE REST ROLE 'Full Access' ON ANY SERVICE FROM "
                + "'anna'@'App';",
            "REVOKE REST ROLE 'Scoped' ON SERVICE m@`/s-v` FROM "
                + "'anna'@'App';",
            "GRANT REST ROLE 'New' ON ANY SERVICE TO 'anna'@'App';",
        ]);
    });

    it("changes no roles where none changed", () => {
        expect(userStatements(userValues({ roles: ["Editor"] }), userContext({
            existingName: "anna", existingRoles: ["Editor"],
        }))).toHaveLength(1);
    });
});

describe("the SDK export dialog", () => {
    it("offers the languages and base classes", () => {
        expect(SDK_LANGUAGES).toEqual(["TypeScript", "Python"]);
        expect(APP_BASE_CLASSES.TypeScript).toEqual(["MrsBaseAppPreact.ts"]);
        expect(APP_BASE_CLASSES.Python).toEqual([]);
    });

    it("checks the directory and the URL", () => {
        const values = {
            directory: "/out",
            serviceUrl: "https://localhost:8443/svc",
            sdkLanguage: "TypeScript",
            addAppBaseClass: "",
            header: "",
        };
        expect(validateSdkExport(values)).toEqual([]);
        expect(validateSdkExport({
            ...values, directory: " ", serviceUrl: "",
        })).toEqual([
            { field: "directory", message: "Please specify a directory." },
            { field: "serviceUrl",
                message: "The REST service URL must not be empty." },
        ]);
    });
});

describe("validateDialog", () => {
    it("dispatches to each dialog's checks", () => {
        const fields = (problems: Array<{ field: string }>): string[] => {
            return problems.map((problem) => { return problem.field; });
        };
        expect(fields(validateDialog("configure",
            configure({ metadataSchema: "x", createAuthApp: true }), INIT)))
            .toEqual(["metadataSchema", "createAuthApp"]);
        expect(fields(validateDialog("service", serviceValues({ path: "" }),
            undefined))).toEqual(["path"]);
        expect(fields(validateDialog("schema",
            schemaValues({ dbSchema: "" }), undefined))).toEqual(["dbSchema"]);
        expect(fields(validateDialog("object",
            objectValues({ schemaPath: "" }), undefined)))
            .toEqual(["schemaPath"]);
        expect(fields(validateDialog("contentSet",
            contentSetValues({ directory: "" }), { services: [] })))
            .toEqual(["directory"]);
        expect(fields(validateDialog("authApp", appValues({ name: "" }),
            appContext()))).toEqual(["name"]);
        expect(fields(validateDialog("user", userValues(), userContext())))
            .toEqual(["password"]);
        expect(fields(validateDialog("sdkExport", {
            directory: "", serviceUrl: "u", sdkLanguage: "TypeScript",
            addAppBaseClass: "", header: "",
        }, undefined))).toEqual(["directory"]);
    });
});
