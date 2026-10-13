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

import * as vscode from "vscode";

import { ENABLED_STATE } from "../mrs/mrsTypes.js";
import { mrsHasChildren, type MrsNode } from "./mrsModel.js";
import type { IconResolver } from "./treeItems.js";

/**
 * The rows of the REST Service part of the Connections view. Their icons
 * are the MySQL Shell for VS Code extension's, and so is what each row
 * says; the names follow MariaDB's: a REST Daemon where that extension had
 * a MySQL Router.
 */

/** The caption of a REST Service root. */
export const MRS_ROOT_LABEL = "MariaDB REST Service";

/**
 * @param bytes A size.
 *
 * @returns It for a person: `1.4 KB`.
 */
export const formatBytes = (bytes: number): string => {
    const units = ["bytes", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }

    return unit === 0
        ? `${bytes} bytes`
        : `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
};

/**
 * @param enabled Whether the row's object is served.
 * @param requiresAuth Whether it needs a signed-in user.
 *
 * @returns The icon variant: `Disabled`, `Private`, `Locked` or none.
 */
export const accessSuffix = (enabled: number, requiresAuth: boolean): string => {
    if (enabled === ENABLED_STATE.disabled) {
        return "Disabled";
    }
    if (enabled === ENABLED_STATE.private) {
        return "Private";
    }

    return requiresAuth ? "Locked" : "";
};

/** `ENABLED`, `DISABLED` or `PRIVATE`, as a tooltip says it. */
const accessText = (enabled: number): string => {
    return enabled === ENABLED_STATE.private ? "PRIVATE"
        : enabled === ENABLED_STATE.disabled ? "DISABLED" : "ENABLED";
};

const isTrue = (value: boolean | number | null | undefined): boolean => {
    return value === true || value === 1;
};

/**
 * @param service A service.
 *
 * @returns The developers it is in development for; none where it is not.
 */
export const developersOf = (
    service: { in_development: { developers?: string[] } | null; developers?: string[] },
): string[] => {
    return service.in_development?.developers ?? service.developers ?? [];
};

/** A REST Service row. */
export class MrsTreeItem extends vscode.TreeItem {
    public constructor(
        public readonly node: MrsNode,
        resolveIcon: IconResolver,
    ) {
        super("", mrsHasChildren(node)
            ? vscode.TreeItemCollapsibleState.Collapsed
            : vscode.TreeItemCollapsibleState.None);

        const schemaPart = node.metadataSchema === undefined
            ? "" : `${node.metadataSchema}/`;
        const idOf = (...parts: string[]): string => {
            return ["mrs", node.uri, schemaPart, ...parts].join(":");
        };
        let icon: string | vscode.ThemeIcon = "mrs.svg";

        switch (node.kind) {
            case "mrsRoot": {
                this.label = MRS_ROOT_LABEL;
                this.description = node.metadataSchema;
                const status = node.status;
                icon = !status.service_enabled ? "mrsDisabled.svg"
                    : status.service_upgradeable && !status.service_upgrade_ignored
                        ? "mrsUpdateAvailable.svg" : "mrs.svg";
                this.contextValue = `mariadbMrsRoot.${node.showPrivate
                    ? "private" : "noPrivate"}`;
                this.tooltip = [
                    `${MRS_ROOT_LABEL}${node.metadataSchema === undefined
                        ? "" : ` (${node.metadataSchema})`}`,
                    `Metadata version ${status.current_metadata_version ?? "-"}`
                    + (status.service_upgradeable
                        && !status.service_upgrade_ignored
                        ? `, ${status.available_metadata_version} available`
                        : ""),
                    status.service_enabled ? "Enabled" : "Disabled",
                ].join("\n");
                this.id = idOf("root");
                break;
            }

            case "mrsMessage": {
                this.label = node.message;
                icon = new vscode.ThemeIcon("info");
                this.contextValue = "mariadbMrsMessage";
                break;
            }

            case "mrsService": {
                const service = node.service;
                const developers = developersOf(service);
                const enabled = service.enabled !== 0;
                this.label = service.url_context_root;
                this.description = enabled && developers.length > 0
                    ? `In Development [${developers.join(",")}]`
                    : !enabled ? "Disabled"
                        : isTrue(service.published) ? "Published" : "Unpublished";
                const variant = !enabled ? "Disabled"
                    : developers.length > 0 ? "InDevelopment"
                        : isTrue(service.published) ? "Published" : "";
                icon = service.is_current === true
                    ? `mrsServiceDefault${variant}.svg`
                    : `mrsService${variant}.svg`;
                this.contextValue = `mariadbMrsService.${service.is_current === true
                    ? "current" : "notCurrent"}`;
                this.tooltip = [
                    service.full_service_path,
                    ...(service.comments ? [service.comments] : []),
                    ...(service.is_current === true
                        ? ["The current REST service"] : []),
                ].join("\n");
                this.id = idOf("service", service.id);
                break;
            }

            case "mrsSchema": {
                const schema = node.schema;
                this.label = `${schema.request_path} (${schema.name})`;
                icon = `${schema.schema_type === "SCRIPT_MODULE"
                    ? "mrsSchemaModule" : "mrsSchema"}${accessSuffix(
                    schema.enabled, schema.requires_auth)}.svg`;
                this.contextValue = "mariadbMrsSchema";
                this.tooltip = schema.comments || undefined;
                this.id = idOf("schema", schema.id);
                break;
            }

            case "mrsObject": {
                const object = node.object;
                const type = object.object_type.charAt(0)
                    + object.object_type.slice(1).toLowerCase();
                this.label = object.request_path;
                this.description = object.name;
                icon = `mrsDbObject${type}${accessSuffix(object.enabled,
                    object.requires_auth)}.svg`;
                this.contextValue =
                    `mariadbMrsObject.${object.object_type.toLowerCase()}`;
                this.tooltip = object.comments || undefined;
                this.id = idOf("object", object.id);
                break;
            }

            case "mrsContentSet": {
                const contentSet = node.contentSet;
                this.label = contentSet.request_path;
                icon = `${contentSet.content_type === "SCRIPTS"
                    ? "mrsContentSetScripts" : "mrsContentSet"}${accessSuffix(
                    contentSet.enabled, contentSet.requires_auth)}.svg`;
                this.contextValue = "mariadbMrsContentSet";
                this.tooltip = contentSet.comments || undefined;
                this.id = idOf("contentSet", contentSet.id);
                break;
            }

            case "mrsContentFile": {
                const file = node.file;
                this.label = file.request_path;
                this.description = formatBytes(file.size);
                icon = file.enabled === ENABLED_STATE.private
                    ? "mrsContentFilePrivate.svg"
                    : file.enabled === ENABLED_STATE.enabled
                        ? "mrsContentFile.svg" : "mrsContentFileDisabled.svg";
                this.contextValue = "mariadbMrsContentFile";
                this.tooltip = `${file.request_path}\nAccess: `
                    + `${accessText(file.enabled)}\nAuthentication: `
                    + `${file.requires_auth ? "" : "NOT "}REQUIRED`;
                this.id = idOf("contentFile", file.id);
                break;
            }

            case "mrsServiceAuthApp": {
                const app = node.authApp;
                this.label = app.name;
                this.description = app.auth_vendor;
                icon = app.enabled ? "mrsAuthAppLink.svg"
                    : "mrsAuthAppLinkDisabled.svg";
                this.contextValue = "mariadbMrsServiceAuthApp";
                this.tooltip = app.description || app.name;
                this.id = idOf("serviceAuthApp", node.service.id, app.id);
                break;
            }

            case "mrsAuthAppGroup": {
                this.label = "REST Authentication Apps";
                icon = "mrsAuthApps.svg";
                this.contextValue = "mariadbMrsAuthAppGroup";
                this.id = idOf("authApps");
                break;
            }

            case "mrsAuthApp": {
                const app = node.authApp;
                this.label = app.name;
                this.description = app.auth_vendor;
                icon = app.enabled ? "mrsAuthApp.svg" : "mrsAuthAppDisabled.svg";
                this.contextValue = "mariadbMrsAuthApp";
                this.tooltip = app.description || app.name;
                this.id = idOf("authApp", app.id);
                break;
            }

            case "mrsUser": {
                const user = node.user;
                this.label = user.name ?? user.email ?? "<unknown>";
                this.description = user.login_permitted ? undefined : "Locked";
                icon = "mrsUser.svg";
                this.contextValue = "mariadbMrsUser";
                this.tooltip = [user.name, user.email].filter(Boolean).join("\n")
                    || undefined;
                this.id = idOf("user", user.id);
                break;
            }

            case "mrsDaemonGroup": {
                this.label = "REST Daemons";
                icon = "restDaemons.svg";
                this.contextValue = "mariadbMrsDaemonGroup";
                this.id = idOf("daemons");
                break;
            }

            case "mrsDaemon": {
                const daemon = node.daemon;
                this.label = daemon.address || daemon.name;
                this.description = daemon.developer
                    ? `[${daemon.developer}] ${daemon.version}` : daemon.version;
                icon = node.requiresUpgrade ? "restDaemonError.svg"
                    : daemon.active ? "restDaemon.svg" : "restDaemonNotActive.svg";
                this.contextValue = "mariadbMrsDaemon";
                this.tooltip = node.requiresUpgrade
                    ? "This MariaDB REST Daemon requires an upgrade."
                    : `${daemon.product_name} ${daemon.version} - `
                    + `${daemon.address}${daemon.active ? "" : " (not active)"}`;
                this.id = idOf("daemon", daemon.id);
                break;
            }

            default: {
                const service = node.service;
                const developers = developersOf(service);
                this.label = service.full_service_path;
                this.description = developers.length > 0 ? "In Development"
                    : isTrue(service.published) ? "Published" : "Unpublished";
                icon = developers.length > 0 ? "mrsServiceInDevelopment.svg"
                    : isTrue(service.published) ? "mrsServicePublished.svg"
                        : "mrsServiceLink.svg";
                this.contextValue = "mariadbMrsDaemonService";
                this.id = idOf("daemonService", node.daemon.id, service.id);
            }
        }

        this.iconPath = typeof icon === "string" ? resolveIcon(icon) : icon;
    }
}
