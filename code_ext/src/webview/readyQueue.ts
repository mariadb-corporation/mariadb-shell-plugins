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
 * Holds messages for a webview until its frontend says it is listening.
 *
 * A webview's script is not up the moment the HTML is set, and a message
 * posted before it is would be lost, so everything is held until the
 * frontend's `ready` arrives and then delivered in the order it was sent.
 */
export class ReadyQueue<T> {
    #ready = false;
    #pending: T[] = [];

    /**
     * @param deliver Posts a message to the webview.
     */
    public constructor(private readonly deliver: (message: T) => void) { }

    /**
     * Delivers a message, or holds it until the frontend is listening.
     *
     * @param message The message to send.
     *
     * @returns Nothing.
     */
    public post(message: T): void {
        if (!this.#ready) {
            this.#pending.push(message);

            return;
        }

        this.deliver(message);
    }

    /**
     * The frontend is listening: everything held is delivered, in order.
     *
     * @returns Nothing.
     */
    public ready(): void {
        this.#ready = true;
        const pending = this.#pending;
        this.#pending = [];
        for (const message of pending) {
            this.deliver(message);
        }
    }

    /**
     * Back to waiting, dropping anything held - for a webview that is gone
     * or is being created afresh.
     *
     * @returns Nothing.
     */
    public reset(): void {
        this.#ready = false;
        this.#pending = [];
    }
}
