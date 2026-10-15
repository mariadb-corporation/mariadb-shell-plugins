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

import { dismissOnOutside } from "../dismiss.js";
import { MenuItems } from "./CanvasMenu.js";

export interface ISplitButtonProps {
    caption: string;
    title: string;
    toggled: boolean;
    onClick: () => void;
    /** What the chevron's menu is called, for its accessible name. */
    menuLabel: string;
    items: ReadonlyArray<{ id: string; caption: string }>;
    selected: string;
    onSelect: (id: string) => void;
}

/**
 * A button with a chevron on its right: the button does what it says with
 * the choice last made, the chevron alone opens the list of choices below
 * it. The list closes on a pick, a click anywhere else, Escape and the
 * window losing focus.
 *
 * @param props The button and its choices.
 *
 * @returns The rendered split button.
 */
export const SplitButton = (props: ISplitButtonProps): JSX.Element => {
    const [open, setOpen] = useState(false);
    const host = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) {
            return undefined;
        }

        return dismissOnOutside(host, () => { setOpen(false); });
    }, [open]);

    return <div className="splitButton" ref={host}>
        <button
            type="button"
            className={"toolbarText splitMain" + (props.toggled ? " toggled" : "")}
            title={props.title}
            aria-pressed={props.toggled}
            onClick={props.onClick}
        >
            {props.caption}
        </button>
        <button
            type="button"
            className={"toolbarText splitChevron" + (open ? " open" : "")}
            aria-label={props.menuLabel}
            title={props.menuLabel}
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => { setOpen(!open); }}
        />
        {open && <div className="splitMenu" role="menu">
            <MenuItems
                items={props.items.map((item) => {
                    return { ...item, checked: item.id === props.selected };
                })}
                checkRole="menuitemradio"
                onPick={props.onSelect}
                onClose={() => { setOpen(false); }}
            />
        </div>}
    </div>;
};
