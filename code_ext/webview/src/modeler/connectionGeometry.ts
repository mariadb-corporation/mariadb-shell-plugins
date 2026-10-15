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

import type { IRect } from "../../../src/modeler/pageSettings.js";
import type { IPoint, Segment } from "../canvas/geometry.js";

/**
 * How the EER diagram draws a foreign key between two tables: an
 * orthogonal route out of the sides that face each other, the crow's
 * foot notation at its ends, and the stubs of a connection drawn split.
 * Pure, so it can be tested without WebGL.
 */

/** The side of a figure a connection leaves or enters. */
export type Side = "left" | "right" | "top" | "bottom";

/** A connection's route. */
export interface IRoute {
    /** The polyline, from the start figure's edge to the end figure's. */
    points: IPoint[];
    startSide: Side;
    endSide: Side;
}

/**
 * Routes a connection between two figures as an orthogonal line: out of
 * the sides that face each other, with one middle segment where the
 * figures are side by side or above each other, and a Z where they
 * overlap in neither direction.
 *
 * @param start The start figure's rectangle (the foreign key's table).
 * @param end The end figure's rectangle (the referenced table).
 *
 * @returns The route.
 */
export const routeConnection = (start: IRect, end: IRect): IRoute => {
    const startRight = start.left + start.width;
    const startBottom = start.top + start.height;
    const endRight = end.left + end.width;
    const endBottom = end.top + end.height;
    const startCenter = {
        x: start.left + start.width / 2,
        y: start.top + start.height / 2,
    };
    const endCenter = {
        x: end.left + end.width / 2,
        y: end.top + end.height / 2,
    };

    const horizontalGap = Math.max(end.left - startRight,
        start.left - endRight);
    const verticalGap = Math.max(end.top - startBottom,
        start.top - endBottom);

    // A self reference, or figures on top of each other: a loop out of
    // the right side and back in at the top.
    if (horizontalGap < 0 && verticalGap < 0) {
        const outX = Math.max(startRight, endRight) + 20;
        const points: IPoint[] = [
            { x: startRight, y: startCenter.y },
            { x: outX, y: startCenter.y },
            { x: outX, y: end.top - 20 },
            { x: endCenter.x, y: end.top - 20 },
            { x: endCenter.x, y: end.top },
        ];

        return { points, startSide: "right", endSide: "top" };
    }

    if (horizontalGap >= verticalGap) {
        const startIsLeft = startCenter.x < endCenter.x;
        const x1 = startIsLeft ? startRight : start.left;
        const x2 = startIsLeft ? end.left : endRight;
        const middle = (x1 + x2) / 2;

        return {
            points: [
                { x: x1, y: startCenter.y },
                { x: middle, y: startCenter.y },
                { x: middle, y: endCenter.y },
                { x: x2, y: endCenter.y },
            ],
            startSide: startIsLeft ? "right" : "left",
            endSide: startIsLeft ? "left" : "right",
        };
    }

    const startIsAbove = startCenter.y < endCenter.y;
    const y1 = startIsAbove ? startBottom : start.top;
    const y2 = startIsAbove ? end.top : endBottom;
    const middle = (y1 + y2) / 2;

    return {
        points: [
            { x: startCenter.x, y: y1 },
            { x: startCenter.x, y: middle },
            { x: endCenter.x, y: middle },
            { x: endCenter.x, y: y2 },
        ],
        startSide: startIsAbove ? "bottom" : "top",
        endSide: startIsAbove ? "top" : "bottom",
    };
};

/**
 * The direction a line takes away from a figure's side.
 *
 * @param side The side.
 *
 * @returns A unit vector pointing away from the figure.
 */
const outward = (side: Side): IPoint => {
    switch (side) {
        case "left": { return { x: -1, y: 0 }; }
        case "right": { return { x: 1, y: 0 }; }
        case "top": { return { x: 0, y: -1 }; }
        default: { return { x: 0, y: 1 }; }
    }
};

/** The size of the end markers in diagram units. */
export const MARKER_SIZE = 12;

/**
 * The crow's foot at the "many" end of a connection: three prongs from a
 * point on the line to the figure's edge, plus a bar for a mandatory
 * side.
 *
 * @param at Where the line meets the figure.
 * @param side The figure's side it meets.
 * @param mandatory Whether every row of the "one" side needs a row here;
 *                  drawn as a bar across the line.
 *
 * @returns The segments to draw.
 */
export const crowsFoot = (
    at: IPoint,
    side: Side,
    mandatory: boolean,
): Segment[] => {
    const out = outward(side);
    // Perpendicular to the line.
    const across = { x: -out.y, y: out.x };
    const root = { x: at.x + out.x * MARKER_SIZE, y: at.y + out.y * MARKER_SIZE };
    const half = MARKER_SIZE / 2;
    const segments: Segment[] = [
        [root, at],
        [root, { x: at.x + across.x * half, y: at.y + across.y * half }],
        [root, { x: at.x - across.x * half, y: at.y - across.y * half }],
    ];
    if (mandatory) {
        const bar = {
            x: root.x + out.x * (MARKER_SIZE / 2),
            y: root.y + out.y * (MARKER_SIZE / 2),
        };
        segments.push([
            { x: bar.x + across.x * half, y: bar.y + across.y * half },
            { x: bar.x - across.x * half, y: bar.y - across.y * half },
        ]);
    }

    return segments;
};

/**
 * The marker at the "one" end of a connection: a bar across the line, and
 * a second one for a mandatory side (a circle would say optional; the
 * model has no optional references).
 *
 * @param at Where the line meets the figure.
 * @param side The figure's side it meets.
 * @param mandatory Whether the reference is mandatory.
 *
 * @returns The segments to draw.
 */
export const oneMarker = (
    at: IPoint,
    side: Side,
    mandatory: boolean,
): Segment[] => {
    const out = outward(side);
    const across = { x: -out.y, y: out.x };
    const half = MARKER_SIZE / 2;
    const bar = (distance: number): Segment => {
        const center = { x: at.x + out.x * distance, y: at.y + out.y * distance };

        return [
            { x: center.x + across.x * half, y: center.y + across.y * half },
            { x: center.x - across.x * half, y: center.y - across.y * half },
        ];
    };
    const segments: Segment[] = [bar(MARKER_SIZE / 2)];
    if (mandatory) {
        segments.push(bar(MARKER_SIZE));
    }

    return segments;
};

/** How far a split connection's stubs reach out of their figures. */
const SPLIT_STUB_LENGTH = 40;

/**
 * The lines of a split connection (Workbench's "draw split"): instead of
 * the whole route, a short stub out of each figure's side, so a reference
 * across the diagram does not draw a line through it.
 *
 * @param route The connection's full route.
 * @param length How far each stub reaches.
 *
 * @returns The two stubs, start first.
 */
export const splitStubs = (
    route: IRoute,
    length = SPLIT_STUB_LENGTH,
): Segment[] => {
    const stub = (at: IPoint, side: Side): Segment => {
        const out = outward(side);

        return [at, { x: at.x + out.x * length, y: at.y + out.y * length }];
    };

    return [
        stub(route.points[0], route.startSide),
        stub(route.points[route.points.length - 1], route.endSide),
    ];
};
