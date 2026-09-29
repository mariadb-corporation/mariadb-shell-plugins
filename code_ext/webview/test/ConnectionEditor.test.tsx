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
import { ConnectionEditor } from "../src/ConnectionEditor.js";
import {
    emptyConnectionFields,
    type IConnectionFields,
} from "../../src/connections/connectionUri.js";
import type {
    EditorHostMessage,
    ISaveMessage,
    ITestMessage,
} from "../../src/connections/editorProtocol.js";

let host: HTMLDivElement;

const mount = async (): Promise<void> => {
    await act(async () => {
        render(<ConnectionEditor />, host);
        await Promise.resolve();
    });
};

const send = async (message: EditorHostMessage): Promise<void> => {
    await act(async () => {
        window.dispatchEvent(new MessageEvent("message", { data: message }));
        await Promise.resolve();
    });
};

/** Loads the editor with the given state, as the host does. */
const load = async (
    overrides: Partial<IConnectionFields> = {},
    rest: Partial<Omit<EditorHostMessage & { type: "load" }, "type" | "fields">>
        = {},
): Promise<void> => {
    await send({
        type: "load",
        fields: { ...emptyConnectionFields(), ...overrides },
        mcpAccess: false,
        hasStoredPassword: false,
        ...rest,
    } as EditorHostMessage);
};

const buttons = (): HTMLButtonElement[] => {
    return [...host.querySelectorAll("button")];
};

const click = async (label: string): Promise<void> => {
    const button = buttons().find((node) => {
        return (node.textContent ?? "").trim() === label;
    });
    if (!button) {
        throw new Error(
            `No button '${label}'. Have: ${buttons().map((node) => {
                return node.textContent;
            }).join(", ")}`,
        );
    }

    await act(async () => {
        button.click();
        await Promise.resolve();
    });
};

/** Clicks the checkbox whose label reads as given. */
const toggle = async (label: string): Promise<void> => {
    const box = [...host.querySelectorAll("label.checkbox")].find((node) => {
        return (node.textContent ?? "").trim() === label;
    })?.querySelector("input") as HTMLInputElement | null;
    if (!box) {
        throw new Error(`No checkbox '${label}'.`);
    }

    await act(async () => {
        box.checked = !box.checked;
        box.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve();
    });
};

/** Types into the input under the given caption. */
const type = async (caption: string, value: string): Promise<void> => {
    const field = [...host.querySelectorAll("label.field")].find((node) => {
        return node.querySelector(".field-caption")?.textContent === caption;
    });
    const input = field?.querySelector("input, select") as
        HTMLInputElement | null;
    if (!input) {
        throw new Error(`No field '${caption}'.`);
    }

    await act(async () => {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve();
    });
};

const lastPosted = <T,>(type: string): T | undefined => {
    return [...posted].reverse().find((message) => {
        return message.type === type;
    }) as T | undefined;
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

describe("ConnectionEditor", () => {
    it("asks the host for its state as soon as it is up", async () => {
        await mount();

        expect(posted).toEqual([{ type: "ready" }]);
    });

    it("shows the tabs the MySQL Shell's editor has", async () => {
        await mount();
        await load();

        expect([...host.querySelectorAll(".tab")].map((node) => {
            return node.textContent;
        })).toEqual(["Basic", "SSL", "SSH", "Advanced"]);
    });

    it("offers no OCI or MDS tab", async () => {
        // They are in the original, and deliberately not here: a connection
        // is stored as a URI, and neither can be written into one.
        await mount();
        await load();

        const text = host.textContent ?? "";
        expect(text).not.toContain("Bastion");
        expect(text).not.toContain("MDS");
    });

    it("hides the SSH settings until the tunnel is asked for", async () => {
        // The fields are only meaningful on a `+ssh` URI - the shell refuses
        // an ssh-* option on any other scheme - so showing them on a
        // connection that does not tunnel would offer a setting that cannot
        // be saved.
        await mount();
        await load();
        await click("SSH");

        expect(host.textContent).not.toContain("SSH Host");

        await toggle("Connect through an SSH tunnel");

        expect(host.textContent).toContain("SSH Host");
    });

    it("turns the tunnel on by changing the protocol, and back again",
        async () => {
            await mount();
            await load({ user: "dba", host: "db.internal", scheme: "mysql" });
            await click("SSH");
            await toggle("Connect through an SSH tunnel");
            await type("SSH Host", "bastion.example.com");
            await type("SSH User Name", "jump");
            await click("Create");

            // The base protocol is kept: only the extension is added.
            expect(lastPosted<ISaveMessage>("save")?.fields.scheme)
                .toBe("mysql+ssh");
            expect(lastPosted<ISaveMessage>("save")?.fields.sshHost)
                .toBe("bastion.example.com");

            await toggle("Connect through an SSH tunnel");
            await click("Create");

            expect(lastPosted<ISaveMessage>("save")?.fields.scheme)
                .toBe("mysql");
        });

    it("fills the fields from the connection it was opened on", async () => {
        await mount();
        await load(
            { user: "dba", host: "db.example.com", port: "3307" },
            { uri: "dba@db.example.com:3307", mcpAccess: true,
                hasStoredPassword: true },
        );

        // The URI box says which connection this is; no subtitle repeats it.
        expect((host.querySelector("#connection-uri") as HTMLInputElement)
            .value).toBe("mariadb://dba@db.example.com:3307");
        expect(host.textContent).not.toContain("Editing");
        expect((host.querySelector("input[type=checkbox]") as HTMLInputElement)
            .checked).toBe(true);
        // A stored password is kept unless the user says otherwise.
        expect(host.textContent).toContain("will be kept");
        expect(buttons().map((node) => { return node.textContent; }))
            .toContain("Set New Password");
    });

    it("saves a new connection with what was typed", async () => {
        await mount();
        await load();

        await type("User Name", "dba");
        await type("Host Name or IP Address", "localhost");
        await click("Create");

        const save = lastPosted<ISaveMessage>("save")!;
        expect(save.fields.user).toBe("dba");
        expect(save.mcpAccess).toBe(false);
        // Never typed, so the host is told to leave the password alone.
        expect(save.password).toBeUndefined();
    });

    it("sends the typed password, and an empty one as a real value",
        async () => {
            await mount();
            await load({ user: "dba" });

            await click("Set Password");
            await click("Create");

            // "" is not undefined: an account with no password has an empty
            // one, and that has to be storable.
            expect(lastPosted<ISaveMessage>("save")!.password).toBe("");
        });

    it("puts the password back to 'keep the stored one'", async () => {
        await mount();
        await load({ user: "dba" }, { uri: "dba@localhost:3306",
            hasStoredPassword: true });

        await click("Set New Password");
        await click("Keep Stored Password");
        await click("Save");

        expect(lastPosted<ISaveMessage>("save")!.password).toBeUndefined();
    });

    it("sends the MCP checkbox with the save", async () => {
        await mount();
        await load({ user: "dba" });

        const checkbox = host.querySelector("input[type=checkbox]") as
            HTMLInputElement;
        await act(async () => {
            checkbox.checked = true;
            checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            await Promise.resolve();
        });
        await click("Create");

        expect(lastPosted<ISaveMessage>("save")!.mcpAccess).toBe(true);
    });

    it("tests the connection without saving it", async () => {
        await mount();
        await load({ user: "dba" });

        await click("Test Connection");

        expect(lastPosted<ITestMessage>("test")).toBeDefined();
        expect(lastPosted("save")).toBeUndefined();
    });

    it("shows what the test said, good or bad", async () => {
        await mount();
        await load({ user: "dba" });

        await send({ type: "testResult", ok: true, message: "Connected." });
        expect(host.querySelector(".message.ok")?.textContent)
            .toBe("Connected.");

        await send({
            type: "testResult", ok: false, message: "Access denied",
        });
        expect(host.querySelector(".message.error")?.textContent)
            .toBe("Access denied");
    });

    it("shows a refused save and stays open", async () => {
        await mount();
        await load();

        await send({ type: "saveError", message: "A user name is required." });

        expect(host.querySelector(".message.error")?.textContent)
            .toBe("A user name is required.");
    });

    it("clears a stale test result as soon as a field changes", async () => {
        // It was about the old values, so leaving it up would say a
        // connection works that nobody has tried.
        await mount();
        await load({ user: "dba" });
        await send({ type: "testResult", ok: true, message: "Connected." });

        await type("User Name", "someone-else");

        expect(host.querySelector(".message")).toBeNull();
    });

    it("disables its buttons while the host is busy", async () => {
        await mount();
        await load({ user: "dba" });

        // The action buttons only. Switching tabs while a test runs is
        // harmless, so those stay live.
        const actions = (): HTMLButtonElement[] => {
            return [...host.querySelectorAll<HTMLButtonElement>(
                ".editor-footer button")];
        };

        await send({ type: "busy", busy: true });
        expect(actions()).not.toHaveLength(0);
        expect(actions().every((node) => { return node.disabled; }))
            .toBe(true);
        expect([...host.querySelectorAll(".tab")].every((node) => {
            return !(node as HTMLButtonElement).disabled;
        })).toBe(true);

        await send({ type: "busy", busy: false });
        expect(actions().some((node) => { return node.disabled; }))
            .toBe(false);
    });

    it("closes on cancel without sending anything else", async () => {
        await mount();
        await load({ user: "dba" });

        await click("Cancel");

        expect(lastPosted("cancel")).toBeDefined();
        expect(lastPosted("save")).toBeUndefined();
    });

    it("edits the SSL and Advanced settings a URI can carry", async () => {
        await mount();
        await load({ user: "dba" });

        await click("SSL");
        await type("Permitted ciphers (optional, comma separated list)",
            "AES256");

        await click("Advanced");
        await type("Connection Timeout", "5000");
        await click("Add Option");

        const rows = host.querySelectorAll(".options tr");
        expect(rows).toHaveLength(1);

        await click("Create");
        const save = lastPosted<ISaveMessage>("save")!;
        expect(save.fields.sslCipher).toBe("AES256");
        expect(save.fields.connectTimeout).toBe("5000");
        expect(save.fields.extraOptions).toEqual([{ name: "", value: "" }]);
    });

    it("toggles a compression algorithm on and off", async () => {
        await mount();
        await load({ user: "dba" });
        await click("Advanced");

        const zstd = [...host.querySelectorAll(".checkbox")].find((node) => {
            return node.textContent?.includes("zstd");
        })?.querySelector("input") as HTMLInputElement;

        await act(async () => {
            zstd.click();
            await Promise.resolve();
        });
        await click("Create");

        expect(lastPosted<ISaveMessage>("save")!.fields.compressionAlgorithms)
            .toEqual(["zstd"]);
    });

    describe("the connection URI", () => {
        const uriBox = (): HTMLInputElement => {
            return host.querySelector("#connection-uri") as HTMLInputElement;
        };

        const typeUri = async (value: string): Promise<void> => {
            await act(async () => {
                uriBox().value = value;
                uriBox().dispatchEvent(new Event("input", { bubbles: true }));
                await Promise.resolve();
            });
        };

        const fieldValue = (caption: string): string => {
            const field = [...host.querySelectorAll("label.field")]
                .find((node) => {
                    return node.querySelector(".field-caption")?.textContent
                        === caption;
                });

            return (field?.querySelector("input, select") as
                HTMLInputElement).value;
        };

        it("shows what the fields spell, and follows them", async () => {
            await mount();
            await load({ user: "dba" });

            expect(uriBox().value).toBe("mariadb://dba@localhost:3306");

            await type("Default Schema", "world");
            await type("Protocol", "mysql");

            expect(uriBox().value).toBe("mysql://dba@localhost:3306/world");
        });

        it("says what is missing while the fields are incomplete",
            async () => {
                await mount();
                await load();

                expect(uriBox().value).toBe("mariadb://@localhost:3306");
                expect(host.querySelector(".uri-note")?.textContent)
                    .toBe("A user name is required.");
            });

        it("fills the fields in from a URI typed into it", async () => {
            await mount();
            await load({ user: "dba" });

            await typeUri("mysql://app@db:3310/shop?ssl-mode=REQUIRED");

            expect(fieldValue("User Name")).toBe("app");
            expect(fieldValue("Host Name or IP Address")).toBe("db");
            expect(fieldValue("Port")).toBe("3310");
            expect(fieldValue("Default Schema")).toBe("shop");
            expect(fieldValue("Protocol")).toBe("mysql");

            await click("Create");

            expect(lastPosted<ISaveMessage>("save")?.fields).toMatchObject({
                scheme: "mysql", user: "app", host: "db", sslMode: "REQUIRED",
            });
        });

        it("points at the problem and will not save it", async () => {
            await mount();
            await load({ user: "dba" });

            await typeUri("mariadb://dba@db:33x6/world");

            expect(uriBox().value).toBe("mariadb://dba@db:33x6/world");
            expect(uriBox().getAttribute("aria-invalid")).toBe("true");
            expect(host.querySelector(".uri-problem p")?.textContent)
                .toContain("'33x6' is not a port number");
            expect(host.querySelector(".uri-problem mark")?.textContent)
                .toBe("33x6");
            // The fields keep what they had last.
            expect(fieldValue("Host Name or IP Address")).toBe("localhost");

            await click("Create");
            await click("Test Connection");

            expect(lastPosted("save")).toBeUndefined();
            expect(lastPosted("test")).toBeUndefined();
            expect(document.activeElement).toBe(uriBox());
            expect(uriBox().selectionStart).toBe(17);
            expect(uriBox().selectionEnd).toBe(21);
        });

        it("drops a broken URI once a field is edited", async () => {
            await mount();
            await load({ user: "dba" });

            await typeUri("mariadb://dba@db:33x6");
            await type("Port", "3307");

            expect(host.querySelector(".uri-problem")).toBeNull();
            expect(uriBox().value).toBe("mariadb://dba@localhost:3307");
        });

        it("asks the host for the clipboard and takes a sound URI",
            async () => {
                await mount();
                await load({ user: "dba" });

                const paste = host.querySelector(
                    "button[aria-label='Paste URI']") as HTMLButtonElement;
                expect(paste.querySelector(".codicon-copy")).not.toBeNull();
                await act(async () => {
                    paste.click();
                    await Promise.resolve();
                });
                expect(lastPosted("paste")).toEqual({ type: "paste" });

                await send({
                    type: "clipboard",
                    text: "  mariadb+ssh://app@db/shop?ssh-host=bastion\n",
                });

                expect(uriBox().value)
                    .toBe("mariadb+ssh://app@db/shop?ssh-host=bastion");
                expect(fieldValue("User Name")).toBe("app");
                expect(host.querySelector(".uri-problem")).toBeNull();
            });

        it("keeps a pasted URI that is wrong, and selects what is",
            async () => {
                await mount();
                await load({ user: "dba" });

                await send({
                    type: "clipboard", text: "postgres://dba@db",
                });

                expect(uriBox().value).toBe("postgres://dba@db");
                expect(host.querySelector(".uri-problem p")?.textContent)
                    .toContain("not a protocol");
                expect(document.activeElement).toBe(uriBox());
                expect(uriBox().value.slice(uriBox().selectionStart!,
                    uriBox().selectionEnd!)).toBe("postgres");
            });

        it("moves a pasted URI's password into the Password field",
            async () => {
                await mount();
                await load({ user: "dba" }, { hasStoredPassword: true });

                await send({
                    type: "clipboard", text: "mariadb://app:s3cret@db:3310",
                });

                // Out of the box, so it is not left on screen in the clear.
                expect(uriBox().value).toBe("mariadb://app@db:3310");
                expect(host.querySelector(".uri-problem")).toBeNull();
                expect((host.querySelector("input[type=password]") as
                    HTMLInputElement).value).toBe("s3cret");

                await click("Create");

                expect(lastPosted<ISaveMessage>("save")).toMatchObject({
                    password: "s3cret",
                    fields: { user: "app", host: "db", port: "3310" },
                });
            });

        it("moves one typed into the box too", async () => {
            await mount();
            await load({ user: "dba" });

            await typeUri("mariadb://app:pw@db");

            expect(uriBox().value).toBe("mariadb://app@db");
            expect((host.querySelector("input[type=password]") as
                HTMLInputElement).value).toBe("pw");
        });
    });

    it("fades whichever edge of the tab has fields scrolled past it",
        async () => {
            await mount();
            await load({ user: "dba" });

            const body = host.querySelector(".tab-body") as HTMLDivElement;
            const shown = (edge: string): boolean => {
                return host.querySelector(`.scroll-fade.${edge}`)!
                    .classList.contains("shown");
            };
            const scrollTo = async (top: number): Promise<void> => {
                await act(async () => {
                    body.scrollTop = top;
                    body.dispatchEvent(new Event("scroll"));
                    await Promise.resolve();
                });
            };

            // jsdom lays nothing out, so the sizes are the test's to say.
            Object.defineProperty(body, "clientHeight", { value: 200 });
            Object.defineProperty(body, "scrollHeight", { value: 500 });

            await scrollTo(0);
            expect(shown("bottom")).toBe(true);
            expect(shown("top")).toBe(false);

            await scrollTo(150);
            expect(shown("bottom")).toBe(true);
            expect(shown("top")).toBe(true);

            await scrollTo(300);
            expect(shown("bottom")).toBe(false);
            expect(shown("top")).toBe(true);
        });

    it("notices the layout changing after the dialog is up", async () => {
        // What a webview does on its first opening: it is sized and styled
        // after the first render, so nothing re-renders to re-measure.
        await mount();
        await load({ user: "dba" });

        const body = host.querySelector(".tab-body") as HTMLDivElement;
        const bottom = host.querySelector(".scroll-fade.bottom")!;
        expect(bottom.classList.contains("shown")).toBe(false);

        Object.defineProperty(body, "clientHeight",
            { value: 200, configurable: true });
        Object.defineProperty(body, "scrollHeight",
            { value: 500, configurable: true });
        await act(async () => {
            window.dispatchEvent(new Event("resize"));
            await Promise.resolve();
        });
        expect(bottom.classList.contains("shown")).toBe(true);

        Object.defineProperty(body, "scrollHeight",
            { value: 200, configurable: true });
        await act(async () => {
            body.dispatchEvent(new Event("pointerenter"));
            await Promise.resolve();
        });
        expect(bottom.classList.contains("shown")).toBe(false);
    });

    it("stops the fades at a scrollbar that takes room, and only then",
        async () => {
            await mount();
            await load({ user: "dba" });

            const body = host.querySelector(".tab-body") as HTMLDivElement;
            const fades = (): string[] => {
                return [...host.querySelectorAll<HTMLElement>(".scroll-fade")]
                    .map((fade) => { return fade.style.right; });
            };
            const resize = async (): Promise<void> => {
                await act(async () => {
                    window.dispatchEvent(new Event("resize"));
                    await Promise.resolve();
                });
            };

            // An overlay scrollbar, as macOS draws it: no room taken.
            Object.defineProperty(body, "offsetWidth",
                { value: 600, configurable: true });
            Object.defineProperty(body, "clientWidth",
                { value: 600, configurable: true });
            await resize();
            expect(fades()).toEqual(["0px", "0px"]);

            // A classic one, 14 pixels wide.
            Object.defineProperty(body, "clientWidth",
                { value: 586, configurable: true });
            await resize();
            expect(fades()).toEqual(["14px", "14px"]);
        });

    it("shows no fade when everything fits", async () => {
        await mount();
        await load({ user: "dba" });

        expect(host.querySelectorAll(".scroll-fade.shown")).toHaveLength(0);
    });
});
