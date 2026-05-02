# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
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
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from os import environ
from threading import Lock

DEBUG_MODE_ENV_VAR = "MYSQL_SHELL_GUI_DEBUG_MODE"


class BackendDbLogger:
    __instance = None
    __gui_backend_db = None
    lock = Lock()

    @staticmethod
    def get_instance(log_rotation=False) -> 'BackendDbLogger':
        if BackendDbLogger.__instance is None:
            BackendDbLogger(log_rotation)

        assert BackendDbLogger.__instance
        return BackendDbLogger.__instance

    @staticmethod
    def is_enabled():
        return environ.get(DEBUG_MODE_ENV_VAR, '0') == '1'

    def __init__(self, log_rotation):
        if BackendDbLogger.__instance is not None:
            raise Exception(
                "This class is a singleton, use get_instance function to get an instance.")
        else:
            from gui_plugin.core.Db import GuiBackendDb
            BackendDbLogger.__instance = self
            self.__gui_backend_db = GuiBackendDb(log_rotation=log_rotation)

    @staticmethod
    def close():
        if BackendDbLogger.__instance is not None:
            BackendDbLogger.__instance.__gui_backend_db.close()
            BackendDbLogger.__instance.__gui_backend_db = None
            BackendDbLogger.__instance = None

    @staticmethod
    def message(session_id, message, is_response, request_id=None):
        if not BackendDbLogger.is_enabled():
            return True

        instance = BackendDbLogger.get_instance()
        if not instance.__gui_backend_db:
            return False

        with instance.lock:
            try:
                instance.__gui_backend_db.start_transaction()
                instance.__gui_backend_db.message(session_id, is_response, message, request_id)
                instance.__gui_backend_db.commit()
            except Exception:
                instance.__gui_backend_db.rollback()
                return False
            return True

    @staticmethod
    def log(event_type, message):
        if not BackendDbLogger.is_enabled():
            return True

        instance = BackendDbLogger.get_instance()
        if not instance.__gui_backend_db:
            return False

        with instance.lock:
            try:
                instance.__gui_backend_db.start_transaction()
                instance.__gui_backend_db.log(event_type, message)
                instance.__gui_backend_db.commit()
            except Exception:
                instance.__gui_backend_db.rollback()
                return False
            return True
