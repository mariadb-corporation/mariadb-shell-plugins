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
import { Vector3 } from "three";

import {
    affineTransform,
    customPreset,
    CUSTOM_ISO_PRESET,
    DEFAULT_ISO_PRESET,
    easeInOut,
    fitZoom,
    GRID,
    gridSpacing,
    ISO_PRESETS,
    isoPreset,
    isoQuaternion,
    liftFactor,
    MAX_TILT,
    MIN_TILT,
    ORBIT_SPEED,
    orbitAngles,
    TOP_VIEW,
    viewQuaternion,
} from "../../src/canvas/geometry.js";
import { cssPixelsPerMm, pixelsPerUnit } from "../../src/canvas/screen.js";

describe("the views", () => {
    it("look down at the diagram from the top, with the page's top up", () => {
        const forward = new Vector3(0, 0, -1).applyQuaternion(TOP_VIEW);
        const up = new Vector3(0, 1, 0).applyQuaternion(TOP_VIEW);

        expect(forward.y).toBeCloseTo(-1, 5);
        // The diagram's y runs along +Z, so its top is at -Z.
        expect(up.z).toBeCloseTo(-1, 5);
    });

    it("turn and tilt to each 3D view's angles", () => {
        for (const preset of ISO_PRESETS) {
            const forward = new Vector3(0, 0, -1)
                .applyQuaternion(isoQuaternion(preset));
            // Turned about the up axis, from the page's bottom left corner:
            // towards +X and -Z.
            expect(Math.atan2(forward.x, -forward.z)).toBeCloseTo(preset.turn, 5);
            expect(forward.x).toBeGreaterThan(0);
            // Looking down by the tilt.
            expect(forward.y).toBeCloseTo(-Math.sin(preset.tilt), 5);
        }
    });

    it("has true isometric among the choices", () => {
        const forward = new Vector3(0, 0, -1)
            .applyQuaternion(isoQuaternion(isoPreset("isometric")));
        // The isometric property: the three components of equal size.
        expect(Math.abs(forward.x)).toBeCloseTo(Math.abs(forward.y), 5);
        expect(Math.abs(forward.x)).toBeCloseTo(Math.abs(forward.z), 5);
    });

    it("falls back to the default for an unknown choice", () => {
        expect(isoPreset("nonsense").id).toBe(DEFAULT_ISO_PRESET);
        expect(isoPreset(undefined).id).toBe(DEFAULT_ISO_PRESET);
    });

    it("blend from one to the other", () => {
        const iso = isoQuaternion(isoPreset("steep"));
        expect(viewQuaternion(0, iso).angleTo(TOP_VIEW)).toBeCloseTo(0, 5);
        expect(viewQuaternion(1, iso).angleTo(iso)).toBeCloseTo(0, 5);
        const half = viewQuaternion(0.5, iso);
        expect(half.angleTo(TOP_VIEW)).toBeCloseTo(half.angleTo(iso), 5);
        // Clamped.
        expect(viewQuaternion(2, iso).angleTo(iso)).toBeCloseTo(0, 5);
    });

    it("ease in and out", () => {
        expect(easeInOut(0)).toBe(0);
        expect(easeInOut(0.5)).toBeCloseTo(0.5, 5);
        expect(easeInOut(1)).toBe(1);
        expect(easeInOut(0.25)).toBeLessThan(0.25);
    });
});

describe("affineTransform", () => {
    /** Applies a CSS matrix() to a point. */
    const apply = (transform: string, x: number, y: number): number[] => {
        const [a, b, c, d, e, f] = /matrix\((.*)\)/.exec(transform)![1]
            .split(",").map(Number);

        return [a * x + c * y + e, b * x + d * y + f];
    };

    it("is a translation where the box lands unchanged", () => {
        expect(affineTransform({ x: 10, y: 20 }, { x: 110, y: 20 },
            { x: 10, y: 40 }, 100, 20)).toBe("matrix(1, 0, 0, 1, 10, 20)");
    });

    it("puts the box's corners onto a tilted parallelogram", () => {
        const topLeft = { x: 50, y: 80 };
        const topRight = { x: 130, y: 40 };
        const bottomLeft = { x: 62, y: 95 };
        const transform = affineTransform(topLeft, topRight, bottomLeft,
            200, 30);

        expect(apply(transform, 0, 0)).toEqual([50, 80]);
        const right = apply(transform, 200, 0);
        expect(right[0]).toBeCloseTo(130, 5);
        expect(right[1]).toBeCloseTo(40, 5);
        const bottom = apply(transform, 0, 30);
        expect(bottom[0]).toBeCloseTo(62, 5);
        expect(bottom[1]).toBeCloseTo(95, 5);
        // The fourth corner follows: a parallelogram.
        const corner = apply(transform, 200, 30);
        expect(corner[0]).toBeCloseTo(142, 5);
        expect(corner[1]).toBeCloseTo(55, 5);
    });
});

describe("gridSpacing", () => {
    it("keeps the base grid where its sublines show well", () => {
        // 10 mm main lines, 2 mm sublines: at 4.29 px/mm (life size on
        // the Studio Display) the sublines are 8.6 px apart.
        const spacing = gridSpacing(4.29);

        expect(spacing.mainMm).toBe(10);
        expect(spacing.subMm).toBe(2);
        expect(spacing.subOpacity).toBeGreaterThan(0);
        expect(spacing.subOpacity).toBeLessThan(1);
    });

    it("makes the main lines sublines of a wider grid zooming out", () => {
        // 2 mm sublines would be 2 px apart: the 10 mm lines become the
        // sublines of a 50 mm grid.
        expect(gridSpacing(1)).toMatchObject({ mainMm: 50, subMm: 10 });
        expect(gridSpacing(0.1)).toMatchObject({ mainMm: 1250, subMm: 250 });
    });

    it("makes the sublines main lines zooming in", () => {
        // 0.4 mm sublines are 12 px apart at 30 px/mm: the 2 mm lines are
        // main lines now.
        expect(gridSpacing(30)).toMatchObject({ mainMm: 2, subMm: 0.4 });
    });

    it("never draws sublines closer than the limit, and fades them in", () => {
        for (let pxPerMm = 0.05; pxPerMm < 60; pxPerMm *= 1.07) {
            const spacing = gridSpacing(pxPerMm);
            const subPx = spacing.subMm * pxPerMm;
            expect(subPx).toBeGreaterThanOrEqual(GRID.minSubPx);
            // Never so wide that a finer level would show.
            expect(subPx / GRID.subdivisions).toBeLessThan(GRID.minSubPx);
        }
        expect(gridSpacing(GRID.minSubPx / 2).subOpacity).toBe(0);
        expect(gridSpacing((GRID.minSubPx + GRID.fadePx) / 2).subOpacity)
            .toBe(1);
    });
});

describe("the screen scale", () => {
    it("makes a page life size on the Studio Display", () => {
        // 218 ppi at the default 2x scaling: 109 CSS px per inch.
        expect(cssPixelsPerMm(2)).toBeCloseTo(109 / 25.4, 5);
        // An A4 sheet's 297 mm come out at about 1275 CSS pixels.
        expect(297 * cssPixelsPerMm(2)).toBeCloseTo(1274.5, 0);
        // With "More Space" the same sheet takes more CSS pixels.
        expect(cssPixelsPerMm(1.5)).toBeGreaterThan(cssPixelsPerMm(2));
    });

    it("turns the zoom into pixels per diagram unit", () => {
        expect(pixelsPerUnit(1, 5, 2)).toBeCloseTo(cssPixelsPerMm(2) / 5, 5);
        expect(pixelsPerUnit(2, 5, 2)).toBeCloseTo(2 * pixelsPerUnit(1, 5, 2), 5);
    });

    it("fits a rectangle into a viewport", () => {
        const zoom = fitZoom({ width: 1000, height: 500 },
            { left: 0, top: 0, width: 2000, height: 500 }, 1, 0);

        expect(zoom).toBe(0.5);
    });
});

describe("orbiting", () => {
    it("turns across and tilts down, as the user's own view", () => {
        const from = { tilt: MAX_TILT, turn: 0 };
        const angles = orbitAngles(from, 100, -50);

        expect(angles.id).toBe(CUSTOM_ISO_PRESET);
        expect(angles.turn).toBeCloseTo(100 * ORBIT_SPEED, 5);
        expect(angles.tilt).toBeCloseTo(MAX_TILT - 50 * ORBIT_SPEED, 5);
        expect(angles.caption).toBe(customPreset(angles.tilt, angles.turn)
            .caption);
    });

    it("keeps the tilt in its range and the turn within one turn", () => {
        expect(orbitAngles({ tilt: MAX_TILT, turn: 0 }, 0, 1000).tilt)
            .toBe(MAX_TILT);
        expect(orbitAngles({ tilt: MAX_TILT, turn: 0 }, 0, -10000).tilt)
            .toBe(MIN_TILT);
        const turn = orbitAngles({ tilt: 1, turn: 3 }, 2000, 0).turn;
        expect(turn).toBeGreaterThan(-Math.PI - 1e-9);
        expect(turn).toBeLessThanOrEqual(Math.PI + 1e-9);
    });

    it("names the user's own view by its angles in degrees", () => {
        expect(customPreset(Math.PI / 4, -Math.PI / 6).caption)
            .toBe("Custom (45° tilt, -30° turn)");
    });

    it("lifts the figures as the camera tips over", () => {
        expect(liftFactor(MAX_TILT)).toBe(0);
        expect(liftFactor(MAX_TILT - Math.PI / 12)).toBeCloseTo(0.5, 5);
        for (const preset of ISO_PRESETS) {
            expect(liftFactor(preset.tilt)).toBe(1);
        }
    });
});
