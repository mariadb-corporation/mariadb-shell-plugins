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

import type {
    IModelFigure,
    IModelTable,
} from "../../../src/modeler/modelFormat.js";
import type { IRect } from "../../../src/modeler/pageSettings.js";
import type { IDiagramTheme } from "../canvas/theme.js";

/**
 * A table figure's card: the Workbench default notation as the MySQL
 * Shell's Pixi designer drew it - a header strip in the figure's colour
 * with the table icon and name, then the columns with their key /
 * not-null / nullable icons and types, and the indices and foreign keys
 * where the figure has them expanded.
 *
 * The layout is pure and in diagram units: every item records its
 * rectangle, so a double-click can be mapped to the name under it. The
 * drawing rasterises the layout into a canvas at a given scale, which the
 * scene puts on a plane as a texture.
 */

/** The card's measurements in diagram units. */
export const CARD = {
    headerHeight: 30,
    padding: 8,
    rowHeight: 18,
    headingHeight: 16,
    sectionGap: 6,
    iconSize: 10,
    gap: 5,
    radius: 8,
    titleFontSize: 14,
    headingFontSize: 12,
    rowFontSize: 11,
} as const;

/** What an item of a card shows. */
export type CardItemKind = "title" | "heading" | "column" | "index"
    | "foreignKey";

/** The icon an item carries. */
export type CardIcon = "table" | "primaryKey" | "notNull" | "nullable"
    | "index" | "foreignKey";

/** One line of a card. */
export interface ICardItem {
    kind: CardItemKind;
    /** Where the item lies, relative to the card's top left corner. */
    rect: IRect;
    /** The name shown, which is what a double-click edits. */
    text: string;
    /** What follows the name: a column's type. */
    detail?: string;
    icon?: CardIcon;
    /** Whether the name can be edited in place. */
    editable: boolean;
    /** For a column: its index in the table's columns. */
    columnIndex?: number;
}

/** A card's layout. */
export interface ICardLayout {
    width: number;
    /** The card's height: its content's, or the user's (`manualSizing`). */
    height: number;
    /** What the content needs, whatever the card's height. */
    contentHeight: number;
    items: ICardItem[];
}

/** The narrowest a figure can be sized to. */
export const MIN_FIGURE_WIDTH = 60;

/**
 * Room kept free at the right of the header: the menu button of a
 * selected figure goes there.
 */
export const TITLE_RESERVE = 26;

/**
 * Lays a table's card out at the figure's width.
 *
 * The height grows with the content - unless the user sized the figure
 * (`manualSizing`, as Workbench calls it), in which case it stays the
 * figure's own and what does not fit is cut off. A collapsed figure is
 * its header.
 *
 * @param table The table.
 * @param figure The figure showing it, for its width, its height where
 *               sized by hand, and what is expanded.
 *
 * @returns The layout.
 */
export const layoutTableCard = (
    table: IModelTable | undefined,
    figure: IModelFigure,
): ICardLayout => {
    const content = layoutContent(table, figure);
    const sized = figure.manualSizing === true && figure.expanded !== false;

    return {
        ...content,
        height: sized
            ? Math.max(CARD.headerHeight, figure.height)
            : content.contentHeight,
    };
};

/**
 * Lays the content of a card out.
 *
 * @param table The table.
 * @param figure The figure showing it.
 *
 * @returns The layout at the content's height.
 */
const layoutContent = (
    table: IModelTable | undefined,
    figure: IModelFigure,
): Omit<ICardLayout, "height"> => {
    const width = figure.width;
    const items: ICardItem[] = [{
        kind: "title",
        rect: { left: 0, top: 0, width, height: CARD.headerHeight },
        text: figure.table ?? figure.name,
        icon: "table",
        editable: table !== undefined,
    }];

    if (table === undefined || figure.expanded === false) {
        return { width, contentHeight: CARD.headerHeight, items };
    }

    let top = CARD.headerHeight + CARD.padding;
    const inner = width - 2 * CARD.padding;
    const section = (
        heading: string,
        lines: Array<Omit<ICardItem, "rect">>,
    ): void => {
        if (lines.length === 0) {
            return;
        }
        if (top > CARD.headerHeight + CARD.padding) {
            top += CARD.sectionGap;
        }
        items.push({
            kind: "heading",
            rect: { left: CARD.padding, top, width: inner,
                height: CARD.headingHeight },
            text: heading,
            editable: false,
        });
        top += CARD.headingHeight;
        for (const line of lines) {
            items.push({
                ...line,
                rect: { left: CARD.padding, top, width: inner,
                    height: CARD.rowHeight },
            });
            top += CARD.rowHeight;
        }
    };

    if (figure.columnsExpanded !== false) {
        section("Columns", table.columns.map((column, columnIndex) => {
            return {
                kind: "column" as const,
                text: column.name,
                detail: column.type,
                icon: column.primaryKey ? "primaryKey" as const
                    : column.notNull ? "notNull" as const
                        : "nullable" as const,
                editable: true,
                columnIndex,
            };
        }));
    }
    if (figure.indicesExpanded === true) {
        section("Indexes", table.indices.map((index) => {
            return {
                kind: "index" as const,
                text: index.name,
                icon: "index" as const,
                editable: false,
            };
        }));
    }
    if (figure.foreignKeysExpanded === true) {
        section("Foreign Keys", table.foreignKeys.map((foreignKey) => {
            return {
                kind: "foreignKey" as const,
                text: foreignKey.name,
                icon: "foreignKey" as const,
                editable: false,
            };
        }));
    }

    return { width, contentHeight: top + CARD.padding, items };
};

/**
 * The item of a card under a point.
 *
 * @param layout The card's layout.
 * @param x The point, relative to the card's top left corner.
 * @param y The point.
 *
 * @returns The item, or nothing between items.
 */
export const cardItemAt = (
    layout: ICardLayout,
    x: number,
    y: number,
): ICardItem | undefined => {
    // An item the card's height cuts off is not there to be hit.
    return layout.items.find((item) => {
        return item.rect.top + item.rect.height <= layout.height
            && x >= item.rect.left && x < item.rect.left + item.rect.width
            && y >= item.rect.top && y < item.rect.top + item.rect.height;
    });
};

/** The primary key icon's square, in diagram units. */
const PRIMARY_KEY_ICON = 12;

/** The title's icon size and its left inset, in diagram units. */
const TITLE_ICON = 15;
const TITLE_INSET = 10;

/**
 * Where an item's name is edited in place: the text alone, without the
 * icon in front of it or the title's collapse triangle after it.
 *
 * @param item The item.
 *
 * @returns The rectangle, relative to the card's top left corner.
 */
export const editRect = (item: ICardItem): IRect => {
    const { rect } = item;
    if (item.kind === "title") {
        const left = TITLE_INSET + TITLE_ICON + CARD.gap;
        const height = CARD.titleFontSize + 8;

        return {
            left,
            top: rect.top + (rect.height - height) / 2,
            // The menu button's place stays clear.
            width: Math.max(20, rect.width - left - TITLE_RESERVE),
            height,
        };
    }
    const iconWidth = item.icon === undefined ? 0 : CARD.iconSize + CARD.gap;

    return {
        left: rect.left + iconWidth,
        top: rect.top,
        width: Math.max(20, rect.width - iconWidth),
        height: rect.height,
    };
};

/**
 * Whether black or white reads better on a colour.
 *
 * @param color A `#rrggbb` colour.
 *
 * @returns The text colour.
 */
export const contrastingText = (color: string): string => {
    const hex = /^#([0-9a-f]{6})$/i.exec(color.trim())?.[1];
    if (hex === undefined) {
        return "#000000";
    }
    const value = Number.parseInt(hex, 16);
    const luminance = (0.299 * ((value >> 16) & 0xff)
        + 0.587 * ((value >> 8) & 0xff) + 0.114 * (value & 0xff)) / 255;

    return luminance > 0.55 ? "#000000" : "#ffffff";
};

/** The icons a card draws, loaded as images. */
export type CardIcons = Record<CardIcon, HTMLImageElement>;

/**
 * Turns an SVG's text into an image, in a colour: the icons are black
 * shapes, and take the colour of the text beside them.
 *
 * @param svg The SVG document.
 * @param color The fill to give every shape.
 *
 * @returns The image, once it has loaded.
 */
export const svgImage = (
    svg: string,
    color: string,
): Promise<HTMLImageElement> => {
    // The index icon's paths have no fill of their own.
    const coloured = svg.replace(/<path\b[^>]*>/g, (tag) => {
        return tag.includes("fill=")
            ? tag.replace(/fill="#000000"/, `fill="${color}"`)
            : tag.replace("<path", `<path fill="${color}"`);
    });
    const image = new Image();
    const loaded = new Promise<HTMLImageElement>((resolve, reject) => {
        image.onload = () => { resolve(image); };
        image.onerror = () => { reject(new Error("Could not load an icon.")); };
    });
    image.src = `data:image/svg+xml;charset=utf-8,`
        + encodeURIComponent(coloured);

    return loaded;
};

/**
 * Draws a card into a canvas.
 *
 * @param layout The card's layout.
 * @param figure The figure, for its colour.
 * @param theme The theme.
 * @param icons The icons, in the theme's text colour.
 * @param headerIcons The icons, in the header's text colour.
 * @param scale Device pixels per diagram unit.
 *
 * @returns The canvas.
 */
export const drawTableCard = (
    layout: ICardLayout,
    figure: IModelFigure,
    theme: IDiagramTheme,
    icons: CardIcons,
    headerIcons: CardIcons,
    scale: number,
): HTMLCanvasElement => {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil(layout.width * scale));
    canvas.height = Math.max(1, Math.ceil(layout.height * scale));
    const context = canvas.getContext("2d");
    if (context === null) {
        return canvas;
    }
    context.scale(scale, scale);

    const { width, height } = layout;
    const headerColor = figure.color || "#98BFDA";
    const headerText = contrastingText(headerColor);

    const outline = new Path2D();
    outline.roundRect(0.5, 0.5, width - 1, height - 1, CARD.radius);
    context.save();
    context.clip(outline);
    context.fillStyle = theme.figureBackground;
    context.fillRect(0, 0, width, height);
    context.fillStyle = headerColor;
    context.fillRect(0, 0, width, CARD.headerHeight);

    context.textBaseline = "middle";
    for (const item of layout.items) {
        const { rect } = item;
        const middle = rect.top + rect.height / 2;
        switch (item.kind) {
            case "title": {
                context.drawImage(headerIcons.table, TITLE_INSET,
                    middle - TITLE_ICON / 2, TITLE_ICON, TITLE_ICON);
                context.font = `bold ${CARD.titleFontSize}px ${theme.fontFamily}`;
                context.fillStyle = headerText;
                drawClipped(context, item.text, TITLE_INSET + TITLE_ICON
                    + CARD.gap, middle, width - TITLE_INSET - TITLE_ICON
                    - CARD.gap - TITLE_RESERVE);
                break;
            }

            case "heading": {
                context.font = `bold ${CARD.headingFontSize}px `
                    + theme.fontFamily;
                context.fillStyle = theme.foreground;
                drawClipped(context, item.text, rect.left, middle, rect.width);
                break;
            }

            default: {
                let x = rect.left;
                if (item.icon !== undefined) {
                    // The icons are square drawings, drawn square. The key
                    // is a tall shape in the middle of its square, so it
                    // gets a larger one to stand as tall as a row.
                    const side = item.icon === "primaryKey"
                        ? PRIMARY_KEY_ICON
                        : CARD.iconSize;
                    context.drawImage(icons[item.icon],
                        x + (CARD.iconSize - side) / 2, middle - side / 2,
                        side, side);
                    x += CARD.iconSize + CARD.gap;
                }
                context.font = `${CARD.rowFontSize}px ${theme.fontFamily}`;
                context.fillStyle = theme.foreground;
                const available = rect.left + rect.width - x;
                const nameWidth = Math.min(context.measureText(item.text).width,
                    available);
                drawClipped(context, item.text, x, middle, available);
                if (item.detail !== undefined && nameWidth + CARD.gap < available) {
                    context.font = `italic ${CARD.rowFontSize}px `
                        + theme.fontFamily;
                    context.fillStyle = theme.dimmed;
                    drawClipped(context, item.detail, x + nameWidth + CARD.gap,
                        middle, available - nameWidth - CARD.gap);
                }
            }
        }
    }
    context.restore();

    context.strokeStyle = theme.figureBorder;
    context.lineWidth = 1;
    context.stroke(outline);

    return canvas;
};

/**
 * Draws text cut off with an ellipsis where it does not fit.
 *
 * @param context The canvas.
 * @param text The text.
 * @param x Where it starts.
 * @param y Its middle line.
 * @param width How much room it has.
 */
const drawClipped = (
    context: CanvasRenderingContext2D,
    text: string,
    x: number,
    y: number,
    width: number,
): void => {
    if (width <= 0) {
        return;
    }
    let shown = text;
    if (context.measureText(shown).width > width) {
        while (shown.length > 0
            && context.measureText(`${shown}…`).width > width) {
            shown = shown.slice(0, -1);
        }
        shown = `${shown}…`;
    }
    context.fillText(shown, x, y);
};

/**
 * Draws a line of text into a canvas of its own, for a label.
 *
 * @param text The text.
 * @param font The CSS font.
 * @param color The text colour.
 * @param scale Device pixels per diagram unit.
 * @param lineHeight The canvas height in diagram units.
 *
 * @returns The canvas, and the text's width in diagram units.
 */
export const drawLabel = (
    text: string,
    font: string,
    color: string,
    scale: number,
    lineHeight: number,
): { canvas: HTMLCanvasElement; width: number } => {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (context === null) {
        return { canvas, width: 0 };
    }
    context.font = font;
    const width = Math.ceil(context.measureText(text).width) + 2;
    canvas.width = Math.max(1, Math.ceil(width * scale));
    canvas.height = Math.max(1, Math.ceil(lineHeight * scale));
    context.scale(scale, scale);
    context.font = font;
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.fillText(text, 1, lineHeight / 2);

    return { canvas, width };
};
