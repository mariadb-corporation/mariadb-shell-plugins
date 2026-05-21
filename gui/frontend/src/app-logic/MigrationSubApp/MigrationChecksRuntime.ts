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

import {
    IMigrationChecksData,
    IMigrationPlanState,
    MigrationStepStatus,
    SubStepId
} from "../../communication/ProtocolMigration.js";

export const MigrationChecksStatus = {
    PENDING: "PENDING",
    RUNNING_UPGRADE_CHECKS: "RUNNING_UPGRADE_CHECKS",
    RUNNING_COMPATIBILITY_CHECKS: "RUNNING_COMPATIBILITY_CHECKS",
    DONE: "DONE",
    ABORTED: "ABORTED",
    ERROR: "ERROR",
} as const;

export type MigrationChecksStatusValue = typeof MigrationChecksStatus[keyof typeof MigrationChecksStatus];

export interface IMigrationChecksRequestControls {
    runChecks?: boolean;
    abortChecks?: boolean;
}

export interface IMigrationChecksPollerOptions {
    intervalMs?: number;
    isRunning: () => boolean;
    canPoll?: () => boolean;
    poll: () => Promise<void>;
    onError?: (error: unknown) => void;
}

export interface IMigrationCheckProgressRuntime {
    completed: number;
    total: number;
    detail: string;
    currentCheckTitle: string;
    currentCheck: number;
    completedChecks: number;
    totalChecks: number;
}

export interface IMigrationChecksDataRuntime extends Omit<IMigrationChecksData, "checkProgress" | "checkStatus"> {
    checkProgress: IMigrationCheckProgressRuntime;
    checkStatus: MigrationChecksStatusValue;
}

export type MigrationChecksStepState = Omit<IMigrationPlanState, "id"> | undefined;

interface IShouldAutoRunMigrationChecksOptions {
    currentSubStepId?: SubStepId;
    previousSubStepId?: SubStepId;
    stepState?: MigrationChecksStepState;
    busy: boolean;
    previousBusy: boolean;
}

interface IShouldAutoCommitMigrationChecksOptions {
    currentSubStepId?: SubStepId;
    previousStepState?: MigrationChecksStepState;
    stepState?: MigrationChecksStepState;
    busy: boolean;
}

export class MigrationChecksPoller {
    private readonly intervalMs: number;
    private timer?: number;
    private inFlight = false;
    private active = false;

    public constructor(private readonly options: IMigrationChecksPollerOptions) {
        this.intervalMs = options.intervalMs ?? 1000;
    }

    public sync(wasRunning: boolean, isRunning: boolean): void {
        this.active = isRunning;

        if (isRunning && !wasRunning) {
            this.schedule();
        } else if (!isRunning && wasRunning) {
            this.stop();
        }
    }

    public stop(): void {
        this.active = false;

        if (this.timer === undefined) {
            return;
        }

        window.clearTimeout(this.timer);
        this.timer = undefined;
    }

    private schedule(): void {
        if (!this.active || this.timer !== undefined) {
            return;
        }

        this.timer = window.setTimeout(() => {
            this.timer = undefined;
            void this.run();
        }, this.intervalMs);
    }

    private shouldContinuePolling(): boolean {
        return this.active && this.options.isRunning();
    }

    private async run(): Promise<void> {
        if (!this.active || this.inFlight || !this.options.isRunning()) {
            return;
        }

        if (!(this.options.canPoll?.() ?? true) && this.shouldContinuePolling()) {
            this.schedule();

            return;
        }

        try {
            this.inFlight = true;
            await this.options.poll();
        } catch (error) {
            this.options.onError?.(error);
        } finally {
            this.inFlight = false;
        }

        if (!this.shouldContinuePolling()) {
            return;
        }

        this.schedule();
    }
}

export const getMigrationChecksData = (
    stepState?: MigrationChecksStepState
): IMigrationChecksDataRuntime | undefined => {
    return stepState?.data as IMigrationChecksDataRuntime | undefined;
};

export const isMigrationChecksRunning = (
    stepState?: MigrationChecksStepState
): boolean => {
    const status = getMigrationChecksData(stepState)?.checkStatus;

    return status === MigrationChecksStatus.RUNNING_COMPATIBILITY_CHECKS
        || status === MigrationChecksStatus.RUNNING_UPGRADE_CHECKS;
};

export const canAdvanceFromMigrationChecks = (
    stepState?: MigrationChecksStepState
): boolean => {
    const data = getMigrationChecksData(stepState);

    return stepState?.status === MigrationStepStatus.FINISHED
        || data?.checkStatus === MigrationChecksStatus.DONE;
};

export const seedMigrationChecksIssueResolution = (
    issueResolution: Partial<Record<string, string>>,
    data: Pick<IMigrationChecksData, "issues">
): Partial<Record<string, string>> | undefined => {
    if (!data.issues.length) {
        return undefined;
    }

    const nextIssueResolution = { ...issueResolution };
    let changed = false;

    for (const issue of data.issues) {
        if (!issue.checkId || !issue.choices.length || nextIssueResolution[issue.checkId]) {
            continue;
        }

        nextIssueResolution[issue.checkId] = issue.choices[0];
        changed = true;
    }

    return changed ? nextIssueResolution : undefined;
};

export const shouldAutoRunMigrationChecks = ({
    currentSubStepId,
    previousSubStepId,
    stepState,
    busy,
    previousBusy,
}: IShouldAutoRunMigrationChecksOptions): boolean => {
    const enteredMigrationChecks = previousSubStepId !== SubStepId.MIGRATION_CHECKS;
    const shouldResumePendingChecks = previousBusy
        && (getMigrationChecksData(stepState)?.checkStatus ?? MigrationChecksStatus.PENDING)
            === MigrationChecksStatus.PENDING;

    return currentSubStepId === SubStepId.MIGRATION_CHECKS
        && (enteredMigrationChecks || shouldResumePendingChecks)
        && !busy
        && !canAdvanceFromMigrationChecks(stepState)
        && !isMigrationChecksRunning(stepState);
};

export const shouldAutoCommitMigrationChecks = ({
    currentSubStepId,
    previousStepState,
    stepState,
    busy,
}: IShouldAutoCommitMigrationChecksOptions): boolean => {
    return currentSubStepId === SubStepId.MIGRATION_CHECKS
        && !busy
        && previousStepState?.status !== MigrationStepStatus.READY_TO_COMMIT
        && stepState?.status === MigrationStepStatus.READY_TO_COMMIT;
};

export const getMigrationChecksProgress = (
    data?: IMigrationChecksDataRuntime
): IMigrationCheckProgressRuntime => {
    return data?.checkProgress ?? {
        completed: 0,
        total: 0,
        detail: "",
        currentCheckTitle: "",
        currentCheck: -1,
        completedChecks: 0,
        totalChecks: 0,
    };
};

export const getMigrationChecksProgressPercent = (
    data?: IMigrationChecksDataRuntime
): number => {
    if (!data) {
        return 0;
    }

    if (data.checkStatus === MigrationChecksStatus.DONE) {
        return 100;
    }

    const progress = getMigrationChecksProgress(data);
    if (progress.total > 0) {
        return Math.max(0, Math.min(100, progress.completed * 100 / progress.total));
    }

    if (progress.totalChecks > 0 && progress.currentCheck >= 0) {
        return Math.max(0, Math.min(100, progress.currentCheck * 100 / progress.totalChecks));
    }

    return 0;
};

export const getMigrationChecksProgressText = (
    data?: IMigrationChecksDataRuntime
): string => {
    const progress = getMigrationChecksProgress(data);

    if (progress.total > 0) {
        return `${progress.completed}/${progress.total}`;
    }

    if (progress.totalChecks > 0 && progress.currentCheck >= 0) {
        return `${Math.min(progress.currentCheck + 1, progress.totalChecks)}/${progress.totalChecks} checks`;
    }

    return "";
};
