-- Copyright (c) 2025, Oracle and/or its affiliates.
-- Copyright (c) 2026, MariaDB plc.
${license}
-- #############################################################################
-- MSM Section 002: Database Schema Update Script
-- -----------------------------------------------------------------------------
-- This script updates the database schema `${schema_name}`
-- from version ${version_from} to ${version_to}
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
-- Replace the `${schema_name}`.`msm_schema_version` VIEW
-- and initialize it with the version 0, 0, 0 which indicates the ongoing
-- update processes of the database schema.
-- #############################################################################

USE ${schema_identifier};

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
-- reversed. This might include DROP statements on other schema objects (VIEWs,
-- TRIGGERs, EVENTs, ...) as they could otherwise prevent change of the TABLE
-- structure, and the DROP of objects the new version no longer has. Do not
-- re-create these objects here: Section 150 of the new version re-creates them
-- after all update steps have run. A DROP PROCEDURE or DROP FUNCTION cannot
-- run here, put it into the MSM Section 260. If there are no changes
-- required, this section can be skipped.
-- -----------------------------------------------------------------------------
-- TABLE changes and DROP statements
-- #############################################################################

/*
-- ToDo: Add ALTER TABLE statements or new CREATE TABLE statements. Include
-- DROP TRIGGER/EVENT/... statements of objects the new version removes.
-- Perform DML statements if needed.

-- -----------------------------------------------------------------------------
-- ALTER TABLE `${schema_name}`.`my_table`
-- -----------------------------------------------------------------------------

ALTER TABLE `my_table`
    ADD COLUMN birthday DATE;
*/


-- #############################################################################
-- MSM Section 260: Removal of PROCEDUREs and FUNCTIONs
-- -----------------------------------------------------------------------------
-- The DROP of every PROCEDURE and FUNCTION the new version no longer has,
-- which cannot run inside Section 240. Every deployment of this or a later
-- version runs this section, a fresh install too, after the update steps and
-- before Section 150, which creates all routines the deployed version has
-- again. So use DROP IF EXISTS. If there are no changes required, this section
-- can be skipped.
-- #############################################################################

-- Drop the removed PROCEDUREs and FUNCTIONs here.


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

-- Drop optional helper PROCEDUREs and FUNCTIONs here.


-- #############################################################################
-- MSM Section 910: Database Schema Version Definition
-- -----------------------------------------------------------------------------
-- Setting the correct database schema version.
-- #############################################################################

USE ${schema_identifier};

CREATE OR REPLACE SQL SECURITY INVOKER
VIEW `msm_schema_version` (`major`,`minor`,`patch`) AS
SELECT ${version_comma_str};


-- #############################################################################
-- MSM Section 920: Server Variable Restoration
-- -----------------------------------------------------------------------------
-- Restore the modified server variables to their original state.
-- #############################################################################

SET SQL_MODE=@OLD_SQL_MODE;
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
