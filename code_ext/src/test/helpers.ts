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
import type { IToolResult } from "../mcp/protocol.js";
import type { IMcpConnection, IMcpConnector } from "../mcp/session.js";
import type {
    ConnectionKind,
    IMariaDbApi,
    IObjectDetails,
    IObjectInfo,
    ISchemaInfo,
    IStatementResult,
} from "../mcp/types.js";
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
    }>;
    /** What the last script was asked to do about a failing statement. */
    stopOnError?: boolean;
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
                    return { uri, path: options.paths?.[uri] ?? "/", kind: of };
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
            path?: string,
        ) => {
            added.push({
                uri, password, kind, ...(path === undefined ? {} : { path }),
            });

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
            newPath?: string,
        ) => {
            updated.push({
                uri, newUri, kind, newKind, password,
                ...(newPath === undefined ? {} : { newPath }),
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
            objectType: string,
        ) => {
            api.lookups.push(`${schema}.${name}:${objectType}`);
            const details = options.details?.[`${schema}.${name}`];
            // Asked for as the wrong kind, the server finds nothing either.
            if (!details || (details.basic.type ?? "table") !== objectType) {
                // Worded as the server words it, which is what tells a
                // view apart from a failure.
                return Promise.reject(new Error(
                    `Error executing tool db.get_object_details: Shell `
                    + `Error: No ${objectType} '${name}' found in schema `
                    + `'${schema}'. Use db.list_objects to list the `
                    + `${objectType}s of a schema.`,
                ));
            }

            return Promise.resolve(details);
        },

        executeScript: (
            _id: string,
            script: string,
            stopOnError?: boolean,
        ) => {
            scripts.push(script);
            api.stopOnError = stopOnError;

            const named = options.results?.[script.trim()];

            return Promise.resolve(
                named ?? options.defaultResults
                ?? [{ affected_items_count: 0, warnings_count: 0 }],
            );
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
