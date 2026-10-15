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

import { ISO_PRESETS } from "../../src/canvas/geometry.js";
import { SplitButton } from "../../src/canvas/SplitButton.js";

describe("SplitButton", () => {
    let host: HTMLDivElement;
    let clicks: number;
    let picked: string[];

    const show = (selected = "upright"): void => {
        act(() => {
            render(<SplitButton
                caption="Isometric"
                title="Isometric"
                toggled={false}
                onClick={() => { clicks += 1; }}
                menuLabel="Choose the 3D View"
                items={ISO_PRESETS}
                selected={selected}
                onSelect={(id) => { picked.push(id); }}
            />, host);
        });
    };

    const chevron = (): HTMLButtonElement => {
        return host.querySelector<HTMLButtonElement>(".splitChevron")!;
    };

    const menu = (): HTMLElement | null => {
        return host.querySelector<HTMLElement>(".splitMenu");
    };

    beforeEach(() => {
        host = document.createElement("div");
        document.body.appendChild(host);
        clicks = 0;
        picked = [];
    });

    afterEach(() => {
        render(null, host);
        host.remove();
    });

    it("does its action from the main part, without the list", () => {
        show();
        act(() => { host.querySelector<HTMLButtonElement>(".splitMain")!.click(); });

        expect(clicks).toBe(1);
        expect(menu()).toBeNull();
    });

    it("opens the list from the chevron only, the choice marked", () => {
        show("steep");
        act(() => { chevron().click(); });

        expect(clicks).toBe(0);
        expect(chevron().getAttribute("aria-expanded")).toBe("true");
        const items = [...menu()!.querySelectorAll<HTMLButtonElement>(
            ".splitMenuItem")];
        expect(items.map((item) => { return item.textContent; }))
            .toEqual(ISO_PRESETS.map((preset) => { return preset.caption; }));
        expect(items.map((item) => { return item.getAttribute("aria-checked"); }))
            .toEqual(["false", "true", "false"]);
    });

    it("picks a choice and closes", () => {
        show();
        act(() => { chevron().click(); });
        act(() => {
            menu()!.querySelectorAll<HTMLButtonElement>(".splitMenuItem")[0]
                .click();
        });

        expect(picked).toEqual(["isometric"]);
        expect(clicks).toBe(0);
        expect(menu()).toBeNull();
    });

    it("closes on Escape and on a click elsewhere", () => {
        show();
        act(() => { chevron().click(); });
        act(() => {
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
        });
        expect(menu()).toBeNull();

        act(() => { chevron().click(); });
        act(() => {
            document.body.dispatchEvent(new MouseEvent("mousedown",
                { bubbles: true }));
        });
        expect(menu()).toBeNull();
        expect(picked).toEqual([]);
    });
});
