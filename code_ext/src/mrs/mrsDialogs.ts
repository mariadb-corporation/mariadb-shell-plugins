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

import {
    camelCase,
    isRoutine,
    objectSql,
    type IMappingDocument,
} from "./dataMapping.js";
import {
    ENABLED_STATE,
    isOAuthVendor,
    type EnabledState,
    type IMrsAuthApp,
    type IMrsContentSet,
    type IMrsObject,
    type IMrsSchema,
    type IMrsService,
    type IMrsStatus,
    type IMrsUser,
    type JsonObject,
} from "./mrsTypes.js";
import {
    alterAuthAppSql,
    alterContentSetSql,
    alterSchemaSql,
    alterServiceSql,
    alterUserSql,
    configureMetadataSql,
    createAuthAppSql,
    createSchemaSql,
    createServiceSql,
    createUserSql,
    linkAuthAppSql,
    useServiceSql,
    userRoleSql,
} from "./restSql.js";

/**
 * The MRS dialogs' fields, what they start with, what is wrong with them
 * and the REST SQL they become. The webview checks as the user types and
 * the host checks again before it runs anything; both use what is here.
 * Free of `vscode` and of the DOM.
 *
 * The dialogs are the MySQL Shell for VS Code extension's, field for
 * field where REST SQL can say the same, and so are their messages.
 */

/** The dialogs. */
export type MrsDialogKind =
    | "configure"
    | "service"
    | "schema"
    | "object"
    | "contentSet"
    | "authApp"
    | "user"
    | "sdkExport";

/** What is wrong with a field. */
export interface IDialogProblem {
    field: string;
    message: string;
}

/** The message a field of JSON gets when it is none. */
export const JSON_PROBLEM = "Please provide a valid JSON object.";

/**
 * @param value A JSON object, or nothing.
 *
 * @returns It pretty printed; `''` for none or an empty one.
 */
export const prettyJson = (value: JsonObject | null | undefined): string => {
    return value === null || value === undefined
        || Object.keys(value).length === 0
        ? "" : JSON.stringify(value, undefined, 4);
};

/**
 * @param text What a JSON field holds.
 *
 * @returns The object; null for an empty field; undefined where it is not
 *          a JSON object.
 */
export const parseJsonObject = (text: string): JsonObject | null | undefined => {
    if (text.trim() === "") {
        return null;
    }
    try {
        const value = JSON.parse(text) as unknown;

        return typeof value === "object" && value !== null
            && !Array.isArray(value) ? value as JsonObject : undefined;
    } catch {
        return undefined;
    }
};

const jsonProblems = (
    fields: Record<string, string>,
): IDialogProblem[] => {
    return Object.entries(fields).filter(([, text]) => {
        return parseJsonObject(text) === undefined;
    }).map(([field]) => {
        return { field, message: JSON_PROBLEM };
    });
};

/**
 * @param path A request path.
 * @param field The field holding it.
 *
 * @returns What is wrong with it, if anything.
 */
export const requestPathProblems = (
    path: string,
    field = "requestPath",
): IDialogProblem[] => {
    if (path.trim() === "") {
        return [{ field, message: "The request path must not be empty." }];
    }

    return path.startsWith("/")
        ? []
        : [{ field, message: "The request path must start with /." }];
};

/**
 * @param password A REST user's password.
 * @param field The field holding it.
 *
 * @returns What MRS requires of it that it lacks.
 */
export const passwordProblems = (
    password: string,
    field: string,
): IDialogProblem[] => {
    if (password.length < 8) {
        return [{
            field,
            message: "The minimum authentication string length is 8 "
                + "characters.",
        }];
    }
    if (!/[A-Z]/.test(password) || !/[a-z]/.test(password)
        || !/[0-9]/.test(password) || !/\W/.test(password)) {
        return [{
            field,
            message: "The authentication string needs to contain at least "
                + "one uppercase, lowercase, a special and a numeric "
                + "character.",
        }];
    }

    return [];
};

/**
 * @param text What an items-per-page field holds.
 *
 * @returns The number; null for the default; NaN where it is none.
 */
export const parseItemsPerPage = (text: string): number | null => {
    if (text.trim() === "") {
        return null;
    }

    return /^\d+$/.test(text.trim()) ? Number(text.trim()) : Number.NaN;
};

const itemsPerPageProblems = (text: string): IDialogProblem[] => {
    const value = parseItemsPerPage(text);

    return value !== null && (Number.isNaN(value) || value < 1)
        ? [{
            field: "itemsPerPage",
            message: "The items per page must be a positive number.",
        }]
        : [];
};

/** The parsed JSON fields; only called once they passed validation. */
const json = (text: string): JsonObject | null => {
    return parseJsonObject(text) ?? null;
};

// --- configuration ----------------------------------------------------

/** The configuration dialog's fields. */
export interface IConfigureValues {
    enabled: boolean;
    /** For a first deployment: the metadata schema's name. */
    metadataSchema: string;
    /** Update the metadata schema to the bundled version. */
    update: boolean;
    /** For a first deployment: create an MRS auth app with one user. */
    createAuthApp: boolean;
    authAppUser: string;
    authAppPassword: string;
    perAccountMinimumTime: string;
    perAccountMaximumAttempts: string;
    perHostMinimumTime: string;
    perHostMaximumAttempts: string;
    blockTimeout: string;
    responseCacheSize: string;
    fileCacheSize: string;
    gtidCache: boolean;
    gtidRefreshRate: string;
    gtidRefreshWhenIncreasedBy: string;
    /** Comma separated. */
    directoryIndex: string;
    defaultStaticContent: string;
    defaultRedirects: string;
    /** The configuration's other options, as JSON. */
    options: string;
}

/** What the configuration dialog shows besides its fields. */
export interface IConfigureContext {
    /** No metadata yet: the dialog deploys it. */
    init: boolean;
    currentVersion: string | null;
    availableVersion: string;
    /** An update is available and not skipped. */
    upgradeable: boolean;
    upgradeIgnored: boolean;
}

/** The name the metadata schema gets unless told otherwise. */
export const DEFAULT_METADATA_SCHEMA = "mariadb_rest_service";

type Path = string[];

const read = (options: JsonObject, path: Path): unknown => {
    let value: unknown = options;
    for (const key of path) {
        if (typeof value !== "object" || value === null) {
            return undefined;
        }
        value = (value as JsonObject)[key];
    }

    return value;
};

/** The configuration options the dialog has fields for, by field. */
const CONFIG_PATHS: Record<string, Path> = {
    perAccountMinimumTime: ["authentication", "throttling", "perAccount",
        "minimumTimeBetweenRequestsInMs"],
    perAccountMaximumAttempts: ["authentication", "throttling", "perAccount",
        "maximumAttemptsPerMinute"],
    perHostMinimumTime: ["authentication", "throttling", "perHost",
        "minimumTimeBetweenRequestsInMs"],
    perHostMaximumAttempts: ["authentication", "throttling", "perHost",
        "maximumAttemptsPerMinute"],
    blockTimeout: ["authentication", "throttling",
        "blockWhenAttemptsExceededInSeconds"],
    responseCacheSize: ["responseCache", "maxCacheSize"],
    fileCacheSize: ["fileCache", "maxCacheSize"],
    gtidRefreshRate: ["gtid", "cache", "refreshRate"],
    gtidRefreshWhenIncreasedBy: ["gtid", "cache", "refreshWhenIncreasesBy"],
};

/** The number fields among {@link CONFIG_PATHS}; the others are text. */
const NUMBER_FIELDS = new Set([
    "perAccountMinimumTime", "perAccountMaximumAttempts",
    "perHostMinimumTime", "perHostMaximumAttempts", "blockTimeout",
    "gtidRefreshRate", "gtidRefreshWhenIncreasedBy",
]);

/** Removes a path from a copy of the options, and empty parents with it. */
const without = (options: JsonObject, path: Path): void => {
    const parents: JsonObject[] = [options];
    for (const key of path.slice(0, -1)) {
        const next = parents.at(-1)![key];
        if (typeof next !== "object" || next === null) {
            return;
        }
        parents.push(next as JsonObject);
    }
    delete parents.at(-1)![path.at(-1)!];
    for (let i = parents.length - 1; i > 0; i -= 1) {
        if (Object.keys(parents[i]).length === 0) {
            delete parents[i - 1][path[i - 1]];
        }
    }
};

/**
 * @param status The server's REST metadata status.
 *
 * @returns What the configuration dialog shows besides its fields.
 */
export const configureContextOf = (status: IMrsStatus): IConfigureContext => {
    return {
        init: !status.service_configured,
        currentVersion: status.current_metadata_version,
        availableVersion: status.available_metadata_version,
        upgradeable: status.service_upgradeable,
        upgradeIgnored: status.service_upgrade_ignored,
    };
};

/**
 * @param status The server's REST metadata status.
 *
 * @returns The configuration dialog's fields.
 */
export const configureDefaults = (status: IMrsStatus): IConfigureValues => {
    const options = structuredClone(status.configuration_options ?? {});
    const values: Record<string, string> = {};
    for (const [field, path] of Object.entries(CONFIG_PATHS)) {
        const value = read(options, path);
        values[field] = value === undefined || value === null ? "" : String(value);
        without(options, path);
    }
    const gtid = read(options, ["gtid", "cache", "enable"]);
    without(options, ["gtid", "cache", "enable"]);
    const index = read(options, ["directoryIndexDirective"]);
    without(options, ["directoryIndexDirective"]);
    const staticContent = read(options, ["defaultStaticContent"]);
    without(options, ["defaultStaticContent"]);
    const redirects = read(options, ["defaultRedirects"]);
    without(options, ["defaultRedirects"]);

    return {
        enabled: status.service_configured ? status.service_enabled : true,
        metadataSchema: status.metadata_schema || DEFAULT_METADATA_SCHEMA,
        update: false,
        createAuthApp: !status.service_configured,
        authAppUser: "",
        authAppPassword: "",
        perAccountMinimumTime: values.perAccountMinimumTime,
        perAccountMaximumAttempts: values.perAccountMaximumAttempts,
        perHostMinimumTime: values.perHostMinimumTime,
        perHostMaximumAttempts: values.perHostMaximumAttempts,
        blockTimeout: values.blockTimeout,
        responseCacheSize: values.responseCacheSize,
        fileCacheSize: values.fileCacheSize,
        gtidCache: gtid === true,
        gtidRefreshRate: values.gtidRefreshRate,
        gtidRefreshWhenIncreasedBy: values.gtidRefreshWhenIncreasedBy,
        directoryIndex: Array.isArray(index)
            ? index.join(", ") : "index.html",
        defaultStaticContent: prettyJson(staticContent as JsonObject),
        defaultRedirects: prettyJson(redirects as JsonObject),
        options: prettyJson(options),
    };
};

/** Pattern a metadata schema name follows: prefix, base, postfix. */
const METADATA_SCHEMA_NAME =
    /^([A-Za-z_][A-Za-z0-9_]*)?mariadb_rest_service(_[A-Za-z0-9_]+)?$/;

export const validateConfigure = (
    values: IConfigureValues,
    context: IConfigureContext,
): IDialogProblem[] => {
    const problems: IDialogProblem[] = [];
    if (context.init) {
        if (!METADATA_SCHEMA_NAME.test(values.metadataSchema)) {
            problems.push({
                field: "metadataSchema",
                message: "The name must be mariadb_rest_service, optionally "
                    + "with a prefix and a postfix starting with _.",
            });
        }
        if (values.createAuthApp) {
            if (values.authAppUser === "" && values.authAppPassword === "") {
                problems.push({
                    field: "createAuthApp",
                    message: "Please specify a REST user name or disable this "
                        + "option to skip the creation of the default REST "
                        + "authentication app.",
                });
            } else if (values.authAppUser.trim() === "") {
                problems.push({
                    field: "authAppUser",
                    message: "Please specify a REST user name.",
                });
            } else if (values.authAppPassword === "") {
                problems.push({
                    field: "authAppPassword",
                    message: "Please specify a password for the MRS Admin User.",
                });
            } else {
                problems.push(...passwordProblems(values.authAppPassword,
                    "authAppPassword"));
            }
        }

        return problems;
    }

    for (const field of NUMBER_FIELDS) {
        const text = (values as unknown as Record<string, string>)[field];
        if (text.trim() !== "" && !/^\d+$/.test(text.trim())) {
            problems.push({ field, message: "Please enter a whole number." });
        }
    }
    problems.push(...jsonProblems({
        defaultStaticContent: values.defaultStaticContent,
        defaultRedirects: values.defaultRedirects,
        options: values.options,
    }));

    return problems;
};

/**
 * @param values The configuration dialog's fields.
 *
 * @returns The configuration options they make up.
 */
export const configureOptions = (values: IConfigureValues): JsonObject => {
    const options: JsonObject = structuredClone(json(values.options) ?? {});
    const set = (path: Path, value: unknown): void => {
        let target = options;
        for (const key of path.slice(0, -1)) {
            const next = target[key];
            target = (typeof next === "object" && next !== null
                ? next : (target[key] = {})) as JsonObject;
        }
        target[path.at(-1)!] = value;
    };
    for (const [field, path] of Object.entries(CONFIG_PATHS)) {
        const text = (values as unknown as Record<string, string>)[field].trim();
        if (text !== "") {
            set(path, NUMBER_FIELDS.has(field) ? Number(text) : text);
        }
    }
    if (values.gtidCache) {
        set(["gtid", "cache", "enable"], true);
    }
    const index = values.directoryIndex.split(",").map((name) => {
        return name.trim();
    }).filter((name) => { return name !== ""; });
    if (index.length > 0) {
        set(["directoryIndexDirective"], index);
    }
    const staticContent = json(values.defaultStaticContent);
    if (staticContent !== null) {
        set(["defaultStaticContent"], staticContent);
    }
    const redirects = json(values.defaultRedirects);
    if (redirects !== null) {
        set(["defaultRedirects"], redirects);
    }

    return options;
};

/** The name of the auth app a first deployment can create. */
export const DEFAULT_AUTH_APP = "MRS";

/**
 * @param values The configuration dialog's fields.
 * @param context What it was opened on.
 *
 * @returns The statements that apply them.
 */
export const configureStatements = (
    values: IConfigureValues,
    context: IConfigureContext,
): string[] => {
    if (context.init) {
        return [
            configureMetadataSql({
                enabled: values.enabled,
                ...(values.metadataSchema === DEFAULT_METADATA_SCHEMA
                    ? {} : { metadataSchema: values.metadataSchema }),
            }),
            ...(values.createAuthApp && values.authAppUser !== ""
                ? [
                    `CREATE REST AUTH APP IF NOT EXISTS '${DEFAULT_AUTH_APP}' `
                    + "VENDOR MRS COMMENT 'MRS Auth App' "
                    + "DO NOT ALLOW NEW USERS TO REGISTER "
                    + "DEFAULT ROLE 'Full Access';",
                    createUserSql({
                        name: values.authAppUser,
                        authApp: DEFAULT_AUTH_APP,
                        password: values.authAppPassword,
                        email: "",
                        vendorUserId: "",
                        mappedUserId: "",
                        loginPermitted: true,
                        options: null,
                        appOptions: null,
                    }),
                ]
                : []),
        ];
    }

    return [configureMetadataSql({
        enabled: values.enabled,
        options: configureOptions(values),
        update: values.update,
    })];
};

// --- services ---------------------------------------------------------

/** The service dialog's fields. */
export interface IServiceDialogValues {
    /** `[devs@]/path`. */
    path: string;
    enabled: boolean;
    published: boolean;
    /** Makes it the current REST service of the tree's session. */
    makeCurrent: boolean;
    protocol: "HTTP" | "HTTPS";
    comments: string;
    options: string;
    metadata: string;
    authPath: string;
    authCompletedUrl: string;
    authCompletedUrlValidation: string;
    authCompletedPageContent: string;
    /** The auth apps linked to it, by name. */
    authApps: string[];
}

/** What the service dialog offers besides its fields. */
export interface IServiceContext {
    /** Every auth app's name. */
    allAuthApps: string[];
    /** The service's full path, when it is edited. */
    existingPath?: string;
    /** The apps linked when it was opened. */
    linkedAuthApps: string[];
}

/** The options a new service starts with, as the shell gives them too. */
export const DEFAULT_SERVICE_OPTIONS: JsonObject = {
    headers: {
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers":
            "Content-Type, Authorization, X-Requested-With, Origin, X-Auth-Token",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    },
    http: { allowedOrigin: "auto" },
    logging: {
        exceptions: true,
        request: { body: true, headers: true },
        response: { body: true, headers: true },
    },
    returnInternalErrorDetails: true,
    includeLinksInResults: false,
};

/**
 * @param service The service to edit; undefined for a new one.
 * @param linked The names of the apps linked to it.
 * @param allAuthApps Every auth app's name.
 *
 * @returns The dialog's fields.
 */
export const serviceDefaults = (
    service: IMrsService | undefined,
    linked: string[],
    allAuthApps: string[],
): IServiceDialogValues => {
    if (service === undefined) {
        // A new service can be signed into with the built-in apps from the
        // start, which is what a first try with it needs.
        const preferred = ["MRS", "MariaDB"].find((name) => {
            return allAuthApps.includes(name);
        });

        return {
            path: "/myService",
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
            authApps: preferred === undefined ? [] : [preferred],
        };
    }

    const protocols = Array.isArray(service.url_protocol)
        ? service.url_protocol
        : typeof service.url_protocol === "string"
            ? service.url_protocol.split(",") : [];

    return {
        path: service.full_service_path,
        enabled: service.enabled !== 0,
        published: service.published === true || service.published === 1,
        makeCurrent: service.is_current === true,
        protocol: protocols.at(-1)?.trim() === "HTTP" ? "HTTP" : "HTTPS",
        comments: service.comments ?? "",
        options: prettyJson(service.options),
        metadata: prettyJson(service.metadata),
        authPath: service.auth_path ?? "",
        authCompletedUrl: service.auth_completed_url ?? "",
        authCompletedUrlValidation: service.auth_completed_url_validation ?? "",
        authCompletedPageContent: service.auth_completed_page_content ?? "",
        authApps: [...linked],
    };
};

export const validateService = (
    values: IServiceDialogValues,
): IDialogProblem[] => {
    const problems: IDialogProblem[] = [];
    const path = values.path.slice(values.path.lastIndexOf("@") + 1);
    if (values.path.trim() === "") {
        problems.push({
            field: "path", message: "The service path must not be empty.",
        });
    } else if (!path.startsWith("/")) {
        problems.push({
            field: "path", message: "The request path must start with /.",
        });
    } else if (path.toLowerCase() === "/mrs") {
        problems.push({
            field: "path",
            message: `The request path \`${path}\` is reserved and cannot `
                + "be used.",
        });
    }
    problems.push(...jsonProblems({
        options: values.options, metadata: values.metadata,
    }));

    return problems;
};

/**
 * @param values The service dialog's fields.
 * @param context What it was opened on.
 *
 * @returns The statements that create or change the service.
 */
export const serviceStatements = (
    values: IServiceDialogValues,
    context: IServiceContext,
): string[] => {
    const service = {
        path: values.path.trim(),
        enabled: values.enabled,
        published: values.published,
        protocol: values.protocol,
        comments: values.comments,
        options: json(values.options),
        metadata: json(values.metadata),
        authPath: values.authPath,
        authCompletedUrl: values.authCompletedUrl,
        authCompletedUrlValidation: values.authCompletedUrlValidation,
        authCompletedPageContent: values.authCompletedPageContent,
    };
    const use = values.makeCurrent ? [useServiceSql(service.path)] : [];

    if (context.existingPath === undefined) {
        return [createServiceSql(service, values.authApps), ...use];
    }

    return [
        alterServiceSql(context.existingPath, service,
            values.authApps.filter((name) => {
                return !context.linkedAuthApps.includes(name);
            }),
            context.linkedAuthApps.filter((name) => {
                return !values.authApps.includes(name);
            })),
        ...use,
    ];
};

// --- REST schemas -----------------------------------------------------

/** The schema dialog's fields. */
export interface ISchemaDialogValues {
    servicePath: string;
    requestPath: string;
    dbSchema: string;
    enabled: EnabledState;
    requiresAuth: boolean;
    itemsPerPage: string;
    comments: string;
    options: string;
    metadata: string;
}

/** What the schema dialog offers besides its fields. */
export interface ISchemaContext {
    /** Every service's full path. */
    services: string[];
    /** The REST schema's path, when it is edited. */
    existingPath?: string;
}

/**
 * @param services Every service.
 * @param schema The REST schema to edit; undefined for a new one.
 * @param dbSchema For a new one: the database schema it exposes.
 * @param servicePath The service it is on, or to be on.
 *
 * @returns The dialog's fields.
 */
export const schemaDefaults = (
    services: IMrsService[],
    schema: IMrsSchema | undefined,
    dbSchema: string,
    servicePath?: string,
): ISchemaDialogValues => {
    const service = servicePath ?? (services.find((candidate) => {
        return candidate.is_current === true;
    }) ?? services[0])?.full_service_path ?? "";

    return {
        servicePath: service,
        requestPath: schema?.request_path ?? `/${camelCase(dbSchema || "schema")}`,
        dbSchema: schema?.name ?? dbSchema,
        enabled: schema?.enabled ?? ENABLED_STATE.enabled,
        requiresAuth: schema?.requires_auth ?? false,
        itemsPerPage: schema?.items_per_page === null
            || schema?.items_per_page === undefined
            ? "" : String(schema.items_per_page),
        comments: schema?.comments ?? "",
        options: prettyJson(schema?.options),
        metadata: prettyJson(schema?.metadata),
    };
};

export const validateSchema = (
    values: ISchemaDialogValues,
): IDialogProblem[] => {
    return [
        ...(values.servicePath === ""
            ? [{ field: "servicePath", message: "Please select a REST service." }]
            : []),
        ...requestPathProblems(values.requestPath),
        ...(values.dbSchema.trim() === ""
            ? [{
                field: "dbSchema",
                message: "The database schema name must not be empty.",
            }]
            : []),
        ...itemsPerPageProblems(values.itemsPerPage),
        ...jsonProblems({ options: values.options, metadata: values.metadata }),
    ];
};

export const schemaStatements = (
    values: ISchemaDialogValues,
    context: ISchemaContext,
): string[] => {
    const schema = {
        servicePath: values.servicePath,
        requestPath: values.requestPath.trim(),
        dbSchema: values.dbSchema.trim(),
        enabled: values.enabled,
        requiresAuth: values.requiresAuth,
        itemsPerPage: parseItemsPerPage(values.itemsPerPage),
        comments: values.comments,
        options: json(values.options),
        metadata: json(values.metadata),
    };

    return [context.existingPath === undefined
        ? createSchemaSql(schema)
        : alterSchemaSql(context.existingPath, schema)];
};

// --- REST objects -----------------------------------------------------

/** The REST object dialog's fields. */
export interface IObjectDialogValues {
    servicePath: string;
    schemaPath: string;
    requestPath: string;
    enabled: EnabledState;
    requiresAuth: boolean;
    itemsPerPage: string;
    comments: string;
    mediaType: string;
    autoDetectMediaType: boolean;
    format: "FEED" | "ITEM" | "MEDIA";
    authStoredProcedure: string;
    options: string;
    metadata: string;
    document: IMappingDocument;
}

/** What the object dialog offers besides its fields. */
export interface IObjectContext {
    /** Every service's full path. */
    services: string[];
    /** The REST schemas of each service, by service. */
    schemas: Record<string, string[]>;
    /** The object's path, when it is edited. */
    existingPath?: string;
}

/**
 * @param object The REST object to edit, or what a new one starts as.
 * @param document Its data mapping.
 * @param servicePath Its service.
 * @param schemaPath Its REST schema.
 *
 * @returns The dialog's fields.
 */
export const objectDefaults = (
    object: Partial<IMrsObject> & { name: string; object_type: string },
    document: IMappingDocument,
    servicePath: string,
    schemaPath: string,
): IObjectDialogValues => {
    return {
        servicePath,
        schemaPath,
        requestPath: object.request_path ?? `/${camelCase(object.name)}`,
        enabled: object.enabled ?? ENABLED_STATE.enabled,
        requiresAuth: object.requires_auth ?? true,
        itemsPerPage: object.items_per_page === null
            || object.items_per_page === undefined
            ? "" : String(object.items_per_page),
        comments: object.comments ?? "",
        mediaType: object.media_type ?? "",
        autoDetectMediaType: object.auto_detect_media_type ?? false,
        format: object.format ?? "FEED",
        authStoredProcedure: object.auth_stored_procedure ?? "",
        options: prettyJson(object.options),
        metadata: prettyJson(object.metadata),
        document,
    };
};

export const validateObject = (
    values: IObjectDialogValues,
): IDialogProblem[] => {
    return [
        ...(values.servicePath === ""
            ? [{ field: "servicePath", message: "Please select a REST service." }]
            : []),
        ...(values.schemaPath === ""
            ? [{ field: "schemaPath", message: "Please select a REST schema." }]
            : []),
        ...requestPathProblems(values.requestPath),
        ...(!isRoutine(values.document.objectType)
            && values.document.className.trim() === ""
            ? [{ field: "className", message: "The object name must not be empty." }]
            : []),
        ...itemsPerPageProblems(values.itemsPerPage),
        ...(parseJsonObject(values.options) === undefined
            ? [{
                field: "options",
                message: "The options must contain a valid JSON string.",
            }]
            : []),
        ...(parseJsonObject(values.metadata) === undefined
            ? [{
                field: "metadata",
                message: "The metadata field must contain a valid JSON string.",
            }]
            : []),
    ];
};

/**
 * @param values The object dialog's fields.
 * @param context What it was opened on.
 *
 * @returns The statement that creates or changes the object.
 */
export const objectStatements = (
    values: IObjectDialogValues,
    context: IObjectContext,
): string[] => {
    return [objectSql(values.document, {
        servicePath: values.servicePath,
        schemaPath: values.schemaPath,
        requestPath: values.requestPath.trim(),
    }, {
        enabled: values.enabled,
        requiresAuth: values.requiresAuth,
        itemsPerPage: parseItemsPerPage(values.itemsPerPage),
        comments: values.comments,
        mediaType: values.mediaType,
        autoDetectMediaType: values.autoDetectMediaType,
        format: values.format,
        authStoredProcedure: values.authStoredProcedure,
        options: json(values.options),
        metadata: json(values.metadata),
    }, context.existingPath)];
};

// --- content sets -----------------------------------------------------

/** The content set dialog's fields. */
export interface IContentSetDialogValues {
    servicePath: string;
    requestPath: string;
    enabled: EnabledState;
    requiresAuth: boolean;
    comments: string;
    options: string;
    /** The folder to upload; only for a new content set. */
    directory: string;
    ignoreList: string;
    /** Register the MRS scripts found in the folder. */
    loadScripts: boolean;
}

/** What the content set dialog offers besides its fields. */
export interface IContentSetContext {
    services: string[];
    /** The content set's path, when it is edited. */
    existingPath?: string;
}

/** The files a folder upload leaves out unless told otherwise. */
export const DEFAULT_IGNORE_LIST = "*node_modules/*, */.*";

/** The build folders a content set's path is not named after. */
const BUILD_FOLDERS = new Set(["build", "output", "out", "web", "dist"]);

/**
 * @param directory The folder to upload.
 *
 * @returns The request path it suggests: its name, in camelCase, with
 *          `Content` appended - or the folder above a build folder's.
 */
export const contentSetPathFor = (directory: string): string => {
    const names = directory.split(/[\\/]/).filter((name) => {
        return name !== "";
    });
    let name = names.at(-1);
    if (name !== undefined && BUILD_FOLDERS.has(name.toLowerCase())
        && names.length > 1) {
        name = names.at(-2);
    }

    return name === undefined ? "/content" : `/${camelCase(name)}Content`;
};

/** The option keys the shell writes for MRS scripts; never edited. */
export const GENERATED_CONTENT_SET_OPTIONS = [
    "contains_mrs_scripts", "mrs_scripting_language", "script_module_files",
    "script_definitions",
];

/**
 * @param services Every service.
 * @param contentSet The content set to edit; undefined for a new one.
 * @param servicePath The service it is on, or is to be on.
 * @param directory For a new one: the folder to upload.
 *
 * @returns The dialog's fields.
 */
export const contentSetDefaults = (
    services: IMrsService[],
    contentSet: IMrsContentSet | undefined,
    servicePath?: string,
    directory = "",
): IContentSetDialogValues => {
    const options = { ...(contentSet?.options ?? {}) };
    for (const key of GENERATED_CONTENT_SET_OPTIONS) {
        delete options[key];
    }

    return {
        servicePath: servicePath ?? (services.find((candidate) => {
            return candidate.is_current === true;
        }) ?? services[0])?.full_service_path ?? "",
        requestPath: contentSet?.request_path ?? contentSetPathFor(directory),
        enabled: contentSet?.enabled ?? ENABLED_STATE.enabled,
        requiresAuth: contentSet?.requires_auth ?? false,
        comments: contentSet?.comments ?? "",
        options: prettyJson(options),
        directory,
        ignoreList: DEFAULT_IGNORE_LIST,
        loadScripts: contentSet?.content_type === "SCRIPTS",
    };
};

export const validateContentSet = (
    values: IContentSetDialogValues,
    context: IContentSetContext,
): IDialogProblem[] => {
    return [
        ...(values.servicePath === ""
            ? [{ field: "servicePath", message: "Please select a REST service." }]
            : []),
        ...requestPathProblems(values.requestPath),
        ...(context.existingPath === undefined && values.directory.trim() === ""
            ? [{
                field: "directory",
                message: "Please select the folder to upload.",
            }]
            : []),
        ...(parseJsonObject(values.options) === undefined
            ? [{
                field: "options",
                message: "The options need to conform to JSON format.",
            }]
            : []),
    ];
};

/**
 * @param values The content set dialog's fields.
 * @param context What it was opened on.
 * @param existing The content set as it is now, to keep its generated
 *        options; undefined for one just uploaded.
 *
 * @returns The statement that applies the settings. A new content set is
 *          uploaded first, by the host, and then given them with this.
 */
export const contentSetStatements = (
    values: IContentSetDialogValues,
    context: IContentSetContext,
    existing?: IMrsContentSet,
): string[] => {
    const generated = Object.fromEntries(Object.entries(
        existing?.options ?? {}).filter(([key]) => {
        return GENERATED_CONTENT_SET_OPTIONS.includes(key);
    }));
    const options = { ...generated, ...(json(values.options) ?? {}) };

    return [alterContentSetSql(
        context.existingPath ?? values.requestPath.trim(),
        {
            servicePath: values.servicePath,
            requestPath: values.requestPath.trim(),
            enabled: values.enabled,
            requiresAuth: values.requiresAuth,
            comments: values.comments,
            options,
        },
        context.existingPath !== undefined && values.loadScripts
        && existing?.content_type !== "SCRIPTS",
    )];
};

// --- auth apps --------------------------------------------------------

/** The auth app dialog's fields. */
export interface IAuthAppDialogValues {
    vendorName: string;
    name: string;
    enabled: boolean;
    limitToRegisteredUsers: boolean;
    description: string;
    defaultRole: string;
    url: string;
    appId: string;
    /** Empty keeps a stored secret. */
    appSecret: string;
}

/** What the auth app dialog offers besides its fields. */
export interface IAuthAppContext {
    vendors: Array<{ id: string; name: string }>;
    /** Every role's caption. */
    roles: string[];
    /** The app's name, when it is edited. */
    existingName?: string;
    /** Whether the app has a secret stored. */
    hasSecret: boolean;
    /** A service to link a new app to. */
    linkToService?: string;
}

/**
 * @param app The app to edit; undefined for a new one.
 * @param roles Every role, by id and caption.
 *
 * @returns The dialog's fields.
 */
export const authAppDefaults = (
    app: IMrsAuthApp | undefined,
    roles: Array<{ id: string; caption: string }>,
): IAuthAppDialogValues => {
    return {
        vendorName: app?.auth_vendor ?? "MRS",
        name: app?.name ?? "",
        enabled: app?.enabled ?? true,
        limitToRegisteredUsers: app?.limit_to_registered_users ?? true,
        description: app?.description ?? "",
        defaultRole: roles.find((role) => {
            return role.id === app?.default_role_id;
        })?.caption ?? (app === undefined ? "Full Access" : ""),
        url: app?.url ?? "",
        appId: app?.app_id ?? "",
        appSecret: "",
    };
};

/**
 * @param values The auth app dialog's fields.
 * @param context What it was opened on.
 *
 * @returns The vendor picked, by id; undefined for none of the known.
 */
export const vendorIdOf = (
    values: IAuthAppDialogValues,
    context: IAuthAppContext,
): string | undefined => {
    return context.vendors.find((vendor) => {
        return vendor.name === values.vendorName;
    })?.id;
};

export const validateAuthApp = (
    values: IAuthAppDialogValues,
    context: IAuthAppContext,
): IDialogProblem[] => {
    const problems: IDialogProblem[] = [];
    if (values.vendorName === "") {
        problems.push({
            field: "vendorName", message: "The vendor name must not be empty.",
        });
    }
    if (values.name.trim() === "") {
        problems.push({ field: "name", message: "The name must not be empty." });
    }
    if (isOAuthVendor(vendorIdOf(values, context))) {
        if (values.url.trim() === "") {
            problems.push({
                field: "url",
                message: "The App URL must not be empty for OAuth2 auth apps.",
            });
        }
        if (values.appId.trim() === "") {
            problems.push({
                field: "appId",
                message: "The App ID must not be empty for OAuth2 auth apps.",
            });
        }
        if (values.appSecret === "" && !context.hasSecret) {
            problems.push({
                field: "appSecret",
                message: "The App Secret must not be empty for OAuth2 auth "
                    + "apps.",
            });
        }
    }

    return problems;
};

export const authAppStatements = (
    values: IAuthAppDialogValues,
    context: IAuthAppContext,
): string[] => {
    const app = {
        name: values.name.trim(),
        vendorId: vendorIdOf(values, context) ?? "",
        vendorName: values.vendorName,
        enabled: values.enabled,
        description: values.description,
        limitToRegisteredUsers: values.limitToRegisteredUsers,
        defaultRole: values.defaultRole,
        appId: values.appId,
        appSecret: values.appSecret,
        url: values.url,
    };

    if (context.existingName !== undefined) {
        return [alterAuthAppSql(context.existingName, app)];
    }

    return [
        createAuthAppSql(app),
        ...(context.linkToService === undefined
            ? [] : [linkAuthAppSql(context.linkToService, app.name, true)]),
    ];
};

// --- users ------------------------------------------------------------

/** The user dialog's fields. */
export interface IUserDialogValues {
    name: string;
    /** Empty keeps a stored password. */
    password: string;
    email: string;
    loginPermitted: boolean;
    /** The roles it holds, by caption. */
    roles: string[];
    options: string;
    appOptions: string;
    vendorUserId: string;
    mappedUserId: string;
}

/** What the user dialog offers besides its fields. */
export interface IUserContext {
    authApp: string;
    authAppVendorId: string;
    /** Every role's caption. */
    allRoles: string[];
    /**
     * The service each role is specific to, by caption; a role missing
     * here, or null, is one of any service.
     */
    roleServices?: Record<string, string | null>;
    /** The user's name, when it is edited. */
    existingName?: string;
    /** The roles it held when the dialog opened. */
    existingRoles: string[];
    /** Whether a password is stored for it. */
    hasPassword: boolean;
}

/**
 * @param user The user to edit; undefined for a new one.
 * @param defaultRole The app's default role, which a new user gets.
 *
 * @returns The dialog's fields.
 */
export const userDefaults = (
    user: IMrsUser | undefined,
    defaultRole: string | undefined,
): IUserDialogValues => {
    const options = { ...(user?.options ?? {}) };
    for (const key of ["email", "vendor_user_id", "mapped_user_id"]) {
        delete options[key];
    }

    return {
        name: user?.name ?? "",
        password: "",
        email: user?.email ?? "",
        loginPermitted: user?.login_permitted ?? true,
        roles: user === undefined
            ? (defaultRole === undefined ? [] : [defaultRole])
            : user.roles.map((role) => { return role.caption; }),
        options: prettyJson(options),
        appOptions: prettyJson(user?.app_options),
        vendorUserId: user?.vendor_user_id ?? "",
        mappedUserId: user?.mapped_user_id ?? "",
    };
};

export const validateUser = (
    values: IUserDialogValues,
    context: IUserContext,
): IDialogProblem[] => {
    const problems: IDialogProblem[] = [];
    if (values.name.trim() === "") {
        problems.push({ field: "name", message: "The user name must not be empty." });
    }
    // Only MRS's own apps keep passwords; the others sign users in
    // elsewhere.
    if (context.authAppVendorId === "30000000-0000-0000-0000-000000000000") {
        if (values.password === "" && !context.hasPassword) {
            problems.push({
                field: "password",
                message: "The authentication string is required for this app.",
            });
        } else if (values.password !== "") {
            problems.push(...passwordProblems(values.password, "password"));
        }
    }
    problems.push(...jsonProblems({
        options: values.options, appOptions: values.appOptions,
    }));

    return problems;
};

export const userStatements = (
    values: IUserDialogValues,
    context: IUserContext,
): string[] => {
    const user = {
        name: values.name.trim(),
        authApp: context.authApp,
        password: values.password,
        email: values.email.trim(),
        vendorUserId: values.vendorUserId.trim(),
        mappedUserId: values.mappedUserId.trim(),
        loginPermitted: values.loginPermitted,
        options: json(values.options),
        appOptions: json(values.appOptions),
    };
    const granted = values.roles.filter((role) => {
        return !context.existingRoles.includes(role);
    });
    const revoked = context.existingRoles.filter((role) => {
        return !values.roles.includes(role);
    });

    return [
        context.existingName === undefined
            ? createUserSql(user) : alterUserSql(context.existingName, user),
        ...revoked.map((role) => {
            return userRoleSql(role, user.name, context.authApp, false,
                context.roleServices?.[role] ?? null);
        }),
        ...granted.map((role) => {
            return userRoleSql(role, user.name, context.authApp, true,
                context.roleServices?.[role] ?? null);
        }),
    ];
};

// --- SDK export -------------------------------------------------------

/** The SDK export dialog's fields. */
export interface ISdkExportValues {
    directory: string;
    serviceUrl: string;
    sdkLanguage: string;
    addAppBaseClass: string;
    header: string;
}

/** The SDK languages the mrs plugin generates. */
export const SDK_LANGUAGES = ["TypeScript", "Python"];

/** The app base classes a TypeScript SDK can bring along. */
export const APP_BASE_CLASSES: Record<string, string[]> = {
    TypeScript: ["MrsBaseAppPreact.ts"],
    Python: [],
};

export const validateSdkExport = (
    values: ISdkExportValues,
): IDialogProblem[] => {
    return [
        ...(values.directory.trim() === ""
            ? [{ field: "directory", message: "Please specify a directory." }]
            : []),
        ...(values.serviceUrl.trim() === ""
            ? [{ field: "serviceUrl", message: "The REST service URL must not be empty." }]
            : []),
    ];
};

/** A dialog's validation, by kind; the object dialog's needs no context. */
export const validateDialog = (
    kind: MrsDialogKind,
    values: unknown,
    context: unknown,
): IDialogProblem[] => {
    switch (kind) {
        case "configure": {
            return validateConfigure(values as IConfigureValues,
                context as IConfigureContext);
        }

        case "service": {
            return validateService(values as IServiceDialogValues);
        }

        case "schema": {
            return validateSchema(values as ISchemaDialogValues);
        }

        case "object": {
            return validateObject(values as IObjectDialogValues);
        }

        case "contentSet": {
            return validateContentSet(values as IContentSetDialogValues,
                context as IContentSetContext);
        }

        case "authApp": {
            return validateAuthApp(values as IAuthAppDialogValues,
                context as IAuthAppContext);
        }

        case "user": {
            return validateUser(values as IUserDialogValues,
                context as IUserContext);
        }

        default: {
            return validateSdkExport(values as ISdkExportValues);
        }
    }
};
