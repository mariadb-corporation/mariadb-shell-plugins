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

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FakeEnvironmentOptions } from "./helpers.js";
import {
    commands as mockCommands,
    configuration,
    configurationUpdates,
    contextKeys,
    ConfigurationTarget,
    errorMessages,
    fireActiveEditorChange,
    fireConfigurationChange,
    informationMessages,
    MockTextDocument,
    openedDocuments,
    outputChannels,
    registeredCommands,
    setWarningMessageAnswer,
    warningMessages,
    webviewPanels,
    shownDocuments,
    resetVscodeMock,
    resolveWebviewView,
    setWorkspaceFolders,
    statusBarItems,
    statusBarMessages,
    treeViews,
    Uri,
    withProgressCalls,
} from "./mocks/vscode.js";

// The Node backed runtime and the MCP SDK are replaced wholesale, so
// activating the extension never touches the file system, spawns a process
// or opens a connection.
const runtime = vi.hoisted(() => {
    return {
        environmentOptions: {} as FakeEnvironmentOptions,
        environmentAfterInstall: undefined as
            | FakeEnvironmentOptions
            | undefined,
        runnerOutput: [] as string[],
        runnerExitCode: 0,
        connector: undefined as
            | ReturnType<
                typeof import("./helpers.js")["createFakeConnector"]
            >
            | undefined,
        runner: undefined as
            | ReturnType<typeof import("./helpers.js")["createFakeRunner"]>
            | undefined,
        /**
         * What the fake MCP server answers `db.list_connections` with, per
         * connection list. The extension asks for both kinds, so a double
         * that ignored the argument would report every connection twice.
         */
        connections: [] as string[],
        guiConnections: [] as string[],
    };
});

vi.mock("../shell/nodeRuntime.js", async () => {
    const helpers = await import("./helpers.js");

    return {
        createNodeShellEnvironment: () => {
            const before = helpers.createFakeEnvironment(
                runtime.environmentOptions);
            if (!runtime.environmentAfterInstall) {
                return before;
            }

            const after = helpers.createFakeEnvironment(
                runtime.environmentAfterInstall);
            const current = () => {
                return (runtime.runner?.calls.length ?? 0) > 0
                    ? after
                    : before;
            };

            return {
                ...before,
                probeVersion: (binaryPath: string) => {
                    return current().probeVersion(binaryPath);
                },
                pathExists: (target: string) => {
                    return current().pathExists(target);
                },
                listDirectories: (target: string) => {
                    return current().listDirectories(target);
                },
            };
        },
        createNodeProcessRunner: () => {
            runtime.runner = helpers.createFakeRunner(
                runtime.runnerOutput, runtime.runnerExitCode);

            return runtime.runner;
        },
    };
});

vi.mock("../mcp/sdkConnector.js", async () => {
    const helpers = await import("./helpers.js");

    return {
        createSdkConnector: () => {
            runtime.connector = helpers.createFakeConnector((name, args) => {
                if (name === "db.list_connections") {
                    const uris = args.kind === "gui"
                        ? runtime.guiConnections
                        : runtime.connections;

                    return {
                        content: uris.map((uri) => {
                            return { type: "text", text: uri };
                        }),
                    };
                }

                if (name === "db.delete_connection"
                    || name === "db.add_connection"
                    || name === "db.update_connection") {
                    const uri = String(args.new_uri ?? args.uri);

                    return {
                        content: [{ type: "text", text: uri }],
                        structuredContent: { result: uri },
                    };
                }

                if (name === "db.test_connection") {
                    return {
                        content: [{ type: "text", text: "Connected." }],
                        structuredContent: { result: "Connected." },
                    };
                }

                if (name === "db.connect") {
                    return {
                        content: [{
                            type: "text",
                            text: `uuid-for-${String(args.uri)}`,
                        }],
                    };
                }

                return { content: [] };
            });

            return runtime.connector;
        },
    };
});

const { activate, deactivate } = await import("../extension.js");

const versionLine = (version: string): string => {
    return `mariadb-shell   Ver ${version} for osx10.21 on arm64 `
        + "- for MariaDB 13.1.0-MariaDB (Source distribution)";
};

interface FakeContext {
    subscriptions: { dispose(): unknown }[];
    extensionUri: unknown;
}

/**
 * @returns The slice of the extension context the extension uses.
 */
const createContext = (): FakeContext => {
    return { subscriptions: [], extensionUri: Uri.file("/ext") };
};

describe("activate", () => {
    beforeEach(() => {
        resetVscodeMock();
        runtime.environmentOptions = {
            versions: { "mariadb-shell": versionLine("26.9.4") },
        };
        runtime.environmentAfterInstall = undefined;
        runtime.runnerOutput = [];
        runtime.runnerExitCode = 0;
        runtime.runner = undefined;
        runtime.connector = undefined;
        runtime.connections = ["dba@localhost:3310"];
        runtime.guiConnections = [];
    });

    it("registers the Connections view and every command", () => {
        const context = createContext();

        activate(context as never);

        expect(treeViews.map((view) => {
            return view.id;
        })).toEqual(["mariadb.connections"]);
        expect([...registeredCommands.keys()].sort()).toEqual([
            "mariadb.addConnection",
            "mariadb.clearDefaultConnection",
            "mariadb.clearResultView",
            "mariadb.connect",
            "mariadb.deleteConnection",
            "mariadb.disconnect",
            "mariadb.editConnection",
            "mariadb.newSqlEditor",
            "mariadb.refreshConnections",
            "mariadb.restartMcpServer",
            "mariadb.runSqlFile",
            "mariadb.runSqlStatement",
            "mariadb.selectEditorConnection",
            "mariadb.setDefaultConnection",
            "mariadb.showMcpServerLog",
            "mariadb.stopOnError.disable",
            "mariadb.stopOnError.enable",
        ]);
    });

    it("publishes the keyboard shortcut choices as context keys",
        async () => {
            activate(createContext() as never);

            // The contributed keybindings are gated on these; without
            // them nothing is bound at all.
            await vi.waitFor(() => {
                expect(contextKeys.get("mariadb.chord.runScript"))
                    .toBe("cmdOrCtrl+enter");
            });
            expect(contextKeys.get("mariadb.chord.runStatement"))
                .toBe("shift+enter");
        });

    it("follows a change to the shortcut settings", async () => {
        activate(createContext() as never);
        await vi.waitFor(() => {
            expect(contextKeys.get("mariadb.chord.runScript")).toBeDefined();
        });

        configuration.set("mariadb.keybindings.runScript", "f5");
        fireConfigurationChange("mariadb.keybindings.runScript");

        await vi.waitFor(() => {
            expect(contextKeys.get("mariadb.chord.runScript")).toBe("f5");
        });
    });

    it("toggles a file's stop-on-error from the toolbar", async () => {
        activate(createContext() as never);
        const document = new MockTextDocument(
            Uri.file("/work/query.sql"), "sql", "SELECT 1;");
        fireActiveEditorChange({
            document,
            selection: { isEmpty: true },
        });
        expect(contextKeys.get("mariadb.stopOnError")).toBe(true);

        // Whichever of the pair is on show, clicking it flips the state.
        await mockCommands.executeCommand("mariadb.stopOnError.disable");
        expect(contextKeys.get("mariadb.stopOnError")).toBe(false);
        expect(statusBarMessages.at(-1)).toContain("every statement");

        await mockCommands.executeCommand("mariadb.stopOnError.enable");
        expect(contextKeys.get("mariadb.stopOnError")).toBe(true);
        expect(statusBarMessages.at(-1)).toContain("first failing");
    });

    it("does nothing toggling with no editor open", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.stopOnError.disable");

        expect(statusBarMessages).toEqual([]);
        expect(errorMessages).toEqual([]);
    });

    it("clears the result view from its toolbar", async () => {
        runtime.connections = ["dba@localhost:3310"];
        activate(createContext() as never);
        const view = resolveWebviewView("mariadb.results");
        view.webview.receive({ type: "ready" });

        await mockCommands.executeCommand("mariadb.clearResultView");

        // Nothing has run, so there is nothing to clear and nothing to
        // go wrong; the command is still safe to invoke.
        expect(errorMessages).toEqual([]);
    });

    /** Every tool call made on every MCP connection the fake handed out. */
    const toolCalls = (): Array<{ name: string; args: unknown }> => {
        return (runtime.connector?.connections ?? []).flatMap((connection) => {
            return connection.calls;
        });
    };

    it("opens the connection editor on a new connection", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.addConnection");

        expect(webviewPanels).toHaveLength(1);
        expect(webviewPanels[0].viewType).toBe("mariadb.connectionEditor");
        expect(webviewPanels[0].title).toBe("New Database Connection");
    });

    it("opens the editor on the connection the menu was used on", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.editConnection", {
            kind: "connection",
            uri: "dba@localhost:3310",
            connected: false,
            isDefault: false,
            connectionKind: "mcp",
        });

        expect(webviewPanels[0].title).toBe("Edit dba@localhost:3310");

        // The node carries the list it is in, and the editor has to be told:
        // it decides whether the MCP box comes up ticked, and which list a
        // save updates.
        webviewPanels[0].webview.receive({ type: "ready" });
        await vi.waitFor(() => {
            expect(webviewPanels[0].webview.posted[0])
                .toMatchObject({ mcpAccess: true });
        });
    });

    it("does nothing when edit or delete arrive without a connection",
        async () => {
            // Both are menu entries on a node; the palette cannot supply one.
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.editConnection");
            await mockCommands.executeCommand("mariadb.deleteConnection");

            expect(webviewPanels).toEqual([]);
            expect(errorMessages).toEqual([]);
        });

    it("asks before deleting a connection, and stops if told no", async () => {
        activate(createContext() as never);

        // The mock dismisses the dialog unless a test says otherwise, which
        // is what this one wants: a stored credential is about to be thrown
        // away and there is no undo.
        await mockCommands.executeCommand("mariadb.deleteConnection", {
            kind: "connection",
            uri: "dba@localhost:3310",
            connected: false,
            isDefault: false,
            connectionKind: "gui",
        });

        expect(warningMessages).toEqual([
            "Delete the connection 'dba@localhost:3310'?",
        ]);
        expect(toolCalls().map((call) => { return call.name; }))
            .not.toContain("db.delete_connection");
    });

    it("deletes the connection from its own list once confirmed", async () => {
        activate(createContext() as never);
        setWarningMessageAnswer("Delete");

        await mockCommands.executeCommand("mariadb.deleteConnection", {
            kind: "connection",
            uri: "shared@localhost:3310",
            connected: false,
            isDefault: false,
            connectionKind: "mcp",
        });

        await vi.waitFor(() => {
            expect(toolCalls()).toContainEqual({
                name: "db.delete_connection",
                args: { uri: "shared@localhost:3310", kind: "mcp" },
            });
        });
        expect(errorMessages).toEqual([]);
    });

    it("starts no shell until something needs the server", () => {
        activate(createContext() as never);

        // An editor that never touches MariaDB must not pay for a shell.
        expect(runtime.connector?.commands ?? []).toEqual([]);
        expect(outputChannels[0].lines.at(-1))
            .toMatch(/^\[[^\]]+\] MariaDB extension activated\.$/);
    });

    it("starts the server the first time the tree is read", async () => {
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: {
                getChildren(): Promise<Array<{ uri: string }>>;
            };
        }).treeDataProvider;

        const roots = await provider.getChildren();

        expect(runtime.connector?.commands).toEqual([{
            command: "mariadb-shell",
            args: [
                "--", "mcp", "start-server", "--transport=stdio", "--gui",
            ],
        }]);
        expect(roots.map((node) => {
            return node.uri;
        })).toEqual(["dba@localhost:3310"]);
    });

    it("installs the shell when there is none, then starts the server",
        async () => {
            const prefix = "/Users/mzinner/.local/share/mariadb-shell";
            const binary = `${prefix}/26.9.4/bin/mariadb-shell`;
            runtime.environmentOptions = {};
            runtime.environmentAfterInstall = {
                directories: { [prefix]: ["26.9.4"] },
                files: [binary],
                versions: { [binary]: versionLine("26.9.4") },
            };
            runtime.runnerOutput = ["==> Downloading", "==> Unpacking"];

            activate(createContext() as never);
            const provider = (treeViews[0].options as {
                treeDataProvider: { getChildren(): Promise<unknown[]> };
            }).treeDataProvider;
            await provider.getChildren();

            const install = withProgressCalls.find((call) => {
                return call.options.title !== undefined;
            });
            expect(install?.options.title)
                .toBe("Installing MariaDB Shell 26.9.4");
            expect(install?.reported).toContain("Downloading");
            // The view shows itself busy for the whole startup, and the
            // welcome content moves on from "installing" once it is done.
            expect(withProgressCalls[0].options.location)
                .toEqual({ viewId: "mariadb.connections" });
            expect(contextKeys.get("mariadb.connectionsView"))
                .toBe("listed");
            expect(informationMessages)
                .toEqual(["MariaDB Shell 26.9.4 was installed."]);
            expect(runtime.connector?.commands[0].command).toBe(binary);
        });

    it("shows an empty tree and reports why when the server fails",
        async () => {
            runtime.environmentOptions = {};
            runtime.runnerExitCode = 1;

            activate(createContext() as never);
            const provider = (treeViews[0].options as {
                treeDataProvider: { getChildren(): Promise<unknown[]> };
            }).treeDataProvider;

            await expect(provider.getChildren()).resolves.toEqual([]);
            expect(errorMessages[0]).toContain("exited with code 1");
            // The view says it failed and offers the log and a retry,
            // rather than claiming no connections are configured.
            expect(contextKeys.get("mariadb.connectionsView"))
                .toBe("failed");
            expect(outputChannels[0].lines.join("\n"))
                .toContain("The MCP server could not be started: "
                    + "MariaDB Shell 26.9.4 could not be installed");
        });

    it("stores the default connection in the settings", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.setDefaultConnection", {
            kind: "connection",
            uri: "dba@localhost:3310",
        });

        expect(configurationUpdates).toEqual([{
            key: "mariadb.defaultConnection",
            value: "dba@localhost:3310",
            target: ConfigurationTarget.Global,
        }]);
        expect(informationMessages[0])
            .toContain("is now the default connection");
    });

    it("stores the default in the workspace when there is one", async () => {
        setWorkspaceFolders([{ name: "project" }]);
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.setDefaultConnection", {
            kind: "connection",
            uri: "dba@localhost:3310",
        });

        expect(configurationUpdates[0].target)
            .toBe(ConfigurationTarget.Workspace);
    });

    it("loads the default connection from the settings", async () => {
        configuration.set("mariadb.defaultConnection", "app@localhost:3311");
        runtime.connections = ["dba@localhost:3310", "app@localhost:3311"];
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: {
                getChildren(): Promise<Array<{
                    uri: string;
                    isDefault: boolean;
                }>>;
            };
        }).treeDataProvider;

        const roots = await provider.getChildren();

        expect(roots.map((node) => {
            return node.isDefault;
        })).toEqual([false, true]);
    });

    it("clears the default connection", async () => {
        configuration.set("mariadb.defaultConnection", "dba@localhost:3310");
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.clearDefaultConnection");

        expect(configurationUpdates[0].value).toBeUndefined();
    });

    it("follows the setting when it is changed in the settings editor",
        () => {
            activate(createContext() as never);
            configuration.set("mariadb.defaultConnection",
                "dba@localhost:3310");

            fireConfigurationChange("mariadb.defaultConnection");

            // The status bar is hidden with no SQL editor open, but it has
            // been asked to redraw, which is what the listener is for.
            expect(statusBarItems).toHaveLength(1);
        });

    it("publishes the connect mode, and follows a change to it", async () => {
        activate(createContext() as never);

        // The Connect button is dropped from the row where connecting is
        // what opening the row does, which is the default.
        await vi.waitFor(() => {
            expect(contextKeys.get("mariadb.connectOnOpen")).toBe(true);
        });

        configuration.set("mariadb.connections.connectMode", "explicit");
        fireConfigurationChange("mariadb.connections.connectMode");

        await vi.waitFor(() => {
            expect(contextKeys.get("mariadb.connectOnOpen")).toBe(false);
        });
    });

    it("opens a connection the user expands in the tree", async () => {
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: {
                getChildren(node?: unknown): Promise<unknown[]>;
            };
        }).treeDataProvider;
        const [root] = await provider.getChildren();

        treeViews[0].expand(root);

        await vi.waitFor(() => {
            expect(runtime.connector?.connections[0].calls
                .filter((call) => {
                    return call.name === "db.connect";
                })).toHaveLength(1);
        });
    });

    it("leaves expanding alone in the explicit connect mode", async () => {
        configuration.set("mariadb.connections.connectMode", "explicit");
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: {
                getChildren(node?: unknown): Promise<unknown[]>;
            };
        }).treeDataProvider;
        const [root] = await provider.getChildren();

        treeViews[0].expand(root);
        await expect(provider.getChildren(root)).resolves.toEqual([]);

        expect(runtime.connector?.connections[0].calls
            .filter((call) => {
                return call.name === "db.connect";
            })).toEqual([]);
    });

    it("opens and closes a connection from the tree", async () => {
        activate(createContext() as never);
        const node = { kind: "connection", uri: "dba@localhost:3310" };

        await mockCommands.executeCommand("mariadb.connect", node);
        const connectCalls = runtime.connector?.connections[0].calls
            .filter((call) => {
                return call.name === "db.connect";
            });
        expect(connectCalls).toHaveLength(1);

        await mockCommands.executeCommand("mariadb.disconnect", node);
        const closeCalls = runtime.connector?.connections[0].calls
            .filter((call) => {
                return call.name === "db.close";
            });
        expect(closeCalls).toHaveLength(1);
    });

    it("reports a failure to connect rather than throwing", async () => {
        runtime.environmentOptions = {};
        runtime.runnerExitCode = 1;
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.connect", {
            kind: "connection",
            uri: "dba@localhost:3310",
        });

        expect(errorMessages[0]).toContain("exited with code 1");
    });

    it("restarts the MCP server", async () => {
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: { getChildren(): Promise<unknown[]> };
        }).treeDataProvider;
        await provider.getChildren();

        await mockCommands.executeCommand("mariadb.restartMcpServer");

        expect(runtime.connector?.commands).toHaveLength(2);
        expect(runtime.connector?.connections[0].closed).toBe(true);
    });

    it("shows the log on request", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.showMcpServerLog");

        expect(outputChannels[0].shown).toBe(true);
    });

    it("closes the connections and the server on deactivate", async () => {
        activate(createContext() as never);
        await mockCommands.executeCommand("mariadb.connect", {
            kind: "connection",
            uri: "dba@localhost:3310",
        });

        await deactivate();

        expect(runtime.connector?.connections[0].calls.some((call) => {
            return call.name === "db.close";
        })).toBe(true);
        expect(runtime.connector?.connections[0].closed).toBe(true);
    });

    it("says so when there is nothing to connect to", async () => {
        activate(createContext() as never);

        // Invoked from the palette, with no node and no default set.
        await mockCommands.executeCommand("mariadb.connect");

        expect(errorMessages).toEqual([
            "MariaDB: No connection was given.",
        ]);
    });

    it("connects to the default when invoked without a node", async () => {
        configuration.set("mariadb.defaultConnection", "dba@localhost:3310");
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.connect");

        expect(runtime.connector?.connections[0].calls.some((call) => {
            return call.name === "db.connect";
        })).toBe(true);
    });

    it("ignores a disconnect with no node", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.disconnect");

        expect(errorMessages).toEqual([]);
        // Nothing to close, so no server was started for it either.
        expect(runtime.connector?.commands).toEqual([]);
    });

    it("ignores a default-connection command with no node", async () => {
        activate(createContext() as never);

        await mockCommands.executeCommand("mariadb.setDefaultConnection");

        expect(configurationUpdates).toEqual([]);
    });

    it("does nothing in the editor commands with no editor open",
        async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand(
                "mariadb.selectEditorConnection");
            await mockCommands.executeCommand("mariadb.runSqlFile");

            expect(errorMessages).toEqual([]);
        });

    it("refreshes the tree on request", async () => {
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: {
                onDidChangeTreeData(listener: () => void): unknown;
            };
        }).treeDataProvider;
        let fired = 0;
        provider.onDidChangeTreeData(() => {
            fired += 1;
        });

        await mockCommands.executeCommand("mariadb.refreshConnections");

        expect(fired).toBe(1);
    });

    it("reports a failure that is not an Error", async () => {
        activate(createContext() as never);
        const provider = (treeViews[0].options as {
            treeDataProvider: { getChildren(): Promise<unknown[]> };
        }).treeDataProvider;
        await provider.getChildren();
        runtime.connector!.connections[0].callTool = () => {
            return Promise.reject("the pipe closed");
        };

        await mockCommands.executeCommand("mariadb.connect", {
            kind: "connection",
            uri: "dba@localhost:3310",
        });

        expect(errorMessages).toEqual(["MariaDB: the pipe closed"]);
    });

    it("opens a SQL editor bound to the connection it was invoked on",
        async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.newSqlEditor", {
                kind: "connection",
                uri: "dba@localhost:3310",
            });

            expect(openedDocuments).toHaveLength(1);
            expect(openedDocuments[0].languageId).toBe("sql");
            expect(openedDocuments[0].getText())
                .toContain("-- MariaDB connection: dba@localhost:3310");
            expect(shownDocuments).toEqual([openedDocuments[0]]);
        });

    it("opens a SQL editor on the default when invoked without a node",
        async () => {
            configuration.set(
                "mariadb.defaultConnection", "app@localhost:3311");
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.newSqlEditor");

            expect(openedDocuments[0].getText())
                .toContain("-- MariaDB connection: app@localhost:3311");
        });

    it("says so when there is no connection to open an editor for",
        async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.newSqlEditor");

            expect(openedDocuments).toEqual([]);
            expect(errorMessages)
                .toEqual(["MariaDB: No connection was given."]);
        });

    it("is safe to deactivate twice", async () => {
        activate(createContext() as never);
        await deactivate();

        await expect(deactivate()).resolves.toBeUndefined();
    });
});
