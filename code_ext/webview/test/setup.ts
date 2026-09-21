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

/**
 * The layout APIs jsdom does not implement.
 *
 * The page measures its tab strip and scrolls a tab into view, and
 * jsdom has neither `ResizeObserver` nor the scroll methods - without
 * these the calls throw inside an effect, where the failure is easy to
 * miss and every test after it is running against a page that never
 * finished rendering.
 *
 * They measure nothing: jsdom reports every element as zero sized, so
 * what they are is stubs that let the code under test run.
 *
 * @returns Nothing.
 */
export const installLayoutStubs = (): void => {
    class NoLayoutResizeObserver implements ResizeObserver {
        public observe(): void { /* nothing to measure */ }
        public unobserve(): void { /* nothing to measure */ }
        public disconnect(): void { /* nothing to measure */ }
    }

    (globalThis as Record<string, unknown>).ResizeObserver =
        NoLayoutResizeObserver;
    Element.prototype.scrollIntoView = function scrollIntoView(): void {
        // Nowhere to scroll.
    };
    Element.prototype.scrollBy = function scrollBy(): void {
        // Nowhere to scroll.
    };
};

installLayoutStubs();
