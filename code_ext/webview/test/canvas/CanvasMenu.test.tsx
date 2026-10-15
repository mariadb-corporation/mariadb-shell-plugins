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
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CanvasMenu, menuBars } from "../../src/canvas/CanvasMenu.js";

describe("CanvasMenu", () => {
    let host: HTMLDivElement;
    let commands: string[];
    let closed: number;

    beforeEach(() => {
        host = document.createElement("div");
        document.body.appendChild(host);
        commands = [];
        closed = 0;
        act(() => {
            render(<CanvasMenu
                items={[
                    { id: "toggleExpanded", caption: "Collapse" },
                    { id: "separator1" },
                    { id: "toggleIndices", caption: "Show Indexes",
                        checked: true },
                    { id: "autoSize", caption: "Auto-Size Height",
                        disabled: true },
                ]}
                x={10}
                y={20}
                onCommand={(command) => { commands.push(command); }}
                onClose={() => { closed += 1; }}
            />, host);
        });
    });

    afterEach(() => {
        render(null, host);
        host.remove();
    });

    const items = (): HTMLButtonElement[] => {
        return [...host.querySelectorAll<HTMLButtonElement>(".splitMenuItem")];
    };

    it("shows the entries, the separators, the checks and what is disabled", () => {
        expect(items().map((item) => { return item.textContent; }))
            .toEqual(["Collapse", "Show Indexes", "Auto-Size Height"]);
        expect(host.querySelectorAll(".splitMenuSeparator")).toHaveLength(1);
        expect(items()[0].getAttribute("role")).toBe("menuitem");
        expect(items()[1].getAttribute("role")).toBe("menuitemcheckbox");
        expect(items()[1].getAttribute("aria-checked")).toBe("true");
        expect(items()[2].disabled).toBe(true);
    });

    it("runs an entry's command and closes", () => {
        act(() => { items()[0].click(); });

        expect(commands).toEqual(["toggleExpanded"]);
        expect(closed).toBe(1);
    });

    it("closes on Escape", () => {
        act(() => {
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
        });

        expect(closed).toBe(1);
        expect(commands).toEqual([]);
    });
});

describe("menuBars", () => {
    it("keeps the bars' proportions at a small size on a Retina screen", () => {
        // 12 CSS px at 2x: 24 device pixels.
        expect(menuBars(12, 2)).toEqual({
            bar: 1, gap: 2, width: 6.5, left: 2.5, top: 5.5,
        });
    });

    it("scales them with the button", () => {
        const small = menuBars(12, 2);
        const large = menuBars(36, 2);
        expect(large.gap / large.bar).toBeCloseTo(small.gap / small.bar, 0);
        expect(large.bar).toBeGreaterThan(small.bar);
    });

    it("keeps a device pixel clear between the bars", () => {
        const bars = menuBars(8, 1);
        expect(bars.gap).toBeGreaterThan(bars.bar);
    });
});
