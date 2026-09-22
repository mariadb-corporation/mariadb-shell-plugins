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
 * The hover popup that shows a cut-off cell in full, with a button that
 * copies it.
 *
 * The panel area is short and its columns are narrow, so a message or a
 * statement is very often wider than the room it has. An ellipsis says
 * that something is missing without saying what, and a `title` attribute
 * cannot be selected, cannot be copied and disappears the moment the
 * pointer moves - so this stands in for one.
 *
 * One popup is shared by the whole page: only one cell is ever hovered,
 * and a popup per cell would be thousands of elements for the log alone.
 */

/**
 * How far the text has to run past its box before it counts as cut off.
 *
 * A pixel of slack, as elsewhere: a fractional layout leaves the scrolled
 * width a hair over the visible one on text that fits perfectly well.
 */
const OVERFLOW_SLACK = 1;

/**
 * How long the pointer may be outside both the cell and the popup before
 * it closes. It has to cross the gap between the two, and a popup that
 * shut on the way would be one that could never be clicked.
 */
const CLOSE_DELAY_MS = 150;

/** How long the copy button says it has copied before going back. */
const COPIED_FOR_MS = 1200;

/** The gap between the cell and the popup pointing at it. */
const ANCHOR_GAP = 4;

/** How far the popup stays clear of the window's edges. */
const VIEWPORT_MARGIN = 8;

/**
 * Whether an element's text is wider than the room it has.
 *
 * @param element What the element measures, as the DOM reports it.
 *
 * @returns True when some of the text is not on screen.
 */
export const isTruncated = (element: {
    scrollWidth: number;
    clientWidth: number;
}): boolean => {
    return element.scrollWidth - element.clientWidth > OVERFLOW_SLACK;
};

/**
 * Where a popup of this size goes, given the cell it belongs to.
 *
 * Below the cell by preference, above it where that would run off the
 * bottom, and always clear of the left and right edges - a popup that
 * opens half off screen is a popup whose copy button cannot be reached.
 *
 * @param anchor What the cell measures.
 * @param popup What the popup measures.
 * @param viewport The window's inner size.
 *
 * @returns The top left corner for it, in viewport coordinates.
 */
export const popupPosition = (
    anchor: { top: number; bottom: number; left: number },
    popup: { width: number; height: number },
    viewport: { width: number; height: number },
): { top: number; left: number } => {
    const below = anchor.bottom + ANCHOR_GAP;
    const fitsBelow = below + popup.height
        <= viewport.height - VIEWPORT_MARGIN;

    return {
        top: fitsBelow
            ? below
            : Math.max(VIEWPORT_MARGIN, anchor.top - ANCHOR_GAP - popup.height),
        left: Math.max(
            VIEWPORT_MARGIN,
            Math.min(
                anchor.left,
                viewport.width - VIEWPORT_MARGIN - popup.width,
            ),
        ),
    };
};

/** What one hovered cell offers. */
export interface IOverflowContent {
    /** The whole of the text, as the popup shows and copies it. */
    text: string;
    /** Puts it on the clipboard. */
    onCopy: (text: string) => void;
}

/**
 * Builds the popup's contents: the text, and the button that copies it.
 *
 * @param content What the hovered cell holds.
 *
 * @returns The popup element, not yet placed or shown.
 */
export const buildOverflowPopup = (
    content: IOverflowContent,
): HTMLElement => {
    const popup = document.createElement("div");
    popup.className = "overflowPopup";
    // It is a hint about the cell the pointer is on, not a dialog: it
    // takes no focus and is announced with the cell.
    popup.setAttribute("role", "tooltip");

    const text = document.createElement("div");
    text.className = "overflowPopupText";
    text.textContent = content.text;
    popup.appendChild(text);

    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "overflowPopupCopy codicon codicon-copy";
    copy.title = "Copy the full text";
    copy.setAttribute("aria-label", "Copy the full text");
    let revert: ReturnType<typeof setTimeout> | undefined;
    copy.addEventListener("click", () => {
        content.onCopy(content.text);
        // Said on the button itself rather than in a notification: the
        // pointer is already here, and a toast for a copy is noise.
        copy.classList.remove("codicon-copy");
        copy.classList.add("codicon-check", "copied");
        clearTimeout(revert);
        revert = setTimeout(() => {
            copy.classList.remove("codicon-check", "copied");
            copy.classList.add("codicon-copy");
        }, COPIED_FOR_MS);
    });
    popup.appendChild(copy);

    return popup;
};

/** The popup now open, if any. */
let open: HTMLElement | undefined;
/** The close that is pending while the pointer crosses the gap. */
let closing: ReturnType<typeof setTimeout> | undefined;

/**
 * Closes the popup, if one is open.
 *
 * @returns Nothing.
 */
export const closeOverflowPopup = (): void => {
    clearTimeout(closing);
    closing = undefined;
    open?.remove();
    open = undefined;
    window.removeEventListener("scroll", closeOverflowPopup, true);
};

/**
 * Opens the popup over a cell, replacing whatever was open before.
 *
 * @param anchor The element the popup belongs to.
 * @param content What it shows and copies.
 *
 * @returns Nothing.
 */
const openOverflowPopup = (
    anchor: HTMLElement,
    content: IOverflowContent,
): void => {
    closeOverflowPopup();

    const popup = buildOverflowPopup(content);
    // Appended before it is placed, because where it goes depends on how
    // big it turns out to be, which nothing can say until it is in the
    // document.
    document.body.appendChild(popup);
    const at = popupPosition(
        anchor.getBoundingClientRect(),
        popup.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight },
    );
    popup.style.top = `${at.top}px`;
    popup.style.left = `${at.left}px`;

    // The pointer has to cross the gap between the cell and the popup to
    // reach the copy button, so the popup keeps itself open.
    popup.addEventListener("mouseenter", () => {
        clearTimeout(closing);
        closing = undefined;
    });
    popup.addEventListener("mouseleave", () => {
        closeOverflowPopup();
    });

    open = popup;
    // The popup is placed against the window, and the grid scrolls
    // inside it, so a scroll leaves it pointing at nothing. Captured,
    // because what scrolls is the grid rather than the window.
    window.addEventListener("scroll", closeOverflowPopup, true);
};

/**
 * Gives a cell's text a popup, for whenever it does not fit.
 *
 * Whether it fits is asked on each hover rather than once here: the
 * columns are resizable and the panel is not, so the same text is cut
 * off at one width and whole at another.
 *
 * @param element The element holding the text, which is what is
 *                measured and what the popup points at.
 * @param content What the popup would show and copy.
 *
 * @returns Nothing.
 */
export const attachOverflowPopup = (
    element: HTMLElement,
    content: IOverflowContent,
): void => {
    if (content.text === "") {
        return;
    }

    element.addEventListener("mouseenter", () => {
        if (isTruncated(element)) {
            openOverflowPopup(element, content);
        }
    });
    element.addEventListener("mouseleave", () => {
        clearTimeout(closing);
        closing = setTimeout(closeOverflowPopup, CLOSE_DELAY_MS);
    });
};
