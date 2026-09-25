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
    env,
    treeViews,
    inputBoxAnswers,
    inputBoxCalls,
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
        /** What `sandbox.list_instances` answers with. */
        sandboxes: [] as Array<{
            port: number;
            version: string | null;
            status: string;
        }>,
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
                    // Both lists at once, each entry naming its list, as a
                    // server that knows `kind: "all"` answers.
                    if (args.kind === "all") {
                        return {
                            content: [
                                ...runtime.connections.map((uri) => {
                                    return { uri, path: "/", kind: "mcp" };
                                }),
                                ...runtime.guiConnections.map((uri) => {
                                    return { uri, path: "/", kind: "gui" };
                                }),
                            ].map((entry) => {
                                return {
                                    type: "text",
                                    text: JSON.stringify(entry),
                                };
                            }),
                        };
                    }

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

                if (name === "sandbox.list_instances") {
                    return {
                        content: runtime.sandboxes.filter((instance) => {
                            return args.port === undefined
                                || instance.port === args.port;
                        }).map((instance) => {
                            return {
                                type: "text",
                                text: JSON.stringify(instance),
                            };
                        }),
                    };
                }

                if (name.startsWith("sandbox.")) {
                    const text = `${name} on port ${String(args.port)}`;

                    return {
                        content: [{ type: "text", text }],
                        structuredContent: { result: text },
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
        runtime.sandboxes = [];
    });

    it("registers the Connections and Sandboxes views and every command", () => {
        const context = createContext();

        activate(context as never);

        expect(treeViews.map((view) => {
            return view.id;
        })).toEqual(["mariadb.connections", "mariadb.sandboxes"]);
        expect([...registeredCommands.keys()].sort()).toEqual([
            "mariadb.addConnection",
            "mariadb.addSandbox",
            "mariadb.clearDefaultConnection",
            "mariadb.clearResultView",
            "mariadb.connect",
            "mariadb.copyConnectionUri",
            "mariadb.deleteConnection",
            "mariadb.deleteSandbox",
            "mariadb.disconnect",
            "mariadb.editConnection",
            "mariadb.newFolder",
            "mariadb.newFolderWithSelection",
            "mariadb.newSqlEditor",
            "mariadb.refreshConnections",
            "mariadb.refreshSandboxes",
            "mariadb.removeFolder",
            "mariadb.renameFolder",
            "mariadb.restartMcpServer",
            "mariadb.retryConnection",
            "mariadb.runSqlFile",
            "mariadb.runSqlStatement",
            "mariadb.selectEditorConnection",
            "mariadb.setDefaultConnection",
            "mariadb.showMcpServerLog",
            "mariadb.startSandbox",
            "mariadb.stopOnError.disable",
            "mariadb.stopOnError.enable",
            "mariadb.stopSandbox",
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

    it("reads the connection list again only when Refresh is pressed",
        async () => {
            activate(createContext() as never);
            const provider = (treeViews[0]!.options as {
                treeDataProvider: { getChildren(): Promise<unknown[]> };
            }).treeDataProvider;
            const reads = () => {
                return (runtime.connector?.connections ?? []).flatMap(
                    (connection) => {
                        return connection.calls.filter((call) => {
                            return call.name === "db.list_connections";
                        });
                    }).length;
            };

            await provider.getChildren();
            await provider.getChildren();
            // One call for both lists.
            expect(reads()).toBe(1);

            await mockCommands.executeCommand("mariadb.refreshConnections");
            await provider.getChildren();
            expect(reads()).toBe(2);
        });

    it("lets several connections be picked and dragged", () => {
        activate(createContext() as never);

        const options = treeViews[0]!.options as {
            canSelectMany?: boolean;
            dragAndDropController?: unknown;
            treeDataProvider?: unknown;
        };
        expect(options.canSelectMany).toBe(true);
        expect(options.dragAndDropController)
            .toBe(options.treeDataProvider);
    });

    it("starts a new connection in the folder of the row it came from",
        async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.addConnection", {
                kind: "connection", uri: "dba@localhost:3310",
                path: "/Sandboxes", connected: false, isDefault: false,
                connectionKind: "mcp", expandable: false,
            });

            webviewPanels[0].webview.receive({ type: "ready" });
            await vi.waitFor(() => {
                expect(webviewPanels[0].webview.posted[0])
                    .toMatchObject({ path: "/Sandboxes" });
            });
        });

    /**
     * @returns The Connections view's provider.
     */
    const treeProvider = () => {
        return (treeViews[0]!.options as {
            treeDataProvider: {
                getChildren(node?: unknown): Promise<Array<{
                    kind: string;
                    path?: string;
                    empty?: boolean;
                }>>;
            };
        }).treeDataProvider;
    };

    it("makes a folder inside the one it was asked from", async () => {
        activate(createContext() as never);
        inputBoxAnswers.push("note app/v2");

        await mockCommands.executeCommand("mariadb.newFolder", {
            kind: "folder", path: "/Sandboxes", name: "Sandboxes", empty: true,
        });

        expect(inputBoxCalls[0]!.prompt).toContain("in /Sandboxes");
        const roots = await treeProvider().getChildren();
        expect(roots[0]).toMatchObject({
            kind: "folder", path: "/Sandboxes", empty: true,
        });
        const [inner] = await treeProvider().getChildren(roots[0]);
        expect(inner).toMatchObject({ path: "/Sandboxes/note app" });
    });

    it("refuses an empty name or one with a colon, and makes nothing on cancel",
        async () => {
            activate(createContext() as never);
            inputBoxAnswers.push(undefined);

            await mockCommands.executeCommand("mariadb.newFolder");

            const validate = inputBoxCalls[0]!.validateInput!;
            expect(validate(" / ")).toBe("Enter a folder name.");
            expect(validate("a:b")).toContain("contains a ':'");
            expect(validate("Work")).toBeUndefined();
            expect((await treeProvider().getChildren()).filter((node) => {
                return node.kind === "folder";
            })).toEqual([]);
        });

    it("renames a folder to a name that stays where it is", async () => {
        activate(createContext() as never);
        inputBoxAnswers.push("Old");
        await mockCommands.executeCommand("mariadb.newFolder");
        inputBoxAnswers.push(" New ");

        await mockCommands.executeCommand("mariadb.renameFolder", {
            kind: "folder", path: "/Old", name: "Old", empty: true,
        });

        const validate = inputBoxCalls[1]!.validateInput!;
        expect(validate("")).toBe("Enter a folder name.");
        expect(validate("a/b")).toContain("cannot contain '/'");
        expect(validate("a:b")).toContain("contains a ':'");
        expect((await treeProvider().getChildren())[0])
            .toMatchObject({ path: "/New" });
    });

    it("renames nothing on cancel, or without a folder", async () => {
        activate(createContext() as never);
        inputBoxAnswers.push("Old");
        await mockCommands.executeCommand("mariadb.newFolder");
        inputBoxAnswers.push(undefined);

        await mockCommands.executeCommand("mariadb.renameFolder", {
            kind: "folder", path: "/Old", name: "Old", empty: true,
        });
        await mockCommands.executeCommand("mariadb.renameFolder");

        expect((await treeProvider().getChildren())[0])
            .toMatchObject({ path: "/Old" });
        expect(inputBoxCalls).toHaveLength(2);
    });

    /**
     * @param uri The connection.
     * @param path Its folder.
     *
     * @returns Its row.
     */
    const connectionRow = (uri: string, path: string) => {
        return {
            kind: "connection", uri, path, connected: false, isDefault: false,
            connectionKind: "mcp", expandable: false,
        };
    };

    /**
     * @returns Each folder move sent to the server, as uri and folder.
     */
    const folderMoves = () => {
        return (runtime.connector?.connections ?? []).flatMap((connection) => {
            return connection.calls.filter((call) => {
                return call.name === "db.update_connection";
            }).map((call) => {
                return [call.args.uri, call.args.new_path];
            });
        });
    };

    it("files the selection in a new folder inside the one it is in",
        async () => {
            activate(createContext() as never);
            inputBoxAnswers.push("Picked");
            const first = connectionRow("dba@localhost:3310", "/Sandboxes");
            const second = connectionRow("app@localhost:3311", "/Sandboxes");

            await mockCommands.executeCommand(
                "mariadb.newFolderWithSelection", first,
                [first, second, { kind: "folder", path: "/X", name: "X" }]);

            expect(inputBoxCalls[0]!.prompt).toContain("in /Sandboxes");
            expect(folderMoves()).toEqual([
                ["dba@localhost:3310", "/Sandboxes/Picked"],
                ["app@localhost:3311", "/Sandboxes/Picked"],
            ]);
        });

    it("puts the new folder in the deepest folder a mixed selection shares",
        async () => {
            activate(createContext() as never);
            inputBoxAnswers.push("Both");
            const first = connectionRow("dba@localhost:3310", "/A/B");
            const second = connectionRow("app@localhost:3311", "/A/C");

            await mockCommands.executeCommand(
                "mariadb.newFolderWithSelection", first, [first, second]);

            expect(folderMoves()).toEqual([
                ["dba@localhost:3310", "/A/Both"],
                ["app@localhost:3311", "/A/Both"],
            ]);
        });

    it("files the one row a menu was opened on, at the top level",
        async () => {
            activate(createContext() as never);
            inputBoxAnswers.push("Solo");

            await mockCommands.executeCommand(
                "mariadb.newFolderWithSelection",
                connectionRow("dba@localhost:3310", "/"));

            expect(inputBoxCalls[0]!.prompt).not.toContain(" in ");
            expect(folderMoves()).toEqual([["dba@localhost:3310", "/Solo"]]);
        });

    it("moves nothing on cancel, or without a connection selected",
        async () => {
            activate(createContext() as never);
            inputBoxAnswers.push(undefined);

            await mockCommands.executeCommand(
                "mariadb.newFolderWithSelection",
                connectionRow("dba@localhost:3310", "/"));
            await mockCommands.executeCommand(
                "mariadb.newFolderWithSelection",
                { kind: "folder", path: "/X", name: "X" });

            expect(inputBoxCalls).toHaveLength(1);
            expect(folderMoves()).toEqual([]);
        });

    it("removes an empty folder, and only an empty one", async () => {
        activate(createContext() as never);
        inputBoxAnswers.push("Old");
        await mockCommands.executeCommand("mariadb.newFolder");

        await mockCommands.executeCommand("mariadb.removeFolder", {
            kind: "folder", path: "/Old", name: "Old", empty: false,
        });
        expect((await treeProvider().getChildren())[0])
            .toMatchObject({ path: "/Old" });

        await mockCommands.executeCommand("mariadb.removeFolder", {
            kind: "folder", path: "/Old", name: "Old", empty: true,
        });
        expect((await treeProvider().getChildren()).filter((node) => {
            return node.kind === "folder";
        })).toEqual([]);
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

    describe("sandboxes", () => {
        /** The Sandboxes view's provider, as activation registered it. */
        const sandboxesProvider = (): {
            getChildren(): Promise<Array<{ port: number; status: string }>>;
        } => {
            const view = treeViews.find((candidate) => {
                return candidate.id === "mariadb.sandboxes";
            });

            return (view!.options as {
                treeDataProvider: ReturnType<typeof sandboxesProvider>;
            }).treeDataProvider;
        };

        const running = {
            kind: "sandbox", port: 3310, version: "12.3.2", status: "running",
        };
        const stopped = { ...running, status: "stopped" };

        const sandboxCalls = (): Array<{ name: string; args: unknown }> => {
            return toolCalls().filter((call) => {
                return call.name.startsWith("sandbox.");
            });
        };

        it("lists the sandboxes, starting the server to do it", async () => {
            runtime.sandboxes = [
                { port: 3310, version: "12.3.2", status: "running" },
            ];
            activate(createContext() as never);

            const rows = await sandboxesProvider().getChildren();

            expect(rows).toEqual([{
                kind: "sandbox", port: 3310, version: "12.3.2",
                status: "running",
            }]);
            expect(contextKeys.get("mariadb.sandboxesView")).toBe("listed");
        });

        it("opens the New Sandbox dialog", () => {
            activate(createContext() as never);

            void mockCommands.executeCommand("mariadb.addSandbox");

            expect(webviewPanels.map((panel) => {
                return panel.viewType;
            })).toEqual(["mariadb.sandboxEditor"]);
        });

        it("starts a stopped sandbox from its row", async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.startSandbox", stopped);

            expect(sandboxCalls()).toContainEqual({
                name: "sandbox.start", args: { port: 3310 },
            });
            expect(errorMessages).toEqual([]);
        });

        it("closes what is open on a sandbox before stopping it", async () => {
            activate(createContext() as never);
            // The connection deploy registered for it, spelled as the
            // plugin stores it.
            await mockCommands.executeCommand("mariadb.connect", {
                kind: "connection", uri: "mariadb://root@127.0.0.1:3310",
            });

            await mockCommands.executeCommand("mariadb.stopSandbox", running);

            const names = toolCalls().map((call) => { return call.name; });
            expect(names).toContain("sandbox.stop");
            expect(names.indexOf("db.close"))
                .toBeLessThan(names.indexOf("sandbox.stop"));
            expect(names.indexOf("db.close")).toBeGreaterThanOrEqual(0);
        });

        it("leaves other connections open when a sandbox stops", async () => {
            activate(createContext() as never);
            await mockCommands.executeCommand("mariadb.connect", {
                kind: "connection", uri: "mariadb://root@127.0.0.1:3320",
            });

            await mockCommands.executeCommand("mariadb.stopSandbox", running);

            expect(toolCalls().map((call) => { return call.name; }))
                .not.toContain("db.close");
        });

        it("asks before deleting a sandbox, and stops if told no",
            async () => {
                activate(createContext() as never);

                await mockCommands.executeCommand(
                    "mariadb.deleteSandbox", running);

                expect(warningMessages).toEqual([
                    "Delete the sandbox on port 3310?",
                ]);
                expect(sandboxCalls()).toEqual([]);
            });

        it("stops a running sandbox before deleting it, then re-reads the "
            + "connections", async () => {
            activate(createContext() as never);
            setWarningMessageAnswer("Delete");
            const tree = (treeViews[0].options as {
                treeDataProvider: { getChildren(): Promise<unknown[]> };
            }).treeDataProvider;
            await tree.getChildren();
            const listed = (): number => {
                return toolCalls().filter((call) => {
                    return call.name === "db.list_connections";
                }).length;
            };
            const before = listed();

            await mockCommands.executeCommand("mariadb.deleteSandbox", running);

            expect(sandboxCalls().map((call) => { return call.name; }))
                .toEqual(["sandbox.stop", "sandbox.delete"]);
            // The delete took its connection away server-side.
            await tree.getChildren();
            expect(listed()).toBeGreaterThan(before);
            expect(errorMessages).toEqual([]);
        });

        it("deletes a stopped sandbox without stopping it", async () => {
            activate(createContext() as never);
            setWarningMessageAnswer("Delete");

            await mockCommands.executeCommand("mariadb.deleteSandbox", stopped);

            expect(sandboxCalls().map((call) => { return call.name; }))
                .toEqual(["sandbox.delete"]);
        });

        it("ignores the row commands without a sandbox", async () => {
            activate(createContext() as never);

            for (const command of [
                "mariadb.startSandbox",
                "mariadb.stopSandbox",
                "mariadb.deleteSandbox",
            ]) {
                await mockCommands.executeCommand(command);
                await mockCommands.executeCommand(command, {
                    kind: "connection", uri: "dba@localhost:3310",
                });
            }

            expect(sandboxCalls()).toEqual([]);
            expect(warningMessages).toEqual([]);
        });

        it("lists the sandboxes once, and again only on Refresh", async () => {
            runtime.sandboxes = [
                { port: 3310, version: "12.3.2", status: "running" },
            ];
            activate(createContext() as never);
            const lists = (): number => {
                return sandboxCalls().filter((call) => {
                    return call.name === "sandbox.list_instances";
                }).length;
            };

            await sandboxesProvider().getChildren();
            await sandboxesProvider().getChildren();
            // An action redraws the view twice; neither redraw lists.
            await mockCommands.executeCommand("mariadb.stopSandbox", running);
            expect((await sandboxesProvider().getChildren())[0]?.status)
                .toBe("stopped");
            expect(lists()).toBe(1);

            await mockCommands.executeCommand("mariadb.refreshSandboxes");
            await sandboxesProvider().getChildren();
            expect(lists()).toBe(2);
        });

        it("re-reads the connections once a deploy has succeeded", async () => {
            activate(createContext() as never);
            const tree = (treeViews[0].options as {
                treeDataProvider: { getChildren(): Promise<unknown[]> };
            }).treeDataProvider;
            await tree.getChildren();
            let redraws = 0;
            (tree as unknown as {
                onDidChangeTreeData(listener: () => void): void;
            }).onDidChangeTreeData(() => { redraws += 1; });

            void mockCommands.executeCommand("mariadb.addSandbox");
            webviewPanels.at(-1)!.webview.receive({
                type: "create",
                fields: {
                    port: "3399", password: "", passwordConfirmation: "",
                    serverVersion: "Server on the PATH",
                    allowRootFrom: "127.0.0.1", serverId: "", ssl: false,
                    mariadbdOptions: "", timeout: "",
                },
            });
            await vi.waitFor(() => {
                expect(webviewPanels.at(-1)!.disposed).toBe(true);
            });
            // The deploy registered its connection in /Sandboxes on the
            // server; the tree reads the list again to show it.
            expect(redraws).toBeGreaterThan(0);
            await tree.getChildren();

            const names = toolCalls().map((call) => { return call.name; });
            expect(names.lastIndexOf("db.list_connections"))
                .toBeGreaterThan(names.indexOf("sandbox.deploy"));
            // The new sandbox is asked about alone, not the whole list.
            expect(sandboxCalls().filter((call) => {
                return call.name === "sandbox.list_instances";
            }).map((call) => { return call.args; }))
                .toEqual([{ port: 3399 }]);
            expect(informationMessages).toEqual([
                "MariaDB: sandbox.deploy on port 3399",
            ]);
        });

        it("suggests 3311 when a connection is on localhost:3310", async () => {
            runtime.connections = ["mariadb://dba@localhost:3310"];
            activate(createContext() as never);

            void mockCommands.executeCommand("mariadb.addSandbox");
            webviewPanels.at(-1)!.webview.receive({ type: "ready" });

            await vi.waitFor(() => {
                expect(webviewPanels.at(-1)!.webview.posted[0])
                    .toMatchObject({ type: "load", fields: { port: "3311" } });
            });
        });

        it("lists the server versions once, however often the dialog opens",
            async () => {
                activate(createContext() as never);

                for (let open = 0; open < 2; open += 1) {
                    void mockCommands.executeCommand("mariadb.addSandbox");
                    webviewPanels.at(-1)!.webview.receive({ type: "ready" });
                    await vi.waitFor(() => {
                        expect(webviewPanels.at(-1)!.webview.posted)
                            .toHaveLength(1);
                    });
                    webviewPanels.at(-1)!.dispose();
                }

                expect(sandboxCalls().filter((call) => {
                    return call.name === "sandbox.list_available_versions";
                })).toHaveLength(1);
            });

        it("logs the sandbox calls as General Actions when asked to, and "
            + "shows them", async () => {
            configuration.set("mariadb.actions.logAllCalls", true);
            activate(createContext() as never);
            const view = resolveWebviewView("mariadb.results");
            view.webview.receive({ type: "ready" });

            await sandboxesProvider().getChildren();
            await mockCommands.executeCommand("mariadb.startSandbox", stopped);

            // The first action logged is what the panel comes up on,
            // rather than an empty grid.
            await vi.waitFor(() => {
                expect(view.description).toBe("General Actions");
            });
            const message = [...view.webview.posted].reverse().find((posted) => {
                return (posted as { type: string }).type === "state";
            }) as { state: { actions: Array<{ statement: string }> } }
                | undefined;
            // Newest first. The panel's own look at the connection list,
            // for its picker, is a general action too, and is left out.
            expect(message?.state.actions.map((row) => {
                return row.statement;
            }).filter((call) => { return call.startsWith("sandbox."); }))
                .toEqual([
                    "sandbox.start(port=3310)",
                    "sandbox.list_instances()",
                ]);
        });

        it("logs no sandbox call while the setting is off", async () => {
            activate(createContext() as never);
            const view = resolveWebviewView("mariadb.results");
            view.webview.receive({ type: "ready" });

            await sandboxesProvider().getChildren();
            await mockCommands.executeCommand("mariadb.startSandbox", stopped);

            expect(view.description ?? "").not.toBe("General Actions");
        });

        it("refreshes the Sandboxes view on request", async () => {
            activate(createContext() as never);
            await sandboxesProvider().getChildren();
            runtime.sandboxes = [
                { port: 3320, version: null, status: "stopped" },
            ];

            await mockCommands.executeCommand("mariadb.refreshSandboxes");

            expect((await sandboxesProvider().getChildren()).map((row) => {
                return row.port;
            })).toEqual([3320]);
        });
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

    it("copies a connection's whole URI, which the tree shortens",
        async () => {
            activate(createContext() as never);

            await mockCommands.executeCommand("mariadb.copyConnectionUri", {
                kind: "connection",
                uri: "mariadb+ssh://dba@db:3310/world?ssh-host=bastion",
            });

            expect(env.clipboard.text)
                .toBe("mariadb+ssh://dba@db:3310/world?ssh-host=bastion");
            expect(statusBarMessages.at(-1)).toContain("Copied");
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
