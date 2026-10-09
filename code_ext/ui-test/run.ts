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

import { spawnSync } from "node:child_process";
import {
    chmodSync,
    mkdirSync,
    mkdtempSync,
    rmSync,
    symlinkSync,
    writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { delimiter, join, resolve } from "node:path";

/**
 * Runs the UI tests: VS Code with the packaged extension, driven by
 * ExTester through WebDriver.
 *
 *     MARIADB_SHELL=/path/to/mariadb-shell npm run ui-test
 *
 * The extension takes the first `mariadb-shell` on the PATH, so the run
 * puts a wrapper there that starts the given shell with a configuration
 * home of its own: its connections, its sandboxes (in a directory of the
 * run's), its secrets (the plaintext credential helper of a development
 * build, in that home) and this repository's plugins, never the user's.
 * `mariadbd` has to be on the PATH for the sandboxes. Everything the run made is removed at
 * the end, also when the tests fail.
 *
 * Extra arguments are passed to `extest setup-and-run`; `UI_TEST_GLOB`
 * picks the test files (default: all of them).
 */

const root = resolve(__dirname, "../..");
const repo = resolve(root, "..");

const freePort = async (): Promise<number> => {
    return await new Promise((done) => {
        const server = createServer();
        server.listen(0, "127.0.0.1", () => {
            const address = server.address();
            const port = typeof address === "object" && address !== null
                ? address.port : 0;
            server.close(() => { done(port); });
        });
    });
};

const main = async (): Promise<number> => {
    const shell = process.env.MARIADB_SHELL;
    if (shell === undefined || shell === "") {
        console.error("Set MARIADB_SHELL to the mariadb-shell to test with.");

        return 2;
    }

    const work = mkdtempSync(join(tmpdir(), "mariadb-ui-test-"));
    const home = join(work, "shell-home");
    const bin = join(work, "bin");
    const sandboxes = join(work, "sandboxes");
    const files = join(work, "files");
    for (const dir of [join(home, "plugins"), bin, sandboxes, files]) {
        mkdirSync(dir, { recursive: true });
    }
    for (const plugin of ["mcp_plugin", "mrs_plugin", "msm_plugin"]) {
        symlinkSync(join(repo, plugin), join(home, "plugins", plugin));
    }
    // The secrets too: the keychain is the whole user's, whatever the
    // configuration home, so the connections would be the user's. The
    // plaintext helper keeps them in the home; development builds have it.
    writeFileSync(join(home, "options.json"), JSON.stringify({
        "sandboxDir": sandboxes,
        "credentialStore.helper": "plaintext",
    }, undefined, 4));
    const wrapper = join(bin, "mariadb-shell");
    writeFileSync(wrapper, "#!/bin/sh\n"
        + `MARIADB_SHELL_USER_CONFIG_HOME='${home}' exec '${shell}' "$@"\n`);
    chmodSync(wrapper, 0o755);

    const storage = join(root, ".vscode-test", "extest");
    // A run started from inside VS Code - its terminal, or an agent in its
    // extension host - inherits what makes Electron run as plain Node and
    // what ties a window to that VS Code; the test instance must have none
    // of it, or it exits the moment ChromeDriver starts it.
    const inherited = Object.fromEntries(Object.entries(process.env).filter(
        ([name]) => {
            return !name.startsWith("VSCODE_") && !name.startsWith("ELECTRON_");
        }));
    const env = {
        ...inherited,
        PATH: [bin, process.env.PATH ?? ""].join(delimiter),
        // Says the environment is final. Without it VS Code on macOS reads
        // the login shell's environment and takes ITS PATH, which finds the
        // user's own shell - with the user's own connections - before the
        // wrapper.
        VSCODE_CLI: "1",
        // And should anything still start the shell another way, it is the
        // run's configuration it gets.
        MARIADB_SHELL_USER_CONFIG_HOME: home,
        // What the tests read: a port for the sandbox they deploy, and a
        // directory for the files they write and upload.
        UI_TEST_PORT: String(await freePort()),
        UI_TEST_FILES: files,
        UI_TEST_SCREENSHOTS: join(storage, "screenshots"),
        UI_TEST_STORAGE: storage,
    };

    try {
        const result = spawnSync("npx", [
            "extest", "setup-and-run",
            process.env.UI_TEST_GLOB ?? "out/ui-test/tests/*.test.js",
            "--storage", storage,
            "--extensions_dir", join(storage, "extensions"),
            "--code_settings", join(root, "ui-test", "settings.json"),
            "--mocha_config", join(root, "ui-test", ".mocharc.json"),
            "--code_version", process.env.UI_TEST_CODE_VERSION ?? "max",
            ...process.argv.slice(2),
        ], { cwd: root, env, stdio: "inherit" });

        return result.status ?? 1;
    } finally {
        // A sandbox a failed run left behind stops with its directory gone.
        spawnSync("pkill", ["-f", sandboxes]);
        // A server that was killed may still be writing for a moment.
        rmSync(work, {
            recursive: true, force: true, maxRetries: 10, retryDelay: 500,
        });
    }
};

void main().then((code) => { process.exitCode = code; });
