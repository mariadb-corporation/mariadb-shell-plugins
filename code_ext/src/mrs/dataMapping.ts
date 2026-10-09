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
    IMrsColumns,
    IMrsDataMapping,
    IMrsDataMappingField,
    IMrsDbColumn,
    IMrsReferenceMapping,
    IMrsTableColumn,
    JsonObject,
} from "./mrsTypes.js";
import {
    objectKeyword,
    objectOptions,
    qualifiedName,
    quoteIdentifier,
    quoteRequestPath,
    quoteServicePath,
    quoteText,
    type IObjectSettings,
} from "./restSql.js";

/**
 * The data mapping editor's model: what the REST Object dialog's Data
 * Mapping tab shows and edits, and the REST SQL it becomes.
 *
 * A table or view's mapping starts from its columns (`SHOW REST COLUMNS`)
 * merged with what is stored (`SHOW CREATE REST VIEW ... FORMAT=JSON`), so
 * a column added since shows up and one dropped since is marked. A
 * reference to another table is a field of its own whose children are
 * loaded when it is first opened. Free of `vscode` and of the DOM: the
 * webview edits it, the host saves what it becomes.
 */

/** The CRUD flags of a data mapping or of a reference. */
export interface ICrudFlags {
    insert: boolean;
    update: boolean;
    delete: boolean;
    /** Leaves it out of the ETAG check. */
    noCheck: boolean;
}

/** A reference's own part of a field. */
export interface IReferencePart {
    mapping: IMrsReferenceMapping;
    crud: ICrudFlags;
    /** Merges the referenced fields into this object. */
    unnest: boolean;
    /**
     * For a 1:n reference: the one child field it is reduced to, by key.
     * The reference is then an array of that field's values.
     */
    reduceTo?: string;
    /** Whether the referenced table's columns have been loaded. */
    loaded: boolean;
    children: IMappingField[];
}

/** One row of the editor: a column, a parameter or a reference. */
export interface IMappingField {
    /** Unique within the document: the stored id, or a made-up one. */
    key: string;
    /** The JSON key. */
    name: string;
    /** The column or parameter; undefined for a reference. */
    column?: IMrsDbColumn;
    reference?: IReferencePart;
    enabled: boolean;
    allowFiltering: boolean;
    allowSorting: boolean;
    noCheck: boolean;
    noUpdate: boolean;
    /** Part of the object's key; defaults to the primary key. */
    isKey: boolean;
    /** The field whose value says which user a row belongs to. */
    rowOwnership: boolean;
    /** The JSON schema stored for it, kept as it is. */
    jsonSchema?: JsonObject | null;
    /** Stored, but its column no longer exists. */
    missing?: boolean;
}

/** A routine's parameters, or one of its result sets. */
export interface IRoutineMapping {
    key: string;
    name: string;
    fields: IMappingField[];
}

/** Everything the Data Mapping tab edits. */
export interface IMappingDocument {
    objectType: string;
    /** The database object, `schema.name`. */
    dbSchema: string;
    dbObject: string;
    /** A table or view's class name. */
    className: string;
    /** A table or view's own CRUD flags. */
    crud: ICrudFlags;
    /** A table or view's fields. */
    fields: IMappingField[];
    /** A routine's parameters. */
    parameters?: IRoutineMapping;
    /** A procedure's result sets; a function's one result. */
    results?: IRoutineMapping[];
}

let nextKey = 0;

/** @returns A key for a field or mapping that has none stored. */
export const newKey = (): string => {
    nextKey += 1;

    return `new-${nextKey}`;
};

/**
 * @param text `snake_case`, `kebab-case` or `space separated`.
 *
 * @returns It in camelCase.
 */
export const camelCase = (text: string): string => {
    const words = text.split(/[^A-Za-z0-9]+/).filter((word) => {
        return word !== "";
    });

    return words.map((word, index) => {
        return index === 0
            ? word.charAt(0).toLowerCase() + word.slice(1)
            : word.charAt(0).toUpperCase() + word.slice(1);
    }).join("");
};

/**
 * @param text Any name.
 *
 * @returns It in PascalCase.
 */
export const pascalCase = (text: string): string => {
    const camel = camelCase(text);

    return camel.charAt(0).toUpperCase() + camel.slice(1);
};

/**
 * @param text A camelCase or PascalCase name.
 *
 * @returns It in snake_case, for a new field's column.
 */
export const snakeCase = (text: string): string => {
    return text.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
};

/**
 * @param servicePath The service's path.
 * @param schemaPath The REST schema's path.
 * @param objectName The database object.
 *
 * @returns The class name a new REST object gets: `SvcSakilaCity`.
 */
export const defaultClassName = (
    servicePath: string,
    schemaPath: string,
    objectName: string,
): string => {
    const path = servicePath.slice(servicePath.lastIndexOf("@") + 1);

    return [path, schemaPath, objectName].map(pascalCase).join("");
};

const sameMapping = (
    a: IMrsReferenceMapping,
    b: IMrsReferenceMapping,
): boolean => {
    return a.kind === b.kind
        && a.referenced_schema === b.referenced_schema
        && a.referenced_table === b.referenced_table
        && JSON.stringify(a.column_mapping) === JSON.stringify(b.column_mapping);
};

/** Gives every name a suffix where it is taken already. */
const uniqueName = (name: string, taken: Set<string>): string => {
    let unique = name;
    for (let i = 2; taken.has(unique); i += 1) {
        unique = `${name}${i}`;
    }
    taken.add(unique);

    return unique;
};

const crudOf = (options: JsonObject | null | undefined): ICrudFlags => {
    return {
        insert: options?.dataMappingViewInsert === true,
        update: options?.dataMappingViewUpdate === true,
        delete: options?.dataMappingViewDelete === true,
        noCheck: options?.dataMappingViewNoCheck === true,
    };
};

/** The stored fields of one level: the top, or below one reference. */
const storedLevel = (
    stored: IMrsDataMappingField[],
    parentReferenceId: string | null,
): IMrsDataMappingField[] => {
    return stored.filter((field) => {
        return field.parent_reference_id === parentReferenceId;
    });
};

/**
 * Builds the fields of one table or view level from its columns and what
 * is stored for it.
 *
 * @param columns The table's columns and references.
 * @param stored Every stored field of the data mapping.
 * @param parentReferenceId The stored reference this level hangs below;
 *        null for the top.
 * @param rowOwnershipId The stored row ownership field of this level.
 * @param tables The tables this level is nested in, its own last, so a
 *        reference back up the chain - the n:m back reference - is left
 *        out rather than opening forever.
 *
 * @returns The fields.
 */
export const fieldsFromColumns = (
    columns: IMrsTableColumn[],
    stored: IMrsDataMappingField[],
    parentReferenceId: string | null,
    rowOwnershipId: string | null,
    tables: string[],
): IMappingField[] => {
    const level = storedLevel(stored, parentReferenceId);
    const taken = new Set<string>();
    const fields: IMappingField[] = [];

    for (const column of columns) {
        if (column.reference_mapping !== null) {
            const mapping = column.reference_mapping;
            const target = `${mapping.referenced_schema}.${mapping.referenced_table}`;
            if (tables.includes(target)) {
                continue;
            }

            const match = level.find((field) => {
                const reference = field.data_mapping_reference;

                return reference !== null
                    && sameMapping(reference.reference_mapping, mapping);
            });
            const reference = match?.data_mapping_reference ?? undefined;
            fields.push({
                key: match?.id ?? newKey(),
                name: uniqueName(match?.name ?? camelCase(column.name), taken),
                reference: {
                    mapping,
                    crud: crudOf(reference?.options),
                    unnest: reference?.unnest ?? false,
                    loaded: false,
                    children: [],
                    ...(reference?.reduce_to_value_of_field_id
                        ? { reduceTo: reference.reduce_to_value_of_field_id }
                        : {}),
                },
                // A reference is not followed unless asked for.
                enabled: match?.enabled ?? false,
                allowFiltering: true,
                allowSorting: false,
                noCheck: false,
                noUpdate: false,
                isKey: false,
                rowOwnership: false,
            });
            continue;
        }

        const db = column.db_column!;
        const match = level.find((field) => {
            return field.data_mapping_reference === null
                && field.db_column?.name === db.name;
        });
        fields.push({
            key: match?.id ?? newKey(),
            name: uniqueName(match?.name ?? camelCase(column.name), taken),
            column: db,
            enabled: match?.enabled ?? true,
            allowFiltering: match?.allow_filtering ?? true,
            allowSorting: match?.allow_sorting
                ?? (db.is_primary === true || db.is_unique === true),
            noCheck: match?.no_check ?? false,
            noUpdate: match?.no_update ?? false,
            isKey: match?.db_column?.is_primary ?? db.is_primary === true,
            rowOwnership: match !== undefined && match.id === rowOwnershipId,
            ...(match?.json_schema ? { jsonSchema: match.json_schema } : {}),
        });
    }

    // What is stored for a column that is gone stays visible, so saving
    // is a decision rather than a silent loss.
    for (const field of level) {
        if (field.data_mapping_reference !== null || field.db_column === null) {
            continue;
        }
        const name = field.db_column.name;
        if (!columns.some((column) => { return column.name === name; })) {
            fields.push({
                key: field.id,
                name: uniqueName(field.name, taken),
                column: field.db_column,
                enabled: false,
                allowFiltering: field.allow_filtering,
                allowSorting: field.allow_sorting,
                noCheck: field.no_check,
                noUpdate: field.no_update,
                isKey: false,
                rowOwnership: false,
                missing: true,
            });
        }
    }

    return fields;
};

/**
 * Fills a reference's children from the referenced table's columns.
 *
 * @param field The reference field.
 * @param columns The referenced table's columns.
 * @param stored Every stored field of the data mapping.
 * @param tables The tables the reference is nested in, the one holding it
 *        last.
 * @param rowOwnershipId The reference's stored row ownership field.
 *
 * @returns The field, loaded.
 */
export const loadReference = (
    field: IMappingField,
    columns: IMrsTableColumn[],
    stored: IMrsDataMappingField[],
    tables: string[],
    rowOwnershipId: string | null = null,
): IMappingField => {
    const reference = field.reference!;
    const target = `${reference.mapping.referenced_schema}.`
        + reference.mapping.referenced_table;
    const storedField = stored.find((candidate) => {
        return candidate.id === field.key;
    });
    const storedReferenceId = storedField?.represents_reference_id ?? null;
    const children = fieldsFromColumns(columns,
        storedReferenceId === null ? [] : stored,
        storedReferenceId, rowOwnershipId, [...tables, target]);
    // A reduced reference keeps only its one field.
    if (reference.reduceTo !== undefined) {
        for (const child of children) {
            child.enabled = child.key === reference.reduceTo;
        }
    }

    return {
        ...field,
        reference: { ...reference, loaded: true, children },
    };
};

/**
 * @param objectType The REST object's type.
 *
 * @returns Whether it is a procedure or function.
 */
export const isRoutine = (objectType: string): boolean => {
    return objectType === "PROCEDURE" || objectType === "FUNCTION";
};

/** A parameter's or a result column's field. */
const plainField = (
    name: string,
    column: IMrsDbColumn,
    stored?: IMrsDataMappingField,
): IMappingField => {
    return {
        key: stored?.id ?? newKey(),
        name: stored?.name ?? name,
        column: { ...column, ...(stored?.db_column?.datatype
            ? { datatype: stored.db_column.datatype } : {}) },
        enabled: stored?.enabled ?? true,
        allowFiltering: stored?.allow_filtering ?? true,
        allowSorting: stored?.allow_sorting ?? false,
        noCheck: stored?.no_check ?? false,
        noUpdate: stored?.no_update ?? false,
        isKey: false,
        rowOwnership: false,
        ...(stored?.json_schema ? { jsonSchema: stored.json_schema } : {}),
    };
};

/**
 * Builds a routine's parameters from its declaration and what is stored.
 *
 * @param columns `SHOW REST COLUMNS` of the routine.
 * @param stored The stored PARAMETERS mapping, if any.
 *
 * @returns The parameters.
 */
export const parametersFrom = (
    columns: IMrsColumns,
    stored: IMrsDataMapping | undefined,
): IMappingField[] => {
    return (columns.parameters ?? []).map((parameter) => {
        const match = stored?.fields.find((field) => {
            return field.db_column?.name === parameter.name;
        });

        return plainField(camelCase(parameter.name), {
            name: parameter.name,
            datatype: parameter.datatype,
            in: parameter.mode.includes("IN"),
            out: parameter.mode.includes("OUT"),
            ...(parameter.charset ? { charset: parameter.charset } : {}),
            ...(parameter.collation ? { collation: parameter.collation } : {}),
        }, match);
    });
};

/**
 * Builds the editor's document of a REST object.
 *
 * @param objectType TABLE, VIEW, PROCEDURE or FUNCTION.
 * @param dbSchema The database schema.
 * @param dbObject The database object.
 * @param columns `SHOW REST COLUMNS` of it.
 * @param mappings Its stored data mappings; empty for a new object.
 * @param className The class name a new object gets.
 *
 * @returns The document. A table or view's stored references are not
 *          loaded yet: see {@link referencesToLoad}.
 */
export const buildDocument = (
    objectType: string,
    dbSchema: string,
    dbObject: string,
    columns: IMrsColumns,
    mappings: IMrsDataMapping[],
    className: string,
): IMappingDocument => {
    const base = {
        objectType,
        dbSchema,
        dbObject,
        crud: { insert: false, update: false, delete: false, noCheck: false },
        fields: [],
    };

    if (!isRoutine(objectType)) {
        const result = mappings.find((mapping) => {
            return mapping.kind === "RESULT";
        });

        return {
            ...base,
            className: result?.name ?? className,
            crud: crudOf(result?.options),
            fields: fieldsFromColumns(columns.columns ?? [],
                result?.fields ?? [], null,
                result?.row_ownership_field_id ?? null,
                [`${dbSchema}.${dbObject}`]),
        };
    }

    const parameters = mappings.find((mapping) => {
        return mapping.kind === "PARAMETERS";
    });
    const storedResults = mappings.filter((mapping) => {
        return mapping.kind === "RESULT";
    }).sort((a, b) => { return a.position - b.position; });
    let results: IRoutineMapping[] = storedResults.map((mapping) => {
        return {
            key: mapping.id,
            name: mapping.name,
            fields: mapping.fields.filter((field) => {
                return field.parent_reference_id === null;
            }).map((field) => {
                return plainField(field.name,
                    field.db_column ?? { name: snakeCase(field.name) }, field);
            }),
        };
    });
    // A function returns one value, which needs a result to be named.
    if (objectType === "FUNCTION" && results.length === 0) {
        results = [{
            key: newKey(),
            name: `${className}Result`,
            fields: [plainField("result", {
                name: "result",
                datatype: columns.return_type ?? "text",
            })],
        }];
    }

    return {
        ...base,
        className,
        parameters: {
            key: parameters?.id ?? newKey(),
            name: parameters?.name ?? `${className}Params`,
            fields: parametersFrom(columns, parameters),
        },
        results,
    };
};

/**
 * @param fields A level of fields.
 *
 * @returns The enabled references below it, at any depth, that have not
 *          been loaded: what has to be loaded for the tree to show what is
 *          stored.
 */
export const referencesToLoad = (fields: IMappingField[]): IMappingField[] => {
    return fields.flatMap((field) => {
        if (field.reference === undefined || !field.enabled) {
            return [];
        }

        return field.reference.loaded
            ? referencesToLoad(field.reference.children)
            : [field];
    });
};

/**
 * Replaces one field anywhere in a tree of fields.
 *
 * @param fields The fields.
 * @param key The field to replace.
 * @param change Builds its replacement.
 *
 * @returns The new tree; untouched branches are shared.
 */
export const updateField = (
    fields: IMappingField[],
    key: string,
    change: (field: IMappingField) => IMappingField,
): IMappingField[] => {
    let changed = false;
    const result = fields.map((field) => {
        if (field.key === key) {
            changed = true;

            return change(field);
        }
        if (field.reference === undefined) {
            return field;
        }
        const children = updateField(field.reference.children, key, change);
        if (children === field.reference.children) {
            return field;
        }
        changed = true;

        return { ...field, reference: { ...field.reference, children } };
    });

    return changed ? result : fields;
};

/**
 * @param fields The fields.
 * @param key A field.
 *
 * @returns It, wherever it is in the tree.
 */
export const findField = (
    fields: IMappingField[],
    key: string,
): IMappingField | undefined => {
    for (const field of fields) {
        if (field.key === key) {
            return field;
        }
        const found = field.reference === undefined
            ? undefined
            : findField(field.reference.children, key);
        if (found !== undefined) {
            return found;
        }
    }

    return undefined;
};

/**
 * @param fields The fields.
 * @param key A field.
 *
 * @returns The fields of the level it is on.
 */
export const siblingsOf = (
    fields: IMappingField[],
    key: string,
): IMappingField[] | undefined => {
    if (fields.some((field) => { return field.key === key; })) {
        return fields;
    }
    for (const field of fields) {
        const found = field.reference === undefined
            ? undefined
            : siblingsOf(field.reference.children, key);
        if (found !== undefined) {
            return found;
        }
    }

    return undefined;
};

/**
 * Makes a field the row ownership field of its level, or clears it.
 *
 * @param fields The fields.
 * @param key The field.
 *
 * @returns The new tree: at most one field per level owns rows.
 */
export const toggleRowOwnership = (
    fields: IMappingField[],
    key: string,
): IMappingField[] => {
    const level = siblingsOf(fields, key) ?? [];
    let result = fields;
    for (const field of level) {
        result = updateField(result, field.key, (current) => {
            return {
                ...current,
                rowOwnership: current.key === key ? !current.rowOwnership : false,
            };
        });
    }

    return result;
};

/**
 * Reduces a 1:n reference to one of its fields, or undoes that.
 *
 * @param fields The fields.
 * @param key The reference.
 * @param childKey The field to reduce to; undefined to undo it.
 *
 * @returns The new tree.
 */
export const reduceReference = (
    fields: IMappingField[],
    key: string,
    childKey: string | undefined,
): IMappingField[] => {
    return updateField(fields, key, (field) => {
        const reference = field.reference!;
        const children = reference.children.map((child) => {
            return {
                ...child,
                enabled: childKey === undefined ? true
                    : child.key === childKey,
            };
        });
        const { reduceTo: _, ...rest } = reference;

        return {
            ...field,
            enabled: true,
            reference: childKey === undefined
                ? { ...rest, unnest: false, children }
                : { ...rest, reduceTo: childKey, unnest: true, children },
        };
    });
};

/**
 * Turns every field of a level on or off.
 *
 * @param fields The fields.
 * @param parentKey The reference whose children to set; undefined for the
 *        top level.
 * @param enabled Whether they are to be in the mapping.
 *
 * @returns The new tree. References are left alone: turning one on loads
 *          a table, which is the user's call each time.
 */
export const setAllEnabled = (
    fields: IMappingField[],
    parentKey: string | undefined,
    enabled: boolean,
): IMappingField[] => {
    const set = (level: IMappingField[]): IMappingField[] => {
        return level.map((field) => {
            return field.reference === undefined ? { ...field, enabled } : field;
        });
    };

    if (parentKey === undefined) {
        return set(fields);
    }

    return updateField(fields, parentKey, (field) => {
        return {
            ...field,
            reference: {
                ...field.reference!,
                children: set(field.reference!.children),
            },
        };
    });
};

/**
 * @param document The editor's document.
 *
 * @returns The CRUD operations the object allows: READ always (a
 *          procedure or function: its call), plus what the mapping's flags
 *          and its references' add.
 */
export const crudOperations = (document: IMappingDocument): string[] => {
    if (isRoutine(document.objectType)) {
        return ["CREATE"];
    }

    const operations = new Set(["READ"]);
    const add = (crud: ICrudFlags): void => {
        if (crud.insert) {
            operations.add("CREATE");
        }
        if (crud.update) {
            operations.add("UPDATE");
        }
        if (crud.delete) {
            operations.add("DELETE");
        }
    };
    add(document.crud);
    const walk = (fields: IMappingField[]): void => {
        for (const field of fields) {
            if (field.reference !== undefined && field.enabled) {
                if (field.reference.crud.insert || field.reference.crud.update
                    || field.reference.crud.delete) {
                    operations.add("UPDATE");
                }
                walk(field.reference.children);
            }
        }
    };
    walk(document.fields);

    return ["CREATE", "READ", "UPDATE", "DELETE"].filter((operation) => {
        return operations.has(operation);
    });
};

// --- REST SQL ---------------------------------------------------------

/** `@INSERT @UPDATE @DELETE @NOCHECK`, with the negatives spelled out. */
const crudText = (crud: ICrudFlags, explicit: boolean): string => {
    const words = explicit
        ? [
            crud.insert ? "@INSERT" : "@NOINSERT",
            crud.update ? "@UPDATE" : "@NOUPDATE",
            crud.delete ? "@DELETE" : "@NODELETE",
        ]
        : [
            ...(crud.insert ? ["@INSERT"] : []),
            ...(crud.update ? ["@UPDATE"] : []),
            ...(crud.delete ? ["@DELETE"] : []),
        ];
    if (crud.noCheck) {
        words.push("@NOCHECK");
    }

    return words.join(" ");
};

/**
 * @param fields One level of fields.
 * @param indent How far its lines are indented.
 * @param withDatatype Whether to name each column's type, as a routine's
 *        result needs: nothing else says what it returns.
 *
 * @returns The `{ ... }` block of the level, or `{}` where nothing of it
 *          is in the mapping.
 */
export const mappingText = (
    fields: IMappingField[],
    indent: number,
    withDatatype = false,
): string => {
    const pad = " ".repeat(indent);
    const lines: string[] = [];

    for (const field of fields) {
        if (!field.enabled || field.missing === true) {
            continue;
        }

        const key = `${pad}    ${quoteIdentifier(field.name)}: `;
        if (field.reference !== undefined) {
            const reference = field.reference;
            const target = qualifiedName(reference.mapping.referenced_schema,
                reference.mapping.referenced_table);
            // The value options before the CRUD ones: after @NOINSERT and
            // the others, @NOCHECK can only be the reference's own.
            const words = [
                ...(reference.unnest ? ["@UNNEST"] : []),
                crudText(reference.crud, true),
            ];
            lines.push(`${key}${target} ${words.join(" ")} `
                + mappingText(reference.children, indent + 4, withDatatype));
            continue;
        }

        const column = field.column!;
        const words: string[] = [];
        if (column.in === true || column.out === true) {
            words.push(column.in === true && column.out === true ? "@INOUT"
                : column.in === true ? "@IN" : "@OUT");
        }
        if (field.noCheck) {
            words.push("@NOCHECK");
        }
        if (field.allowSorting) {
            words.push("@SORTABLE");
        }
        if (!field.allowFiltering) {
            words.push("@NOFILTERING");
        }
        if (field.rowOwnership) {
            words.push("@ROWOWNERSHIP");
        }
        if (field.isKey) {
            words.push("@KEY");
        }
        if (withDatatype && column.datatype !== undefined
            && column.datatype !== "") {
            words.push(`@DATATYPE(${quoteText(column.datatype)})`);
        }
        if (field.noUpdate) {
            words.push("@NOUPDATE");
        }
        if (field.jsonSchema) {
            words.push(`JSON SCHEMA ${JSON.stringify(field.jsonSchema)}`);
        }
        lines.push(`${key}${quoteIdentifier(column.name)}`
            + (words.length === 0 ? "" : ` ${words.join(" ")}`));
    }

    return lines.length === 0 ? "{}" : `{\n${lines.join(",\n")}\n${pad}}`;
};

/** Where a REST object is, and what it is called. */
export interface IObjectPlace {
    servicePath: string;
    schemaPath: string;
    requestPath: string;
}

/**
 * Builds the statement that creates or changes a REST object.
 *
 * @param document The data mapping.
 * @param place Where the object is to be.
 * @param settings Its settings.
 * @param existingPath Its request path as it is now, to ALTER it; left out
 *        to CREATE it.
 *
 * @returns The REST SQL.
 */
export const objectSql = (
    document: IMappingDocument,
    place: IObjectPlace,
    settings: IObjectSettings,
    existingPath?: string,
): string => {
    const keyword = objectKeyword(document.objectType);
    const where = `ON SERVICE ${quoteServicePath(place.servicePath)} `
        + `SCHEMA ${quoteRequestPath(place.schemaPath)}`;
    const routine = isRoutine(document.objectType);
    const lines: string[] = existingPath === undefined
        ? [
            `CREATE REST ${keyword} ${quoteRequestPath(place.requestPath)}`,
            `    ${where}`,
            `    AS ${qualifiedName(document.dbSchema, document.dbObject)}`
            + (routine ? "" : ` CLASS ${quoteIdentifier(document.className)}`
                + crudSuffix(document.crud)
                + ` ${mappingText(document.fields, 4)}`),
        ]
        : [
            `ALTER REST ${keyword} ${quoteRequestPath(existingPath)}`,
            `    ${where}`,
            ...(existingPath === place.requestPath
                ? []
                : [`    NEW REQUEST PATH ${quoteRequestPath(place.requestPath)}`]),
            ...(routine ? [] : [`    CLASS ${quoteIdentifier(document.className)}`
                + crudSuffix(document.crud)
                + ` ${mappingText(document.fields, 4)}`]),
        ];

    if (routine) {
        if (document.parameters !== undefined) {
            lines.push(`    PARAMETERS ${quoteIdentifier(document.parameters.name)} `
                + mappingText(document.parameters.fields, 4));
        }
        for (const result of document.results ?? []) {
            lines.push(`    RESULT ${quoteIdentifier(result.name)} `
                + mappingText(result.fields, 4, true));
        }
    }

    for (const option of objectOptions(settings, routine)) {
        lines.push(`    ${option.replaceAll("\n", "\n    ")}`);
    }

    return `${lines.join("\n")};`;
};

const crudSuffix = (crud: ICrudFlags): string => {
    const text = crudText(crud, false);

    return text === "" ? "" : ` ${text}`;
};
