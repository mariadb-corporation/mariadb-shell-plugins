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

import type { IToolCaller } from "../mcp/mariaDbApi.js";
import {
    decodeList,
    decodeObject,
    decodeScalar,
    decodeVoid,
} from "../mcp/protocol.js";
import type { IMariaDbApi, IStatementResult } from "../mcp/types.js";
import type {
    IMrsAuthApp,
    IMrsAuthVendor,
    IMrsColumns,
    IMrsContentFile,
    IMrsContentSet,
    IMrsDaemon,
    IMrsObject,
    IMrsRole,
    IMrsSchema,
    IMrsScriptDefinitions,
    IMrsService,
    IMrsStatus,
    IMrsUser,
} from "./mrsTypes.js";
import {
    qualifiedName,
    quoteIdentifier,
    quoteRequestPath,
    quoteServicePath,
    quoteText,
    serviceSchemaSelector,
    userRef,
} from "./restSql.js";

/**
 * The MariaDB REST Service as the extension reaches it.
 *
 * Everything about the REST metadata is REST SQL sent through
 * `db.execute_sql`: the shell's `mrs` module answers `SHOW ... FORMAT=JSON`
 * with one cell of JSON and runs the CREATE / ALTER / DROP statements. What
 * needs the developer's files - the client SDK, dumping and loading, a
 * content set from a folder - goes through the server's `mrs.*` tools.
 */

/**
 * Where a call is made: an open connection, and the REST metadata schema
 * on it when there is more than one - the shell refuses to guess between
 * them, so every call then picks one first.
 */
export interface IMrsScope {
    connectionId: string;
    /** Undefined where the server has one metadata schema, or none. */
    metadataSchema?: string;
}

/** One REST metadata schema of a server. */
export interface IMrsMetadataSchema {
    schema_name: string;
    version: string | null;
    current: boolean;
}

/**
 * Raised where the shell answered a REST SQL statement with an error, or
 * the server has no REST metadata at all.
 */
export class MrsError extends Error {
    public constructor(message: string) {
        super(message);
        this.name = "MrsError";
    }
}

/**
 * @param result A statement's result.
 *
 * @returns The value of its first cell, where it has one.
 */
const firstCell = (result: IStatementResult | undefined): unknown => {
    const row = result?.result_sets?.[0]?.rows[0];

    return row === undefined ? undefined : Object.values(row)[0];
};

/**
 * @param value A JSON cell, as text or already decoded.
 *
 * @returns The value.
 */
const parseCell = <T>(value: unknown): T => {
    if (typeof value !== "string") {
        return value as T;
    }

    return JSON.parse(value) as T;
};

/** What `listObjects` merges: every list statement of REST objects. */
const OBJECT_LISTS = ["VIEWS", "PROCEDURES", "FUNCTIONS", "SCRIPTS"] as const;

/** REST SQL against one server, as typed calls. */
export class MrsApi {
    /**
     * @param api The database API; asked for on each call, since the MCP
     *            server can be restarted and hand out a new one.
     */
    public constructor(private readonly api: () => Promise<IMariaDbApi>) { }

    /**
     * Runs statements on the scope's connection, after choosing its
     * metadata schema where there is a choice. Sent as one script, so
     * nothing else on the connection runs between the choice and them.
     *
     * @param scope Where to run them.
     * @param statements The statements.
     *
     * @returns Their results, the USE left out.
     */
    public async run(
        scope: IMrsScope,
        statements: string[],
    ): Promise<IStatementResult[]> {
        const api = await this.api();
        const use = scope.metadataSchema === undefined
            ? []
            : [`USE REST METADATA SCHEMA ${quoteIdentifier(scope.metadataSchema)};`];
        const all = [...use, ...statements.map((sql) => {
            return sql.trimEnd().endsWith(";") ? sql : `${sql};`;
        })];
        if (all.length === 0) {
            return [];
        }
        const results = all.length === 1
            ? [await api.executeSql(scope.connectionId, all[0])]
            : await api.executeScript(scope.connectionId, all.join("\n"), true);
        const failed = results.find((result) => {
            return result.error !== undefined;
        });
        if (failed !== undefined) {
            throw new MrsError(failed.error!);
        }

        return results.slice(use.length);
    }

    /**
     * Runs one `SHOW ... FORMAT=JSON` and decodes its one cell.
     *
     * @param scope Where to run it.
     * @param sql The statement.
     *
     * @returns The document.
     */
    public async json<T>(scope: IMrsScope, sql: string): Promise<T> {
        const [result] = await this.run(scope, [sql]);

        return parseCell<T>(firstCell(result));
    }

    /**
     * Runs one statement that answers with one cell of text, such as
     * `SHOW CREATE REST SERVICE`.
     *
     * @param scope Where to run it.
     * @param sql The statement.
     *
     * @returns The text.
     */
    public async text(scope: IMrsScope, sql: string): Promise<string> {
        const [result] = await this.run(scope, [sql]);

        return String(firstCell(result) ?? "");
    }

    /**
     * @param connectionId An open connection.
     *
     * @returns The REST metadata schemas the connection's account can see.
     */
    public async metadataSchemas(
        connectionId: string,
    ): Promise<IMrsMetadataSchema[]> {
        return await this.json<IMrsMetadataSchema[]>({ connectionId },
            "SHOW REST METADATA SCHEMAS FORMAT=JSON");
    }

    public async status(scope: IMrsScope): Promise<IMrsStatus> {
        return await this.json<IMrsStatus>(scope,
            "SHOW REST METADATA STATUS FORMAT=JSON");
    }

    public async services(scope: IMrsScope): Promise<IMrsService[]> {
        return await this.json<IMrsService[]>(scope,
            "SHOW REST SERVICES FORMAT=JSON");
    }

    public async servicesOfDaemon(
        scope: IMrsScope,
        daemonId: string,
    ): Promise<IMrsService[]> {
        return await this.json<IMrsService[]>(scope,
            `SHOW REST SERVICES FOR DAEMON ${quoteText(daemonId)} FORMAT=JSON`);
    }

    public async schemas(
        scope: IMrsScope,
        servicePath: string,
    ): Promise<IMrsSchema[]> {
        return await this.json<IMrsSchema[]>(scope,
            `SHOW REST SCHEMAS ON SERVICE ${quoteServicePath(servicePath)} `
            + "FORMAT=JSON");
    }

    /**
     * @param scope Where to look.
     * @param servicePath The service.
     * @param schemaPath The REST schema.
     *
     * @returns Its views, procedures, functions and MRS scripts, by path.
     */
    public async objects(
        scope: IMrsScope,
        servicePath: string,
        schemaPath: string,
    ): Promise<IMrsObject[]> {
        const selector = serviceSchemaSelector(servicePath, schemaPath);
        const results = await this.run(scope, OBJECT_LISTS.map((list) => {
            return `SHOW REST ${list}${selector} FORMAT=JSON`;
        }));

        return results.flatMap((result) => {
            return parseCell<IMrsObject[]>(firstCell(result)) ?? [];
        }).sort((a, b) => {
            return a.request_path.localeCompare(b.request_path);
        });
    }

    /**
     * @param scope Where to look.
     * @param objectType The object's type.
     * @param servicePath Its service.
     * @param schemaPath Its REST schema.
     * @param requestPath Its path.
     *
     * @returns The object with its data mappings.
     */
    public async object(
        scope: IMrsScope,
        objectType: string,
        servicePath: string,
        schemaPath: string,
        requestPath: string,
    ): Promise<IMrsObject> {
        const keyword = objectType === "PROCEDURE" || objectType === "FUNCTION"
            ? objectType : "VIEW";

        return await this.json<IMrsObject>(scope,
            `SHOW CREATE REST ${keyword} ${quoteRequestPath(requestPath)}`
            + `${serviceSchemaSelector(servicePath, schemaPath)} FORMAT=JSON`);
    }

    /**
     * @param scope Where to look.
     * @param kind TABLE, VIEW, PROCEDURE or FUNCTION.
     * @param schema The database schema.
     * @param name The database object.
     *
     * @returns Its columns and references, or its parameters.
     */
    public async columns(
        scope: IMrsScope,
        kind: string,
        schema: string,
        name: string,
    ): Promise<IMrsColumns> {
        return await this.json<IMrsColumns>(scope,
            `SHOW REST COLUMNS FROM ${kind} ${qualifiedName(schema, name)} `
            + "FORMAT=JSON");
    }

    public async contentSets(
        scope: IMrsScope,
        servicePath: string,
    ): Promise<IMrsContentSet[]> {
        return await this.json<IMrsContentSet[]>(scope,
            "SHOW REST CONTENT SETS ON SERVICE "
            + `${quoteServicePath(servicePath)} FORMAT=JSON`);
    }

    public async contentFiles(
        scope: IMrsScope,
        servicePath: string,
        contentSetPath: string,
    ): Promise<IMrsContentFile[]> {
        return await this.json<IMrsContentFile[]>(scope,
            `SHOW REST CONTENT FILES ON SERVICE ${quoteServicePath(servicePath)}`
            + ` CONTENT SET ${quoteRequestPath(contentSetPath)} FORMAT=JSON`);
    }

    /**
     * @param scope Where to look.
     * @param servicePath A service, to list the apps linked to it; left
     *                    out, every app.
     *
     * @returns The auth apps.
     */
    public async authApps(
        scope: IMrsScope,
        servicePath?: string,
    ): Promise<IMrsAuthApp[]> {
        // With no service named the shell lists the current service's apps
        // where there is one, so "all of them" has to say so.
        return await this.json<IMrsAuthApp[]>(scope, servicePath === undefined
            ? "SHOW REST AUTH APPS ON ANY SERVICE FORMAT=JSON"
            : `SHOW REST AUTH APPS ON SERVICE ${quoteServicePath(servicePath)} `
            + "FORMAT=JSON");
    }

    public async authVendors(scope: IMrsScope): Promise<IMrsAuthVendor[]> {
        return await this.json<IMrsAuthVendor[]>(scope,
            "SHOW REST AUTH VENDORS FORMAT=JSON");
    }

    public async users(
        scope: IMrsScope,
        authApp: string,
    ): Promise<IMrsUser[]> {
        return await this.json<IMrsUser[]>(scope,
            `SHOW REST USERS FOR AUTH APP ${quoteText(authApp)} FORMAT=JSON`);
    }

    public async user(
        scope: IMrsScope,
        name: string,
        authApp: string,
    ): Promise<IMrsUser> {
        return await this.json<IMrsUser>(scope,
            `SHOW CREATE REST USER ${userRef(name, authApp)} FORMAT=JSON`);
    }

    public async roles(scope: IMrsScope): Promise<IMrsRole[]> {
        return await this.json<IMrsRole[]>(scope,
            "SHOW REST ROLES ON ANY SERVICE FORMAT=JSON");
    }

    public async daemons(scope: IMrsScope): Promise<IMrsDaemon[]> {
        return await this.json<IMrsDaemon[]>(scope,
            "SHOW REST DAEMONS FORMAT=JSON");
    }
}

/** How long an SDK or dump call may take: they write many files. */
export const MRS_TOOL_TIMEOUT_MS = 10 * 60 * 1000;

/** What `mrs.dump_service_project` writes. */
export interface IServiceProjectOptions {
    destination: string;
    servicePath: string;
    name: string;
    description: string;
    publisher: string;
    version: string;
    iconPath?: string;
    zip: boolean;
}

/** The SDK options a dump writes and `mrs.get_sdk_options` reads back. */
export interface IMrsSdkOptions {
    serviceId?: string;
    serviceUrl?: string;
    sdkLanguage?: string;
    addAppBaseClass?: string;
    header?: string;
    dbConnectionUri?: string;
}

/** The result of `mrs.load_content_set`. */
export interface IContentSetUpload {
    files: string[];
    message?: string;
}

/**
 * The server's `mrs.*` tools: the mrs plugin's functions that work with
 * the developer's files. Those that use a REST service take an open
 * connection; the metadata schema on it is chosen with {@link MrsApi.run}
 * first where there is a choice.
 */
export class MrsToolsApi {
    public constructor(private readonly caller: IToolCaller) { }

    public async getSdkOptions(directory: string): Promise<IMrsSdkOptions> {
        const name = "mrs.get_sdk_options";
        const result = await this.caller.callTool(name, { directory });

        return (decodeList<IMrsSdkOptions>(name, result)[0] ?? {});
    }

    public async dumpSdkServiceFiles(
        connectionId: string,
        directory: string,
        options: Record<string, unknown>,
    ): Promise<void> {
        const name = "mrs.dump_sdk_service_files";
        decodeVoid(name, await this.caller.callTool(name, {
            connection_id: connectionId, directory, options,
        }, MRS_TOOL_TIMEOUT_MS));
    }

    public async dumpService(
        connectionId: string,
        servicePath: string,
        filePath: string,
        endpoints: string,
    ): Promise<void> {
        const name = "mrs.dump_service";
        decodeVoid(name, await this.caller.callTool(name, {
            connection_id: connectionId,
            service_path: servicePath,
            file_path: filePath,
            endpoints,
            overwrite: true,
        }, MRS_TOOL_TIMEOUT_MS));
    }

    public async loadService(
        connectionId: string,
        filePath: string,
        asPath?: string,
    ): Promise<void> {
        const name = "mrs.load_service";
        decodeVoid(name, await this.caller.callTool(name, {
            connection_id: connectionId,
            file_path: filePath,
            ...(asPath === undefined ? {} : { as_path: asPath }),
        }, MRS_TOOL_TIMEOUT_MS));
    }

    public async dumpServiceProject(
        connectionId: string,
        options: IServiceProjectOptions,
    ): Promise<void> {
        const name = "mrs.dump_service_project";
        decodeVoid(name, await this.caller.callTool(name, {
            connection_id: connectionId,
            destination: options.destination,
            services: [{
                name: options.servicePath,
                include_database_endpoints: true,
                include_static_endpoints: true,
                include_dynamic_endpoints: true,
            }],
            settings: {
                name: options.name,
                description: options.description,
                publisher: options.publisher,
                version: options.version,
                ...(options.iconPath === undefined
                    ? {} : { icon_path: options.iconPath }),
            },
            overwrite: true,
            zip: options.zip,
        }, MRS_TOOL_TIMEOUT_MS));
    }

    public async loadServiceProject(
        connectionId: string,
        source: string,
    ): Promise<void> {
        const name = "mrs.load_service_project";
        decodeVoid(name, await this.caller.callTool(name, {
            connection_id: connectionId, source,
        }, MRS_TOOL_TIMEOUT_MS));
    }

    public async loadContentSet(
        connectionId: string,
        directory: string,
        contentSetPath: string,
        servicePath: string,
        ignoreList: string,
        loadScripts: boolean | undefined,
    ): Promise<IContentSetUpload> {
        const name = "mrs.load_content_set";

        return decodeObject<IContentSetUpload>(name, await this.caller.callTool(
            name, {
                connection_id: connectionId,
                directory,
                content_set_path: contentSetPath,
                service_path: servicePath,
                ignore_list: ignoreList,
                ...(loadScripts === undefined
                    ? {} : { load_scripts: loadScripts }),
                replace: true,
            }, MRS_TOOL_TIMEOUT_MS));
    }

    public async folderScriptLanguage(
        path: string,
        ignoreList: string,
    ): Promise<string | undefined> {
        const name = "mrs.get_folder_mrs_script_language";
        const result = await this.caller.callTool(name, {
            path, ignore_list: ignoreList,
        });
        const texts = decodeList<string | null>(name, result);
        const language = texts[0];

        return typeof language === "string" && language !== ""
            && language !== "null" ? language : undefined;
    }

    public async folderScriptDefinitions(
        path: string,
        ignoreList: string,
    ): Promise<IMrsScriptDefinitions> {
        const name = "mrs.get_folder_mrs_script_definitions";

        return decodeObject<IMrsScriptDefinitions>(name,
            await this.caller.callTool(name, {
                path, ignore_list: ignoreList,
            }, MRS_TOOL_TIMEOUT_MS));
    }

    public async version(): Promise<string> {
        const name = "mrs.version";

        return decodeScalar(name, await this.caller.callTool(name, {}));
    }
}
