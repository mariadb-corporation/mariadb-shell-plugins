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

/**
 * The messages every dialog of the extension exchanges with its host,
 * whatever it edits: the connection editor and the New Sandbox dialog add
 * their own to these.
 */

/** Whether a long running button should be showing itself as busy. */
export interface IBusyMessage {
    type: "busy";
    busy: boolean;
}

/** The webview is up and wants its state. */
export interface IReadyMessage {
    type: "ready";
}

/** Close without saving. */
export interface ICancelMessage {
    type: "cancel";
}
