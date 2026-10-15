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

import type { IModelLayer } from "../../../src/modeler/modelFormat.js";
import type { IRect } from "../../../src/modeler/pageSettings.js";

/**
 * How tables and layers relate, the way Workbench has it: a table lies ON
 * a layer when it lies fully inside it, and the model files it under the
 * smallest layer it lies on, with its position relative to that layer.
 * Pure, so it is tested without a scene.
 */

/** The smallest a layer can be sized to. */
export const MIN_LAYER_WIDTH = 80;
export const MIN_LAYER_HEIGHT = 40;

/** The room around the tables when a layer is fitted to them. */
export const LAYER_PADDING = 20;

/** The room above them, for the layer's name. */
export const LAYER_LABEL_ROOM = 26;

/** How far a table may stick out and still count as on a layer. */
const TOLERANCE = 0.5;

/**
 * @param layer A layer.
 *
 * @returns Its rectangle on the diagram.
 */
export const layerRect = (layer: IModelLayer): IRect => {
    return {
        left: layer.left,
        top: layer.top,
        width: layer.width,
        height: layer.height,
    };
};

/**
 * @param outer A rectangle.
 * @param inner Another.
 *
 * @returns Whether the second lies fully inside the first.
 */
export const containsRect = (outer: IRect, inner: IRect): boolean => {
    return inner.left >= outer.left - TOLERANCE
        && inner.top >= outer.top - TOLERANCE
        && inner.left + inner.width <= outer.left + outer.width + TOLERANCE
        && inner.top + inner.height <= outer.top + outer.height + TOLERANCE;
};

/**
 * @param rect A rectangle.
 * @param point A point.
 * @param point.x Its x.
 * @param point.y Its y.
 *
 * @returns Whether the point lies in the rectangle.
 */
export const containsPoint = (
    rect: IRect,
    point: { x: number; y: number },
): boolean => {
    return point.x >= rect.left && point.x <= rect.left + rect.width
        && point.y >= rect.top && point.y <= rect.top + rect.height;
};

/**
 * Picks the smallest of the layers that pass a test: where layers
 * overlap, the one in front of the others is the inner one.
 *
 * @param layers The layers.
 * @param test Which layers count.
 *
 * @returns The layer, or nothing where none counts.
 */
const smallest = (
    layers: readonly IModelLayer[],
    test: (rect: IRect) => boolean,
): IModelLayer | undefined => {
    let found: IModelLayer | undefined;
    for (const layer of layers) {
        if (test(layerRect(layer)) && (found === undefined
            || layer.width * layer.height < found.width * found.height)) {
            found = layer;
        }
    }

    return found;
};

/**
 * The layer a table lies on: the smallest one it lies fully inside.
 *
 * @param rect The table's rectangle on the diagram.
 * @param layers The diagram's layers.
 *
 * @returns The layer, or nothing where the table lies on none.
 */
export const layerUnder = (
    rect: IRect,
    layers: readonly IModelLayer[],
): IModelLayer | undefined => {
    return smallest(layers, (outer) => { return containsRect(outer, rect); });
};

/**
 * The layer under a point: the smallest one that holds it.
 *
 * @param point The point on the diagram.
 * @param point.x Its x.
 * @param point.y Its y.
 * @param layers The diagram's layers.
 *
 * @returns The layer, or nothing.
 */
export const layerAtPoint = (
    point: { x: number; y: number },
    layers: readonly IModelLayer[],
): IModelLayer | undefined => {
    return smallest(layers, (rect) => { return containsPoint(rect, point); });
};

/**
 * The rectangle a layer takes when fitted around tables: their bounds,
 * with room around them and above them for the layer's name, and never
 * less than a layer's least size.
 *
 * @param rects The tables' rectangles.
 *
 * @returns The layer's rectangle, or nothing for no tables.
 */
export const fitAround = (rects: readonly IRect[]): IRect | undefined => {
    if (rects.length === 0) {
        return undefined;
    }
    const left = Math.min(...rects.map((rect) => { return rect.left; }));
    const top = Math.min(...rects.map((rect) => { return rect.top; }));
    const right = Math.max(...rects.map((rect) => {
        return rect.left + rect.width;
    }));
    const bottom = Math.max(...rects.map((rect) => {
        return rect.top + rect.height;
    }));

    return {
        left: left - LAYER_PADDING,
        top: top - LAYER_LABEL_ROOM,
        width: Math.max(MIN_LAYER_WIDTH, right - left + 2 * LAYER_PADDING),
        height: Math.max(MIN_LAYER_HEIGHT,
            bottom - top + LAYER_LABEL_ROOM + LAYER_PADDING),
    };
};
