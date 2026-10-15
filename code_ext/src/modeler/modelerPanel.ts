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

import { buildWebviewHtml, webviewOptions } from "../webview/html.js";
import { errorText } from "../text.js";
import type { ISchemaModel } from "./modelFormat.js";
import type {
    ModelerHostMessage,
    ModelerWebviewMessage,
} from "./modelerProtocol.js";
import { DEFAULT_PAGE_SETTINGS, type IPageSettings } from "./pageSettings.js";

/**
 * The webview panels of the schema diagram viewer: one per schema of a
 * connection, opened on one of its diagrams. Opening another diagram of
 * the same schema shows it in the panel that is up.
 *
 * What is drawn, and how, lives in the webview (`webview/src/modeler/`).
 */

/** What a panel needs from the outside, so a test can supply its own. */
export interface IModelerHost {
    /**
     * Reads the schema's model. Called once the page is up, and again on a
     * reload of the webview.
     */
    readModel(): Promise<{ model: ISchemaModel; source: string }>;
    /** How the diagrams are laid out on paper. */
    pageSettings?: IPageSettings;
    log(message: string): void;
}

/** What a panel shows: a schema of a connection. */
export interface IModelerTarget {
    /** The connection's URI, which tells the panels apart. */
    connection: string;
    schema: string;
    /** The diagram to show. */
    diagram: string;
}

/** The view type the panels are registered under. */
export const MODELER_VIEW_TYPE = "mariadb.modelViewer";

/**
 * Builds the HTML shell the viewer loads.
 *
 * @param webview The webview to build the HTML for.
 * @param extensionUri The root of the installed extension.
 * @param title The document title.
 *
 * @returns The HTML document.
 */
export const buildModelerHtml = (
    webview: vscode.Webview,
    extensionUri: vscode.Uri,
    title = "Schema Diagram",
): string => {
    return buildWebviewHtml(webview, extensionUri, "modeler", title);
};

/**
 * @param schema The schema.
 * @param diagram The diagram shown.
 *
 * @returns The panel's title.
 */
export const modelerTitle = (schema: string, diagram: string): string => {
    return `${diagram} (${schema})`;
};

/** A schema diagram viewer. */
export class ModelerPanel {
    static readonly #open = new Map<string, ModelerPanel>();

    #panel: vscode.WebviewPanel;
    #disposables: vscode.Disposable[] = [];
    /** The diagram to show once the model is there. */
    #diagram: string;

    private constructor(
        panel: vscode.WebviewPanel,
        private readonly key: string,
        private readonly target: IModelerTarget,
        private readonly host: IModelerHost,
    ) {
        this.#panel = panel;
        this.#diagram = target.diagram;
    }

    /**
     * @param target A schema of a connection.
     *
     * @returns The key its panel is kept under.
     */
    static #keyOf(target: IModelerTarget): string {
        return JSON.stringify([target.connection, target.schema]);
    }

    /**
     * Shows a diagram: in the schema's panel that is up, else in a new one.
     *
     * @param extensionUri The root of the installed extension.
     * @param target The schema and the diagram.
     * @param host What the panel needs from the extension.
     *
     * @returns Nothing.
     */
    public static show(
        extensionUri: vscode.Uri,
        target: IModelerTarget,
        host: IModelerHost,
    ): void {
        const key = ModelerPanel.#keyOf(target);
        const open = ModelerPanel.#open.get(key);
        if (open !== undefined) {
            open.#showDiagram(target.diagram);
            open.#panel.reveal(vscode.ViewColumn.Active);

            return;
        }

        const title = modelerTitle(target.schema, target.diagram);
        const panel = vscode.window.createWebviewPanel(
            MODELER_VIEW_TYPE,
            title,
            vscode.ViewColumn.Active,
            {
                ...webviewOptions(extensionUri),
                // The scene is costly to build; keep it while the tab is
                // in the background.
                retainContextWhenHidden: true,
            },
        );
        panel.webview.html = buildModelerHtml(panel.webview, extensionUri,
            title);

        const current = new ModelerPanel(panel, key, target, host);
        ModelerPanel.#open.set(key, current);
        current.#disposables.push(
            panel.webview.onDidReceiveMessage((message: unknown) => {
                void current.#handle(message as ModelerWebviewMessage);
            }),
            panel.onDidDispose(() => { current.dispose(); }),
        );
    }

    #post(message: ModelerHostMessage): void {
        void this.#panel.webview.postMessage(message);
    }

    #showDiagram(name: string): void {
        this.#diagram = name;
        this.#panel.title = modelerTitle(this.target.schema, name);
        this.#post({ type: "showDiagram", name });
    }

    async #handle(message: ModelerWebviewMessage): Promise<void> {
        switch (message.type) {
            case "ready": {
                try {
                    const { model, source } = await this.host.readModel();
                    this.host.log(`Showing the diagrams of ${model.schema} `
                        + `from ${source}`);
                    this.#post({
                        type: "load",
                        model,
                        source,
                        pageSettings: this.host.pageSettings
                            ?? DEFAULT_PAGE_SETTINGS,
                        diagram: this.#diagram,
                    });
                } catch (error) {
                    const text = errorText(error);
                    this.host.log(`Could not read the diagrams of `
                        + `${this.target.schema}: ${text}`);
                    this.#post({ type: "error", message: text });
                }
                break;
            }

            case "diagramShown": {
                this.#diagram = message.name;
                this.#panel.title = modelerTitle(this.target.schema,
                    message.name);
                break;
            }

            case "log": {
                this.host.log(message.message);
                break;
            }

            default:
        }
    }

    /**
     * Tears the panel down.
     *
     * @returns Nothing.
     */
    public dispose(): void {
        if (ModelerPanel.#open.get(this.key) === this) {
            ModelerPanel.#open.delete(this.key);
        }
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
        this.#disposables = [];
    }
}
