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

import * as assert from "node:assert/strict";

import {
    menuConfirm,
    CONNECTION,
    hasTreeItem,
    openSection,
    SERVER_TIMEOUT,
    waitFor,
    waitGone,
    SANDBOX_ROW,
} from "../lib/ui";

/**
 * Deletes the sandbox the tests deployed, from the Sandboxes view, and
 * checks its connection goes with it.
 */
describe("Cleaning up", () => {
    it("deletes the sandbox, and its connection with it", async () => {
        const sandboxes = await openSection("Sandboxes");
        assert.match(await menuConfirm(sandboxes, [SANDBOX_ROW],
            ["Delete Sandbox"], "Delete"), /localhost:\d+|sandbox/i);
        await waitFor(async () => {
            return !await hasTreeItem(sandboxes, [SANDBOX_ROW]);
        }, "the sandbox to go", SERVER_TIMEOUT * 2);

        const connections = await openSection("Connections");
        await waitGone(connections, CONNECTION);
    });
});
