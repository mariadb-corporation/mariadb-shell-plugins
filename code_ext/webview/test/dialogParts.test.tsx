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

import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TOOLTIP_DELAY_MS, Tooltips } from "../src/dialogParts.js";

let host: HTMLDivElement;

beforeEach(async () => {
    vi.useFakeTimers();
    host = document.createElement("div");
    document.body.append(host);
    await act(async () => {
        render(
            <div>
                <Tooltips />
                <button type="button" data-tooltip="Copy the URI">
                    Copy
                </button>
                <button type="button" data-tooltip="Paste a URI">
                    Paste
                </button>
                <span class="plain">nothing to say</span>
            </div>,
            host,
        );
        await Promise.resolve();
    });
});

afterEach(() => {
    render(null, host);
    host.remove();
    vi.useRealTimers();
});

/**
 * @param text The button's text.
 *
 * @returns The button.
 */
const button = (text: string): HTMLButtonElement => {
    return [...host.querySelectorAll("button")].find((node) => {
        return node.textContent?.trim() === text;
    })!;
};

/** @returns The tooltip's text, or undefined when none is shown. */
const tooltip = (): string | undefined => {
    return host.querySelector(".tooltip")?.textContent ?? undefined;
};

/**
 * Fires an event and lets the page catch up.
 *
 * @param target What it happens to.
 * @param event The event.
 * @param wait How long to let pass after it.
 *
 * @returns Nothing.
 */
const fire = async (
    target: EventTarget,
    event: Event,
    wait = 0,
): Promise<void> => {
    await act(async () => {
        target.dispatchEvent(event);
        vi.advanceTimersByTime(wait);
        await Promise.resolve();
    });
};

/** @returns A pointer moving onto something. */
const over = (): MouseEvent => {
    return new MouseEvent("mouseover", { bubbles: true });
};

describe("Tooltips", () => {
    it("shows an element's tooltip once the pointer rests on it",
        async () => {
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS - 1);
            expect(tooltip()).toBeUndefined();

            await fire(document, new Event("noop"), 1);
            expect(tooltip()).toBe("Copy the URI");
            expect(host.querySelector(".tooltip")?.getAttribute("role"))
                .toBe("tooltip");
        });

    it("follows the pointer from one element to the next", async () => {
        await fire(button("Copy"), over(), TOOLTIP_DELAY_MS);
        await fire(button("Paste"), over(), TOOLTIP_DELAY_MS);

        expect(tooltip()).toBe("Paste a URI");
    });

    it("goes when the pointer moves onto something without one",
        async () => {
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS);
            await fire(host.querySelector(".plain")!, over(), TOOLTIP_DELAY_MS);

            expect(tooltip()).toBeUndefined();
        });

    it("never shows for a pointer that moved on before the delay",
        async () => {
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS / 2);
            await fire(host.querySelector(".plain")!, over(), TOOLTIP_DELAY_MS);

            expect(tooltip()).toBeUndefined();
        });

    it("goes on a click, and stays gone while the pointer stays",
        async () => {
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS);
            await fire(button("Copy"),
                new MouseEvent("mousedown", { bubbles: true }));
            expect(tooltip()).toBeUndefined();

            // The pointer moving within the button is still the button.
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS);
            expect(tooltip()).toBeUndefined();
        });

    it("goes when the pointer leaves the window, or anything scrolls",
        async () => {
            await fire(button("Copy"), over(), TOOLTIP_DELAY_MS);
            await fire(button("Copy"), new MouseEvent("mouseout",
                { bubbles: true, relatedTarget: null }));
            expect(tooltip()).toBeUndefined();

            await fire(button("Paste"), over(), TOOLTIP_DELAY_MS);
            await fire(host, new Event("scroll"));
            expect(tooltip()).toBeUndefined();
        });
});
