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

import type { ISandboxFields } from "./sandboxFields.js";

/**
 * What the New Sandbox dialog's webview and its host say to each other.
 *
 * The root password crosses this boundary once, webview to host, on
 * `create`. The host never sends one back.
 */

/** The dialog's starting state, sent once the webview says it is ready. */
export interface ISandboxLoadMessage {
    type: "load";
    fields: ISandboxFields;
    /** The ports of the sandboxes deployed already, which a new one cannot take. */
    takenPorts: number[];
    /**
     * The latest release of every series a deploy can download, NEWEST
     * first; `fields.serverVersion` starts on the newest major version.
     * Empty where they could not be listed, which leaves the server on the
     * PATH as the one choice offered - and the one started on.
     */
    versions: string[];
}

/** A deploy that did not go through; the dialog stays open. */
export interface ISandboxErrorMessage {
    type: "createError";
    message: string;
}

/** Whether the deploy is under way. */
export interface ISandboxBusyMessage {
    type: "busy";
    busy: boolean;
}

export type SandboxHostMessage =
    | ISandboxLoadMessage
    | ISandboxErrorMessage
    | ISandboxBusyMessage;

/** The webview is up and wants its state. */
export interface ISandboxReadyMessage {
    type: "ready";
}

/** Deploy a sandbox with these fields. */
export interface ISandboxCreateMessage {
    type: "create";
    fields: ISandboxFields;
}

/** Close without deploying. */
export interface ISandboxCancelMessage {
    type: "cancel";
}

export type SandboxWebviewMessage =
    | ISandboxReadyMessage
    | ISandboxCreateMessage
    | ISandboxCancelMessage;
