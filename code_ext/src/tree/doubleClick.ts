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

/** How close two clicks on one row are to count as a double-click. */
export const DOUBLE_CLICK_MS = 500;

/**
 * Tells a double-click on a tree row from a single one.
 *
 * VS Code's tree views have no double-click: a row's command runs on every
 * click. So the command reports each click here, and the second click on
 * the same row within `DOUBLE_CLICK_MS` is a double-click - which a third
 * does not extend.
 */
export class DoubleClick {
    #last?: { key: string; at: number };

    /**
     * @param now The clock, in milliseconds; a test brings its own.
     */
    public constructor(private readonly now: () => number = Date.now) { }

    /**
     * Reports a click.
     *
     * @param key What was clicked, as a string that tells rows apart.
     *
     * @returns Whether it completes a double-click.
     */
    public click(key: string): boolean {
        const at = this.now();
        const last = this.#last;
        if (last !== undefined && last.key === key
            && at - last.at <= DOUBLE_CLICK_MS) {
            this.#last = undefined;

            return true;
        }
        this.#last = { key, at };

        return false;
    }
}
