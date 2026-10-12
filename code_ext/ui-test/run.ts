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
 *     MARIADB_SHELL=/path/to/mariadb-shell npm run ui-test [suite...]
 *
 * The tests are split into suites by area - `core` (startup, connections,
 * SQL and results, the sandbox lifecycle), `mrs` and `dump-load` - so a
 * change to one area is tested without running the rest. Each suite runs
 * in a VS Code of its own with a sandbox of its own: `01-sandbox` deploys
 * it and creates the `uitest` schema every suite works on, and
 * `99-cleanup` deletes it again. Name suites to run only those (`npm run
 * ui-test -- mrs`, or `npm run ui-test:mrs`); none named runs them all,
 * one after the other, and a suite that fails does not stop the next.
 *
 * The extension takes the first `mariadb-shell` on the PATH, so each
 * suite puts a wrapper there that starts the given shell with a
 * configuration home of its own: its connections, its sandboxes (in a
 * directory of the suite's), its secrets (the plaintext credential
 * helper of a development build, in that home) and this repository's
 * plugins, never the user's. `mariadbd` has to be on the PATH for the
 * sandboxes. Everything a suite made is removed when it ends, also when
 * its tests fail.
 *
 * Arguments that name no suite are passed to `extest setup-and-run`;
 * `UI_TEST_GLOB` runs the files it matches as one run instead, without
 * the suites' setup and cleanup. `UI_TEST_SUITE_MINUTES` (default 20)
 * is how long a suite may take before it is stopped.
 */

const root = resolve(__dirname, "../..");
const repo = resolve(root, "..");
const compiled = "out/ui-test/tests";

/**
 * How long a suite may run. ExTester does not exit when ChromeDriver
 * fails to start - its port taken in the moment between being picked
 * and being bound - so without a limit such a run waits forever and the
 * suites after it never start.
 */
const SUITE_TIMEOUT_MS =
    Number(process.env.UI_TEST_SUITE_MINUTES ?? "20") * 60_000;

/** Deploys the suite's sandbox and creates the schema the tests use. */
const SETUP = "01-sandbox";
/** Deletes the suite's sandbox, and its connection with it. */
const CLEANUP = "99-cleanup";

/**
 * The suites, by name: the test files of each, in the order they run,
 * between SETUP and CLEANUP. `00-startup` comes before SETUP, because it
 * checks the views of a first start, before anything is in them.
 */
const SUITES: Record<string, { before?: string[]; files: string[] }> = {
    core: {
        before: ["00-startup"],
        files: ["03-connections", "04-sql-results", "05-lifecycle"],
    },
    mrs: { files: ["02-mrs"] },
    "dump-load": { files: ["06-dump-load"] },
};

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

/**
 * Runs test files in a VS Code of their own, with a shell home, a
 * sandbox directory and ports of their own.
 *
 * @param shell The mariadb-shell to test with.
 * @param testFiles The files, in the order they run.
 * @param extraArgs Passed on to `extest setup-and-run`.
 *
 * @returns The exit code of the run.
 */
const runFiles = async (
    shell: string,
    testFiles: string[],
    extraArgs: string[],
): Promise<number> => {
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
        // What the tests read: ports for the sandboxes they deploy, and a
        // directory for the files they write and upload.
        UI_TEST_PORT: String(await freePort()),
        UI_TEST_PORT2: String(await freePort()),
        UI_TEST_FILES: files,
        UI_TEST_SCREENSHOTS: join(storage, "screenshots"),
        UI_TEST_STORAGE: storage,
    };

    try {
        // Started directly, not through npx: a timeout kills the child it
        // started, and through npx that would leave ExTester running.
        const result = spawnSync(join(root, "node_modules", ".bin", "extest"), [
            "setup-and-run",
            ...testFiles,
            "--storage", storage,
            "--extensions_dir", join(storage, "extensions"),
            "--code_settings", join(root, "ui-test", "settings.json"),
            "--mocha_config", join(root, "ui-test", ".mocharc.json"),
            "--code_version", process.env.UI_TEST_CODE_VERSION ?? "max",
            ...extraArgs,
        ], {
            cwd: root, env, stdio: "inherit",
            timeout: SUITE_TIMEOUT_MS, killSignal: "SIGKILL",
        });
        if (result.error !== undefined) {
            console.error(`The run was stopped: ${result.error.message}`);
        }

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

/**
 * The compiled files of a suite, in the order they run.
 *
 * @param name The suite.
 *
 * @returns One path per file: one pattern each, so the order holds -
 *          ExTester adds the files of one pattern in no set order.
 */
const filesOf = (name: string): string[] => {
    const suite = SUITES[name];

    return [...suite.before ?? [], SETUP, ...suite.files, CLEANUP]
        .map((file) => {
            return `${compiled}/${file}.test.js`;
        });
};

const main = async (): Promise<number> => {
    const shell = process.env.MARIADB_SHELL;
    if (shell === undefined || shell === "") {
        console.error("Set MARIADB_SHELL to the mariadb-shell to test with.");

        return 2;
    }

    const args = process.argv.slice(2);
    const extraArgs = args.filter((arg) => { return !(arg in SUITES); });
    if (process.env.UI_TEST_GLOB !== undefined) {
        return await runFiles(shell, [process.env.UI_TEST_GLOB], extraArgs);
    }

    const named = args.filter((arg) => { return arg in SUITES; });
    const suites = named.length > 0 ? named : Object.keys(SUITES);
    const outcomes: Array<[string, number]> = [];
    for (const name of suites) {
        console.log(`\n=== UI test suite: ${name} ===\n`);
        outcomes.push([name, await runFiles(shell, filesOf(name), extraArgs)]);
    }

    if (outcomes.length > 1) {
        console.log("\n=== UI test suites ===");
        for (const [name, code] of outcomes) {
            console.log(`${name}: ${code === 0 ? "passed" : "FAILED"}`);
        }
    }

    return outcomes.some(([, code]) => { return code !== 0; }) ? 1 : 0;
};

void main().then((code) => { process.exitCode = code; });
