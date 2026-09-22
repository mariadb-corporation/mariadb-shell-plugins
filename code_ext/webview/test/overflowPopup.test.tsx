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

import { afterEach, describe, expect, it, vi } from "vitest";

import {
    attachOverflowPopup,
    buildOverflowPopup,
    closeOverflowPopup,
    isTruncated,
    popupPosition,
} from "../src/overflowPopup.js";

afterEach(() => {
    closeOverflowPopup();
    vi.useRealTimers();
});

/**
 * Says what an element measures, jsdom laying nothing out.
 *
 * @param element The element to measure.
 * @param sizes What it should report.
 *
 * @returns The same element.
 */
const measure = (
    element: HTMLElement,
    sizes: { scrollWidth: number; clientWidth: number },
): HTMLElement => {
    for (const [name, value] of Object.entries(sizes)) {
        Object.defineProperty(element, name, {
            configurable: true,
            value,
        });
    }

    return element;
};

describe("isTruncated", () => {
    it("says when the text runs past its box", () => {
        expect(isTruncated({ scrollWidth: 300, clientWidth: 120 })).toBe(true);
        expect(isTruncated({ scrollWidth: 120, clientWidth: 120 })).toBe(false);
    });

    it("ignores a fraction of a pixel", () => {
        // A fractional layout leaves the scrolled width a hair over the
        // visible one on text that fits perfectly well, which is not
        // something to open a popup for.
        expect(isTruncated({ scrollWidth: 120.5, clientWidth: 120 }))
            .toBe(false);
    });
});

describe("popupPosition", () => {
    const viewport = { width: 800, height: 600 };

    it("puts the popup under the cell", () => {
        expect(popupPosition(
            { top: 100, bottom: 118, left: 40 },
            { width: 300, height: 60 },
            viewport,
        )).toEqual({ top: 122, left: 40 });
    });

    it("puts it above where it would fall off the bottom", () => {
        expect(popupPosition(
            { top: 560, bottom: 578, left: 40 },
            { width: 300, height: 60 },
            viewport,
        )).toEqual({ top: 496, left: 40 });
    });

    it("keeps it clear of both edges", () => {
        // A popup opening half off screen is one whose copy button
        // cannot be reached.
        expect(popupPosition(
            { top: 100, bottom: 118, left: 700 },
            { width: 300, height: 60 },
            viewport,
        ).left).toBe(492);
        expect(popupPosition(
            { top: 100, bottom: 118, left: -20 },
            { width: 300, height: 60 },
            viewport,
        ).left).toBe(8);
    });
});

describe("buildOverflowPopup", () => {
    it("shows the whole text and offers to copy it", () => {
        const copied: string[] = [];
        const popup = buildOverflowPopup({
            text: "MySQL Error (1064): You have an error in your SQL syntax",
            onCopy: (text) => {
                copied.push(text);
            },
        });

        expect(popup.querySelector(".overflowPopupText")?.textContent)
            .toBe("MySQL Error (1064): You have an error in your SQL syntax");

        const copy = popup.querySelector("button");
        expect(copy?.title).toBe("Copy the full text");
        copy?.click();

        expect(copied).toEqual([
            "MySQL Error (1064): You have an error in your SQL syntax",
        ]);
    });

    it("says on the button that it has copied, then goes back", () => {
        vi.useFakeTimers();
        const popup = buildOverflowPopup({
            text: "SELECT 1",
            onCopy: () => { /* nothing to record */ },
        });
        const copy = popup.querySelector("button");

        copy?.click();

        // On the button rather than in a toast: the pointer is already
        // here, and a notification for a copy is noise.
        expect(copy?.className).toContain("codicon-check");
        expect(copy?.className).not.toContain("codicon-copy");

        vi.advanceTimersByTime(2000);

        expect(copy?.className).toContain("codicon-copy");
        expect(copy?.className).not.toContain("codicon-check");
    });
});

describe("attachOverflowPopup", () => {
    /**
     * @param sizes What the hovered element should measure.
     *
     * @returns The element, in the document and wired up.
     */
    const attached = (
        sizes: { scrollWidth: number; clientWidth: number },
    ): HTMLElement => {
        const element = measure(document.createElement("div"), sizes);
        document.body.appendChild(element);
        attachOverflowPopup(element, {
            text: "a statement far too long for its column",
            onCopy: () => { /* nothing to record */ },
        });

        return element;
    };

    it("opens on a cell whose text is cut off", () => {
        attached({ scrollWidth: 400, clientWidth: 120 })
            .dispatchEvent(new Event("mouseenter"));

        expect(document.querySelector(".overflowPopup")?.textContent)
            .toContain("a statement far too long for its column");
    });

    it("stays shut on a cell that fits", () => {
        // Whether it fits is asked on each hover, not once when the cell
        // is built: the columns are resizable and the panel is not.
        attached({ scrollWidth: 120, clientWidth: 120 })
            .dispatchEvent(new Event("mouseenter"));

        expect(document.querySelector(".overflowPopup")).toBeNull();
    });

    it("stays open while the pointer crosses to it", () => {
        vi.useFakeTimers();
        const element = attached({ scrollWidth: 400, clientWidth: 120 });
        element.dispatchEvent(new Event("mouseenter"));
        const popup = document.querySelector(".overflowPopup");

        // Leaving the cell starts a close, and reaching the popup before
        // it fires cancels it - without which the copy button could
        // never be clicked.
        element.dispatchEvent(new Event("mouseleave"));
        popup?.dispatchEvent(new Event("mouseenter"));
        vi.advanceTimersByTime(1000);

        expect(document.querySelector(".overflowPopup")).not.toBeNull();

        popup?.dispatchEvent(new Event("mouseleave"));

        expect(document.querySelector(".overflowPopup")).toBeNull();
    });

    it("closes when the pointer goes nowhere near it", () => {
        vi.useFakeTimers();
        const element = attached({ scrollWidth: 400, clientWidth: 120 });
        element.dispatchEvent(new Event("mouseenter"));

        element.dispatchEvent(new Event("mouseleave"));
        vi.advanceTimersByTime(1000);

        expect(document.querySelector(".overflowPopup")).toBeNull();
    });

    it("closes when the grid under it scrolls", () => {
        const element = attached({ scrollWidth: 400, clientWidth: 120 });
        element.dispatchEvent(new Event("mouseenter"));

        // It is placed against the window, so a scroll of the grid
        // leaves it pointing at nothing.
        element.dispatchEvent(new Event("scroll", { bubbles: true }));

        expect(document.querySelector(".overflowPopup")).toBeNull();
    });

    it("leaves an empty cell alone", () => {
        const element = measure(document.createElement("div"), {
            scrollWidth: 400,
            clientWidth: 120,
        });
        document.body.appendChild(element);
        attachOverflowPopup(element, {
            text: "",
            onCopy: () => { /* nothing to record */ },
        });

        element.dispatchEvent(new Event("mouseenter"));

        expect(document.querySelector(".overflowPopup")).toBeNull();
    });
});
