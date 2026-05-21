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

import { SubStepId } from "../../communication/ProtocolMigration.js";
import {
    canAdvanceFromMigrationChecks,
    getMigrationChecksData,
    IMigrationChecksDataRuntime,
    IMigrationChecksRequestControls,
    isMigrationChecksRunning,
    MigrationChecksPoller,
    MigrationChecksStepState,
    shouldAutoCommitMigrationChecks,
    shouldAutoRunMigrationChecks
} from "./MigrationChecksRuntime.js";

export interface IMigrationChecksControllerState {
    currentSubStepId?: SubStepId;
    backendRequestInProgress: boolean;
    stepState?: MigrationChecksStepState;
}

interface IMigrationChecksControllerOptions {
    getState: () => IMigrationChecksControllerState;
    updateStep: (controls?: IMigrationChecksRequestControls) => Promise<void>;
    pollStep?: () => Promise<void>;
    commitStep: () => Promise<void>;
    onError?: (error: unknown) => void;
}

export class MigrationChecksController {
    private readonly poller: MigrationChecksPoller;

    public constructor(private readonly options: IMigrationChecksControllerOptions) {
        this.poller = new MigrationChecksPoller({
            isRunning: () => {
                return isMigrationChecksRunning(this.options.getState().stepState);
            },
            canPoll: () => {
                return !this.options.getState().backendRequestInProgress;
            },
            poll: async () => {
                await (this.options.pollStep ?? this.options.updateStep)();
            },
            onError: options.onError,
        });
    }

    public sync(previousState: IMigrationChecksControllerState): void {
        const currentState = this.options.getState();

        this.poller.sync(
            isMigrationChecksRunning(previousState.stepState),
            isMigrationChecksRunning(currentState.stepState)
        );

        if (shouldAutoRunMigrationChecks({
            currentSubStepId: currentState.currentSubStepId,
            previousSubStepId: previousState.currentSubStepId,
            stepState: currentState.stepState,
            busy: currentState.backendRequestInProgress,
            previousBusy: previousState.backendRequestInProgress,
        })) {
            this.run();

            return;
        }

        if (shouldAutoCommitMigrationChecks({
            currentSubStepId: currentState.currentSubStepId,
            previousStepState: previousState.stepState,
            stepState: currentState.stepState,
            busy: currentState.backendRequestInProgress,
        })) {
            void this.options.commitStep();
        }
    }

    public stop(): void {
        this.poller.stop();
    }

    public canAdvance(stepState = this.options.getState().stepState): boolean {
        return canAdvanceFromMigrationChecks(stepState);
    }

    public getDisplayData(stepState = this.options.getState().stepState): IMigrationChecksDataRuntime | undefined {
        return getMigrationChecksData(stepState);
    }

    public readonly run = (): void => {
        void this.options.updateStep({ runChecks: true });
    };

    public readonly abort = (): void => {
        void this.options.updateStep({ abortChecks: true });
    };
}
