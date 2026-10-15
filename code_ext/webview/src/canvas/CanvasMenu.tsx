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
import type { ICanvasOverlay, IMenuEntry } from "./canvasTypes.js";

/**
 * An item's menu, and the button that opens it. The menu opens at a point
 * of the canvas host - under the button, or where the item was
 * right-clicked - and stays inside the host.
 */

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
        setPlace({
            left: Math.max(0, Math.min(props.x,
                host.clientWidth - element.offsetWidth - 4)),
            top: Math.max(0, Math.min(props.y,
                host.clientHeight - element.offsetHeight - 4)),
        });
    }, [props.x, props.y]);

    return <div
        ref={menu}
        className="splitMenu figureMenu"
        role="menu"
        style={{ left: `${place.left}px`, top: `${place.top}px` }}
    >
        {props.items.map((item, index) => {
            if (item.caption === undefined) {
                return <div key={`separator${index}`}
                    className="splitMenuSeparator" role="separator" />;
            }
            const checkable = item.checked !== undefined;

            return <button
                key={item.id}
                type="button"
                role={checkable ? "menuitemcheckbox" : "menuitem"}
                aria-checked={checkable ? item.checked : undefined}
                className={"splitMenuItem"
                    + (item.checked === true ? " checked" : "")}
                disabled={item.disabled}
                onClick={() => {
                    props.onClose();
                    props.onCommand(item.id);
                }}
            >
                {item.caption}
            </button>;
        })}
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
 * one, with at least one clear between the bars.
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
 */
export const MenuButton = (props: IMenuButtonProps): JSX.Element => {
    const { overlay } = props;
    // The bars in whole DEVICE pixels, centred, so that they line up and
    // keep their proportions at every size: a thickness, a gap between
    // them and a width, each a share of the button. (Whole CSS pixels
    // made a small button's bars too thin and too far apart on a Retina
    // screen: never under a CSS pixel, the gap padded.)

    return <button
        type="button"
        className={"figureMenuButton" + (props.open ? " open" : "")}
        title="Menu"
        aria-label="Menu"
        aria-haspopup="menu"
        aria-expanded={props.open}
        style={{
            width: `${overlay.size}px`,
            height: `${overlay.size}px`,
            transform: overlay.transform,
            color: overlay.color,
            ...menuBarStyle(overlay.size),
        }}
        onClick={(event) => {
            // Under the button as it lies on the screen, transformed.
            const button = event.currentTarget;
            const host = button.offsetParent?.getBoundingClientRect();
            const rect = button.getBoundingClientRect();
            props.onOpen(rect.left - (host?.left ?? 0),
                rect.bottom - (host?.top ?? 0) + 2);
        }}
    />;
};
