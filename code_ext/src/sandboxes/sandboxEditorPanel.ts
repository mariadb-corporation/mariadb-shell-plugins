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

import { buildDialogHtml } from "../connections/connectionEditorPanel.js";
import { showErrorWithLog } from "../errorMessages.js";
import {
    defaultServerVersion,
    emptySandboxFields,
    localConnectionPorts,
    newestFirst,
    sandboxDeployOptions,
    suggestSandboxPort,
    type ISandboxFields,
} from "./sandboxFields.js";
import type {
    SandboxHostMessage,
    SandboxWebviewMessage,
} from "./sandboxProtocol.js";
import type { ISandboxStore } from "./sandboxStore.js";

/**
 * The webview panel behind the New Sandbox dialog.
 *
 * What the fields mean lives in `sandboxFields`; this owns the panel, the
 * HTML and the message loop, as `ConnectionEditorPanel` does for the
 * connection editor it is modelled on.
 */

/** What the panel needs from the outside, so a test can supply its own. */
export interface ISandboxEditorHost {
    /**
     * The sandboxes: the ports a new one cannot take, the versions to
     * offer - both kept, so opening the dialog lists nothing that has
     * been listed before - and the deploy, which has the view's list read
     * again.
     */
    store: ISandboxStore;
    /**
     * The configured connections, as last read, so the suggested port is
     * not one a connection to this machine already uses. Left out, only
     * the sandboxes' ports are avoided.
     */
    connectionUris?(): Promise<string[]>;
    /**
     * Wraps the deploy, which can take minutes, in whatever progress the
     * extension shows for it. Left out, it just runs.
     */
    withProgress?<T>(work: () => Promise<T>): Promise<T>;
    /** Called after a sandbox was deployed, with the server's message. */
    onCreated(message: string): void;
    log(message: string): void;
}

/** The view type the panel is registered under. */
export const SANDBOX_EDITOR_VIEW_TYPE = "mariadb.sandboxEditor";

/** The dialog's title, on the tab and in the document. */
const TITLE = "New Sandbox";

/**
 * Builds the HTML shell the dialog loads: the connection editor's, with the
 * dialog's own bundle.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 *
 * @returns The HTML document.
 */
export const buildSandboxEditorHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
): string => {
    return buildDialogHtml(webview, extensionUri, "sandbox", TITLE);
};

/**
 * Opens the New Sandbox dialog.
 *
 * One at a time, as with the connection editor: asking again reveals the
 * one that is up - and, unlike the connection editor, does NOT reload it,
 * since a deploy may be running in it.
 */
export class SandboxEditorPanel {
    static #current: SandboxEditorPanel | undefined;

    #panel: vscode.WebviewPanel;
    #disposed = false;
    #disposables: vscode.Disposable[] = [];

    private constructor(
        panel: vscode.WebviewPanel,
        private readonly host: ISandboxEditorHost,
    ) {
        this.#panel = panel;
    }

    /**
     * Shows the dialog.
     *
     * @param extensionUri The root of the installed extension.
     * @param host What the panel needs from the extension.
     *
     * @returns Nothing.
     */
    public static show(
        extensionUri: vscode.Uri,
        host: ISandboxEditorHost,
    ): void {
        if (SandboxEditorPanel.#current) {
            SandboxEditorPanel.#current.#panel.reveal(vscode.ViewColumn.Active);

            return;
        }

        const panel = vscode.window.createWebviewPanel(
            SANDBOX_EDITOR_VIEW_TYPE,
            TITLE,
            vscode.ViewColumn.Active,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [
                    vscode.Uri.joinPath(extensionUri, "dist"),
                ],
            },
        );

        const editor = new SandboxEditorPanel(panel, host);
        SandboxEditorPanel.#current = editor;

        panel.onDidDispose(() => { editor.dispose(); }, undefined,
            editor.#disposables);
        panel.webview.onDidReceiveMessage(
            (message: SandboxWebviewMessage) => {
                void editor.#onMessage(message);
            },
            undefined,
            editor.#disposables,
        );

        panel.webview.html = buildSandboxEditorHtml(panel.webview, extensionUri);
    }

    /**
     * Closes the dialog, if one is open. Used when the extension shuts down.
     *
     * @returns Nothing.
     */
    public static disposeCurrent(): void {
        const current = SandboxEditorPanel.#current;
        if (current !== undefined) {
            current.#panel.dispose();
        }
    }

    /**
     * Sends a message to the webview, unless it has been closed - which a
     * deploy that outlives its dialog finds it has.
     *
     * @param message The message to send.
     *
     * @returns Nothing.
     */
    #post(message: SandboxHostMessage): void {
        if (!this.#disposed) {
            void this.#panel.webview.postMessage(message);
        }
    }

    /**
     * Handles one message from the webview.
     *
     * @param message The message that arrived.
     *
     * @returns Nothing.
     */
    async #onMessage(message: SandboxWebviewMessage): Promise<void> {
        switch (message.type) {
            case "ready": {
                // Only the sandboxes' ports are refused outright; one a
                // connection names is merely not suggested - that server may
                // be long gone, and the shell says so if it is not.
                const takenPorts = this.host.store.ports;
                const [versions, connectionPorts] = await Promise.all([
                    this.#versions(), this.#connectionPorts(),
                ]);
                // A new sandbox starts on the newest major version - the
                // server on the PATH is whatever happens to be installed,
                // and is a choice rather than the default.
                const offered = newestFirst(versions);
                this.#post({
                    type: "load",
                    fields: emptySandboxFields(
                        String(suggestSandboxPort(
                            [...takenPorts, ...connectionPorts])),
                        defaultServerVersion(offered),
                    ),
                    takenPorts,
                    versions: offered,
                });
                break;
            }

            case "create": {
                await this.#create(message.fields);
                break;
            }

            default: {
                this.#panel.dispose();
            }
        }
    }

    /**
     * The versions to offer. A convenience, so a listing that fails never
     * stands in the dialog's way: the version can still be typed.
     *
     * @returns The latest release of every series, or nothing.
     */
    async #versions(): Promise<string[]> {
        try {
            return await this.host.store.availableVersions();
        } catch (error) {
            this.host.log("Could not list the sandbox server versions: "
                + `${error instanceof Error ? error.message : String(error)}`);

            return [];
        }
    }

    /**
     * The ports configured connections to this machine use, to keep the
     * suggestion off them. A convenience like the versions: where the
     * connections cannot be had, the sandboxes' ports alone are avoided.
     *
     * @returns The ports.
     */
    async #connectionPorts(): Promise<number[]> {
        if (this.host.connectionUris === undefined) {
            return [];
        }

        try {
            return localConnectionPorts(await this.host.connectionUris());
        } catch (error) {
            this.host.log("Could not list the connections to suggest a "
                + "sandbox port: "
                + `${error instanceof Error ? error.message : String(error)}`);

            return [];
        }
    }

    /**
     * Deploys the sandbox and closes the dialog.
     *
     * @param fields The fields as they are on screen.
     *
     * @returns Nothing.
     */
    async #create(fields: ISandboxFields): Promise<void> {
        // Checked again here, not only in the dialog: this is what is
        // about to start a server.
        const built = sandboxDeployOptions(fields, this.host.store.ports);
        if ("problem" in built) {
            this.#post({ type: "createError", message: built.problem.message });

            return;
        }

        this.#post({ type: "busy", busy: true });
        try {
            const deploy = async (): Promise<string> => {
                return await this.host.store.deploy(built.options);
            };
            const message = this.host.withProgress === undefined
                ? await deploy()
                : await this.host.withProgress(deploy);

            this.host.log(message);
            this.host.onCreated(message);
            this.#panel.dispose();
        } catch (error) {
            const text = error instanceof Error ? error.message : String(error);
            this.host.log(`Failed to deploy the sandbox on port `
                + `${built.options.port}: ${text}`);
            if (this.#disposed) {
                // The dialog was closed while the deploy ran, so there is
                // nowhere left to show the reason but a notification.
                void showErrorWithLog(text);
            } else {
                this.#post({ type: "createError", message: text });
            }
        } finally {
            this.#post({ type: "busy", busy: false });
        }
    }

    /**
     * Tears the panel down.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        this.#disposed = true;
        SandboxEditorPanel.#current = undefined;
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
        this.#disposables = [];
    }
}
