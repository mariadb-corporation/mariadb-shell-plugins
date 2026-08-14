# Copyright (c) 2021, 2024, Oracle and/or its affiliates.
# Copyright (c) 2026, MariaDB plc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

# Where the shell keeps its bundled libraries and plugins, relative to the build
# or install root. The shell defines INSTALL_LIBDIR in its top-level
# CMakeLists.txt (see MARIADB_PORT.md 14.1); fall back to the same value it uses
# so this file also works when configured standalone.
IF(NOT INSTALL_LIBDIR)
  SET(INSTALL_LIBDIR "lib/mariadb-shell")
ENDIF()

ADD_CUSTOM_COMMAND(OUTPUT copy_external_plugins
    COMMAND ${CMAKE_COMMAND} -E make_directory ${CONFIG_BINARY_DIR}/${INSTALL_LIBDIR}
    COMMENT "Copying External Plugins"
)

ADD_CUSTOM_TARGET(external_plugins ALL DEPENDS copy_external_plugins)

###
### Handle external plugin packaging (Either JS/PY)
### Each plugin must be self contained into a folder in plugins/external
### They will be copied into the shell build folder and so bundled into the
### shell package
###

# Copies any folder in the current to the build plugins folder
FILE(GLOB children RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/*)
SET(PLUGIN_LIST "")
FOREACH(child ${children})
  IF(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/${child})
    ADD_CUSTOM_COMMAND(TARGET external_plugins
        PRE_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}/${child} ${CONFIG_BINARY_DIR}/${INSTALL_LIBDIR}/plugins/${child}
        COMMENT "Copying External Shell Plugin"
    )
  ENDIF()
ENDFOREACH()
