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

import type { IMariaDbApi } from "../mcp/types.js";
import { ROOT_FOLDER, allFolders } from "./connectionFolders.js";
import { emptyConnectionFields } from "./connectionUri.js";
import {
    fieldsOf,
    listConnections,
    saveConnection,
    testConnection,
    type IStoredConnection,
} from "./connectionStore.js";
import type {
    EditorHostMessage,
    EditorWebviewMessage,
} from "./editorProtocol.js";

/**
 * The webview panel behind the connection editor.
 *
 * Everything it decides lives in `connectionStore`; this owns the panel, the
 * HTML and the message loop, which is the part a test cannot reach without a
 * running VS Code.
 */

/** What the panel needs from the outside, so a test can supply its own. */
export interface IConnectionEditorHost {
    /** The database API, starting the MCP server if it is not up yet. */
    api(): Promise<IMariaDbApi>;
    /**
     * The configured connections, as last read - the folders are offered
     * from them. Left out, they are read from `api` each time the editor
     * opens.
     */
    listStored?(): Promise<IStoredConnection[]>;
    /** Called after a connection was stored, so the tree can redraw. */
    onSaved(): void;
    log(message: string): void;
}

/** The view type the panel is registered under. */
export const CONNECTION_EDITOR_VIEW_TYPE = "mariadb.connectionEditor";

/**
 * Builds a nonce for one load of a dialog.
 *
 * @returns 32 random alphanumeric characters.
 */
const createNonce = (): string => {
    const alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let nonce = "";
    for (let index = 0; index < 32; index += 1) {
        nonce += alphabet[Math.floor(Math.random() * alphabet.length)];
    }

    return nonce;
};

/**
 * Builds the HTML shell a dialog webview loads - the connection editor's,
 * and the New Sandbox dialog's, each its own build.
 *
 * Mirrors the result view's: everything from the extension's own folder,
 * under a strict content security policy with a per-load nonce, so a dialog
 * that handles credentials cannot reach the network.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 * @param bundle The dialog's build: `<bundle>.js` and `<bundle>.css` under
 *               `dist/webview`.
 * @param title The document title.
 *
 * @returns The HTML document.
 */
export const buildDialogHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
    bundle: string,
    title: string,
): string => {
    const asset = (...parts: string[]): string => {
        return webview.asWebviewUri(
            vscode.Uri.joinPath(extensionUri, ...parts),
        ).toString();
    };

    const script = asset("dist", "webview", `${bundle}.js`);
    const style = asset("dist", "webview", `${bundle}.css`);
    const nonce = createNonce();

    return `<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; ${""
        }style-src ${webview.cspSource} 'unsafe-inline'; ${""
        }img-src ${webview.cspSource} data:; ${""
        }font-src ${webview.cspSource}; ${""
        }script-src 'nonce-${nonce}';" />
    <link rel="stylesheet" href="${style}" />
    <title>${title}</title>
</head>

<body>
    <div id="root"></div>
    <script type="module" nonce="${nonce}" src="${script}"></script>
</body>

</html>`;
};

/**
 * Builds the HTML shell the connection editor webview loads.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 *
 * @returns The HTML document.
 */
export const buildEditorHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
): string => {
    return buildDialogHtml(webview, extensionUri, "editor",
        "Database Connection Configuration");
};

/**
 * Opens the connection editor on a connection, or on a new one.
 *
 * One panel at a time: opening the editor again reveals and reloads the panel
 * that is already up, rather than leaving two dialogs able to save over each
 * other.
 */
export class ConnectionEditorPanel {
    static #current: ConnectionEditorPanel | undefined;

    #panel: vscode.WebviewPanel;
    #connection: IStoredConnection | undefined;
    /** The folder a new connection starts in. */
    #newIn = ROOT_FOLDER;
    #disposables: vscode.Disposable[] = [];

    private constructor(
        panel: vscode.WebviewPanel,
        private readonly extensionUri: vscode.Uri,
        private readonly host: IConnectionEditorHost,
    ) {
        this.#panel = panel;
    }

    /**
     * Shows the editor.
     *
     * @param extensionUri The root of the installed extension.
     * @param host What the panel needs from the extension.
     * @param connection The connection to edit, or undefined to add one.
     * @param newIn The folder a new connection starts in, where it was asked
     *              for from a folder in the tree. Ignored when editing.
     *
     * @returns Nothing.
     */
    public static show(
        extensionUri: vscode.Uri,
        host: IConnectionEditorHost,
        connection?: IStoredConnection,
        newIn: string = ROOT_FOLDER,
    ): void {
        const title = connection === undefined
            ? "New Database Connection"
            : `Edit ${connection.uri}`;

        if (ConnectionEditorPanel.#current) {
            const existing = ConnectionEditorPanel.#current;
            existing.#connection = connection;
            existing.#newIn = newIn;
            existing.#panel.title = title;
            existing.#panel.reveal(vscode.ViewColumn.Active);
            // Reloading the HTML restarts the webview, which then asks for
            // its state again - so the dialog cannot be left showing the
            // connection it was opened on last time.
            existing.#render();

            return;
        }

        const panel = vscode.window.createWebviewPanel(
            CONNECTION_EDITOR_VIEW_TYPE,
            title,
            vscode.ViewColumn.Active,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [
                    vscode.Uri.joinPath(extensionUri, "dist"),
                ],
            },
        );

        const editor = new ConnectionEditorPanel(panel, extensionUri, host);
        editor.#connection = connection;
        editor.#newIn = newIn;
        ConnectionEditorPanel.#current = editor;

        panel.onDidDispose(() => { editor.dispose(); }, undefined,
            editor.#disposables);
        panel.webview.onDidReceiveMessage(
            (message: EditorWebviewMessage) => {
                void editor.#onMessage(message);
            },
            undefined,
            editor.#disposables,
        );

        editor.#render();
    }

    /**
     * Closes the editor, if one is open. Used when the extension shuts down.
     *
     * @returns Nothing.
     */
    public static disposeCurrent(): void {
        // Not `#current?.#panel`: an optional chain cannot carry a private
        // name, so the check is spelled out.
        const current = ConnectionEditorPanel.#current;
        if (current !== undefined) {
            current.#panel.dispose();
        }
    }

    /**
     * Loads the HTML, which starts the webview over.
     *
     * @returns Nothing.
     */
    #render(): void {
        this.#panel.webview.html =
            buildEditorHtml(this.#panel.webview, this.extensionUri);
    }

    /**
     * Sends a message to the webview.
     *
     * @param message The message to send.
     *
     * @returns Nothing.
     */
    #post(message: EditorHostMessage): void {
        void this.#panel.webview.postMessage(message);
    }

    /**
     * Handles one message from the webview.
     *
     * @param message The message that arrived.
     *
     * @returns Nothing.
     */
    async #onMessage(message: EditorWebviewMessage): Promise<void> {
        switch (message.type) {
            case "ready": {
                const state = this.#connection === undefined
                    ? {
                        fields: emptyConnectionFields(),
                        mcpAccess: false,
                        path: this.#newIn,
                    }
                    : fieldsOf(this.#connection);

                this.#post({
                    type: "load",
                    uri: this.#connection?.uri,
                    fields: state.fields,
                    mcpAccess: state.mcpAccess,
                    // A connection that exists has a password stored for it,
                    // even if it is the empty one. The editor only needs to
                    // know whether to offer "keep" or "set".
                    hasStoredPassword: this.#connection !== undefined,
                    path: state.path,
                    folders: await this.#folders(),
                });
                break;
            }

            case "test": {
                await this.#test(message.fields, message.password);
                break;
            }

            case "save": {
                await this.#save(
                    message.fields, message.password, message.mcpAccess,
                    message.path,
                );
                break;
            }

            case "paste": {
                this.#post({
                    type: "clipboard",
                    text: await vscode.env.clipboard.readText(),
                });
                break;
            }

            default: {
                this.#panel.dispose();
            }
        }
    }

    /**
     * The folders connections are filed in, to offer in the Folder field.
     *
     * A convenience, so it never stands in the dialog's way: where the list
     * cannot be had, the field is simply typed into.
     *
     * @returns The folders, parents included, the top level left out.
     */
    async #folders(): Promise<string[]> {
        try {
            const stored = this.host.listStored === undefined
                ? await listConnections(await this.host.api())
                : await this.host.listStored();

            return allFolders(stored.map((connection) => {
                return connection.path ?? ROOT_FOLDER;
            }));
        } catch {
            return [];
        }
    }

    /**
     * Runs a Test Connection and reports what happened.
     *
     * @param fields The fields as they are on screen.
     * @param password The password typed, or undefined for the stored one.
     *
     * @returns Nothing.
     */
    async #test(
        fields: Parameters<typeof testConnection>[1],
        password: string | undefined,
    ): Promise<void> {
        this.#post({ type: "busy", busy: true });
        try {
            const api = await this.host.api();
            const message = await testConnection(api, fields, password);
            this.#post({ type: "testResult", ok: true, message });
        } catch (error) {
            this.#post({
                type: "testResult",
                ok: false,
                message: error instanceof Error ? error.message : String(error),
            });
        } finally {
            this.#post({ type: "busy", busy: false });
        }
    }

    /**
     * Stores the connection and closes the editor.
     *
     * @param fields The fields as they are on screen.
     * @param password The password typed, or undefined to keep the stored one.
     * @param mcpAccess Whether MCP clients may open it.
     * @param path The folder to file it in, as typed.
     *
     * @returns Nothing.
     */
    async #save(
        fields: Parameters<typeof testConnection>[1],
        password: string | undefined,
        mcpAccess: boolean,
        path: string,
    ): Promise<void> {
        this.#post({ type: "busy", busy: true });
        try {
            const api = await this.host.api();
            const result = await saveConnection(api, {
                fields,
                password,
                mcpAccess,
                path,
                original: this.#connection,
            });

            if (result.error !== undefined) {
                this.#post({ type: "saveError", message: result.error });

                return;
            }

            this.host.log(`Saved the connection '${result.uri}'.`);
            this.host.onSaved();
            this.#panel.dispose();
        } catch (error) {
            const text = error instanceof Error
                ? error.message
                : String(error);
            this.#post({ type: "saveError", message: text });
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
        ConnectionEditorPanel.#current = undefined;
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
        this.#disposables = [];
    }
}
