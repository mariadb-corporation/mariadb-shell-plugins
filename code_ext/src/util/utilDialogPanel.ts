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
import type { UtilOptions } from "../mcp/utilApi.js";
import {
    buildUtilOptions,
    initialUtilValues,
    type IUtilOperationSpec,
} from "./utilFields.js";
import type { UtilHostMessage, UtilWebviewMessage } from "./utilProtocol.js";

/** What the dialog asks of the extension. */
export interface IUtilDialogHost {
    /** The path the dialog starts with. */
    initialPath?: string;
    /** The connections a copy can go to. */
    targets?: string[];
    /**
     * Starts the work. Resolves once it runs, after which the dialog
     * closes; a rejection is shown in the dialog.
     */
    start(options: UtilOptions, paths: string[], target?: string):
        Promise<void>;
    log(message: string): void;
}

export const UTIL_DIALOG_VIEW_TYPE = "mariadb.utilDialog";

/**
 * The dialog that sets up a dump, load, copy, export or import: one at a
 * time, as the other dialogs are. It closes once the work started; its
 * progress is shown by the task's notification and the Tasks view.
 */
export class UtilDialogPanel {
    static #current: UtilDialogPanel | undefined;

    #panel: vscode.WebviewPanel;
    #disposed = false;
    #disposables: vscode.Disposable[] = [];

    private constructor(
        panel: vscode.WebviewPanel,
        private readonly spec: IUtilOperationSpec,
        private readonly host: IUtilDialogHost,
    ) {
        this.#panel = panel;
    }

    /**
     * Opens the dialog, replacing one that is open for something else.
     *
     * @param extensionUri The extension's folder.
     * @param spec What the dialog asks for.
     * @param host What it starts.
     */
    public static show(
        extensionUri: vscode.Uri,
        spec: IUtilOperationSpec,
        host: IUtilDialogHost,
    ): void {
        const open = UtilDialogPanel.#current;
        if (open !== undefined) {
            open.#panel.dispose();
        }

        const panel = vscode.window.createWebviewPanel(
            UTIL_DIALOG_VIEW_TYPE,
            spec.title,
            vscode.ViewColumn.Active,
            { ...webviewOptions(extensionUri), retainContextWhenHidden: true },
        );
        const dialog = new UtilDialogPanel(panel, spec, host);
        UtilDialogPanel.#current = dialog;

        panel.onDidDispose(() => { dialog.dispose(); }, undefined,
            dialog.#disposables);
        panel.webview.onDidReceiveMessage(
            (message: UtilWebviewMessage) => {
                void dialog.#onMessage(message);
            },
            undefined,
            dialog.#disposables,
        );
        panel.webview.html = buildWebviewHtml(panel.webview, extensionUri,
            "util", spec.title);
    }

    public static disposeCurrent(): void {
        const open = UtilDialogPanel.#current;
        if (open !== undefined) {
            open.#panel.dispose();
        }
    }

    #post(message: UtilHostMessage): void {
        if (!this.#disposed) {
            void this.#panel.webview.postMessage(message);
        }
    }

    async #onMessage(message: UtilWebviewMessage): Promise<void> {
        switch (message.type) {
            case "ready": {
                this.#post({
                    type: "load",
                    spec: this.spec,
                    values: initialUtilValues(this.spec,
                        this.host.initialPath ?? ""),
                    targets: this.host.targets ?? [],
                });
                break;
            }

            case "browse": {
                const path = await this.#browse(message.current);
                if (path !== undefined) {
                    this.#post({ type: "browsed", path });
                }
                break;
            }

            case "start": {
                // Checked again here: this is what starts the work.
                const built = buildUtilOptions(this.spec, message.values);
                if ("problem" in built) {
                    this.#post({
                        type: "startError", message: built.problem.message,
                        field: built.problem.field,
                    });
                    break;
                }

                this.#post({ type: "busy", busy: true });
                try {
                    await this.host.start(built.options, built.paths,
                        built.target);
                    this.#panel.dispose();
                } catch (error) {
                    const text = errorText(error);
                    this.host.log(`${this.spec.title} did not start: ${text}`);
                    this.#post({ type: "startError", message: text });
                } finally {
                    this.#post({ type: "busy", busy: false });
                }
                break;
            }

            default: {
                this.#panel.dispose();
            }
        }
    }

    /**
     * @param current What the path field holds, to start the picker at.
     *
     * @returns The path picked, or undefined where none was.
     */
    async #browse(current: string): Promise<string | undefined> {
        const browse = this.spec.path?.browse;
        const start = current.trim() === ""
            ? undefined : vscode.Uri.file(current.split(",")[0].trim());

        if (browse === "saveFile") {
            const picked = await vscode.window.showSaveDialog({
                title: this.spec.path?.label,
                ...(start === undefined ? {} : { defaultUri: start }),
            });

            return picked?.fsPath;
        }

        const picked = await vscode.window.showOpenDialog({
            title: this.spec.path?.label,
            canSelectFolders: browse === "folder",
            canSelectFiles: browse === "openFiles",
            canSelectMany: browse === "openFiles",
            openLabel: browse === "folder" ? "Select Folder" : "Select",
            ...(start === undefined ? {} : { defaultUri: start }),
        });
        if (picked === undefined || picked.length === 0) {
            return undefined;
        }

        return picked.map((uri) => { return uri.fsPath; }).join(", ");
    }

    public dispose(): void {
        this.#disposed = true;
        if (UtilDialogPanel.#current === this) {
            UtilDialogPanel.#current = undefined;
        }
        for (const disposable of this.#disposables) {
            disposable.dispose();
        }
        this.#disposables = [];
    }
}
