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

import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import * as vscode from "vscode";

import { GENERAL_ACTIONS } from "../connections/connectionActivity.js";
import { parseConnectionUri } from "../connections/connectionUri.js";
import { reportError } from "../errorMessages.js";
import type { IRestApi, IRestdSetup } from "../mcp/restApi.js";
import { MrsDialogPanel } from "../mrs/mrsDialogPanel.js";
import {
    restdDefaults,
    type IRestdContext,
    type IRestdDialogValues,
} from "../mrs/mrsDialogs.js";
import type { IConnectionNode } from "../tree/connectionsModel.js";
import {
    restdServerLabel,
    type IRestdNode,
    type RestdTreeProvider,
} from "../tree/restdTreeProvider.js";
import { errorText } from "../text.js";
import { ActionGroup, type ActionPutter } from "./actionGroup.js";

/** What the REST Daemon commands need from the extension. */
export interface IRestdCommandsHost {
    extensionUri: vscode.Uri;
    /** Opens the extension's connection to a URI; its connection id. */
    connect(uri: string): Promise<string>;
    /** The REST metadata schemas a connection's account can see. */
    metadataSchemas(connectionId: string): Promise<string[]>;
    restApi(): Promise<IRestApi>;
    tree: RestdTreeProvider;
    put: ActionPutter;
    /** Brings up the panel on a connection's actions. */
    showActions(connection: string): void;
    /** Where the failures' full text is kept, a file each. */
    logFolder: string;
    log(message: string): void;
}

/**
 * Keeps a failure's full text in a file of its own, for its row to open:
 * a setup's error carries everything mariadb-restd printed, more than a
 * row can show.
 *
 * @param folder Where.
 * @param name What it was, for the file name.
 * @param text The text.
 *
 * @returns The file.
 */
export const writeRestdLog = async (
    folder: string,
    name: string,
    text: string,
): Promise<string> => {
    await mkdir(folder, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/gu, "-");
    const file = join(folder, `${name.replace(/[^\w-]/gu, "_")}-${stamp}.log`);
    await writeFile(file, `${text}\n`);

    return file;
};

/**
 * The REST Daemons view's commands, and setting a REST Daemon up for any
 * connection from its menu.
 */
export class RestdCommands {
    public constructor(private readonly host: IRestdCommandsHost) { }

    /**
     * @returns The registrations, for the extension to dispose.
     */
    public register(): vscode.Disposable[] {
        const command = <T>(
            id: string,
            handler: (node: T) => Promise<void>,
        ): vscode.Disposable => {
            return vscode.commands.registerCommand(id, async (node?: T) => {
                if (node === undefined) {
                    return;
                }
                try {
                    await handler(node);
                } catch (error) {
                    reportError(this.host.log, error);
                }
            });
        };

        return [
            vscode.commands.registerCommand("mariadb.restd.refresh", () => {
                this.host.tree.refresh();
            }),
            command<IConnectionNode>("mariadb.restd.setUp", (node) => {
                return this.setUp(node);
            }),
            command<IRestdNode>("mariadb.restd.start", (node) => {
                return this.#lifecycle(node, "start");
            }),
            command<IRestdNode>("mariadb.restd.stop", (node) => {
                return this.#lifecycle(node, "stop");
            }),
            command<IRestdNode>("mariadb.restd.delete", (node) => {
                return this.delete(node);
            }),
            command<IRestdNode>("mariadb.restd.openLog", async (node) => {
                await this.#openLog(node.log_path);
            }),
            command<IRestdNode>("mariadb.restd.openInBrowser", async (node) => {
                await vscode.env.openExternal(vscode.Uri.parse(node.url));
            }),
        ];
    }

    /**
     * Opens the Set Up MariaDB REST Daemon dialog on a connection.
     *
     * @param node The connection's row.
     */
    public async setUp(node: IConnectionNode): Promise<void> {
        const connectionId = await this.host.connect(node.uri);
        const fields = parseConnectionUri(node.uri);
        let metadataSchemas: string[] = [];
        try {
            metadataSchemas = await this.host.metadataSchemas(connectionId);
        } catch {
            // No REST metadata: the setup creates it.
        }
        const context: IRestdContext = {
            server: `${fields.host}:${fields.port}`,
            user: fields.user || "root",
            metadataSchemas,
        };

        MrsDialogPanel.show(this.host.extensionUri, {
            dialog: "restd",
            title: "Set Up MariaDB REST Daemon",
            values: restdDefaults(context),
            context,
            save: async (values) => {
                await this.#configure(node.uri, connectionId, context,
                    values as IRestdDialogValues);
            },
        }, {
            onSaved: () => { this.host.tree.refresh(); },
            onFailed: () => { this.host.showActions(node.uri); },
            log: this.host.log,
        });
    }

    /**
     * Sets the daemon up, as one action of the connection's.
     *
     * @param uri The connection.
     * @param connectionId Its connection id.
     * @param context What the dialog was opened on.
     * @param values What the dialog says.
     */
    async #configure(
        uri: string,
        connectionId: string,
        context: IRestdContext,
        values: IRestdDialogValues,
    ): Promise<void> {
        const port = values.port.trim();
        const group = new ActionGroup(this.host.put, uri, {
            doing: `Setting up REST Daemon for ${context.server}`,
            done: `Set up REST Daemon for ${context.server}`,
        });
        try {
            const setup = await group.step<IRestdSetup>({
                doing: "Setting up REST Daemon"
                    + (port === "" ? "" : ` on port ${port}`),
                done: "Set up REST Daemon",
            }, "rest.configure_restd(" + [
                ...(port === "" ? [] : [`port=${port}`]),
                ...(values.metadataSchema === ""
                    ? [] : [`metadata_schema='${values.metadataSchema}'`]),
            ].join(", ") + ")", async () => {
                return (await this.host.restApi()).configure({
                    connectionId,
                    ...(values.password === ""
                        ? {} : { password: values.password }),
                    ...(port === "" ? {} : { port: Number(port) }),
                    ...(values.metadataSchema === ""
                        ? {} : { metadataSchema: values.metadataSchema }),
                });
            }, {
                detail: (done) => {
                    return `Serves ${done.url}`
                        + (done.installed ? ", mariadb-restd was installed" : "")
                        + ` (${done.directory})`;
                },
                logOf: (error) => {
                    return writeRestdLog(this.host.logFolder,
                        `restd-setup-${context.server}`, errorText(error));
                },
            });
            group.finish(setup.url);
        } catch (error) {
            group.finish();
            throw error;
        }
    }

    /**
     * Starts or stops a daemon from its row.
     *
     * @param node The row.
     * @param change What to do.
     */
    async #lifecycle(node: IRestdNode, change: "start" | "stop"): Promise<void> {
        const daemon = `REST Daemon on port ${String(node.port)}`;
        const words = change === "start"
            ? { doing: `Starting ${daemon}`, done: `Started ${daemon}` }
            : { doing: `Stopping ${daemon}`, done: `Stopped ${daemon}` };
        await this.host.tree.run(node,
            change === "start" ? "starting" : "stopping", async () => {
                const group = new ActionGroup(this.host.put, GENERAL_ACTIONS,
                    words);
                try {
                    await group.step(words,
                        `rest.${change}_restd(directory='${node.directory}')`,
                        async () => {
                            const api = await this.host.restApi();
                            await (change === "start"
                                ? api.start(node.directory)
                                : api.stop(node.directory));
                        }, {
                            logOf: () => {
                                return Promise.resolve(node.log_path);
                            },
                        });
                } catch (error) {
                    this.host.showActions(GENERAL_ACTIONS);
                    throw error;
                } finally {
                    group.finish();
                }
            });
    }

    /**
     * Deletes a deployment, dropping the account its setup created where
     * its server can be reached, after asking.
     *
     * @param node The row.
     */
    public async delete(node: IRestdNode): Promise<void> {
        const confirmed = await vscode.window.showWarningMessage(
            `Delete the REST Daemon serving ${restdServerLabel(node)}?`,
            {
                modal: true,
                detail: "It is stopped, the server account its setup "
                    + "created is dropped, and its deployment folder is "
                    + `deleted:\n${node.directory}`,
            },
            "Delete",
        );
        if (confirmed !== "Delete") {
            return;
        }

        let connectionId: string | undefined;
        try {
            if (node.connection_uri === null) {
                throw new Error("It does not say which connection set it up.");
            }
            connectionId = await this.host.connect(node.connection_uri);
        } catch (error) {
            const folderOnly = await vscode.window.showWarningMessage(
                `The server of the REST Daemon cannot be reached: `
                + `${errorText(error)}`,
                {
                    modal: true,
                    detail: "Its server account cannot be dropped. Delete "
                        + "its deployment folder only?",
                },
                "Delete Folder Only",
            );
            if (folderOnly !== "Delete Folder Only") {
                return;
            }
        }

        const daemon = `REST Daemon on port ${String(node.port)}`;
        const words = { doing: `Deleting ${daemon}`, done: `Deleted ${daemon}` };
        await this.host.tree.run(node, "deleting", async () => {
            const group = new ActionGroup(this.host.put, GENERAL_ACTIONS, words);
            try {
                await group.step(words, "rest.delete_restd_config("
                    + `directory='${node.directory}'`
                    + (connectionId === undefined ? ", drop_accounts=False" : "")
                    + ")", async () => {
                    return (await this.host.restApi()).delete(node.directory,
                        connectionId);
                }, { detail: (said) => { return said; } });
            } catch (error) {
                this.host.showActions(GENERAL_ACTIONS);
                throw error;
            } finally {
                group.finish();
            }
        });
    }

    /**
     * @param file A daemon's log.
     */
    async #openLog(file: string): Promise<void> {
        try {
            const document = await vscode.workspace.openTextDocument(
                vscode.Uri.file(file));
            await vscode.window.showTextDocument(document, { preview: true });
        } catch {
            void vscode.window.showInformationMessage(
                `The REST Daemon has not written its log yet: ${file}`);
        }
    }
}
