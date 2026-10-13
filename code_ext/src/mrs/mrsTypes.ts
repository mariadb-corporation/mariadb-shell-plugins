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

/**
 * The MariaDB REST Service as the shell's `mrs` module reports it.
 *
 * Every document here is what a `SHOW ... FORMAT=JSON` statement returns:
 * the metadata's column names as keys, ids as lower-case UUID text, option
 * columns embedded as JSON. A list statement returns an array of the same
 * documents `SHOW CREATE REST ... FORMAT=JSON` returns for one object, less
 * the parts a list leaves out (a REST object's data mappings). Free of
 * `vscode`, since the dialogs' webview reads the same documents.
 */

/** A JSON object of options, as stored. */
export type JsonObject = Record<string, unknown>;

/**
 * Whether an object is served: `0` disabled, `1` enabled, `2` private -
 * served to the REST Daemon's own requests only, such as an MRS script's.
 */
export type EnabledState = 0 | 1 | 2;

export const ENABLED_STATE = {
    disabled: 0,
    enabled: 1,
    private: 2,
} as const;

/** `SHOW REST METADATA STATUS FORMAT=JSON`. */
export interface IMrsStatus {
    service_configured: boolean;
    service_enabled: boolean;
    service_upgradeable: boolean;
    service_upgrade_ignored: boolean;
    /** How many services are enabled. */
    service_count: number;
    /** A deployment is under way, or one stopped half way. */
    service_being_upgraded: boolean;
    major_upgrade_required: boolean;
    current_metadata_version: string | null;
    available_metadata_version: string;
    required_rest_daemon_version: string;
    /**
     * Grows with every change to the REST metadata (the audit log's
     * highest id); null before the schema exists.
     */
    metadata_version: number | null;
    metadata_schema: string;
    available_metadata_versions?: string[];
    configuration_options?: JsonObject;
}

/** One REST service, from `SHOW REST SERVICES FORMAT=JSON`. */
export interface IMrsService {
    id: string;
    url_context_root: string;
    /** `[devs@]/path`: the path a statement names the service by. */
    full_service_path: string;
    url_protocol?: string[] | string | null;
    name: string;
    enabled: number;
    published: boolean | number;
    comments: string | null;
    options: JsonObject | null;
    metadata: JsonObject | null;
    auth_path: string | null;
    auth_completed_url: string | null;
    auth_completed_url_validation: string | null;
    auth_completed_page_content: string | null;
    /** Set while the service is in development. */
    in_development: { developers?: string[] } | null;
    developers?: string[];
    /** The names of the auth apps linked to it. */
    auth_apps?: string[];
    /** Whether it is the session's current service (USE REST SERVICE). */
    is_current?: boolean;
}

/** One REST schema, from `SHOW REST SCHEMAS FORMAT=JSON`. */
export interface IMrsSchema {
    id: string;
    service_id: string;
    /** The database schema it exposes. */
    name: string;
    /** `DATABASE_SCHEMA`, or `SCRIPT_MODULE` for an MRS script module. */
    schema_type: string;
    request_path: string;
    requires_auth: boolean;
    enabled: EnabledState;
    internal?: boolean | number;
    items_per_page: number | null;
    comments: string | null;
    options: JsonObject | null;
    metadata: JsonObject | null;
    /** The service path and the schema's own: `/svc/sakila`. */
    host_ctx?: string;
}

/** What a REST object is backed by. */
export type MrsObjectType =
    | "TABLE" | "VIEW" | "PROCEDURE" | "FUNCTION" | "SCRIPT";

/** A column of a table or view, or a routine's parameter, as stored. */
export interface IMrsDbColumn {
    name: string;
    datatype?: string;
    not_null?: boolean;
    is_primary?: boolean;
    is_unique?: boolean;
    is_generated?: boolean | null;
    id_generation?: string | null;
    comment?: string | null;
    /** A routine parameter that is passed in. */
    in?: boolean;
    /** A routine parameter that is passed out. */
    out?: boolean;
    charset?: string | null;
    collation?: string | null;
}

/** One pair of columns a reference joins on. */
export interface IMrsColumnMapping {
    base: string;
    ref: string;
}

/** A foreign key relationship, seen from the table holding the field. */
export interface IMrsReferenceMapping {
    kind: "1:1" | "n:1" | "1:n";
    constraint?: string;
    to_many: boolean;
    referenced_schema: string;
    referenced_table: string;
    column_mapping: IMrsColumnMapping[];
}

/** The CRUD flags a data mapping or a reference stores in its options. */
export interface IMrsDataMappingOptions extends JsonObject {
    dataMappingViewInsert?: boolean;
    dataMappingViewUpdate?: boolean;
    dataMappingViewDelete?: boolean;
    dataMappingViewNoCheck?: boolean;
}

/** A field standing for a referenced table, and how it is joined. */
export interface IMrsDataMappingReference {
    id: string;
    reduce_to_value_of_field_id: string | null;
    row_ownership_field_id: string | null;
    reference_mapping: IMrsReferenceMapping;
    unnest: boolean;
    options: IMrsDataMappingOptions | null;
    sdk_options?: JsonObject | null;
    comments?: string | null;
}

/**
 * One field of a data mapping. The list is flat: a field below a
 * reference names it in `parent_reference_id`, which is the
 * `represents_reference_id` of the field standing for the reference.
 */
export interface IMrsDataMappingField {
    id: string;
    data_mapping_id?: string;
    parent_reference_id: string | null;
    represents_reference_id: string | null;
    name: string;
    position: number;
    db_column: IMrsDbColumn | null;
    enabled: boolean;
    allow_filtering: boolean;
    allow_sorting: boolean;
    no_check: boolean;
    no_update: boolean;
    json_schema?: JsonObject | null;
    options?: JsonObject | null;
    sdk_options?: JsonObject | null;
    comments?: string | null;
    data_mapping_reference: IMrsDataMappingReference | null;
}

/**
 * A data mapping: the shape of a REST view's JSON documents, or of a
 * routine's parameters or one of its result sets.
 */
export interface IMrsDataMapping {
    id: string;
    name: string;
    kind: "RESULT" | "PARAMETERS";
    position: number;
    row_ownership_field_id: string | null;
    options: IMrsDataMappingOptions | null;
    sdk_options?: JsonObject | null;
    comments?: string | null;
    fields: IMrsDataMappingField[];
}

/**
 * One REST object - a view, procedure, function or MRS script. A list
 * statement leaves out `data_mappings`; `SHOW CREATE` has them.
 */
export interface IMrsObject {
    id: string;
    rest_schema_id: string;
    service_id?: string;
    /** The database object it serves. */
    name: string;
    schema_name: string;
    request_path: string;
    schema_request_path?: string;
    host_ctx?: string;
    object_type: MrsObjectType;
    crud_operations: string[];
    format: "FEED" | "ITEM" | "MEDIA";
    enabled: EnabledState;
    requires_auth: boolean;
    items_per_page: number | null;
    media_type: string | null;
    auto_detect_media_type?: boolean;
    auth_stored_procedure: string | null;
    comments: string | null;
    options: JsonObject | null;
    metadata: JsonObject | null;
    data_mappings?: IMrsDataMapping[];
}

/** One content set, from `SHOW REST CONTENT SETS FORMAT=JSON`. */
export interface IMrsContentSet {
    id: string;
    service_id: string;
    /** `STATIC`, or `SCRIPTS` once its MRS scripts are loaded. */
    content_type: string;
    request_path: string;
    requires_auth: boolean;
    enabled: EnabledState;
    comments: string | null;
    options: JsonObject | null;
    host_ctx?: string;
}

/** One content file, from `SHOW REST CONTENT FILES FORMAT=JSON`. */
export interface IMrsContentFile {
    id: string;
    content_set_id: string;
    request_path: string;
    requires_auth: boolean;
    enabled: EnabledState;
    size: number;
    options?: JsonObject | null;
    content_set_request_path?: string;
    host_ctx?: string;
}

/** One authentication app, from `SHOW REST AUTH APPS FORMAT=JSON`. */
export interface IMrsAuthApp {
    id: string;
    auth_vendor_id: string;
    /** The vendor's name: `MRS`, `MariaDB Internal`, `Google`, ... */
    auth_vendor: string;
    name: string;
    description: string | null;
    url: string | null;
    url_direct_auth?: string | null;
    app_id: string | null;
    /** Whether a secret is stored; the secret itself is never reported. */
    has_app_secret: boolean;
    enabled: boolean;
    limit_to_registered_users: boolean;
    default_role_id: string | null;
    options?: JsonObject | null;
    /** The full paths of the services it is linked to. */
    services?: string[];
}

/** One authentication vendor, from `SHOW REST AUTH VENDORS FORMAT=JSON`. */
export interface IMrsAuthVendor {
    id: string;
    name: string;
    comments: string | null;
    enabled: boolean;
    validation_url?: string | null;
}

/** A role a user holds. */
export interface IMrsUserRole {
    role_id: string;
    caption: string;
    specific_to_service_id?: string | null;
    comments?: string | null;
    options?: JsonObject | null;
}

/** One REST user, from `SHOW REST USERS FORMAT=JSON`. */
export interface IMrsUser {
    id: string;
    auth_app_id: string;
    auth_app_name: string;
    name: string | null;
    email: string | null;
    vendor_user_id: string | null;
    mapped_user_id: string | null;
    login_permitted: boolean;
    /** Whether a password is stored; the hash is never reported. */
    has_password: boolean;
    app_options: JsonObject | null;
    options: JsonObject | null;
    roles: IMrsUserRole[];
}

/** One REST role, from `SHOW REST ROLES FORMAT=JSON`. */
export interface IMrsRole {
    id: string;
    caption: string;
    derived_from_role_id: string | null;
    derived_from_role_caption?: string | null;
    specific_to_service_id: string | null;
    specific_to_service?: string | null;
    description: string | null;
    options: JsonObject | null;
}

/** One REST Daemon, from `SHOW REST DAEMONS FORMAT=JSON`. */
export interface IMrsDaemon {
    id: string;
    name: string;
    address: string;
    product_name: string;
    version: string;
    last_check_in: string | null;
    /** Whether it checked in within the last 10 seconds. */
    active: boolean;
    developer: string | null;
    attributes?: JsonObject | null;
    options?: JsonObject | null;
}

/** A column of `SHOW REST COLUMNS FROM TABLE|VIEW ... FORMAT=JSON`. */
export interface IMrsTableColumn {
    position: number;
    name: string;
    /** The column; null for a reference to another table. */
    db_column: IMrsDbColumn | null;
    /** The relationship; null for a column. */
    reference_mapping: IMrsReferenceMapping | null;
}

/** A parameter of `SHOW REST COLUMNS FROM PROCEDURE|FUNCTION ... FORMAT=JSON`. */
export interface IMrsRoutineParameter {
    position: number;
    name: string;
    mode: "IN" | "OUT" | "INOUT";
    datatype: string;
    charset?: string | null;
    collation?: string | null;
}

/** `SHOW REST COLUMNS ... FORMAT=JSON`. */
export interface IMrsColumns {
    schema: string;
    name: string;
    type: "TABLE" | "VIEW" | "PROCEDURE" | "FUNCTION";
    columns?: IMrsTableColumn[];
    parameters?: IMrsRoutineParameter[];
    /** A function's return type. */
    return_type?: string;
}

/** What a folder of MRS scripts defines. */
export interface IMrsScriptDefinitions {
    script_modules?: unknown[];
    interfaces?: unknown[];
    errors?: Array<{ kind?: string; message: string }>;
    build_folder?: string;
    static_content_folders?: string[];
}

/** The vendors an app needs no OAuth2 settings for. */
export const MRS_VENDOR_ID = "30000000-0000-0000-0000-000000000000";
export const MARIADB_VENDOR_ID = "31000000-0000-0000-0000-000000000000";

/**
 * @param vendorId The id of an auth vendor.
 *
 * @returns Whether its apps sign users in with OAuth2, which needs a URL,
 *          an app id and a secret - every vendor but MRS's own and the
 *          MariaDB accounts.
 */
export const isOAuthVendor = (vendorId: string | undefined): boolean => {
    return vendorId !== undefined && vendorId !== MRS_VENDOR_ID
        && vendorId !== MARIADB_VENDOR_ID;
};
