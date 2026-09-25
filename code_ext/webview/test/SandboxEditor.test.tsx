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
import { SandboxEditor } from "../src/SandboxEditor.js";
import { emptySandboxFields } from "../../src/sandboxes/sandboxFields.js";
import type {
    ISandboxCreateMessage,
    SandboxHostMessage,
} from "../../src/sandboxes/sandboxProtocol.js";

let host: HTMLDivElement;

const mount = async (): Promise<void> => {
    await act(async () => {
        render(<SandboxEditor />, host);
        await Promise.resolve();
    });
};

const send = async (message: SandboxHostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await Promise.resolve();
    });
};

/**
 * Loads the dialog as the host does, for a new sandbox on 3310: the
 * versions newest first, starting on the highest.
 */
const load = async (takenPorts: number[] = []): Promise<void> => {
    await send({
        type: "load",
        fields: emptySandboxFields("3310", "12.3.2"),
        takenPorts,
        versions: ["12.3.2", "11.8.9"],
    });
};

const click = async (label: string): Promise<void> => {
    const button = [...host.querySelectorAll("button")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
    if (!button) {
        throw new Error(`No button '${label}'.`);
    }

    await act(async () => {
        button.click();
        await Promise.resolve();
    });
};

/** The field under the given caption. */
const fieldOf = (caption: string): HTMLLabelElement => {
    const field = [...host.querySelectorAll("label.field")].find((node) => {
        return node.querySelector(".field-caption")?.textContent === caption;
    }) as HTMLLabelElement | undefined;
    if (!field) {
        throw new Error(`No field '${caption}'.`);
    }

    return field;
};

/** Types into an input or textarea. */
const typeInto = async (
    input: HTMLInputElement | HTMLTextAreaElement,
    value: string,
): Promise<void> => {
    await act(async () => {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        await Promise.resolve();
    });
};

const type = async (caption: string, value: string): Promise<void> => {
    await typeInto(fieldOf(caption).querySelector("input")!, value);
};

/** The version box, its list button, and the choices while open. */
const versionBox = (): HTMLInputElement => {
    return fieldOf("Server Version").querySelector("input")!;
};

const versionChoices = (): string[] => {
    return [...host.querySelectorAll(".combo-choice")].map((choice) => {
        return choice.textContent ?? "";
    });
};

const openVersions = async (): Promise<void> => {
    await act(async () => {
        fieldOf("Server Version").querySelector<HTMLButtonElement>(
            "button")!.click();
        await Promise.resolve();
    });
};

/** Picks a choice from the open list, with the pointer. */
const choose = async (choice: string): Promise<void> => {
    const item = [...host.querySelectorAll(".combo-choice")].find((node) => {
        return node.textContent === choice;
    });
    if (!item) {
        throw new Error(`No choice '${choice}'. Have: ${versionChoices()}`);
    }
    await act(async () => {
        item.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        await Promise.resolve();
    });
};

const key = async (name: string): Promise<void> => {
    await act(async () => {
        versionBox().dispatchEvent(new KeyboardEvent("keydown", {
            key: name, bubbles: true,
        }));
        await Promise.resolve();
    });
};

const createButton = (): HTMLButtonElement => {
    return [...host.querySelectorAll("button")].find((node) => {
        return node.textContent?.trim() === "Create";
    })!;
};

const created = (): ISandboxCreateMessage | undefined => {
    return posted.find((message) => {
        return message.type === "create";
    }) as ISandboxCreateMessage | undefined;
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

describe("SandboxEditor", () => {
    it("asks the host for its state as soon as it is up", async () => {
        await mount();

        expect(posted).toEqual([{ type: "ready" }]);
    });

    it("says what it makes under the title", async () => {
        await mount();
        await load();

        expect(host.querySelector(".editor-header .editor-description")
            ?.textContent).toContain("A local MariaDB server");
    });

    it("shows the connection it will register as text, following the port",
        async () => {
            await mount();
            await load();
            const uri = host.querySelector(".sandbox-uri")!;

            // Not a box: nothing about it can be typed into.
            expect(uri.tagName).toBe("CODE");
            expect(host.querySelector(".sandbox-connection input[type=text]"))
                .toBeNull();
            expect(uri.textContent).toBe("mariadb://root@127.0.0.1:3310");

            await type("Port", "3320");
            expect(uri.textContent).toBe("mariadb://root@127.0.0.1:3320");
        });

    it("lines the MCP checkbox up with the fields' right-hand column",
        async () => {
            // jsdom lays nothing out, so what is checked is the structure
            // that does it: the connection line is a grid of the fields' two
            // columns, the caption across both, the URI then the checkbox.
            await mount();
            await load();
            const line = host.querySelector(".sandbox-connection")!;

            expect(line.classList.contains("grid")).toBe(true);
            expect([...line.children].map((child) => {
                return child.className;
            })).toEqual([
                "field-caption sandbox-connection-caption",
                "sandbox-uri",
                "checkbox",
            ]);
        });

    it("offers MCP access beside the connection, on by default", async () => {
        await mount();
        await load();
        const box = host.querySelector<HTMLInputElement>(
            ".sandbox-connection input[type=checkbox]")!;
        expect(box.checked).toBe(true);

        await act(async () => {
            box.checked = false;
            box.dispatchEvent(new Event("change", { bubbles: true }));
            await Promise.resolve();
        });
        await click("Create");

        expect(created()?.fields.mcpAccess).toBe(false);
    });

    it("starts on the highest version, typed into a box", async () => {
        await mount();
        await load();

        expect(versionBox().value).toBe("12.3.2");
        expect(fieldOf("Server Version").querySelector(".field-hint")
            ?.textContent).toContain("downloaded first");
        expect(createButton().disabled).toBe(false);
    });

    it("lists every choice whatever the box holds, the PATH last",
        async () => {
            // What a datalist cannot do: it would offer only what matches
            // the 12.3.2 already in the box.
            await mount();
            await load();

            await openVersions();

            expect(versionChoices()).toEqual([
                "12.3.2", "11.8.9", "Server on the PATH",
            ]);
        });

    it("offers only the server on the PATH when no version was listed",
        async () => {
            await mount();
            await send({
                type: "load",
                fields: emptySandboxFields("3310"),
                takenPorts: [],
                versions: [],
            });
            await openVersions();

            expect(versionBox().value).toBe("Server on the PATH");
            expect(versionChoices()).toEqual(["Server on the PATH"]);
        });

    it("marks a version that is not one at once, and disables Create",
        async () => {
            await mount();
            await load();

            await typeInto(versionBox(), "latest");

            expect(versionBox().className).toBe("invalid");
            expect(fieldOf("Server Version").querySelector(".field-hint")
                ?.textContent).toContain("'latest' is not a server version.");
            expect(createButton().disabled).toBe(true);

            await typeInto(versionBox(), "11.8");
            expect(createButton().disabled).toBe(false);
            expect(versionBox().className).toBe("");
        });

    it("disables Create for an empty version", async () => {
        await mount();
        await load();

        await typeInto(versionBox(), "");

        expect(createButton().disabled).toBe(true);
        expect(createButton().title).toContain("Enter a server version");
    });

    it("picks a choice with the keyboard, and closes on Escape", async () => {
        await mount();
        await load();

        await key("ArrowDown");
        expect(versionChoices()).toHaveLength(3);
        // Opened on the 12.3.2 in the box; two down is the PATH.
        await key("ArrowDown");
        await key("ArrowDown");
        await key("Enter");

        expect(versionBox().value).toBe("Server on the PATH");
        expect(versionChoices()).toEqual([]);

        await key("ArrowDown");
        await key("Escape");
        expect(versionChoices()).toEqual([]);
        expect(versionBox().value).toBe("Server on the PATH");
    });

    it("posts the highest version on Create unless another is picked",
        async () => {
            await mount();
            await load();

            await click("Create");

            expect(created()?.fields.serverVersion).toBe("12.3.2");
        });

    it("puts the confirmation beside the password, and refuses a mismatch",
        async () => {
            await mount();
            await load();
            const [password, confirmation] = [
                ...host.querySelectorAll<HTMLInputElement>(
                    "input[type=password]"),
            ];
            expect(fieldOf("Root Password").querySelector("input"))
                .toBe(password);
            expect(fieldOf("Confirm Root Password").querySelector("input"))
                .toBe(confirmation);

            await typeInto(password!, "secret");
            await typeInto(confirmation!, "secert");

            expect(fieldOf("Confirm Root Password").querySelector(
                ".field-hint")?.textContent).toBe("The passwords do not match.");
            await click("Create");
            expect(created()).toBeUndefined();

            await typeInto(confirmation!, "secret");
            expect(fieldOf("Confirm Root Password").querySelector(
                ".field-hint")).toBeNull();
            await click("Create");
            expect(created()?.fields).toMatchObject({
                password: "secret", passwordConfirmation: "secret",
            });
        });

    it("asks for the confirmation on Create when only the password was "
        + "typed", async () => {
        await mount();
        await load();

        await typeInto(host.querySelector<HTMLInputElement>(
            "input[type=password]")!, "secret");
        await click("Create");

        expect(created()).toBeUndefined();
        expect(fieldOf("Confirm Root Password").querySelector(".field-hint")
            ?.textContent).toBe("The passwords do not match.");
    });

    it("posts the server on the PATH once it is picked", async () => {
        await mount();
        await load();
        await openVersions();
        await choose("Server on the PATH");

        expect(fieldOf("Server Version").querySelector(".field-hint")
            ?.textContent).toContain("found on the PATH");
        await click("Create");

        // Sent as shown; the host turns it into "no version asked for".
        expect(created()?.fields.serverVersion).toBe("Server on the PATH");
    });

    it("posts the fields on Create", async () => {
        await mount();
        await load();
        await openVersions();
        await choose("11.8.9");
        for (const box of host.querySelectorAll<HTMLInputElement>(
            "input[type=password]")) {
            await typeInto(box, "secret");
        }

        await click("Create");

        expect(created()?.fields).toEqual({
            ...emptySandboxFields("3310"),
            serverVersion: "11.8.9",
            password: "secret",
            passwordConfirmation: "secret",
        });
    });

    it("marks a bad field once it is typed into, and refuses to post",
        async () => {
            await mount();
            await load([3310]);

            // Not before: a dialog that opens complaining helps nobody.
            expect(host.querySelector(".invalid")).toBeNull();

            await type("Port", "80");
            expect(fieldOf("Port").querySelector(".field-hint")?.textContent)
                .toBe("The port must be between 1024 and 65535.");
            expect(fieldOf("Port").querySelector("input")?.className)
                .toBe("invalid");

            await click("Create");
            expect(created()).toBeUndefined();
        });

    it("brings up the tab a problem is on when Create is pressed",
        async () => {
            await mount();
            await load();
            await click("Advanced");
            await type("Server ID", "x");
            await click("Basic");

            await click("Create");

            expect(host.querySelector(".tab.selected")?.textContent)
                .toBe("Advanced");
            expect(fieldOf("Server ID").querySelector(".field-hint")
                ?.textContent).toBe("The server ID is a whole number.");
            expect(created()).toBeUndefined();
        });

    it("shows a problem with an untouched field on Create", async () => {
        // The port the host suggested is taken by the time Create is
        // pressed - the field was never typed into, and still says why.
        await mount();
        await load([3310]);

        await click("Create");

        expect(fieldOf("Port").querySelector(".field-hint")?.textContent)
            .toBe("There is a sandbox on port 3310 already.");
    });

    it("takes the advanced options", async () => {
        await mount();
        await load();
        await click("Advanced");
        expect(fieldOf("Allow root Access From").querySelector("input")
            ?.value).toBe("127.0.0.1");
        await type("Allow root Access From", "");
        await act(async () => {
            const box = host.querySelector<HTMLInputElement>(
                ".tab-body label.checkbox input")!;
            box.checked = true;
            box.dispatchEvent(new Event("change", { bubbles: true }));
            await Promise.resolve();
        });
        await typeInto(host.querySelector("textarea")!, "a=1\nb=2");

        await click("Create");

        expect(created()?.fields).toMatchObject({
            allowRootFrom: "", ssl: true, mariadbdOptions: "a=1\nb=2",
        });
    });

    it("says it is deploying, and disables the buttons meanwhile",
        async () => {
            await mount();
            await load();

            await send({ type: "busy", busy: true });

            expect(host.textContent).toContain("Deploying the sandbox.");
            expect([...host.querySelectorAll("footer button")]
                .every((button) => {
                    return (button as HTMLButtonElement).disabled;
                })).toBe(true);
        });

    it("shows why a deploy failed, until a field changes", async () => {
        await mount();
        await load();

        await send({ type: "createError", message: "Port 3310 is in use." });
        expect(host.querySelector(".message.error")?.textContent)
            .toBe("Port 3310 is in use.");

        await type("Port", "3320");
        expect(host.querySelector(".message.error")).toBeNull();
    });

    it("posts cancel", async () => {
        await mount();
        await load();

        await click("Cancel");

        expect(posted.at(-1)).toEqual({ type: "cancel" });
    });
});
