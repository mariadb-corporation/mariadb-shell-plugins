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

import { beforeEach, describe, expect, it } from "vitest";

import type {
    ISandboxApi,
    ISandboxDeployOptions,
} from "../../mcp/sandboxApi.js";
import {
    buildSandboxEditorHtml,
    SandboxEditorPanel,
    SANDBOX_EDITOR_VIEW_TYPE,
    type ISandboxEditorHost,
} from "../../sandboxes/sandboxEditorPanel.js";
import {
    emptySandboxFields,
    type ISandboxFields,
} from "../../sandboxes/sandboxFields.js";
import type {
    SandboxHostMessage,
    SandboxWebviewMessage,
} from "../../sandboxes/sandboxProtocol.js";
import { SandboxStore } from "../../sandboxes/sandboxStore.js";
import {
    errorMessages,
    MockWebview,
    resetVscodeMock,
    Uri,
    webviewPanels,
    type MockWebviewPanel,
} from "../mocks/vscode.js";

const extensionUri = Uri.file("/ext");

interface IHostState {
    host: ISandboxEditorHost;
    deployed: ISandboxDeployOptions[];
    created: string[];
    logs: string[];
    progressed: number;
}

/**
 * The host, plus what it recorded.
 *
 * @param options How the fake server behaves.
 *
 * @returns The host and its records.
 */
const createHost = (options: {
    taken?: number[];
    connections?: string[] | Error;
    versions?: string[] | Error;
    deploy?: (deploy: ISandboxDeployOptions) => Promise<string>;
} = {}): IHostState => {
    const state: IHostState = {
        host: undefined as unknown as ISandboxEditorHost,
        deployed: [],
        created: [],
        logs: [],
        progressed: 0,
    };

    const api: ISandboxApi = {
        listInstances: () => { return Promise.resolve([]); },
        listAvailableVersions: () => {
            return options.versions instanceof Error
                ? Promise.reject(options.versions)
                : Promise.resolve(options.versions ?? ["11.8.9", "12.3.2"]);
        },
        deploy: (deploy) => {
            state.deployed.push(deploy);

            return options.deploy?.(deploy)
                ?? Promise.resolve(`Deployed on port ${deploy.port}.`);
        },
        start: () => { return Promise.resolve(""); },
        stop: () => { return Promise.resolve(""); },
        delete: () => { return Promise.resolve(""); },
    };

    const store = new SandboxStore(() => { return Promise.resolve(api); });
    // The ports come from the list the view has read.
    Object.defineProperty(store, "ports", {
        get: () => { return options.taken ?? []; },
    });
    state.host = {
        store,
        ...(options.connections === undefined ? {} : {
            connectionUris: () => {
                return options.connections instanceof Error
                    ? Promise.reject(options.connections)
                    : Promise.resolve(options.connections!);
            },
        }),
        withProgress: async (work) => {
            state.progressed += 1;

            return await work();
        },
        onCreated: (message) => { state.created.push(message); },
        log: (message) => { state.logs.push(message); },
    };

    return state;
};

const currentPanel = (): MockWebviewPanel => {
    return webviewPanels[webviewPanels.length - 1]!;
};

const posted = (): SandboxHostMessage[] => {
    return currentPanel().webview.posted as SandboxHostMessage[];
};

const receive = async (message: SandboxWebviewMessage): Promise<void> => {
    currentPanel().webview.receive(message);
    await new Promise((resolve) => { setTimeout(resolve, 0); });
};

const fields = (overrides: Partial<ISandboxFields> = {}): ISandboxFields => {
    return { ...emptySandboxFields("3310"), ...overrides };
};

describe("buildSandboxEditorHtml", () => {
    it("loads only the dialog's own bundle, under a nonced policy", () => {
        const html = buildSandboxEditorHtml(
            new MockWebview() as never, extensionUri as never);

        expect(html).toContain("/ext/dist/webview/sandbox.js");
        expect(html).toContain("/ext/dist/webview/sandbox.css");
        expect(html).not.toContain("editor.js");
        expect(html).toContain("default-src 'none'");
        expect(html).toContain("<title>New Sandbox</title>");
    });
});

describe("SandboxEditorPanel", () => {
    beforeEach(() => {
        SandboxEditorPanel.disposeCurrent();
        resetVscodeMock();
    });

    it("opens with a free port suggested and the versions offered",
        async () => {
            const { host } = createHost({ taken: [3310] });

            SandboxEditorPanel.show(extensionUri as never, host);
            expect(currentPanel().viewType).toBe(SANDBOX_EDITOR_VIEW_TYPE);
            expect(currentPanel().title).toBe("New Sandbox");

            await receive({ type: "ready" });

            // Newest first, starting on the highest.
            expect(posted()).toEqual([{
                type: "load",
                fields: emptySandboxFields("3311", "12.3.2"),
                takenPorts: [3310],
                versions: ["12.3.2", "11.8.9"],
            }]);
        });

    it("starts on the highest version however the server orders them",
        async () => {
        const { host } = createHost({
            versions: ["11.10.1", "10.6.21", "11.9.4"],
        });

        SandboxEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });

        expect(posted()[0]).toMatchObject({
            fields: { serverVersion: "11.10.1" },
            versions: ["11.10.1", "11.9.4", "10.6.21"],
        });
    });

    it("suggests past the ports local connections use", async () => {
        const { host } = createHost({
            taken: [3311],
            connections: [
                "mariadb://dba@localhost:3310",
                "mariadb://root@127.0.0.1:3312",
                "mariadb://dba@db.example.com:3313",
            ],
        });

        SandboxEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });

        expect(posted()[0]).toMatchObject({
            fields: { port: "3313" },
            // Only a sandbox's port is refused; a connection's is only
            // not suggested.
            takenPorts: [3311],
        });
    });

    it("still suggests a port when the connections cannot be listed",
        async () => {
            const state = createHost({ connections: new Error("down") });

            SandboxEditorPanel.show(extensionUri as never, state.host);
            await receive({ type: "ready" });

            expect(posted()[0]).toMatchObject({ fields: { port: "3310" } });
            expect(state.logs).toEqual([
                "Could not list the connections to suggest a sandbox port: "
                + "down",
            ]);
        });

    it("still opens when the versions cannot be listed", async () => {
        const state = createHost({ versions: new Error("Unknown tool") });

        SandboxEditorPanel.show(extensionUri as never, state.host);
        await receive({ type: "ready" });

        // The server on the PATH is then the one choice, and so the one
        // picked.
        expect(posted()[0]).toMatchObject({
            type: "load", versions: [],
            fields: { serverVersion: "Server on the PATH" },
        });
        expect(state.logs).toEqual([
            "Could not list the sandbox server versions: Unknown tool",
        ]);
    });

    it("reveals the open dialog rather than reloading it", () => {
        const { host } = createHost();

        SandboxEditorPanel.show(extensionUri as never, host);
        const first = currentPanel();
        const html = first.webview.html;
        SandboxEditorPanel.show(extensionUri as never, host);

        // A deploy may be running in it; reloading would lose its answer.
        expect(webviewPanels).toHaveLength(1);
        expect(first.revealed).toBe(1);
        expect(first.webview.html).toBe(html);
    });

    it("deploys, reports it and closes", async () => {
        const state = createHost();
        SandboxEditorPanel.show(extensionUri as never, state.host);

        await receive({
            type: "create",
            fields: fields({
                serverVersion: "11.8", password: "pw", passwordConfirmation: "pw",
            }),
        });

        expect(state.deployed).toEqual([{
            port: 3310, password: "pw", serverVersion: "11.8",
            allowRootFrom: "127.0.0.1", mcpAccess: true,
        }]);
        expect(state.progressed).toBe(1);
        expect(state.created).toEqual(["Deployed on port 3310."]);
        expect(currentPanel().disposed).toBe(true);
        expect(posted()).toContainEqual({ type: "busy", busy: true });
    });

    it("refuses passwords that do not match before deploying", async () => {
        const state = createHost();
        SandboxEditorPanel.show(extensionUri as never, state.host);

        await receive({
            type: "create",
            fields: fields({ password: "pw", passwordConfirmation: "wp" }),
        });

        expect(state.deployed).toEqual([]);
        expect(posted()).toEqual([{
            type: "createError", message: "The passwords do not match.",
        }]);
    });

    it("refuses fields with a problem before deploying anything", async () => {
        const state = createHost({ taken: [3310] });
        SandboxEditorPanel.show(extensionUri as never, state.host);

        await receive({ type: "create", fields: fields() });

        expect(state.deployed).toEqual([]);
        expect(posted()).toEqual([{
            type: "createError",
            message: "There is a sandbox on port 3310 already.",
        }]);
        expect(currentPanel().disposed).toBe(false);
    });

    it("stays open with the shell's reason when the deploy fails",
        async () => {
            const state = createHost({
                deploy: () => {
                    return Promise.reject(new Error("Port 3310 is in use."));
                },
            });
            SandboxEditorPanel.show(extensionUri as never, state.host);

            await receive({ type: "create", fields: fields() });

            expect(posted().slice(-2)).toEqual([
                { type: "createError", message: "Port 3310 is in use." },
                { type: "busy", busy: false },
            ]);
            expect(currentPanel().disposed).toBe(false);
            expect(state.created).toEqual([]);
            expect(errorMessages).toEqual([]);
        });

    it("notifies a failure once the dialog has been closed", async () => {
        let fail = (): void => { /* set below */ };
        const state = createHost({
            deploy: () => {
                return new Promise((_, reject) => {
                    fail = () => { reject(new Error("Download failed.")); };
                });
            },
        });
        SandboxEditorPanel.show(extensionUri as never, state.host);
        const panel = currentPanel();

        currentPanel().webview.receive({ type: "create", fields: fields() });
        await new Promise((resolve) => { setTimeout(resolve, 0); });
        panel.dispose();
        const before = panel.webview.posted.length;
        fail();
        await new Promise((resolve) => { setTimeout(resolve, 0); });

        expect(errorMessages).toEqual(["MariaDB: Download failed."]);
        // Nothing is posted to a webview that is gone.
        expect(panel.webview.posted).toHaveLength(before);
    });

    it("closes on cancel", async () => {
        SandboxEditorPanel.show(extensionUri as never, createHost().host);

        await receive({ type: "cancel" });

        expect(currentPanel().disposed).toBe(true);
    });
});
