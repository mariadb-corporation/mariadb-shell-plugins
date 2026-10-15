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

import type {
    IModelFigure,
    IModelTable,
    ISchemaModel,
} from "../../../src/modeler/modelFormat.js";
import { catalogFromModel } from "../../src/modeler/catalog.js";
import {
    CARD,
    cardItemAt,
    contrastingText,
    editRect,
    layoutTableCard,
    TITLE_RESERVE,
} from "../../src/modeler/tableCard.js";

const table: IModelTable = {
    name: "actor",
    columns: [
        { name: "actor_id", type: "INT", notNull: true, primaryKey: true,
            foreignKey: false },
        { name: "first_name", type: "VARCHAR(45)", notNull: true,
            primaryKey: false, foreignKey: false },
        { name: "note", type: "TEXT", notNull: false, primaryKey: false,
            foreignKey: false },
    ],
    foreignKeys: [{
        name: "fk_actor_film", columns: ["film_id"], referencedTable: "film",
        referencedColumns: ["film_id"], many: true, mandatory: true,
        referencedMandatory: true, deleteRule: "NO ACTION",
        updateRule: "NO ACTION", modelOnly: false,
    }],
    indices: [{ name: "PRIMARY", type: "PRIMARY", columns: ["actor_id"] }],
};

const figure: IModelFigure = {
    id: "f1", type: "TableFigure", name: "actor", left: 10, top: 20,
    width: 180, height: 100, color: "#98BFDA", table: "actor",
    expanded: true, columnsExpanded: true,
};

describe("layoutTableCard", () => {
    it("puts the title in the header and a row per column", () => {
        const layout = layoutTableCard(table, figure);

        expect(layout.width).toBe(180);
        expect(layout.items[0]).toMatchObject({
            kind: "title", text: "actor", editable: true, icon: "table",
            rect: { left: 0, top: 0, width: 180, height: CARD.headerHeight },
        });
        const columns = layout.items.filter((item) => {
            return item.kind === "column";
        });
        expect(columns.map((item) => { return item.text; }))
            .toEqual(["actor_id", "first_name", "note"]);
        expect(columns.map((item) => { return item.icon; }))
            .toEqual(["primaryKey", "notNull", "nullable"]);
        expect(columns.map((item) => { return item.columnIndex; }))
            .toEqual([0, 1, 2]);
        expect(columns.every((item) => { return item.editable; })).toBe(true);
        expect(columns[0].detail).toBe("INT");
        // The height follows the content.
        expect(layout.height).toBe(CARD.headerHeight + CARD.padding
            + CARD.headingHeight + 3 * CARD.rowHeight + CARD.padding);
    });

    it("adds the indices and foreign keys where expanded", () => {
        const layout = layoutTableCard(table, {
            ...figure, indicesExpanded: true, foreignKeysExpanded: true,
        });
        const kinds = layout.items.map((item) => { return item.kind; });

        expect(kinds).toContain("index");
        expect(kinds).toContain("foreignKey");
        expect(layout.items.filter((item) => { return item.kind === "heading"; })
            .map((item) => { return item.text; }))
            .toEqual(["Columns", "Indexes", "Foreign Keys"]);
        expect(layout.items.find((item) => { return item.kind === "index"; })
            ?.editable).toBe(false);
    });

    it("grows with the content unless sized by hand", () => {
        const auto = layoutTableCard(table, { ...figure, height: 40 });
        expect(auto.height).toBe(auto.contentHeight);

        const smaller = layoutTableCard(table, {
            ...figure, manualSizing: true, height: 70,
        });
        expect(smaller.height).toBe(70);
        expect(smaller.contentHeight).toBe(auto.contentHeight);

        const larger = layoutTableCard(table, {
            ...figure, manualSizing: true, height: 300,
        });
        expect(larger.height).toBe(300);

        // Never less than the header.
        expect(layoutTableCard(table, {
            ...figure, manualSizing: true, height: 5,
        }).height).toBe(CARD.headerHeight);
    });

    it("hits no item the card's height cuts off", () => {
        const layout = layoutTableCard(table, {
            ...figure, manualSizing: true,
            height: CARD.headerHeight + CARD.padding + CARD.headingHeight
                + CARD.rowHeight + 4,
        });
        const firstRow = CARD.headerHeight + CARD.padding + CARD.headingHeight;

        expect(cardItemAt(layout, 50, firstRow + 1)?.text).toBe("actor_id");
        // The second row is only partly inside: not editable from there.
        expect(cardItemAt(layout, 50, firstRow + CARD.rowHeight + 1))
            .toBeUndefined();
    });

    it("shows only the header of a collapsed figure", () => {
        const layout = layoutTableCard(table, { ...figure, expanded: false });

        expect(layout.items).toHaveLength(1);
        expect(layout.height).toBe(CARD.headerHeight);
    });

    it("shows a figure whose table is unknown by name, not editable", () => {
        const layout = layoutTableCard(undefined, figure);

        expect(layout.items[0].editable).toBe(false);
        expect(layout.items).toHaveLength(1);
    });

    it("finds the item under a point", () => {
        const layout = layoutTableCard(table, figure);

        expect(cardItemAt(layout, 50, 10)?.kind).toBe("title");
        const firstRow = CARD.headerHeight + CARD.padding + CARD.headingHeight;
        expect(cardItemAt(layout, 50, firstRow + 1)?.text).toBe("actor_id");
        expect(cardItemAt(layout, 50, firstRow + CARD.rowHeight + 1)?.text)
            .toBe("first_name");
        // In the padding, nothing.
        expect(cardItemAt(layout, 2, firstRow + 1)).toBeUndefined();
    });
});

describe("editRect", () => {
    it("leaves out the icons and the title's triangle", () => {
        const layout = layoutTableCard(table, figure);
        const title = editRect(layout.items[0]);

        // Past the table icon: inset 10, icon 15, gap 5; clear of the
        // menu button's place.
        expect(title.left).toBe(30);
        expect(title.left + title.width).toBe(180 - TITLE_RESERVE);
        // Centred in the header.
        expect(title.top + title.height / 2).toBe(CARD.headerHeight / 2);

        const column = layout.items.find((item) => {
            return item.kind === "column";
        })!;
        expect(editRect(column)).toEqual({
            left: column.rect.left + CARD.iconSize + CARD.gap,
            top: column.rect.top,
            width: column.rect.width - CARD.iconSize - CARD.gap,
            height: column.rect.height,
        });
    });
});

describe("contrastingText", () => {
    it("puts black on a light colour and white on a dark one", () => {
        expect(contrastingText("#98BFDA")).toBe("#000000");
        expect(contrastingText("#203040")).toBe("#ffffff");
        expect(contrastingText("nonsense")).toBe("#000000");
    });
});

describe("catalogFromModel", () => {
    it("finds tables by name and follows a rename", () => {
        const model: ISchemaModel = {
            schema: "s",
            tables: [structuredClone(table), {
                name: "film", columns: [], foreignKeys: [], indices: [],
            }],
            diagrams: [],
        };
        const catalog = catalogFromModel(model);

        expect(catalog.table("actor")?.columns).toHaveLength(3);
        catalog.renameTable("film", "movie");
        expect(catalog.table("film")).toBeUndefined();
        expect(catalog.table("movie")?.name).toBe("movie");
        // The foreign key that pointed at it points at the new name.
        expect(catalog.table("actor")?.foreignKeys[0].referencedTable)
            .toBe("movie");
    });
});
