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

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
    chatSkillsFor,
    newestReleaseTag,
    readSkills,
    skillName,
    syncSkills,
    withChatSkills,
    type SyncEnvironment,
} from "../../../tools/syncAiSkills.mjs";

const extensionRoot = path.resolve(import.meta.dirname, "..", "..", "..");

const skillMd = (name: string): string => {
    return `---\nname: ${name}\ndescription: About ${name}.\n---\n\n# ${name}\n`;
};

describe("newestReleaseTag", () => {
    it("takes the highest vX.Y.Z, prereleases included, drafts not", () => {
        expect(newestReleaseTag([
            { tag_name: "v26.9.5", draft: false },
            { tag_name: "v26.10.2", draft: false },
            { tag_name: "v26.10.10", draft: true },
            { tag_name: "v26.10.1", draft: false },
            { tag_name: "nightly", draft: false },
        ])).toBe("v26.10.2");
    });

    it("compares numerically, not as text", () => {
        expect(newestReleaseTag([
            { tag_name: "v26.10.9", draft: false },
            { tag_name: "v26.10.10", draft: false },
        ])).toBe("v26.10.10");
    });

    it("finds none among none", () => {
        expect(newestReleaseTag([])).toBeUndefined();
    });
});

describe("skillName", () => {
    it("reads the frontmatter's name, quoted or not", () => {
        expect(skillName(skillMd("mariadb-call"))).toBe("mariadb-call");
        expect(skillName("---\r\nname: \"x-y\"\r\n---\r\n")).toBe("x-y");
    });

    it("finds none outside a frontmatter", () => {
        expect(skillName("# name: nope\n")).toBeUndefined();
    });
});

describe("chatSkillsFor and withChatSkills", () => {
    it("lists the skills sorted, under the setting", () => {
        expect(chatSkillsFor(["b", "a"])).toEqual([
            { path: "skills/a/SKILL.md", when: "config.mariadb.copilot.enabled" },
            { path: "skills/b/SKILL.md", when: "config.mariadb.copilot.enabled" },
        ]);
    });

    it("replaces chatSkills and leaves every other key where it was", () => {
        const manifest = {
            name: "x",
            contributes: { commands: [], chatSkills: [{ path: "old" }] },
            scripts: {},
        };
        const next = withChatSkills(manifest, chatSkillsFor(["a"]));

        expect(Object.keys(next)).toEqual(["name", "contributes", "scripts"]);
        expect(next.contributes).toEqual({
            commands: [],
            chatSkills: chatSkillsFor(["a"]),
        });
    });
});

describe("the committed skills", () => {
    it("are each named after their folder, as VS Code requires", () => {
        expect(readSkills(path.join(extensionRoot, "skills")).length)
            .toBeGreaterThan(0);
    });

    it("are what package.json contributes, no more and no less", () => {
        const manifest = JSON.parse(fs.readFileSync(
            path.join(extensionRoot, "package.json"), "utf8")) as {
            contributes: { chatSkills: unknown };
        };

        expect(manifest.contributes.chatSkills).toEqual(
            chatSkillsFor(readSkills(path.join(extensionRoot, "skills"))));
    });
});

describe("syncSkills", () => {
    let root: string;
    let logged: string[];

    beforeEach(() => {
        root = fs.mkdtempSync(path.join(os.tmpdir(), "sync-skills-test-"));
        logged = [];
        fs.writeFileSync(path.join(root, "package.json"),
            `${JSON.stringify({ name: "x", contributes: {} }, null, 2)}\n`);
    });

    afterEach(() => {
        fs.rmSync(root, { recursive: true, force: true });
    });

    /**
     * A world whose newest release is `tag`, its archive holding `skills`
     * (folder name to SKILL.md text).
     */
    const world = (
        tag: string,
        skills: Record<string, string>,
        failing = false,
    ): SyncEnvironment & { downloads: string[] } => {
        const downloads: string[] = [];

        return {
            root,
            downloads,
            log: (message) => { logged.push(message); },
            fetchJson: () => {
                return failing
                    ? Promise.reject(new Error("offline"))
                    : Promise.resolve([
                        { tag_name: "v1.0.0", draft: false },
                        { tag_name: tag, draft: false },
                    ]);
            },
            download: (url) => {
                downloads.push(url);

                return Promise.resolve();
            },
            untar: (_archive, into) => {
                const plugin = path.join(into, `ai-plugins-${tag.slice(1)}`,
                    "claude", "dev-plugin");
                for (const [name, text] of Object.entries(skills)) {
                    fs.mkdirSync(path.join(plugin, "skills", name),
                        { recursive: true });
                    fs.writeFileSync(path.join(plugin, "skills", name,
                        "SKILL.md"), text);
                }
                fs.writeFileSync(path.join(plugin, "skills", ".gitkeep"), "");
                fs.writeFileSync(path.join(plugin, "skills-source.json"),
                    JSON.stringify({ source_repo: "upstream" }));
            },
            now: () => { return new Date("2026-10-12T10:00:00Z"); },
        };
    };

    const manifest = (): { contributes: { chatSkills: unknown } } => {
        return JSON.parse(fs.readFileSync(path.join(root, "package.json"),
            "utf8")) as { contributes: { chatSkills: unknown } };
    };

    it("vendors the newest release and contributes its skills", async () => {
        const environment = world("v2.0.0",
            { b: skillMd("b"), a: skillMd("a") });

        expect(await syncSkills(environment, false)).toBe("updated");

        expect(environment.downloads).toEqual([
            "https://codeload.github.com/mariadb-corporation/ai-plugins/"
                + "tar.gz/refs/tags/v2.0.0",
        ]);
        expect(fs.readdirSync(path.join(root, "skills")).sort())
            .toEqual(["README.md", "a", "b", "skills-source.json"]);
        expect(JSON.parse(fs.readFileSync(
            path.join(root, "skills", "skills-source.json"), "utf8")))
            .toEqual({
                tag: "v2.0.0",
                synced_at: "2026-10-12",
                upstream: { source_repo: "upstream" },
            });
        expect(manifest().contributes.chatSkills)
            .toEqual(chatSkillsFor(["a", "b"]));
    });

    it("downloads nothing when the release is the one it has", async () => {
        await syncSkills(world("v2.0.0", { a: skillMd("a") }), false);
        const again = world("v2.0.0", { a: skillMd("a") });

        expect(await syncSkills(again, false)).toBe("current");
        expect(again.downloads).toEqual([]);
    });

    it("drops a skill the new release no longer has", async () => {
        await syncSkills(world("v2.0.0",
            { a: skillMd("a"), gone: skillMd("gone") }), false);
        await syncSkills(world("v2.1.0", { a: skillMd("a") }), false);

        expect(fs.existsSync(path.join(root, "skills", "gone"))).toBe(false);
        expect(manifest().contributes.chatSkills).toEqual(chatSkillsFor(["a"]));
    });

    it("keeps what it has when GitHub cannot be reached", async () => {
        await syncSkills(world("v2.0.0", { a: skillMd("a") }), false);

        expect(await syncSkills(world("v2.1.0", {}, true), false))
            .toBe("kept");
        expect(logged.at(-1)).toContain("warning");
        expect(readSkills(path.join(root, "skills"))).toEqual(["a"]);
    });

    it("fails when strict, or when there is nothing to keep", async () => {
        await expect(syncSkills(world("v2.0.0", {}, true), false))
            .rejects.toThrow("offline");

        await syncSkills(world("v2.0.0", { a: skillMd("a") }), false);
        await expect(syncSkills(world("v2.1.0", {}, true), true))
            .rejects.toThrow("offline");
    });

    it("leaves skills/ alone when a skill is misnamed", async () => {
        await syncSkills(world("v2.0.0", { a: skillMd("a") }), false);

        const outcome = await syncSkills(
            world("v2.1.0", { a: skillMd("a"), b: skillMd("not-b") }), false);

        expect(outcome).toBe("kept");
        expect(logged.at(-1)).toContain("match its folder");
        expect(readSkills(path.join(root, "skills"))).toEqual(["a"]);
    });
});
