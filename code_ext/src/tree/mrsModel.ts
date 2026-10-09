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

import { MrsApi, type IMrsScope } from "../mrs/mrsApi.js";
import {
    ENABLED_STATE,
    type IMrsAuthApp,
    type IMrsContentFile,
    type IMrsContentSet,
    type IMrsDaemon,
    type IMrsObject,
    type IMrsSchema,
    type IMrsService,
    type IMrsStatus,
    type IMrsUser,
} from "../mrs/mrsTypes.js";

/**
 * The MariaDB REST Service part of the Connections view: one root per REST
 * metadata schema on an open connection, and what hangs below it.
 *
 * Every node carries the connection's URI and the metadata schema it was
 * read from, which is what each later call needs: the tree asks the
 * connection manager for the session it browses with at the time of the
 * call, so a reconnect is followed. Free of `vscode`, like the rest of the
 * model.
 */

/** What every node below a REST Service root knows. */
export interface IMrsNodeBase {
    uri: string;
    /** Set where the server has several metadata schemas. */
    metadataSchema?: string;
}

/** The "MariaDB REST Service" row of a connection. */
export interface IMrsRootNode extends IMrsNodeBase {
    kind: "mrsRoot";
    status: IMrsStatus;
    /** Whether rows only the REST Daemon may serve are shown. */
    showPrivate: boolean;
}

/** A line of text standing in for what a root cannot show. */
export interface IMrsMessageNode extends IMrsNodeBase {
    kind: "mrsMessage";
    message: string;
}

export interface IMrsServiceNode extends IMrsNodeBase {
    kind: "mrsService";
    service: IMrsService;
    showPrivate: boolean;
}

export interface IMrsSchemaNode extends IMrsNodeBase {
    kind: "mrsSchema";
    service: IMrsService;
    schema: IMrsSchema;
    showPrivate: boolean;
}

export interface IMrsObjectNode extends IMrsNodeBase {
    kind: "mrsObject";
    service: IMrsService;
    schema: IMrsSchema;
    object: IMrsObject;
}

export interface IMrsContentSetNode extends IMrsNodeBase {
    kind: "mrsContentSet";
    service: IMrsService;
    contentSet: IMrsContentSet;
    showPrivate: boolean;
}

export interface IMrsContentFileNode extends IMrsNodeBase {
    kind: "mrsContentFile";
    service: IMrsService;
    contentSet: IMrsContentSet;
    file: IMrsContentFile;
}

/** An auth app linked to a service, shown under it. */
export interface IMrsServiceAuthAppNode extends IMrsNodeBase {
    kind: "mrsServiceAuthApp";
    service: IMrsService;
    authApp: IMrsAuthApp;
}

export interface IMrsAuthAppGroupNode extends IMrsNodeBase {
    kind: "mrsAuthAppGroup";
}

export interface IMrsAuthAppNode extends IMrsNodeBase {
    kind: "mrsAuthApp";
    authApp: IMrsAuthApp;
}

export interface IMrsUserNode extends IMrsNodeBase {
    kind: "mrsUser";
    authApp: IMrsAuthApp;
    user: IMrsUser;
}

export interface IMrsDaemonGroupNode extends IMrsNodeBase {
    kind: "mrsDaemonGroup";
    /** The oldest REST Daemon version the metadata works with. */
    requiredVersion: string;
}

export interface IMrsDaemonNode extends IMrsNodeBase {
    kind: "mrsDaemon";
    daemon: IMrsDaemon;
    requiresUpgrade: boolean;
}

/** A service a REST Daemon serves. */
export interface IMrsDaemonServiceNode extends IMrsNodeBase {
    kind: "mrsDaemonService";
    daemon: IMrsDaemon;
    service: IMrsService;
}

export type MrsNode =
    | IMrsRootNode
    | IMrsMessageNode
    | IMrsServiceNode
    | IMrsSchemaNode
    | IMrsObjectNode
    | IMrsContentSetNode
    | IMrsContentFileNode
    | IMrsServiceAuthAppNode
    | IMrsAuthAppGroupNode
    | IMrsAuthAppNode
    | IMrsUserNode
    | IMrsDaemonGroupNode
    | IMrsDaemonNode
    | IMrsDaemonServiceNode;

/** The kinds of {@link MrsNode}, to tell them from the other rows. */
const MRS_KINDS = new Set<string>([
    "mrsRoot", "mrsMessage", "mrsService", "mrsSchema", "mrsObject",
    "mrsContentSet", "mrsContentFile", "mrsServiceAuthApp",
    "mrsAuthAppGroup", "mrsAuthApp", "mrsUser", "mrsDaemonGroup",
    "mrsDaemon", "mrsDaemonService",
]);

/**
 * @param node Any row of the Connections view.
 *
 * @returns Whether it belongs to a REST Service root.
 */
export const isMrsNode = (node: { kind: string }): node is MrsNode => {
    return MRS_KINDS.has(node.kind);
};

/**
 * @param a A version, `26.10.0`.
 * @param b Another.
 *
 * @returns Negative, zero or positive as `a` is older, the same or newer.
 */
export const compareVersions = (a: string, b: string): number => {
    const parts = (version: string): number[] => {
        return version.split(/[.-]/).map((part) => {
            return Number.parseInt(part, 10) || 0;
        });
    };
    const left = parts(a);
    const right = parts(b);
    for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
        const difference = (left[i] ?? 0) - (right[i] ?? 0);
        if (difference !== 0) {
            return difference;
        }
    }

    return 0;
};

/** Whether a row is shown with private items hidden. */
const shown = (enabled: number, showPrivate: boolean): boolean => {
    return showPrivate || enabled !== ENABLED_STATE.private;
};

/**
 * Builds the REST Service rows of the Connections view.
 */
export class MrsModel {
    /** The roots showing private items, by URI and metadata schema. */
    readonly #privateShown = new Set<string>();

    /**
     * @param api The REST SQL calls.
     * @param connectionIdFor The connection the tree browses with on a
     *        URI; undefined while it is not open.
     * @param log Where a REST Service that cannot be read is reported. The
     *        rows are left out then: a server without REST metadata, or an
     *        account that cannot see it, is not an error of the tree's.
     */
    public constructor(
        private readonly api: MrsApi,
        private readonly connectionIdFor: (uri: string) => string | undefined,
        private readonly log: (message: string) => void = () => { /* */ },
    ) { }

    /**
     * @param node A row below a root.
     *
     * @returns Where its calls go, or undefined while its connection is
     *          not open.
     */
    public scopeOf(node: IMrsNodeBase): IMrsScope | undefined {
        const connectionId = this.connectionIdFor(node.uri);
        if (connectionId === undefined) {
            return undefined;
        }

        return node.metadataSchema === undefined
            ? { connectionId }
            : { connectionId, metadataSchema: node.metadataSchema };
    }

    /**
     * Shows or hides the private rows of a root.
     *
     * @param node The root.
     * @param show Whether to show them.
     *
     * @returns Nothing.
     */
    public setShowPrivate(node: IMrsNodeBase, show: boolean): void {
        const key = `${node.uri}\n${node.metadataSchema ?? ""}`;
        if (show) {
            this.#privateShown.add(key);
        } else {
            this.#privateShown.delete(key);
        }
    }

    #showsPrivate(node: IMrsNodeBase): boolean {
        return this.#privateShown.has(`${node.uri}\n${node.metadataSchema ?? ""}`);
    }

    /**
     * The REST Service roots of an open connection: none where it has no
     * REST metadata, one per metadata schema where it has several.
     *
     * @param uri The connection.
     *
     * @returns The roots, or a message where the metadata is being
     *          deployed.
     */
    public async rootsOf(
        uri: string,
    ): Promise<Array<IMrsRootNode | IMrsMessageNode>> {
        const connectionId = this.connectionIdFor(uri);
        if (connectionId === undefined) {
            return [];
        }

        try {
            const schemas = await this.api.metadataSchemas(connectionId);
            if (schemas.length === 0) {
                return [];
            }

            // Named only where there is a choice: the shell resolves a
            // single one by itself, and a USE before every call would cost
            // a round of its checks each time.
            const named = schemas.length > 1;
            const roots: Array<IMrsRootNode | IMrsMessageNode> = [];
            for (const schema of schemas) {
                const base: IMrsNodeBase = named
                    ? { uri, metadataSchema: schema.schema_name }
                    : { uri };
                const status = await this.api.status({
                    connectionId,
                    ...(named ? { metadataSchema: schema.schema_name } : {}),
                });
                if (status.service_being_upgraded) {
                    roots.push({
                        ...base,
                        kind: "mrsMessage",
                        message: "The REST metadata"
                            + (named ? ` in ${schema.schema_name}` : "")
                            + " is being deployed. Refresh once it is done.",
                    });
                    continue;
                }
                if (!status.service_configured) {
                    continue;
                }

                roots.push({
                    ...base,
                    kind: "mrsRoot",
                    status,
                    showPrivate: this.#showsPrivate(base),
                });
            }

            return roots;
        } catch (error) {
            this.log(`Could not read the REST Service of ${uri}: `
                + (error instanceof Error ? error.message : String(error)));

            return [];
        }
    }

    /**
     * @param node A REST Service row.
     *
     * @returns Its children.
     */
    public async getChildren(node: MrsNode): Promise<MrsNode[]> {
        const scope = this.scopeOf(node);
        if (scope === undefined) {
            return [];
        }
        const base: IMrsNodeBase = node.metadataSchema === undefined
            ? { uri: node.uri }
            : { uri: node.uri, metadataSchema: node.metadataSchema };

        switch (node.kind) {
            case "mrsRoot": {
                const services = await this.api.services(scope);

                return [
                    ...services.map((service): IMrsServiceNode => {
                        return {
                            ...base, kind: "mrsService", service,
                            showPrivate: node.showPrivate,
                        };
                    }),
                    {
                        ...base,
                        kind: "mrsDaemonGroup",
                        requiredVersion: node.status.required_rest_daemon_version,
                    },
                    { ...base, kind: "mrsAuthAppGroup" },
                ];
            }

            case "mrsService": {
                const path = node.service.full_service_path;
                const [schemas, contentSets, authApps] = [
                    await this.api.schemas(scope, path),
                    await this.api.contentSets(scope, path),
                    await this.api.authApps(scope, path),
                ];

                return [
                    ...schemas.filter((schema) => {
                        return shown(schema.enabled, node.showPrivate);
                    }).map((schema): IMrsSchemaNode => {
                        return {
                            ...base, kind: "mrsSchema", service: node.service,
                            schema, showPrivate: node.showPrivate,
                        };
                    }),
                    ...contentSets.filter((contentSet) => {
                        return shown(contentSet.enabled, node.showPrivate);
                    }).map((contentSet): IMrsContentSetNode => {
                        return {
                            ...base, kind: "mrsContentSet",
                            service: node.service, contentSet,
                            showPrivate: node.showPrivate,
                        };
                    }),
                    ...authApps.map((authApp): IMrsServiceAuthAppNode => {
                        return {
                            ...base, kind: "mrsServiceAuthApp",
                            service: node.service, authApp,
                        };
                    }),
                ];
            }

            case "mrsSchema": {
                const objects = await this.api.objects(scope,
                    node.service.full_service_path, node.schema.request_path);

                return objects.filter((object) => {
                    return shown(object.enabled, node.showPrivate);
                }).map((object): IMrsObjectNode => {
                    return {
                        ...base, kind: "mrsObject", service: node.service,
                        schema: node.schema, object,
                    };
                });
            }

            case "mrsContentSet": {
                const files = await this.api.contentFiles(scope,
                    node.service.full_service_path,
                    node.contentSet.request_path);

                return files.filter((file) => {
                    return shown(file.enabled, node.showPrivate);
                }).map((file): IMrsContentFileNode => {
                    return {
                        ...base, kind: "mrsContentFile",
                        service: node.service, contentSet: node.contentSet,
                        file,
                    };
                });
            }

            case "mrsAuthAppGroup": {
                const authApps = await this.api.authApps(scope);

                return authApps.map((authApp): IMrsAuthAppNode => {
                    return { ...base, kind: "mrsAuthApp", authApp };
                });
            }

            case "mrsAuthApp": {
                const users = await this.api.users(scope, node.authApp.name);

                return users.map((user): IMrsUserNode => {
                    return {
                        ...base, kind: "mrsUser", authApp: node.authApp, user,
                    };
                });
            }

            case "mrsDaemonGroup": {
                const daemons = await this.api.daemons(scope);

                return daemons.map((daemon): IMrsDaemonNode => {
                    return {
                        ...base, kind: "mrsDaemon", daemon,
                        requiresUpgrade: compareVersions(
                            node.requiredVersion, daemon.version) > 0,
                    };
                });
            }

            case "mrsDaemon": {
                const services = await this.api.servicesOfDaemon(scope,
                    node.daemon.id);

                return services.map((service): IMrsDaemonServiceNode => {
                    return {
                        ...base, kind: "mrsDaemonService",
                        daemon: node.daemon, service,
                    };
                });
            }

            default: {
                return [];
            }
        }
    }
}

/**
 * @param node A REST Service row.
 *
 * @returns Whether it has children to show.
 */
export const mrsHasChildren = (node: MrsNode): boolean => {
    switch (node.kind) {
        case "mrsRoot":
        case "mrsService":
        case "mrsSchema":
        case "mrsContentSet":
        case "mrsAuthAppGroup":
        case "mrsAuthApp":
        case "mrsDaemonGroup":
        case "mrsDaemon": {
            return true;
        }

        default: {
            return false;
        }
    }
};
