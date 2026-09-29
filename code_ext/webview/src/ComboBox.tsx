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

/**
 * A text box with a list of choices under a button beside it.
 *
 * Not an `<input list>`: a browser only offers the datalist entries that
 * match what the box holds already, so a box that starts on `12` would
 * never offer `Server on the PATH` until it was emptied by hand. This list
 * always shows every choice; typing is still the box's own.
 *
 * Its styles are in `sandboxStyles.css`, the one dialog that uses it.
 */
export const ComboBox = (props: {
    value: string;
    choices: readonly string[];
    onInput(value: string): void;
    invalid?: boolean;
    disabled?: boolean;
    placeholder?: string;
    /** What the button that opens the list is called. */
    listLabel: string;
}): preact.JSX.Element => {
    const [open, setOpen] = useState(false);
    // The choice the arrow keys are on, or -1 for none.
    const [highlight, setHighlight] = useState(-1);

    const show = (): void => {
        setHighlight(props.choices.indexOf(props.value));
        setOpen(true);
    };

    const pick = (choice: string): void => {
        props.onInput(choice);
        setOpen(false);
    };

    const onKeyDown = (event: KeyboardEvent): void => {
        const last = props.choices.length - 1;
        switch (event.key) {
            case "ArrowDown": {
                event.preventDefault();
                if (open) {
                    setHighlight((at) => { return Math.min(at + 1, last); });
                } else {
                    show();
                }
                break;
            }

            case "ArrowUp": {
                if (open) {
                    event.preventDefault();
                    setHighlight((at) => { return Math.max(at - 1, 0); });
                }
                break;
            }

            case "Enter": {
                if (open && highlight >= 0) {
                    event.preventDefault();
                    pick(props.choices[highlight]!);
                }
                break;
            }

            case "Escape": {
                if (open) {
                    // The list closes; the dialog does not.
                    event.preventDefault();
                    event.stopPropagation();
                    setOpen(false);
                }
                break;
            }

            default:
        }
    };

    return (
        <div
            class="combo"
            onFocusOut={(event) => {
                // Closed once focus leaves the box and its button, not when
                // it moves between the two.
                const next = event.relatedTarget as Node | null;
                if (next === null
                    || !(event.currentTarget as HTMLElement).contains(next)) {
                    setOpen(false);
                }
            }}
        >
            <div class="row">
                <input
                    type="text"
                    class={props.invalid ? "invalid" : undefined}
                    aria-invalid={props.invalid}
                    role="combobox"
                    aria-expanded={open}
                    aria-autocomplete="list"
                    value={props.value}
                    placeholder={props.placeholder}
                    spellcheck={false}
                    disabled={props.disabled}
                    onInput={(event) => {
                        props.onInput((event.target as HTMLInputElement).value);
                    }}
                    onKeyDown={onKeyDown}
                />
                <button
                    type="button"
                    class="icon-button"
                    aria-label={props.listLabel}
                    title={props.listLabel}
                    disabled={props.disabled}
                    onClick={() => {
                        if (open) {
                            setOpen(false);
                        } else {
                            show();
                        }
                    }}
                >
                    <span class="codicon codicon-chevron-down" aria-hidden="true" />
                </button>
            </div>
            {open ? (
                <ul class="combo-list" role="listbox">
                    {props.choices.map((choice, at) => {
                        return (
                            <li
                                key={choice}
                                role="option"
                                aria-selected={choice === props.value}
                                class={at === highlight
                                    ? "combo-choice highlighted"
                                    : "combo-choice"}
                                // Before the box loses focus, which would
                                // close the list under the pointer.
                                onMouseDown={(event) => {
                                    event.preventDefault();
                                    pick(choice);
                                }}
                                onMouseEnter={() => { setHighlight(at); }}
                            >
                                {choice}
                            </li>
                        );
                    })}
                </ul>
            ) : null}
        </div>
    );
};
