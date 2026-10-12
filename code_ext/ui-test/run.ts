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

import { spawn, spawnSync } from "node:child_process";
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
 * is how long a suite may take before it is stopped. Ctrl-C, SIGTERM
 * or SIGHUP stop the suite running and end the run.
 */

const root = resolve(__dirname, "../..");
const repo = resolve(root, "..");
const compiled = "out/ui-test/tests";

/**
 * How long a suite may run. ExTester does not exit when ChromeDriver
 * fails to start - its port taken in the moment between being picked
 * and being bound - so without a limit such a run waits forever and the
 * suites after it never start.
 *
 * @returns The limit in milliseconds, or undefined when
 *          `UI_TEST_SUITE_MINUTES` is not a positive number.
 */
const suiteTimeoutMs = (): number | undefined => {
    const minutes = Number(process.env.UI_TEST_SUITE_MINUTES ?? "20");

    return Number.isFinite(minutes) && minutes > 0
        ? minutes * 60_000
        : undefined;
};

/** Deploys the suite's sandbox and creates the schema the tests use. */
const SETUP = "01-sandbox";
/** Deletes the suite's sandbox, and its connection with it. */
const CLEANUP = "99-cleanup";

/**
 * The suites, by name: the test files of each, in the order they run.
 * Each starts with SETUP and ends with CLEANUP - `core` with `00-startup`
 * first, as it checks the views of a first start, before anything is in
 * them.
 */
const SUITES: Record<string, string[]> = {
    core: ["00-startup", SETUP, "03-connections", "04-sql-results",
        "05-lifecycle", CLEANUP],
    mrs: [SETUP, "02-mrs", CLEANUP],
    "dump-load": [SETUP, "06-dump-load", CLEANUP],
};

/** The signals that end the run, with the exit code each ends it with. */
const STOP_SIGNALS = { SIGHUP: 129, SIGINT: 130, SIGTERM: 143 } as const;

/** The process group of the suite running now, while one is. */
let runningGroup: number | undefined;

/** The exit code of the signal that ended the run, once one did. */
let interruptedWith: number | undefined;

/**
 * Kills the process group of the suite running now, if there is one.
 *
 * @param reason Why, for the log.
 *
 * @returns Nothing.
 */
const stopRunningGroup = (reason: string): void => {
    if (runningGroup === undefined) {
        return;
    }
    console.error(`The run was stopped: ${reason}`);
    try {
        process.kill(-runningGroup, "SIGKILL");
    } catch {
        // Gone already.
    }
};

/*
 * A suite runs in a process group of its own, which neither the terminal's
 * Ctrl-C nor a closed terminal nor a SIGTERM from a task reaches: each is
 * passed on to it here, and ends the run - no suite after it starts. The
 * handlers stay for the whole run, so a second Ctrl-C does not kill the
 * runner before it has cleaned up. And should the runner exit anyway, the
 * group goes with it.
 */
for (const [signal, code] of Object.entries(STOP_SIGNALS)) {
    process.on(signal, () => {
        interruptedWith ??= code;
        stopRunningGroup(signal);
    });
}
process.on("exit", () => {
    stopRunningGroup("the runner exited");
});

/**
 * Runs a command in a process group of its own, stopping the whole group
 * - ExTester, the VS Code and the ChromeDriver it started - when the
 * time is up or the run is interrupted. A kill of the command alone
 * would leave those running, holding their ports and the storage the
 * next suite needs.
 *
 * @param command The command.
 * @param args Its arguments.
 * @param options Where, with what environment, and for how long.
 *
 * @returns Its exit code, 1 when it was stopped or could not start.
 */
const runGroup = async (
    command: string,
    args: string[],
    options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
): Promise<number> => {
    if (interruptedWith !== undefined) {
        // Interrupted while the suite was being set up.
        return interruptedWith;
    }
    const { timeoutMs, ...spawnOptions } = options;
    const child = spawn(command, args,
        { ...spawnOptions, stdio: "inherit", detached: true });
    // No pid when it could not start; the error event says why.
    runningGroup = child.pid;
    const timer = setTimeout(() => {
        stopRunningGroup(`over ${String(timeoutMs / 60_000)} minutes`);
    }, timeoutMs);

    try {
        return await new Promise<number>((done) => {
            child.once("error", (error) => {
                console.error(`Could not run ${command}: ${error.message}`);
                done(1);
            });
            child.once("exit", (code) => { done(code ?? 1); });
        });
    } finally {
        clearTimeout(timer);
        runningGroup = undefined;
    }
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
 * @param timeoutMs How long the run may take.
 *
 * @returns The exit code of the run.
 */
const runFiles = async (
    shell: string,
    testFiles: string[],
    extraArgs: string[],
    timeoutMs: number,
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
        return await runGroup(join(root, "node_modules", ".bin", "extest"), [
            "setup-and-run",
            ...testFiles,
            "--storage", storage,
            "--extensions_dir", join(storage, "extensions"),
            "--code_settings", join(root, "ui-test", "settings.json"),
            "--mocha_config", join(root, "ui-test", ".mocharc.json"),
            "--code_version", process.env.UI_TEST_CODE_VERSION ?? "max",
            ...extraArgs,
        ], { cwd: root, env, timeoutMs });
    } finally {
        // A sandbox a failed run left behind stops with its directory gone.
        spawnSync("pkill", ["-f", sandboxes]);
        // A server that was killed may still be writing for a moment.
        rmSync(work, {
            recursive: true, force: true, maxRetries: 10, retryDelay: 500,
        });
    }
};

const main = async (): Promise<number> => {
    const shell = process.env.MARIADB_SHELL;
    if (shell === undefined || shell === "") {
        console.error("Set MARIADB_SHELL to the mariadb-shell to test with.");

        return 2;
    }
    const timeoutMs = suiteTimeoutMs();
    if (timeoutMs === undefined) {
        console.error("Set UI_TEST_SUITE_MINUTES to a number of minutes "
            + `above 0, not "${process.env.UI_TEST_SUITE_MINUTES ?? ""}".`);

        return 2;
    }

    const named: string[] = [];
    const extraArgs: string[] = [];
    for (const arg of process.argv.slice(2)) {
        (Object.hasOwn(SUITES, arg) ? named : extraArgs).push(arg);
    }
    if (process.env.UI_TEST_GLOB !== undefined) {
        return await runFiles(shell, [process.env.UI_TEST_GLOB], extraArgs,
            timeoutMs);
    }

    const outcomes: Array<[string, number]> = [];
    for (const name of named.length > 0 ? named : Object.keys(SUITES)) {
        if (interruptedWith !== undefined) {
            console.log(`${name}: not run, the run was interrupted`);
            continue;
        }
        console.log(`\n=== UI test suite: ${name} ===\n`);
        // One pattern per file, so the order holds: ExTester adds the
        // files of one pattern in no set order.
        const files = SUITES[name].map((file) => {
            return `${compiled}/${file}.test.js`;
        });
        outcomes.push(
            [name, await runFiles(shell, files, extraArgs, timeoutMs)]);
    }

    if (outcomes.length > 1) {
        console.log("\n=== UI test suites ===");
        for (const [name, code] of outcomes) {
            console.log(`${name}: ${code === 0 ? "passed" : "FAILED"}`);
        }
    }

    if (interruptedWith !== undefined) {
        return interruptedWith;
    }

    return outcomes.some(([, code]) => { return code !== 0; }) ? 1 : 0;
};

void main().then((code) => { process.exitCode = code; });
