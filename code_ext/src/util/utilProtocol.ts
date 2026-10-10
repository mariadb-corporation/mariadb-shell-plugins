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
import type { IUtilOperationSpec, UtilValues } from "./utilFields.js";

/** What the dump, load, copy, export and import dialog is told. */
export interface IUtilLoadMessage {
    type: "load";
    spec: IUtilOperationSpec;
    values: UtilValues;
    /** The connections a copy can go to, by address. */
    targets: string[];
}

/** A path picked with Browse. */
export interface IUtilBrowsedMessage {
    type: "browsed";
    path: string;
}

export interface IUtilStartErrorMessage {
    type: "startError";
    message: string;
    /** The field the problem is in, if it is in one. */
    field?: string;
}

export type UtilHostMessage =
    | IUtilLoadMessage
    | IUtilBrowsedMessage
    | IUtilStartErrorMessage
    | IBusyMessage;

/** Browse was pressed: the extension opens a folder or file picker. */
export interface IUtilBrowseMessage {
    type: "browse";
    current: string;
}

export interface IUtilStartMessage {
    type: "start";
    values: UtilValues;
}

export type UtilWebviewMessage =
    | IReadyMessage
    | IUtilBrowseMessage
    | IUtilStartMessage
    | ICancelMessage;
