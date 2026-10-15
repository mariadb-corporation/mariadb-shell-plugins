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
 * The paper a diagram is drawn on, the Workbench way: a diagram is a grid
 * of pages, and its coordinate space is the pages' PRINTABLE areas laid
 * side by side, so that it can be printed or exported to PDF page by page.
 * The margins lie outside that space.
 *
 * Pure, shared by the viewer (which draws the pages) and whatever exports
 * them later, so free of `vscode` and the DOM.
 */

import type { IModelDiagram } from "./modelFormat.js";

/** A paper format, in millimetres, portrait. */
export interface IPaperFormat {
    name: string;
    width: number;
    height: number;
}

/** The paper formats a diagram can be laid out on. */
const PAPER_FORMATS: readonly IPaperFormat[] = [
    { name: "A3", width: 297, height: 420 },
    { name: "A4", width: 210, height: 297 },
    { name: "A5", width: 148, height: 210 },
    { name: "Letter", width: 216, height: 279 },
    { name: "Legal", width: 216, height: 356 },
    { name: "Tabloid", width: 279, height: 432 },
];

/** How a diagram is laid out on paper. */
export interface IPageSettings {
    /** The paper format's name, one of `PAPER_FORMATS`. */
    paper: string;
    orientation: "portrait" | "landscape";
    /** The margins in millimetres. */
    marginLeft: number;
    marginTop: number;
    marginRight: number;
    marginBottom: number;
    /** Diagram units per millimetre. */
    scale: number;
}

/**
 * What a Workbench model is laid out on unless it says otherwise: A4
 * portrait at 5 units per millimetre, a quarter inch at the sides and
 * the top and a wider margin at the bottom. The `mariadb_rest_service`
 * model's own, read from its `app.PageSettings`.
 */
export const DEFAULT_PAGE_SETTINGS: IPageSettings = {
    paper: "A4",
    orientation: "portrait",
    marginLeft: 6.35,
    marginTop: 6.35,
    marginRight: 6.35,
    marginBottom: 14.46,
    scale: 5,
};

/** A rectangle in diagram units. */
export interface IRect {
    left: number;
    top: number;
    width: number;
    height: number;
}

/** One page of a diagram. */
export interface IPage {
    column: number;
    row: number;
    /** The whole sheet, margins included. */
    paper: IRect;
    /** The part of the sheet the diagram's content lies on. */
    printable: IRect;
}

/**
 * @param settings The page settings.
 *
 * @returns The sheet's size in millimetres, as oriented.
 */
export const paperSize = (
    settings: IPageSettings,
): { width: number; height: number } => {
    const format = PAPER_FORMATS.find((candidate) => {
        return candidate.name === settings.paper;
    }) ?? PAPER_FORMATS[1];

    return settings.orientation === "landscape"
        ? { width: format.height, height: format.width }
        : { width: format.width, height: format.height };
};

/**
 * @param settings The page settings.
 *
 * @returns The size of one page's printable area in diagram units.
 */
export const printableSize = (
    settings: IPageSettings,
): { width: number; height: number } => {
    const paper = paperSize(settings);

    return {
        width: (paper.width - settings.marginLeft - settings.marginRight)
            * settings.scale,
        height: (paper.height - settings.marginTop - settings.marginBottom)
            * settings.scale,
    };
};

/**
 * How many pages a diagram covers. A diagram's size is a whole number of
 * printable areas, up to rounding in the model file.
 *
 * @param settings The page settings.
 * @param diagram The diagram.
 *
 * @returns The page count in both directions, at least one each.
 */
export const pageCount = (
    settings: IPageSettings,
    diagram: Pick<IModelDiagram, "width" | "height">,
): { horizontal: number; vertical: number } => {
    const printable = printableSize(settings);

    return {
        horizontal: Math.max(1, Math.round(diagram.width / printable.width)),
        vertical: Math.max(1, Math.round(diagram.height / printable.height)),
    };
};

/**
 * Lays the pages out: the printable areas tile the diagram from its
 * origin, and each sheet extends by its margins beyond its printable area,
 * so neighbouring sheets overlap by their margins - as Workbench draws
 * them, with the page split line between the printable areas.
 *
 * @param settings The page settings.
 * @param diagram The diagram.
 *
 * @returns The pages, row by row.
 */
export const layoutPages = (
    settings: IPageSettings,
    diagram: Pick<IModelDiagram, "width" | "height">,
): IPage[] => {
    const printable = printableSize(settings);
    const count = pageCount(settings, diagram);
    const pages: IPage[] = [];
    for (let row = 0; row < count.vertical; row += 1) {
        for (let column = 0; column < count.horizontal; column += 1) {
            const area: IRect = {
                left: column * printable.width,
                top: row * printable.height,
                width: printable.width,
                height: printable.height,
            };
            pages.push({
                column,
                row,
                printable: area,
                paper: {
                    left: area.left - settings.marginLeft * settings.scale,
                    top: area.top - settings.marginTop * settings.scale,
                    width: area.width + (settings.marginLeft
                        + settings.marginRight) * settings.scale,
                    height: area.height + (settings.marginTop
                        + settings.marginBottom) * settings.scale,
                },
            });
        }
    }

    return pages;
};

/**
 * @param pages The diagram's pages.
 *
 * @returns The rectangle every sheet lies within.
 */
export const paperBounds = (pages: IPage[]): IRect => {
    const left = Math.min(...pages.map((page) => { return page.paper.left; }));
    const top = Math.min(...pages.map((page) => { return page.paper.top; }));
    const right = Math.max(...pages.map((page) => {
        return page.paper.left + page.paper.width;
    }));
    const bottom = Math.max(...pages.map((page) => {
        return page.paper.top + page.paper.height;
    }));

    return { left, top, width: right - left, height: bottom - top };
};
