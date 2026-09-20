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
import { beforeEach, describe, expect, it, vi } from "vitest";

import "./setup.js";
import { SqlPreview } from "../src/SqlPreview.js";

let host: HTMLDivElement;

beforeEach(() => {
    document.body.innerHTML = "";
    host = document.createElement("div");
    document.body.append(host);
});

describe("SqlPreview", () => {
    it("says so when there is nothing to preview", () => {
        render(
            <SqlPreview statements={[]} onStatementClick={() => { }} />,
            host,
        );

        expect(host.textContent).toContain("No changes to preview.");
    });

    it("shows each statement with a trailing semicolon", () => {
        render(
            <SqlPreview
                statements={[
                    { rowIndex: 0, sql: "UPDATE `t` SET `a` = 1 WHERE `id` = 1" },
                    { rowIndex: 4, sql: "DELETE FROM `t` WHERE `id` = 5" },
                ]}
                onStatementClick={() => { }}
            />,
            host,
        );

        const codes = [...host.querySelectorAll("code")].map((node) => {
            return node.textContent;
        });
        expect(codes).toEqual([
            "UPDATE `t` SET `a` = 1 WHERE `id` = 1;",
            "DELETE FROM `t` WHERE `id` = 5;",
        ]);
    });

    it("numbers each statement with its one-based grid row", () => {
        render(
            <SqlPreview
                statements={[{ rowIndex: 4, sql: "DELETE FROM `t`" }]}
                onStatementClick={() => { }}
            />,
            host,
        );

        expect(host.querySelector(".previewRow")?.textContent).toBe("5");
    });

    it("takes the user back to the row a statement came from", () => {
        const onStatementClick = vi.fn();
        render(
            <SqlPreview
                statements={[{ rowIndex: 4, sql: "DELETE FROM `t`" }]}
                onStatementClick={onStatementClick}
            />,
            host,
        );

        host.querySelector<HTMLButtonElement>(".previewStatement")?.click();

        expect(onStatementClick).toHaveBeenCalledWith(4);
    });

    it("shows an error under the statement it belongs to", () => {
        render(
            <SqlPreview
                statements={[
                    { rowIndex: 0, sql: "UPDATE `t` SET `a` = 1" },
                    { rowIndex: 1, sql: "DELETE FROM `t` WHERE `id` = 2" },
                ]}
                errors={{ 1: "Cannot delete a parent row" }}
                onStatementClick={() => { }}
            />,
            host,
        );

        const items = host.querySelectorAll(".previewItem");
        expect(items[0].classList.contains("failed")).toBe(false);
        expect(items[1].classList.contains("failed")).toBe(true);
        expect(items[1].querySelector(".previewError")?.textContent)
            .toBe("Cannot delete a parent row");
    });
});
