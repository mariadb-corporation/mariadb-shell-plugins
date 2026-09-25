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

import type { EditorWebviewMessage }
    from "../../src/connections/editorProtocol.js";
import type { SandboxWebviewMessage }
    from "../../src/sandboxes/sandboxProtocol.js";
import type { WebviewMessage } from "../../src/webview/protocol.js";

/** The bridge VS Code injects into every webview. */
interface IVsCodeApi {
    postMessage(message: unknown): void;
    getState(): unknown;
    setState(state: unknown): void;
}

declare function acquireVsCodeApi(): IVsCodeApi;

// acquireVsCodeApi may be called only once per webview, so the handle is
// taken at module load and shared from there.
const api = acquireVsCodeApi();

/**
 * Sends a message to the extension.
 *
 * The webviews - the result view, the connection editor and New Sandbox -
 * speak different protocols but load the same module, so this accepts any
 * of them. A
 * webview only ever sends one of them, and the type parameter is what keeps
 * a call site honest about which.
 *
 * @param message The message to send.
 *
 * @returns Nothing.
 */
export const post = <
    T extends WebviewMessage | EditorWebviewMessage | SandboxWebviewMessage,
>(
    message: T,
): void => {
    api.postMessage(message);
};
