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

import {
    isOAuthVendor,
    type IMrsScriptDefinitions,
} from "../../../src/mrs/mrsTypes.js";
import {
    vendorIdOf,
    type IAuthAppContext,
    type IAuthAppDialogValues,
    type IConfigureContext,
    type IContentSetContext,
    type IObjectContext,
    type IObjectDialogValues,
    type ISchemaContext,
    type IServiceContext,
    type IUserContext,
    type MrsDialogKind,
} from "../../../src/mrs/mrsDialogs.js";
import { DataMappingEditor } from "./DataMappingEditor.js";
import {
    ACCESS_CHOICES,
    CheckBox,
    CheckList,
    PathField,
    Select,
    StringSelect,
    TextArea,
    TextField,
    type IFieldContext,
} from "./fields.js";

/**
 * What each MRS dialog shows: its tabs, which tab each field is on, and
 * the fields of each tab. The fields, hints and messages are the MySQL
 * Shell for VS Code extension's dialogs', as far as REST SQL can say the
 * same.
 */

/** What a dialog's tabs get besides the field context. */
export interface IDialogExtras {
    context: unknown;
    /** The dialog's fields as they are. */
    values: Record<string, unknown>;
    /** A folder's MRS scripts, as the host analysed them. */
    scripts?: {
        directory: string;
        language?: string;
        definitions?: IMrsScriptDefinitions;
        error?: string;
    };
}

/** One dialog. */
export interface IDialogDefinition {
    /** The tab without a strip above it; undefined to start on a tab. */
    main?: (ctx: IFieldContext, extras: IDialogExtras) => preact.JSX.Element;
    tabs: (extras: IDialogExtras) => string[];
    /** The tab each field is on; one not listed is in the main part. */
    fieldTabs: Record<string, string>;
    render(
        tab: string,
        ctx: IFieldContext,
        extras: IDialogExtras,
    ): preact.JSX.Element | null;
    /** The text of the OK button. */
    okLabel?: string;
    /** What the progress bar says while the host saves. */
    busyLabel?: string;
    /**
     * Other fields that follow from a change, merged into the values with
     * it: a service picked offers its own REST schemas.
     */
    onChange?(
        field: string,
        value: unknown,
        values: Record<string, unknown>,
        context: unknown,
    ): Record<string, unknown> | undefined;
}

const JSON_HINT = "Additional options in JSON format";
const METADATA_HINT = "Metadata settings in JSON format";

const configure: IDialogDefinition = {
    // Deploying the metadata schema runs a script of its own, which takes
    // a few seconds.
    busyLabel: "Deploying the MariaDB REST Service metadata schema...",
    main: (ctx, extras) => {
        const context = extras.context as IConfigureContext;

        return (
            <section class="grid">
                <Select
                    ctx={ctx}
                    field="enabled"
                    caption="MariaDB REST Service Status"
                    hint={"If set to disabled, all MariaDB REST Service "
                        + "endpoints will be disabled."}
                    choices={[
                        { value: true, label: "Enabled" },
                        { value: false, label: "Disabled" },
                    ]}
                />
                {context.init ? (
                    <TextField
                        ctx={ctx}
                        field="metadataSchema"
                        caption="Metadata Schema"
                        hint={"mariadb_rest_service, or a name with a prefix "
                            + "and a postfix for one of several on a server."}
                    />
                ) : (
                    <div class="mrs-version">
                        <span class="field-caption">Current Version</span>
                        <span>{context.currentVersion ?? "-"}</span>
                        <span class="field-hint">
                            {context.upgradeable
                                ? `Version ${context.availableVersion} is available.`
                                : context.upgradeIgnored
                                    ? "The current MariaDB REST Service update "
                                    + "is being skipped."
                                    : "The MariaDB REST Service metadata "
                                    + "schema is up to date."}
                        </span>
                        {context.upgradeable ? (
                            <CheckBox
                                ctx={ctx}
                                field="update"
                                label={`Update to version ${context.availableVersion}`}
                            />
                        ) : null}
                    </div>
                )}
            </section>
        );
    },
    tabs: (extras) => {
        return (extras.context as IConfigureContext).init
            ? ["Authentication"]
            : ["Authentication Throttling", "Caches",
                "Redirects & Static Content", "Options"];
    },
    fieldTabs: {
        createAuthApp: "Authentication",
        authAppUser: "Authentication",
        authAppPassword: "Authentication",
        perAccountMinimumTime: "Authentication Throttling",
        perAccountMaximumAttempts: "Authentication Throttling",
        perHostMinimumTime: "Authentication Throttling",
        perHostMaximumAttempts: "Authentication Throttling",
        blockTimeout: "Authentication Throttling",
        responseCacheSize: "Caches",
        fileCacheSize: "Caches",
        gtidRefreshRate: "Caches",
        gtidRefreshWhenIncreasedBy: "Caches",
        directoryIndex: "Redirects & Static Content",
        defaultStaticContent: "Redirects & Static Content",
        defaultRedirects: "Redirects & Static Content",
        options: "Options",
    },
    render: (tab, ctx) => {
        switch (tab) {
            case "Authentication": {
                return (
                    <section class="grid">
                        <div class="group">
                            <CheckBox
                                ctx={ctx}
                                field="createAuthApp"
                                label="Create default REST authentication app"
                                hint={"Creates a REST authentication app using "
                                    + "the built-in MRS vendor. REST "
                                    + "authentication apps are required to "
                                    + "allow end users to login into the "
                                    + "MariaDB REST Service and access REST "
                                    + "endpoints that require authentication."}
                            />
                        </div>
                        <TextField
                            ctx={ctx}
                            field="authAppUser"
                            caption="REST User Name"
                            hint={"The user name of the REST user account. This "
                                + "account will be enabled to login into the "
                                + "MariaDB REST Service and access all REST "
                                + "endpoints of the linked REST services."}
                        />
                        <TextField
                            ctx={ctx}
                            field="authAppPassword"
                            type="password"
                            caption="REST User Password"
                            hint="The password of the REST user account."
                        />
                    </section>
                );
            }

            case "Authentication Throttling": {
                return (
                    <section class="grid">
                        <TextField ctx={ctx} field="perAccountMinimumTime"
                            caption="Per Account: Minimum Time Between Requests"
                            placeholder="1500"
                            hint={"Sets the minimum time between connection "
                                + "attempts. The value is given in "
                                + "milliseconds."} />
                        <TextField ctx={ctx} field="perHostMinimumTime"
                            caption="Per Host: Minimum Time Between Requests"
                            placeholder="1500"
                            hint={"Sets the minimum time between connection "
                                + "attempts for a given host, in ms."} />
                        <TextField ctx={ctx} field="perAccountMaximumAttempts"
                            caption="Per Account: Maximum Attempts Per Minute"
                            placeholder="5"
                            hint={"Sets the maximum amount of attempts per "
                                + "minute per client. Further attempts will "
                                + "be blocked."} />
                        <TextField ctx={ctx} field="perHostMaximumAttempts"
                            caption="Per Host: Maximum Attempts Per Minute"
                            placeholder="5"
                            hint={"Sets the maximum amount of attempts per "
                                + "minute for a given host."} />
                        <TextField ctx={ctx} field="blockTimeout"
                            caption="Block Timeout" placeholder="120"
                            hint={"Sets the amount of time the account or "
                                + "client host will be blocked from "
                                + "authentication. The value is given in "
                                + "seconds."} />
                    </section>
                );
            }

            case "Caches": {
                return (
                    <section class="grid">
                        <TextField ctx={ctx} field="responseCacheSize"
                            caption="Endpoint Response Cache" placeholder="1M"
                            hint={"Maximum size of the in-memory cache of "
                                + "responses to GET requests on tables, "
                                + "views, procedures and functions. An "
                                + "endpoint is cached when it sets the "
                                + "cacheTimeToLive option."} />
                        <TextField ctx={ctx} field="fileCacheSize"
                            caption="Static File Cache" placeholder="1M"
                            hint={"Maximum size of the in-memory cache of "
                                + "responses to GET requests on content set "
                                + "files. Default is 1M."} />
                        <div class="group">
                            <CheckBox ctx={ctx} field="gtidCache"
                                label="GTID Cache"
                                hint={"Enables the Global Transaction Id "
                                    + "(GTID) cache. GTIDs are used to ensure "
                                    + "a given commit can be read on "
                                    + "secondary instances."} />
                        </div>
                        <TextField ctx={ctx} field="gtidRefreshRate"
                            caption="Refresh Rate"
                            hint={"How often the GTID cache is refreshed on "
                                + "the REST Daemons, in seconds, e.g. 5."} />
                        <TextField ctx={ctx} field="gtidRefreshWhenIncreasedBy"
                            caption="Refresh When Increased"
                            hint={"Also refreshes the GTID cache after this "
                                + "many transactions, e.g. 500."} />
                    </section>
                );
            }

            case "Redirects & Static Content": {
                return (
                    <section class="grid">
                        <div class="group">
                            <TextField ctx={ctx} field="directoryIndex"
                                caption="Directory Index"
                                hint={"An ordered, comma separated list of "
                                    + "files returned when a directory path "
                                    + "has been requested. The first matching "
                                    + "file that is available is returned."} />
                        </div>
                        <TextArea ctx={ctx} field="defaultStaticContent"
                            caption="Default Static Content" rows={6}
                            hint={"Static content for the root path /, by "
                                + "file name: a JSON key index.html is "
                                + "served as /index.html by the REST "
                                + "Daemon."} />
                        <TextArea ctx={ctx} field="defaultRedirects"
                            caption="Default Redirects" rows={6}
                            hint={"Internal redirects of the REST Daemon, to "
                                + "expose content of a REST service on the "
                                + "root path /: a key index.html holding "
                                + "/myService/myContentSet/index.html serves "
                                + "that file as /index.html."} />
                    </section>
                );
            }

            default: {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="options" caption="Options"
                            rows={12} hint={JSON_HINT} />
                    </section>
                );
            }
        }
    },
};

const service: IDialogDefinition = {
    main: (ctx) => {
        return (
            <section class="grid">
                <TextField ctx={ctx} field="path" caption="REST Service Path"
                    hint={"The URL context root of this service, has to start "
                        + "with / and needs to be unique. developer@/path "
                        + "makes a service in development."} />
                <div class="mrs-flags">
                    <span class="field-caption">REST Service Flags</span>
                    <div class="row wrap">
                        <CheckBox ctx={ctx} field="enabled" label="Enabled" />
                        <CheckBox ctx={ctx} field="makeCurrent" label="Default"
                            hint="Makes it the current REST service" />
                        <CheckBox ctx={ctx} field="published" label="Published" />
                    </div>
                </div>
            </section>
        );
    },
    tabs: () => {
        return ["Settings", "Options", "Authentication Details", "Advanced"];
    },
    fieldTabs: {
        comments: "Settings",
        authApps: "Settings",
        options: "Options",
        metadata: "Options",
        authPath: "Authentication Details",
        authCompletedUrl: "Authentication Details",
        authCompletedUrlValidation: "Authentication Details",
        authCompletedPageContent: "Authentication Details",
        protocol: "Advanced",
    },
    render: (tab, ctx, extras) => {
        const context = extras.context as IServiceContext;
        switch (tab) {
            case "Settings": {
                return (
                    <section class="grid">
                        <CheckList ctx={ctx} field="authApps"
                            caption="Linked REST Authentication Apps"
                            choices={context.allAuthApps}
                            hint={"Select one or more REST authentication "
                                + "apps. This allows REST users of those "
                                + "applications to authenticate."} />
                        <TextArea ctx={ctx} field="comments" caption="Comments"
                            rows={4} hint="Comments to describe this REST Service." />
                    </section>
                );
            }

            case "Options": {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="options" caption="Options"
                            rows={8} hint={JSON_HINT} />
                        <TextArea ctx={ctx} field="metadata" caption="Metadata"
                            rows={8} hint={METADATA_HINT} />
                    </section>
                );
            }

            case "Authentication Details": {
                return (
                    <section class="grid">
                        <TextField ctx={ctx} field="authPath"
                            caption="Authentication Path"
                            hint="The path used for authentication." />
                        <TextField ctx={ctx} field="authCompletedUrl"
                            caption="Redirection URL"
                            hint={"The authentication workflow will redirect "
                                + "to this URL after login."} />
                        <TextField ctx={ctx} field="authCompletedUrlValidation"
                            caption="Redirection URL Validation"
                            hint={"A regular expression to validate the "
                                + "/login?onCompletionRedirect parameter set "
                                + "by the app."} />
                        <TextArea ctx={ctx} field="authCompletedPageContent"
                            caption="Authentication Completed Page Content"
                            rows={4}
                            hint={"If this field is set its content will "
                                + "replace the page content of the "
                                + "/completed page."} />
                    </section>
                );
            }

            default: {
                return (
                    <section class="grid">
                        <StringSelect ctx={ctx} field="protocol"
                            caption="Supported Protocols"
                            choices={["HTTPS", "HTTP"]}
                            hint={"The protocol the REST service is accessed "
                                + "on. HTTPS is preferred."} />
                    </section>
                );
            }
        }
    },
};

const accessFlags = (ctx: IFieldContext): preact.JSX.Element => {
    return (
        <div class="mrs-flags">
            <Select ctx={ctx} field="enabled" caption="Access"
                choices={ACCESS_CHOICES} />
            <CheckBox ctx={ctx} field="requiresAuth" label="Auth. Required" />
        </div>
    );
};

const schema: IDialogDefinition = {
    main: (ctx, extras) => {
        const context = extras.context as ISchemaContext;

        return (
            <section class="grid">
                <StringSelect ctx={ctx} field="servicePath"
                    caption="REST Service Path" choices={context.services}
                    disabled={context.existingPath !== undefined}
                    hint="The path of the REST Service this REST Schema belongs to." />
                <TextField ctx={ctx} field="requestPath" caption="REST Schema Path"
                    hint={"The request path to access the schema, has to start "
                        + "with / and needs to be unique."} />
                {accessFlags(ctx)}
            </section>
        );
    },
    tabs: () => { return ["Settings", "Options", "Metadata"]; },
    fieldTabs: {
        dbSchema: "Settings",
        itemsPerPage: "Settings",
        comments: "Settings",
        options: "Options",
        metadata: "Metadata",
    },
    render: (tab, ctx) => {
        switch (tab) {
            case "Settings": {
                return (
                    <section class="grid">
                        <TextField ctx={ctx} field="dbSchema"
                            caption="Database Schema Name"
                            hint="The name of the corresponding database schema." />
                        <TextField ctx={ctx} field="itemsPerPage"
                            caption="Items per Page" placeholder="25" />
                        <div class="group">
                            <TextField ctx={ctx} field="comments" caption="Comments"
                                hint="Comments to describe this REST Schema." />
                        </div>
                    </section>
                );
            }

            case "Options": {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="options" caption="Options"
                            rows={8} hint={JSON_HINT} />
                    </section>
                );
            }

            default: {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="metadata" caption="Metadata"
                            rows={8} hint={METADATA_HINT} />
                    </section>
                );
            }
        }
    },
};

const object: IDialogDefinition = {
    main: (ctx, extras) => {
        const context = extras.context as IObjectContext;
        const servicePath = String(ctx.get("servicePath"));
        const editing = context.existingPath !== undefined;

        return (
            <section class="grid mrs-object-main">
                <StringSelect ctx={ctx} field="servicePath"
                    caption="REST Service Path" choices={context.services}
                    disabled={editing} hint="The path of the REST Service" />
                <StringSelect ctx={ctx} field="schemaPath"
                    caption="REST Schema Path"
                    choices={context.schemas[servicePath] ?? []}
                    disabled={editing} hint="The path of the REST Schema" />
                <TextField ctx={ctx} field="requestPath" caption="REST Object Path"
                    hint="The path, has to start with /" />
                {accessFlags(ctx)}
            </section>
        );
    },
    tabs: () => {
        return ["Data Mapping", "Settings", "Authorization", "Options"];
    },
    onChange: (field, value, _values, context) => {
        return field === "servicePath"
            ? {
                schemaPath: (context as IObjectContext).schemas[
                    String(value)]?.[0] ?? "",
            }
            : undefined;
    },
    fieldTabs: {
        className: "Data Mapping",
        itemsPerPage: "Settings",
        comments: "Settings",
        mediaType: "Settings",
        format: "Settings",
        authStoredProcedure: "Authorization",
        options: "Options",
        metadata: "Options",
    },
    render: (tab, ctx, extras) => {
        switch (tab) {
            case "Data Mapping": {
                return (
                    <DataMappingEditor
                        ctx={ctx}
                        values={{
                            servicePath: ctx.get("servicePath"),
                            schemaPath: ctx.get("schemaPath"),
                            requestPath: ctx.get("requestPath"),
                            enabled: ctx.get("enabled"),
                            requiresAuth: ctx.get("requiresAuth"),
                            itemsPerPage: ctx.get("itemsPerPage"),
                            comments: ctx.get("comments"),
                            mediaType: ctx.get("mediaType"),
                            autoDetectMediaType: ctx.get("autoDetectMediaType"),
                            format: ctx.get("format"),
                            authStoredProcedure: ctx.get("authStoredProcedure"),
                            options: ctx.get("options"),
                            metadata: ctx.get("metadata"),
                            document: ctx.get("document"),
                        } as IObjectDialogValues}
                        context={extras.context as IObjectContext}
                    />
                );
            }

            case "Settings": {
                return (
                    <section class="grid">
                        <StringSelect ctx={ctx} field="format"
                            caption="Result Format" choices={["FEED", "ITEM", "MEDIA"]} />
                        <TextField ctx={ctx} field="itemsPerPage"
                            caption="Items per Page" placeholder="25" />
                        <TextField ctx={ctx} field="mediaType" caption="Media Type"
                            hint="The HTML MIME Type of the result" />
                        <div class="mrs-check-cell">
                            <CheckBox ctx={ctx} field="autoDetectMediaType"
                                label="Automatically Detect Media Type" />
                        </div>
                        <div class="group">
                            <TextField ctx={ctx} field="comments" caption="Comments" />
                        </div>
                    </section>
                );
            }

            case "Authorization": {
                return (
                    <section class="grid">
                        <div class="group">
                            <TextField ctx={ctx} field="authStoredProcedure"
                                caption="Custom Stored Procedure used for Authorization"
                                placeholder="schema.procedure" />
                        </div>
                    </section>
                );
            }

            default: {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="options" caption="Options"
                            rows={8} hint={JSON_HINT} />
                        <TextArea ctx={ctx} field="metadata" caption="Metadata"
                            rows={8} hint={METADATA_HINT} />
                    </section>
                );
            }
        }
    },
};

/** A folder's script errors, as the MySQL Shell's dialog lists them. */
const scriptErrors = (definitions: IMrsScriptDefinitions | undefined): string => {
    return (definitions?.errors ?? []).map((error) => {
        return error.kind === undefined
            ? `ERROR: ${error.message}` : `${error.kind}: ${error.message}`;
    }).join("\n");
};

const contentSet: IDialogDefinition = {
    busyLabel: "Uploading the files...",
    main: (ctx, extras) => {
        const context = extras.context as IContentSetContext;
        const editing = context.existingPath !== undefined;

        return (
            <section class="grid">
                <TextField ctx={ctx} field="requestPath" caption="Request Path"
                    hint="The request path to access the content, has to start with /" />
                <StringSelect ctx={ctx} field="servicePath"
                    caption="REST Service Path" choices={context.services}
                    disabled={editing} hint="The REST Service to hold the content" />
                {accessFlags(ctx)}
                {editing ? null : (
                    <>
                        <PathField ctx={ctx} field="directory"
                            caption="Folder to upload" folders
                            hint={"The folder that should be uploaded, "
                                + "including all its files and sub-folders"} />
                        <TextField ctx={ctx} field="ignoreList"
                            caption="Files to ignore"
                            hint="A list of files to ignore, use * and ? as wildcards" />
                    </>
                )}
            </section>
        );
    },
    tabs: (extras) => {
        return extras.scripts?.language === undefined
            ? ["Settings", "Options"]
            : ["Settings", "MRS Scripts", "Options"];
    },
    fieldTabs: { comments: "Settings", loadScripts: "Settings", options: "Options" },
    render: (tab, ctx, extras) => {
        const scripts = extras.scripts;
        switch (tab) {
            case "Settings": {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="comments" caption="Comments"
                            rows={3} />
                        {scripts?.language === undefined ? null : (
                            <div class="group">
                                <CheckBox ctx={ctx} field="loadScripts"
                                    label="Enable MRS Scripts"
                                    hint={"Load and enable the MRS scripts and "
                                        + "triggers from this content set and "
                                        + "create the corresponding endpoints"} />
                            </div>
                        )}
                        {scripts?.error === undefined ? null : (
                            <p class="note">{scripts.error}</p>
                        )}
                    </section>
                );
            }

            case "MRS Scripts": {
                const errors = scriptErrors(scripts?.definitions);

                return (
                    <section class="grid">
                        <div class="mrs-version">
                            <span class="field-caption">Language</span>
                            <span>{scripts?.language}</span>
                        </div>
                        <div class="mrs-version">
                            <span class="field-caption">Build Folder</span>
                            <span>{scripts?.definitions?.build_folder ?? "build"}</span>
                        </div>
                        <div class="group">
                            <span class="field-caption">Module Definitions</span>
                            <pre class="mrs-code mrs-readonly">
                                {JSON.stringify(scripts?.definitions?.script_modules
                                    ?? [], undefined, 4)}
                            </pre>
                        </div>
                        {errors === "" ? null : (
                            <div class="group">
                                <span class="field-caption">MRS Script Errors</span>
                                <pre class="mrs-code mrs-readonly">{errors}</pre>
                            </div>
                        )}
                    </section>
                );
            }

            default: {
                return (
                    <section class="grid">
                        <TextArea ctx={ctx} field="options" caption="Options"
                            rows={10} hint={JSON_HINT} />
                    </section>
                );
            }
        }
    },
};

const authApp: IDialogDefinition = {
    main: (ctx, extras) => {
        const context = extras.context as IAuthAppContext;

        return (
            <section class="grid">
                <StringSelect ctx={ctx} field="vendorName" caption="Vendor"
                    choices={context.vendors.map((vendor) => { return vendor.name; })}
                    disabled={context.existingName !== undefined}
                    hint="The authentication vendor" />
                <TextField ctx={ctx} field="name" caption="Name"
                    hint="The name of the authentication app" />
                <div class="mrs-flags">
                    <span class="field-caption">Flags</span>
                    <div class="row wrap">
                        <CheckBox ctx={ctx} field="enabled" label="Enabled" />
                        <CheckBox ctx={ctx} field="limitToRegisteredUsers"
                            label="Limit to registered users" />
                    </div>
                </div>
            </section>
        );
    },
    // The OAuth2 tab is there for an OAuth2 vendor only.
    tabs: (extras) => {
        const oauth = isOAuthVendor(vendorIdOf(
            extras.values as unknown as IAuthAppDialogValues,
            extras.context as IAuthAppContext));

        return oauth ? ["OAuth2 Settings", "Settings"] : ["Settings"];
    },
    fieldTabs: {
        url: "OAuth2 Settings",
        appId: "OAuth2 Settings",
        appSecret: "OAuth2 Settings",
        description: "Settings",
        defaultRole: "Settings",
    },
    render: (tab, ctx, extras) => {
        return authAppTab(tab, ctx, extras);
    },
};

const authAppTab = (
    tab: string,
    ctx: IFieldContext,
    extras: IDialogExtras,
): preact.JSX.Element => {
    const context = extras.context as IAuthAppContext;
    if (tab === "OAuth2 Settings") {
        return (
            <section class="grid">
                <div class="group">
                    <TextField ctx={ctx} field="url" caption="Custom URL"
                        hint="A custom OAuth2 service URL" />
                </div>
                <TextField ctx={ctx} field="appId" caption="App ID"
                    hint="The OAuth2 App ID/Client ID for this app as defined by the vendor" />
                <TextField ctx={ctx} field="appSecret" caption="App Secret"
                    type="password"
                    hint={context.hasSecret
                        ? "Stored. Leave empty to keep it."
                        : "The OAuth2 App Secret/Client Secret for this app as "
                        + "defined by the vendor"} />
            </section>
        );
    }

    return (
        <section class="grid">
            <TextArea ctx={ctx} field="description" caption="Description"
                rows={6} hint="A short description of the app" />
            <StringSelect ctx={ctx} field="defaultRole" caption="Default Role"
                choices={context.roles} optional
                hint="The default role for users" />
        </section>
    );
};

const user: IDialogDefinition = {
    main: (ctx, extras) => {
        const context = extras.context as IUserContext;

        return (
            <section class="grid">
                <TextField ctx={ctx} field="name" caption="User Name"
                    disabled={context.existingName !== undefined}
                    hint="The name of the user" />
                <TextField ctx={ctx} field="password" caption="User Password"
                    type="password"
                    hint={context.hasPassword
                        ? "Stored. Leave empty to keep it."
                        : "The password of the user"} />
                <div class="mrs-version">
                    <span class="field-caption">Authentication App</span>
                    <span>{context.authApp}</span>
                </div>
                <TextField ctx={ctx} field="email" caption="Email"
                    hint="The email of the user" />
                <CheckList ctx={ctx} field="roles" caption="Assigned Roles"
                    choices={context.allRoles}
                    hint="Roles assign REST object privileges to the user." />
                <div class="mrs-check-cell">
                    <CheckBox ctx={ctx} field="loginPermitted" label="Permit Login"
                        hint="Allow user to log in" />
                </div>
            </section>
        );
    },
    tabs: () => { return ["Options", "Auth App Settings"]; },
    fieldTabs: {
        options: "Options",
        appOptions: "Auth App Settings",
        vendorUserId: "Auth App Settings",
        mappedUserId: "Auth App Settings",
    },
    render: (tab, ctx) => {
        if (tab === "Options") {
            return (
                <section class="grid">
                    <TextArea ctx={ctx} field="options" caption="User Options"
                        rows={8} hint={JSON_HINT} />
                </section>
            );
        }

        return (
            <section class="grid">
                <TextArea ctx={ctx} field="appOptions" caption="Application Options"
                    rows={8} hint={JSON_HINT} />
                <TextField ctx={ctx} field="vendorUserId" caption="Vendor User Id"
                    hint="Set by OAuth2 vendors" />
                <TextField ctx={ctx} field="mappedUserId" caption="Mapped User Id"
                    hint="Optional id for sync-ing" />
            </section>
        );
    },
};

const sdkExport: IDialogDefinition = {
    main: (ctx, extras) => {
        const context = extras.context as {
            languages: string[];
            baseClasses: Record<string, string[]>;
        };
        const language = String(ctx.get("sdkLanguage"));

        return (
            <section class="grid">
                <div class="group">
                    <PathField ctx={ctx} field="directory" caption="Directory"
                        folders
                        hint="The directory where the SDK files should be written" />
                </div>
                <div class="group">
                    <TextField ctx={ctx} field="serviceUrl"
                        caption="REST Service URL"
                        hint="The URL to access the REST Service" />
                </div>
                <StringSelect ctx={ctx} field="sdkLanguage"
                    caption="SDK Client API Language" choices={context.languages}
                    hint="The development language that should be used for generation" />
                <StringSelect ctx={ctx} field="addAppBaseClass"
                    caption="Include AppBaseClass" optional
                    choices={context.baseClasses[language] ?? []}
                    hint="Add an application BaseClass with core MRS functionality." />
                <TextArea ctx={ctx} field="header" caption="SDK File Header"
                    rows={4}
                    hint="The header that should be applied to the generated SDK files." />
            </section>
        );
    },
    tabs: () => { return []; },
    fieldTabs: {},
    render: () => { return null; },
    okLabel: "Export",
    busyLabel: "Writing the SDK files...",
    // A base class of one language is none of another's.
    onChange: (field, value, values, context) => {
        const classes = (context as { baseClasses: Record<string, string[]> })
            .baseClasses[String(value)] ?? [];

        return field === "sdkLanguage"
            && !classes.includes(String(values.addAppBaseClass))
            ? { addAppBaseClass: "" } : undefined;
    },
};

/** Every dialog, by kind. */
export const DIALOGS: Record<MrsDialogKind, IDialogDefinition> = {
    configure,
    service,
    schema,
    object,
    contentSet,
    authApp,
    user,
    sdkExport,
};
