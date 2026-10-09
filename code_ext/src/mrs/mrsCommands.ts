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

import {
    UI_BACKEND_SESSION,
    type ConnectionManager,
} from "../connections/connectionManager.js";
import { reportError } from "../errorMessages.js";
import type { IConnectionNode, IObjectNode, ISchemaNode } from "../tree/connectionsModel.js";
import type {
    IMrsAuthAppGroupNode,
    IMrsAuthAppNode,
    IMrsContentFileNode,
    IMrsContentSetNode,
    IMrsDaemonNode,
    IMrsNodeBase,
    IMrsObjectNode,
    IMrsRootNode,
    IMrsSchemaNode,
    IMrsServiceAuthAppNode,
    IMrsServiceNode,
    IMrsUserNode,
    MrsModel,
} from "../tree/mrsModel.js";
import {
    buildDocument,
    camelCase,
    defaultClassName,
    loadReference,
    referencesToLoad,
    updateField,
    type IMappingDocument,
} from "./dataMapping.js";
import type {
    IMrsScope,
    IMrsSdkOptions,
    MrsApi,
    MrsToolsApi,
} from "./mrsApi.js";
import {
    APP_BASE_CLASSES,
    authAppDefaults,
    authAppStatements,
    configureContextOf,
    configureDefaults,
    configureStatements,
    contentSetDefaults,
    contentSetStatements,
    DEFAULT_METADATA_SCHEMA,
    objectDefaults,
    objectStatements,
    schemaDefaults,
    schemaStatements,
    SDK_LANGUAGES,
    serviceDefaults,
    serviceStatements,
    userDefaults,
    userStatements,
    type IAuthAppContext,
    type IAuthAppDialogValues,
    type IConfigureContext,
    type IConfigureValues,
    type IContentSetContext,
    type IContentSetDialogValues,
    type IObjectContext,
    type IObjectDialogValues,
    type ISchemaContext,
    type ISchemaDialogValues,
    type ISdkExportValues,
    type IServiceContext,
    type IServiceDialogValues,
    type IUserContext,
    type IUserDialogValues,
} from "./mrsDialogs.js";
import { MrsDialogPanel, type IMrsDialogSpec } from "./mrsDialogPanel.js";
import type {
    IMrsDataMappingField,
    IMrsService,
    IMrsStatus,
    MrsObjectType,
} from "./mrsTypes.js";
import {
    dropAuthAppSql,
    dropContentFileSql,
    dropContentSetSql,
    dropDaemonSql,
    dropObjectSql,
    dropSchemaSql,
    dropServiceSql,
    dropUserSql,
    linkAuthAppSql,
    objectKeyword,
    quoteRequestPath,
    quoteServicePath,
    serviceSchemaSelector,
    useServiceSql,
    userRef,
    quoteText,
} from "./restSql.js";

/**
 * The commands of the REST Service rows, and of the database rows that
 * add to a REST service: what the MySQL Shell for VS Code extension
 * offers on its MySQL REST Service tree, through REST SQL and the `mrs.*`
 * tools.
 */

/** Where the MariaDB REST Service documentation is. */
export const MRS_DOCS_URL =
    "https://mariadb.com/docs/tools/mariadb-shell/mariadb-rest-service";

/** What the commands need from the extension. */
export interface IMrsCommandsHost {
    extensionUri: vscode.Uri;
    api: MrsApi;
    tools(): Promise<MrsToolsApi>;
    model: MrsModel;
    connections: ConnectionManager;
    /** Redraws the Connections view. */
    refresh(): void;
    log(message: string): void;
}

/** The setting naming where REST requests reach the REST Daemon. */
const DAEMON_URL_SETTING = "mrs.restDaemonUrl";

/**
 * @returns Where the REST Daemon serves, from the settings:
 *          `https://localhost:8443` unless set.
 */
export const restDaemonUrl = (): string => {
    const url = vscode.workspace.getConfiguration("mariadb")
        .get<string>(DAEMON_URL_SETTING, "https://localhost:8443");

    return url.replace(/\/+$/, "");
};

/**
 * @param service A service.
 *
 * @returns The path its requests go to: its context root, which a
 *          developer service shares with the published one.
 */
const servicePathOf = (service: IMrsService): string => {
    return service.url_context_root;
};

/**
 * Asks before something is removed, as the MySQL Shell does: a modal
 * warning whose button says what it does.
 *
 * @param message The question.
 * @param action The button.
 *
 * @returns Whether to go ahead.
 */
const confirm = async (message: string, action: string): Promise<boolean> => {
    const answer = await vscode.window.showWarningMessage(message,
        { modal: true, detail: "This operation cannot be reverted!" }, action);

    return answer === action;
};

/** The MRS commands, registered by {@link MrsCommands.register}. */
export class MrsCommands {
    public constructor(private readonly host: IMrsCommandsHost) { }

    /**
     * @returns The commands, to dispose with the extension.
     */
    public register(): vscode.Disposable[] {
        const commands: Record<string, (...args: never[]) => Promise<void>> = {
            "mariadb.mrs.configure": this.configure,
            "mariadb.mrs.showPrivateItems": this.showPrivateItems,
            "mariadb.mrs.hidePrivateItems": this.hidePrivateItems,
            "mariadb.mrs.docs": this.docs,
            "mariadb.mrs.addService": this.addService,
            "mariadb.mrs.editService": this.editService,
            "mariadb.mrs.deleteService": this.deleteService,
            "mariadb.mrs.setCurrentService": this.setCurrentService,
            "mariadb.mrs.linkAuthApp": this.linkAuthApp,
            "mariadb.mrs.addAndLinkAuthApp": this.addAndLinkAuthApp,
            "mariadb.mrs.unlinkAuthApp": this.unlinkAuthApp,
            "mariadb.mrs.copyCreateServiceSql": this.copyCreateServiceSql,
            "mariadb.mrs.copyCreateServiceSqlIncludeDatabaseEndpoints":
                this.copyCreateServiceSqlWithEndpoints,
            "mariadb.mrs.dumpCreateServiceSql": this.dumpCreateServiceSql,
            "mariadb.mrs.exportServiceSdk": this.exportServiceSdk,
            "mariadb.mrs.dumpServiceAsProject": this.dumpServiceAsProject,
            "mariadb.mrs.loadProjectFromDisk": this.loadProjectFromDisk,
            "mariadb.mrs.loadServiceFromDisk": this.loadServiceFromDisk,
            "mariadb.mrs.addSchema": this.addSchema,
            "mariadb.mrs.editSchema": this.editSchema,
            "mariadb.mrs.deleteSchema": this.deleteSchema,
            "mariadb.mrs.copyCreateSchemaSql": this.copyCreateSchemaSql,
            "mariadb.mrs.dumpCreateSchemaSql": this.dumpCreateSchemaSql,
            "mariadb.mrs.addDbObject": this.addDbObject,
            "mariadb.mrs.editDbObject": this.editDbObject,
            "mariadb.mrs.deleteDbObject": this.deleteDbObject,
            "mariadb.mrs.copyDbObjectRequestPath": this.copyDbObjectRequestPath,
            "mariadb.mrs.openDbObjectRequestPath": this.openDbObjectRequestPath,
            "mariadb.mrs.copyCreateDbObjectSql": this.copyCreateDbObjectSql,
            "mariadb.mrs.dumpCreateDbObjectSql": this.dumpCreateDbObjectSql,
            "mariadb.mrs.addContentSet": this.addContentSet,
            "mariadb.mrs.addFolderAsContentSet": this.addFolderAsContentSet,
            "mariadb.mrs.editContentSet": this.editContentSet,
            "mariadb.mrs.deleteContentSet": this.deleteContentSet,
            "mariadb.mrs.openContentSetRequestPath":
                this.openContentSetRequestPath,
            "mariadb.mrs.copyCreateContentSetSql": this.copyCreateContentSetSql,
            "mariadb.mrs.dumpCreateContentSetSql": this.dumpCreateContentSetSql,
            "mariadb.mrs.openContentFileRequestPath":
                this.openContentFileRequestPath,
            "mariadb.mrs.copyCreateContentFileSql":
                this.copyCreateContentFileSql,
            "mariadb.mrs.deleteContentFile": this.deleteContentFile,
            "mariadb.mrs.addAuthApp": this.addAuthApp,
            "mariadb.mrs.editAuthApp": this.editAuthApp,
            "mariadb.mrs.deleteAuthApp": this.deleteAuthApp,
            "mariadb.mrs.copyCreateAuthAppSql": this.copyCreateAuthAppSql,
            "mariadb.mrs.dumpCreateAuthAppSql": this.dumpCreateAuthAppSql,
            "mariadb.mrs.addUser": this.addUser,
            "mariadb.mrs.editUser": this.editUser,
            "mariadb.mrs.deleteUser": this.deleteUser,
            "mariadb.mrs.copyCreateUserSql": this.copyCreateUserSql,
            "mariadb.mrs.dumpCreateUserSql": this.dumpCreateUserSql,
            "mariadb.mrs.deleteDaemon": this.deleteDaemon,
            "mariadb.mrs.rebuildMrsSdk": this.rebuildMrsSdk,
        };

        return Object.entries(commands).map(([id, handler]) => {
            return vscode.commands.registerCommand(id, async (...args: never[]) => {
                try {
                    await handler.apply(this, args);
                } catch (error) {
                    reportError(this.host.log, error);
                }
            });
        });
    }

    // --- helpers -----------------------------------------------------

    /**
     * @param node A REST Service row.
     *
     * @returns Where its calls go.
     */
    #scope(node: IMrsNodeBase): IMrsScope {
        const scope = this.host.model.scopeOf(node);
        if (scope === undefined) {
            throw new Error(`The connection ${node.uri} is not open.`);
        }

        return scope;
    }

    /**
     * The REST metadata of a connection the user picked a database row
     * on: the one there is, or the one picked where there are several.
     *
     * @param uri The connection.
     *
     * @returns Its root, or undefined where it has none or none was
     *          picked.
     */
    async #rootOf(uri: string): Promise<IMrsRootNode | undefined> {
        await this.host.connections.connect(uri, UI_BACKEND_SESSION);
        const roots = (await this.host.model.rootsOf(uri)).filter(
            (root): root is IMrsRootNode => { return root.kind === "mrsRoot"; });
        if (roots.length === 0) {
            void vscode.window.showErrorMessage("The MariaDB REST Service is "
                + "not configured on this connection. Use Configure "
                + "MariaDB REST Service first.");

            return undefined;
        }
        if (roots.length === 1) {
            return roots[0];
        }

        const picked = await vscode.window.showQuickPick(roots.map((root) => {
            return { label: root.metadataSchema ?? DEFAULT_METADATA_SCHEMA, root };
        }), { title: "Select the REST metadata schema" });

        return picked?.root;
    }

    /** Runs statements and redraws the tree. */
    async #run(scope: IMrsScope, statements: string[]): Promise<void> {
        await this.host.api.run(scope, statements);
        this.host.refresh();
    }

    #show(spec: IMrsDialogSpec): void {
        MrsDialogPanel.show(this.host.extensionUri, spec, {
            onSaved: (message) => {
                this.host.refresh();
                if (message !== undefined) {
                    void vscode.window.showInformationMessage(message);
                }
            },
            log: this.host.log,
        });
    }

    async #copy(text: string): Promise<void> {
        await vscode.env.clipboard.writeText(text);
        void vscode.window.showInformationMessage(
            "The CREATE statement was copied to the system clipboard");
    }

    /**
     * Writes REST SQL to a file the user picks.
     *
     * @param text The statements.
     * @param kind What they create, for the dialog: `Service`.
     * @param name The default file name, without `.mrs.sql`.
     *
     * @returns Nothing.
     */
    async #dump(text: string, kind: string, name: string): Promise<void> {
        const target = await vscode.window.showSaveDialog({
            title: `Export REST ${kind} SQL to file...`,
            saveLabel: "Export SQL File",
            defaultUri: vscode.Uri.file(`${process.env.HOME ?? ""}/${name}.mrs.sql`),
            filters: { "REST SQL": ["sql"] },
        });
        if (target === undefined) {
            return;
        }
        await vscode.workspace.fs.writeFile(target,
            new TextEncoder().encode(text.endsWith("\n") ? text : `${text}\n`));
        void vscode.window.showInformationMessage(
            `The REST ${kind} SQL was exported`);
    }

    /** Asks for confirmation, then drops what the statement drops. */
    async #drop(
        node: IMrsNodeBase,
        question: string,
        action: string,
        statement: string,
        done: string,
    ): Promise<void> {
        if (!await confirm(question, action)) {
            return;
        }
        await this.#run(this.#scope(node), [statement]);
        void vscode.window.showInformationMessage(done);
    }

    // --- metadata ----------------------------------------------------

    /**
     * Opens the configuration dialog: on a connection, for its REST
     * metadata - deploying it where there is none - and on a root, for
     * that root's.
     */
    public async configure(node?: IConnectionNode | IMrsRootNode): Promise<void> {
        if (node === undefined) {
            return;
        }
        const connectionId = await this.host.connections.connect(node.uri,
            UI_BACKEND_SESSION);

        let scope: IMrsScope = { connectionId };
        let status: IMrsStatus | undefined;
        if (node.kind === "mrsRoot") {
            scope = this.#scope(node);
            status = await this.host.api.status(scope);
        } else {
            const schemas = await this.host.api.metadataSchemas(connectionId);
            if (schemas.length === 1) {
                status = await this.host.api.status(scope);
            } else if (schemas.length > 1) {
                const NEW = "New REST metadata schema...";
                const picked = await vscode.window.showQuickPick([
                    ...schemas.map((schema) => { return schema.schema_name; }),
                    NEW,
                ], { title: "Select the REST metadata schema to configure" });
                if (picked === undefined) {
                    return;
                }
                if (picked !== NEW) {
                    scope = { connectionId, metadataSchema: picked };
                    status = await this.host.api.status(scope);
                }
            } else {
                status = await this.host.api.status(scope);
            }
        }

        // A server with several schemas reports one of them; a new one is
        // what the dialog is to deploy then.
        const effective: IMrsStatus = status ?? {
            service_configured: false,
            service_enabled: true,
            service_upgradeable: false,
            service_upgrade_ignored: false,
            service_count: 0,
            service_being_upgraded: false,
            major_upgrade_required: false,
            current_metadata_version: null,
            available_metadata_version: "",
            required_rest_daemon_version: "",
            metadata_version: null,
            metadata_schema: "",
        };
        const context = configureContextOf(effective);
        const values = configureDefaults(effective);
        if (status === undefined) {
            values.metadataSchema = "";
        }

        this.#show({
            dialog: "configure",
            title: context.init
                ? "Configure Instance for MariaDB REST Service Support"
                : "MariaDB REST Service Configuration",
            values,
            context,
            save: async (saved) => {
                const statements = configureStatements(
                    saved as IConfigureValues, context as IConfigureContext);
                // A new metadata schema is named by the statement itself,
                // and may not be chosen before it exists.
                await this.host.api.run(context.init
                    ? { connectionId: scope.connectionId } : scope, statements);

                return "MariaDB REST Service configured successfully.";
            },
        });
    }

    public async showPrivateItems(node?: IMrsRootNode): Promise<void> {
        if (node?.kind === "mrsRoot") {
            this.host.model.setShowPrivate(node, true);
            this.host.refresh();
        }
    }

    public async hidePrivateItems(node?: IMrsRootNode): Promise<void> {
        if (node?.kind === "mrsRoot") {
            this.host.model.setShowPrivate(node, false);
            this.host.refresh();
        }
    }

    public async docs(): Promise<void> {
        await vscode.env.openExternal(vscode.Uri.parse(MRS_DOCS_URL));
    }

    // --- services ----------------------------------------------------

    async #serviceDialog(
        node: IMrsNodeBase,
        service: IMrsService | undefined,
    ): Promise<void> {
        const scope = this.#scope(node);
        const allApps = (await this.host.api.authApps(scope)).map((app) => {
            return app.name;
        });
        const linked = service === undefined ? []
            : (await this.host.api.authApps(scope, service.full_service_path))
                .map((app) => { return app.name; });
        const context: IServiceContext = {
            allAuthApps: allApps,
            linkedAuthApps: linked,
            ...(service === undefined
                ? {} : { existingPath: service.full_service_path }),
        };

        this.#show({
            dialog: "service",
            title: service === undefined
                ? "Enter Configuration Values for the New REST Service"
                : "Adjust the REST Service Configuration",
            values: serviceDefaults(service, linked, allApps),
            context,
            save: async (values) => {
                await this.host.api.run(scope, serviceStatements(
                    values as IServiceDialogValues, context));

                return service === undefined
                    ? "The MRS service has been created."
                    : "The MRS service has been successfully updated.";
            },
        });
    }

    public async addService(node?: IMrsRootNode): Promise<void> {
        if (node !== undefined) {
            await this.#serviceDialog(node, undefined);
        }
    }

    public async editService(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind === "mrsService") {
            await this.#serviceDialog(node, node.service);
        }
    }

    public async deleteService(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        await this.#drop(node,
            `Are you sure the MRS service ${node.service.full_service_path} `
            + "should be deleted?",
            "Delete REST Service",
            dropServiceSql(node.service.full_service_path),
            "The MRS service has been deleted successfully.");
    }

    public async setCurrentService(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        await this.#run(this.#scope(node),
            [useServiceSql(node.service.full_service_path)]);
        void vscode.window.showInformationMessage(
            "The MRS service has been set as the new default service.");
    }

    public async linkAuthApp(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        const scope = this.#scope(node);
        const linked = new Set((await this.host.api.authApps(scope,
            node.service.full_service_path)).map((app) => { return app.name; }));
        const apps = (await this.host.api.authApps(scope)).filter((app) => {
            return !linked.has(app.name);
        });
        if (apps.length === 0) {
            void vscode.window.showInformationMessage("Every REST "
                + "authentication app is linked to this service already.");

            return;
        }
        const picked = await vscode.window.showQuickPick(apps.map((app) => {
            return { label: app.name, description: app.auth_vendor };
        }), {
            title: "Select a REST Authentication app to link it to this service.",
            placeHolder: "Type the name of an existing REST authentication app",
        });
        if (picked === undefined) {
            return;
        }
        await this.#run(scope, [linkAuthAppSql(node.service.full_service_path,
            picked.label, true)]);
        void vscode.window.showInformationMessage("The MRS Authentication App "
            + `has been linked to service ${node.service.full_service_path}`);
    }

    public async addAndLinkAuthApp(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind === "mrsService") {
            await this.#authAppDialog(node, undefined,
                node.service.full_service_path);
        }
    }

    public async unlinkAuthApp(node?: IMrsServiceAuthAppNode): Promise<void> {
        if (node?.kind !== "mrsServiceAuthApp") {
            return;
        }
        const app = node.authApp.name;
        const service = node.service.full_service_path;
        if (!await confirm(`Are you sure the MRS authentication app "${app}" `
            + `should be unlinked from the service "${service}"?`,
        "Unlink")) {
            return;
        }
        await this.#run(this.#scope(node),
            [linkAuthAppSql(service, app, false)]);
        void vscode.window.showInformationMessage(
            `The MRS Authentication App "${app}" has been unlinked.`);
    }

    async #createServiceSql(
        node: IMrsServiceNode,
        endpoints: string | undefined,
    ): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            `SHOW CREATE REST SERVICE ${quoteServicePath(
                node.service.full_service_path)}`
            + (endpoints === undefined ? "" : ` INCLUDING ${endpoints} ENDPOINTS`));
    }

    public async copyCreateServiceSql(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind === "mrsService") {
            await this.#copy(await this.#createServiceSql(node, undefined));
        }
    }

    public async copyCreateServiceSqlWithEndpoints(
        node?: IMrsServiceNode,
    ): Promise<void> {
        if (node?.kind === "mrsService") {
            await this.#copy(await this.#createServiceSql(node, "DATABASE"));
        }
    }

    public async dumpCreateServiceSql(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        const picked = await vscode.window.showQuickPick([
            { label: "Export SQL Script Including All Endpoints", value: "ALL" },
            {
                label: "Export SQL Script Including Database Endpoints Only",
                value: "DATABASE",
            },
            {
                label: "Export SQL Script Including Database and Static "
                    + "Endpoints Only",
                value: "DATABASE AND STATIC",
            },
        ], { title: "Select the endpoints to include" });
        if (picked === undefined) {
            return;
        }
        await this.#dump(await this.#createServiceSql(node, picked.value),
            "Service", camelCase(servicePathOf(node.service)));
    }

    public async exportServiceSdk(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        const scope = this.#scope(node);
        const service = node.service;
        const target = await vscode.window.showSaveDialog({
            title: "Export REST Service SDK Files...",
            saveLabel: "Export SDK Files",
            defaultUri: vscode.Uri.file(`${process.env.HOME ?? ""}/`
                + `${camelCase(servicePathOf(service))}.mrs.sdk`),
        });
        if (target === undefined) {
            return;
        }
        const tools = await this.host.tools();
        const stored = await tools.getSdkOptions(target.fsPath).catch(
            (): IMrsSdkOptions => { return {}; });
        const values: ISdkExportValues = {
            directory: target.fsPath,
            serviceUrl: stored.serviceUrl
                ?? `${restDaemonUrl()}${servicePathOf(service)}`,
            sdkLanguage: stored.sdkLanguage ?? "TypeScript",
            addAppBaseClass: stored.addAppBaseClass ?? "",
            header: stored.header ?? "",
        };

        this.#show({
            dialog: "sdkExport",
            title: `Export MRS SDK Files for ${service.full_service_path}`,
            values,
            context: { languages: SDK_LANGUAGES, baseClasses: APP_BASE_CLASSES },
            save: async (saved) => {
                const sdk = saved as ISdkExportValues;
                // The plugin works on the session's metadata schema.
                if (scope.metadataSchema !== undefined) {
                    await this.host.api.run(scope, []);
                }
                await tools.dumpSdkServiceFiles(scope.connectionId,
                    sdk.directory, {
                        url_context_root: service.full_service_path,
                        service_url: sdk.serviceUrl,
                        sdk_language: sdk.sdkLanguage,
                        ...(sdk.addAppBaseClass === ""
                            ? {} : { add_app_base_class: sdk.addAppBaseClass }),
                        ...(sdk.header === "" ? {} : { header: sdk.header }),
                    });

                return "MRS SDK Files exported successfully.";
            },
        });
    }

    public async dumpServiceAsProject(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind !== "mrsService") {
            return;
        }
        const scope = this.#scope(node);
        const target = await vscode.window.showSaveDialog({
            title: "Dump REST Project...",
            saveLabel: "Dump REST Project",
            defaultUri: vscode.Uri.file(`${process.env.HOME ?? ""}/`
                + `${camelCase(servicePathOf(node.service))}`),
        });
        if (target === undefined) {
            return;
        }
        const ask = async (prompt: string, value = ""): Promise<string | undefined> => {
            return await vscode.window.showInputBox({ prompt, value });
        };
        const name = await ask("Enter the name of the REST Project",
            camelCase(servicePathOf(node.service)));
        if (name === undefined) {
            return;
        }
        const description = await ask("Enter the description of the REST Project");
        const publisher = await ask("Enter the publisher of the REST Project");
        const version = await ask("Enter the REST Project version", "1.0.0");
        if (description === undefined || publisher === undefined
            || version === undefined) {
            return;
        }
        const icon = await vscode.window.showOpenDialog({
            title: "Select the icon of the REST Project (optional)",
            canSelectMany: false,
            filters: { Images: ["jpg", "jpeg", "png", "svg", "ico"] },
        });

        if (scope.metadataSchema !== undefined) {
            await this.host.api.run(scope, []);
        }
        await (await this.host.tools()).dumpServiceProject(scope.connectionId, {
            destination: target.fsPath,
            servicePath: node.service.full_service_path,
            name,
            description,
            publisher,
            version,
            ...(icon?.[0] === undefined ? {} : { iconPath: icon[0].fsPath }),
            zip: target.fsPath.toLowerCase().endsWith(".zip"),
        });
        void vscode.window.showInformationMessage(
            "The REST Project has been dumped successfully.");
    }

    public async loadProjectFromDisk(
        node?: IMrsRootNode | IMrsServiceNode,
    ): Promise<void> {
        if (node === undefined) {
            return;
        }
        const scope = this.#scope(node);
        const picked = await vscode.window.showOpenDialog({
            title: "Load REST Project",
            canSelectFiles: true,
            canSelectFolders: true,
            canSelectMany: false,
            filters: { "REST Project": ["zip"] },
        });
        if (picked?.[0] === undefined) {
            return;
        }
        if (scope.metadataSchema !== undefined) {
            await this.host.api.run(scope, []);
        }
        await (await this.host.tools()).loadServiceProject(scope.connectionId,
            picked[0].fsPath);
        this.host.refresh();
        void vscode.window.showInformationMessage(
            "The REST Project has been loaded successfully.");
    }

    public async loadServiceFromDisk(node?: IMrsRootNode): Promise<void> {
        if (node === undefined) {
            return;
        }
        const scope = this.#scope(node);
        const picked = await vscode.window.showOpenDialog({
            title: "Load REST Service SQL Script",
            canSelectMany: false,
            filters: { "REST SQL": ["sql"] },
        });
        if (picked?.[0] === undefined) {
            return;
        }
        const asPath = await vscode.window.showInputBox({
            prompt: "Create the REST service under another request path "
                + "(leave empty to use the one in the script)",
        });
        if (asPath === undefined) {
            return;
        }
        if (scope.metadataSchema !== undefined) {
            await this.host.api.run(scope, []);
        }
        await (await this.host.tools()).loadService(scope.connectionId,
            picked[0].fsPath, asPath.trim() === "" ? undefined : asPath.trim());
        this.host.refresh();
        void vscode.window.showInformationMessage(
            "The REST Service has been loaded successfully.");
    }

    // --- REST schemas -------------------------------------------------

    async #schemaDialog(
        node: IMrsNodeBase,
        dbSchema: string,
        service: IMrsService | undefined,
        schema: IMrsSchemaNode["schema"] | undefined,
    ): Promise<void> {
        const scope = this.#scope(node);
        const services = await this.host.api.services(scope);
        if (services.length === 0) {
            void vscode.window.showErrorMessage(
                "Please create a REST Service before adding a DB Schema.");

            return;
        }
        const context: ISchemaContext = {
            services: services.map((candidate) => {
                return candidate.full_service_path;
            }),
            ...(schema === undefined ? {} : { existingPath: schema.request_path }),
        };

        this.#show({
            dialog: "schema",
            title: schema === undefined
                ? "Enter Configuration Values for the New REST Schema"
                : "Adjust the REST Schema Configuration",
            values: schemaDefaults(services, schema, dbSchema,
                service?.full_service_path),
            context,
            save: async (values) => {
                await this.host.api.run(scope, schemaStatements(
                    values as ISchemaDialogValues, context));

                return schema === undefined
                    ? "The MRS schema has been added successfully."
                    : "The MRS schema has been updated successfully.";
            },
        });
    }

    /** Adds a database schema of the tree to a REST service. */
    public async addSchema(node?: ISchemaNode): Promise<void> {
        if (node?.kind !== "schema") {
            return;
        }
        const root = await this.#rootOf(node.uri);
        if (root !== undefined) {
            await this.#schemaDialog(root, node.schema, undefined, undefined);
        }
    }

    public async editSchema(node?: IMrsSchemaNode): Promise<void> {
        if (node?.kind === "mrsSchema") {
            await this.#schemaDialog(node, node.schema.name, node.service,
                node.schema);
        }
    }

    public async deleteSchema(node?: IMrsSchemaNode): Promise<void> {
        if (node?.kind !== "mrsSchema") {
            return;
        }
        await this.#drop(node,
            `Are you sure the MRS schema ${node.schema.request_path} should `
            + "be deleted?",
            "Delete REST Schema",
            dropSchemaSql(node.schema.request_path,
                node.service.full_service_path),
            "The MRS schema has been deleted successfully.");
    }

    async #createSchemaSql(node: IMrsSchemaNode): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            `SHOW CREATE REST SCHEMA ${quoteRequestPath(node.schema.request_path)}`
            + ` ON SERVICE ${quoteServicePath(node.service.full_service_path)}`);
    }

    public async copyCreateSchemaSql(node?: IMrsSchemaNode): Promise<void> {
        if (node?.kind === "mrsSchema") {
            await this.#copy(await this.#createSchemaSql(node));
        }
    }

    public async dumpCreateSchemaSql(node?: IMrsSchemaNode): Promise<void> {
        if (node?.kind === "mrsSchema") {
            await this.#dump(await this.#createSchemaSql(node), "Schema",
                `${camelCase(node.schema.request_path)}.`
                + camelCase(servicePathOf(node.service)));
        }
    }

    // --- REST objects -------------------------------------------------

    /**
     * Loads the referenced tables of every enabled reference of a
     * document, so the dialog shows what is stored.
     */
    async #loadStoredReferences(
        scope: IMrsScope,
        document: IMappingDocument,
        stored: IMrsDataMappingField[],
    ): Promise<IMappingDocument> {
        let fields = document.fields;
        const tables = [`${document.dbSchema}.${document.dbObject}`];
        for (let round = 0; round < 8; round += 1) {
            const pending = referencesToLoad(fields);
            if (pending.length === 0) {
                break;
            }
            for (const field of pending) {
                const mapping = field.reference!.mapping;
                const columns = await this.host.api.columns(scope, "TABLE",
                    mapping.referenced_schema, mapping.referenced_table);
                fields = updateField(fields, field.key, (current) => {
                    return loadReference(current, columns.columns ?? [],
                        stored, tables);
                });
            }
        }

        return { ...document, fields };
    }

    async #objectDialog(
        node: IMrsNodeBase,
        service: IMrsService,
        schema: IMrsSchemaNode["schema"],
        object: IMrsObjectNode["object"] | undefined,
        newObject?: { name: string; objectType: string },
    ): Promise<void> {
        const scope = this.#scope(node);
        const objectType = object?.object_type ?? newObject!.objectType;
        const dbObject = object?.name ?? newObject!.name;
        const dbSchema = object?.schema_name ?? schema.name;
        const stored = object === undefined ? undefined
            : await this.host.api.object(scope, object.object_type,
                service.full_service_path, schema.request_path,
                object.request_path);
        const columnsKind = objectType === "PROCEDURE" || objectType === "FUNCTION"
            ? objectType : "TABLE";
        const columns = await this.host.api.columns(scope,
            objectType === "VIEW" ? "VIEW" : columnsKind, dbSchema, dbObject);
        const mappings = stored?.data_mappings ?? [];
        let document = buildDocument(objectType, dbSchema, dbObject, columns,
            mappings, defaultClassName(service.full_service_path,
                schema.request_path, dbObject));
        const storedFields = mappings.flatMap((mapping) => {
            return mapping.fields;
        });
        document = await this.#loadStoredReferences(scope, document,
            storedFields);

        const services = await this.host.api.services(scope);
        const schemas: Record<string, string[]> = {};
        for (const candidate of services) {
            schemas[candidate.full_service_path] = (await this.host.api.schemas(
                scope, candidate.full_service_path)).map((item) => {
                return item.request_path;
            });
        }
        const context: IObjectContext = {
            services: services.map((candidate) => {
                return candidate.full_service_path;
            }),
            schemas,
            ...(object === undefined ? {} : { existingPath: object.request_path }),
        };

        this.#show({
            dialog: "object",
            title: object === undefined
                ? "Enter Configuration Values for the New REST Object"
                : "Adjust the REST Object Configuration",
            values: objectDefaults(stored ?? {
                name: dbObject,
                object_type: objectType as MrsObjectType,
            }, document, service.full_service_path, schema.request_path),
            context,
            save: async (values) => {
                await this.host.api.run(scope, objectStatements(
                    values as IObjectDialogValues, context));

                return `The MRS Database Object ${dbObject} was successfully `
                    + `${object === undefined ? "created" : "updated"}.`;
            },
            loadColumns: async (refSchema, table) => {
                return await this.host.api.columns(scope, "TABLE", refSchema,
                    table);
            },
        });
    }

    /** Adds a table, view, procedure or function of the tree to REST. */
    public async addDbObject(node?: IObjectNode): Promise<void> {
        if (node?.kind !== "object") {
            return;
        }
        const objectType = node.objectType.toUpperCase();
        if (!["TABLE", "VIEW", "PROCEDURE", "FUNCTION"].includes(objectType)) {
            void vscode.window.showErrorMessage(`The database object type `
                + `'${node.objectType}' is not supported at this time`);

            return;
        }
        const root = await this.#rootOf(node.uri);
        if (root === undefined) {
            return;
        }
        const scope = this.#scope(root);
        const services = await this.host.api.services(scope);
        if (services.length === 0) {
            void vscode.window.showErrorMessage(
                "Please create a REST Service before adding DB Objects.");

            return;
        }
        let service = services.length === 1 ? services[0]
            : services.find((candidate) => { return candidate.is_current === true; });
        if (service === undefined) {
            const picked = await vscode.window.showQuickPick(
                services.map((candidate) => {
                    return { label: candidate.full_service_path, candidate };
                }), { title: "Select the REST service to add the object to" });
            if (picked === undefined) {
                void vscode.window.showErrorMessage("No REST Service selected.");

                return;
            }
            service = picked.candidate;
        }

        let schema = (await this.host.api.schemas(scope,
            service.full_service_path)).find((candidate) => {
            return candidate.name === node.schema;
        });
        if (schema === undefined) {
            const answer = await vscode.window.showInformationMessage(
                `The database schema ${node.schema} has not been added to the `
                + "REST Service. Do you want to add the schema now?",
                { modal: true }, "Yes");
            if (answer !== "Yes") {
                return;
            }
            await this.host.api.run(scope, [`CREATE REST SCHEMA `
                + `${quoteRequestPath(`/${camelCase(node.schema)}`)} ON SERVICE `
                + `${quoteServicePath(service.full_service_path)} FROM `
                + `\`${node.schema.replaceAll("`", "``")}\`;`]);
            this.host.refresh();
            schema = (await this.host.api.schemas(scope,
                service.full_service_path)).find((candidate) => {
                return candidate.name === node.schema;
            });
            if (schema === undefined) {
                return;
            }
        }

        await this.#objectDialog(root, service, schema, undefined,
            { name: node.name, objectType });
    }

    public async editDbObject(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind === "mrsObject") {
            await this.#objectDialog(node, node.service, node.schema,
                node.object);
        }
    }

    public async deleteDbObject(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind !== "mrsObject") {
            return;
        }
        await this.#drop(node,
            "Are you sure you want to delete the REST DB Object "
            + `${node.object.request_path}?`,
            "Delete DB Object",
            dropObjectSql(node.object.object_type, node.object.request_path,
                node.service.full_service_path, node.schema.request_path),
            `The REST DB Object ${node.object.request_path} has been deleted.`);
    }

    #objectUrl(node: IMrsObjectNode): string {
        return `${restDaemonUrl()}${servicePathOf(node.service)}`
            + `${node.schema.request_path}${node.object.request_path}`;
    }

    public async copyDbObjectRequestPath(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind === "mrsObject") {
            await vscode.env.clipboard.writeText(this.#objectUrl(node));
            void vscode.window.showInformationMessage(
                "The DB Object Path was copied to the system clipboard");
        }
    }

    public async openDbObjectRequestPath(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind === "mrsObject") {
            await vscode.env.openExternal(vscode.Uri.parse(this.#objectUrl(node)));
        }
    }

    async #createObjectSql(node: IMrsObjectNode): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            `SHOW CREATE REST ${objectKeyword(node.object.object_type)} `
            + quoteRequestPath(node.object.request_path)
            + serviceSchemaSelector(node.service.full_service_path,
                node.schema.request_path));
    }

    public async copyCreateDbObjectSql(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind === "mrsObject") {
            await this.#copy(await this.#createObjectSql(node));
        }
    }

    public async dumpCreateDbObjectSql(node?: IMrsObjectNode): Promise<void> {
        if (node?.kind === "mrsObject") {
            await this.#dump(await this.#createObjectSql(node), "Object",
                `${camelCase(node.object.request_path)}.`
                + camelCase(node.schema.request_path));
        }
    }

    // --- content sets ------------------------------------------------

    async #contentSetDialog(
        node: IMrsNodeBase,
        service: IMrsService | undefined,
        contentSet: IMrsContentSetNode["contentSet"] | undefined,
        directory = "",
    ): Promise<void> {
        const scope = this.#scope(node);
        const services = await this.host.api.services(scope);
        if (services.length === 0) {
            void vscode.window.showErrorMessage(
                "Please create a REST Service before adding a content set.");

            return;
        }
        const context: IContentSetContext = {
            services: services.map((candidate) => {
                return candidate.full_service_path;
            }),
            ...(contentSet === undefined
                ? {} : { existingPath: contentSet.request_path }),
        };
        const tools = await this.host.tools();

        this.#show({
            dialog: "contentSet",
            title: contentSet === undefined
                ? "Enter Configuration Values for the New MRS Static Content Set"
                : "Adjust the MRS Static Content Set Configuration",
            values: contentSetDefaults(services, contentSet,
                service?.full_service_path, directory),
            context,
            save: async (saved) => {
                const values = saved as IContentSetDialogValues;
                if (contentSet !== undefined) {
                    await this.host.api.run(scope, contentSetStatements(
                        values, context, contentSet));

                    return "The MRS static content set has been updated.";
                }

                if (scope.metadataSchema !== undefined) {
                    await this.host.api.run(scope, []);
                }
                const upload = await tools.loadContentSet(scope.connectionId,
                    values.directory, values.requestPath.trim(),
                    values.servicePath, values.ignoreList,
                    values.loadScripts ? undefined : false);
                // The upload makes the set as the plugin does; its settings
                // are this dialog's, applied once it exists.
                const created = (await this.host.api.contentSets(scope,
                    values.servicePath)).find((candidate) => {
                    return candidate.request_path === values.requestPath.trim();
                });
                await this.host.api.run(scope, contentSetStatements(values,
                    { ...context, existingPath: values.requestPath.trim() },
                    created));

                return "The MRS static content set has been added "
                    + `successfully. ${upload.files.length} file(s) have been `
                    + "uploaded";
            },
            analyzeFolder: async (folder, ignoreList) => {
                const language = await tools.folderScriptLanguage(folder,
                    ignoreList);
                if (language === undefined) {
                    return {};
                }

                return {
                    language,
                    definitions: await tools.folderScriptDefinitions(folder,
                        ignoreList),
                };
            },
        });
    }

    public async addContentSet(node?: IMrsServiceNode): Promise<void> {
        if (node?.kind === "mrsService") {
            await this.#contentSetDialog(node, node.service, undefined);
        }
    }

    /** Uploads a folder of the Explorer as a content set. */
    public async addFolderAsContentSet(folder?: vscode.Uri): Promise<void> {
        if (folder === undefined) {
            return;
        }
        const connected = this.host.connections.openConnections;
        if (connected.length === 0) {
            void vscode.window.showErrorMessage("Please open a connection with "
                + "the MariaDB REST Service configured in the Connections "
                + "view first.");

            return;
        }
        const uri = connected.length === 1 ? connected[0]
            : (await vscode.window.showQuickPick(connected, {
                title: "Select the connection to upload the folder to",
            }));
        if (uri === undefined) {
            return;
        }
        const root = await this.#rootOf(uri);
        if (root !== undefined) {
            await this.#contentSetDialog(root, undefined, undefined,
                folder.fsPath);
        }
    }

    public async editContentSet(node?: IMrsContentSetNode): Promise<void> {
        if (node?.kind === "mrsContentSet") {
            await this.#contentSetDialog(node, node.service, node.contentSet);
        }
    }

    public async deleteContentSet(node?: IMrsContentSetNode): Promise<void> {
        if (node?.kind !== "mrsContentSet") {
            return;
        }
        await this.#drop(node,
            "Are you sure you want to drop the static content set "
            + `${node.contentSet.request_path}?`,
            "Delete Static Content Set",
            dropContentSetSql(node.contentSet.request_path,
                node.service.full_service_path),
            "The MRS static content set has been deleted successfully.");
    }

    public async openContentSetRequestPath(
        node?: IMrsContentSetNode,
    ): Promise<void> {
        if (node?.kind === "mrsContentSet") {
            await vscode.env.openExternal(vscode.Uri.parse(
                `${restDaemonUrl()}${servicePathOf(node.service)}`
                + `${node.contentSet.request_path}/`));
        }
    }

    async #createContentSetSql(node: IMrsContentSetNode): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            "SHOW CREATE REST CONTENT SET "
            + `${quoteRequestPath(node.contentSet.request_path)} ON SERVICE `
            + quoteServicePath(node.service.full_service_path));
    }

    public async copyCreateContentSetSql(
        node?: IMrsContentSetNode,
    ): Promise<void> {
        if (node?.kind === "mrsContentSet") {
            await this.#copy(await this.#createContentSetSql(node));
        }
    }

    public async dumpCreateContentSetSql(
        node?: IMrsContentSetNode,
    ): Promise<void> {
        if (node?.kind === "mrsContentSet") {
            await this.#dump(await this.#createContentSetSql(node),
                "Content Set", `${camelCase(node.contentSet.request_path)}.`
                + camelCase(servicePathOf(node.service)));
        }
    }

    public async openContentFileRequestPath(
        node?: IMrsContentFileNode,
    ): Promise<void> {
        if (node?.kind === "mrsContentFile") {
            await vscode.env.openExternal(vscode.Uri.parse(
                `${restDaemonUrl()}${servicePathOf(node.service)}`
                + `${node.contentSet.request_path}${node.file.request_path}`));
        }
    }

    public async copyCreateContentFileSql(
        node?: IMrsContentFileNode,
    ): Promise<void> {
        if (node?.kind === "mrsContentFile") {
            await this.#copy(await this.host.api.text(this.#scope(node),
                "SHOW CREATE REST CONTENT FILE "
                + `${quoteRequestPath(node.file.request_path)} ON SERVICE `
                + `${quoteServicePath(node.service.full_service_path)} `
                + "CONTENT SET "
                + quoteRequestPath(node.contentSet.request_path)));
        }
    }

    public async deleteContentFile(node?: IMrsContentFileNode): Promise<void> {
        if (node?.kind !== "mrsContentFile") {
            return;
        }
        await this.#drop(node,
            `Are you sure you want to delete the content file `
            + `${node.file.request_path}?`,
            "Delete Content File",
            dropContentFileSql(node.file.request_path,
                node.service.full_service_path, node.contentSet.request_path),
            "The MRS content file has been deleted successfully.");
    }

    // --- auth apps and users -----------------------------------------

    async #authAppDialog(
        node: IMrsNodeBase,
        app: IMrsAuthAppNode["authApp"] | undefined,
        linkToService?: string,
    ): Promise<void> {
        const scope = this.#scope(node);
        const [vendors, roles] = [
            await this.host.api.authVendors(scope),
            await this.host.api.roles(scope),
        ];
        const context: IAuthAppContext = {
            vendors: vendors.map((vendor) => {
                return { id: vendor.id, name: vendor.name };
            }),
            roles: roles.map((role) => { return role.caption; }),
            hasSecret: app?.has_app_secret ?? false,
            ...(app === undefined ? {} : { existingName: app.name }),
            ...(linkToService === undefined ? {} : { linkToService }),
        };

        this.#show({
            dialog: "authApp",
            title: app === undefined
                ? "Enter Configuration Values for the New MRS Authentication App"
                : "Adjust the MRS Authentication App Configuration",
            values: authAppDefaults(app, roles),
            context,
            save: async (values) => {
                await this.host.api.run(scope, authAppStatements(
                    values as IAuthAppDialogValues, context));

                return app === undefined
                    ? "The MRS Authentication App has been added."
                    : "The MRS Authentication App has been updated.";
            },
        });
    }

    public async addAuthApp(node?: IMrsAuthAppGroupNode): Promise<void> {
        if (node !== undefined) {
            await this.#authAppDialog(node, undefined);
        }
    }

    public async editAuthApp(
        node?: IMrsAuthAppNode | IMrsServiceAuthAppNode,
    ): Promise<void> {
        if (node?.kind === "mrsAuthApp" || node?.kind === "mrsServiceAuthApp") {
            await this.#authAppDialog(node, node.authApp);
        }
    }

    public async deleteAuthApp(node?: IMrsAuthAppNode): Promise<void> {
        if (node?.kind !== "mrsAuthApp") {
            return;
        }
        await this.#drop(node,
            `Are you sure the MRS authentication app ${node.authApp.name} `
            + "should be deleted?",
            "Delete Authentication App",
            dropAuthAppSql(node.authApp.name),
            `The MRS Authentication App ${node.authApp.name} has been deleted.`);
    }

    async #createAuthAppSql(
        node: IMrsAuthAppNode | IMrsServiceAuthAppNode,
    ): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            `SHOW CREATE REST AUTH APP ${quoteText(node.authApp.name)}`);
    }

    public async copyCreateAuthAppSql(
        node?: IMrsAuthAppNode | IMrsServiceAuthAppNode,
    ): Promise<void> {
        if (node?.kind === "mrsAuthApp" || node?.kind === "mrsServiceAuthApp") {
            await this.#copy(await this.#createAuthAppSql(node));
        }
    }

    public async dumpCreateAuthAppSql(
        node?: IMrsAuthAppNode | IMrsServiceAuthAppNode,
    ): Promise<void> {
        if (node?.kind === "mrsAuthApp" || node?.kind === "mrsServiceAuthApp") {
            await this.#dump(await this.#createAuthAppSql(node),
                "Auth App", camelCase(node.authApp.name));
        }
    }

    async #userDialog(
        node: IMrsNodeBase,
        app: IMrsAuthAppNode["authApp"],
        user: IMrsUserNode["user"] | undefined,
    ): Promise<void> {
        const scope = this.#scope(node);
        const roles = await this.host.api.roles(scope);
        const full = user === undefined || user.name === null ? user
            : await this.host.api.user(scope, user.name, app.name);
        const defaultRole = roles.find((role) => {
            return role.id === app.default_role_id;
        })?.caption;
        const values = userDefaults(full, defaultRole);
        const context: IUserContext = {
            authApp: app.name,
            authAppVendorId: app.auth_vendor_id,
            allRoles: roles.map((role) => { return role.caption; }),
            existingRoles: user === undefined ? [] : values.roles,
            hasPassword: full?.has_password ?? false,
            ...(user?.name === undefined || user.name === null
                ? {} : { existingName: user.name }),
        };

        this.#show({
            dialog: "user",
            title: user === undefined
                ? "Enter new MariaDB REST User Values" : "Adjust the REST User",
            values,
            context,
            save: async (saved) => {
                const userValues = saved as IUserDialogValues;
                await this.host.api.run(scope, userStatements(userValues,
                    context));

                return `The MRS User "${userValues.name}" has been `
                    + `${user === undefined ? "added" : "updated"}.`;
            },
        });
    }

    public async addUser(node?: IMrsAuthAppNode): Promise<void> {
        if (node?.kind === "mrsAuthApp") {
            await this.#userDialog(node, node.authApp, undefined);
        }
    }

    public async editUser(node?: IMrsUserNode): Promise<void> {
        if (node?.kind === "mrsUser") {
            await this.#userDialog(node, node.authApp, node.user);
        }
    }

    public async deleteUser(node?: IMrsUserNode): Promise<void> {
        if (node?.kind !== "mrsUser" || node.user.name === null) {
            return;
        }
        await this.#drop(node,
            `Are you sure the MRS user ${node.user.name} should be deleted?`,
            "Delete User",
            dropUserSql(node.user.name, node.authApp.name),
            `The MRS User ${node.user.name} has been deleted.`);
    }

    async #createUserSql(node: IMrsUserNode): Promise<string> {
        return await this.host.api.text(this.#scope(node),
            `SHOW CREATE REST USER ${userRef(node.user.name ?? "",
                node.authApp.name)}`);
    }

    public async copyCreateUserSql(node?: IMrsUserNode): Promise<void> {
        if (node?.kind === "mrsUser") {
            await this.#copy(await this.#createUserSql(node));
        }
    }

    public async dumpCreateUserSql(node?: IMrsUserNode): Promise<void> {
        if (node?.kind === "mrsUser") {
            await this.#dump(await this.#createUserSql(node), "User",
                `${camelCase(node.user.name ?? "user")}.`
                + camelCase(node.authApp.name));
        }
    }

    // --- REST Daemons -------------------------------------------------

    public async deleteDaemon(node?: IMrsDaemonNode): Promise<void> {
        if (node?.kind !== "mrsDaemon") {
            return;
        }
        await this.#drop(node,
            `Are you sure the MariaDB REST Daemon ${node.daemon.address || node.daemon.name} `
            + "should be deleted?",
            "Delete REST Daemon",
            dropDaemonSql(node.daemon.id),
            "The MariaDB REST Daemon has been deleted successfully.");
    }

    // --- the Explorer --------------------------------------------------

    /** Writes an SDK folder's files again, with the options it keeps. */
    public async rebuildMrsSdk(folder?: vscode.Uri): Promise<void> {
        if (folder === undefined) {
            return;
        }
        const connected = this.host.connections.openConnections;
        const uri = connected.length === 1 ? connected[0]
            : await vscode.window.showQuickPick(connected, {
                title: "Select the connection the REST service is on",
            });
        if (uri === undefined) {
            void vscode.window.showErrorMessage("Please open a connection with "
                + "the MariaDB REST Service configured in the Connections "
                + "view first.");

            return;
        }
        const root = await this.#rootOf(uri);
        if (root === undefined) {
            return;
        }
        const scope = this.#scope(root);
        if (scope.metadataSchema !== undefined) {
            await this.host.api.run(scope, []);
        }
        await (await this.host.tools()).dumpSdkServiceFiles(scope.connectionId,
            folder.fsPath, {});
        void vscode.window.showInformationMessage(
            "MRS SDK Files exported successfully.");
    }
}
