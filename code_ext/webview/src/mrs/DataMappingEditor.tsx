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

import { useState } from "preact/hooks";

import {
    isRoutine,
    loadReference,
    newKey,
    reduceReference,
    setAllEnabled,
    snakeCase,
    toggleRowOwnership,
    updateField,
    type ICrudFlags,
    type IMappingDocument,
    type IMappingField,
    type IRoutineMapping,
} from "../../../src/mrs/dataMapping.js";
import type { IMrsColumnsMessage, MrsWebviewMessage }
    from "../../../src/mrs/mrsDialogProtocol.js";
import {
    objectStatements,
    type IObjectContext,
    type IObjectDialogValues,
} from "../../../src/mrs/mrsDialogs.js";
import type { IMrsColumns } from "../../../src/mrs/mrsTypes.js";
import { post } from "../vscodeApi.js";
import type { IFieldContext } from "./fields.js";

/**
 * The Data Mapping tab of the REST Object dialog: the MySQL Shell's object
 * field editor, as a table of two columns - the JSON side, with the field
 * names and their flags, and the relational side, with the columns and
 * the tables references lead to.
 *
 * A reference's table is loaded when it is first opened, from the host,
 * which runs `SHOW REST COLUMNS` for it.
 */

let nextRequest = 0;
const pending = new Map<number, (message: IMrsColumnsMessage) => void>();

/**
 * Hands a `columns` answer to the request waiting for it.
 *
 * @param message The host's answer.
 *
 * @returns Nothing.
 */
export const resolveColumns = (message: IMrsColumnsMessage): void => {
    pending.get(message.requestId)?.(message);
    pending.delete(message.requestId);
};

/**
 * Asks the host for a table's columns.
 *
 * @param schema The database schema.
 * @param table The table.
 *
 * @returns The columns.
 */
export const requestColumns = async (
    schema: string,
    table: string,
): Promise<IMrsColumns> => {
    nextRequest += 1;
    const requestId = nextRequest;

    return await new Promise<IMrsColumns>((resolve, reject) => {
        pending.set(requestId, (message) => {
            if (message.columns === undefined) {
                reject(new Error(message.error ?? "No columns."));
            } else {
                resolve(message.columns);
            }
        });
        post<MrsWebviewMessage>({ type: "loadColumns", requestId, schema, table });
    });
};

/** A toggle drawn as a small labelled pill. */
const Pill = (props: {
    label: string;
    on: boolean;
    tooltip: string;
    disabled?: boolean;
    onToggle: () => void;
}): preact.JSX.Element => {
    return (
        <button
            type="button"
            class={props.on ? "mrs-pill on" : "mrs-pill"}
            aria-pressed={props.on}
            data-tooltip={props.tooltip}
            disabled={props.disabled === true}
            onClick={props.onToggle}
        >
            {props.label}
        </button>
    );
};

/** A flag of a column field, drawn as a codicon that is on or off. */
const Flag = (props: {
    icon: string;
    on: boolean;
    tooltip: string;
    onToggle: () => void;
}): preact.JSX.Element => {
    return (
        <button
            type="button"
            class={`icon-button mrs-flag codicon codicon-${props.icon}${
                props.on ? " on" : ""}`}
            aria-pressed={props.on}
            aria-label={props.tooltip}
            data-tooltip={props.tooltip}
            onClick={props.onToggle}
        />
    );
};

const CrudPills = (props: {
    crud: ICrudFlags;
    kind?: string;
    short?: boolean;
    onChange: (crud: ICrudFlags) => void;
}): preact.JSX.Element => {
    const label = (word: string): string => {
        return props.short === true ? word.slice(0, 3) : word;
    };
    // An n:1 reference can only be updated through.
    const updateOnly = props.kind === "n:1";

    return (
        <span class="mrs-pills">
            {updateOnly ? null : (
                <Pill label={label("INSERT")} on={props.crud.insert}
                    tooltip="Allow INSERT operations on this object"
                    onToggle={() => {
                        props.onChange({ ...props.crud, insert: !props.crud.insert });
                    }} />
            )}
            <Pill label={label("UPDATE")} on={props.crud.update}
                tooltip="Allow UPDATE operations on this object"
                onToggle={() => {
                    props.onChange({ ...props.crud, update: !props.crud.update });
                }} />
            {updateOnly ? null : (
                <Pill label={label("DELETE")} on={props.crud.delete}
                    tooltip="Allow DELETE operations on this object"
                    onToggle={() => {
                        props.onChange({ ...props.crud, delete: !props.crud.delete });
                    }} />
            )}
            <Pill label="NOCHECK" on={props.crud.noCheck}
                tooltip="Disable ETAG calculations for this table."
                onToggle={() => {
                    props.onChange({ ...props.crud, noCheck: !props.crud.noCheck });
                }} />
        </span>
    );
};

export const DataMappingEditor = (props: {
    ctx: IFieldContext;
    values: IObjectDialogValues;
    context: IObjectContext;
}): preact.JSX.Element => {
    const document = props.values.document;
    const routine = isRoutine(document.objectType);
    const [preview, setPreview] = useState(false);
    const [mapping, setMapping] = useState<string>("parameters");
    const [expanded, setExpanded] = useState<Set<string>>(() => {
        // What is stored opens up, so it can be seen.
        const open = new Set<string>();
        const walk = (fields: IMappingField[]): void => {
            for (const field of fields) {
                if (field.reference?.loaded === true && field.enabled) {
                    open.add(field.key);
                    walk(field.reference.children);
                }
            }
        };
        walk(document.fields);

        return open;
    });
    const [loadError, setLoadError] = useState<string | undefined>(undefined);

    const setDocument = (next: IMappingDocument): void => {
        props.ctx.set("document", next);
    };

    /** The fields being edited: the view's, or the picked mapping's. */
    const current: IRoutineMapping | undefined = !routine ? undefined
        : mapping === "parameters" ? document.parameters
            : document.results?.find((result) => {
                return result.key === mapping;
            }) ?? document.parameters;
    const fields = current?.fields ?? document.fields;
    const isResult = routine && current !== document.parameters;

    const setFields = (next: IMappingField[]): void => {
        if (!routine) {
            setDocument({ ...document, fields: next });
        } else if (current === document.parameters) {
            setDocument({
                ...document,
                parameters: { ...document.parameters!, fields: next },
            });
        } else {
            setDocument({
                ...document,
                results: (document.results ?? []).map((result) => {
                    return result.key === current!.key
                        ? { ...result, fields: next } : result;
                }),
            });
        }
    };

    const change = (key: string, update: (field: IMappingField) => IMappingField): void => {
        setFields(updateField(fields, key, update));
    };

    const toggleExpanded = async (
        field: IMappingField,
        tables: string[],
    ): Promise<void> => {
        if (expanded.has(field.key)) {
            const next = new Set(expanded);
            next.delete(field.key);
            setExpanded(next);

            return;
        }
        if (field.reference?.loaded === false) {
            try {
                const columns = await requestColumns(
                    field.reference.mapping.referenced_schema,
                    field.reference.mapping.referenced_table);
                setLoadError(undefined);
                change(field.key, (current) => {
                    return {
                        ...loadReference(current, columns.columns ?? [], [], tables),
                        enabled: true,
                    };
                });
            } catch (error) {
                setLoadError(error instanceof Error ? error.message : String(error));

                return;
            }
        }
        setExpanded(new Set(expanded).add(field.key));
    };

    const sql = objectStatements(props.values, props.context)[0];

    const row = (
        field: IMappingField,
        depth: number,
        tables: string[],
        owner: IMappingField | undefined,
    ): preact.JSX.Element[] => {
        const reference = field.reference;
        const open = expanded.has(field.key) && reference?.loaded === true;
        const target = reference === undefined ? undefined
            : `${reference.mapping.referenced_schema}.${reference.mapping.referenced_table}`;
        const reducedAway = owner?.reference?.reduceTo !== undefined
            && owner.reference.reduceTo !== field.key;
        const viewField = !routine && reference === undefined;

        const jsonSide = (
            <div class="mrs-json" style={{ paddingLeft: `${depth * 1.25}rem` }}>
                {reference === undefined ? <span class="mrs-twistie" /> : (
                    <button
                        type="button"
                        class={`icon-button mrs-twistie codicon codicon-${
                            open ? "chevron-down" : "chevron-right"}`}
                        aria-label={open ? "Collapse" : "Expand"}
                        onClick={() => { void toggleExpanded(field, tables); }}
                    />
                )}
                <input
                    type="checkbox"
                    aria-label={`Include ${field.name}`}
                    checked={field.enabled}
                    disabled={reducedAway || field.missing === true}
                    onChange={() => {
                        if (reference !== undefined && !field.enabled
                            && !reference.loaded) {
                            void toggleExpanded(field, tables);

                            return;
                        }
                        change(field.key, (current) => {
                            return { ...current, enabled: !current.enabled };
                        });
                    }}
                />
                <input
                    type="text"
                    class="mrs-name"
                    aria-label="JSON field name"
                    value={field.name}
                    spellcheck={false}
                    onInput={(event) => {
                        const name = (event.target as HTMLInputElement).value;
                        change(field.key, (current) => {
                            return { ...current, name };
                        });
                    }}
                />
                {field.missing === true
                    ? <span class="mrs-missing">column dropped</span> : null}
                {viewField ? (
                    <span class="mrs-flags-row">
                        <Flag icon="key" on={field.isKey}
                            tooltip="Include field in object composite key"
                            onToggle={() => {
                                change(field.key, (current) => {
                                    return { ...current, isKey: !current.isKey };
                                });
                            }} />
                        <Flag icon="list-ordered" on={field.allowSorting}
                            tooltip="Allow sorting operations using this field"
                            onToggle={() => {
                                change(field.key, (current) => {
                                    return { ...current, allowSorting: !current.allowSorting };
                                });
                            }} />
                        <Flag icon="filter" on={!field.allowFiltering}
                            tooltip="Prevent filtering operations on this field"
                            onToggle={() => {
                                change(field.key, (current) => {
                                    return {
                                        ...current, allowFiltering: !current.allowFiltering,
                                    };
                                });
                            }} />
                        <Flag icon="lock" on={field.noUpdate}
                            tooltip="Prevent updates on this field"
                            onToggle={() => {
                                change(field.key, (current) => {
                                    return { ...current, noUpdate: !current.noUpdate };
                                });
                            }} />
                        <Flag icon="eye-closed" on={field.noCheck}
                            tooltip="Exclude this field from ETAG calculations"
                            onToggle={() => {
                                change(field.key, (current) => {
                                    return { ...current, noCheck: !current.noCheck };
                                });
                            }} />
                        <Flag icon="person" on={field.rowOwnership}
                            tooltip="Set as row ownership field"
                            onToggle={() => {
                                setFields(toggleRowOwnership(fields, field.key));
                            }} />
                    </span>
                ) : null}
                {isResult && document.objectType === "PROCEDURE" ? (
                    <button
                        type="button"
                        class="icon-button codicon codicon-trash"
                        aria-label="Delete field"
                        data-tooltip="Delete field"
                        onClick={() => {
                            setFields(fields.filter((candidate) => {
                                return candidate.key !== field.key;
                            }));
                        }}
                    />
                ) : null}
            </div>
        );

        const relationalSide = reference === undefined ? (
            <div class="mrs-relational">
                <span class={`codicon codicon-${field.column?.is_primary === true
                    ? "key" : "symbol-field"}`}
                data-tooltip={field.column?.is_primary === true
                    ? "Primary key column"
                    : field.column?.not_null === true
                        ? "Table column that must not be NULL" : "Table column"} />
                {routine && (field.column?.in === true || field.column?.out === true) ? (
                    <span class="mrs-mode">
                        {field.column?.in === true && field.column.out === true
                            ? "INOUT" : field.column?.in === true ? "IN" : "OUT"}
                    </span>
                ) : null}
                {isResult ? (
                    <>
                        <input
                            type="text"
                            class="mrs-name"
                            aria-label="Column name"
                            value={field.column?.name ?? ""}
                            onInput={(event) => {
                                const name = (event.target as HTMLInputElement).value;
                                change(field.key, (current) => {
                                    return { ...current, column: { ...current.column!, name } };
                                });
                            }}
                        />
                        <input
                            type="text"
                            class="mrs-type"
                            aria-label="Datatype"
                            value={field.column?.datatype ?? ""}
                            onInput={(event) => {
                                const datatype = (event.target as HTMLInputElement).value;
                                change(field.key, (current) => {
                                    return {
                                        ...current, column: { ...current.column!, datatype },
                                    };
                                });
                            }}
                        />
                    </>
                ) : (
                    <>
                        <span>{field.column?.name}</span>
                        <span class="mrs-type">{field.column?.datatype}</span>
                    </>
                )}
            </div>
        ) : (
            <div class="mrs-relational">
                <span class="codicon codicon-references"
                    data-tooltip={`Table with a ${reference.mapping.kind} relationship`} />
                <span class="mrs-kind">{reference.mapping.kind}</span>
                <span>{target}</span>
                {field.enabled ? (
                    <CrudPills crud={reference.crud} kind={reference.mapping.kind}
                        short={depth > 0}
                        onChange={(crud) => {
                            change(field.key, (current) => {
                                return { ...current, reference: { ...current.reference!, crud } };
                            });
                        }} />
                ) : null}
                {field.enabled && reference.mapping.kind !== "1:n" ? (
                    <Pill label="UNNEST" on={reference.unnest}
                        tooltip="Merge columns of the referenced table into this JSON object"
                        onToggle={() => {
                            change(field.key, (current) => {
                                return {
                                    ...current,
                                    reference: {
                                        ...current.reference!,
                                        unnest: !current.reference!.unnest,
                                    },
                                };
                            });
                        }} />
                ) : null}
                {field.enabled && reference.mapping.kind === "1:n" && reference.loaded ? (
                    <select
                        class="mrs-reduce"
                        aria-label="Reduce to field"
                        data-tooltip={"Display selected field instead of the "
                            + "object. Updates will be disabled"}
                        onChange={(event) => {
                            const key = (event.target as HTMLSelectElement).value;
                            setFields(reduceReference(fields, field.key,
                                key === "" ? undefined : key));
                        }}
                    >
                        <option value="" selected={reference.reduceTo === undefined}>
                            Unnest field ...
                        </option>
                        {reference.children.filter((child) => {
                            return child.reference === undefined;
                        }).map((child) => {
                            return (
                                <option key={child.key} value={child.key}
                                    selected={child.key === reference.reduceTo}>
                                    {child.name}
                                </option>
                            );
                        })}
                    </select>
                ) : null}
            </div>
        );

        const rows = [(
            <div
                key={field.key}
                class={`mrs-row${reference === undefined ? "" : " reference"}${
                    field.enabled ? "" : " disabled"}`}
                data-field={field.name}
            >
                {jsonSide}
                {relationalSide}
            </div>
        )];
        if (open) {
            rows.push(...reference!.children.flatMap((child) => {
                return row(child, depth + 1, [...tables, target!], field);
            }));
        }

        return rows;
    };

    const tables = [`${document.dbSchema}.${document.dbObject}`];
    const procedure = document.objectType === "PROCEDURE";

    return (
        <div class="mrs-mapping">
            <div class="mrs-mapping-toolbar row wrap">
                {routine ? (
                    <>
                        <select
                            aria-label="Data mapping"
                            onChange={(event) => {
                                setMapping((event.target as HTMLSelectElement).value);
                            }}
                        >
                            <option value="parameters" selected={mapping === "parameters"}>
                                Parameters
                            </option>
                            {(document.results ?? []).map((result, index) => {
                                return (
                                    <option key={result.key} value={result.key}
                                        selected={mapping === result.key}>
                                        {`Result ${index + 1}`}
                                    </option>
                                );
                            })}
                        </select>
                        <input
                            type="text"
                            class="mrs-name"
                            aria-label="Data mapping name"
                            value={current?.name ?? ""}
                            onInput={(event) => {
                                const name = (event.target as HTMLInputElement).value;
                                if (current === document.parameters) {
                                    setDocument({
                                        ...document,
                                        parameters: { ...document.parameters!, name },
                                    });
                                } else {
                                    setDocument({
                                        ...document,
                                        results: (document.results ?? []).map((result) => {
                                            return result.key === current!.key
                                                ? { ...result, name } : result;
                                        }),
                                    });
                                }
                            }}
                        />
                        {procedure ? (
                            <>
                                <button type="button"
                                    data-tooltip="Add a result set definition returned by this stored procedure"
                                    onClick={() => {
                                        const results = document.results ?? [];
                                        const key = newKey();
                                        setDocument({
                                            ...document,
                                            results: [...results, {
                                                key,
                                                name: `${document.className}${
                                                    results.length === 0 ? "" : results.length + 1}`,
                                                fields: [],
                                            }],
                                        });
                                        setMapping(key);
                                    }}>
                                    Add Result
                                </button>
                                {isResult ? (
                                    <button type="button"
                                        data-tooltip="Remove the current result set definition"
                                        onClick={() => {
                                            setDocument({
                                                ...document,
                                                results: (document.results ?? []).filter((result) => {
                                                    return result.key !== current!.key;
                                                }),
                                            });
                                            setMapping("parameters");
                                        }}>
                                        Remove Result
                                    </button>
                                ) : null}
                            </>
                        ) : null}
                        {isResult && procedure ? (
                            <button type="button" onClick={() => {
                                setFields([...fields, {
                                    key: newKey(),
                                    name: "newField",
                                    column: {
                                        name: snakeCase("newField"),
                                        datatype: "VARCHAR(255)",
                                    },
                                    enabled: true,
                                    allowFiltering: true,
                                    allowSorting: false,
                                    noCheck: false,
                                    noUpdate: false,
                                    isKey: false,
                                    rowOwnership: false,
                                }]);
                            }}>
                                Add Field
                            </button>
                        ) : null}
                    </>
                ) : (
                    <>
                        <label class="row">
                            <span class="field-caption">Class</span>
                            <input
                                type="text"
                                class="mrs-name"
                                aria-label="Class name"
                                value={document.className}
                                onInput={(event) => {
                                    setDocument({
                                        ...document,
                                        className: (event.target as HTMLInputElement).value,
                                    });
                                }}
                            />
                        </label>
                        <CrudPills crud={document.crud} onChange={(crud) => {
                            setDocument({ ...document, crud });
                        }} />
                        <button type="button" class="icon-button codicon codicon-check-all"
                            aria-label="Select all fields" data-tooltip="Select all fields"
                            onClick={() => { setFields(setAllEnabled(fields, undefined, true)); }} />
                        <button type="button" class="icon-button codicon codicon-close-all"
                            aria-label="Deselect all fields" data-tooltip="Deselect all fields"
                            onClick={() => { setFields(setAllEnabled(fields, undefined, false)); }} />
                    </>
                )}
                <span class="spacer" />
                <label class="checkbox">
                    <input type="checkbox" checked={preview}
                        onChange={() => { setPreview(!preview); }} />
                    <span>SQL Preview</span>
                </label>
                <button type="button" class="icon-button codicon codicon-copy"
                    aria-label="Copy SQL to Clipboard" data-tooltip="Copy SQL to Clipboard"
                    onClick={() => {
                        post<MrsWebviewMessage>({ type: "copy", text: sql });
                    }} />
            </div>
            {props.ctx.problem("className") === undefined ? null
                : <p class="message error">{props.ctx.problem("className")}</p>}
            {loadError === undefined ? null : <p class="message error">{loadError}</p>}
            {preview ? (
                <pre class="mrs-code mrs-readonly mrs-preview">{sql}</pre>
            ) : (
                <div class="mrs-mapping-rows" role="tree">
                    {fields.length === 0
                        ? <p class="note">No fields.</p>
                        : fields.flatMap((field) => {
                            return row(field, 0, tables, undefined);
                        })}
                </div>
            )}
        </div>
    );
};
