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

import { describe, expect, it } from "vitest";

import { DOUBLE_CLICK_MS, DoubleClick } from "../../tree/doubleClick.js";

describe("DoubleClick", () => {
    it("takes a second click on the same row within the moment", () => {
        let now = 1000;
        const clicks = new DoubleClick(() => { return now; });

        expect(clicks.click("a")).toBe(false);
        now += DOUBLE_CLICK_MS - 1;
        expect(clicks.click("a")).toBe(true);
        // A third click starts over.
        now += 10;
        expect(clicks.click("a")).toBe(false);
    });

    it("does not take clicks too far apart, or on another row", () => {
        let now = 0;
        const clicks = new DoubleClick(() => { return now; });

        clicks.click("a");
        now += DOUBLE_CLICK_MS + 1;
        expect(clicks.click("a")).toBe(false);
        now += 10;
        expect(clicks.click("b")).toBe(false);
    });
});
