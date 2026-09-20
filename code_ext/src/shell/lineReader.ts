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
 * Reassembles whole lines from a stream that arrives in arbitrary chunks.
 * Child process output is not delivered on line boundaries, so a progress
 * message can otherwise be split across two `data` events.
 */
export class LineReader {
    #buffer = "";

    public constructor(private readonly onLine: (line: string) => void) { }

    /**
     * Feeds a chunk in, emitting every complete line it contains.
     *
     * @param chunk The text that arrived.
     *
     * @returns Nothing.
     */
    public push(chunk: string): void {
        this.#buffer += chunk;

        let newline = this.#buffer.indexOf("\n");
        while (newline !== -1) {
            const line = this.#buffer.slice(0, newline).replace(/\r$/, "");
            this.#buffer = this.#buffer.slice(newline + 1);
            if (line.length > 0) {
                this.onLine(line);
            }
            newline = this.#buffer.indexOf("\n");
        }
    }

    /**
     * Emits whatever is left when the stream ends without a final newline.
     *
     * @returns Nothing.
     */
    public flush(): void {
        const rest = this.#buffer.trim();
        this.#buffer = "";
        if (rest.length > 0) {
            this.onLine(rest);
        }
    }
}
