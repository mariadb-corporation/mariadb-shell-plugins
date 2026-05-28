/*
 * Copyright (c) 2026, Oracle and/or its affiliates.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is designed to work with certain software (including
 * but not limited to OpenSSL) that is licensed under separate terms, as
 * designated in a particular file or component or in included license
 * documentation.  The authors of MySQL hereby grant you an additional
 * permission to link the program and your derivative works with the
 * separately licensed software that they have either included with
 * the program or referenced in the documentation.
 *
 * This program is distributed in the hope that it will be useful,  but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
 * the GNU General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */

import { fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MigrationStepStatus, SubStepId } from "../../../../communication/ProtocolMigration.js";
import {
    canAdvanceFromMigrationChecks,
    getMigrationChecksProgressPercent,
    isMigrationChecksRunning,
    MigrationChecksPoller,
    MigrationChecksStatus,
    shouldAutoCommitMigrationChecks,
    shouldAutoRunMigrationChecks
} from "../../../../app-logic/MigrationSubApp/MigrationChecksRuntime.js";
import { MigrationChecksView } from "../../../../app-logic/MigrationSubApp/MigrationChecksView.js";

const onAbort = vi.fn();
const onRetry = vi.fn();

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe("MigrationChecksRuntime", () => {
    it("detects running and completed states", () => {
        const runningState = {
            status: MigrationStepStatus.IN_PROGRESS,
            errors: [],
            values: null,
            data: {
                issues: [],
                checkStatus: MigrationChecksStatus.RUNNING_UPGRADE_CHECKS,
                checkProgress: {
                    completed: 25,
                    total: 100,
                    detail: "Collecting routines to check",
                    currentCheckTitle: "MySQL syntax check for routine-like objects",
                    currentCheck: 0,
                    completedChecks: 0,
                    totalChecks: 2,
                },
            },
        };

        expect(isMigrationChecksRunning(runningState)).toBe(true);
        expect(canAdvanceFromMigrationChecks(runningState)).toBe(false);
        expect(getMigrationChecksProgressPercent(runningState.data)).toBe(25);

        const doneState = {
            ...runningState,
            data: {
                ...runningState.data,
                checkStatus: MigrationChecksStatus.DONE,
            },
        };

        expect(canAdvanceFromMigrationChecks(doneState)).toBe(true);
        expect(getMigrationChecksProgressPercent(doneState.data)).toBe(100);
    });

    it("starts checks automatically only when entering the sub-step and work is still pending", () => {
        const pendingState = {
            status: MigrationStepStatus.IN_PROGRESS,
            errors: [],
            values: null,
            data: {
                issues: [],
                checkStatus: MigrationChecksStatus.PENDING,
                checkProgress: {
                    completed: 0,
                    total: 0,
                    detail: "",
                    currentCheckTitle: "",
                    currentCheck: -1,
                    completedChecks: 0,
                    totalChecks: 0,
                },
            },
        };

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.SCHEMA_SELECTION,
            stepState: pendingState,
            busy: false,
            previousBusy: false,
        })).toBe(true);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.MIGRATION_CHECKS,
            stepState: pendingState,
            busy: false,
            previousBusy: false,
        })).toBe(false);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.MIGRATION_CHECKS,
            stepState: pendingState,
            busy: false,
            previousBusy: true,
        })).toBe(true);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.MIGRATION_CHECKS,
            stepState: {
                ...pendingState,
                data: {
                    ...pendingState.data,
                    checkStatus: MigrationChecksStatus.ABORTED,
                },
            },
            busy: false,
            previousBusy: true,
        })).toBe(false);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.SCHEMA_SELECTION,
            stepState: {
                ...pendingState,
                data: {
                    ...pendingState.data,
                    checkStatus: MigrationChecksStatus.DONE,
                },
            },
            busy: false,
            previousBusy: false,
        })).toBe(false);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.SCHEMA_SELECTION,
            stepState: {
                ...pendingState,
                data: {
                    ...pendingState.data,
                    checkStatus: MigrationChecksStatus.ABORTED,
                },
            },
            busy: false,
            previousBusy: false,
        })).toBe(true);

        expect(shouldAutoRunMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousSubStepId: SubStepId.SCHEMA_SELECTION,
            stepState: {
                ...pendingState,
                data: {
                    ...pendingState.data,
                    checkStatus: MigrationChecksStatus.ERROR,
                },
            },
            busy: false,
            previousBusy: false,
        })).toBe(true);
    });

    it("auto-commits only when the sub-step becomes ready to commit", () => {
        const previousState = {
            status: MigrationStepStatus.IN_PROGRESS,
            errors: [],
            values: null,
            data: {
                issues: [],
                checkStatus: MigrationChecksStatus.DONE,
                checkProgress: {
                    completed: 0,
                    total: 0,
                    detail: "",
                    currentCheckTitle: "",
                    currentCheck: 1,
                    completedChecks: 0,
                    totalChecks: 1,
                },
            },
        };

        expect(shouldAutoCommitMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousStepState: previousState,
            stepState: {
                ...previousState,
                status: MigrationStepStatus.READY_TO_COMMIT,
            },
            busy: false,
        })).toBe(true);

        expect(shouldAutoCommitMigrationChecks({
            currentSubStepId: SubStepId.MIGRATION_CHECKS,
            previousStepState: {
                ...previousState,
                status: MigrationStepStatus.READY_TO_COMMIT,
            },
            stepState: {
                ...previousState,
                status: MigrationStepStatus.READY_TO_COMMIT,
            },
            busy: false,
        })).toBe(false);
    });

});

describe("MigrationChecksPoller", () => {
    it("keeps polling while checks remain running", async () => {
        vi.useFakeTimers();

        let running = false;
        const poll = vi.fn(() => {
            return Promise.resolve();
        });
        const poller = new MigrationChecksPoller({
            isRunning: () => {
                return running;
            },
            poll,
        });

        running = true;
        poller.sync(false, true);
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(1);

        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(2);

        running = false;
        poller.sync(true, false);
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(2);
    });

    it("does not reschedule after being stopped mid-poll", async () => {
        vi.useFakeTimers();

        let resolvePoll: (() => void) | undefined;
        const poll = vi.fn(() => {
            return new Promise<void>((resolve) => {
                resolvePoll = resolve;
            });
        });
        const poller = new MigrationChecksPoller({
            isRunning: () => {
                return true;
            },
            poll,
        });

        poller.sync(false, true);
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(1);

        poller.stop();
        resolvePoll?.();
        await Promise.resolve();
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(1);
    });

    it("waits until the app is idle before polling again", async () => {
        vi.useFakeTimers();

        let running = true;
        let busy = true;
        const poll = vi.fn(() => {
            return Promise.resolve();
        });
        const poller = new MigrationChecksPoller({
            isRunning: () => {
                return running;
            },
            canPoll: () => {
                return !busy;
            },
            poll,
        });

        poller.sync(false, true);
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).not.toHaveBeenCalled();

        busy = false;
        await vi.advanceTimersByTimeAsync(1000);
        expect(poll).toHaveBeenCalledTimes(1);

        running = false;
        poller.sync(true, false);
    });
});

describe("MigrationChecksView", () => {
    it("renders the idle state", () => {
        render(
            <MigrationChecksView
                busy={false}
                onAbort={onAbort}
                onRetry={onRetry}
            />
        );

        expect(screen.getByText("Compatibility and upgrade checks start automatically when this step opens."))
            .toBeTruthy();
        expect(screen.queryByText("Select the target MySQL version first.")).toBeNull();
        expect(screen.queryByText("Run Checks")).toBeNull();
    });

    it("renders progress with AnimatedProgressIndicator while checks are running", () => {
        const { container } = render(
            <MigrationChecksView
                busy={false}
                onAbort={onAbort}
                onRetry={onRetry}
                data={{
                    issues: [],
                    checkStatus: MigrationChecksStatus.RUNNING_UPGRADE_CHECKS,
                    checkProgress: {
                        completed: 50,
                        total: 100,
                        detail: "Collecting routines to check",
                        currentCheckTitle: "MySQL syntax check for routine-like objects",
                        currentCheck: 0,
                        completedChecks: 0,
                        totalChecks: 2,
                    },
                }}
            />
        );

        expect(screen.getByText("Running upgrade checks.")).toBeTruthy();
        expect(screen.getByText("Abort Checks")).toBeTruthy();
        expect(screen.getByText("50%")).toBeTruthy();
        expect(container.querySelector(".animated-progress-indicator")).toBeTruthy();
    });

    it("renders detected issues after checks complete", () => {
        const { container } = render(
            <MigrationChecksView
                busy={false}
                onAbort={onAbort}
                onRetry={onRetry}
                data={{
                    issues: [{
                        checkId: "check-1",
                        level: "WARNING" as const,
                        title: "Example issue",
                        result: "",
                        description: "",
                        objects: [],
                        choices: [],
                        status: 1,
                    }],
                    checkStatus: MigrationChecksStatus.DONE,
                    checkProgress: {
                        completed: 0,
                        total: 0,
                        detail: "",
                        currentCheckTitle: "",
                        currentCheck: 2,
                        completedChecks: 0,
                        totalChecks: 2,
                    },
                }}
                issues={<div>Issue list</div>}
            />
        );

        expect(screen.getByText("Compatibility issues were detected in the source database.")).toBeTruthy();
        expect(screen.getByText("Issue list")).toBeTruthy();
        expect(container.querySelector("[data-testid='migration-checks-done-with-issues']")).toBeTruthy();
    });

    it("renders a retry action after checks fail", () => {
        render(
            <MigrationChecksView
                busy={false}
                onAbort={onAbort}
                onRetry={onRetry}
                data={{
                    issues: [],
                    checkStatus: MigrationChecksStatus.ERROR,
                    checkProgress: {
                        completed: 0,
                        total: 0,
                        detail: "",
                        currentCheckTitle: "",
                        currentCheck: -1,
                        completedChecks: 0,
                        totalChecks: 0,
                    },
                }}
            />
        );

        fireEvent.click(screen.getByText("Retry Checks"));
        expect(onRetry).toHaveBeenCalledTimes(1);
    });
});
