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

import type { IModelLayer } from "../../../src/modeler/modelFormat.js";
import {
    containsRect,
    fitAround,
    LAYER_LABEL_ROOM,
    LAYER_PADDING,
    layerAtPoint,
    layerUnder,
    MIN_LAYER_WIDTH,
} from "../../src/modeler/layerRules.js";

const layer = (
    id: string,
    left: number,
    top: number,
    width: number,
    height: number,
): IModelLayer => {
    return { id, name: id, left, top, width, height, color: "#fff", figures: [] };
};

const big = layer("big", 0, 0, 1000, 1000);
const small = layer("small", 100, 100, 300, 300);
const apart = layer("apart", 2000, 0, 200, 200);
const layers = [big, small, apart];

describe("layer rules", () => {
    it("counts a table as on a layer only when fully inside", () => {
        expect(containsRect(small, { left: 120, top: 120, width: 50, height: 50 }))
            .toBe(true);
        // Sticking out on the right.
        expect(containsRect(small, { left: 380, top: 120, width: 50, height: 50 }))
            .toBe(false);
        // Edges touching count as inside.
        expect(containsRect(small, { left: 100, top: 100, width: 300, height: 300 }))
            .toBe(true);
    });

    it("files a table under the smallest layer it lies on", () => {
        expect(layerUnder({ left: 120, top: 120, width: 50, height: 50 }, layers))
            .toBe(small);
        // Half on the small one: the big one holds it whole.
        expect(layerUnder({ left: 380, top: 120, width: 50, height: 50 }, layers))
            .toBe(big);
        expect(layerUnder({ left: 1500, top: 0, width: 50, height: 50 }, layers))
            .toBeUndefined();
    });

    it("finds the inner layer under a point", () => {
        expect(layerAtPoint({ x: 200, y: 200 }, layers)).toBe(small);
        expect(layerAtPoint({ x: 600, y: 600 }, layers)).toBe(big);
        expect(layerAtPoint({ x: 1500, y: 600 }, layers)).toBeUndefined();
    });

    it("fits a layer around tables, with room for its name", () => {
        expect(fitAround([
            { left: 100, top: 200, width: 150, height: 100 },
            { left: 300, top: 250, width: 100, height: 200 },
        ])).toEqual({
            left: 100 - LAYER_PADDING,
            top: 200 - LAYER_LABEL_ROOM,
            width: 300 + 2 * LAYER_PADDING,
            height: 250 + LAYER_LABEL_ROOM + LAYER_PADDING,
        });
        expect(fitAround([])).toBeUndefined();
        // Never less than a layer's least size.
        expect(fitAround([{ left: 0, top: 0, width: 5, height: 5 }])?.width)
            .toBe(MIN_LAYER_WIDTH);
    });
});
