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
    ENABLED_STATE,
    MARIADB_VENDOR_ID,
    MRS_VENDOR_ID,
    type EnabledState,
    type JsonObject,
} from "./mrsTypes.js";

/**
 * The REST SQL the extension sends: every statement is built here, so the
 * quoting is decided once. The shell's `mrs` module runs them; the
 * dialogs' SQL preview shows the same text. Free of `vscode`.
 *
 * Text is single quoted, which reads the same with and without
 * ANSI_QUOTES; a backslash is doubled, which is what a session without
 * NO_BACKSLASH_ESCAPES - the shell's default - reads back as one.
 */

/**
 * @param text Any text.
 *
 * @returns It as a REST SQL string literal.
 */
export const quoteText = (text: string): string => {
    return `'${text.replaceAll("\\", "\\\\").replaceAll("'", "''")}'`;
};

/**
 * @param name A database name.
 *
 * @returns It back-tick quoted.
 */
export const quoteIdentifier = (name: string): string => {
    return `\`${name.replaceAll("`", "``")}\``;
};

/**
 * @param schema A database schema.
 * @param name An object in it.
 *
 * @returns `` `schema`.`name` ``.
 */
export const qualifiedName = (schema: string, name: string): string => {
    return `${quoteIdentifier(schema)}.${quoteIdentifier(name)}`;
};

/** A request path the lexer reads as it is: `/a/b_c`. */
const PLAIN_PATH = /^(\/[A-Za-z_][A-Za-z0-9_]*)+$/;

/**
 * @param path A request path, `/sakila`.
 *
 * @returns It as REST SQL: as it is where the lexer reads it so, else back
 *          tick quoted - a path with a dot, a dash or a leading digit.
 */
export const quoteRequestPath = (path: string): string => {
    return PLAIN_PATH.test(path) ? path : quoteIdentifier(path);
};

/**
 * @param fullPath A service's full path, `/svc` or `mike,anna@/svc`, as
 *                 `full_service_path` reports it: developers quoted
 *                 where they need to be.
 *
 * @returns It as REST SQL.
 */
export const quoteServicePath = (fullPath: string): string => {
    const at = fullPath.lastIndexOf("@/");
    if (at < 0) {
        return quoteRequestPath(fullPath);
    }

    return `${fullPath.slice(0, at + 1)}${quoteRequestPath(
        fullPath.slice(at + 1))}`;
};

/**
 * @param value A JSON value.
 *
 * @returns It as REST SQL JSON: the JSON text itself.
 */
export const jsonText = (value: unknown): string => {
    return JSON.stringify(value, undefined, 4);
};

/**
 * @param state Whether the object is served.
 *
 * @returns `ENABLED`, `DISABLED` or `PRIVATE`.
 */
export const enabledKeyword = (state: EnabledState): string => {
    switch (state) {
        case ENABLED_STATE.disabled: {
            return "DISABLED";
        }

        case ENABLED_STATE.private: {
            return "PRIVATE";
        }

        default: {
            return "ENABLED";
        }
    }
};

/** Joins option lines under a statement's first line. */
const statement = (head: string, options: string[]): string => {
    return [head, ...options.map((line) => {
        return `    ${line.replaceAll("\n", "\n    ")}`;
    })].join("\n") + ";";
};

/** `ON SERVICE /svc SCHEMA /schema`, or less where a part is not given. */
export const serviceSchemaSelector = (
    servicePath?: string,
    schemaPath?: string,
): string => {
    const parts = [
        ...(servicePath === undefined
            ? [] : [`SERVICE ${quoteServicePath(servicePath)}`]),
        ...(schemaPath === undefined
            ? [] : [`SCHEMA ${quoteRequestPath(schemaPath)}`]),
    ];

    return parts.length === 0 ? "" : ` ON ${parts.join(" ")}`;
};

// --- metadata ---------------------------------------------------------

/** `CONFIGURE REST METADATA` as the configuration dialog sends it. */
export interface IConfigureMetadataValues {
    enabled: boolean;
    /** Replaces the configuration options; left out, they stay. */
    options?: JsonObject;
    /** Updates the metadata schema to the bundled version. */
    update?: boolean;
    /**
     * The metadata schema to deploy or configure, for a server that is to
     * have more than one: `<prefix>mariadb_rest_service<postfix>`.
     */
    metadataSchema?: string;
}

/**
 * @param values What to configure.
 *
 * @returns The statement.
 */
export const configureMetadataSql = (
    values: IConfigureMetadataValues,
): string => {
    return statement("CONFIGURE REST METADATA", [
        ...(values.metadataSchema === undefined
            ? [] : [`SCHEMA ${quoteIdentifier(values.metadataSchema)}`]),
        values.enabled ? "ENABLED" : "DISABLED",
        ...(values.options === undefined
            ? [] : [`OPTIONS ${jsonText(values.options)}`]),
        ...(values.update === true ? ["UPDATE IF AVAILABLE"] : []),
    ]);
};

// --- services ---------------------------------------------------------

/** A service as the service dialog edits it. */
export interface IServiceValues {
    /** `[devs@]/path`. */
    path: string;
    enabled: boolean;
    published: boolean;
    protocol: "HTTP" | "HTTPS";
    comments: string;
    options: JsonObject | null;
    metadata: JsonObject | null;
    authPath: string;
    authCompletedUrl: string;
    authCompletedUrlValidation: string;
    authCompletedPageContent: string;
}

/** `'text'`, or `DEFAULT` for none. */
const textOrDefault = (text: string): string => {
    return text === "" ? "DEFAULT" : quoteText(text);
};

const serviceOptions = (
    values: IServiceValues,
    addApps: string[],
    removeApps: string[],
): string[] => {
    return [
        values.enabled ? "ENABLED" : "DISABLED",
        values.published ? "PUBLISHED" : "UNPUBLISHED",
        `PROTOCOL ${values.protocol}`,
        `COMMENT ${quoteText(values.comments)}`,
        "AUTHENTICATION",
        `    PATH ${textOrDefault(values.authPath)}`,
        `    REDIRECTION ${textOrDefault(values.authCompletedUrl)}`,
        `    VALIDATION ${textOrDefault(values.authCompletedUrlValidation)}`,
        `    PAGE CONTENT ${textOrDefault(values.authCompletedPageContent)}`,
        `OPTIONS ${jsonText(values.options ?? {})}`,
        ...(values.metadata === null
            ? [] : [`METADATA ${jsonText(values.metadata)}`]),
        ...removeApps.map((name) => {
            return `REMOVE AUTH APP ${quoteText(name)} IF EXISTS`;
        }),
        ...addApps.map((name) => {
            return `ADD AUTH APP ${quoteText(name)}`;
        }),
    ];
};

/**
 * @param values The new service.
 * @param authApps The names of the auth apps to link to it.
 *
 * @returns `CREATE REST SERVICE`.
 */
export const createServiceSql = (
    values: IServiceValues,
    authApps: string[] = [],
): string => {
    return statement(
        `CREATE REST SERVICE ${quoteServicePath(values.path)}`,
        serviceOptions(values, authApps, []));
};

/**
 * @param fullPath The service's path as it is now.
 * @param values Its new settings, its new path among them.
 * @param addApps The auth apps to link.
 * @param removeApps The auth apps to unlink.
 *
 * @returns `ALTER REST SERVICE`.
 */
export const alterServiceSql = (
    fullPath: string,
    values: IServiceValues,
    addApps: string[] = [],
    removeApps: string[] = [],
): string => {
    return statement(
        `ALTER REST SERVICE ${quoteServicePath(fullPath)}`,
        [
            ...(values.path === fullPath
                ? []
                : [`NEW REQUEST PATH ${quoteServicePath(values.path)}`]),
            ...serviceOptions(values, addApps, removeApps),
        ]);
};

/**
 * @param fullPath The service's full path.
 * @param name The auth app to link or unlink.
 * @param link Whether to link it.
 *
 * @returns The `ALTER REST SERVICE` that does it.
 */
export const linkAuthAppSql = (
    fullPath: string,
    name: string,
    link: boolean,
): string => {
    return `ALTER REST SERVICE ${quoteServicePath(fullPath)} `
        + `${link ? "ADD" : "REMOVE"} AUTH APP ${quoteText(name)};`;
};

/** `USE REST SERVICE`, which makes a service the session's current one. */
export const useServiceSql = (fullPath: string): string => {
    return `USE REST SERVICE ${quoteServicePath(fullPath)};`;
};

// --- schemas ----------------------------------------------------------

/** A REST schema as the schema dialog edits it. */
export interface ISchemaValues {
    servicePath: string;
    requestPath: string;
    dbSchema: string;
    enabled: EnabledState;
    requiresAuth: boolean;
    /** Null for the default, 25. */
    itemsPerPage: number | null;
    comments: string;
    options: JsonObject | null;
    metadata: JsonObject | null;
}

const schemaOptions = (values: ISchemaValues): string[] => {
    return [
        enabledKeyword(values.enabled),
        values.requiresAuth
            ? "AUTHENTICATION REQUIRED" : "AUTHENTICATION NOT REQUIRED",
        `ITEMS PER PAGE ${values.itemsPerPage ?? 25}`,
        `COMMENT ${quoteText(values.comments)}`,
        `OPTIONS ${jsonText(values.options ?? {})}`,
        ...(values.metadata === null
            ? [] : [`METADATA ${jsonText(values.metadata)}`]),
    ];
};

/**
 * @param values The new REST schema.
 *
 * @returns `CREATE REST SCHEMA`.
 */
export const createSchemaSql = (values: ISchemaValues): string => {
    return statement(
        `CREATE REST SCHEMA ${quoteRequestPath(values.requestPath)} `
        + `ON SERVICE ${quoteServicePath(values.servicePath)} `
        + `FROM ${quoteIdentifier(values.dbSchema)}`,
        schemaOptions(values));
};

/**
 * @param requestPath The schema's path as it is now.
 * @param values Its new settings; the service is the one it is on.
 *
 * @returns `ALTER REST SCHEMA`.
 */
export const alterSchemaSql = (
    requestPath: string,
    values: ISchemaValues,
): string => {
    return statement(
        `ALTER REST SCHEMA ${quoteRequestPath(requestPath)} `
        + `ON SERVICE ${quoteServicePath(values.servicePath)}`,
        [
            ...(values.requestPath === requestPath
                ? []
                : [`NEW REQUEST PATH ${quoteRequestPath(values.requestPath)}`]),
            `FROM ${quoteIdentifier(values.dbSchema)}`,
            ...schemaOptions(values),
        ]);
};

// --- REST objects -----------------------------------------------------

/** The settings of a REST object, beside its data mappings. */
export interface IObjectSettings {
    enabled: EnabledState;
    requiresAuth: boolean;
    itemsPerPage: number | null;
    comments: string;
    /** `''` with autodetect off for none. */
    mediaType: string;
    autoDetectMediaType: boolean;
    format: "FEED" | "ITEM" | "MEDIA";
    /** `schema.procedure`; `''` for none. */
    authStoredProcedure: string;
    options: JsonObject | null;
    metadata: JsonObject | null;
}

/**
 * @param settings A REST object's settings.
 * @param routine Whether it is a procedure or function, which have no
 *                result format and pages of their own.
 *
 * @returns Its option lines.
 */
export const objectOptions = (
    settings: IObjectSettings,
    routine: boolean,
): string[] => {
    const [schema, name] = splitQualified(settings.authStoredProcedure);

    return [
        enabledKeyword(settings.enabled),
        settings.requiresAuth
            ? "AUTHENTICATION REQUIRED" : "AUTHENTICATION NOT REQUIRED",
        ...(routine ? [] : [`ITEMS PER PAGE ${settings.itemsPerPage ?? 25}`]),
        `COMMENT ${quoteText(settings.comments)}`,
        ...(settings.autoDetectMediaType
            ? ["MEDIA TYPE AUTODETECT"]
            : settings.mediaType === ""
                ? []
                : [`MEDIA TYPE ${quoteText(settings.mediaType)}`]),
        ...(routine ? [] : [`FORMAT ${settings.format}`]),
        ...(name === ""
            ? []
            : [`AUTHENTICATION PROCEDURE ${schema === ""
                ? quoteIdentifier(name)
                : qualifiedName(schema, name)}`]),
        `OPTIONS ${jsonText(settings.options ?? {})}`,
        ...(settings.metadata === null
            ? [] : [`METADATA ${jsonText(settings.metadata)}`]),
    ];
};

/**
 * @param text `schema.name` or `name`.
 *
 * @returns The two parts; the schema `''` where there is none.
 */
export const splitQualified = (text: string): [string, string] => {
    const trimmed = text.trim();
    const dot = trimmed.indexOf(".");

    return dot < 0
        ? ["", trimmed]
        : [trimmed.slice(0, dot), trimmed.slice(dot + 1)];
};

/** Which REST object statement a database object needs. */
export const objectKeyword = (objectType: string): string => {
    switch (objectType) {
        case "PROCEDURE": {
            return "PROCEDURE";
        }

        case "FUNCTION": {
            return "FUNCTION";
        }

        default: {
            return "VIEW";
        }
    }
};

/**
 * @param objectType The REST object's type.
 * @param requestPath Its path.
 * @param servicePath The service it is on.
 * @param schemaPath The REST schema it is in.
 *
 * @returns `DROP REST VIEW|PROCEDURE|FUNCTION`.
 */
export const dropObjectSql = (
    objectType: string,
    requestPath: string,
    servicePath: string,
    schemaPath: string,
): string => {
    return `DROP REST ${objectKeyword(objectType)} `
        + `${quoteRequestPath(requestPath)} FROM SERVICE `
        + `${quoteServicePath(servicePath)} SCHEMA `
        + `${quoteRequestPath(schemaPath)};`;
};

// --- content sets -----------------------------------------------------

/** A content set's settings, as the content set dialog edits them. */
export interface IContentSetValues {
    servicePath: string;
    requestPath: string;
    enabled: EnabledState;
    requiresAuth: boolean;
    comments: string;
    options: JsonObject | null;
}

/**
 * @param requestPath The content set's path as it is now.
 * @param values Its new settings.
 * @param loadScripts Whether to (re)register its MRS scripts.
 *
 * @returns `ALTER REST CONTENT SET`.
 */
export const alterContentSetSql = (
    requestPath: string,
    values: IContentSetValues,
    loadScripts = false,
): string => {
    return statement(
        `ALTER REST CONTENT SET ${quoteRequestPath(requestPath)} `
        + `ON SERVICE ${quoteServicePath(values.servicePath)}`,
        [
            ...(values.requestPath === requestPath
                ? []
                : [`NEW REQUEST PATH ${quoteRequestPath(values.requestPath)}`]),
            enabledKeyword(values.enabled),
            values.requiresAuth
                ? "AUTHENTICATION REQUIRED" : "AUTHENTICATION NOT REQUIRED",
            `COMMENT ${quoteText(values.comments)}`,
            `OPTIONS ${jsonText(values.options ?? {})}`,
            ...(loadScripts ? ["LOAD TYPESCRIPT SCRIPTS"] : []),
        ]);
};

// --- auth apps --------------------------------------------------------

/** An auth app as the auth app dialog edits it. */
export interface IAuthAppValues {
    name: string;
    vendorId: string;
    vendorName: string;
    enabled: boolean;
    description: string;
    limitToRegisteredUsers: boolean;
    /** The role new users get; `''` for none. */
    defaultRole: string;
    appId: string;
    /** `''` keeps a stored secret. */
    appSecret: string;
    url: string;
}

/**
 * @param vendorId The vendor's id.
 * @param vendorName Its name.
 *
 * @returns How REST SQL names it: the `MRS` and `MARIADB` keywords for the
 *          built-in vendors, else its name as text.
 */
export const vendorClause = (vendorId: string, vendorName: string): string => {
    if (vendorId === MRS_VENDOR_ID) {
        return "MRS";
    }

    return vendorId === MARIADB_VENDOR_ID ? "MARIADB" : quoteText(vendorName);
};

const authAppOptions = (values: IAuthAppValues): string[] => {
    return [
        values.enabled ? "ENABLED" : "DISABLED",
        `COMMENT ${quoteText(values.description)}`,
        values.limitToRegisteredUsers
            ? "DO NOT ALLOW NEW USERS TO REGISTER"
            : "ALLOW NEW USERS TO REGISTER",
        ...(values.defaultRole === ""
            ? [] : [`DEFAULT ROLE ${quoteText(values.defaultRole)}`]),
        ...(values.appId === "" ? [] : [`APP ID ${quoteText(values.appId)}`]),
        ...(values.appSecret === ""
            ? [] : [`APP SECRET ${quoteText(values.appSecret)}`]),
        ...(values.url === "" ? [] : [`URL ${quoteText(values.url)}`]),
    ];
};

/**
 * @param values The new auth app.
 *
 * @returns `CREATE REST AUTH APP`.
 */
export const createAuthAppSql = (values: IAuthAppValues): string => {
    return statement(
        `CREATE REST AUTH APP ${quoteText(values.name)} VENDOR `
        + vendorClause(values.vendorId, values.vendorName),
        authAppOptions(values));
};

/**
 * @param name The app's name as it is now.
 * @param values Its new settings. The vendor cannot change.
 *
 * @returns `ALTER REST AUTH APP`.
 */
export const alterAuthAppSql = (
    name: string,
    values: IAuthAppValues,
): string => {
    return statement(
        `ALTER REST AUTH APP ${quoteText(name)}`,
        [
            ...(values.name === name
                ? [] : [`NEW NAME ${quoteText(values.name)}`]),
            ...authAppOptions(values),
        ]);
};

// --- users ------------------------------------------------------------

/** A REST user as the user dialog edits it. */
export interface IUserValues {
    name: string;
    authApp: string;
    /** `''` keeps the stored password. */
    password: string;
    email: string;
    vendorUserId: string;
    mappedUserId: string;
    loginPermitted: boolean;
    options: JsonObject | null;
    appOptions: JsonObject | null;
}

/** `'name'@'app'`. */
export const userRef = (name: string, authApp: string): string => {
    return `${quoteText(name)}@${quoteText(authApp)}`;
};

const userOptions = (values: IUserValues): string[] => {
    // The email and the two ids are columns of their own, which the shell
    // takes from these keys.
    const options: JsonObject = {
        ...(values.options ?? {}),
        email: values.email === "" ? null : values.email,
        vendor_user_id: values.vendorUserId === ""
            ? null : values.vendorUserId,
        mapped_user_id: values.mappedUserId === ""
            ? null : values.mappedUserId,
    };

    return [
        values.loginPermitted ? "ACCOUNT UNLOCK" : "ACCOUNT LOCK",
        ...(values.appOptions === null
            ? [] : [`APP OPTIONS ${jsonText(values.appOptions)}`]),
        `OPTIONS ${jsonText(options)}`,
    ];
};

/**
 * @param values The new user.
 *
 * @returns `CREATE REST USER`.
 */
export const createUserSql = (values: IUserValues): string => {
    return statement(
        `CREATE REST USER ${userRef(values.name, values.authApp)}`
        + (values.password === ""
            ? "" : ` IDENTIFIED BY ${quoteText(values.password)}`),
        userOptions(values));
};

/**
 * @param name The user's name as it is now; REST SQL cannot rename one.
 * @param values Its new settings.
 *
 * @returns `ALTER REST USER`.
 */
export const alterUserSql = (name: string, values: IUserValues): string => {
    return statement(
        `ALTER REST USER ${userRef(name, values.authApp)}`
        + (values.password === ""
            ? "" : ` IDENTIFIED BY ${quoteText(values.password)}`),
        userOptions(values));
};

/**
 * @param role The role's caption.
 * @param user The user's name.
 * @param authApp The user's auth app.
 * @param grant Whether to grant or revoke it.
 * @param servicePath The service the role is specific to; null or left
 *        out for a role of any service, such as `Full Access` - the shell
 *        looks a role up on the current service unless told otherwise.
 *
 * @returns `GRANT REST ROLE` or `REVOKE REST ROLE`.
 */
export const userRoleSql = (
    role: string,
    user: string,
    authApp: string,
    grant: boolean,
    servicePath: string | null = null,
): string => {
    const scope = servicePath === null
        ? "ON ANY SERVICE" : `ON SERVICE ${quoteServicePath(servicePath)}`;

    return grant
        ? `GRANT REST ROLE ${quoteText(role)} ${scope} TO `
        + `${userRef(user, authApp)};`
        : `REVOKE REST ROLE ${quoteText(role)} ${scope} FROM `
        + `${userRef(user, authApp)};`;
};

// --- dropping ---------------------------------------------------------

export const dropServiceSql = (fullPath: string): string => {
    return `DROP REST SERVICE ${quoteServicePath(fullPath)};`;
};

export const dropSchemaSql = (
    requestPath: string,
    servicePath: string,
): string => {
    return `DROP REST SCHEMA ${quoteRequestPath(requestPath)} FROM SERVICE `
        + `${quoteServicePath(servicePath)};`;
};

export const dropContentSetSql = (
    requestPath: string,
    servicePath: string,
): string => {
    return `DROP REST CONTENT SET ${quoteRequestPath(requestPath)} `
        + `FROM SERVICE ${quoteServicePath(servicePath)};`;
};

export const dropContentFileSql = (
    requestPath: string,
    servicePath: string,
    contentSetPath: string,
): string => {
    return `DROP REST CONTENT FILE ${quoteRequestPath(requestPath)} FROM `
        + `SERVICE ${quoteServicePath(servicePath)} CONTENT SET `
        + `${quoteRequestPath(contentSetPath)};`;
};

export const dropAuthAppSql = (name: string): string => {
    return `DROP REST AUTH APP ${quoteText(name)};`;
};

export const dropUserSql = (name: string, authApp: string): string => {
    return `DROP REST USER ${userRef(name, authApp)};`;
};

export const dropDaemonSql = (id: string): string => {
    return `DROP REST DAEMON ${quoteText(id)};`;
};
