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

import type { IConnectionSettings } from "../connections/connectionManager.js";
import { ObjectNotFoundError } from "../mcp/mariaDbApi.js";
import type { IToolResult } from "../mcp/protocol.js";
import type { IMcpConnection, IMcpConnector } from "../mcp/session.js";
import type {
    ConnectionColor,
    ConnectionKind,
    IConnectionDetails,
    IMariaDbApi,
    IObjectDetails,
    IObjectInfo,
    ISchemaInfo,
    IPageRequest,
    IStatementResult,
    ObjectType,
} from "../mcp/types.js";
import type {
    IMrsAuthApp,
    IMrsContentFile,
    IMrsContentSet,
    IMrsDaemon,
    IMrsObject,
    IMrsSchema,
    IMrsService,
    IMrsStatus,
    IMrsUser,
} from "../mrs/mrsTypes.js";
import type { InstallCommand, ProcessRunner } from "../shell/installer.js";
import type { ShellEnvironment } from "../shell/locator.js";
import type { McpServerCommand } from "../shell/mcpServer.js";

export interface FakeEnvironmentOptions {
    platform?: NodeJS.Platform;
    homeDir?: string;
    env?: Record<string, string | undefined>;
    /** Maps a binary path to the `--version` output it answers with. */
    versions?: Record<string, string>;
    /** Paths that exist on the fake file system. */
    files?: string[];
    /** Maps a directory to the sub directory names it holds. */
    directories?: Record<string, string[]>;
}

export interface FakeEnvironment extends ShellEnvironment {
    /** The binaries that were probed, in order. */
    probed: string[];
}

/**
 * Builds a shell environment that answers from plain in-memory tables, so
 * lookup rules can be exercised for any platform from any host.
 *
 * @param options What the fake should report.
 *
 * @returns The fake environment.
 */
export const createFakeEnvironment = (
    options: FakeEnvironmentOptions = {},
): FakeEnvironment => {
    const versions = options.versions ?? {};
    const files = new Set(options.files ?? []);
    const directories = options.directories ?? {};
    const probed: string[] = [];

    return {
        platform: options.platform ?? "darwin",
        homeDir: options.homeDir ?? "/Users/mzinner",
        env: options.env ?? {},
        probed,

        probeVersion: (binaryPath: string) => {
            probed.push(binaryPath);

            return Promise.resolve(versions[binaryPath]);
        },

        pathExists: (target: string) => {
            return Promise.resolve(files.has(target));
        },

        listDirectories: (target: string) => {
            return Promise.resolve(directories[target] ?? []);
        },
    };
};

export interface FakeRunner extends ProcessRunner {
    /** The commands that were run, in order. */
    calls: InstallCommand[];
}

/**
 * Builds a process runner that emits a canned transcript and exit code.
 *
 * @param output The lines the command should print.
 * @param exitCode The exit code it should end with.
 *
 * @returns The fake runner.
 */
export const createFakeRunner = (
    output: string[] = [],
    exitCode = 0,
): FakeRunner => {
    const calls: InstallCommand[] = [];

    return {
        calls,
        run: (command, onOutput) => {
            calls.push(command);
            for (const line of output) {
                onOutput(line);
            }

            return Promise.resolve(exitCode);
        },
    };
};

export interface RecordingLog {
    (message: string): void;
    lines: string[];
}

/**
 * Builds a log function that keeps every line it was given.
 *
 * @returns The recording log.
 */
export const createRecordingLog = (): RecordingLog => {
    const lines: string[] = [];
    const log = ((message: string) => {
        lines.push(message);
    }) as RecordingLog;
    log.lines = lines;

    return log;
};

/** What a fake database API should answer with. */
export interface FakeApiOptions {
    connections?: string[];
    /** The connections of the extension's own list, if it has any. */
    guiConnections?: string[];
    /** Connection URI -> the folder it is filed in; `/` when not named. */
    paths?: Record<string, string>;
    /** Connection URI -> its caption and color, where it has them. */
    looks?: Record<string, { caption?: string; color?: ConnectionColor }>;
    /** Refuse `kind: "all"`, as a server that predates it does. */
    noAllKind?: boolean;
    /** Makes `testConnection` reject with this message instead of passing. */
    testFailure?: string;
    /** Connection URI -> the UUID handing it out produces. */
    connectionIds?: Record<string, string>;
    schemas?: ISchemaInfo[];
    /** `${schema}/${objectType}` -> the objects in it. */
    objects?: Record<string, IObjectInfo[]>;
    /** `${schema}.${table}` -> its description. */
    details?: Record<string, IObjectDetails>;
    /** Script text -> the results running it produces. */
    results?: Record<string, IStatementResult[]>;
    /** Runs for any script that `results` does not name. */
    defaultResults?: IStatementResult[];
    /**
     * The rows `executeSql` pages through, as a server would: it answers
     * `limit` of them from `offset`, with `has_more_pages`.
     */
    pagedRows?: Array<Record<string, unknown>>;
}

export interface FakeApi extends IMariaDbApi {
    /** The scripts that were run, in order. */
    scripts: string[];
    /** The connections that were closed, in order. */
    closed: string[];
    /** The connections that were added, in order. */
    added: Array<{
        uri: string;
        password: string;
        kind?: ConnectionKind;
        path?: string;
        caption?: string;
        color?: ConnectionColor | "";
    }>;
    /** The connections that were deleted, in order. */
    deleted: Array<{ uri: string; kind?: ConnectionKind }>;
    /** The connections that were tested, in order. */
    tested: Array<{ uri: string; password?: string }>;
    /** The updates that were applied, in order. */
    updated: Array<{
        uri: string;
        newUri?: string;
        kind?: ConnectionKind;
        newKind?: ConnectionKind;
        password?: string;
        newPath?: string;
        newCaption?: string;
        newColor?: ConnectionColor | "";
    }>;
    /** What the last script was asked to do about a failing statement. */
    stopOnError?: boolean;
    /** The limit the last script was run with. */
    limit?: number;
    /** Every `executeSql` call, in order. */
    statements: Array<{ sql: string; page?: IPageRequest }>;
    /** Every `getObjectDetails` call, as `schema.name:type`. */
    lookups: string[];
}

/**
 * Builds a database API that answers from in-memory tables.
 *
 * @param options What the fake should report.
 *
 * @returns The fake API.
 */
export const createFakeApi = (options: FakeApiOptions = {}): FakeApi => {
    const scripts: string[] = [];
    const closed: string[] = [];
    /** How often each connection has been opened, for its UUIDs. */
    const opens = new Map<string, number>();
    const added: FakeApi["added"] = [];
    const deleted: FakeApi["deleted"] = [];
    const tested: FakeApi["tested"] = [];
    const updated: FakeApi["updated"] = [];

    const api: FakeApi = {
        scripts,
        statements: [],
        lookups: [],
        closed,
        added,
        deleted,
        tested,
        updated,

        listConnections: (kind?: ConnectionKind) => {
            return Promise.resolve(
                (kind === "gui"
                    ? options.guiConnections
                    : options.connections) ?? [],
            );
        },

        listConnectionEntries: (kind?: ConnectionKind | "all") => {
            const entries = (list: string[] | undefined, of: ConnectionKind) => {
                return (list ?? []).map((uri) => {
                    return {
                        uri,
                        path: options.paths?.[uri] ?? "/",
                        kind: of,
                        ...options.looks?.[uri],
                    };
                });
            };

            if (kind === "all") {
                return options.noAllKind
                    ? Promise.reject(new Error("'all' is not a known "
                        + "connection kind."))
                    : Promise.resolve([
                        ...entries(options.connections, "mcp"),
                        ...entries(options.guiConnections, "gui"),
                    ]);
            }

            return Promise.resolve(kind === "gui"
                ? entries(options.guiConnections, "gui")
                : entries(options.connections, "mcp"));
        },

        addConnection: (
            uri: string,
            password: string,
            kind?: ConnectionKind,
            _verify?: boolean,
            details?: IConnectionDetails,
        ) => {
            added.push({ uri, password, kind, ...details });

            return Promise.resolve(uri);
        },

        deleteConnection: (uri: string, kind?: ConnectionKind) => {
            deleted.push({ uri, kind });

            return Promise.resolve(uri);
        },

        updateConnection: (
            uri: string,
            newUri?: string,
            kind?: ConnectionKind,
            newKind?: ConnectionKind,
            password?: string,
            newDetails?: IConnectionDetails,
        ) => {
            updated.push({
                uri, newUri, kind, newKind, password,
                ...(newDetails?.path === undefined
                    ? {}
                    : { newPath: newDetails.path }),
                ...(newDetails?.caption === undefined
                    ? {}
                    : { newCaption: newDetails.caption }),
                ...(newDetails?.color === undefined
                    ? {}
                    : { newColor: newDetails.color }),
            });

            return Promise.resolve(newUri ?? uri);
        },

        testConnection: (uri: string, password?: string) => {
            tested.push({ uri, password });

            return options.testFailure === undefined
                ? Promise.resolve(`Connected to '${uri}' successfully.`)
                : Promise.reject(new Error(options.testFailure));
        },

        connect: (uri: string) => {
            const id = options.connectionIds?.[uri];
            if (id === undefined) {
                return Promise.reject(
                    new Error(`'${uri}' is not a configured connection.`),
                );
            }

            // A real server hands out a UUID per call, so opening one
            // connection twice gives two connections.
            const opened = (opens.get(uri) ?? 0) + 1;
            opens.set(uri, opened);

            return Promise.resolve(opened === 1 ? id : `${id}-${opened}`);
        },

        close: (connectionId: string) => {
            closed.push(connectionId);

            return Promise.resolve();
        },

        listSchemas: () => {
            return Promise.resolve(options.schemas ?? []);
        },

        listObjects: (_id: string, schema: string, type: string) => {
            return Promise.resolve(
                options.objects?.[`${schema}/${type}`] ?? [],
            );
        },

        getObjectDetails: (
            _id: string,
            schema: string,
            name: string,
            objectType: ObjectType,
        ) => {
            api.lookups.push(`${schema}.${name}:${objectType}`);
            const details = options.details?.[`${schema}.${name}`];
            // Asked for as the wrong kind, the server finds nothing either.
            if (!details || (details.basic.type ?? "table") !== objectType) {
                // The typed answer the real API turns the server's words
                // into, which is what tells a view apart from a failure.
                return Promise.reject(new ObjectNotFoundError(
                    `Error executing tool db.get_object_details: Shell `
                    + `Error: No ${objectType} '${name}' found in schema `
                    + `'${schema}'. Use db.list_objects to list the `
                    + `${objectType}s of a schema.`,
                    objectType,
                    schema,
                    name,
                ));
            }

            return Promise.resolve(details);
        },

        executeScript: (
            _id: string,
            script: string,
            stopOnError?: boolean,
            limit?: number,
        ) => {
            scripts.push(script);
            api.stopOnError = stopOnError;
            api.limit = limit;

            const named = options.results?.[script.trim()];

            return Promise.resolve(
                named ?? options.defaultResults
                ?? [{ affected_items_count: 0, warnings_count: 0 }],
            );
        },

        executeSql: (_id: string, sql: string, page?: IPageRequest) => {
            api.statements.push({ sql, ...(page ? { page } : {}) });
            const all = options.pagedRows ?? [];
            if (!page) {
                return Promise.resolve({
                    result_sets: [{ columns: ["n"], rows: all }],
                });
            }

            const offset = page.offset ?? 0;

            return Promise.resolve({
                result_sets: [{
                    columns: Object.keys(all[0] ?? {}),
                    rows: all.slice(offset, offset + page.limit),
                    has_more_pages: all.length > offset + page.limit,
                }],
            });
        },
    };

    return api;
};

/** Remembers a default connection in memory. */
export const createFakeSettings = (
    initial?: string,
): IConnectionSettings & { value: string | undefined } => {
    const settings = {
        value: initial,

        getDefaultConnection: (): string | undefined => {
            return settings.value;
        },

        setDefaultConnection: (uri: string | undefined): Promise<void> => {
            settings.value = uri;

            return Promise.resolve();
        },
    };

    return settings;
};

/** A fake MCP connection, recording the tool calls made through it. */
export interface FakeMcpConnection extends IMcpConnection {
    calls: Array<{ name: string; args: Record<string, unknown> }>;
    closed: boolean;
}

export interface FakeConnector extends IMcpConnector {
    /** The commands that were started, in order. */
    commands: McpServerCommand[];
    /** The connections that were handed out, in order. */
    connections: FakeMcpConnection[];
}

/**
 * Builds an MCP connector that hands out recording connections.
 *
 * @param answer Called for every tool call, to produce its result.
 *
 * @returns The fake connector.
 */
export const createFakeConnector = (
    answer: (name: string, args: Record<string, unknown>) => IToolResult = () => {
        return { content: [] };
    },
): FakeConnector => {
    const commands: McpServerCommand[] = [];
    const connections: FakeMcpConnection[] = [];

    return {
        commands,
        connections,
        open: (command, onLog) => {
            commands.push(command);
            onLog(`Serving ${command.command}`);

            const connection: FakeMcpConnection = {
                calls: [],
                closed: false,
                callTool: (name, args) => {
                    connection.calls.push({ name, args });

                    return Promise.resolve(answer(name, args));
                },
                close: () => {
                    connection.closed = true;

                    return Promise.resolve();
                },
            };
            connections.push(connection);

            return Promise.resolve(connection);
        },
    };
};

/** What a fake server answers REST SQL with. */
export interface FakeRestSqlOptions extends FakeApiOptions {
    /**
     * Statement, without its `;` -> its one cell: a string as it is (the
     * text of a `SHOW CREATE`), anything else as JSON text.
     */
    answers?: Record<string, unknown>;
    /** Statement, without its `;` -> the error it fails with. */
    errors?: Record<string, string>;
}

export interface FakeRestSql extends FakeApi {
    /** Every statement that reached the server, in order. */
    sent: string[];
    /** Every call: one statement, or the statements of one script. */
    calls: Array<{ connectionId: string; statements: string[] }>;
    /** What a statement answers with from now on. */
    answers: Record<string, unknown>;
    errors: Record<string, string>;
}

/**
 * Builds a database API that answers REST SQL as the shell's `mrs` module
 * does: one cell of JSON per `SHOW ... FORMAT=JSON`, one result per
 * statement of a script.
 *
 * @param options What it answers with, and the plain fake's options.
 *
 * @returns The fake.
 */
export const createFakeRestSql = (
    options: FakeRestSqlOptions = {},
): FakeRestSql => {
    const base = createFakeApi(options);
    const fake = base as FakeRestSql;
    fake.sent = [];
    fake.calls = [];
    fake.answers = { ...options.answers };
    fake.errors = { ...options.errors };

    const answer = (sql: string): IStatementResult => {
        fake.sent.push(sql);
        const key = sql.trim().replace(/;$/, "");
        const error = fake.errors[key];
        if (error !== undefined) {
            return { error, statement: key };
        }
        if (!(key in fake.answers)) {
            return { affected_items_count: 0, warnings_count: 0 };
        }
        const value = fake.answers[key];

        return {
            result_sets: [{
                columns: ["result"],
                rows: [{
                    result: typeof value === "string"
                        ? value : JSON.stringify(value),
                }],
            }],
        };
    };

    fake.executeSql = (connectionId: string, sql: string) => {
        fake.calls.push({ connectionId, statements: [sql] });

        return Promise.resolve(answer(sql));
    };
    fake.executeScript = (
        connectionId: string,
        script: string,
        stopOnError?: boolean,
    ) => {
        fake.scripts.push(script);
        fake.stopOnError = stopOnError;
        // Every statement ends its line with `;`; a statement's own lines
        // do not.
        const statements = script.split(/(?<=;)\n/);
        fake.calls.push({ connectionId, statements });
        const results: IStatementResult[] = [];
        for (const statement of statements) {
            const result = answer(statement);
            results.push(result);
            if (result.error !== undefined && stopOnError !== false) {
                break;
            }
        }

        return Promise.resolve(results);
    };

    return fake;
};

/** A REST metadata status of a configured, enabled, current server. */
export const mrsStatus = (
    overrides: Partial<IMrsStatus> = {},
): IMrsStatus => {
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
        required_rest_daemon_version: "26.10.0",
        metadata_version: 12,
        metadata_schema: "mariadb_rest_service",
        ...overrides,
    };
};

export const mrsService = (
    overrides: Partial<IMrsService> = {},
): IMrsService => {
    return {
        id: "11111111-0000-0000-0000-000000000001",
        url_context_root: "/myService",
        full_service_path: "/myService",
        url_protocol: ["HTTPS"],
        name: "myService",
        enabled: 1,
        published: false,
        comments: null,
        options: null,
        metadata: null,
        auth_path: "/authentication",
        auth_completed_url: null,
        auth_completed_url_validation: null,
        auth_completed_page_content: null,
        in_development: null,
        ...overrides,
    };
};

export const mrsSchema = (
    overrides: Partial<IMrsSchema> = {},
): IMrsSchema => {
    return {
        id: "22222222-0000-0000-0000-000000000001",
        service_id: "11111111-0000-0000-0000-000000000001",
        name: "sakila",
        schema_type: "DATABASE_SCHEMA",
        request_path: "/sakila",
        requires_auth: false,
        enabled: 1,
        items_per_page: null,
        comments: null,
        options: null,
        metadata: null,
        ...overrides,
    };
};

export const mrsObject = (
    overrides: Partial<IMrsObject> = {},
): IMrsObject => {
    return {
        id: "33333333-0000-0000-0000-000000000001",
        rest_schema_id: "22222222-0000-0000-0000-000000000001",
        name: "actor",
        schema_name: "sakila",
        request_path: "/actor",
        object_type: "TABLE",
        crud_operations: ["READ"],
        format: "FEED",
        enabled: 1,
        requires_auth: false,
        items_per_page: null,
        media_type: null,
        auth_stored_procedure: null,
        comments: null,
        options: null,
        metadata: null,
        ...overrides,
    };
};

export const mrsContentSet = (
    overrides: Partial<IMrsContentSet> = {},
): IMrsContentSet => {
    return {
        id: "44444444-0000-0000-0000-000000000001",
        service_id: "11111111-0000-0000-0000-000000000001",
        content_type: "STATIC",
        request_path: "/app",
        requires_auth: false,
        enabled: 1,
        comments: null,
        options: null,
        ...overrides,
    };
};

export const mrsContentFile = (
    overrides: Partial<IMrsContentFile> = {},
): IMrsContentFile => {
    return {
        id: "55555555-0000-0000-0000-000000000001",
        content_set_id: "44444444-0000-0000-0000-000000000001",
        request_path: "/index.html",
        requires_auth: false,
        enabled: 1,
        size: 512,
        ...overrides,
    };
};

export const mrsAuthApp = (
    overrides: Partial<IMrsAuthApp> = {},
): IMrsAuthApp => {
    return {
        id: "66666666-0000-0000-0000-000000000001",
        auth_vendor_id: "30000000-0000-0000-0000-000000000000",
        auth_vendor: "MRS",
        name: "MRS",
        description: null,
        url: null,
        app_id: null,
        has_app_secret: false,
        enabled: true,
        limit_to_registered_users: true,
        default_role_id: null,
        ...overrides,
    };
};

export const mrsUser = (overrides: Partial<IMrsUser> = {}): IMrsUser => {
    return {
        id: "77777777-0000-0000-0000-000000000001",
        auth_app_id: "66666666-0000-0000-0000-000000000001",
        auth_app_name: "MRS",
        name: "anna",
        email: null,
        vendor_user_id: null,
        mapped_user_id: null,
        login_permitted: true,
        has_password: true,
        app_options: null,
        options: null,
        roles: [],
        ...overrides,
    };
};

export const mrsDaemon = (
    overrides: Partial<IMrsDaemon> = {},
): IMrsDaemon => {
    return {
        id: "88888888-0000-0000-0000-000000000001",
        name: "daemon1",
        address: "host1:8443",
        product_name: "MariaDB REST Daemon",
        version: "26.10.0",
        last_check_in: null,
        active: true,
        developer: null,
        ...overrides,
    };
};
