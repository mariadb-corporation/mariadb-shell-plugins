<!-- Copyright (c) 2022, 2025, Oracle and/or its affiliates.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License, version 2.0,
as published by the Free Software Foundation.

This program is designed to work with certain software (including
but not limited to OpenSSL) that is licensed under separate terms, as
designated in a particular file or component or in included license
documentation.  The authors of MySQL hereby grant you an additional
permission to link the program and your derivative works with the
separately licensed software that they have either included with
the program or referenced in the documentation.

This program is distributed in the hope that it will be useful,  but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
the GNU General Public License, version 2.0, for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA -->

<!-- cSpell:ignore pandoc -->

# MRS Docs

This folder contains the MRS documentation.

## Generating Railroad Diagrams

The railroad diagrams in `./images/sql` (one SVG file per parser rule of `../grammar/MRSParser.g4`) are rendered by a Python script that needs only the Python standard library. Run it after every change of the grammar:

    python3 scripts/generate_rrd_svg_files.py

or the `update-rrd-svg-files` NPM script, from the `mrs_plugin` folder. It writes the files of new and changed rules and leaves unchanged ones alone.

- `--check` only reports which files would change and exits with 1 if any would.
- `--prune` deletes the files of rules that no longer exist (they are listed in every run).
- Rule names as arguments render only these rules.

The script produces the diagrams the ANTLR4 VS Code extension exported before (with `"antlr4.rrd.stripNamePart": "_SYMBOL|_OPERATOR"`, `"antlr4.rrd.wrapAfter": 50`, DATABASE shown as SCHEMA and the docs' style sheet), except that a diagram is always high enough to show everything drawn in it: the extension cut off the bottom of some diagrams, which had to be fixed by hand.

## Generate Distribution

The documentation is written in Markdown syntax. It is converted to HTML with the tool [pandoc](https://pandoc.org/).

### Installing pandoc 2.19.2

Download and install pandoc 2.19.2 from <https://github.com/jgm/pandoc/releases/tag/2.19.2>

### Installing pandoc-include

    pip install --user pandoc-include

### Generating the HTML file

A VS Code build tasks has been defined to build the documentation in the docs/dist folder. Press `Cmd + Shift + B` to start the build. The documentation is built using the `./scripts/generate_html_docs.sh` script.

Alternatively, the `update-html-docs` NPM script can be run.

To build the documentation manually, invoke the following command to generate the index.html page from `mrs_plugin/docs`:

    pandoc index.md -f markdown -t html -s -o index.html --template=templates/mysql_docs.html --toc --toc-depth=2 --metadata title="MySQL REST Service - Reference Manual" --variable=template_css:style/style.css --filter pandoc-include --number-sections

Repeat this for all other sections.

The style.css file is expected to be placed in a `dist/style/` folder.
