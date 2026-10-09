# MySQL REST Service (MRS) Grammar

The MRS grammar is written using ANTLR.

It is recommended to use the `ANTLR4 grammar syntax support` VS Code extension to write and debug the grammar.

## Use

The REST SQL statements are run by the mrs module built into the MariaDB Shell
(`modules/mrs` in the mariadb-shell repository), which has its own bison
grammar (`modules/mrs/core/mrs_parser.yy`). The two grammars are kept rule for
rule in step: a statement change goes to both.

This ANTLR grammar is the reference grammar of the documentation:

- `scripts/update_grammar_docs.py` checks the rule blocks of the SQL
  reference (`docs/sections/sql/*.md`) against it and updates them.
- `scripts/generate_rrd_svg_files.py` (npm script `update-rrd-svg-files`)
  renders its railroad diagrams into `docs/images/sql`.
- `test/grammar_test.sql` holds a statement of every kind, run by
  `scripts/run_grammar_test.sh` against a MariaDB Shell build.

Copyright (c) 2023, Oracle and/or its affiliates.
Copyright (c) 2026, MariaDB plc.
