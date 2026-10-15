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
 * A schema's diagrams: a Workbench model exported as JSON, one document per
 * row of the schema's `msm_schema_diagrams` view - the schema definition
 * (`mwb-schema/1`: the tables with their columns, foreign keys and indices)
 * and one document per EER diagram (`mwb-diagram/1`: layers, figures and
 * connections).
 *
 * Shared by the extension (which reads it) and the model viewer webview
 * (which draws it), so this file is free of `vscode`.
 */

/** A column of a table. */
export interface IModelColumn {
    name: string;
    type: string;
    notNull: boolean;
    primaryKey: boolean;
    foreignKey: boolean;
    default?: string;
    comment?: string;
    flags?: string[];
}

/** A foreign key of a table. */
export interface IModelForeignKey {
    name: string;
    columns: string[];
    referencedTable: string;
    referencedColumns: string[];
    many: boolean;
    mandatory: boolean;
    referencedMandatory: boolean;
    deleteRule: string;
    updateRule: string;
    modelOnly: boolean;
}

/** An index of a table. */
export interface IModelIndex {
    name: string;
    type: string;
    columns: string[];
}

/** A table of the schema. */
export interface IModelTable {
    name: string;
    comment?: string;
    columns: IModelColumn[];
    foreignKeys: IModelForeignKey[];
    indices: IModelIndex[];
}

/** A layer of a diagram: a coloured area grouping figures. */
export interface IModelLayer {
    id: string;
    name: string;
    left: number;
    top: number;
    width: number;
    height: number;
    color: string;
    description?: string;
    /** The ids of the figures on the layer. */
    figures: string[];
    /**
     * How far the layer stands above the page in the 3D view, with the
     * tables on it. The viewer's own; Workbench has no such thing.
     */
    elevation?: number;
}

/**
 * A figure of a diagram. Only tables so far; `left` and `top` are relative
 * to the layer where the figure has one, else to the diagram.
 */
export interface IModelFigure {
    id: string;
    type: string;
    name: string;
    left: number;
    top: number;
    width: number;
    height: number;
    color: string;
    /** The id of the layer the figure lies on, if any. */
    layer?: string;
    table?: string;
    expanded?: boolean;
    columnsExpanded?: boolean;
    indicesExpanded?: boolean;
    foreignKeysExpanded?: boolean;
    triggersExpanded?: boolean;
    manualSizing?: boolean;
}

/** A connection of a diagram: a foreign key drawn between two figures. */
export interface IModelConnection {
    id: string;
    foreignKey: string;
    /** The table owning the foreign key (the "many" side). */
    table: string;
    startFigure: string;
    endFigure: string;
    visible: boolean;
    caption?: string;
    middleSegmentOffset?: number;
    drawSplit?: boolean;
}

/** An EER diagram. */
export interface IModelDiagram {
    id: string;
    name: string;
    description?: string;
    width: number;
    height: number;
    zoom?: number;
    layers: IModelLayer[];
    figures: IModelFigure[];
    connections: IModelConnection[];
}

/**
 * A schema model as the viewer shows it: the schema definition's tables
 * and the schema's diagrams, put together from the rows of its
 * `msm_schema_diagrams` view (see `assembleSchemaModel`).
 */
export interface ISchemaModel {
    schema: string;
    figureNotation?: string;
    connectionNotation?: string;
    tables: IModelTable[];
    diagrams: IModelDiagram[];
}

/** The format of the schema definition's document. */
export const SCHEMA_DEFINITION_FORMAT = "mwb-schema/1";

/** The format of a diagram's document. */
export const DIAGRAM_FORMAT = "mwb-diagram/1";

/** The schema definition: the tables the diagrams draw. */
export interface ISchemaDefinition {
    format: typeof SCHEMA_DEFINITION_FORMAT;
    schema: string;
    tables: IModelTable[];
}

/** A diagram's document: one EER diagram, in a notation. */
export interface IDiagramDocument extends IModelDiagram {
    format: typeof DIAGRAM_FORMAT;
    figureNotation?: string;
    connectionNotation?: string;
}

/** A row of a schema's `msm_schema_diagrams` view. */
export interface IDiagramRow {
    name: string;
    format: string;
    /** The JSON document: as text, or already parsed by the driver. */
    document: unknown;
}

/** The view a schema's diagrams are kept in. */
export const DIAGRAMS_VIEW = "msm_schema_diagrams";

/**
 * Parses a row's document.
 *
 * @param row The row.
 *
 * @returns The document as an object.
 * @throws If it is not a JSON object.
 */
const documentOf = (row: IDiagramRow): Record<string, unknown> => {
    const value: unknown = typeof row.document === "string"
        ? JSON.parse(row.document)
        : row.document;
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error(`The document of ${row.name} is not a JSON object.`);
    }

    return value as Record<string, unknown>;
};

/**
 * Puts a schema model together out of the rows of its `msm_schema_diagrams`
 * view: the one schema definition (`mwb-schema/1`), and every diagram
 * (`mwb-diagram/1`) in the order the view has them. Rows of another format
 * are skipped, so the view can grow.
 *
 * @param rows The view's rows.
 *
 * @returns The model.
 * @throws If there is no schema definition, no diagram, or a document of
 *         the formats read is not what they say.
 */
export const assembleSchemaModel = (rows: readonly IDiagramRow[]): ISchemaModel => {
    let definition: ISchemaDefinition | undefined;
    const diagrams: IDiagramDocument[] = [];
    for (const row of rows) {
        if (row.format === SCHEMA_DEFINITION_FORMAT) {
            const document = documentOf(row);
            if (!Array.isArray(document.tables)) {
                throw new Error("The schema definition has no tables.");
            }
            definition = document as unknown as ISchemaDefinition;
        } else if (row.format === DIAGRAM_FORMAT) {
            const document = documentOf(row);
            for (const key of ["layers", "figures", "connections"]) {
                if (!Array.isArray(document[key])) {
                    throw new Error(`The diagram ${row.name} has no ${key}.`);
                }
            }
            diagrams.push({
                ...(document as unknown as IDiagramDocument),
                // The row's name is what the tree lists and opens.
                name: row.name,
            });
        }
    }
    if (definition === undefined) {
        throw new Error(`The ${DIAGRAMS_VIEW} view has no schema definition `
            + `(${SCHEMA_DEFINITION_FORMAT}).`);
    }
    if (diagrams.length === 0) {
        throw new Error(`The ${DIAGRAMS_VIEW} view has no diagram `
            + `(${DIAGRAM_FORMAT}).`);
    }

    return {
        schema: definition.schema,
        figureNotation: diagrams[0].figureNotation,
        connectionNotation: diagrams[0].connectionNotation,
        tables: definition.tables,
        diagrams,
    };
};
