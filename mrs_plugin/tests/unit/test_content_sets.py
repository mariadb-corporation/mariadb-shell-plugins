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


# --- the OpenAPI UI ------------------------------------------------------------

# What of Swagger UI's dist files the patches look for, and the files the
# UI must leave out: another flavour's dist, source maps, ES bundles.
_SWAGGER_FILES = {
    "dist/swagger-initializer.js": 'window.ui = SwaggerUIBundle({\n'
    '    url: "https://petstore.swagger.io/v2/swagger.json",\n});\n',
    "dist/swagger-ui-bundle.js": 'x("btn authorize unlocked",onClick:i},y)',
    "dist/swagger-ui.css": ".swagger-ui .topbar a{align-items:center;color:#fff;"
    "display:flex;}",
    "dist/index.html": "<html><head>\n    <title>Swagger UI</title>\n"
    '    <link rel="icon" type="image/png" href="./favicon-32x32.png" '
    'sizes="32x32" />\n'
    '    <link rel="icon" type="image/png" href="./favicon-16x16.png" '
    'sizes="16x16" />\n</head></html>\n',
    "dist/index.css": "html { box-sizing: border-box; }\n",
    "dist/favicon-16x16.png": b"\x89PNG16",
    "dist/favicon-32x32.png": b"\x89PNG32",
    "dist/swagger-ui-bundle.js.map": "{}",
    "dist/swagger-ui-es-bundle.js": "export {}",
    "flavors/swagger-ui-react/dist/index.js": "react",
}


def _swagger_archive(files=_SWAGGER_FILES):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(f"swagger-ui-{lib.open_api_ui.SWAGGER_UI_VERSION}/{name}", data)
    return buffer.getvalue()


@pytest.fixture
def offline_swagger(monkeypatch):
    """Swagger UI and its dark theme, without going to github.com."""
    fetched = []

    def fetch(url, sha256):
        fetched.append(url)
        if url == lib.open_api_ui.SWAGGER_UI_URL:
            return _swagger_archive()
        assert url == lib.open_api_ui.DARK_CSS_URL
        return b".dark { color: #eee; }\n"

    monkeypatch.setattr(lib.open_api_ui, "download", fetch)
    # prepare() took the default when it was defined.
    monkeypatch.setattr(
        lib.open_api_ui.prepare, "__defaults__",
        lib.open_api_ui.prepare.__defaults__[:-1] + (fetch,),
    )
    return fetched


def test_the_open_api_ui_is_swagger_ui_patched_for_the_service(tmp_path, offline_swagger):
    messages = []

    names = lib.open_api_ui.prepare(
        str(tmp_path), "Shop", "/shop", "/openApiUi",
        send_gui_message=lambda kind, text: messages.append(text),
    )

    # Swagger's own dist only, its favicons swapped for MariaDB's.
    assert names == sorted([
        "favicon.ico", "favicon.svg", "index.css", "index.html",
        "swagger-initializer.js", "swagger-ui-bundle.js", "swagger-ui.css",
    ])
    read = lambda name: (tmp_path / name).read_text()
    assert 'url: "/shop/open-api-catalog/"' in read("swagger-initializer.js")
    assert ('onClick:()=>{window.location.href="/?service=/shop&redirectUrl='
            '/shop/openApiUi/index.html";}}') in read("swagger-ui-bundle.js")
    assert "display:none;" in read("swagger-ui.css")
    index = read("index.html")
    assert "<title>Shop - OpenAPI UI</title>" in index
    assert "favicon.svg" in index and "png" not in index
    assert ".dark { color: #eee; }" in read("index.css")
    assert messages[0].startswith("Downloading Swagger UI")


def test_an_open_api_ui_that_cannot_be_patched_is_refused(tmp_path, monkeypatch):
    files = dict(_SWAGGER_FILES)
    files["dist/swagger-ui-bundle.js"] = "a newer bundle"

    def fetch(url, sha256):
        return _swagger_archive(files) if url.endswith(".zip") else b""

    with pytest.raises(ValueError, match="swagger-ui-bundle.js could not be patched"):
        lib.open_api_ui.prepare(str(tmp_path), "Shop", "/shop", fetch=fetch)


def test_a_download_that_is_not_the_file_expected_is_refused(monkeypatch):
    import io

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        lib.open_api_ui.urllib.request, "urlopen",
        lambda url, timeout=None: Response(b"something else"),
    )
    with pytest.raises(ValueError, match="not the file expected"):
        lib.open_api_ui.download("https://example.com/x.zip", "0" * 64)


def test_load_content_set_uploads_the_open_api_ui(phone_book, offline_swagger):
    session = phone_book["session"]

    # No directory with it, and no content set path needed.
    with pytest.raises(ValueError, match="pass no directory"):
        load_content_set(
            "/tmp", "/openApiUi", service_path="/test", open_api_ui=True,
            session=session,
        )

    result = load_content_set(
        service_path="/test", open_api_ui=True, session=session
    )
    try:
        assert "/swagger-initializer.js" in result["files"]
        files = file_contents(session, "/openApiUi")
        assert b'url: "/test/open-api-catalog/"' in files["/swagger-initializer.js"][0]
        # Served to everyone, and no scripts.
        assert {enabled for _, enabled in files.values()} == {1}
    finally:
        session.run_sql("DROP REST CONTENT SET /openApiUi FROM SERVICE /test")
