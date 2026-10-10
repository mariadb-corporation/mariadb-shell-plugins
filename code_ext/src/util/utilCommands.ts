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

import * as path from "node:path";

import * as vscode from "vscode";

import { connectionLabel } from "../connections/connectionUri.js";
import type {
    ConnectionsNode,
    IConnectionNode,
    IObjectNode,
    ISchemaNode,
} from "../tree/connectionsModel.js";
import type {
    IUtilApi,
    IUtilTaskStarted,
    UtilOptions,
} from "../mcp/utilApi.js";
import { errorText } from "../text.js";
import {
    canResume,
    type IResumeLoad,
    type ITrackedTask,
    type TaskMonitor,
} from "./taskMonitor.js";
import { UtilDialogPanel } from "./utilDialogPanel.js";
import { utilOperationSpec, type UtilOperation } from "./utilFields.js";

/** What the dump, load, copy, export and import commands need. */
export interface IUtilCommandsHost {
    extensionUri: vscode.Uri;
    /** Opens the tree's session on a connection; returns its id. */
    connect(uri: string): Promise<string>;
    /** The configured connections, which a copy can go to. */
    connectionUris(): Promise<string[]>;
    utilApi(): Promise<IUtilApi>;
    monitor: TaskMonitor;
    /** Reads the Connections view again, after a load changed a server. */
    refresh(): void;
    /** Where a new dump goes, unless the user picks another place. */
    defaultFolder(): string;
    log(message: string): void;
}

/** The rows a command applies to: the one it was opened on, or the selection. */
const selected = <T extends ConnectionsNode>(
    node: T,
    selection: ConnectionsNode[] | undefined,
    matches: (candidate: ConnectionsNode) => candidate is T,
): T[] => {
    const picked = (selection ?? []).filter(matches);
    // By what the row is, not which object: a selection made before the
    // tree redrew holds other objects for the same rows.
    const key = (candidate: ConnectionsNode): string => {
        return JSON.stringify([candidate.kind,
            "uri" in candidate ? candidate.uri : "",
            "schema" in candidate ? candidate.schema : "",
            "name" in candidate ? candidate.name : ""]);
    };
    const opened = key(node);

    return picked.some((candidate) => { return key(candidate) === opened; })
        ? picked : [node];
};

const isSchema = (candidate: ConnectionsNode): candidate is ISchemaNode => {
    return candidate.kind === "schema";
};

const isTable = (candidate: ConnectionsNode): candidate is IObjectNode => {
    return candidate.kind === "object"
        && (candidate.objectType === "table" || candidate.objectType === "view");
};

/** A name for a new dump folder: what is dumped and when. */
export const dumpFolderName = (name: string, now = new Date()): string => {
    const pad = (value: number): string => { return String(value).padStart(2, "0"); };
    const stamp = `${String(now.getFullYear())}-${pad(now.getMonth() + 1)}-`
        + `${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;

    return `${name.replace(/[^\w.-]+/gu, "_")}-${stamp}`;
};

interface IStart {
    operation: UtilOperation;
    uri: string;
    schemas?: string[];
    tables?: string[];
    initialPath?: string;
    /** Whether the server's contents change, so the tree is read again. */
    changes: boolean;
    /** How to start it again, for a load. */
    resume?(options: UtilOptions, paths: string[]): IResumeLoad;
    run(api: IUtilApi, connectionId: string, options: UtilOptions,
        paths: string[], targetId?: string): Promise<IUtilTaskStarted>;
}

export class UtilCommands {
    public constructor(private readonly host: IUtilCommandsHost) { }

    public register(): vscode.Disposable[] {
        const commands: Record<string, (...args: never[]) => unknown> = {
            "mariadb.dumpInstance": (node?: IConnectionNode) => {
                return this.#connectionCommand(node, "dumpInstance");
            },
            "mariadb.loadDump": (node?: IConnectionNode) => {
                return this.#connectionCommand(node, "loadDump");
            },
            "mariadb.copyInstance": (node?: IConnectionNode) => {
                return this.#connectionCommand(node, "copyInstance");
            },
            "mariadb.dumpSchemas": (node?: ISchemaNode,
                selection?: ConnectionsNode[]) => {
                return this.#schemaCommand(node, selection, "dumpSchemas");
            },
            "mariadb.copySchemas": (node?: ISchemaNode,
                selection?: ConnectionsNode[]) => {
                return this.#schemaCommand(node, selection, "copySchemas");
            },
            "mariadb.dumpTables": (node?: IObjectNode,
                selection?: ConnectionsNode[]) => {
                return this.#tableCommand(node, selection, "dumpTables");
            },
            "mariadb.copyTables": (node?: IObjectNode,
                selection?: ConnectionsNode[]) => {
                return this.#tableCommand(node, selection, "copyTables");
            },
            "mariadb.exportTable": (node?: IObjectNode) => {
                return this.#tableCommand(node, undefined, "exportTable");
            },
            "mariadb.importTable": (node?: IObjectNode) => {
                return this.#tableCommand(node, undefined, "importTable");
            },
            "mariadb.cancelTask": (item?: ITrackedTask) => {
                if (item !== undefined) {
                    void this.host.monitor.cancel(item.state.task_id);
                }
            },
            "mariadb.resumeTask": (item?: ITrackedTask) => {
                if (item !== undefined && canResume(item)) {
                    void this.#resume(item);
                }
            },
            "mariadb.removeTask": (item?: ITrackedTask) => {
                if (item !== undefined) {
                    this.host.monitor.remove(item.state.task_id);
                }
            },
            "mariadb.clearFinishedTasks": () => {
                this.host.monitor.clearFinished();
            },
            "mariadb.showTaskOutput": () => {
                this.host.monitor.showOutput();
            },
        };

        return Object.entries(commands).map(([id, handler]) => {
            return vscode.commands.registerCommand(id, handler);
        });
    }

    #connectionCommand(node: IConnectionNode | undefined,
        operation: UtilOperation): void {
        if (node?.kind !== "connection") {
            return;
        }
        const name = connectionLabel(node.uri);

        switch (operation) {
            case "dumpInstance": {
                this.#open({
                    operation, uri: node.uri, changes: false,
                    initialPath: path.join(this.host.defaultFolder(),
                        dumpFolderName(name.replace(/^.*@/u, ""))),
                    run: (api, id, options, paths) => {
                        return api.dumpInstance(id, paths[0], options);
                    },
                });
                break;
            }

            case "loadDump": {
                this.#open({
                    operation, uri: node.uri, changes: true,
                    run: (api, id, options, paths) => {
                        return api.loadDump(id, paths[0], options);
                    },
                    resume: (options, paths) => {
                        // Starting over would load everything again.
                        const { resetProgress: _reset, ...kept } = options;

                        return { operation: "loadDump", uri: node.uri,
                            url: paths[0], options: kept };
                    },
                });
                break;
            }

            default: {
                this.#open({
                    operation, uri: node.uri, changes: true,
                    run: (api, id, options, _paths, target) => {
                        return api.copyInstance(id, target!, options);
                    },
                });
            }
        }
    }

    #schemaCommand(node: ISchemaNode | undefined,
        selection: ConnectionsNode[] | undefined,
        operation: UtilOperation): void {
        if (node?.kind !== "schema") {
            return;
        }
        const schemas = selected(node, selection, isSchema)
            .filter((candidate) => { return candidate.uri === node.uri; })
            .map((candidate) => { return candidate.schema; });

        if (operation === "dumpSchemas") {
            this.#open({
                operation, uri: node.uri, schemas, changes: false,
                initialPath: path.join(this.host.defaultFolder(),
                    dumpFolderName(schemas.length === 1 ? schemas[0]
                        : "schemas")),
                run: (api, id, options, paths) => {
                    return api.dumpSchemas(id, schemas, paths[0], options);
                },
            });

            return;
        }

        this.#open({
            operation, uri: node.uri, schemas, changes: true,
            run: (api, id, options, _paths, target) => {
                return api.copySchemas(id, schemas, target!, options);
            },
        });
    }

    #tableCommand(node: IObjectNode | undefined,
        selection: ConnectionsNode[] | undefined,
        operation: UtilOperation): void {
        if (node === undefined || !isTable(node)) {
            return;
        }
        const tables = selected(node, selection, isTable)
            .filter((candidate) => {
                return candidate.uri === node.uri
                    && candidate.schema === node.schema;
            })
            .map((candidate) => { return candidate.name; });
        const schema = node.schema;
        const subject = { uri: node.uri, schemas: [schema], tables };

        switch (operation) {
            case "dumpTables": {
                this.#open({
                    ...subject, operation, changes: false,
                    initialPath: path.join(this.host.defaultFolder(),
                        dumpFolderName(tables.length === 1
                            ? `${schema}.${tables[0]}` : schema)),
                    run: (api, id, options, paths) => {
                        return api.dumpTables(id, schema, tables, paths[0],
                            options);
                    },
                });
                break;
            }

            case "copyTables": {
                this.#open({
                    ...subject, operation, changes: true,
                    run: (api, id, options, _paths, target) => {
                        return api.copyTables(id, schema, tables, target!,
                            options);
                    },
                });
                break;
            }

            case "exportTable": {
                this.#open({
                    ...subject, tables: [node.name], operation, changes: false,
                    initialPath: path.join(this.host.defaultFolder(),
                        `${schema}.${node.name}.tsv`),
                    run: (api, id, options, paths) => {
                        return api.exportTable(id,
                            `${quote(schema)}.${quote(node.name)}`, paths[0],
                            options);
                    },
                });
                break;
            }

            default: {
                this.#open({
                    ...subject, tables: [node.name], operation, changes: true,
                    run: (api, id, options, paths) => {
                        return api.importTable(id,
                            paths.length === 1 ? paths[0] : paths, options);
                    },
                });
            }
        }
    }

    /**
     * Starts a load that stopped again, in place of its row: it loads what
     * the first one had not, from the progress file in the dump folder.
     */
    async #resume(tracked: ITrackedTask): Promise<void> {
        const resume = tracked.resume!;
        try {
            const id = await this.host.connect(resume.uri);
            const started = await (await this.host.utilApi()).loadDump(id,
                resume.url, resume.options);
            this.host.log(`Resumed ${tracked.state.title}.`);
            this.host.monitor.remove(tracked.state.task_id);

            void this.host.monitor.follow(started, {
                title: tracked.state.title,
                connection: resume.uri,
                resume,
                onDone: (state) => {
                    if (state.status !== "failed") {
                        this.host.refresh();
                    }
                },
            });
        } catch (error) {
            void vscode.window.showErrorMessage(
                `Could not resume ${tracked.state.title}: ${errorText(error)}`);
        }
    }

    /** Opens the dialog for an operation, which then starts and follows it. */
    #open(start: IStart): void {
        const spec = utilOperationSpec(start.operation, {
            connection: connectionLabel(start.uri),
            schemas: start.schemas,
            tables: start.tables,
        });
        const needsTarget = spec.target === true;

        void (async () => {
            const targets = needsTarget
                ? (await this.host.connectionUris()).filter((uri) => {
                    return uri !== start.uri;
                })
                : [];

            UtilDialogPanel.show(this.host.extensionUri, spec, {
                initialPath: start.initialPath,
                targets,
                log: this.host.log,
                start: async (options, paths, target) => {
                    const id = await this.host.connect(start.uri);
                    const targetId = target === undefined ? undefined
                        : await this.host.connect(target);
                    const api = await this.host.utilApi();
                    const started = await start.run(api, id, options, paths,
                        targetId);
                    this.host.log(`Started ${spec.title}.`);

                    // Followed from here on, not awaited: the dialog closes
                    // as soon as the work runs.
                    void this.host.monitor.follow(started, {
                        title: spec.title,
                        connection: start.uri,
                        ...(start.resume === undefined ? {}
                            : { resume: start.resume(options, paths) }),
                        onDone: (state) => {
                            if (start.changes && state.status !== "failed") {
                                this.host.refresh();
                            }
                        },
                    });
                },
            });
        })();
    }
}

/** A name for util.export_table's `schema.table`, quoted where it must be. */

const quote = (name: string): string => {
    return /^[A-Za-z_][A-Za-z0-9_$]*$/u.test(name)
        ? name : `\`${name.replaceAll("`", "``")}\``;
};
