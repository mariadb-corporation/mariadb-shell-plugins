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

import { posted } from "./setup.js";
import { UtilDialog } from "../src/UtilDialog.js";
import {
    initialUtilValues,
    utilOperationSpec,
    type UtilOperation,
} from "../../src/util/utilFields.js";
import type { UtilHostMessage } from "../../src/util/utilProtocol.js";

let host: HTMLDivElement;

const send = async (message: UtilHostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await Promise.resolve();
    });
};

const open = async (operation: UtilOperation, path = "/home/me/dump",
    targets: string[] = []): Promise<void> => {
    await act(async () => {
        render(<UtilDialog />, host);
        await Promise.resolve();
    });
    const spec = utilOperationSpec(operation, {
        connection: "root@localhost", schemas: ["shop"], tables: ["orders"],
    });
    await send({ type: "load", spec, values: initialUtilValues(spec, path),
        targets });
};

const button = (label: string): HTMLButtonElement => {
    const found = [...host.querySelectorAll("button")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
    if (!found) {
        throw new Error(`No button '${label}'.`);
    }

    return found;
};

const click = async (label: string): Promise<void> => {
    await act(async () => {
        button(label).click();
        await Promise.resolve();
    });
};

const input = (name: string): HTMLInputElement => {
    return host.querySelector(`[name='${name}']`)!;
};

const change = async (name: string, value: string | boolean): Promise<void> => {
    await act(async () => {
        const element = input(name);
        if (typeof value === "boolean") {
            element.checked = value;
            element.dispatchEvent(new Event("change", { bubbles: true }));
        } else {
            element.value = value;
            element.dispatchEvent(new Event(
                element.tagName === "SELECT" ? "change" : "input",
                { bubbles: true }));
        }
        await Promise.resolve();
    });
};

beforeEach(() => {
    posted.length = 0;
    host = document.createElement("div");
    document.body.append(host);
});

afterEach(() => {
    render(null, host);
    host.remove();
});

describe("UtilDialog", () => {
    it("asks for the operation's path and options, and sends them", async () => {
        await open("dumpSchemas");

        expect(posted[0]).toEqual({ type: "ready" });
        expect(host.querySelector("h1")?.textContent).toBe("Dump shop to Disk");
        expect(input("path").value).toBe("/home/me/dump");
        // the advanced options wait on their own tab
        expect(input("maxRate")).toBeNull();

        await change("threads", "8");
        await change("consistent", false);
        await click("Dump");

        expect(posted.at(-1)).toMatchObject({
            type: "start",
            values: { path: "/home/me/dump", threads: "8", consistent: false },
        });
    });

    it("shows the advanced options on their tab", async () => {
        await open("dumpSchemas");
        await click("Advanced");

        expect(input("maxRate")).not.toBeNull();
        expect(input("threads")).toBeNull();
    });

    it("says what is wrong instead of sending it", async () => {
        await open("dumpSchemas", "");
        await click("Dump");

        expect(posted.some((message) => { return message.type === "start"; }))
            .toBe(false);
        expect(input("path").getAttribute("aria-invalid")).toBe("true");
        expect(host.textContent).toContain("Output Folder must be given.");
    });

    it("brings a problem on the other tab into view", async () => {
        await open("loadDump", "/dump");
        await click("Advanced");
        // a number on the Basic tab is wrong
        await click("Basic");
        await change("threads", "many");
        await click("Advanced");
        await click("Load");

        expect(input("threads")).not.toBeNull();
        expect(host.textContent).toContain("Threads must be a whole number.");
    });

    it("asks the extension to browse, and takes the path it picked", async () => {
        await open("loadDump", "");
        await click("Browse...");
        expect(posted.at(-1)).toEqual({ type: "browse", current: "" });

        await send({ type: "browsed", path: "/picked" });
        expect(input("path").value).toBe("/picked");
    });

    it("offers the targets of a copy", async () => {
        await open("copySchemas", "", ["root@a:3306", "root@b:3306"]);
        const options = [...host.querySelectorAll("[name='target'] option")]
            .map((option) => { return option.textContent; });
        expect(options).toEqual(["Choose a connection", "root@a:3306",
            "root@b:3306"]);

        await click("Copy");
        expect(host.textContent).toContain("Choose the connection to copy to.");

        await change("target", "root@b:3306");
        await click("Copy");
        expect(posted.at(-1)).toMatchObject({ type: "start",
            values: { target: "root@b:3306" } });
    });

    it("shows a progress bar and disables the buttons while it starts",
        async () => {
            await open("dumpSchemas");
            await send({ type: "busy", busy: true });

            expect(host.querySelector("[role='progressbar']")).not.toBeNull();
            expect(button("Dump").disabled).toBe(true);
            expect(button("Cancel").disabled).toBe(true);

            await send({ type: "busy", busy: false });
            await send({ type: "startError", message: "Access denied" });
            expect(host.querySelector("[role='progressbar']")).toBeNull();
            expect(host.querySelector(".message.error")?.textContent)
                .toBe("Access denied");
        });

    it("closes with Cancel", async () => {
        await open("exportTable", "/out.tsv");
        await click("Cancel");

        expect(posted.at(-1)).toEqual({ type: "cancel" });
    });
});
