# Database Schema Change Log for `mysql_rest_service_metadata`

## 5.0.0

- Changed every id column, and every foreign key to one, from `BINARY(16)` to MariaDB's `UUID` type; primary keys default to `UUID_v7()`, and `get_sequence_id()` returns one
- Ids in JSON (the audit log, `object_fields_with_references`, `sdk_service_data`) are plain UUID strings instead of hex or base64
- Removed the MySQL-only `UUID_TO_BIN_SWAP` and `BIN_TO_UUID_SWAP` functions
- Set `NO_AUTO_CREATE_USER` in the SQL mode of the scripts, so a GRANT to a missing account fails instead of creating it
- Dropped the releases before 4.1.6, which only ran on MySQL; 4.1.6 is the only version that can be upgraded to 5.0.0

## 4.1.4

- Update the `sdk_service_data` PROCEDURE to aggregate objects ordered by position

## 4.1.3

- Fixed wrong time unit when compressing router_status log entries

## 4.1.2

- Added missing privileges for `restore_roles` and `sdk_service_data` PROCEDURE

## 4.1.1

- Changed whole schema to use a USE statement and not fully qualified database object names in order to allow the schema to be ignore during replication

## 4.1.0

- Added `restore_roles` PROCEDURE to restore all ROLEs used by MRS
- Added `sdk_service_data` PROCEDURE to collect all data of a given REST service

## 4.0.4

- Added fractional seconds precision to TIMESTAMP values in log tables

## 4.0.3

- Changed the export format of the audit_log dump to valid JSON
- Enabled the `delete_old_audit_log_entries` EVENT by default
- Added `msm_instance_demoted` and `msm_instance_promoted` procedures

## 4.0.2

- Changes to the router_general_log table
- Added event to clean the router_general_log table after one day
- Fix GRANTs for msm_schema_version

Copyright (c) 2025, Oracle and/or its affiliates.
