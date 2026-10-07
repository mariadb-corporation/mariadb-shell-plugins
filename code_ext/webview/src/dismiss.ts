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
 * Closing a menu or popup the ways any menu closes: a click anywhere
 * outside it, Escape, and the window losing focus.
 */

/** What else, beyond the three every menu listens for, closes it. */
export interface IDismissOptions {
    /** Anything scrolling, which would leave it pointing at nothing. */
    scroll?: boolean;
    /** The window resizing, which moves what it was placed against. */
    resize?: boolean;
}

/**
 * Closes an element on a mousedown outside it, on Escape, and on the window
 * losing focus. The pointer and key are heard in the capture phase, so a
 * target that swallows the event cannot keep the element open; Escape's
 * default is prevented, so nothing else acts on it.
 *
 * @param element The element, or a ref to it. A ref holding nothing counts
 *                as outside, so a click then closes it.
 * @param close Closes the element.
 * @param options What else closes it.
 *
 * @returns Stops listening.
 */
export const dismissOnOutside = (
    element: HTMLElement | { current: HTMLElement | null },
    close: () => void,
    options: IDismissOptions = {},
): () => void => {
    const onPointer = (event: Event): void => {
        const target = "current" in element ? element.current : element;
        if (!target?.contains(event.target as Node)) {
            close();
        }
    };
    const onKey = (event: KeyboardEvent): void => {
        if (event.key === "Escape") {
            event.preventDefault();
            close();
        }
    };
    document.addEventListener("mousedown", onPointer, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", close);
    if (options.scroll) {
        document.addEventListener("scroll", close, true);
    }
    if (options.resize) {
        window.addEventListener("resize", close);
    }

    return () => {
        document.removeEventListener("mousedown", onPointer, true);
        document.removeEventListener("keydown", onKey, true);
        window.removeEventListener("blur", close);
        document.removeEventListener("scroll", close, true);
        window.removeEventListener("resize", close);
    };
};
