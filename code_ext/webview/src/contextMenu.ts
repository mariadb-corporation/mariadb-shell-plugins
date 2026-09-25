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
 * The menu a right-click on a cell of the actions grid opens: one item,
 * Copy, for that cell's text.
 *
 * It stands in for the menu VS Code puts up on any webview, whose Cut,
 * Copy and Paste act on the text SELECTION - and clicking a grid cell
 * selects nothing, so its Copy copied nothing. Calling `preventDefault()`
 * on the `contextmenu` event is what keeps VS Code's own menu away: the
 * webview only shows it for an event nobody has handled.
 *
 * One menu is shared by the whole page, like the overflow popup; opening
 * it again moves it.
 */

/** How far the menu stays clear of the window's edges. */
const VIEWPORT_MARGIN = 4;

/** What the menu copies, and how. */
export interface ICopyMenuContent {
    /** The cell's text, as it is shown. */
    text: string;
    /** Puts it on the clipboard. */
    onCopy(text: string): void;
}

let menu: HTMLElement | undefined;
let stopListening: (() => void) | undefined;

/**
 * Where the menu goes: at the pointer, moved in from an edge it would run
 * past, so it never opens half off screen at the panel's bottom or right.
 *
 * @param pointer Where the right-click happened, in viewport pixels.
 * @param size The menu's size.
 * @param viewport The window's size.
 *
 * @returns The menu's top-left corner.
 */
export const menuPosition = (
    pointer: { x: number; y: number },
    size: { width: number; height: number },
    viewport: { width: number; height: number },
): { left: number; top: number } => {
    return {
        left: Math.max(VIEWPORT_MARGIN, Math.min(pointer.x,
            viewport.width - size.width - VIEWPORT_MARGIN)),
        top: Math.max(VIEWPORT_MARGIN, Math.min(pointer.y,
            viewport.height - size.height - VIEWPORT_MARGIN)),
    };
};

/**
 * Closes the menu, if it is open.
 *
 * @returns Nothing.
 */
export const closeCopyMenu = (): void => {
    stopListening?.();
    stopListening = undefined;
    menu?.remove();
    menu = undefined;
};

/** One entry of a cell's menu; a separator when it has no label. */
export interface IContextMenuItem {
    label?: string;
    disabled?: boolean;
    onClick?(): void;
}

/**
 * Opens the menu for one cell.
 *
 * @param event The right-click. Its default is prevented here, which is
 *              what keeps VS Code's Cut / Copy / Paste menu away.
 * @param content What to copy.
 *
 * @returns The menu element.
 */
export const openCopyMenu = (
    event: MouseEvent,
    content: ICopyMenuContent,
): HTMLElement => {
    return openContextMenu(event, [{
        label: "Copy",
        disabled: content.text === "",
        onClick: () => { content.onCopy(content.text); },
    }]);
};

/**
 * Opens a menu of the given items at the pointer. Only one is open at a
 * time, whichever grid opened it, and it closes on a pick as it does on
 * anything else happening.
 *
 * @param event The right-click. Its default is prevented here, which is
 *              what keeps VS Code's Cut / Copy / Paste menu away.
 * @param items What it offers, in order.
 *
 * @returns The menu element.
 */
export const openContextMenu = (
    event: MouseEvent,
    items: IContextMenuItem[],
): HTMLElement => {
    event.preventDefault();
    closeCopyMenu();

    const element = document.createElement("div");
    element.className = "copyMenu";
    element.setAttribute("role", "menu");

    let first: HTMLButtonElement | undefined;
    for (const entry of items) {
        if (entry.label === undefined) {
            const separator = document.createElement("div");
            separator.className = "copyMenuSeparator";
            separator.setAttribute("role", "separator");
            element.append(separator);
            continue;
        }

        const item = document.createElement("button");
        item.type = "button";
        item.className = "copyMenuItem";
        item.setAttribute("role", "menuitem");
        item.textContent = entry.label;
        item.disabled = entry.disabled ?? false;
        item.addEventListener("click", () => {
            closeCopyMenu();
            entry.onClick?.();
        });
        element.append(item);
        if (!first && !item.disabled) {
            first = item;
        }
    }
    const item = first ?? element;

    document.body.append(element);
    const position = menuPosition(
        { x: event.clientX, y: event.clientY },
        { width: element.offsetWidth, height: element.offsetHeight },
        { width: window.innerWidth, height: window.innerHeight },
    );
    element.style.left = `${position.left}px`;
    element.style.top = `${position.top}px`;
    menu = element;
    item.focus();

    // Whatever else happens next closes it: a click elsewhere, a key, the
    // grid scrolling out from under it, the window going away.
    const onPointer = (pointer: Event): void => {
        if (!element.contains(pointer.target as Node)) {
            closeCopyMenu();
        }
    };
    const onKey = (key: KeyboardEvent): void => {
        if (key.key === "Escape") {
            key.preventDefault();
            closeCopyMenu();
        }
    };
    document.addEventListener("mousedown", onPointer, true);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("scroll", closeCopyMenu, true);
    window.addEventListener("resize", closeCopyMenu);
    window.addEventListener("blur", closeCopyMenu);
    stopListening = () => {
        document.removeEventListener("mousedown", onPointer, true);
        document.removeEventListener("keydown", onKey, true);
        document.removeEventListener("scroll", closeCopyMenu, true);
        window.removeEventListener("resize", closeCopyMenu);
        window.removeEventListener("blur", closeCopyMenu);
    };

    return element;
};
