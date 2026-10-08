/*
 * Copyright (c) 2025, Oracle and/or its affiliates.
 * Copyright (c) 2026, MariaDB plc.
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

-- #############################################################################
-- MSM Section 002: Database Schema Update Script
-- -----------------------------------------------------------------------------
-- This script updates the database schema `mysql_rest_service_metadata`
-- from version 4.1.6 to 5.0.0
-- #############################################################################


-- #############################################################################
-- MSM Section 010: Server Variable Settings
-- -----------------------------------------------------------------------------
-- Set server variables, remember their state to be able to restore accordingly.
-- #############################################################################

SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0;
SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0;
SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,'
    'NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,'
    'NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION';


-- #############################################################################
-- MSM Section 220: Database Schema Version Update Indication
-- -----------------------------------------------------------------------------
-- Replace the `mysql_rest_service_metadata`.`msm_schema_version` VIEW
-- and initialize it with the version 0, 0, 0 which indicates the ongoing
-- update processes of the database schema.
-- #############################################################################

USE `mysql_rest_service_metadata`;

CREATE OR REPLACE SQL SECURITY INVOKER
VIEW `msm_schema_version` (`major`,`minor`,`patch`) AS
SELECT 0, 0, 0;


-- #############################################################################
-- MSM Section 230: Creation of Update Helpers
-- -----------------------------------------------------------------------------
-- Definitions of optional helper PROCEDUREs and FUNCTIONs that are called
-- during the update of the database schema. It is important to note that these
-- need to be defined in a way as if a schema object of the same name and type
-- already exists. Use explicit DROP IF EXISTS statements or CREATE OR REPLACE
-- statements when creating the helper objects. The names of all helper
-- routines need to start with `msm_`.
-- #############################################################################

DELIMITER %%

-- Insert optional helper PROCEDUREs and FUNCTIONs here

DELIMITER ;


-- #############################################################################
-- MSM Section 240: Non-idempotent Schema Object Changes and All DROPs
-- -----------------------------------------------------------------------------
-- This section contains changes performed on schema TABLEs. It is important to
-- note that these changes need to be carefully processed during a schema
-- upgrade operation. These changes must be executed in the right order as
-- each operation will result in a state change that often cannot be easily
-- revered. This might include DROP statements on other schema objects (VIEWs,
-- PROCEDUREs, FUNCTIONs, TRIGGERs EVENTs, ...) as they could otherwise prevent
-- change of the TABLE structure. These schema objects may then be re-created
-- inside the MSM Section 250: Idempotent Schema Object Changes. If there are
-- no changes required, this section can be skipped.
-- -----------------------------------------------------------------------------
-- TABLE changes and all DROP statements
-- #############################################################################
-- Every id column and every foreign key to one changes from BINARY(16) to UUID.
-- MariaDB refuses to change a column that is part of a FOREIGN KEY, so the
-- keys are dropped first and added back below; the 16 bytes of each id are
-- kept as they are, only their type changes.

ALTER TABLE `service` DROP FOREIGN KEY `fk_service_url_host1`;
ALTER TABLE `service` DROP FOREIGN KEY `fk_service_service1`;
ALTER TABLE `db_schema` DROP FOREIGN KEY `fk_db_schema_service1`;
ALTER TABLE `db_object` DROP FOREIGN KEY `fk_db_objects_db_schema1`;
ALTER TABLE `auth_app` DROP FOREIGN KEY `fk_auth_app_auth_vendor1`;
ALTER TABLE `mrs_user` DROP FOREIGN KEY `fk_auth_user_auth_app1`;
ALTER TABLE `url_host_alias` DROP FOREIGN KEY `fk_url_host_alias_url_host1`;
ALTER TABLE `content_set` DROP FOREIGN KEY `fk_static_content_version_service1`;
ALTER TABLE `content_file` DROP FOREIGN KEY `fk_content_content_set1`;
ALTER TABLE `mrs_role` DROP FOREIGN KEY `fk_priv_role_priv_role1`;
ALTER TABLE `mrs_role` DROP FOREIGN KEY `fk_auth_role_service1`;
ALTER TABLE `mrs_user_has_role` DROP FOREIGN KEY `fk_auth_user_has_privilege_role_auth_user1`;
ALTER TABLE `mrs_user_has_role` DROP FOREIGN KEY `fk_auth_user_has_privilege_role_privilege_role1`;
ALTER TABLE `mrs_user_hierarchy_type` DROP FOREIGN KEY `fk_user_hierarchy_type_service1`;
ALTER TABLE `mrs_user_hierarchy` DROP FOREIGN KEY `fk_user_hierarchy_auth_user1`;
ALTER TABLE `mrs_user_hierarchy` DROP FOREIGN KEY `fk_user_hierarchy_auth_user2`;
ALTER TABLE `mrs_user_hierarchy` DROP FOREIGN KEY `fk_user_hierarchy_hierarchy_type1`;
ALTER TABLE `mrs_privilege` DROP FOREIGN KEY `fk_priv_on_schema_auth_role1`;
ALTER TABLE `mrs_user_group` DROP FOREIGN KEY `fk_user_group_service1`;
ALTER TABLE `mrs_user_group_has_role` DROP FOREIGN KEY `fk_user_group_has_auth_role_user_group1`;
ALTER TABLE `mrs_user_group_has_role` DROP FOREIGN KEY `fk_user_group_has_auth_role_auth_role1`;
ALTER TABLE `mrs_user_has_group` DROP FOREIGN KEY `fk_auth_user_has_user_group_auth_user1`;
ALTER TABLE `mrs_user_has_group` DROP FOREIGN KEY `fk_auth_user_has_user_group_user_group1`;
ALTER TABLE `mrs_user_group_hierarchy` DROP FOREIGN KEY `fk_user_group_has_user_group_user_group1`;
ALTER TABLE `mrs_user_group_hierarchy` DROP FOREIGN KEY `fk_user_group_has_user_group_user_group2`;
ALTER TABLE `mrs_user_group_hierarchy` DROP FOREIGN KEY `fk_user_group_hierarchy_group_hierarchy_type1`;
ALTER TABLE `mrs_db_object_row_group_security` DROP FOREIGN KEY `fk_table1_db_object1`;
ALTER TABLE `mrs_db_object_row_group_security` DROP FOREIGN KEY `fk_db_object_row_security_group_hierarchy_type1`;
ALTER TABLE `object` DROP FOREIGN KEY `fk_result_db_object1`;
ALTER TABLE `object_field` DROP FOREIGN KEY `fk_properties_result1`;
ALTER TABLE `object_field` DROP FOREIGN KEY `fk_result_property_result_reference1`;
ALTER TABLE `object_field` DROP FOREIGN KEY `fk_result_property_result_reference2`;
ALTER TABLE `service_has_auth_app` DROP FOREIGN KEY `fk_service_has_auth_app_service1`;
ALTER TABLE `service_has_auth_app` DROP FOREIGN KEY `fk_service_has_auth_app_auth_app1`;
ALTER TABLE `content_set_has_obj_def` DROP FOREIGN KEY `fk_content_set_has_db_object_content_set1`;
ALTER TABLE `content_set_has_obj_def` DROP FOREIGN KEY `fk_content_set_has_db_object_db_object1`;

ALTER TABLE `url_host`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7();
ALTER TABLE `service`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `parent_id` UUID NULL,
    MODIFY COLUMN `url_host_id` UUID NOT NULL;
ALTER TABLE `db_schema`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `service_id` UUID NOT NULL;
ALTER TABLE `db_object`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `db_schema_id` UUID NOT NULL;
ALTER TABLE `auth_vendor`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7();
ALTER TABLE `auth_app`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `auth_vendor_id` UUID NOT NULL,
    MODIFY COLUMN `default_role_id` UUID NULL COMMENT 'If set, a new user that has not any auth_roles assigned will get this role assigned when he logs in the first time.';
ALTER TABLE `mrs_user`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `auth_app_id` UUID NOT NULL;
ALTER TABLE `redirect`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7();
ALTER TABLE `url_host_alias`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `url_host_id` UUID NOT NULL;
ALTER TABLE `content_set`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `service_id` UUID NOT NULL;
ALTER TABLE `content_file`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `content_set_id` UUID NOT NULL;
ALTER TABLE `audit_log`
    MODIFY COLUMN `old_row_id` UUID NULL,
    MODIFY COLUMN `new_row_id` UUID NULL;
ALTER TABLE `mrs_role`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `derived_from_role_id` UUID NULL,
    MODIFY COLUMN `specific_to_service_id` UUID NULL;
ALTER TABLE `mrs_user_has_role`
    MODIFY COLUMN `user_id` UUID NOT NULL,
    MODIFY COLUMN `role_id` UUID NOT NULL;
ALTER TABLE `mrs_user_hierarchy_type`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `specific_to_service_id` UUID NULL;
ALTER TABLE `mrs_user_hierarchy`
    MODIFY COLUMN `user_id` UUID NOT NULL,
    MODIFY COLUMN `reporting_to_user_id` UUID NOT NULL,
    MODIFY COLUMN `user_hierarchy_type_id` UUID NOT NULL;
ALTER TABLE `mrs_privilege`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `role_id` UUID NOT NULL;
ALTER TABLE `mrs_user_group`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `specific_to_service_id` UUID NULL;
ALTER TABLE `mrs_user_group_has_role`
    MODIFY COLUMN `user_group_id` UUID NOT NULL,
    MODIFY COLUMN `role_id` UUID NOT NULL;
ALTER TABLE `mrs_user_has_group`
    MODIFY COLUMN `user_id` UUID NOT NULL,
    MODIFY COLUMN `user_group_id` UUID NOT NULL;
ALTER TABLE `mrs_group_hierarchy_type`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7();
ALTER TABLE `mrs_user_group_hierarchy`
    MODIFY COLUMN `user_group_id` UUID NOT NULL,
    MODIFY COLUMN `parent_group_id` UUID NOT NULL,
    MODIFY COLUMN `group_hierarchy_type_id` UUID NOT NULL;
ALTER TABLE `mrs_db_object_row_group_security`
    MODIFY COLUMN `db_object_id` UUID NOT NULL,
    MODIFY COLUMN `group_hierarchy_type_id` UUID NOT NULL;
ALTER TABLE `router_session`
    MODIFY COLUMN `user_id` UUID NOT NULL,
    MODIFY COLUMN `service_id` UUID NOT NULL;
ALTER TABLE `object`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `db_object_id` UUID NOT NULL,
    MODIFY COLUMN `row_ownership_field_id` UUID NULL;
ALTER TABLE `object_reference`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `reduce_to_value_of_field_id` UUID NULL COMMENT 'If set to an object_field, this reference will be reduced to the value of the given field. Example: \"films\": [ { \"categories\": [ \"Thriller\", \"Action\"] } ] instead of \"films\": [ { \"categories\": [ { \"name\": \"Thriller\" }, { \"name\": \"Action\" } ] } ],',
    MODIFY COLUMN `row_ownership_field_id` UUID NULL;
ALTER TABLE `object_field`
    MODIFY COLUMN `id` UUID NOT NULL DEFAULT UUID_v7(),
    MODIFY COLUMN `object_id` UUID NOT NULL,
    MODIFY COLUMN `parent_reference_id` UUID NULL,
    MODIFY COLUMN `represents_reference_id` UUID NULL;
ALTER TABLE `service_has_auth_app`
    MODIFY COLUMN `service_id` UUID NOT NULL,
    MODIFY COLUMN `auth_app_id` UUID NOT NULL;
ALTER TABLE `content_set_has_obj_def`
    MODIFY COLUMN `content_set_id` UUID NOT NULL,
    MODIFY COLUMN `db_object_id` UUID NOT NULL;

-- The foreign keys, as the 5.0.0 tables define them.
ALTER TABLE `service`
    ADD CONSTRAINT `fk_service_url_host1`
    FOREIGN KEY (`url_host_id`)
    REFERENCES `url_host` (`id`)
    ON DELETE RESTRICT
    ON UPDATE NO ACTION;
ALTER TABLE `service`
    ADD CONSTRAINT `fk_service_service1`
    FOREIGN KEY (`parent_id`)
    REFERENCES `service` (`id`)
    ON DELETE RESTRICT
    ON UPDATE NO ACTION;
ALTER TABLE `db_schema`
    ADD CONSTRAINT `fk_db_schema_service1`
    FOREIGN KEY (`service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `db_object`
    ADD CONSTRAINT `fk_db_objects_db_schema1`
    FOREIGN KEY (`db_schema_id`)
    REFERENCES `db_schema` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `auth_app`
    ADD CONSTRAINT `fk_auth_app_auth_vendor1`
    FOREIGN KEY (`auth_vendor_id`)
    REFERENCES `auth_vendor` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user`
    ADD CONSTRAINT `fk_auth_user_auth_app1`
    FOREIGN KEY (`auth_app_id`)
    REFERENCES `auth_app` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `url_host_alias`
    ADD CONSTRAINT `fk_url_host_alias_url_host1`
    FOREIGN KEY (`url_host_id`)
    REFERENCES `url_host` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `content_set`
    ADD CONSTRAINT `fk_static_content_version_service1`
    FOREIGN KEY (`service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `content_file`
    ADD CONSTRAINT `fk_content_content_set1`
    FOREIGN KEY (`content_set_id`)
    REFERENCES `content_set` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
-- As section 140 redefines it: deleting a role deletes the roles derived from it.
ALTER TABLE `mrs_role`
    ADD CONSTRAINT `fk_priv_role_priv_role1`
    FOREIGN KEY (`derived_from_role_id`)
    REFERENCES `mrs_role` (`id`)
    ON DELETE CASCADE;
ALTER TABLE `mrs_role`
    ADD CONSTRAINT `fk_auth_role_service1`
    FOREIGN KEY (`specific_to_service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_has_role`
    ADD CONSTRAINT `fk_auth_user_has_privilege_role_auth_user1`
    FOREIGN KEY (`user_id`)
    REFERENCES `mrs_user` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_has_role`
    ADD CONSTRAINT `fk_auth_user_has_privilege_role_privilege_role1`
    FOREIGN KEY (`role_id`)
    REFERENCES `mrs_role` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_hierarchy_type`
    ADD CONSTRAINT `fk_user_hierarchy_type_service1`
    FOREIGN KEY (`specific_to_service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_hierarchy`
    ADD CONSTRAINT `fk_user_hierarchy_auth_user1`
    FOREIGN KEY (`user_id`)
    REFERENCES `mrs_user` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_hierarchy`
    ADD CONSTRAINT `fk_user_hierarchy_auth_user2`
    FOREIGN KEY (`reporting_to_user_id`)
    REFERENCES `mrs_user` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_hierarchy`
    ADD CONSTRAINT `fk_user_hierarchy_hierarchy_type1`
    FOREIGN KEY (`user_hierarchy_type_id`)
    REFERENCES `mrs_user_hierarchy_type` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_privilege`
    ADD CONSTRAINT `fk_priv_on_schema_auth_role1`
    FOREIGN KEY (`role_id`)
    REFERENCES `mrs_role` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group`
    ADD CONSTRAINT `fk_user_group_service1`
    FOREIGN KEY (`specific_to_service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group_has_role`
    ADD CONSTRAINT `fk_user_group_has_auth_role_user_group1`
    FOREIGN KEY (`user_group_id`)
    REFERENCES `mrs_user_group` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group_has_role`
    ADD CONSTRAINT `fk_user_group_has_auth_role_auth_role1`
    FOREIGN KEY (`role_id`)
    REFERENCES `mrs_role` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_has_group`
    ADD CONSTRAINT `fk_auth_user_has_user_group_auth_user1`
    FOREIGN KEY (`user_id`)
    REFERENCES `mrs_user` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_has_group`
    ADD CONSTRAINT `fk_auth_user_has_user_group_user_group1`
    FOREIGN KEY (`user_group_id`)
    REFERENCES `mrs_user_group` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group_hierarchy`
    ADD CONSTRAINT `fk_user_group_has_user_group_user_group1`
    FOREIGN KEY (`user_group_id`)
    REFERENCES `mrs_user_group` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group_hierarchy`
    ADD CONSTRAINT `fk_user_group_has_user_group_user_group2`
    FOREIGN KEY (`parent_group_id`)
    REFERENCES `mrs_user_group` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_user_group_hierarchy`
    ADD CONSTRAINT `fk_user_group_hierarchy_group_hierarchy_type1`
    FOREIGN KEY (`group_hierarchy_type_id`)
    REFERENCES `mrs_group_hierarchy_type` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_db_object_row_group_security`
    ADD CONSTRAINT `fk_table1_db_object1`
    FOREIGN KEY (`db_object_id`)
    REFERENCES `db_object` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `mrs_db_object_row_group_security`
    ADD CONSTRAINT `fk_db_object_row_security_group_hierarchy_type1`
    FOREIGN KEY (`group_hierarchy_type_id`)
    REFERENCES `mrs_group_hierarchy_type` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `object`
    ADD CONSTRAINT `fk_result_db_object1`
    FOREIGN KEY (`db_object_id`)
    REFERENCES `db_object` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `object_field`
    ADD CONSTRAINT `fk_properties_result1`
    FOREIGN KEY (`object_id`)
    REFERENCES `object` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `object_field`
    ADD CONSTRAINT `fk_result_property_result_reference1`
    FOREIGN KEY (`parent_reference_id`)
    REFERENCES `object_reference` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `object_field`
    ADD CONSTRAINT `fk_result_property_result_reference2`
    FOREIGN KEY (`represents_reference_id`)
    REFERENCES `object_reference` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `service_has_auth_app`
    ADD CONSTRAINT `fk_service_has_auth_app_service1`
    FOREIGN KEY (`service_id`)
    REFERENCES `service` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `service_has_auth_app`
    ADD CONSTRAINT `fk_service_has_auth_app_auth_app1`
    FOREIGN KEY (`auth_app_id`)
    REFERENCES `auth_app` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `content_set_has_obj_def`
    ADD CONSTRAINT `fk_content_set_has_db_object_content_set1`
    FOREIGN KEY (`content_set_id`)
    REFERENCES `content_set` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
ALTER TABLE `content_set_has_obj_def`
    ADD CONSTRAINT `fk_content_set_has_db_object_db_object1`
    FOREIGN KEY (`db_object_id`)
    REFERENCES `db_object` (`id`)
    ON DELETE NO ACTION
    ON UPDATE NO ACTION;
-- #############################################################################
-- MSM Section 250: Idempotent Schema Object Additions And Changes
-- -----------------------------------------------------------------------------
-- This section contains the new and update creation of all schema objects,
-- except TABLEs, ROLEs and GRANTs. Ensure that all existing objects are
-- overwritten in a clean manner using explicit DROP IF EXISTS statements or
-- CREATE OR REPLACE when re-creating the objects. All object removals must
-- be defined in the MSM Section 240. If there are no changes required, this
-- section can be skipped.
-- -----------------------------------------------------------------------------
-- All other schema object definitions (VIEWs, PROCEDUREs, FUNCTIONs, TRIGGERs,
-- EVENTs, ...) that are new or have changed
-- #############################################################################

DELIMITER %%

/*
-- ToDo: Add schema objects create statements

-- -----------------------------------------------------------------------------
-- VIEW `mysql_rest_service_metadata`.`my_view`
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW `mysql_rest_service_metadata`.`my_view` AS
    SELECT t.`id`, t.`name`
    FROM `mysql_rest_service_metadata`.`my_table` AS t
    ORDER BY t.`name`%%

-- -----------------------------------------------------------------------------
-- PROCEDURE `mysql_rest_service_metadata`.`my_proc`
-- -----------------------------------------------------------------------------

DROP PROCEDURE IF EXISTS `mysql_rest_service_metadata`.`my_proc`%%
CREATE PROCEDURE `mysql_rest_service_metadata`.`my_proc`(INOUT value INT)
BEGIN
    SET value = value + 1;
END%%
*/

DELIMITER ;


-- #############################################################################
-- MSM Section 270: Authorization
-- -----------------------------------------------------------------------------
-- This section is used to define changes for ROLEs and GRANTs in respect to
-- the previous version. If there are no changes required, this section can
-- be skipped.
-- #############################################################################

-- Change ROLEs and perform the required GRANT/REVOKE statements.


-- #############################################################################
-- MSM Section 290: Removal of Update Helpers
-- -----------------------------------------------------------------------------
-- Removal of optional helper PROCEDUREs and FUNCTIONs that are called during
-- the update of the database schema. Note that DROP IF EXISTS needs to be
-- used.
-- #############################################################################
DELIMITER %%
-- The MySQL-era helpers for ids held as BINARY(16); the ids are UUIDs now and
-- get_sequence_id() returns UUID_v7(). A FUNCTION cannot be dropped from
-- inside the update procedure (section 240), so they go here.
DROP FUNCTION IF EXISTS `mysql_rest_service_metadata`.`UUID_TO_BIN_SWAP`%%
DROP FUNCTION IF EXISTS `mysql_rest_service_metadata`.`BIN_TO_UUID_SWAP`%%
DELIMITER ;
-- #############################################################################
-- MSM Section 910: Database Schema Version Definition
-- -----------------------------------------------------------------------------
-- Setting the correct database schema version.
-- #############################################################################

USE `mysql_rest_service_metadata`;

CREATE OR REPLACE SQL SECURITY INVOKER
VIEW `msm_schema_version` (`major`,`minor`,`patch`) AS
SELECT 5, 0, 0;


-- #############################################################################
-- MSM Section 920: Server Variable Restoration
-- -----------------------------------------------------------------------------
-- Restore the modified server variables to their original state.
-- #############################################################################

SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
