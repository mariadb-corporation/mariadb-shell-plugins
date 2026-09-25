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

import * as vscode from "vscode";

import {
    ConnectionManager,
    UI_BACKEND_SESSION,
} from "./connections/connectionManager.js";
import {
    connectOnOpen,
    createWorkspaceSettings,
    publishConnectMode,
} from "./connections/settings.js";
import { applyKeybindings } from "./editor/keybindings.js";
import { SqlEditorBinding } from "./editor/sqlEditorBinding.js";
import { StatementDecorator } from "./editor/statementDecorations.js";
import { showErrorWithLog } from "./errorMessages.js";
import { createSdkConnector } from "./mcp/sdkConnector.js";
import {
    ServerStarter,
    type ServerStepPhase,
} from "./mcp/serverStarter.js";
import { McpSession } from "./mcp/session.js";
import type { IMariaDbApi } from "./mcp/types.js";
import { ensureShell } from "./shell/bootstrap.js";
import { MINIMUM_SHELL_VERSION } from "./shell/constants.js";
import { buildMcpServerCommand } from "./shell/mcpServer.js";
import {
    createNodeProcessRunner,
    createNodeShellEnvironment,
} from "./shell/nodeRuntime.js";
import { createNotificationProgressHost } from "./shell/vscodeProgress.js";
import {
    ConnectionsTreeProvider,
    CONNECTIONS_VIEW_ID,
} from "./tree/connectionsTreeProvider.js";
import { createIconResolver } from "./tree/treeItems.js";
import { ConnectionEditorPanel } from "./connections/connectionEditorPanel.js";
import { deleteConnection } from "./connections/connectionStore.js";
import type {
    IConnectionNode,
    IConnectionStatusNode,
} from "./tree/connectionsModel.js";
import {
    ResultViewProvider,
    RESULT_VIEW_ID,
} from "./webview/resultViewProvider.js";

/**
 * Everything the extension builds on activation, kept together so
 * deactivate can take it all down again.
 */
interface IExtensionState {
    session: McpSession;
    connections: ConnectionManager;
    resultView: ResultViewProvider;
}

let state: IExtensionState | undefined;

/**
 * Finds or installs the MariaDB Shell and starts the MCP server on it.
 *
 * @param session The session to start.
 * @param log Where to write progress and errors.
 * @param onPhase Told when the installer starts and when the server does.
 *
 * @returns The typed API to call the server with.
 */
const startServer = async (
    session: McpSession,
    log: (message: string) => void,
    onPhase: (phase: ServerStepPhase) => void,
): Promise<IMariaDbApi> => {
    const { location, installed } = await ensureShell({
        environment: createNodeShellEnvironment(log),
        runner: createNodeProcessRunner(),
        progress: createNotificationProgressHost(),
        log,
        minimumVersion: MINIMUM_SHELL_VERSION,
        onInstalling: () => { onPhase("installing"); },
    });

    if (installed) {
        void vscode.window.showInformationMessage(
            `MariaDB Shell ${MINIMUM_SHELL_VERSION} was installed.`,
        );
    }

    onPhase("starting");

    return await session.start(buildMcpServerCommand(location));
};

/**
 * Shows the Connections view as busy for as long as the server is on its
 * way up, which is the one sign of life the view itself can give while
 * its welcome content says what is happening.
 *
 * @param starter The startup to follow.
 *
 * @returns Nothing.
 */
const showStartupInView = (starter: ServerStarter): void => {
    let busy: (() => void) | undefined;
    starter.onDidChangePhase((phase) => {
        const underWay = phase === "locating"
            || phase === "installing"
            || phase === "starting";
        if (underWay && !busy) {
            void vscode.window.withProgress(
                { location: { viewId: CONNECTIONS_VIEW_ID } },
                () => {
                    return new Promise<void>((resolve) => {
                        busy = resolve;
                    });
                },
            );
        } else if (!underWay && busy) {
            busy();
            busy = undefined;
        }
    });
};

/**
 * Runs an action, reporting a failure rather than letting it escape into
 * the command handler.
 *
 * @param log Where to write the failure.
 * @param action The action to run.
 *
 * @returns Nothing.
 */
const guard = async (
    log: (message: string) => void,
    action: () => Promise<void>,
): Promise<void> => {
    try {
        await action();
    } catch (error) {
        const message = error instanceof Error
            ? error.message
            : String(error);
        log(message);
        void showErrorWithLog(message);
    }
};

/**
 * Activates the extension.
 *
 * @param context The extension context.
 *
 * @returns Nothing.
 */
export const activate = (context: vscode.ExtensionContext): void => {
    const output = vscode.window.createOutputChannel("MariaDB");
    context.subscriptions.push(output);

    const log = (message: string): void => {
        output.appendLine(`[${new Date().toISOString()}] ${message}`);
    };

    const packageJson = context.extension?.packageJSON as
        | { version?: string }
        | undefined;
    log(`MariaDB extension ${packageJson?.version ?? "(unknown version)"} `
        + `on VS Code ${vscode.version}, ${process.platform} `
        + `${process.arch}, Node ${process.versions.node}. `
        + `Needs MariaDB Shell ${MINIMUM_SHELL_VERSION} or newer.`);

    const session = new McpSession(createSdkConnector(), log);
    const starter = new ServerStarter((onPhase) => {
        return startServer(session, log, onPhase);
    }, log);
    showStartupInView(starter);
    const resultView = new ResultViewProvider(context.extensionUri, log);
    const connections = new ConnectionManager(
        async () => {
            return session.api ?? await starter.start();
        },
        createWorkspaceSettings(),
        // Everything that happens on an open connection is a row of that
        // connection's output, not only the SQL an editor runs on it.
        (event) => { void resultView.appendEvent(event); },
    );
    state = { session, connections, resultView };

    const tree = new ConnectionsTreeProvider(
        connections,
        createIconResolver(context.extensionUri),
        log,
        connectOnOpen,
        starter,
    );
    const editors = new SqlEditorBinding(connections, resultView, log);
    // Marks where each statement begins. The ranges come from the SQL
    // scanner for now; a language server would report the same thing.
    const statementDots = new StatementDecorator(context.extensionUri);
    resultView.setRefreshHandler(async (resultSet) => {
        await editors.refreshResultSet(resultSet);
    });
    resultView.setConnectionLister(async () => {
        return await connections.listConnections();
    });
    resultView.setSessionLister((uri) => {
        return connections.sessionsOf(uri).map((open) => {
            return open.label;
        });
    });
    resultView.setRevealHandler(async (source) => {
        await editors.revealStatement(source);
    });

    const connectionsView = vscode.window.createTreeView(CONNECTIONS_VIEW_ID, {
        treeDataProvider: tree,
        showCollapseAll: true,
    });

    // Where connecting is implicit, this is what makes it happen: opening a
    // connection row is the gesture, and the tree provider decides whether
    // the mode in force means anything by it.
    connectionsView.onDidExpandElement((event) => {
        void tree.expanded(event.element);
    });

    context.subscriptions.push(
        tree,
        editors,
        statementDots,
        resultView,
        connectionsView,
        vscode.window.registerWebviewViewProvider(
            RESULT_VIEW_ID,
            resultView,
            // The grid's pending edits must survive the panel being
            // hidden, which happens whenever another panel tab is picked.
            { webviewOptions: { retainContextWhenHidden: true } },
        ),
    );

    // The setting can be changed in the settings editor as well as from
    // the tree, and the tree has to follow either way.
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration((event) => {
            if (event.affectsConfiguration("mariadb.defaultConnection")) {
                tree.refresh();
                editors.updateStatusBar();
            }

            if (event.affectsConfiguration("mariadb.keybindings")) {
                void applyKeybindings();
            }

            // Both the twistie on a closed connection and the Connect
            // button follow the mode, so the tree and the menus have to
            // be told when it changes.
            if (event.affectsConfiguration(
                "mariadb.connections.connectMode")) {
                void publishConnectMode();
                tree.refresh();
            }

            // Files that have not been switched by hand follow the
            // setting, so the toolbar button has to catch up.
            if (event.affectsConfiguration("mariadb.execute.stopOnError")) {
                editors.updateStatusBar();
            }
        }),
    );

    // The contributed keybindings are gated on context keys, which is how
    // the chosen chords reach them.
    void applyKeybindings();
    void publishConnectMode();

    context.subscriptions.push(
        vscode.commands.registerCommand("mariadb.refreshConnections", () => {
            tree.refresh();
        }),

        vscode.commands.registerCommand("mariadb.addConnection", () => {
            ConnectionEditorPanel.show(context.extensionUri, {
                api: () => { return connections.api(); },
                onSaved: () => { tree.refresh(); },
                log,
            });
        }),

        vscode.commands.registerCommand(
            "mariadb.editConnection",
            (node?: IConnectionNode) => {
                if (node === undefined) {
                    return;
                }

                // Both halves of what identifies it: the URI is the key and
                // the kind says which of the two lists it is the key in.
                ConnectionEditorPanel.show(
                    context.extensionUri,
                    {
                        api: () => { return connections.api(); },
                        onSaved: () => { tree.refresh(); },
                        log,
                    },
                    { uri: node.uri, kind: node.connectionKind },
                );
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.deleteConnection",
            async (node?: IConnectionNode) => {
                if (node === undefined) {
                    return;
                }

                // Modal, because this throws away a stored credential and
                // there is no undo. The MCP list is called out by name: a
                // connection there may be in use by something that is not
                // this editor.
                const detail = node.connectionKind === "mcp"
                    ? "It is in the shared MCP connection list, so any MCP "
                    + "client configured to use it will lose it too. Its "
                    + "stored password is deleted."
                    : "Its stored password is deleted.";

                const confirmed = await vscode.window.showWarningMessage(
                    `Delete the connection '${node.uri}'?`,
                    { modal: true, detail },
                    "Delete",
                );
                if (confirmed !== "Delete") {
                    return;
                }

                await guard(log, async () => {
                    await deleteConnection(await connections.api(), {
                        uri: node.uri,
                        kind: node.connectionKind,
                    });
                    log(`Deleted the connection '${node.uri}'.`);
                    tree.refresh();
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.retryConnection",
            async (node?: IConnectionStatusNode) => {
                if (node?.kind === "connectionStatus") {
                    await tree.retry(node);
                }
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.copyConnectionUri",
            async (node?: IConnectionNode) => {
                if (node === undefined) {
                    return;
                }

                // The tree shows a shortened URI, so this is where the whole
                // of it - scheme and options included - can be had.
                await vscode.env.clipboard.writeText(node.uri);
                vscode.window.setStatusBarMessage(
                    "MariaDB: Copied the connection URI.", 3000);
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.connect",
            async (node?: IConnectionNode) => {
                await guard(log, async () => {
                    const uri = node?.uri
                        ?? connections.defaultConnection;
                    if (uri === undefined) {
                        throw new Error("No connection was given.");
                    }
                    // The tree's own connection: connecting here is what
                    // the Connections view then browses on.
                    await connections.connect(uri, UI_BACKEND_SESSION);
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.disconnect",
            async (node?: IConnectionNode) => {
                await guard(log, async () => {
                    if (node?.uri !== undefined) {
                        await connections.disconnect(node.uri);
                    }
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.newSqlEditor",
            async (node?: IConnectionNode) => {
                await guard(log, async () => {
                    const uri = node?.uri
                        ?? connections.defaultConnection;
                    if (uri === undefined) {
                        throw new Error("No connection was given.");
                    }
                    await editors.openSqlEditor(uri);
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.setDefaultConnection",
            async (node?: IConnectionNode) => {
                await guard(log, async () => {
                    if (node?.uri === undefined) {
                        return;
                    }
                    await connections.setDefaultConnection(node.uri);
                    editors.updateStatusBar();
                    void vscode.window.showInformationMessage(
                        `MariaDB: ${node.uri} is now the default connection.`,
                    );
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.clearDefaultConnection",
            async () => {
                await guard(log, async () => {
                    await connections.setDefaultConnection(undefined);
                    editors.updateStatusBar();
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.clearResultView",
            async () => {
                await guard(log, async () => {
                    await resultView.clear();
                });
            },
        ),

        vscode.commands.registerCommand(
            "mariadb.selectEditorConnection",
            async () => {
                const editor = vscode.window.activeTextEditor;
                if (!editor) {
                    return;
                }

                await guard(log, async () => {
                    await editors.selectConnection(editor.document);
                });
            },
        ),

        vscode.commands.registerCommand("mariadb.runSqlFile", async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                return;
            }

            await editors.run(editor);
        }),

        vscode.commands.registerCommand(
            "mariadb.runSqlStatement",
            async () => {
                const editor = vscode.window.activeTextEditor;
                if (!editor) {
                    return;
                }

                await editors.runStatementAtCursor(editor);
            },
        ),

        // One button, two commands: VS Code cannot swap a command's
        // icon, so which of the pair the toolbar shows is what changes.
        ...["mariadb.stopOnError.disable", "mariadb.stopOnError.enable"]
            .map((command) => {
                return vscode.commands.registerCommand(command, () => {
                    const editor = vscode.window.activeTextEditor;
                    if (!editor) {
                        return;
                    }

                    const now = editors.toggleStopOnError(editor.document);
                    void vscode.window.setStatusBarMessage(
                        now
                            ? "MariaDB: this file will stop at the first "
                            + "failing statement."
                            : "MariaDB: this file will run every statement, "
                            + "failures and all.",
                        4000,
                    );
                });
            }),

        vscode.commands.registerCommand(
            "mariadb.restartMcpServer",
            async () => {
                await guard(log, async () => {
                    await connections.disconnectAll();
                    await session.stop();
                    starter.stopped();
                    // Not refreshed on a failure: the tree would ask for
                    // its roots, and that would start a second attempt.
                    // The view says the start failed without it.
                    await starter.start();
                    tree.refresh();
                });
            },
        ),

        vscode.commands.registerCommand("mariadb.showMcpServerLog", () => {
            output.show(true);
        }),
    );

    // Nothing is started here: the server comes up the first time
    // something actually needs it, which keeps an editor that never
    // touches MariaDB from paying for a shell process.
    log("MariaDB extension activated.");
};

/**
 * Deactivates the extension, closing the connections and the MCP server.
 *
 * @returns A promise that settles once everything is down.
 */
export const deactivate = async (): Promise<void> => {
    const current = state;
    state = undefined;
    if (!current) {
        return;
    }

    // Before the session goes: the panel's buttons reach for the API, and
    // one left open across a reload would be bound to a server that is gone.
    ConnectionEditorPanel.disposeCurrent();

    await current.connections.disconnectAll();
    await current.session.stop();
};
