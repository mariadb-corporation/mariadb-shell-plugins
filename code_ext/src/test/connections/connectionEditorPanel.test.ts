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

import {
    buildEditorHtml,
    ConnectionEditorPanel,
    CONNECTION_EDITOR_VIEW_TYPE,
    type IConnectionEditorHost,
} from "../../connections/connectionEditorPanel.js";
import type { IConnectionFields } from "../../connections/connectionUri.js";
import type {
    EditorHostMessage,
    EditorWebviewMessage,
} from "../../connections/editorProtocol.js";
import {
    MockWebview,
    resetVscodeMock,
    Uri,
    webviewPanels,
    type MockWebviewPanel,
} from "../mocks/vscode.js";
import { createFakeApi, type FakeApi, type FakeApiOptions } from "../helpers.js";

const extensionUri = Uri.file("/ext");

/** The host, plus what it recorded. */
const createHost = (options: FakeApiOptions = {}): {
    host: IConnectionEditorHost;
    api: FakeApi;
    saved: number;
    logs: string[];
} => {
    const api = createFakeApi(options);
    const state = {
        host: undefined as unknown as IConnectionEditorHost,
        api,
        saved: 0,
        logs: [] as string[],
    };

    state.host = {
        api: () => { return Promise.resolve(api); },
        onSaved: () => { state.saved += 1; },
        log: (message: string) => { state.logs.push(message); },
    };

    return state;
};

/** The panel most recently created, as the mock recorded it. */
const currentPanel = (): MockWebviewPanel => {
    return webviewPanels[webviewPanels.length - 1]!;
};

/** Everything the host has posted to the webview. */
const posted = (): EditorHostMessage[] => {
    return currentPanel().webview.posted as EditorHostMessage[];
};

/** The fields the host sent in its `load` message. */
const loadedFields = (): IConnectionFields => {
    const load = posted().find((message) => { return message.type === "load"; });
    if (load === undefined || load.type !== "load") {
        throw new Error("The host has not sent a load message.");
    }

    return load.fields;
};

/** Drives one message in, as the webview would, and lets promises settle. */
const receive = async (message: EditorWebviewMessage): Promise<void> => {
    currentPanel().webview.receive(message);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
};

describe("buildEditorHtml", () => {
    it("loads only the editor's own bundle, under a nonced policy", () => {
        const webview = new MockWebview();

        const html = buildEditorHtml(webview as never, extensionUri as never);

        // Its own bundle, not the result view's: the two are separate
        // builds precisely so neither pulls the other's code in.
        expect(html).toContain("/ext/dist/webview/editor.js");
        expect(html).toContain("/ext/dist/webview/editor.css");
        expect(html).not.toContain("main.js");

        // A dialog that handles credentials must not reach the network.
        expect(html).toContain("default-src 'none'");
        expect(html).toMatch(/script-src 'nonce-[A-Za-z0-9]{32}'/);
    });

    it("gives each load a nonce of its own", () => {
        const webview = new MockWebview();
        const first = buildEditorHtml(webview as never, extensionUri as never);
        const second = buildEditorHtml(webview as never, extensionUri as never);

        expect(first).not.toBe(second);
    });
});

describe("ConnectionEditorPanel", () => {
    beforeEach(() => {
        ConnectionEditorPanel.disposeCurrent();
        resetVscodeMock();
    });

    it("opens a panel on a new connection", async () => {
        const { host } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);

        const panel = currentPanel();
        expect(panel.viewType).toBe(CONNECTION_EDITOR_VIEW_TYPE);
        expect(panel.title).toBe("New Database Connection");

        await receive({ type: "ready" });

        const load = posted()[0]!;
        expect(load).toMatchObject({
            type: "load",
            uri: undefined,
            mcpAccess: false,
            // Nothing is stored yet, so the editor offers "Set Password"
            // rather than "keep the stored one".
            hasStoredPassword: false,
        });
    });

    it("opens on an existing connection with its fields filled in", async () => {
        const { host } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host, {
            uri: "mariadb://dba@db.example.com:3307/world",
            kind: "mcp",
        });

        expect(currentPanel().title).toBe("Edit mariadb://dba@db.example.com:3307/world");

        await receive({ type: "ready" });

        expect(posted()[0]).toMatchObject({
            type: "load",
            uri: "mariadb://dba@db.example.com:3307/world",
            // In the shared list, so the checkbox comes up ticked.
            mcpAccess: true,
            hasStoredPassword: true,
        });
        expect(loadedFields())
            .toMatchObject({ host: "db.example.com", schema: "world" });
    });

    it("never sends a password to the webview", async () => {
        // It cannot: nothing can read one back. The editor is told only
        // whether one exists.
        const { host } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host, {
            uri: "mariadb://dba@localhost:3306", kind: "gui",
        });
        await receive({ type: "ready" });

        expect(JSON.stringify(posted())).not.toContain("password\":\"");
    });

    it("reuses the one panel, reloading it for the new subject", async () => {
        const { host } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);
        const first = currentPanel();

        ConnectionEditorPanel.show(extensionUri as never, host, {
            uri: "mariadb://dba@localhost:3306", kind: "gui",
        });

        // The same panel, revealed and retitled - not a second dialog able
        // to save over the first.
        expect(webviewPanels).toHaveLength(1);
        expect(currentPanel()).toBe(first);
        expect(first.revealed).toBe(1);
        expect(first.title).toBe("Edit mariadb://dba@localhost:3306");

        await receive({ type: "ready" });
        expect(posted()[posted().length - 1]).toMatchObject({
            uri: "mariadb://dba@localhost:3306",
        });
    });

    it("tests the connection and reports what happened", async () => {
        const { host, api } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });
        await receive({
            type: "test",
            fields: { ...loadedFields(), user: "dba" },
            password: "pw",
        });

        expect(api.tested).toEqual([
            { uri: "mariadb://dba@localhost:3306", password: "pw" },
        ]);
        expect(posted()).toContainEqual({
            type: "testResult",
            ok: true,
            message: "Connected to 'mariadb://dba@localhost:3306' successfully.",
        });
        // The buttons go back to being usable either way.
        expect(posted()).toContainEqual({ type: "busy", busy: false });
    });

    it("reports a failed test without closing the dialog", async () => {
        const { host } = createHost({ testFailure: "Access denied" });

        ConnectionEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });
        await receive({
            type: "test",
            fields: { ...loadedFields(), user: "dba" },
            password: "wrong",
        });

        expect(posted()).toContainEqual({
            type: "testResult", ok: false, message: "Access denied",
        });
        expect(currentPanel().disposed).toBe(false);
    });

    it("saves, tells the tree and closes", async () => {
        const { host, api } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });
        await receive({
            type: "save",
            fields: { ...loadedFields(), user: "dba" },
            password: "pw",
            mcpAccess: true,
        });

        expect(api.added).toEqual([{
            uri: "mariadb://dba@localhost:3306", password: "pw", kind: "mcp",
        }]);
        expect(currentPanel().disposed).toBe(true);
    });

    it("keeps the dialog open and says why when a save is refused",
        async () => {
            const { host, api } = createHost();

            ConnectionEditorPanel.show(extensionUri as never, host);
            await receive({ type: "ready" });
            await receive({
                type: "save",
                // No user name, so this builds no URI at all.
                fields: loadedFields(),
                password: "pw",
                mcpAccess: false,
            });

            expect(posted()).toContainEqual({
                type: "saveError", message: "A user name is required.",
            });
            expect(api.added).toEqual([]);
            expect(currentPanel().disposed).toBe(false);
        });

    it("closes without saving on cancel", async () => {
        const { host, api } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);
        await receive({ type: "ready" });
        await receive({ type: "cancel" });

        expect(api.added).toEqual([]);
        expect(api.updated).toEqual([]);
        expect(currentPanel().disposed).toBe(true);
    });

    it("lets a new panel open after the last one was closed", () => {
        const { host } = createHost();

        ConnectionEditorPanel.show(extensionUri as never, host);
        currentPanel().dispose();

        ConnectionEditorPanel.show(extensionUri as never, host);

        expect(webviewPanels).toHaveLength(2);
    });
});
