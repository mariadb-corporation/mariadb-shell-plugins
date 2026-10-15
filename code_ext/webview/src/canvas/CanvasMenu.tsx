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
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";

import { dismissOnOutside } from "../dismiss.js";
import { clampToViewport } from "../position.js";
import type { ICanvasOverlay, IMenuEntry } from "./canvasTypes.js";

/**
 * The canvas's menus and the hamburger buttons that open them: an item's
 * menu, opened at a point of the canvas host - under its button, or where
 * the item was right-clicked - and kept inside the host; and the list of
 * entries every menu of the canvas draws, a split button's too.
 */

/** The gap between a button and the menu it opens, in CSS pixels. */
const MENU_GAP = 2;

/** How far a menu stays clear of the host's edges, in CSS pixels. */
const MENU_MARGIN = 4;

/**
 * Where a menu opened by a button goes: under the button as it lies on the
 * screen (transformed, where it is), relative to a host.
 *
 * @param button The button.
 * @param host What the menu is placed in.
 *
 * @returns The menu's top left corner, relative to the host.
 */
export const pointUnder = (
    button: Element,
    host: Element | null | undefined,
): { x: number; y: number } => {
    const rect = button.getBoundingClientRect();
    const origin = host?.getBoundingClientRect();

    return {
        x: rect.left - (origin?.left ?? 0),
        y: rect.bottom - (origin?.top ?? 0) + MENU_GAP,
    };
};

export interface IMenuItemsProps {
    items: readonly IMenuEntry[];
    /** Called with an entry's id when it is picked, after `onClose`. */
    onPick: (id: string) => void;
    onClose: () => void;
    /**
     * The role of a checkable entry: a radio where one of the entries is
     * the choice, a checkbox where each is switched on its own.
     */
    checkRole?: "menuitemradio" | "menuitemcheckbox";
}

/**
 * The entries of a menu: a button each, a check where it is switched on, a
 * line for an entry without a caption.
 *
 * @param props The entries and what a pick does.
 *
 * @returns The entries.
 */
export const MenuItems = (props: IMenuItemsProps): JSX.Element => {
    return <>
        {props.items.map((item, index) => {
            if (item.caption === undefined) {
                return <div key={`separator${index}`}
                    className="splitMenuSeparator" role="separator" />;
            }
            const checkable = item.checked !== undefined;

            return <button
                key={item.id}
                type="button"
                role={checkable ? props.checkRole ?? "menuitemcheckbox"
                    : "menuitem"}
                aria-checked={checkable ? item.checked : undefined}
                className={"splitMenuItem"
                    + (item.checked === true ? " checked" : "")}
                disabled={item.disabled}
                onClick={() => {
                    props.onClose();
                    props.onPick(item.id);
                }}
            >
                {item.caption}
            </button>;
        })}
    </>;
};

export interface ICanvasMenuProps {
    items: IMenuEntry[];
    /** Where the menu's top left corner goes, relative to the host. */
    x: number;
    y: number;
    onCommand: (command: string) => void;
    onClose: () => void;
}

export const CanvasMenu = (props: ICanvasMenuProps): JSX.Element => {
    const menu = useRef<HTMLDivElement>(null);
    const [place, setPlace] = useState({ left: props.x, top: props.y });

    useEffect(() => {
        return dismissOnOutside(menu, props.onClose,
            { scroll: true, resize: true });
    }, [props.onClose]);

    // Kept inside the host: moved left or up by what would overflow.
    useLayoutEffect(() => {
        const element = menu.current;
        const host = element?.offsetParent as HTMLElement | null;
        if (element === null || host === null || host === undefined) {
            return;
        }
        setPlace(clampToViewport({ left: props.x, top: props.y },
            { width: element.offsetWidth, height: element.offsetHeight },
            { width: host.clientWidth, height: host.clientHeight },
            MENU_MARGIN));
    }, [props.x, props.y]);

    return <div
        ref={menu}
        className="splitMenu"
        role="menu"
        style={{ left: `${place.left}px`, top: `${place.top}px` }}
    >
        <MenuItems items={props.items} onPick={props.onCommand}
            onClose={props.onClose} />
    </div>;
};

/** The side of the Navigator's and the toolbar's menu buttons, in CSS pixels. */
export const MENU_BUTTON_SIZE = 20;

/** The three bars of a menu button, in CSS pixels. */
export interface IMenuBars {
    /** Each bar's thickness. */
    bar: number;
    /** From one bar's top to the next one's. */
    gap: number;
    width: number;
    /** The middle bar's top left corner. */
    left: number;
    top: number;
}

/**
 * The bars of a menu button of a size: 8% of it thick, 16% apart, 56%
 * wide, centred - each rounded to whole device pixels, never thinner than
 * one, with at least one clear between the bars. (Whole CSS pixels made a
 * small button's bars too thin and too far apart on a Retina screen.)
 *
 * @param size The button's side, in CSS pixels.
 * @param ratio Device pixels per CSS pixel.
 *
 * @returns The bars.
 */
export const menuBars = (size: number, ratio: number): IMenuBars => {
    const device = Math.round(size * ratio);
    const bar = Math.max(1, Math.round(device * 0.08));
    const gap = Math.max(bar + 1, Math.round(device * 0.16));
    const width = Math.round(device * 0.56);

    return {
        bar: bar / ratio,
        gap: gap / ratio,
        width: width / ratio,
        left: Math.floor((device - width) / 2) / ratio,
        top: Math.floor((device - bar) / 2) / ratio,
    };
};

/**
 * The CSS variables a hamburger button's `::before` draws its bars from
 * (see `menuBars`).
 *
 * @param size The button's side, in CSS pixels.
 *
 * @returns The style properties.
 */
export const menuBarStyle = (size: number): Record<string, string> => {
    const bars = menuBars(size, globalThis.devicePixelRatio || 1);

    return {
        "--bar": `${bars.bar}px`,
        "--bar-gap": `${bars.gap}px`,
        "--bar-width": `${bars.width}px`,
        "--bar-left": `${bars.left}px`,
        "--bar-top": `${bars.top}px`,
    };
};

export interface IHamburgerButtonProps {
    /** Its side, in CSS pixels. */
    size: number;
    /** What it is called, for its tooltip and accessible name. */
    label: string;
    open: boolean;
    /** Classes beside `menuButton`, which place and colour it. */
    className?: string;
    /** More of its style: where an item's button goes, and its colour. */
    style?: Record<string, string>;
    /** Opens the menu; the button is passed to place the menu by. */
    onOpen: (button: HTMLElement) => void;
}

/**
 * A hamburger that opens a menu: the same bars wherever it is - on an
 * item, in the Navigator's title, at the toolbar's end.
 *
 * @param props The button.
 *
 * @returns The button.
 */
export const HamburgerButton = (props: IHamburgerButtonProps): JSX.Element => {
    return <button
        type="button"
        className={"menuButton"
            + (props.className === undefined ? "" : ` ${props.className}`)
            + (props.open ? " open" : "")}
        title={props.label}
        aria-label={props.label}
        aria-haspopup="menu"
        aria-expanded={props.open}
        style={{
            width: `${props.size}px`,
            height: `${props.size}px`,
            ...props.style,
            ...menuBarStyle(props.size),
        }}
        onClick={(event) => { props.onOpen(event.currentTarget); }}
    />;
};

export interface IMenuButtonProps {
    overlay: ICanvasOverlay;
    open: boolean;
    /** Opens the menu at a point of the canvas host: under the button. */
    onOpen: (x: number, y: number) => void;
}

/**
 * The hamburger an item carries while selected or under the pointer,
 * laid onto the item by the canvas's transform, so it tilts with it in
 * the 3D view as the inline input does.
 *
 * @param props The button.
 *
 * @returns The button.
 */
export const MenuButton = (props: IMenuButtonProps): JSX.Element => {
    const { overlay } = props;

    return <HamburgerButton
        size={overlay.size}
        label="Menu"
        open={props.open}
        className="itemMenuButton"
        style={{ transform: overlay.transform, color: overlay.color }}
        onOpen={(button) => {
            const at = pointUnder(button, button.offsetParent);
            props.onOpen(at.x, at.y);
        }}
    />;
};
