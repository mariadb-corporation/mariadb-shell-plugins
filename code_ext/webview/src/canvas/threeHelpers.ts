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

import * as THREE from "three";

import type { IRect } from "../../../src/modeler/pageSettings.js";
import type { IPoint, Segment } from "./geometry.js";

/**
 * The three.js pieces the canvas and its figure sets share: how the page's
 * coordinates map onto the plane the canvas draws on, the order things are
 * drawn in, and a few shapes.
 *
 * The canvas's two groups lie in the page's plane: x to the right, and the
 * page's y DOWN the page as -y, with z pointing at the 2D view's camera. A
 * page point (x, y) is the group's local (x, -y, z).
 */

/**
 * The order the canvas's parts are drawn in, back to front. Most of them
 * are transparent, and three.js otherwise sorts transparent meshes by
 * their distance from the camera - which, tilted, puts a large plate in
 * front of what lies on it. A figure set gives its objects one of these,
 * plus a fraction to order within a step.
 */
export const DRAW_ORDER = {
    pages: 0,
    grid: 1,
    /** What lies on the page: layers, areas. */
    base: 2,
    shadows: 3,
    /** What rises off the page in the 3D view: tables, cards. */
    lifted: 4,
    connections: 5,
    /** The canvas's selection outlines and handles. */
    decorations: 6,
} as const;

/**
 * Empties a group, releasing its meshes' and lines' GPU resources.
 *
 * @param group The group.
 */
export const disposeGroup = (group: THREE.Object3D): void => {
    group.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
            const material = object.material as THREE.Material & {
                map?: THREE.Texture;
            };
            material.map?.dispose();
            material.dispose();
            object.geometry.dispose();
        }
    });
    group.clear();
};

/**
 * Sets the draw order of an object and everything in it.
 *
 * @param object The object.
 * @param order Its order (see `DRAW_ORDER`).
 */
export const setDrawOrder = (object: THREE.Object3D, order: number): void => {
    object.traverse((child) => { child.renderOrder = order; });
};

/**
 * Makes line geometry out of segments in page coordinates.
 *
 * @param segments The segments.
 * @param z The height above the plane.
 *
 * @returns The geometry.
 */
export const segmentsGeometry = (
    segments: Segment[],
    z: number,
): THREE.BufferGeometry => {
    const positions = new Float32Array(segments.length * 6);
    segments.forEach(([from, to], index) => {
        positions.set([from.x, -from.y, z, to.x, -to.y, z], index * 6);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position",
        new THREE.BufferAttribute(positions, 3));

    return geometry;
};

/**
 * The outline of a rectangle as segments.
 *
 * @param rect The rectangle.
 *
 * @returns Its four sides.
 */
export const rectSegments = (rect: IRect): Segment[] => {
    const right = rect.left + rect.width;
    const bottom = rect.top + rect.height;

    return [
        [{ x: rect.left, y: rect.top }, { x: right, y: rect.top }],
        [{ x: right, y: rect.top }, { x: right, y: bottom }],
        [{ x: right, y: bottom }, { x: rect.left, y: bottom }],
        [{ x: rect.left, y: bottom }, { x: rect.left, y: rect.top }],
    ];
};

/**
 * A polyline as segments.
 *
 * @param points The points.
 *
 * @returns The segments between them.
 */
export const polylineSegments = (points: IPoint[]): Segment[] => {
    const segments: Segment[] = [];
    for (let index = 1; index < points.length; index += 1) {
        segments.push([points[index - 1], points[index]]);
    }

    return segments;
};

/**
 * A rounded rectangle in the plane's coordinates (y up).
 *
 * @param x Its left edge.
 * @param y Its BOTTOM edge.
 * @param w Its width.
 * @param h Its height.
 * @param r The corner radius; less where the rectangle is too small.
 *
 * @returns The shape.
 */
export const roundedRect = (
    x: number,
    y: number,
    w: number,
    h: number,
    r: number,
): THREE.Shape => {
    const radius = Math.max(0, Math.min(r, w / 2, h / 2));
    // Circular corners, as Canvas2D's `roundRect` draws the cards; a
    // quadratic curve bulges past a circle's arc.
    const shape = new THREE.Shape().moveTo(x + radius, y);
    shape.lineTo(x + w - radius, y);
    shape.absarc(x + w - radius, y + radius, radius, -Math.PI / 2, 0, false);
    shape.lineTo(x + w, y + h - radius);
    shape.absarc(x + w - radius, y + h - radius, radius, 0, Math.PI / 2,
        false);
    shape.lineTo(x + radius, y + h);
    shape.absarc(x + radius, y + h - radius, radius, Math.PI / 2, Math.PI,
        false);
    shape.lineTo(x, y + radius);
    shape.absarc(x + radius, y + radius, radius, Math.PI, Math.PI * 1.5,
        false);

    return shape;
};

/**
 * @param rect A rectangle on the page.
 *
 * @returns Its centre in the plane's coordinates (y up).
 */
export const planeCenter = (rect: IRect): { x: number; y: number } => {
    return {
        x: rect.left + rect.width / 2,
        y: -(rect.top + rect.height / 2),
    };
};
