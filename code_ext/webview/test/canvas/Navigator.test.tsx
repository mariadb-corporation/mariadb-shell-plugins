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

import { ZOOM_RANGE } from "../../src/canvas/Canvas.js";
import { sliderToZoom, zoomToSlider } from "../../src/canvas/Navigator.js";

describe("the Navigator's zoom slider", () => {
    it("spans the zoom range on a log scale", () => {
        expect(zoomToSlider(ZOOM_RANGE.min, ZOOM_RANGE)).toBe(0);
        expect(zoomToSlider(ZOOM_RANGE.max, ZOOM_RANGE)).toBe(1000);
        expect(sliderToZoom(0, ZOOM_RANGE)).toBeCloseTo(ZOOM_RANGE.min, 9);
        expect(sliderToZoom(1000, ZOOM_RANGE)).toBeCloseTo(ZOOM_RANGE.max, 9);
        // The middle of the slider is the geometric middle of the range.
        expect(sliderToZoom(500, ZOOM_RANGE)).toBeCloseTo(
            Math.sqrt(ZOOM_RANGE.min * ZOOM_RANGE.max), 9);
    });

    it("goes back and forth without drifting", () => {
        for (const zoom of [0.1, 0.5, 1, 2.5, 7]) {
            expect(sliderToZoom(zoomToSlider(zoom, ZOOM_RANGE), ZOOM_RANGE))
                .toBeCloseTo(zoom, 1);
        }
    });
});
