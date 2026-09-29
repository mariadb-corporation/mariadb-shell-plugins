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

import type { IConnectionFields } from "./connectionUri.js";

/**
 * What the connection editor's webview and its host say to each other.
 *
 * A password crosses this boundary, so two rules hold throughout:
 *
 * * The host NEVER sends one. It cannot read a stored password back - no tool
 *   returns one - and it must not need to: `hasStoredPassword` says whether
 *   there is one, and that is all the editor shows about it.
 * * The webview sends `password: undefined` to mean "keep what is stored",
 *   which is different from `""` - an empty password is a real password for
 *   an account that has none.
 */

/** The editor's starting state, sent once the webview says it is ready. */
export interface ILoadMessage {
    type: "load";
    /** The connection being edited, or undefined when adding a new one. */
    uri?: string;
    fields: IConnectionFields;
    /** Whether the connection is in the shared MCP list. */
    mcpAccess: boolean;
    /** Whether a password is already stored for it. */
    hasStoredPassword: boolean;
}

/** The answer to a Test Connection. */
export interface ITestResultMessage {
    type: "testResult";
    ok: boolean;
    message: string;
}

/** A save that could not be made; the editor stays open. */
export interface ISaveErrorMessage {
    type: "saveError";
    message: string;
}

/** Whether a long running button should be showing itself as busy. */
export interface IBusyMessage {
    type: "busy";
    busy: boolean;
}

/**
 * What the clipboard held when the webview asked for it. A webview cannot
 * read the clipboard itself without the user pressing the paste keys, so the
 * Paste button asks the host.
 */
export interface IClipboardMessage {
    type: "clipboard";
    text: string;
}

export type EditorHostMessage =
    | ILoadMessage
    | ITestResultMessage
    | ISaveErrorMessage
    | IBusyMessage
    | IClipboardMessage;

/** The webview is up and wants its state. */
export interface IReadyMessage {
    type: "ready";
}

/** Try these credentials without storing anything. */
export interface ITestMessage {
    type: "test";
    fields: IConnectionFields;
    /** Undefined means the stored password, which only the server can use. */
    password?: string;
}

/** Store the connection. */
export interface ISaveMessage {
    type: "save";
    fields: IConnectionFields;
    /** Undefined keeps the stored password; a string replaces it. */
    password?: string;
    mcpAccess: boolean;
}

/** Send the clipboard's text, to be taken as a connection URI. */
export interface IPasteMessage {
    type: "paste";
}

/** Close without saving. */
export interface ICancelMessage {
    type: "cancel";
}

export type EditorWebviewMessage =
    | IReadyMessage
    | ITestMessage
    | ISaveMessage
    | IPasteMessage
    | ICancelMessage;
