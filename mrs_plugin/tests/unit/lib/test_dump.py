# Copyright (c) 2021, 2026, Oracle and/or its affiliates.
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
#
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import json
import os
import tempfile

import mrs_plugin.lib as lib


def _insert_audit_row(session, schema, id):
    session.run_sql(
        f"INSERT INTO `{schema}`.audit_log (id, table_name, dml_type, changed_by, changed_at) "
        "VALUES (?, 'test_dump', 'UPDATE', 'test', NOW(6))",
        [id],
    )


def _exported_ids(file_path):
    with open(file_path) as f:
        return [int(line.split(" ")[2]) for line in f]


def test_export_audit_log_writes_rows_committed_late(init_mrs):
    # Audit log ids are not in commit order: on Galera with several write
    # nodes a row with a lower id can become visible after a higher one was
    # exported. The export looks again below its position.
    session = init_mrs
    schema = lib.core.metadata_schema(session)
    with tempfile.TemporaryDirectory() as dir:
        log_file = os.path.join(dir, "audit.log")
        position_file = os.path.join(dir, "mrs_audit_log_position.json")
        try:
            lib.dump.export_audit_log(
                log_file, starting_from_today=False, session=session
            )
            with open(position_file) as f:
                position = json.load(f)
            last_id = session.run_sql(
                f"SELECT MAX(id) FROM `{schema}`.audit_log"
            ).fetch_one()[0]
            assert position["position"] == last_id
            assert last_id in position["exportedIds"]
            assert _exported_ids(log_file)[-1] == last_id

            # "Another node" commits a higher id first, then a lower one
            _insert_audit_row(session, schema, last_id + 100)
            lib.dump.export_audit_log(
                log_file, starting_from_today=False, session=session
            )
            _insert_audit_row(session, schema, last_id + 50)
            lib.dump.export_audit_log(
                log_file, starting_from_today=False, session=session
            )
            lib.dump.export_audit_log(
                log_file, starting_from_today=False, session=session
            )

            ids = _exported_ids(log_file)
            assert ids[-2:] == [last_id + 100, last_id + 50]
            assert len(ids) == len(set(ids))
            with open(position_file) as f:
                position = json.load(f)
            assert position["position"] == last_id + 100
            assert last_id + 50 in position["exportedIds"]

            # An explicit position counts everything up to it as exported
            other_log = os.path.join(dir, "other.log")
            _insert_audit_row(session, schema, last_id + 60)
            lib.dump.export_audit_log(
                other_log,
                audit_log_position_file=os.path.join(dir, "other.json"),
                audit_log_position=last_id + 100,
                starting_from_today=False,
                session=session,
            )
            assert not os.path.exists(other_log)

            # The writeable check uses MariaDB's read_only
            lib.dump.export_audit_log(
                log_file,
                starting_from_today=False,
                when_server_is_writeable=True,
                session=session,
            )
            assert _exported_ids(log_file)[-1] == last_id + 60
        finally:
            session.run_sql(
                f"DELETE FROM `{schema}`.audit_log WHERE table_name = 'test_dump'"
            )
