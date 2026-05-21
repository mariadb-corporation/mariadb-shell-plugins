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

import "./MigrationChecksView.css";

import { ComponentChildren, FunctionalComponent } from "preact";

import { Container, Orientation } from "../../components/ui/Container/Container.js";
import { Button } from "../../components/ui/Button/Button.js";
import { AnimatedProgressIndicator } from "./AnimatedProgressIndicator.js";
import {
    getMigrationChecksProgress,
    getMigrationChecksProgressPercent,
    getMigrationChecksProgressText,
    IMigrationChecksDataRuntime,
    MigrationChecksStatus
} from "./MigrationChecksRuntime.js";

interface IMigrationChecksViewProps {
    data?: IMigrationChecksDataRuntime;
    busy: boolean;
    issues?: ComponentChildren;
    errors?: ComponentChildren;
    onAbort: () => void;
    onRetry: () => void;
}

export const MigrationChecksView: FunctionalComponent<IMigrationChecksViewProps> = ({
    data,
    busy,
    issues,
    errors,
    onAbort,
    onRetry,
}) => {
    const status = data?.checkStatus ?? MigrationChecksStatus.PENDING;
    const progress = getMigrationChecksProgress(data);
    const progressText = getMigrationChecksProgressText(data);
    const progressValue = getMigrationChecksProgressPercent(data);
    const issuesCount = data?.issues.length ?? 0;

    const isRunning = status === MigrationChecksStatus.RUNNING_COMPATIBILITY_CHECKS
        || status === MigrationChecksStatus.RUNNING_UPGRADE_CHECKS;

    return (
        <div className="migration-checks-view">
            {status === MigrationChecksStatus.PENDING && (
                <div className="migration-checks-state">
                    <p className="heading">
                        Compatibility and upgrade checks start automatically when this step opens.
                    </p>
                </div>
            )}

            {isRunning && (
                <div className="migration-checks-state">
                    <p className="heading">
                        {status === MigrationChecksStatus.RUNNING_COMPATIBILITY_CHECKS
                            ? "Running compatibility checks."
                            : "Running upgrade checks."}
                    </p>

                    <div className="progress-wrapper animated migration-checks-progress">
                        Progress:
                        <Container orientation={Orientation.LeftToRight} className="progress-line">
                            <AnimatedProgressIndicator
                                progress={progressValue}
                                width={300}
                                active={true}
                            />
                            <div>{progressValue.toFixed(0)}%</div>
                            {progressText && <div>{progressText}</div>}
                        </Container>
                    </div>

                    {progress.currentCheckTitle && (
                        <p className="comment">{progress.currentCheckTitle}</p>
                    )}
                    {progress.detail && (
                        <p className="comment">{progress.detail}</p>
                    )}
                </div>
            )}

            {status === MigrationChecksStatus.ABORTED && (
                <div className="migration-checks-state">
                    <p className="heading">Checks were aborted.</p>
                </div>
            )}

            {status === MigrationChecksStatus.ERROR && (
                <div className="migration-checks-state">
                    <p className="heading">Checks failed.</p>
                </div>
            )}

            {status === MigrationChecksStatus.DONE && issuesCount > 0 && (
                <div className="migration-checks-state">
                    <p className="heading">
                        Compatibility issues were detected in the source database.
                    </p>
                    <p className="heading">
                        Review the detected issues below, then click Next to apply the selected resolutions.
                    </p>
                </div>
            )}

            {status === MigrationChecksStatus.DONE && issuesCount === 0 && (
                <div className="migration-checks-state">
                    <p className="heading">
                        No compatibility or upgrade issues were detected.
                    </p>
                </div>
            )}

            {isRunning && (
                <div className="migration-checks-actions">
                    <Button
                        caption="Abort Checks"
                        disabled={busy}
                        onClick={onAbort}
                    />
                </div>
            )}

            {(status === MigrationChecksStatus.ABORTED
                || status === MigrationChecksStatus.ERROR) && (
                <div className="migration-checks-actions">
                    <Button
                        caption="Retry Checks"
                        disabled={busy}
                        onClick={onRetry}
                    />
                </div>
            )}

            {issues}
            {errors}
        </div>
    );
};
