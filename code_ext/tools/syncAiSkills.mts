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

/**
 * Vendors the MariaDB AI plugins' skills into `skills/`, for Copilot.
 *
 * Run by `npm run build` (as `npm run sync:skills`). It asks GitHub for the
 * newest release of mariadb-corporation/ai-plugins, and if that is not the
 * release `skills/skills-source.json` records, replaces `skills/` with the
 * `claude/dev-plugin/skills` of that release and rewrites
 * `contributes.chatSkills` in `package.json` to match. The result is
 * committed: a skills update is a diff to review, and a build without the
 * network still has every skill.
 *
 * - `SKIP_SKILL_SYNC=1` skips the check (CI sets it, so a CI run never
 *   depends on GitHub or on what was released that day).
 * - `--strict` turns a failed check or download into a failed build; without
 *   it the skills already there are kept and a warning is printed.
 * - `GH_TOKEN` / `GITHUB_TOKEN`, if set, lift the API's rate limit.
 *
 * Runs on Node's own TypeScript support (`node tools/syncAiSkills.mts`),
 * so it uses only erasable syntax and Node's built-ins - and the system
 * `tar`, which macOS, Linux and Windows 10 and later all have.
 */

import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/** The repository the skills are released from. */
const SOURCE_REPO = "mariadb-corporation/ai-plugins";

/** The plugin in that repository whose skills these are. */
const PLUGIN_PATH = "claude/dev-plugin";

/** Where in that repository the skills are. */
const SOURCE_PATH = `${PLUGIN_PATH}/skills`;

/** The provenance file, inside the skills folder. */
const STAMP_FILE = "skills-source.json";

/** The `when` clause every skill is contributed under. */
const SKILL_WHEN = "config.mariadb.copilot.enabled";

/** What `skills/skills-source.json` records. */
interface SkillsStamp {
    tag: string;
    synced_at: string;
    /** The release's own `skills-source.json`: where its skills came from. */
    upstream?: unknown;
}

/** One `contributes.chatSkills` entry. */
interface ChatSkill {
    path: string;
    when: string;
}

/** The fields of a GitHub release this script reads. */
interface Release {
    tag_name: string;
    draft: boolean;
}

const RELEASE_TAG = /^v(\d+)\.(\d+)\.(\d+)$/;

/**
 * @param releases The releases, in any order.
 *
 * @returns The tag of the newest published `vX.Y.Z` release, prereleases
 *          included - every ai-plugins release so far is one - or undefined.
 */
export const newestReleaseTag = (
    releases: readonly Release[],
): string | undefined => {
    // Not src/shell/version.ts: a .ts file outside tools/ makes Node warn
    // that it cannot tell the module type, on every build.
    const compare = (a: number[], b: number[]): number => {
        return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
    };

    let newest: { tag: string; parts: number[] } | undefined;
    for (const release of releases) {
        const match = RELEASE_TAG.exec(release.tag_name);
        if (release.draft || !match) {
            continue;
        }

        const parts = match.slice(1).map(Number);
        if (!newest || compare(parts, newest.parts) > 0) {
            newest = { tag: release.tag_name, parts };
        }
    }

    return newest?.tag;
};

/**
 * @param skillMd A `SKILL.md`'s text.
 *
 * @returns The `name` in its frontmatter, if it has one.
 */
export const skillName = (skillMd: string): string | undefined => {
    const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---/.exec(skillMd);
    const name = frontmatter
        ? /^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m.exec(frontmatter[1])
        : undefined;

    return name?.[1];
};

/**
 * @param names The skills' names, which are their folders' names.
 *
 * @returns The `contributes.chatSkills` entries for them, sorted.
 */
export const chatSkillsFor = (names: readonly string[]): ChatSkill[] => {
    return [...names].sort().map((name) => {
        return { path: `skills/${name}/SKILL.md`, when: SKILL_WHEN };
    });
};

/**
 * @param manifest The parsed `package.json`.
 * @param skills The entries it should contribute.
 *
 * @returns The manifest with `contributes.chatSkills` replaced, and every
 *          other key where it was.
 */
export const withChatSkills = (
    manifest: Record<string, unknown>,
    skills: ChatSkill[],
): Record<string, unknown> => {
    const contributes = {
        ...(manifest.contributes as Record<string, unknown> | undefined),
        chatSkills: skills,
    };

    return { ...manifest, contributes };
};

/**
 * Reads the skills in a folder, checking each the way VS Code will: a
 * folder with a `SKILL.md` whose `name` is the folder's own name.
 *
 * @param folder The folder of skill folders.
 *
 * @returns The skills' names, sorted.
 */
export const readSkills = (folder: string): string[] => {
    const names: string[] = [];
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
        if (!entry.isDirectory()) {
            continue;
        }

        const skillMd = path.join(folder, entry.name, "SKILL.md");
        if (!fs.existsSync(skillMd)) {
            throw new Error(`${entry.name} has no SKILL.md.`);
        }

        const name = skillName(fs.readFileSync(skillMd, "utf8"));
        if (name !== entry.name) {
            throw new Error(`${entry.name}/SKILL.md is named `
                + `"${name ?? ""}"; VS Code needs it to match its folder.`);
        }
        names.push(entry.name);
    }

    if (names.length === 0) {
        throw new Error(`No skills in ${folder}.`);
    }

    return names.sort();
};

/** What the script needs from the world, so the steps can be tested. */
export interface SyncEnvironment {
    /** The extension's root, where `package.json` and `skills/` are. */
    root: string;
    log: (message: string) => void;
    fetchJson: (url: string) => Promise<unknown>;
    /** Downloads a URL into a file. */
    download: (url: string, file: string) => Promise<void>;
    /** Unpacks a `.tar.gz` into a folder. */
    untar: (archive: string, into: string) => void;
    now: () => Date;
}

/**
 * Brings `skills/` and `contributes.chatSkills` up to the newest release.
 *
 * @param environment What the sync needs from the world.
 * @param strict Whether a failed check or download fails the sync.
 *
 * @returns What happened.
 */
export const syncSkills = async (
    environment: SyncEnvironment,
    strict: boolean,
): Promise<"updated" | "current" | "kept"> => {
    const { root, log } = environment;
    const skillsDir = path.join(root, "skills");
    const stamp = readStamp(skillsDir);
    let outcome: "updated" | "current" | "kept" = "current";

    try {
        const releases = await environment.fetchJson(
            `https://api.github.com/repos/${SOURCE_REPO}/releases?per_page=30`);
        const tag = newestReleaseTag(releases as Release[]);
        if (!tag) {
            throw new Error(`${SOURCE_REPO} has no vX.Y.Z release.`);
        }

        if (stamp?.tag === tag) {
            log(`The skills are those of ${SOURCE_REPO} ${tag}, the newest `
                + "release.");
        } else {
            log(`Fetching the skills of ${SOURCE_REPO} ${tag} (had `
                + `${stamp?.tag ?? "none"}).`);
            await replaceSkills(environment, tag, skillsDir);
            outcome = "updated";
        }
    } catch (error) {
        if (strict || !stamp) {
            throw error;
        }

        log(`warning: the skills could not be updated, keeping those of `
            + `${stamp.tag}: ${errorText(error)}`);
        outcome = "kept";
    }

    writeChatSkills(root, readSkills(skillsDir), log);

    return outcome;
};

/**
 * @param skillsDir The skills folder.
 *
 * @returns Its provenance, if it has any.
 */
const readStamp = (skillsDir: string): SkillsStamp | undefined => {
    const file = path.join(skillsDir, STAMP_FILE);

    return fs.existsSync(file)
        ? JSON.parse(fs.readFileSync(file, "utf8")) as SkillsStamp
        : undefined;
};

/**
 * Downloads a release, checks its skills and swaps them in for the old.
 * Nothing in `skills/` changes until all of them have been checked.
 *
 * @param environment What the sync needs from the world.
 * @param tag The release.
 * @param skillsDir Where the skills go.
 *
 * @returns Nothing.
 */
const replaceSkills = async (
    environment: SyncEnvironment,
    tag: string,
    skillsDir: string,
): Promise<void> => {
    const work = fs.mkdtempSync(path.join(os.tmpdir(), "mariadb-skills-"));
    try {
        const archive = path.join(work, "release.tar.gz");
        await environment.download(
            `https://codeload.github.com/${SOURCE_REPO}/tar.gz/refs/tags/${tag}`,
            archive);
        const unpacked = path.join(work, "unpacked");
        fs.mkdirSync(unpacked);
        environment.untar(archive, unpacked);

        // The archive holds one folder, named after the repository and tag.
        const [top] = fs.readdirSync(unpacked);
        const pluginDir = path.join(unpacked, top ?? "", PLUGIN_PATH);
        const source = path.join(unpacked, top ?? "", SOURCE_PATH);
        const names = readSkills(source);

        // Staged beside skills/, so the swap is a rename on one volume.
        const staged = `${skillsDir}.new`;
        fs.rmSync(staged, { recursive: true, force: true });
        fs.mkdirSync(staged);
        for (const name of names) {
            fs.cpSync(path.join(source, name), path.join(staged, name),
                { recursive: true });
        }

        const upstreamFile = path.join(pluginDir, "skills-source.json");
        const stamp: SkillsStamp = {
            tag,
            synced_at: environment.now().toISOString().slice(0, 10),
            ...(fs.existsSync(upstreamFile)
                ? { upstream: JSON.parse(fs.readFileSync(upstreamFile, "utf8")) }
                : {}),
        };
        fs.writeFileSync(path.join(staged, STAMP_FILE),
            `${JSON.stringify(stamp, null, 2)}\n`);
        fs.writeFileSync(path.join(staged, "README.md"), README);

        fs.rmSync(skillsDir, { recursive: true, force: true });
        fs.renameSync(staged, skillsDir);
        environment.log(`${names.length} skills from ${tag} are in skills/.`);
    } finally {
        fs.rmSync(work, { recursive: true, force: true });
    }
};

/**
 * Rewrites `contributes.chatSkills`, if it does not list these skills yet.
 *
 * @param root The extension's root.
 * @param names The skills in `skills/`.
 * @param log Where to say so.
 *
 * @returns Nothing.
 */
const writeChatSkills = (
    root: string,
    names: string[],
    log: (message: string) => void,
): void => {
    const file = path.join(root, "package.json");
    const before = fs.readFileSync(file, "utf8");
    const manifest = JSON.parse(before) as Record<string, unknown>;
    const after = `${JSON.stringify(
        withChatSkills(manifest, chatSkillsFor(names)), null, 2)}\n`;
    if (after !== before) {
        fs.writeFileSync(file, after);
        log(`package.json contributes ${names.length} skills.`);
    }
};

/**
 * @param error What was thrown.
 *
 * @returns Its message.
 */
const errorText = (error: unknown): string => {
    return error instanceof Error ? error.message : String(error);
};

const README = `# Skills for Copilot

The MariaDB agent skills this extension contributes to GitHub Copilot, through
\`contributes.chatSkills\` in \`package.json\`.

**Vendored - do not edit.** \`npm run sync:skills\` (run by \`npm run build\`)
replaces this folder with the \`${SOURCE_PATH}\` of the newest
[${SOURCE_REPO}](https://github.com/${SOURCE_REPO}/releases) release, and
rewrites \`chatSkills\` to match. \`${STAMP_FILE}\` records which release that
was, and where its skills came from. To change a skill, change it at its
source.

Skills vendored from \`mariadb-corporation/mariadb-docs\` are licensed as that
repository says (its \`agent-skills\` are MIT); those from ai-plugins'
\`additional-skills/\` are GPL-2.0, as this extension is.
`;

/**
 * @returns The request headers, with a token when there is one.
 */
const headers = (): Record<string, string> => {
    const token = process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN;

    return {
        "User-Agent": "mariadb-dev-skill-sync",
        Accept: "application/vnd.github+json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
};

/**
 * @param url The URL.
 *
 * @returns The response, which was a success.
 */
const get = async (url: string): Promise<Response> => {
    const response = await fetch(url, {
        headers: headers(),
        signal: AbortSignal.timeout(60_000),
    });
    if (!response.ok) {
        throw new Error(`GET ${url}: ${response.status} ${response.statusText}`);
    }

    return response;
};

/** The real world: Node's fetch and the system tar. */
const nodeEnvironment = (root: string): SyncEnvironment => {
    return {
        root,
        log: (message) => { console.log(`sync:skills: ${message}`); },
        fetchJson: async (url) => {
            return await (await get(url)).json() as unknown;
        },
        download: async (url, file) => {
            const body = await (await get(url)).arrayBuffer();
            fs.writeFileSync(file, Buffer.from(body));
        },
        untar: (archive, into) => {
            execFileSync("tar", ["-xzf", archive, "-C", into],
                { stdio: "inherit" });
        },
        now: () => { return new Date(); },
    };
};

if (import.meta.main) {
    const root = path.resolve(import.meta.dirname, "..");
    if (process.env.SKIP_SKILL_SYNC) {
        console.log("sync:skills: skipped, SKIP_SKILL_SYNC is set.");
    } else {
        try {
            await syncSkills(nodeEnvironment(root),
                process.argv.includes("--strict"));
        } catch (error) {
            console.error(`sync:skills: ${errorText(error)}`);
            process.exitCode = 1;
        }
    }
}
