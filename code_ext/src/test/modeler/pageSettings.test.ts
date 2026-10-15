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

import {
    DEFAULT_PAGE_SETTINGS,
    layoutPages,
    pageCount,
    paperBounds,
    paperSize,
    printableSize,
} from "../../modeler/pageSettings.js";

/** The MRS diagram's size, as the model file has it. */
const mrsDiagram = { width: 1972, height: 1380.95 };

describe("page settings", () => {
    it("has the Workbench defaults: A4 portrait at 5 units per mm", () => {
        expect(paperSize(DEFAULT_PAGE_SETTINGS)).toEqual({
            width: 210, height: 297,
        });
        expect(paperSize({ ...DEFAULT_PAGE_SETTINGS, orientation: "landscape" }))
            .toEqual({ width: 297, height: 210 });
    });

    it("takes the margins off the printable area", () => {
        const printable = printableSize(DEFAULT_PAGE_SETTINGS);

        expect(printable.width).toBeCloseTo(986.5, 5);
        expect(printable.height).toBeCloseTo(1380.95, 5);
    });

    it("sees the MRS diagram on two pages side by side", () => {
        expect(pageCount(DEFAULT_PAGE_SETTINGS, mrsDiagram))
            .toEqual({ horizontal: 2, vertical: 1 });
    });

    it("lays the pages out with the printable areas tiling the diagram", () => {
        const pages = layoutPages(DEFAULT_PAGE_SETTINGS, mrsDiagram);

        expect(pages).toHaveLength(2);
        expect(pages[0].printable.left).toBe(0);
        expect(pages[1].printable.left).toBeCloseTo(986.5, 5);
        // The sheet extends by the margins around its printable area.
        expect(pages[0].paper.left).toBeCloseTo(-6.35 * 5, 5);
        expect(pages[0].paper.top).toBeCloseTo(-6.35 * 5, 5);
        expect(pages[0].paper.width).toBeCloseTo(210 * 5, 5);
        expect(pages[0].paper.height).toBeCloseTo(297 * 5, 5);

        const bounds = paperBounds(pages);
        // Two sheets: the diagram is a unit short of two printable areas.
        expect(bounds.width).toBeCloseTo(2 * 986.5 + 12.7 * 5, 5);
        expect(bounds.height).toBeCloseTo(297 * 5, 5);
    });

    it("never has fewer than one page", () => {
        expect(pageCount(DEFAULT_PAGE_SETTINGS, { width: 10, height: 10 }))
            .toEqual({ horizontal: 1, vertical: 1 });
    });
});
