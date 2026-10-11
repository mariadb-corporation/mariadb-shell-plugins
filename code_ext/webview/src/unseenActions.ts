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

import type { IActionRow } from "../../src/webview/protocol.js";

/**
 * Which actions the user has seen, for the Actions tab's badge: it counts
 * only what came in, or changed, while another tab was on show.
 *
 * A row is kept by its id with the kind it had when seen. A row is
 * replaced in place as what it stands for goes on - a run is pending, then
 * done or failed - so a run seen while pending that fails behind a result
 * tab counts again. Progress alone (a task's percentage, the lines under
 * it) does not change the kind, and is not something to be called back
 * for.
 */
export type SeenActions = Map<string, string>;

/**
 * Marks rows as seen.
 *
 * @param seen What has been seen so far; changed in place.
 * @param rows The rows, top level only.
 * @param which Which of them; all when left out.
 */
export const markSeen = (
    seen: SeenActions,
    rows: readonly IActionRow[],
    which: (row: IActionRow) => boolean = () => { return true; },
): void => {
    for (const row of rows) {
        if (which(row)) {
            seen.set(row.id, row.kind);
        }
    }
};

/**
 * @param seen What has been seen.
 * @param rows The rows, top level only.
 *
 * @returns How many of them have not been seen as they are now.
 */
export const unseenCount = (
    seen: SeenActions,
    rows: readonly IActionRow[],
): number => {
    return rows.filter((row) => {
        return seen.get(row.id) !== row.kind;
    }).length;
};

/**
 * @param row A row.
 *
 * @returns Whether a run's result set opening on its own tab tells the
 *          user enough about it: it worked, or is still under way. A
 *          warning or an error is more than its result says.
 */
export const shownByItsResult = (row: IActionRow): boolean => {
    return row.kind === "info" || row.kind === "pending";
};
