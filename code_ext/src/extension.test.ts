/*
 * Copyright (c) 2026, MariaDB plc.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    createFakeEnvironment,
    createFakeRunner,
    createFakeSpawner,
    type FakeEnvironmentOptions,
} from "./test/helpers.js";
import {
    commands as mockCommands,
    errorMessages,
    informationMessages,
    outputChannels,
    registeredCommands,
    resetVscodeMock,
    withProgressCalls,
} from "./test/mocks/vscode.js";

// The Node backed runtime is replaced wholesale, so activating the
// extension never touches the file system or spawns a process.
const runtime = vi.hoisted(() => {
    return {
        environmentOptions: {} as FakeEnvironmentOptions,
        /** What the environment looks like once the installer has run. */
        environmentAfterInstall: undefined as
            | FakeEnvironmentOptions
            | undefined,
        runnerOutput: [] as string[],
        runnerExitCode: 0,
        /** Makes the runner reject with a non-Error value. */
        runnerRejection: undefined as unknown,
        spawner: undefined as
            | ReturnType<typeof import("./test/helpers.js")["createFakeSpawner"]>
            | undefined,
        runner: undefined as
            | ReturnType<typeof import("./test/helpers.js")["createFakeRunner"]>
            | undefined,
    };
});

vi.mock("./shell/nodeRuntime.js", async () => {
    const helpers = await import("./test/helpers.js");

    return {
        createNodeShellEnvironment: () => {
            const before = helpers.createFakeEnvironment(
                runtime.environmentOptions,
            );
            if (!runtime.environmentAfterInstall) {
                return before;
            }

            // The installer's side effect, modelled as a second set of
            // answers that takes over once the runner has been called.
            const after = helpers.createFakeEnvironment(
                runtime.environmentAfterInstall,
            );
            const current = () => {
                return (runtime.runner?.calls.length ?? 0) > 0
                    ? after
                    : before;
            };

            return {
                ...before,
                probeVersion: (binaryPath: string) => {
                    return current().probeVersion(binaryPath);
                },
                pathExists: (target: string) => {
                    return current().pathExists(target);
                },
                listDirectories: (target: string) => {
                    return current().listDirectories(target);
                },
            };
        },
        createNodeProcessRunner: () => {
            const base = helpers.createFakeRunner(
                runtime.runnerOutput,
                runtime.runnerExitCode,
            );
            runtime.runner = {
                calls: base.calls,
                run: async (command, onOutput) => {
                    const code = await base.run(command, onOutput);
                    if (runtime.runnerRejection !== undefined) {
                        throw runtime.runnerRejection;
                    }

                    return code;
                },
            };

            return runtime.runner;
        },
        createNodeMcpServerSpawner: () => {
            runtime.spawner = helpers.createFakeSpawner();

            return runtime.spawner;
        },
    };
});

const { activate, deactivate } = await import("./extension.js");

const versionLine = (version: string): string => {
    return `mariadb-shell   Ver ${version} for osx10.21 on arm64 `
        + "- for MariaDB 13.1.0-MariaDB (Source distribution)";
};

interface FakeContext {
    subscriptions: { dispose(): unknown }[];
}

/**
 * Builds the slice of the extension context the extension uses.
 *
 * @returns The fake context.
 */
const createContext = (): FakeContext => {
    return { subscriptions: [] };
};

describe("activate", () => {
    beforeEach(() => {
        resetVscodeMock();
        runtime.environmentOptions = {};
        runtime.environmentAfterInstall = undefined;
        runtime.runnerOutput = [];
        runtime.runnerExitCode = 0;
        runtime.runnerRejection = undefined;
        runtime.spawner = undefined;
        runtime.runner = undefined;
    });

    it("starts the MCP server with the shell found on the PATH",
        async () => {
            runtime.environmentOptions = {
                versions: { "mariadb-shell": versionLine("26.9.2") },
            };
            const context = createContext();

            activate(context as never);

            await vi.waitFor(() => {
                expect(runtime.spawner?.commands).toHaveLength(1);
            });
            expect(runtime.spawner?.commands[0]).toEqual({
                command: "mariadb-shell",
                args: ["--", "mcp", "start-server", "--transport=stdio"],
            });
            // Nothing was downloaded, so no notification was shown.
            expect(withProgressCalls).toEqual([]);
            expect(informationMessages).toEqual([]);

            deactivate();
        });

    it("writes the discovered shell to the output channel", async () => {
        runtime.environmentOptions = {
            versions: { "mariadb-shell": versionLine("26.9.2") },
        };

        activate(createContext() as never);

        await vi.waitFor(() => {
            expect(runtime.spawner?.commands).toHaveLength(1);
        });
        expect(outputChannels[0].name).toBe("MariaDB");
        expect(outputChannels[0].lines[0])
            .toContain("MariaDB Shell 26.9.2 found on the PATH");

        deactivate();
    });

    it("registers the restart and log commands", async () => {
        runtime.environmentOptions = {
            versions: { "mariadb-shell": versionLine("26.9.2") },
        };
        const context = createContext();

        activate(context as never);

        await vi.waitFor(() => {
            expect(runtime.spawner?.commands).toHaveLength(1);
        });
        expect([...registeredCommands.keys()]).toEqual([
            "mariadb.restartMcpServer",
            "mariadb.showMcpServerLog",
        ]);

        await mockCommands.executeCommand("mariadb.restartMcpServer");
        expect(runtime.spawner?.commands).toHaveLength(2);
        expect(runtime.spawner?.processes[0].killed).toBe(true);

        await mockCommands.executeCommand("mariadb.showMcpServerLog");
        expect(outputChannels[0].shown).toBe(true);

        deactivate();
    });

    it("installs the shell, reports it and then starts the server",
        async () => {
            const prefix = "/Users/mzinner/.local/share/mariadb-shell";
            const binary = `${prefix}/26.9.2/bin/mariadb-shell`;
            runtime.environmentOptions = {};
            runtime.environmentAfterInstall = {
                directories: { [prefix]: ["26.9.2"] },
                files: [binary],
                versions: { [binary]: versionLine("26.9.2") },
            };
            runtime.runnerOutput = [
                "==> Downloading",
                "==> Unpacking into " + prefix,
            ];

            activate(createContext() as never);

            await vi.waitFor(() => {
                expect(runtime.spawner?.commands).toHaveLength(1);
            });
            expect(withProgressCalls).toHaveLength(1);
            expect(withProgressCalls[0].options.title)
                .toBe("Installing MariaDB Shell 26.9.2");
            expect(withProgressCalls[0].reported)
                .toContain("Downloading");
            expect(informationMessages)
                .toEqual(["MariaDB Shell 26.9.2 was installed."]);
            expect(runtime.spawner?.commands[0].command).toBe(binary);

            deactivate();
        });

    it("reports a failure that is not an Error", async () => {
        runtime.runnerRejection = "spawn EACCES";

        activate(createContext() as never);

        await vi.waitFor(() => {
            expect(errorMessages).toHaveLength(1);
        });
        expect(errorMessages[0]).toBe(
            "Failed to start the MariaDB MCP server: spawn EACCES",
        );

        deactivate();
    });

    it("surfaces a bootstrap failure instead of throwing", async () => {
        // No shell anywhere and an installer that fails.
        runtime.environmentOptions = {};
        runtime.runnerExitCode = 1;

        activate(createContext() as never);

        await vi.waitFor(() => {
            expect(errorMessages).toHaveLength(1);
        });
        expect(errorMessages[0]).toContain(
            "The MariaDB Shell installer exited with code 1.",
        );
        expect(runtime.spawner?.commands ?? []).toHaveLength(0);

        deactivate();
    });

    it("stops the MCP server when the context is disposed", async () => {
        runtime.environmentOptions = {
            versions: { "mariadb-shell": versionLine("26.9.2") },
        };
        const context = createContext();

        activate(context as never);

        await vi.waitFor(() => {
            expect(runtime.spawner?.commands).toHaveLength(1);
        });
        for (const subscription of context.subscriptions) {
            subscription.dispose();
        }

        expect(runtime.spawner?.processes[0].killed).toBe(true);
        expect(outputChannels[0].disposed).toBe(true);

        deactivate();
    });
});

describe("helpers used by the extension test", () => {
    it("are wired to the same fakes the runtime mock hands out", () => {
        expect(typeof createFakeEnvironment).toBe("function");
        expect(typeof createFakeRunner).toBe("function");
        expect(typeof createFakeSpawner).toBe("function");
    });
});
