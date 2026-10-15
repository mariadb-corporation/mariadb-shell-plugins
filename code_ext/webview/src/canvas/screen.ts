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
 * What 100% means: a page on screen as large as the sheet of paper it
 * stands for.
 *
 * No browser API reports a display's physical pixel density, so the
 * density is a constant: that of the Apple Studio Display (27", 5120 x
 * 2880). How macOS scales it IS known - `devicePixelRatio` is 2 at the
 * default "looks like 2560 x 1440", and less with "More Space" - so the
 * CSS pixels per millimetre follow the chosen scaling.
 */

/** The display's pixels per inch. */
export const DISPLAY_PPI = 218;

/**
 * @param devicePixelRatio Device pixels per CSS pixel; the window's.
 *
 * @returns CSS pixels per millimetre at which a page is life size.
 */
export const cssPixelsPerMm = (
    devicePixelRatio: number = globalThis.devicePixelRatio ?? 1,
): number => {
    return DISPLAY_PPI / Math.max(devicePixelRatio, 0.5) / 25.4;
};

/**
 * The camera scale at a zoom level: how many CSS pixels one diagram unit
 * takes.
 *
 * @param zoom The zoom level, 1 being life size.
 * @param unitsPerMm The diagram's units per millimetre.
 * @param devicePixelRatio Device pixels per CSS pixel; the window's.
 *
 * @returns CSS pixels per diagram unit.
 */
export const pixelsPerUnit = (
    zoom: number,
    unitsPerMm: number,
    devicePixelRatio?: number,
): number => {
    return zoom * cssPixelsPerMm(devicePixelRatio) / unitsPerMm;
};
