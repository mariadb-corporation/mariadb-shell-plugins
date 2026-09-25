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

import type { JSX } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

/** One entry of a toolbar menu; a separator when it has no caption. */
export interface IToolbarMenuItem {
    id: string;
    caption?: string;
    /** The class that draws its icon, as the toolbar's buttons do. */
    icon?: string;
    disabled?: boolean;
    /** Set for an item that is switched on or off: a check shows it on. */
    checked?: boolean;
}

interface IToolbarMenuProperties {
    /** What the button that opens the menu is called, for its tooltip. */
    title: string;
    /** Its accessible name. */
    label: string;
    /** Extra classes for that button, which draw its icon. */
    buttonClass: string;
    /** Draws a chevron after the icon, as a dropdown does. */
    dropdown?: boolean;
    items: IToolbarMenuItem[];
    /** The item that is picked, marked in the list. */
    selected?: string;
    onSelect(id: string): void;
}

/**
 * A toolbar button that opens a list of items above it.
 *
 * Above rather than below, because the bar it sits in runs along the
 * bottom of the result set and a list opened downwards would go off the
 * bottom of the view. It closes on a pick, a click anywhere else, Escape,
 * and the window losing focus - the ways any menu does.
 *
 * @param props The button and its items.
 *
 * @returns The rendered button, and its list while it is open.
 */
export const ToolbarMenu = (props: IToolbarMenuProperties): JSX.Element => {
    const [open, setOpen] = useState(false);
    /**
     * Where the list goes, against the window: the bar clips what
     * overflows it, so the list cannot hang off the button inside it.
     */
    const [place, setPlace] = useState({ right: 0, bottom: 0 });
    const host = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) {
            return undefined;
        }

        const onPointer = (event: Event): void => {
            if (!host.current?.contains(event.target as Node)) {
                setOpen(false);
            }
        };
        const onKey = (event: KeyboardEvent): void => {
            if (event.key === "Escape") {
                event.preventDefault();
                setOpen(false);
            }
        };
        const close = (): void => { setOpen(false); };
        document.addEventListener("mousedown", onPointer, true);
        document.addEventListener("keydown", onKey, true);
        window.addEventListener("blur", close);

        return () => {
            document.removeEventListener("mousedown", onPointer, true);
            document.removeEventListener("keydown", onKey, true);
            window.removeEventListener("blur", close);
        };
    }, [open]);

    return (
        <div class="toolbarMenuHost" ref={host}>
            <button
                type="button"
                class={`toolbarIcon ${props.buttonClass}${props.dropdown
                    ? " dropdownButton"
                    : ""}${open ? " toggled" : ""}`}
                title={props.title}
                aria-label={props.label}
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={(event) => {
                    const rect = (event.currentTarget as HTMLElement)
                        .getBoundingClientRect();
                    setPlace({
                        right: Math.max(0, window.innerWidth - rect.right),
                        bottom: window.innerHeight - rect.top + 2,
                    });
                    setOpen(!open);
                }}
            >
                {props.dropdown && (
                    <span class="dropdownChevron codicon codicon-chevron-down" />
                )}
            </button>
            {open && (
                <div
                    class="toolbarMenu"
                    role="menu"
                    style={{
                        right: `${place.right}px`,
                        bottom: `${place.bottom}px`,
                    }}
                >
                    {props.items.map((item) => {
                        if (item.caption === undefined) {
                            return (
                                <div
                                    key={item.id}
                                    class="toolbarMenuSeparator"
                                    role="separator"
                                />
                            );
                        }

                        return (
                            <button
                                key={item.id}
                                type="button"
                                role={item.checked === undefined
                                    ? "menuitem"
                                    : "menuitemcheckbox"}
                                aria-checked={item.checked}
                                class={item.id === props.selected
                                    ? "toolbarMenuItem selected"
                                    : "toolbarMenuItem"}
                                disabled={item.disabled}
                                onClick={() => {
                                    setOpen(false);
                                    props.onSelect(item.id);
                                }}
                            >
                                <span class={item.checked === undefined
                                    ? `toolbarMenuIcon ${item.icon ?? "noIcon"}`
                                    : `toolbarMenuCheck codicon ${item.checked
                                        ? "codicon-check"
                                        : ""}`} />
                                {item.caption}
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
};
