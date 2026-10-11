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
    type IToolResult,
} from "../mcp/protocol.js";
import { AsyncLocalStorage } from "node:async_hooks";

import type { IMariaDbApi, IStatementResult } from "../mcp/types.js";
import { callArguments } from "../connections/connectionActivity.js";
import { counted, errorText } from "../text.js";
import {
    describeRestSql,
    describeStatement,
    failedWords,
    type IMrsAction,
    type IMrsStatement,
    type IMrsWords,
    words,
} from "./mrsActions.js";
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

/**
 * @param statements The statements sent.
 * @param results What came back, one per statement that ran.
 * @param elapsedMs How long the call took, where the server did not say.
 *
 * @returns Each statement as the row under its action shows it.
 */
const statementsOf = (
    statements: string[],
    results: IStatementResult[],
    elapsedMs: number,
): IMrsStatement[] => {
    return statements.map((statement, index): IMrsStatement => {
        const result = results[index];
        const sql = statement.replace(/\s+/gu, " ").trim();
        // What it does, so the statement beside it can be learned from.
        const caption = describeStatement(sql)?.done ?? "Ran a REST statement";
        if (result === undefined) {
            return { sql, message: `${caption} - not run, after the error` };
        }
        const took = result.execution_time === undefined
            ? (statements.length === 1 ? elapsedMs : undefined)
            : Math.round(result.execution_time * 1000);
        const timed = took === undefined ? {} : { elapsedMs: took };

        return result.error === undefined
            ? { sql, message: caption, ...timed }
            : { sql, message: result.error, error: result.error, ...timed };
    });
};

/** What `listObjects` merges: every list statement of REST objects. */
const OBJECT_LISTS = ["VIEWS", "PROCEDURES", "FUNCTIONS", "SCRIPTS"] as const;

/**
 * The action the calls being made belong to: their statements. Shared by
 * {@link MrsApi} and {@link MrsToolsApi}, so a tool called inside a group
 * is a step of the group's action rather than an action of its own.
 */
const currentAction = new AsyncLocalStorage<IMrsStatement[]>();

/** REST SQL against one server, as typed calls. */
export class MrsApi {
    /**
     * @param api The database API; asked for on each call, since the MCP
     *            server can be restarted and hand out a new one.
     * @param report Where what is done is reported, for the Actions tab:
     *               each call, or each {@link group} of calls, once as it
     *               starts and once as it ended.
     * @param verbose Whether every call is logged: the listings and reads
     *                too, not only what changes something.
     */
    public constructor(
        private readonly api: () => Promise<IMariaDbApi>,
        private readonly report?: (action: IMrsAction) => void,
        private readonly verbose: () => boolean = () => { return false; },
    ) { }

    /** Makes the actions' ids unique. */
    #actions = 0;

    /**
     * Makes the calls of one piece of work one action: listing a tree row
     * reads several kinds of objects, and is still one thing the user did.
     * Groups inside a group are part of it.
     *
     * @param connectionId The connection the work is done on.
     * @param said What it is called while it runs and once done.
     * @param work The work.
     * @param detail What it came to, from its result.
     * @param onlyFailures Whether it is reported only where it failed or
     *                     every call is logged: work the user did not ask
     *                     for as such, such as browsing.
     *
     * @returns What the work returned.
     */
    public async group<T>(
        connectionId: string,
        said: IMrsWords,
        work: () => Promise<T>,
        detail?: (value: T) => string | undefined,
        onlyFailures = false,
    ): Promise<T> {
        if (currentAction.getStore() !== undefined) {
            return await work();
        }

        return await this.#act(connectionId, said, work, detail,
            onlyFailures);
    }

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
        const run = async (): Promise<IStatementResult[]> => {
            const startedMs = Date.now();
            const results = all.length === 1
                ? [await api.executeSql(scope.connectionId, all[0])]
                : await api.executeScript(scope.connectionId,
                    all.join("\n"), true);
            currentAction.getStore()?.push(...statementsOf(all, results,
                Date.now() - startedMs));
            const failed = results.find((result) => {
                return result.error !== undefined;
            });
            if (failed !== undefined) {
                throw new MrsError(failed.error!);
            }

            return results;
        };

        // A call on its own is an action of its own; inside a group, the
        // group is the action. A read on its own - a dialog filling in its
        // choices - is not something the user did, and is reported only
        // where it failed, or every call is logged.
        const said = describeRestSql(all);
        const results = currentAction.getStore() !== undefined
            ? await run()
            : await this.#act(scope.connectionId, said, run, undefined,
                said.done.startsWith("Read "));

        return results.slice(use.length);
    }

    /**
     * Does one action, reporting it as it starts and as it ended: what is
     * being done, then what was done or why it failed, with the REST SQL
     * it ran.
     *
     * @param connectionId The connection it is done on.
     * @param said What it is called.
     * @param work The work.
     * @param detail What it came to, from its result.
     * @param onlyFailures Whether it is reported only where it failed or
     *                     every call is logged.
     *
     * @returns What the work returned.
     */
    async #act<T>(
        connectionId: string,
        said: IMrsWords,
        work: () => Promise<T>,
        detail: ((value: T) => string | undefined) | undefined,
        onlyFailures: boolean,
    ): Promise<T> {
        const shown = !onlyFailures || this.verbose();
        this.#actions += 1;
        const id = `mrs-sql-${String(this.#actions)}`;
        const when = new Date();
        const statements: IMrsStatement[] = [];
        const ended = (): Pick<IMrsAction, "elapsedMs" | "statements"> => {
            return {
                elapsedMs: Date.now() - when.getTime(),
                ...(statements.length > 0 ? { statements } : {}),
            };
        };
        if (shown) {
            this.report?.({ id, connectionId, message: said.doing,
                pending: true, when, elapsedMs: 0 });
        }

        try {
            const value = await currentAction.run(statements, work);
            if (shown) {
                const said2 = detail?.(value);
                this.report?.({
                    id, connectionId, message: said.done, when, ...ended(),
                    ...(said2 === undefined ? {} : { detail: said2 }),
                });
            }

            return value;
        } catch (error) {
            this.report?.({ id, connectionId, message: failedWords(said),
                when, error: errorText(error), ...ended() });
            throw error;
        }
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

/**
 * How long a content set upload may take: as good as forever, the longest
 * a timer waits (about 24 days). A large folder takes what it takes, and
 * giving up would not stop the server, which finishes the upload anyway -
 * the extension would only report a failure and skip the set's settings.
 */
export const MRS_UPLOAD_TIMEOUT_MS = 2 ** 31 - 1;

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
    /**
     * @param caller What calls the server's tools.
     * @param report Where what each call did is reported, for the Actions
     *               tab.
     */
    public constructor(
        private readonly caller: IToolCaller,
        private readonly report?: (action: IMrsAction) => void,
    ) { }

    /** Makes the calls' ids unique. */
    #calls = 0;

    public async getSdkOptions(directory: string): Promise<IMrsSdkOptions> {
        return await this.#tool("mrs.get_sdk_options", undefined,
            { directory },
            (name, result) => {
                return decodeList<IMrsSdkOptions>(name, result)[0] ?? {};
            },
            words(`Reading the REST Client SDK options in ${directory}`,
                `Read the REST Client SDK options in ${directory}`));
    }

    public async dumpSdkServiceFiles(
        connectionId: string,
        directory: string,
        options: Record<string, unknown>,
    ): Promise<void> {
        await this.#tool("mrs.dump_sdk_service_files", connectionId,
            { directory, options }, decodeVoid,
            words(`Dumping the REST Client SDK to ${directory}`,
                `Dumped the REST Client SDK to ${directory}`),
            undefined, MRS_TOOL_TIMEOUT_MS);
    }

    public async dumpService(
        connectionId: string,
        servicePath: string,
        filePath: string,
        endpoints: string,
    ): Promise<void> {
        await this.#tool("mrs.dump_service", connectionId, {
            service_path: servicePath,
            file_path: filePath,
            endpoints,
            overwrite: true,
        }, decodeVoid,
        words(`Dumping REST Service '${servicePath}' to ${filePath}`,
            `Dumped REST Service '${servicePath}' to ${filePath}`),
        undefined, MRS_TOOL_TIMEOUT_MS);
    }

    public async loadService(
        connectionId: string,
        filePath: string,
        asPath?: string,
    ): Promise<void> {
        await this.#tool("mrs.load_service", connectionId, {
            file_path: filePath,
            ...(asPath === undefined ? {} : { as_path: asPath }),
        }, decodeVoid,
        words((asPath === undefined ? "Loading a REST Service"
            : `Loading REST Service '${asPath}'`) + ` from ${filePath}`,
        (asPath === undefined ? "Loaded a REST Service"
            : `Loaded REST Service '${asPath}'`) + ` from ${filePath}`),
        undefined, MRS_TOOL_TIMEOUT_MS);
    }

    public async dumpServiceProject(
        connectionId: string,
        options: IServiceProjectOptions,
    ): Promise<void> {
        await this.#tool("mrs.dump_service_project", connectionId, {
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
        }, decodeVoid,
        words(`Dumping REST Service '${options.servicePath}' as a REST `
            + `Project to ${options.destination}`,
        `Dumped REST Service '${options.servicePath}' as a REST Project to `
            + options.destination),
        undefined, MRS_TOOL_TIMEOUT_MS);
    }

    public async loadServiceProject(
        connectionId: string,
        source: string,
    ): Promise<void> {
        await this.#tool("mrs.load_service_project", connectionId, { source },
            decodeVoid, words(`Loading the REST Project ${source}`,
                `Loaded the REST Project ${source}`),
            undefined, MRS_TOOL_TIMEOUT_MS);
    }

    public async loadContentSet(
        connectionId: string,
        directory: string,
        contentSetPath: string,
        servicePath: string,
        ignoreList: string,
        loadScripts: boolean | undefined,
    ): Promise<IContentSetUpload> {
        return await this.#tool("mrs.load_content_set", connectionId, {
            directory,
            content_set_path: contentSetPath,
            service_path: servicePath,
            ignore_list: ignoreList,
            ...(loadScripts === undefined ? {} : { load_scripts: loadScripts }),
            replace: true,
        }, decodeObject<IContentSetUpload>,
        words(`Loading REST Content Set '${contentSetPath}' from ${directory}`,
            `Loaded REST Content Set '${contentSetPath}' from ${directory}`),
        (upload) => { return counted(upload.files.length, "file"); },
        MRS_UPLOAD_TIMEOUT_MS);
    }

    public async folderScriptLanguage(
        path: string,
        ignoreList: string,
    ): Promise<string | undefined> {
        return await this.#tool("mrs.get_folder_mrs_script_language",
            undefined, { path, ignore_list: ignoreList },
            (name, result) => {
                const language = decodeList<string | null>(name, result)[0];

                return typeof language === "string" && language !== ""
                    && language !== "null" ? language : undefined;
            },
            words(`Looking for MRS scripts in ${path}`,
                `Looked for MRS scripts in ${path}`),
            (language) => { return language ?? "none"; });
    }

    public async folderScriptDefinitions(
        path: string,
        ignoreList: string,
    ): Promise<IMrsScriptDefinitions> {
        return await this.#tool("mrs.get_folder_mrs_script_definitions",
            undefined, { path, ignore_list: ignoreList },
            decodeObject<IMrsScriptDefinitions>,
            words(`Reading the MRS scripts in ${path}`,
                `Read the MRS scripts in ${path}`), undefined,
            MRS_TOOL_TIMEOUT_MS);
    }

    public async version(): Promise<string> {
        return await this.#tool("mrs.version", undefined, {}, decodeScalar,
            words("Reading the MRS plugin version",
                "Read the MRS plugin version"), (version) => { return version; });
    }

    /**
     * Calls a tool, timing it and reporting what it did.
     *
     * @param name The tool.
     * @param connectionId The connection it works on, if it takes one.
     * @param args Its other arguments.
     * @param decode Reads its answer.
     * @param said What it is called while it runs and once done.
     * @param detail What it came to, from the answer, where that says more.
     * @param timeout How long it may take, where longer than the default.
     *
     * @returns The answer, decoded.
     */
    async #tool<T>(
        name: string,
        connectionId: string | undefined,
        args: Record<string, unknown>,
        decode: (name: string, result: IToolResult) => T,
        said: IMrsWords,
        detail?: (value: T) => string,
        timeout?: number,
    ): Promise<T> {
        const when = new Date();
        const call = (): Promise<T> => {
            return this.caller.callTool(name, {
                ...(connectionId === undefined
                    ? {} : { connection_id: connectionId }),
                ...args,
            }, timeout).then((result) => { return decode(name, result); });
        };

        // Inside a group the call is one of its steps: a row under the
        // group's action, beside the REST SQL around it.
        const group = currentAction.getStore();
        if (group !== undefined) {
            const sql = `${name}(${callArguments(args)})`;
            try {
                const value = await call();
                const came = detail?.(value);
                group.push({
                    sql, elapsedMs: Date.now() - when.getTime(),
                    message: came === undefined
                        ? said.done : `${said.done}: ${came}`,
                });

                return value;
            } catch (error) {
                const text = errorText(error);
                group.push({ sql, message: text, error: text,
                    elapsedMs: Date.now() - when.getTime() });
                throw error;
            }
        }

        this.#calls += 1;
        const action = {
            id: `mrs-tool-${String(this.#calls)}`,
            ...(connectionId === undefined ? {} : { connectionId }),
            when,
        };
        this.report?.({ ...action, message: said.doing, pending: true,
            elapsedMs: 0 });
        try {
            const value = await call();
            this.report?.({
                ...action, message: said.done,
                elapsedMs: Date.now() - when.getTime(),
                ...(detail === undefined ? {} : { detail: detail(value) }),
            });

            return value;
        } catch (error) {
            this.report?.({
                ...action, message: failedWords(said), error: errorText(error),
                elapsedMs: Date.now() - when.getTime(),
            });
            throw error;
        }
    }
}
