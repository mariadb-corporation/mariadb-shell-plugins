/*
 * Copyright (c) 2026, Oracle and/or its affiliates.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License, version 2.0,
 * as published by the Free Software Foundation.
 *
 * This program is designed to work with certain software (including
 * but not limited to OpenSSL) that is licensed under separate terms,
 * as designated in a particular file or component or in included
 * license documentation.  The authors of MySQL hereby grant you an
 * additional permission to link the program and your derivative works
 * with the separately licensed software that they have either included
 * with the program or referenced in the documentation.
 *
 * This program is distributed in the hope that it will be useful,  but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License, version 2.0, for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
 */


PRAGMA foreign_keys = OFF;

-- -----------------------------------------------------
-- Privileges for Single Server User
-- -----------------------------------------------------
BEGIN TRANSACTION;
UPDATE `privilege`
SET `access_pattern` = 'gui\.users\.(get_gui_module_list|list_profiles|get_profile|add_profile|get_default_profile|set_default_profile|set_web_session_profile)'
WHERE `id` = 5;

UPDATE `privilege`
SET `access_pattern` = '^(?!(?:gui\.(?:shell|users)\b))(?:(gui|mrs|mds|msm))\.[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?$'
WHERE `id` = 6;

INSERT INTO `role_has_privilege` (`role_id`, `privilege_id`) VALUES (4, 5);

COMMIT;

-- -----------------------------------------------------
-- View `schema_version`
-- -----------------------------------------------------
DROP VIEW IF EXISTS `schema_version`;
CREATE VIEW schema_version (major, minor, patch) AS SELECT 0, 0, 24;

PRAGMA foreign_keys = ON;
