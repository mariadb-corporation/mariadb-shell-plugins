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
    crowsFoot,
    MARKER_SIZE,
    oneMarker,
    routeConnection,
    splitStubs,
} from "../../src/modeler/connectionGeometry.js";

describe("routeConnection", () => {
    const a = { left: 0, top: 0, width: 100, height: 50 };

    it("leaves and enters the facing sides of figures side by side", () => {
        const b = { left: 300, top: 100, width: 100, height: 50 };
        const route = routeConnection(a, b);

        expect(route.startSide).toBe("right");
        expect(route.endSide).toBe("left");
        expect(route.points[0]).toEqual({ x: 100, y: 25 });
        expect(route.points[route.points.length - 1]).toEqual({ x: 300, y: 125 });
        // One middle segment, half way between.
        expect(route.points[1].x).toBe(200);
        expect(route.points[2].x).toBe(200);

        const back = routeConnection(b, a);
        expect(back.startSide).toBe("left");
        expect(back.endSide).toBe("right");
    });

    it("goes up and down between figures above each other", () => {
        const b = { left: 20, top: 200, width: 100, height: 50 };
        const route = routeConnection(a, b);

        expect(route.startSide).toBe("bottom");
        expect(route.endSide).toBe("top");
        expect(route.points[0]).toEqual({ x: 50, y: 50 });
        expect(route.points[3]).toEqual({ x: 70, y: 200 });
    });

    it("loops out and back in for a self reference", () => {
        const route = routeConnection(a, a);

        expect(route.startSide).toBe("right");
        expect(route.endSide).toBe("top");
        expect(route.points[route.points.length - 1]).toEqual({ x: 50, y: 0 });
    });
});

describe("splitStubs", () => {
    it("draws a stub out of each figure instead of the whole route", () => {
        const route = routeConnection(
            { left: 0, top: 0, width: 100, height: 50 },
            { left: 900, top: 400, width: 100, height: 50 });
        const stubs = splitStubs(route, 40);

        expect(stubs).toEqual([
            [{ x: 100, y: 25 }, { x: 140, y: 25 }],
            [{ x: 900, y: 425 }, { x: 860, y: 425 }],
        ]);
    });

    it("follows the sides a vertical route leaves and enters", () => {
        const route = routeConnection(
            { left: 0, top: 0, width: 100, height: 50 },
            { left: 0, top: 900, width: 100, height: 50 });

        expect(splitStubs(route, 40)).toEqual([
            [{ x: 50, y: 50 }, { x: 50, y: 90 }],
            [{ x: 50, y: 900 }, { x: 50, y: 860 }],
        ]);
    });
});

describe("the crow's foot notation", () => {
    it("draws three prongs, and a bar where mandatory", () => {
        const optional = crowsFoot({ x: 100, y: 25 }, "right", false);
        expect(optional).toHaveLength(3);
        // The prongs meet on the line, a marker's length out.
        expect(optional[0][0]).toEqual({ x: 100 + MARKER_SIZE, y: 25 });
        expect(optional[0][1]).toEqual({ x: 100, y: 25 });
        expect(optional[1][1].y).toBeCloseTo(25 + MARKER_SIZE / 2, 5);
        expect(optional[2][1].y).toBeCloseTo(25 - MARKER_SIZE / 2, 5);

        expect(crowsFoot({ x: 100, y: 25 }, "right", true)).toHaveLength(4);
    });

    it("draws one bar for one, two for exactly one", () => {
        expect(oneMarker({ x: 0, y: 0 }, "left", false)).toHaveLength(1);
        const two = oneMarker({ x: 0, y: 0 }, "left", true);
        expect(two).toHaveLength(2);
        // Across the line, which leaves to the left.
        expect(two[0][0].x).toBeCloseTo(-MARKER_SIZE / 2, 5);
        expect(two[0][0].x).toBeCloseTo(two[0][1].x, 5);
    });
});
