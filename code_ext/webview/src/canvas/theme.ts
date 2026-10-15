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

import { Color, SRGBColorSpace } from "three";

/**
 * The colours and fonts the scene draws with, taken from the VS Code
 * theme's CSS variables so the canvas matches the editor around it.
 */
export interface IDiagramTheme {
    /** Whether the theme is a dark one. */
    dark: boolean;
    /** The area outside the pages. */
    background: string;
    /** The paper. */
    paper: string;
    /** Grid, page and margin lines. */
    grid: string;
    foreground: string;
    /** Column types, and other secondary text. */
    dimmed: string;
    /** A figure's body. */
    figureBackground: string;
    figureBorder: string;
    /** The selection outline. */
    selection: string;
    /** Connection lines. */
    connection: string;
    shadow: string;
    fontFamily: string;
    /** The UI font size in CSS pixels. */
    fontSize: number;
}

/** How far a dark theme's paper is lifted towards white. */
const DARK_PAPER_LIFT = 0.08;

/**
 * The paper's colour: the editor's background, lifted a little towards
 * white on a dark theme, whose background can be all but black - which
 * made the sheets look like holes rather than paper.
 *
 * @param background The editor's background, as CSS has it.
 * @param dark Whether the theme is a dark one.
 *
 * @returns The colour, as `#rrggbb`.
 */
export const paperColor = (background: string, dark: boolean): string => {
    if (!dark) {
        return background;
    }
    const color = new Color().setStyle(background, SRGBColorSpace)
        .getRGB({ r: 0, g: 0, b: 0 }, SRGBColorSpace);
    const lift = (value: number): number => {
        return value + (1 - value) * DARK_PAPER_LIFT;
    };

    return `#${new Color().setRGB(lift(color.r), lift(color.g), lift(color.b),
        SRGBColorSpace).getHexString(SRGBColorSpace)}`;
};

/**
 * Reads the theme off the document.
 *
 * @returns The theme.
 */
export const readTheme = (): IDiagramTheme => {
    const style = getComputedStyle(document.body);
    const read = (name: string, fallback: string): string => {
        const value = style.getPropertyValue(name).trim();

        return value === "" ? fallback : value;
    };
    const dark = document.body.classList.contains("vscode-dark")
        || document.body.classList.contains("vscode-high-contrast");

    return {
        dark,
        background: read("--vscode-editorWidget-background",
            dark ? "#252526" : "#f3f3f3"),
        paper: paperColor(
            read("--vscode-editor-background", dark ? "#1e1e1e" : "#ffffff"),
            dark),
        grid: read("--vscode-editorIndentGuide-background",
            dark ? "#404040" : "#d3d3d3"),
        foreground: read("--vscode-editor-foreground",
            dark ? "#d4d4d4" : "#333333"),
        dimmed: read("--vscode-descriptionForeground",
            dark ? "#9d9d9d" : "#717171"),
        figureBackground: read("--vscode-editorWidget-background",
            dark ? "#252526" : "#f8f8f8"),
        figureBorder: read("--vscode-editorWidget-border",
            dark ? "#474747" : "#c8c8c8"),
        selection: read("--vscode-focusBorder", "#007fd4"),
        connection: read("--vscode-editor-foreground",
            dark ? "#d4d4d4" : "#333333"),
        shadow: read("--vscode-widget-shadow", "#000000"),
        fontFamily: read("--vscode-font-family",
            "Helvetica Neue, Arial, sans-serif"),
        fontSize: Number.parseFloat(read("--vscode-font-size", "13px")) || 13,
    };
};
