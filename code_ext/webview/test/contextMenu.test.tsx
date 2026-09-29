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

import "./setup.js";
import {
    closeCopyMenu,
    menuPosition,
    openCopyMenu,
} from "../src/contextMenu.js";

/** A right-click at the given point, as the grid hands it over. */
const rightClick = (x = 10, y = 20): MouseEvent => {
    return new MouseEvent("contextmenu", {
        clientX: x, clientY: y, bubbles: true, cancelable: true,
    });
};

const menu = (): HTMLElement | null => {
    return document.querySelector(".copyMenu");
};

afterEach(() => {
    closeCopyMenu();
});

describe("menuPosition", () => {
    const size = { width: 120, height: 30 };
    const viewport = { width: 800, height: 400 };

    it("opens at the pointer where there is room", () => {
        expect(menuPosition({ x: 100, y: 50 }, size, viewport))
            .toEqual({ left: 100, top: 50 });
    });

    it("moves in from the right and bottom edges", () => {
        expect(menuPosition({ x: 790, y: 395 }, size, viewport))
            .toEqual({ left: 676, top: 366 });
    });

    it("never goes past the top or left edge", () => {
        expect(menuPosition({ x: -5, y: -5 }, size, viewport))
            .toEqual({ left: 4, top: 4 });
    });
});

describe("openCopyMenu", () => {
    it("keeps VS Code's own menu away and offers Copy alone", () => {
        const event = rightClick();

        openCopyMenu(event, { text: "SELECT 1", onCopy: vi.fn() });

        // The webview only shows its Cut / Copy / Paste for an event
        // nobody handled.
        expect(event.defaultPrevented).toBe(true);
        expect([...menu()!.querySelectorAll("[role=menuitem]")]
            .map((item) => { return item.textContent; })).toEqual(["Copy"]);
    });

    it("copies the cell's text and closes", () => {
        const onCopy = vi.fn();
        openCopyMenu(rightClick(), { text: "Kabul", onCopy });

        menu()!.querySelector<HTMLButtonElement>("button")!.click();

        expect(onCopy).toHaveBeenCalledWith("Kabul");
        expect(menu()).toBeNull();
    });

    it("offers nothing to copy from an empty cell", () => {
        openCopyMenu(rightClick(), { text: "", onCopy: vi.fn() });

        expect(menu()!.querySelector<HTMLButtonElement>("button")!.disabled)
            .toBe(true);
    });

    it("closes on Escape, a click elsewhere, a scroll or a resize", () => {
        const events: Array<() => void> = [
            () => {
                document.dispatchEvent(new KeyboardEvent("keydown", {
                    key: "Escape", bubbles: true,
                }));
            },
            () => {
                document.body.dispatchEvent(new MouseEvent("mousedown", {
                    bubbles: true,
                }));
            },
            () => { document.dispatchEvent(new Event("scroll")); },
            () => { window.dispatchEvent(new Event("resize")); },
            () => { window.dispatchEvent(new Event("blur")); },
        ];

        for (const happen of events) {
            openCopyMenu(rightClick(), { text: "x", onCopy: vi.fn() });
            happen();
            expect(menu()).toBeNull();
        }
    });

    it("stays open for a click on itself", () => {
        openCopyMenu(rightClick(), { text: "x", onCopy: vi.fn() });

        menu()!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));

        expect(menu()).not.toBeNull();
    });

    it("is one menu: opening it again replaces it", () => {
        const onCopy = vi.fn();
        openCopyMenu(rightClick(), { text: "first", onCopy });
        openCopyMenu(rightClick(), { text: "second", onCopy });

        expect(document.querySelectorAll(".copyMenu")).toHaveLength(1);
        menu()!.querySelector<HTMLButtonElement>("button")!.click();
        expect(onCopy).toHaveBeenCalledWith("second");
    });

    it("stops listening once closed", () => {
        openCopyMenu(rightClick(), { text: "x", onCopy: vi.fn() });
        closeCopyMenu();
        // A later menu is not closed by what the first one listened for.
        const second = openCopyMenu(rightClick(), { text: "y", onCopy: vi.fn() });
        closeCopyMenu();
        document.dispatchEvent(new Event("scroll"));

        expect(second.isConnected).toBe(false);
    });
});
