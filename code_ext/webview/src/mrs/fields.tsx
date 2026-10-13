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

import type { MrsWebviewMessage } from "../../../src/mrs/mrsDialogProtocol.js";
import { Field } from "../dialogParts.js";
import { post } from "../vscodeApi.js";

/**
 * The fields the MRS dialogs are built from. Each one reads and writes one
 * key of the dialog's values through an {@link IFieldContext}, and shows
 * the key's problem in place of its hint once there is one to show.
 */

/** What a field needs from its dialog. */
export interface IFieldContext {
    get(field: string): unknown;
    set(field: string, value: unknown): void;
    /**
     * Changes a field from its latest value, for a change that lands after
     * a wait, when the value read before it may be out of date.
     */
    update(field: string, change: (current: unknown) => unknown): void;
    /** Counts a field as edited, so its problem shows. */
    touch(field: string): void;
    /** The problem to show under the field, if any. */
    problem(field: string): string | undefined;
    busy: boolean;
}

/** The three access states and how the MySQL Shell's dialogs say them. */
export const ACCESS_CHOICES = [
    { value: 1, label: "Access ENABLED" },
    { value: 0, label: "Access DISABLED" },
    { value: 2, label: "PRIVATE Access Only" },
];

const hintOf = (
    ctx: IFieldContext,
    field: string,
    hint: string | undefined,
): string | undefined => {
    return ctx.problem(field) ?? hint;
};

export const TextField = (props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    type?: "text" | "password";
    placeholder?: string;
    disabled?: boolean;
}): preact.JSX.Element => {
    const invalid = props.ctx.problem(props.field) !== undefined;

    return (
        <Field
            caption={props.caption}
            hint={hintOf(props.ctx, props.field, props.hint)}
        >
            <input
                type={props.type ?? "text"}
                name={props.field}
                class={invalid ? "invalid" : undefined}
                aria-invalid={invalid}
                value={String(props.ctx.get(props.field) ?? "")}
                placeholder={props.placeholder ?? ""}
                spellcheck={false}
                disabled={props.ctx.busy || props.disabled === true}
                onInput={(event) => {
                    props.ctx.set(props.field,
                        (event.target as HTMLInputElement).value);
                }}
            />
        </Field>
    );
};

export const TextArea = (props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    rows?: number;
    placeholder?: string;
    readOnly?: boolean;
}): preact.JSX.Element => {
    const invalid = props.ctx.problem(props.field) !== undefined;

    return (
        <div class="group mrs-text-group">
            <Field
                caption={props.caption}
                hint={hintOf(props.ctx, props.field, props.hint)}
            >
                <textarea
                    name={props.field}
                    class={invalid ? "invalid mrs-code" : "mrs-code"}
                    aria-invalid={invalid}
                    rows={props.rows ?? 6}
                    value={String(props.ctx.get(props.field) ?? "")}
                    placeholder={props.placeholder ?? ""}
                    spellcheck={false}
                    readOnly={props.readOnly === true}
                    disabled={props.ctx.busy}
                    onInput={(event) => {
                        props.ctx.set(props.field,
                            (event.target as HTMLTextAreaElement).value);
                    }}
                />
            </Field>
        </div>
    );
};

export const CheckBox = (props: {
    ctx: IFieldContext;
    field: string;
    label: string;
    hint?: string;
}): preact.JSX.Element => {
    const problem = props.ctx.problem(props.field);

    return (
        <div class="mrs-check">
            <label class="checkbox" data-tooltip={props.hint}>
                <input
                    type="checkbox"
                    name={props.field}
                    checked={props.ctx.get(props.field) === true}
                    disabled={props.ctx.busy}
                    onChange={(event) => {
                        props.ctx.set(props.field,
                            (event.target as HTMLInputElement).checked);
                    }}
                />
                <span>{props.label}</span>
            </label>
            {problem === undefined
                ? null : <span class="field-hint mrs-problem">{problem}</span>}
        </div>
    );
};

export const Select = <T extends string | number | boolean>(props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    choices: Array<{ value: T; label: string }>;
    disabled?: boolean;
    /** Offers an empty choice, for none. */
    optional?: boolean;
}): preact.JSX.Element => {
    const current = props.ctx.get(props.field);

    return (
        <Field
            caption={props.caption}
            hint={hintOf(props.ctx, props.field, props.hint)}
        >
            <select
                name={props.field}
                disabled={props.ctx.busy || props.disabled === true}
                onChange={(event) => {
                    const index = (event.target as HTMLSelectElement)
                        .selectedIndex - (props.optional === true ? 1 : 0);
                    props.ctx.set(props.field, index < 0
                        ? "" : props.choices[index].value);
                }}
            >
                {props.optional === true
                    ? <option value="" selected={current === ""} />
                    : null}
                {props.choices.map((choice) => {
                    return (
                        <option
                            key={String(choice.value)}
                            value={String(choice.value)}
                            selected={choice.value === current}
                        >
                            {choice.label}
                        </option>
                    );
                })}
            </select>
        </Field>
    );
};

/** Picks one of the strings offered, by their own text. */
export const StringSelect = (props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    choices: string[];
    disabled?: boolean;
    optional?: boolean;
}): preact.JSX.Element => {
    return (
        <Select
            ctx={props.ctx}
            field={props.field}
            caption={props.caption}
            {...(props.hint === undefined ? {} : { hint: props.hint })}
            {...(props.disabled === undefined ? {} : { disabled: props.disabled })}
            {...(props.optional === undefined ? {} : { optional: props.optional })}
            choices={props.choices.map((choice) => {
                return { value: choice, label: choice };
            })}
        />
    );
};

/** A path typed in, or picked with the VS Code dialog behind Browse. */
export const PathField = (props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    folders: boolean;
}): preact.JSX.Element => {
    const invalid = props.ctx.problem(props.field) !== undefined;

    return (
        <Field
            caption={props.caption}
            hint={hintOf(props.ctx, props.field, props.hint)}
        >
            <div class="row">
                <input
                    type="text"
                    name={props.field}
                    class={invalid ? "invalid" : undefined}
                    value={String(props.ctx.get(props.field) ?? "")}
                    spellcheck={false}
                    disabled={props.ctx.busy}
                    onInput={(event) => {
                        props.ctx.set(props.field,
                            (event.target as HTMLInputElement).value);
                    }}
                />
                <button
                    type="button"
                    disabled={props.ctx.busy}
                    onClick={() => {
                        post<MrsWebviewMessage>({
                            type: "browse",
                            field: props.field,
                            folders: props.folders,
                        });
                    }}
                >
                    Browse...
                </button>
            </div>
        </Field>
    );
};

/** One checkbox per choice, for a list of strings. */
export const CheckList = (props: {
    ctx: IFieldContext;
    field: string;
    caption: string;
    hint?: string;
    choices: string[];
}): preact.JSX.Element => {
    const selected = (props.ctx.get(props.field) as string[] | undefined) ?? [];

    return (
        <div class="group">
            <h2>{props.caption}</h2>
            {props.choices.length === 0
                ? <p class="note">None defined.</p>
                : (
                    <div class="row wrap">
                        {props.choices.map((choice) => {
                            return (
                                <label class="checkbox" key={choice}>
                                    <input
                                        type="checkbox"
                                        name={`${props.field}:${choice}`}
                                        checked={selected.includes(choice)}
                                        disabled={props.ctx.busy}
                                        onChange={(event) => {
                                            const on = (event.target as
                                                HTMLInputElement).checked;
                                            props.ctx.set(props.field, on
                                                ? [...selected, choice]
                                                : selected.filter((item) => {
                                                    return item !== choice;
                                                }));
                                        }}
                                    />
                                    <span>{choice}</span>
                                </label>
                            );
                        })}
                    </div>
                )}
            {props.hint === undefined && props.ctx.problem(props.field) === undefined
                ? null
                : (
                    <p class="note">
                        {props.ctx.problem(props.field) ?? props.hint}
                    </p>
                )}
        </div>
    );
};
