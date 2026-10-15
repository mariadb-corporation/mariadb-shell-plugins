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

import {
    assembleSchemaModel,
    DIAGRAM_FORMAT,
    SCHEMA_DEFINITION_FORMAT,
    type IDiagramRow,
} from "../../modeler/modelFormat.js";

const definition = {
    format: SCHEMA_DEFINITION_FORMAT,
    schema: "test",
    tables: [{
        name: "t",
        columns: [{
            name: "kind", type: "ENUM('A', 'B')", notNull: true,
            primaryKey: false, foreignKey: false,
        }],
        foreignKeys: [],
        indices: [],
    }],
};

const diagram = (id: string) => {
    return {
        format: DIAGRAM_FORMAT,
        figureNotation: "workbench/default",
        connectionNotation: "crowsfoot",
        id, name: id, width: 10, height: 10,
        layers: [], figures: [], connections: [],
    };
};

/** The view's rows, documents as text as the server sends them. */
const rows = (): IDiagramRow[] => {
    return [
        { name: "schema_definition", format: SCHEMA_DEFINITION_FORMAT,
            document: JSON.stringify(definition) },
        { name: "MRS", format: DIAGRAM_FORMAT,
            document: JSON.stringify(diagram("d1")) },
        { name: "MRS Status", format: DIAGRAM_FORMAT,
            document: JSON.stringify(diagram("d2")) },
    ];
};

describe("assembleSchemaModel", () => {
    it("puts the schema definition and the diagrams together", () => {
        const model = assembleSchemaModel(rows());

        expect(model.schema).toBe("test");
        expect(model.tables[0].columns[0].type).toBe("ENUM('A', 'B')");
        expect(model.diagrams.map((entry) => { return entry.name; }))
            .toEqual(["MRS", "MRS Status"]);
        expect(model.figureNotation).toBe("workbench/default");
        expect(model.connectionNotation).toBe("crowsfoot");
    });

    it("names a diagram as its row does: what the tree lists", () => {
        const model = assembleSchemaModel(rows().map((row) => {
            return row.name === "MRS"
                ? { ...row, document: JSON.stringify(diagram("other")) }
                : row;
        }));

        expect(model.diagrams[0].name).toBe("MRS");
    });

    it("takes a document already parsed by the driver", () => {
        const model = assembleSchemaModel(rows().map((row) => {
            return { ...row, document: JSON.parse(row.document as string) };
        }));

        expect(model.diagrams).toHaveLength(2);
    });

    it("skips rows of another format", () => {
        const model = assembleSchemaModel([...rows(),
            { name: "notes", format: "text/1", document: "not json" }]);

        expect(model.diagrams).toHaveLength(2);
    });

    it("wants a schema definition and a diagram", () => {
        expect(() => { return assembleSchemaModel(rows().slice(1)); })
            .toThrow(/no schema definition/);
        expect(() => { return assembleSchemaModel(rows().slice(0, 1)); })
            .toThrow(/no diagram/);
    });

    it("rejects a diagram without its parts", () => {
        expect(() => {
            return assembleSchemaModel([rows()[0], {
                name: "MRS", format: DIAGRAM_FORMAT,
                document: JSON.stringify({ format: DIAGRAM_FORMAT }),
            }]);
        }).toThrow(/The diagram MRS has no layers/);
    });
});
