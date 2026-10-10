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

import { useRef, useState } from "preact/hooks";

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
import addIcon from "../assets/mrs/add.svg";
import allowSortingIcon from "../assets/mrs/allowSorting.svg";
import arrowIcon from "../assets/mrs/arrow.svg";
import checkAllIcon from "../assets/mrs/checkAll.svg";
import checkNoneIcon from "../assets/mrs/checkNone.svg";
import closeIcon from "../assets/mrs/close.svg";
import inIcon from "../assets/mrs/in.svg";
import inOutIcon from "../assets/mrs/inOut.svg";
import isKeyIcon from "../assets/mrs/isKey.svg";
import dbObjectIcon from "../assets/mrs/mrsDbObject.svg";
import noCheckIcon from "../assets/mrs/noCheck.svg";
import noFilterIcon from "../assets/mrs/noFilter.svg";
import noUpdateIcon from "../assets/mrs/noUpdate.svg";
import outIcon from "../assets/mrs/out.svg";
import rowOwnershipIcon from "../assets/mrs/rowOwnership.svg";
import functionIcon from "../assets/mrs/schemaFunction.svg";
import procedureIcon from "../assets/mrs/schemaProcedure.svg";
import tableIcon from "../assets/mrs/schemaTable.svg";
import columnIcon from "../assets/mrs/schemaTableColumn.svg";
import columnNotNullIcon from "../assets/mrs/schemaTableColumnNN.svg";
import columnPkIcon from "../assets/mrs/schemaTableColumnPK.svg";
import foreignKey11Icon from "../assets/mrs/schemaTableForeignKey11.svg";
import foreignKey1NIcon from "../assets/mrs/schemaTableForeignKey1N.svg";
import viewIcon from "../assets/mrs/schemaView.svg";
import unnestIcon from "../assets/mrs/unnest.svg";
import { post } from "../vscodeApi.js";
import type { IFieldContext } from "./fields.js";

/**
 * The Data Mapping tab of the REST Object dialog: the MySQL Shell's
 * object field editor, row for row. Two columns - the JSON side, with
 * the field names and their flags, and the relational side, with the
 * columns and the tables references lead to - over a tree whose lists
 * open with `{` and close with `}`.
 *
 * As there: the flags of a field are monochrome icons that show only
 * while the pointer is over its JSON cell - those that are on always -
 * with `…` standing in for them otherwise; a name is edited with a double
 * click, Enter keeping and Escape dropping the edit; a single click on a
 * reference opens or closes it; an unnested reference shows its fields as
 * copies at its own level. A reference's table is loaded when it is first
 * opened, from the host, which runs `SHOW REST COLUMNS` for it.
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

/** How long a click waits to see whether it is the first of a double click. */
const DOUBLE_CLICK_MS = 200;

/** Keeps a click or double click on a control off the row under it. */
const stop = (event: Event): void => {
    event.stopPropagation();
};

/**
 * An icon as the MySQL Shell draws them: the SVG as a mask over the
 * theme's icon colour, so it takes the colour of the theme.
 */
const Icon = (props: {
    src: string;
    class?: string;
    tooltip?: string;
    size?: number;
    pressed?: boolean;
    onClick?: () => void;
}): preact.JSX.Element => {
    const size = `${props.size ?? 16}px`;
    const onClick = props.onClick;

    return (
        <div
            class={`mrs-icon${props.class === undefined ? "" : ` ${props.class}`}`}
            style={{
                maskImage: `url("${props.src}")`,
                WebkitMaskImage: `url("${props.src}")`,
                width: size,
                height: size,
                minWidth: size,
            }}
            role={onClick === undefined ? undefined : "button"}
            aria-label={props.tooltip}
            aria-pressed={props.pressed}
            data-tooltip={props.tooltip}
            onClick={onClick === undefined ? undefined : (event) => {
                // Kept off the row: a click on a reference row opens it.
                event.stopPropagation();
                onClick();
            }}
            onDblClick={stop}
        />
    );
};

/** A flag of a field: shown when on, else only on hover and faint. */
const Flag = (props: {
    src: string;
    on: boolean;
    tooltip: string;
    onToggle?: () => void;
}): preact.JSX.Element => {
    return (
        <Icon
            src={props.src}
            class={props.on ? "selected" : "notSelected"}
            tooltip={props.tooltip}
            pressed={props.on}
            {...(props.onToggle === undefined ? {} : { onClick: props.onToggle })}
        />
    );
};

/** The checkbox of a field, the MySQL Shell's: checked, unchecked or neither. */
const CheckBox = (props: {
    state: "checked" | "unchecked" | "indeterminate";
    disabled?: boolean;
    label: string;
    onClick: () => void;
}): preact.JSX.Element => {
    const disabled = props.disabled === true;

    return (
        <span
            class={`mrs-checkbox ${props.state}${disabled ? " disabled" : ""}`}
            role="checkbox"
            aria-checked={props.state === "indeterminate" ? "mixed"
                : props.state === "checked"}
            aria-disabled={disabled}
            aria-label={props.label}
            tabIndex={disabled ? -1 : 0}
            onClick={(event) => {
                event.stopPropagation();
                if (!disabled) {
                    props.onClick();
                }
            }}
            onKeyDown={(event) => {
                if ((event.key === " " || event.key === "Enter") && !disabled) {
                    event.preventDefault();
                    props.onClick();
                }
            }}
            onDblClick={stop}
        >
            <span class="checkMark" />
        </span>
    );
};

/** The INSERT / UPDATE / DELETE switches of a list, as one segmented pill. */
const CrudPills = (props: {
    crud: ICrudFlags;
    kind?: string;
    short?: boolean;
    locked?: boolean;
    onChange: (crud: ICrudFlags) => void;
}): preact.JSX.Element => {
    // An n:1 reference can only be updated through.
    const operations: Array<"insert" | "update" | "delete"> =
        props.kind === "n:1" ? ["update"] : ["insert", "update", "delete"];

    return (
        <div class={`crudDiv${operations.length > 1 ? " multiItems" : ""}`}>
            {operations.map((operation) => {
                const name = operation.toUpperCase();
                const on = props.crud[operation];

                return (
                    <div
                        key={operation}
                        class={on ? "activated" : "deactivated"}
                        role="switch"
                        aria-checked={on}
                        aria-label={name}
                        data-tooltip={`Allow ${name} operations on this object`}
                        onClick={(event) => {
                            event.stopPropagation();
                            if (props.locked !== true) {
                                props.onChange({ ...props.crud, [operation]: !on });
                            }
                        }}
                        onDblClick={stop}
                    >
                        {props.short === true && operations.length > 1
                            ? name.slice(0, 3) : name}
                    </div>
                );
            })}
        </div>
    );
};

/** One line of the tree, as it is drawn. */
interface ITreeRow {
    kind: "top" | "open" | "field" | "copy" | "close" | "loading";
    key: string;
    depth: number;
    field?: IMappingField;
    /** The reference whose list the row is in; undefined at the top. */
    owner?: IMappingField;
    toMany?: boolean;
}

/** An edit in progress: a field's name, or a result column's `name: type`. */
interface IEdit {
    key: string;
    side: "json" | "relational";
    text: string;
}

/**
 * Whether a reference's fields are merged into its parent object. A 1:n
 * reference reduced to one field is unnested too, in the model, but stays
 * a list: its other fields are shown, disabled.
 */
const merged = (field: IMappingField): boolean => {
    return field.reference?.unnest === true
        && field.reference.reduceTo === undefined;
};

const removed = <T,>(set: Set<T>, item: T): Set<T> => {
    const next = new Set(set);
    next.delete(item);

    return next;
};

export const DataMappingEditor = (props: {
    ctx: IFieldContext;
    values: IObjectDialogValues;
    context: IObjectContext;
}): preact.JSX.Element => {
    const document = props.values.document;
    const routine = isRoutine(document.objectType);
    const procedure = document.objectType === "PROCEDURE";
    const [preview, setPreview] = useState(false);
    const [mapping, setMapping] = useState<string>("parameters");
    const [expanded, setExpanded] = useState<Set<string>>(() => {
        // What is stored opens up, so it can be seen.
        const open = new Set<string>();
        const walk = (fields: IMappingField[]): void => {
            for (const field of fields) {
                if (field.reference?.loaded === true && field.enabled
                    && !merged(field)) {
                    open.add(field.key);
                    walk(field.reference.children);
                }
            }
        };
        walk(document.fields);

        return open;
    });
    const [loading, setLoading] = useState<Set<string>>(new Set());
    const [editing, setEditing] = useState<IEdit | undefined>(undefined);
    const [loadError, setLoadError] = useState<string | undefined>(undefined);
    const clickTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
        undefined);

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
    const tableOrView = !routine;

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

    /** Changes a field of a view in the document as it is by then. */
    const changeLatest = (key: string,
        update: (field: IMappingField) => IMappingField): void => {
        props.ctx.update("document", (latest) => {
            const doc = latest as IMappingDocument;

            return { ...doc, fields: updateField(doc.fields, key, update) };
        });
    };

    /**
     * Loads a reference's table and includes it. The columns are put into
     * the document as it is when they arrive: the user may have edited it
     * meanwhile. References are only ever in a view's own fields.
     */
    const load = async (field: IMappingField, tables: string[]): Promise<boolean> => {
        setLoading((now) => { return new Set(now).add(field.key); });
        try {
            const columns = await requestColumns(
                field.reference!.mapping.referenced_schema,
                field.reference!.mapping.referenced_table);
            setLoadError(undefined);
            changeLatest(field.key, (now) => {
                return {
                    ...loadReference(now, columns.columns ?? [], [], tables),
                    enabled: true,
                };
            });

            return true;
        } catch (error) {
            setLoadError(error instanceof Error ? error.message : String(error));

            return false;
        } finally {
            setLoading((now) => { return removed(now, field.key); });
        }
    };

    /** Opens or closes a reference; the first opening loads and includes it. */
    const toggle = async (field: IMappingField, tables: string[]): Promise<void> => {
        const reference = field.reference;
        if (reference === undefined || merged(field) || loading.has(field.key)) {
            return;
        }
        if (expanded.has(field.key)) {
            setExpanded((now) => { return removed(now, field.key); });

            return;
        }
        setExpanded((now) => { return new Set(now).add(field.key); });
        if (!reference.loaded) {
            if (!await load(field, tables)) {
                setExpanded((now) => { return removed(now, field.key); });
            }
        } else if (!field.enabled) {
            change(field.key, (now) => { return { ...now, enabled: true }; });
        }
    };

    /**
     * The checkbox. As the MySQL Shell's: on a closed reference it opens
     * it; unchecked, a reference closes and forgets its table.
     */
    const check = async (field: IMappingField, tables: string[]): Promise<void> => {
        const reference = field.reference;
        if (reference !== undefined && !field.enabled) {
            if (expanded.has(field.key) && reference.loaded) {
                change(field.key, (now) => { return { ...now, enabled: true }; });
            } else {
                await toggle(field, tables);
            }

            return;
        }
        if (reference !== undefined) {
            setExpanded((now) => { return removed(now, field.key); });
            change(field.key, (now) => {
                return {
                    ...now,
                    enabled: false,
                    reference: {
                        ...now.reference!, loaded: false, children: [],
                        reduceTo: undefined,
                    },
                };
            });

            return;
        }
        change(field.key, (now) => { return { ...now, enabled: !now.enabled }; });
    };

    /** The Unnest switch of a 1:1 or n:1 reference. */
    const unnest = async (field: IMappingField, tables: string[]): Promise<void> => {
        const reference = field.reference!;
        if (reference.unnest) {
            change(field.key, (now) => {
                return { ...now, reference: { ...now.reference!, unnest: false } };
            });
            setExpanded((now) => { return new Set(now).add(field.key); });

            return;
        }
        if (!reference.loaded && !await load(field, tables)) {
            return;
        }
        changeLatest(field.key, (now) => {
            return {
                ...now,
                enabled: true,
                reference: { ...now.reference!, unnest: true },
            };
        });
        setExpanded((now) => { return removed(now, field.key); });
    };

    /** A single click on a reference row opens or closes it, unless doubled. */
    const rowClicked = (field: IMappingField, tables: string[]): void => {
        if (field.reference === undefined) {
            return;
        }
        clearTimeout(clickTimer.current);
        clickTimer.current = setTimeout(() => {
            void toggle(field, tables);
        }, DOUBLE_CLICK_MS);
    };

    const startEdit = (edit: IEdit): void => {
        clearTimeout(clickTimer.current);
        setEditing(edit);
    };

    /** Keeps an edit: a name, or a result column's `name: type`. */
    const commit = (edit: IEdit): void => {
        setEditing(undefined);
        const text = edit.text.trim();
        if (edit.key === "top") {
            // Kept also when empty: the dialog says what is missing.
            if (!routine) {
                setDocument({ ...document, className: text });
                props.ctx.touch("className");
            } else if (current === document.parameters) {
                setDocument({
                    ...document, parameters: { ...document.parameters!, name: text },
                });
            } else {
                setDocument({
                    ...document,
                    results: (document.results ?? []).map((result) => {
                        return result.key === current!.key
                            ? { ...result, name: text } : result;
                    }),
                });
            }

            return;
        }
        if (edit.side === "json") {
            if (text !== "") {
                change(edit.key, (field) => { return { ...field, name: text }; });
            }

            return;
        }
        const colon = text.indexOf(":");
        const name = (colon < 0 ? text : text.slice(0, colon)).trim();
        const datatype = colon < 0 ? "" : text.slice(colon + 1).trim();
        change(edit.key, (field) => {
            return {
                ...field,
                column: {
                    ...field.column!,
                    ...(name === "" ? {} : { name }),
                    ...(datatype === "" ? {} : { datatype }),
                },
            };
        });
    };

    /** The inline editor of a name, as the MySQL Shell's. */
    const editor = (edit: IEdit, label: string): preact.JSX.Element => {
        return (
            <input
                type="text"
                class="fieldEditor"
                aria-label={label}
                value={edit.text}
                spellcheck={false}
                ref={(element) => {
                    if (element === null
                        || globalThis.document.activeElement === element) {
                        return;
                    }
                    element.focus();
                    // All of a new field's name; else the caret before the
                    // `:` of `name: type`, or at the end.
                    const colon = element.value.indexOf(":");
                    if (element.value === "newField") {
                        element.select();
                    } else if (colon >= 0) {
                        element.setSelectionRange(colon, colon);
                    } else {
                        element.setSelectionRange(element.value.length,
                            element.value.length);
                    }
                }}
                onInput={(event) => {
                    setEditing({
                        ...edit, text: (event.target as HTMLInputElement).value,
                    });
                }}
                onKeyDown={(event) => {
                    if (event.key === "Enter") {
                        event.preventDefault();
                        event.stopPropagation();
                        commit({
                            ...edit, text: (event.target as HTMLInputElement).value,
                        });
                    } else if (event.key === "Escape") {
                        // Drops the edit, not the dialog.
                        event.preventDefault();
                        event.stopPropagation();
                        setEditing(undefined);
                    }
                }}
                onBlur={() => { setEditing(undefined); }}
                onClick={stop}
                onDblClick={stop}
            />
        );
    };

    // --- the rows ----------------------------------------------------------

    const rows: ITreeRow[] = [{ kind: "top", key: "top", depth: 0 }];
    const tablesOf = new Map<string, string[]>();
    const walk = (
        level: IMappingField[],
        depth: number,
        owner: IMappingField | undefined,
        tables: string[],
    ): void => {
        for (const field of level) {
            tablesOf.set(field.key, tables);
            rows.push({
                kind: "field", key: field.key, depth, field,
                ...(owner === undefined ? {} : { owner }),
            });
            const reference = field.reference;
            if (reference === undefined) {
                continue;
            }
            if (merged(field)) {
                // Its columns, merged into this object, show here as copies.
                for (const child of reference.children) {
                    if (child.reference === undefined && child.enabled) {
                        rows.push({
                            kind: "copy", key: `copy-${child.key}`, depth,
                            field: child, owner: field,
                        });
                    }
                }
                continue;
            }
            if (!expanded.has(field.key)) {
                continue;
            }
            if (!reference.loaded) {
                rows.push({
                    kind: "loading", key: `loading-${field.key}`,
                    depth: depth + 1, owner: field,
                });
                continue;
            }
            const toMany = reference.mapping.to_many;
            const target = `${reference.mapping.referenced_schema}.`
                + reference.mapping.referenced_table;
            rows.push({
                kind: "open", key: `open-${field.key}`, depth, owner: field, toMany,
            });
            walk(reference.children, depth + 1, field, [...tables, target]);
            rows.push({
                kind: "close", key: `close-${field.key}`, depth, owner: field, toMany,
            });
        }
    };
    walk(fields, 1, undefined, [`${document.dbSchema}.${document.dbObject}`]);
    rows.push({ kind: "close", key: "close-top", depth: 0 });

    const mappingName = current?.name ?? document.className;

    const newField = (): IMappingField => {
        return {
            key: newKey(),
            name: "newField",
            column: { name: snakeCase("newField"), datatype: "VARCHAR(255)" },
            enabled: true,
            allowFiltering: true,
            allowSorting: false,
            noCheck: false,
            noUpdate: false,
            isKey: false,
            rowOwnership: false,
        };
    };

    /** The `… ` that stands in for the flags until the pointer is there. */
    const more = <span class="label">…</span>;

    /** The flags of a column or parameter field. */
    const fieldFlags = (field: IMappingField): preact.JSX.Element[] => {
        const flags = [(
            <Flag key="rowOwnership" src={rowOwnershipIcon} on={field.rowOwnership}
                tooltip="Set as row ownership field"
                onToggle={() => { setFields(toggleRowOwnership(fields, field.key)); }} />
        )];
        if (tableOrView) {
            const toggleFlag = (name: "isKey" | "allowSorting" | "allowFiltering"
                | "noUpdate" | "noCheck"): () => void => {
                return () => {
                    change(field.key, (now) => { return { ...now, [name]: !now[name] }; });
                };
            };
            flags.push(
                <Flag key="isKey" src={isKeyIcon} on={field.isKey}
                    tooltip="Include field in object composite key"
                    onToggle={toggleFlag("isKey")} />,
                <Flag key="allowSorting" src={allowSortingIcon} on={field.allowSorting}
                    tooltip="Allow sorting operations using this field"
                    onToggle={toggleFlag("allowSorting")} />,
                <Flag key="noFilter" src={noFilterIcon} on={!field.allowFiltering}
                    tooltip="Prevent filtering operations on this field"
                    onToggle={toggleFlag("allowFiltering")} />,
                <Flag key="noUpdate" src={noUpdateIcon} on={field.noUpdate}
                    tooltip="Prevent updates on this field"
                    onToggle={toggleFlag("noUpdate")} />,
                <Flag key="noCheck" src={noCheckIcon} on={field.noCheck}
                    tooltip="Exclude this field from ETAG calculations"
                    onToggle={toggleFlag("noCheck")} />,
            );
        }
        const column = field.column;
        if (routine && !isResult && document.objectType !== "FUNCTION"
            && (column?.in === true || column?.out === true)) {
            const both = column.in === true && column.out === true;
            flags.push(
                <Icon key="mode" class="selected"
                    src={both ? inOutIcon : column.out === true ? outIcon : inIcon}
                    tooltip={both ? "INOUT parameter"
                        : column.out === true ? "OUT parameter" : "IN parameter"} />,
            );
        }

        return flags;
    };

    /** The JSON side of a row. */
    const jsonCell = (row: ITreeRow): preact.JSX.Element => {
        if (row.kind === "top") {
            const edit = editing?.key === "top" ? editing : undefined;

            return (
                <div class="mrsObjectJsonFieldDiv">
                    <div class="fieldInfo">
                        <Icon src={dbObjectIcon} class="tableIcon" />
                        {edit === undefined ? (
                            <span class="tableName mrsObjectName"
                                data-tooltip="Double click to edit"
                                onDblClick={() => {
                                    startEdit({ key: "top", side: "json", text: mappingName });
                                }}>
                                {mappingName}
                            </span>
                        ) : editor(edit, routine ? "Data mapping name" : "Class name")}
                        <span class="bracket">{"{"}</span>
                    </div>
                    {isResult ? null : (
                        <div class="fieldOptions">
                            {tableOrView ? (
                                <Flag src={noCheckIcon} on={document.crud.noCheck}
                                    tooltip="Disable ETAG calculations for this table."
                                    onToggle={() => {
                                        setDocument({
                                            ...document,
                                            crud: {
                                                ...document.crud,
                                                noCheck: !document.crud.noCheck,
                                            },
                                        });
                                    }} />
                            ) : null}
                            <Icon src={checkAllIcon} class="action" tooltip="Select all fields"
                                onClick={() => {
                                    setFields(setAllEnabled(fields, undefined, true));
                                }} />
                            <Icon src={checkNoneIcon} class="action"
                                tooltip="Deselect all fields"
                                onClick={() => {
                                    setFields(setAllEnabled(fields, undefined, false));
                                }} />
                            {more}
                        </div>
                    )}
                </div>
            );
        }

        if (row.kind === "open" || row.kind === "close") {
            const addHere = row.key === "close-top" && isResult && procedure;

            return (
                <div class="mrsObjectJsonFieldDiv">
                    <span class="bracket">
                        {row.kind === "open"
                            ? (row.toMany === true ? "[ {" : "{")
                            : (row.toMany === true ? "}, ... ]" : "}")}
                    </span>
                    {addHere ? (
                        <Icon src={addIcon} class="addField" size={11}
                            tooltip="Add Field"
                            onClick={() => {
                                const field = newField();
                                setFields([...fields, field]);
                                startEdit({ key: field.key, side: "json", text: field.name });
                            }} />
                    ) : null}
                </div>
            );
        }

        if (row.kind === "loading") {
            return (
                <div class="mrsObjectJsonFieldDiv">
                    <span class="jsonFieldDisabled loading">Loading...</span>
                </div>
            );
        }

        const field = row.field!;
        const tables = tablesOf.get(field.key) ?? [];
        const reference = field.reference;
        const copy = row.kind === "copy";
        const reducedAway = row.owner?.reference?.reduceTo !== undefined
            && row.owner.reference.reduceTo !== field.key;
        const state = merged(field) ? "indeterminate"
            : field.enabled ? "checked" : "unchecked";
        const edit = !copy && editing?.side === "json" && editing.key === field.key
            ? editing : undefined;
        const open = expanded.has(field.key);

        let options: preact.JSX.Element | null = null;
        if (reference !== undefined) {
            options = (
                <div class="fieldOptions">
                    <Flag src={noCheckIcon} on={reference.crud.noCheck}
                        tooltip="Disable ETAG calculations for this table."
                        onToggle={() => {
                            change(field.key, (now) => {
                                const crud = now.reference!.crud;

                                return {
                                    ...now,
                                    reference: {
                                        ...now.reference!,
                                        crud: { ...crud, noCheck: !crud.noCheck },
                                    },
                                };
                            });
                        }} />
                    {reference.loaded && !merged(field) ? (
                        <>
                            <Icon src={checkAllIcon} class="action"
                                tooltip="Select all fields"
                                onClick={() => {
                                    setFields(setAllEnabled(fields, field.key, true));
                                }} />
                            <Icon src={checkNoneIcon} class="action"
                                tooltip="Deselect all fields"
                                onClick={() => {
                                    setFields(setAllEnabled(fields, field.key, false));
                                }} />
                        </>
                    ) : null}
                    {more}
                </div>
            );
        } else if (!copy && isResult) {
            options = procedure ? (
                <div class="fieldOptions">
                    <Icon src={closeIcon} class="action" tooltip="Delete field"
                        onClick={() => {
                            setFields(fields.filter((candidate) => {
                                return candidate.key !== field.key;
                            }));
                        }} />
                    {more}
                </div>
            ) : null;
        } else if (!copy) {
            options = <div class="fieldOptions">{fieldFlags(field)}{more}</div>;
        }

        return (
            <div class={`mrsObjectJsonFieldDiv ${reference === undefined
                ? "withoutChildren" : "withChildren"}`}>
                <div class="fieldInfo">
                    {reference === undefined || copy ? null : (
                        <span
                            class={`treeToggle codicon codicon-${open && !merged(field)
                                ? "chevron-down" : "chevron-right"}${merged(field)
                                ? " disabled" : ""}`}
                            role="button"
                            aria-label={open ? "Collapse" : "Expand"}
                            onClick={(event) => {
                                event.stopPropagation();
                                clearTimeout(clickTimer.current);
                                void toggle(field, tables);
                            }}
                            onDblClick={stop}
                        />
                    )}
                    {copy ? null : (
                        <CheckBox
                            state={state}
                            disabled={merged(field) || reducedAway
                                || field.missing === true || loading.has(field.key)}
                            label={`Include ${field.name}`}
                            onClick={() => { void check(field, tables); }}
                        />
                    )}
                    {edit === undefined ? (
                        <span
                            class={`fieldName ${field.enabled && !merged(field)
                                && !reducedAway ? "jsonField" : "jsonFieldDisabled"}`}
                            onDblClick={(event) => {
                                event.stopPropagation();
                                if (!copy) {
                                    startEdit({ key: field.key, side: "json", text: field.name });
                                }
                            }}
                        >
                            {field.name}
                        </span>
                    ) : editor(edit, "JSON field name")}
                    {copy ? (
                        <span class="unnestedFrom"
                            data-tooltip={`Unnested from ${row.owner!.name}`}>
                            (
                            <Icon src={unnestIcon} class="unnestedIcon" size={12} />
                            {` ${row.owner!.name} )`}
                        </span>
                    ) : null}
                    {field.missing === true
                        ? <span class="mrs-missing">column dropped</span> : null}
                    {reference !== undefined && !copy && reference.mapping.kind !== "1:n"
                        && reference.reduceTo === undefined ? (
                            <div
                                class={`unnestDiv ${reference.unnest ? "activated" : "deactivated"}`}
                                role="switch"
                                aria-checked={reference.unnest}
                                aria-label="Unnest"
                                data-tooltip="Merge columns of the referenced table into this JSON object"
                                onClick={(event) => {
                                    event.stopPropagation();
                                    clearTimeout(clickTimer.current);
                                    void unnest(field, tables);
                                }}
                                onDblClick={stop}
                            >
                                <Icon src={unnestIcon} size={12} />
                                <span>Unnest</span>
                            </div>
                        ) : null}
                </div>
                {options}
            </div>
        );
    };

    /** The relational side of a row. */
    const relationalCell = (row: ITreeRow): preact.JSX.Element | null => {
        if (row.kind === "top") {
            const icon = document.objectType === "VIEW" ? viewIcon
                : procedure ? procedureIcon
                    : document.objectType === "FUNCTION" ? functionIcon : tableIcon;

            return (
                <div class="mrsObjectDbColumnFieldDiv">
                    <Icon src={icon} class="tableIcon" />
                    <span class="tableName">{`${document.dbSchema}.${document.dbObject}`}</span>
                    {tableOrView ? (
                        <CrudPills crud={document.crud} onChange={(crud) => {
                            setDocument({ ...document, crud });
                        }} />
                    ) : null}
                </div>
            );
        }
        const field = row.field;
        if (field === undefined || row.kind !== "field" && row.kind !== "copy") {
            return null;
        }

        const reference = field.reference;
        if (reference !== undefined) {
            const kind = reference.mapping.kind;

            return (
                <div class="mrsObjectDbColumnFieldDiv">
                    <Icon src={arrowIcon} class="arrow" />
                    <Icon src={kind === "1:n" ? foreignKey1NIcon : foreignKey11Icon}
                        tooltip={`Table with a ${kind} relationship`} />
                    <Icon src={tableIcon} />
                    <span class="tableName">
                        {`${reference.mapping.referenced_schema}.`
                            + reference.mapping.referenced_table}
                    </span>
                    {field.enabled && !merged(field) ? (
                        <CrudPills crud={reference.crud} kind={kind}
                            short={row.depth > 1}
                            locked={reference.reduceTo !== undefined}
                            onChange={(crud) => {
                                change(field.key, (now) => {
                                    return { ...now, reference: { ...now.reference!, crud } };
                                });
                            }} />
                    ) : null}
                    {field.enabled && kind === "1:n" && reference.loaded ? (
                        <select
                            class="reduceToDropdown"
                            aria-label="Reduce to field"
                            data-tooltip={"Display selected field instead of the "
                                + "object. Updates will be disabled"}
                            onClick={stop}
                            onDblClick={stop}
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
                                        {child.column?.name ?? child.name}
                                    </option>
                                );
                            })}
                        </select>
                    ) : null}
                </div>
            );
        }

        const column = field.column;
        const columnIconOf = column?.is_primary === true
            ? { src: columnPkIcon, tooltip: "Primary key column" }
            : column?.not_null === true
                ? { src: columnNotNullIcon, tooltip: "Table column that must not be NULL" }
                : { src: columnIcon, tooltip: "Table column" };
        const edit = editing?.side === "relational" && editing.key === field.key
            ? editing : undefined;
        const editable = isResult && row.kind === "field";
        const text = isResult
            ? `${column?.name ?? ""}: ${column?.datatype ?? ""}` : column?.name ?? "";

        return (
            <div class="mrsObjectDbColumnFieldDiv">
                <Icon src={arrowIcon} class="arrow" />
                <Icon src={columnIconOf.src} tooltip={columnIconOf.tooltip} />
                {edit === undefined ? (
                    <span
                        class="columnName"
                        onDblClick={(event) => {
                            event.stopPropagation();
                            if (editable) {
                                startEdit({ key: field.key, side: "relational", text });
                            }
                        }}
                    >
                        {text}
                    </span>
                ) : editor(edit, "Column name and datatype")}
                {!isResult && routine && column?.datatype !== undefined ? (
                    <span class="datatype">{column.datatype}</span>
                ) : null}
            </div>
        );
    };

    const sql = objectStatements(props.values, props.context)[0];

    return (
        <div class="mrsObjectFieldEditor">
            <div class="settings">
                <label class="labelWithInput">
                    <span>DB Object:</span>
                    <input type="text" class="dbObjectInput" aria-label="DB Object"
                        value={`${document.dbSchema}.${document.dbObject}`} readOnly />
                </label>
                {routine ? (
                    <>
                        <select
                            class="mrsObjectSelect"
                            aria-label="Data mapping"
                            onChange={(event) => {
                                setEditing(undefined);
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
                        {procedure && isResult ? (
                            <button type="button" class="settingsButton"
                                aria-label="Remove Result"
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
                                <span class="codicon codicon-remove" />
                            </button>
                        ) : null}
                        {procedure ? (
                            <button type="button" class="settingsButton"
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
                                <span class="codicon codicon-add" />
                                <span>Add Result</span>
                            </button>
                        ) : null}
                    </>
                ) : null}
                <span class="spacer" />
                <div class="divider" />
                <button type="button"
                    class={`settingsButton${preview ? " activated" : ""}`}
                    aria-pressed={preview}
                    data-tooltip="Toggle MRS SQL Preview"
                    onClick={() => { setPreview(!preview); }}>
                    <span class="codicon codicon-search" />
                    <span>SQL Preview</span>
                </button>
                <button type="button" class="settingsButton"
                    aria-label="Copy SQL to Clipboard" data-tooltip="Copy SQL to Clipboard"
                    onClick={() => {
                        post<MrsWebviewMessage>({ type: "copy", text: sql });
                    }}>
                    <span class="codicon codicon-copy" />
                </button>
            </div>
            {props.ctx.problem("className") === undefined ? null
                : <p class="message error">{props.ctx.problem("className")}</p>}
            {loadError === undefined ? null : <p class="message error">{loadError}</p>}
            {preview ? (
                <pre class="mrs-code mrs-readonly mrs-preview">{sql}</pre>
            ) : (
                <div class="mrsObjectTreeGrid" role="tree">
                    {rows.map((row) => {
                        const field = row.field;
                        const reference = row.kind === "field" ? field?.reference : undefined;

                        return (
                            <div
                                key={row.key}
                                class={[
                                    "mrs-row",
                                    row.kind === "top" ? "topRow" : "",
                                    row.kind === "copy" ? "unnestedCopy" : "",
                                    reference === undefined ? "" : "reference",
                                    field === undefined || field.enabled ? "" : "disabled",
                                    field?.missing === true ? "deleted" : "",
                                ].filter(Boolean).join(" ")}
                                role="treeitem"
                                aria-level={row.depth + 1}
                                aria-expanded={reference === undefined ? undefined
                                    : expanded.has(field!.key)}
                                data-field={row.kind === "field" ? field!.name : undefined}
                                data-copy={row.kind === "copy" ? field!.name : undefined}
                                onClick={reference === undefined ? undefined : () => {
                                    rowClicked(field!, tablesOf.get(field!.key) ?? []);
                                }}
                                onDblClick={reference === undefined ? undefined : () => {
                                    // The second click of a double click: no toggle.
                                    clearTimeout(clickTimer.current);
                                }}
                            >
                                <div class="jsonCell"
                                    style={{ paddingLeft: `${row.depth * 24 + 4}px` }}>
                                    {jsonCell(row)}
                                </div>
                                <div class="relationalCell">{relationalCell(row)}</div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
};
