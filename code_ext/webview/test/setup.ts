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
 * The webview bridge VS Code injects. The frontend takes it at module
 * load, so it has to exist before anything under `webview/src` is
 * imported.
 */

export interface IPostedMessage {
    type: string;
    [key: string]: unknown;
}

/** Everything the frontend posted to the extension. */
export const posted: IPostedMessage[] = [];

/**
 * Installs the `acquireVsCodeApi` global.
 *
 * @returns Nothing.
 */
export const installVsCodeApi = (): void => {
    let state: unknown;
    (globalThis as Record<string, unknown>).acquireVsCodeApi = () => {
        return {
            postMessage: (message: IPostedMessage) => {
                posted.push(message);
            },
            getState: () => {
                return state;
            },
            setState: (value: unknown) => {
                state = value;
            },
        };
    };
};

installVsCodeApi();
