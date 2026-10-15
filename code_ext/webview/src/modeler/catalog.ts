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
    IModelTable,
    ISchemaModel,
} from "../../../src/modeler/modelFormat.js";

/**
 * Where the diagrams' figures get their database objects from.
 *
 * A figure names its table; what the table looks like is the catalog's
 * business. Today the catalog is the `tables` list the model JSON carries
 * beside its diagrams; it is to come from the schema's CREATE statements,
 * through a language server, so the scene never reads `model.tables`
 * itself.
 */
export interface ITableCatalog {
    /**
     * @param name A table's name.
     *
     * @returns The table, or nothing where the catalog has none of that
     *          name.
     */
    table(name: string): IModelTable | undefined;
    /**
     * Renames a table. The diagrams refer to tables by name, so the
     * catalog is told first and the figures follow.
     *
     * @param name The table's name.
     * @param newName Its new name.
     *
     * @returns Nothing.
     */
    renameTable(name: string, newName: string): void;
}

/**
 * The catalog the model JSON carries.
 *
 * @param model The model.
 *
 * @returns A catalog over its `tables`, which it changes in place.
 */
export const catalogFromModel = (model: ISchemaModel): ITableCatalog => {
    const byName = new Map(model.tables.map((table) => {
        return [table.name, table];
    }));

    return {
        table: (name) => { return byName.get(name); },
        renameTable: (name, newName) => {
            const table = byName.get(name);
            if (table === undefined || newName === name) {
                return;
            }
            byName.delete(name);
            table.name = newName;
            byName.set(newName, table);
            // The other tables' foreign keys point at it by name too.
            for (const other of model.tables) {
                for (const foreignKey of other.foreignKeys) {
                    if (foreignKey.referencedTable === name) {
                        foreignKey.referencedTable = newName;
                    }
                }
            }
        },
    };
};
