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

import { Euler, Quaternion } from "three";

import type { IRect } from "../../../src/modeler/pageSettings.js";

/**
 * The canvas's geometry that needs no scene: the camera orientations of
 * the 2D and 3D views and the blend between them, the grid's spacing, and
 * the transforms that lay HTML elements onto the page. Pure, so it can be
 * tested without WebGL.
 *
 * The page lies in the world's XZ plane, its x along X and its y (down
 * the page) along +Z, with Y up for stacking in the 3D view.
 */

/** A point in page coordinates. */
export interface IPoint {
    x: number;
    y: number;
}

/** A line segment in page coordinates. */
export type Segment = [IPoint, IPoint];

/** The camera looking straight down: the 2D view. */
export const TOP_VIEW = new Quaternion().setFromEuler(
    new Euler(-Math.PI / 2, 0, 0));

/** The angles of the 3D view's camera. */
export interface IIsoAngles {
    id: string;
    /** What the toolbar's menu calls it. */
    caption: string;
    /** How far the camera looks down, in radians. */
    tilt: number;
    /** How far it is turned to the left about the up axis, in radians. */
    turn: number;
}

const degrees = (value: number): number => {
    return value * Math.PI / 180;
};

/**
 * The 3D views to choose from. True isometric tilts by atan(1 / sqrt 2),
 * about 35.26 degrees, which keeps only 58% of the page's depth and looked
 * pinched to the user; the steeper two are axonometric, strictly, and keep
 * 71% and 77%. The last one also turns less, so the text slants less.
 */
export const ISO_PRESETS: readonly IIsoAngles[] = [
    {
        id: "isometric",
        caption: "True Isometric (35° tilt, 45° turn)",
        tilt: Math.atan(1 / Math.SQRT2),
        turn: degrees(45),
    },
    {
        id: "steep",
        caption: "Steep (45° tilt, 45° turn)",
        tilt: degrees(45),
        turn: degrees(45),
    },
    {
        id: "upright",
        caption: "Upright (50° tilt, 30° turn)",
        tilt: degrees(50),
        turn: degrees(30),
    },
];

/** The 3D view used until the user picks another. */
export const DEFAULT_ISO_PRESET = "upright";

/**
 * @param id A preset's id.
 *
 * @returns The preset, or the default one for an unknown id.
 */
export const isoPreset = (id: string | undefined): IIsoAngles => {
    return ISO_PRESETS.find((preset) => { return preset.id === id; })
        ?? ISO_PRESETS.find((preset) => {
            return preset.id === DEFAULT_ISO_PRESET;
        })!;
};

/**
 * The camera in the 3D view: turned to the left, so it looks in from the
 * page's bottom LEFT corner and the page's left edge runs up and to the
 * right, and tilted down.
 *
 * @param angles The view's angles.
 *
 * @returns The orientation.
 */
export const isoQuaternion = (
    angles: Pick<IIsoAngles, "tilt" | "turn">,
): Quaternion => {
    return new Quaternion().setFromEuler(
        new Euler(-angles.tilt, -angles.turn, 0, "YXZ"));
};

/**
 * The camera orientation part way between the two views.
 *
 * @param t 0 for the 2D view, 1 for the 3D one.
 * @param iso The 3D view's orientation.
 *
 * @returns The orientation.
 */
export const viewQuaternion = (
    t: number,
    iso: Quaternion = isoQuaternion(isoPreset(DEFAULT_ISO_PRESET)),
): Quaternion => {
    return new Quaternion().slerpQuaternions(TOP_VIEW, iso,
        Math.min(1, Math.max(0, t)));
};

/** The id of the 3D view the user orbits to with Cmd + drag. */
export const CUSTOM_ISO_PRESET = "custom";

/** How far the camera can tilt while orbiting: from straight down to low. */
export const MIN_TILT = degrees(10);
export const MAX_TILT = degrees(90);

/** How many radians a pixel of orbiting drag turns or tilts the view. */
export const ORBIT_SPEED = degrees(0.4);

/**
 * The user's own 3D view, named by its angles for the toolbar's list.
 *
 * @param tilt How far the camera looks down, in radians.
 * @param turn How far it is turned, in radians.
 *
 * @returns The preset.
 */
export const customPreset = (tilt: number, turn: number): IIsoAngles => {
    const show = (radians: number): number => {
        return Math.round(radians * 180 / Math.PI);
    };

    return {
        id: CUSTOM_ISO_PRESET,
        caption: `Custom (${show(tilt)}° tilt, ${show(turn)}° turn)`,
        tilt,
        turn,
    };
};

/**
 * The angles an orbiting drag leads to: across turns the view, down tilts
 * it towards looking straight down, up towards the side. The tilt stays
 * between `MIN_TILT` and `MAX_TILT`; the turn goes round, kept within
 * one turn either way.
 *
 * @param from The angles the drag started at.
 * @param dx How far the pointer moved across, in CSS pixels.
 * @param dy How far it moved down.
 *
 * @returns The user's own preset at the new angles.
 */
export const orbitAngles = (
    from: Pick<IIsoAngles, "tilt" | "turn">,
    dx: number,
    dy: number,
): IIsoAngles => {
    const tilt = Math.min(MAX_TILT, Math.max(MIN_TILT,
        from.tilt + dy * ORBIT_SPEED));
    let turn = from.turn + dx * ORBIT_SPEED;
    turn = Math.atan2(Math.sin(turn), Math.cos(turn));

    return customPreset(tilt, turn);
};

/**
 * How far the figures rise off the page at a tilt: not at all looking
 * straight down, fully from 60 degrees down - so an orbit that starts
 * in the 2D view lifts them as the camera tips over, and every preset
 * lifts them fully.
 *
 * @param tilt How far the camera looks down, in radians.
 *
 * @returns The share of the full lift, 0..1.
 */
export const liftFactor = (tilt: number): number => {
    return Math.min(1, Math.max(0, (MAX_TILT - tilt) / degrees(30)));
};

/**
 * An ease in and out, for the view transition.
 *
 * @param t Progress, 0..1.
 *
 * @returns The eased progress.
 */
export const easeInOut = (t: number): number => {
    return t < 0.5 ? 2 * t * t : 1 - ((-2 * t + 2) ** 2) / 2;
};

/**
 * The CSS transform that puts a box of a given size onto a parallelogram
 * on the screen, about the box's top left corner. Under an orthographic
 * camera every rectangle of the diagram lands on the screen as such a
 * parallelogram, so this places an element exactly over one.
 *
 * @param topLeft Where the box's top left corner goes.
 * @param topRight Where its top right corner goes.
 * @param bottomLeft Where its bottom left corner goes.
 * @param width The box's width, before the transform.
 * @param height Its height.
 *
 * @returns The `matrix()` transform.
 */
export const affineTransform = (
    topLeft: IPoint,
    topRight: IPoint,
    bottomLeft: IPoint,
    width: number,
    height: number,
): string => {
    const a = (topRight.x - topLeft.x) / width;
    const b = (topRight.y - topLeft.y) / width;
    const c = (bottomLeft.x - topLeft.x) / height;
    const d = (bottomLeft.y - topLeft.y) / height;

    return `matrix(${a}, ${b}, ${c}, ${d}, ${topLeft.x}, ${topLeft.y})`;
};

/** How the grid adapts to the zoom, the way Illustrator's does. */
export const GRID = {
    /** The main lines' spacing at the base level, in millimetres. */
    baseMm: 10,
    /** How many cells the sublines divide a main cell into. */
    subdivisions: 5,
    /** The closest sublines are drawn on the screen, in CSS pixels. */
    minSubPx: 6,
    /** Over how many pixels above that they fade in. */
    fadePx: 8,
} as const;

/** The grid at one zoom. */
export interface IGridSpacing {
    /** The main lines' spacing, in millimetres. */
    mainMm: number;
    /** The sublines' spacing, in millimetres. */
    subMm: number;
    /** How strongly the sublines show, 0..1: they fade out near the limit. */
    subOpacity: number;
}

/**
 * The grid's spacing at a zoom. Zooming out, sublines that would come
 * closer than `minSubPx` are dropped and the main lines become the
 * sublines of a grid `subdivisions` times wider; zooming in, as soon as a
 * finer subgrid would show, the sublines become main lines with sublines
 * of their own. Every level's lines lie on the base grid's, so the switch
 * keeps the lines that stay where they were.
 *
 * @param pxPerMm CSS pixels per millimetre of paper at the zoom.
 * @param grid The grid's settings.
 *
 * @returns The spacing.
 */
export const gridSpacing = (
    pxPerMm: number,
    grid: typeof GRID = GRID,
): IGridSpacing => {
    let mainMm: number = grid.baseMm;
    // Bounded, in case of a zoom of nothing or of everything.
    for (let step = 0; step < 20
        && (mainMm / grid.subdivisions) * pxPerMm < grid.minSubPx; step += 1) {
        mainMm *= grid.subdivisions;
    }
    for (let step = 0; step < 20
        && (mainMm / grid.subdivisions ** 2) * pxPerMm >= grid.minSubPx;
        step += 1) {
        mainMm /= grid.subdivisions;
    }
    const subMm = mainMm / grid.subdivisions;

    return {
        mainMm,
        subMm,
        subOpacity: Math.min(1, Math.max(0,
            (subMm * pxPerMm - grid.minSubPx) / grid.fadePx)),
    };
};

/**
 * The zoom at which a rectangle fills a viewport, with a border around it.
 *
 * @param viewport The viewport's size in CSS pixels.
 * @param bounds The rectangle in diagram units.
 * @param pixelsPerUnitAtOne CSS pixels per unit at zoom 1.
 * @param padding The border in CSS pixels.
 *
 * @returns The zoom level.
 */
export const fitZoom = (
    viewport: { width: number; height: number },
    bounds: IRect,
    pixelsPerUnitAtOne: number,
    padding = 24,
): number => {
    const width = Math.max(1, viewport.width - 2 * padding);
    const height = Math.max(1, viewport.height - 2 * padding);

    return Math.min(width / (bounds.width * pixelsPerUnitAtOne),
        height / (bounds.height * pixelsPerUnitAtOne));
};
