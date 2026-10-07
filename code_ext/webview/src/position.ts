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

/**
 * Where a floating element - a tooltip, a popup, a menu - goes so that it
 * stays on screen. Each caller keeps its own gaps and margins: how far a
 * tooltip sits from its element is not how far a menu keeps from the
 * window's edge.
 */

/** A width and a height, as of a popup or the window. */
export interface ISize {
    width: number;
    height: number;
}

/** A top left corner, in viewport coordinates. */
export interface IPosition {
    top: number;
    left: number;
}

/** How far a popup sits from its anchor and keeps from the window's edges. */
export interface ISpacing {
    /** The gap between the anchor and the popup. */
    gap: number;
    /** How far the popup stays clear of the window's edges. */
    margin: number;
}

/**
 * Moves a corner in from any edge it would run past, so that the element
 * never opens half off screen.
 *
 * @param at Where the element would go.
 * @param size The element's size.
 * @param viewport The window's inner size.
 * @param margin How far it stays clear of the edges.
 *
 * @returns The corner, moved in where it had to be.
 */
export const clampToViewport = (
    at: IPosition,
    size: ISize,
    viewport: ISize,
    margin: number,
): IPosition => {
    return {
        left: Math.max(margin, Math.min(at.left,
            viewport.width - size.width - margin)),
        top: Math.max(margin, Math.min(at.top,
            viewport.height - size.height - margin)),
    };
};

/**
 * Where an element hung from an anchor goes: below it by preference, above
 * it where that would run off the bottom, and moved in from either side.
 *
 * @param anchor What the anchor measures.
 * @param size What the element measures.
 * @param viewport The window's inner size.
 * @param spacing The gap from the anchor, and the margin from the edges.
 *
 * @returns The top left corner for it.
 */
export const placeBelowOrAbove = (
    anchor: { top: number; bottom: number; left: number },
    size: ISize,
    viewport: ISize,
    spacing: ISpacing,
): IPosition => {
    const below = anchor.bottom + spacing.gap;
    const fitsBelow = below + size.height
        <= viewport.height - spacing.margin;

    return {
        top: fitsBelow
            ? below
            : Math.max(spacing.margin,
                anchor.top - spacing.gap - size.height),
        left: Math.max(spacing.margin, Math.min(anchor.left,
            viewport.width - spacing.margin - size.width)),
    };
};
