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


import os
import pytest
import tempfile

from mrs_plugin import lib
from mrs_plugin.content_sets import load_content_set

MRS_SCRIPT = """@Mrs.module({ name: "hello", requestPath: "/hello" })
class Hello {
    @Mrs.script({ name: "greet", requiresAuth: false })
    public static async greet(name: string): Promise<string> {
        return "Hello " + name;
    }
}
"""


def write_file(directory, relative_path, data):
    path = os.path.join(directory, *relative_path.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def file_contents(session, content_set_path):
    rows = session.run_sql(
        lib.core.metadata_sql(
            session,
            """SELECT f.request_path, f.content, f.enabled
        FROM <metadata>.content_file f
            JOIN <metadata>.content_set cs
                ON cs.id = f.content_set_id
            JOIN <metadata>.service se ON se.id = cs.service_id
        WHERE cs.request_path = ? AND se.url_context_root = '/test'
        ORDER BY f.request_path""",
        ),
        [content_set_path],
    ).fetch_all()
    return {row[0]: (bytes(row[1]), row[2]) for row in rows}


def test_load_content_set(phone_book):
    session = phone_book["session"]

    binary = bytes(range(256))
    with tempfile.TemporaryDirectory() as directory:
        write_file(directory, "index.html", '<html>It\'s "here"</html>\n')
        write_file(directory, "css/site.css", "a::before { content: '\\2014'; }\n")
        write_file(directory, "img/logo.png", binary)
        write_file(directory, "node_modules/lib/index.js", "ignored")
        write_file(directory, ".git/config", "ignored")

        result = load_content_set(
            directory, "/uploaded", service_path="/test", session=session
        )
        try:
            assert sorted(result["files"]) == [
                "/css/site.css",
                "/img/logo.png",
                "/index.html",
            ]
            files = file_contents(session, "/uploaded")
            assert files == {
                "/css/site.css": (b"a::before { content: '\\2014'; }\n", 1),
                "/img/logo.png": (binary, 1),
                "/index.html": (b'<html>It\'s "here"</html>\n', 1),
            }

            # The content set exists now
            with pytest.raises(Exception):
                load_content_set(
                    directory, "/uploaded", service_path="/test", session=session
                )
        finally:
            session.run_sql("DROP REST CONTENT SET /uploaded FROM SERVICE /test")


def test_load_content_set_with_scripts(phone_book):
    session = phone_book["session"]

    with tempfile.TemporaryDirectory() as directory:
        write_file(directory, "src/hello.mts", MRS_SCRIPT)
        write_file(directory, "dist/hello.mjs", "export class Hello {}\n")
        write_file(directory, "static/index.html", "<html></html>\n")

        result = load_content_set(
            directory, "/scripts", service_path="/test", session=session
        )
        try:
            assert sorted(result["files"]) == [
                "/dist/hello.mjs",
                "/src/hello.mts",
                "/static/index.html",
            ]
            assert "1 MRS script(s) of 1 module(s) registered" in result["message"]

            # Only the static folders are served, the sources and the build
            # output are private
            files = file_contents(session, "/scripts")
            assert files["/src/hello.mts"][1] == 2
            assert files["/dist/hello.mjs"][1] == 2
            assert files["/static/index.html"][1] == 1

            script = session.run_sql(
                "SHOW CREATE REST CONTENT SET /scripts ON SERVICE /test"
            ).fetch_one()[0]
            assert script.endswith("LOAD TYPESCRIPT SCRIPTS;")
        finally:
            session.run_sql("DROP REST CONTENT SET /scripts FROM SERVICE /test")

        # Without registering the scripts
        result = load_content_set(
            directory,
            "/scripts",
            service_path="/test",
            load_scripts=False,
            session=session,
        )
        try:
            assert "registered" not in (result["message"] or "")
            script = session.run_sql(
                "SHOW CREATE REST CONTENT SET /scripts ON SERVICE /test"
            ).fetch_one()[0]
            assert "LOAD TYPESCRIPT SCRIPTS" not in script
        finally:
            session.run_sql("DROP REST CONTENT SET /scripts FROM SERVICE /test")
