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

import type {
    IBusyMessage,
    ICancelMessage,
    IReadyMessage,
} from "../webview/dialogProtocol.js";
import type { MrsDialogKind } from "./mrsDialogs.js";
import type { IMrsColumns, IMrsScriptDefinitions } from "./mrsTypes.js";

/**
 * What the MRS dialogs' webview and its host say to each other. One
 * webview serves every MRS dialog; `load` says which one it is.
 *
 * Passwords and app secrets cross this boundary once, webview to host, on
 * `save`. The host never sends one back.
 */

/** The dialog to show, sent once the webview says it is ready. */
export interface IMrsLoadMessage {
    type: "load";
    dialog: MrsDialogKind;
    title: string;
    /** The dialog's fields; their type depends on `dialog`. */
    values: unknown;
    /** What it offers besides them; its type depends on `dialog`. */
    context: unknown;
}

/** A save that did not go through; the dialog stays open. */
export interface IMrsSaveErrorMessage {
    type: "saveError";
    message: string;
}

/** The folder or file picked for a field. */
export interface IMrsBrowsedMessage {
    type: "browsed";
    field: string;
    path: string;
}

/** The columns of a referenced table, for the data mapping editor. */
export interface IMrsColumnsMessage {
    type: "columns";
    requestId: number;
    columns?: IMrsColumns;
    error?: string;
}

/** What a folder's MRS scripts define, for the content set dialog. */
export interface IMrsScriptsMessage {
    type: "scripts";
    directory: string;
    language?: string;
    definitions?: IMrsScriptDefinitions;
    error?: string;
}

export type MrsHostMessage =
    | IMrsLoadMessage
    | IMrsSaveErrorMessage
    | IMrsBrowsedMessage
    | IMrsColumnsMessage
    | IMrsScriptsMessage
    | IBusyMessage;

/** Save the dialog's fields. */
export interface IMrsSaveMessage {
    type: "save";
    values: unknown;
}

/** Pick a folder or a file for a field. */
export interface IMrsBrowseMessage {
    type: "browse";
    field: string;
    folders: boolean;
}

/** Load the columns of a table a reference points to. */
export interface IMrsLoadColumnsMessage {
    type: "loadColumns";
    requestId: number;
    schema: string;
    table: string;
}

/** Look for MRS scripts in a folder. */
export interface IMrsAnalyzeFolderMessage {
    type: "analyzeFolder";
    directory: string;
    ignoreList: string;
}

/** Put text on the clipboard: the SQL preview's copy button. */
export interface IMrsCopyMessage {
    type: "copy";
    text: string;
}

export type MrsWebviewMessage =
    | IReadyMessage
    | ICancelMessage
    | IMrsSaveMessage
    | IMrsBrowseMessage
    | IMrsLoadColumnsMessage
    | IMrsAnalyzeFolderMessage
    | IMrsCopyMessage;
