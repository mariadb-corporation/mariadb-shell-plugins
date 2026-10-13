# The MariaDB SQL language and its grammar

The extension contributes the language `.sql` files open in - **MariaDB
SQL**, `mariadb-sql` - with a TextMate grammar for syntax highlighting.
This file is where the grammar comes from, what it can and cannot do,
how it is tested, and how it is kept in step with the servers it
highlights for.

## One language; ANSI_QUOTES is a semantic highlight

What `"..."` means depends on the connection's `sql_mode`: a string by
default, an identifier under `ANSI_QUOTES`. A grammar cannot know the
connection, so the grammar reads it as a string, and
`AnsiQuotesHighlighter` (`src/editor/ansiQuotes.ts`, see
[running-sql.md](running-sql.md)) - a semantic tokens provider - reports
every `"..."` of a document whose connection has ANSI_QUOTES as the
token type `sqlQuotedIdentifier`. `package.json` declares that type
(`semanticTokenTypes`, super type `variable`) and maps it to the
grammar's own quoted identifier scope (`semanticTokenScopes` ->
`entity.name.identifier.quoted.sql`), so the theme colours it exactly
as a backtick name. The spans come from `src/sql/doubleQuotedSpans.ts`,
which follows the server's lexer: `""` is the one escape in an
identifier, a backslash is a character, single-quoted strings,
backticks and comments are skipped, a versioned comment's body is code,
and a `"..."` straight after `@` (a variable's name, an account's host)
is left to the grammar. A semantic token cannot span lines, so a
multi-line span becomes a token per line.

**Two languages were built first and dropped (2026-10-12).** One grammar
per mode (`mariadb-sql-ansi` beside `mariadb-sql`, built from the one
template) with each document moved between them by
`setTextDocumentLanguage`. That call **closes and reopens the document**
as far as every extension is concerned (`onDidCloseTextDocument`, then
`onDidOpenTextDocument` - documented in `vscode.d.ts`), so the
binding's close handler forgot a connection picked in the status bar
and the file's Stop on Error choice, and the reopen applied the
header's connection's mode back. Semantic tokens never touch the
document's language. What they cost:

- They show only where `editor.semanticHighlighting.enabled` is on. Its
  default leaves it to the theme; every built-in theme turns it on
  (2026, Dark+ / Light+ through `dark_vs` / `light_vs`, Dark/Light
  Modern through those, High Contrast). A third-party theme that does
  not shows ANSI identifiers as strings.
- They arrive after the grammar's colours: an ANSI file shows `"x"` as
  a string for a moment on open, and the first time a connection is
  asked for its `sql_mode`.
- They recolour; they cannot retokenize. The grammar still applies a
  string's backslash escapes inside `"..."`, so after an odd backslash
  (`"a\"b"`) the grammar's idea of where the quote ends differs from
  the server's. The provider marks the server's span; text the grammar
  then thinks is quoted keeps the string colour.

The built-in `sql` language (aliases "MS SQL", "T-SQL") still exists and
a user can set a file to it by hand; everything that checks the language
(`isSqlDocument` in `src/editor/sqlLanguage.ts`, the `when` clauses in
`package.json`, which use `=~ /^(sql|mariadb-sql)$/`, and the semantic
tokens provider's selector) takes both, so such a file keeps its
statement markers, run commands and ANSI identifiers.

## A tokenizer, so: lexically complete, contextually blind

A TextMate grammar is a regex tokenizer run line by line, so only the
**lexers** of the two sources matter to it, not their parsers. It
reproduces every token class of the server's `sql/sql_lex.cc`
(`lex_one_token`, the `MY_LEX_*` states) and of the shell's
`modules/mrs/core/mrs_lexer.h`, and names every keyword, function and
annotation. What it cannot do is what the parser does, and each of
those has a decided policy:

- **Non-reserved keywords** (418 of the 691 keyword words) can be
  identifiers - `name`, `status`, `type` as column names. They are
  coloured like reserved ones (the user's decision, 2026-10-12), but in a
  scope of their own, `keyword.other.unreserved.sql` against
  `keyword.other.reserved.sql`, so a theme or a later rule can tell them
  apart without a regeneration.
- **A name is a function only directly before `(`**, as in the lexer
  (`find_keyword(..., c == '(')`): `LEFT JOIN` is a keyword, `LEFT(` a
  function, a column may be called `date`. The function list is the
  union of `lex.h`'s `sql_functions[]`, the native functions of
  `item_create.cc` / `item_geofunc.cc`, and the keywords the parser's
  `function_call_*` rules accept with `(` (`DATE(`, `CHAR(`, `INSERT(`).
- **`sql_mode`.** `ANSI_QUOTES` is a semantic highlight, above. The
  default mode is assumed otherwise: backslash escapes in strings
  (`NO_BACKSLASH_ESCAPES` is not modelled), no Oracle `q'...'`.
- **Versioned comments** `/*!...*/`, `/*M!...*/` and the reversed
  `/*!!...*/` hold SQL: the markers and the version are a comment, the
  body is tokenized as code (`meta.comment.versioned.sql`, body
  `#tokens`). Inside one, another `/*!` is a plain comment, as in the
  server, and the outer one runs to the next `*/`. The server would run
  or skip the body by version; the grammar cannot know the server.
- **`DELIMITER x`** is recognised on a line of its own (the client
  command, as the statement splitter expects it); the custom delimiter
  itself is not tracked.
- **REST request paths** `/svc/schema` are recognised after whitespace
  only: `a/b` and `a / b` are divisions, `a /b` is read as a path, which
  only the parser could tell apart.
- A `keyword.other.rest.sql` is a word REST SQL adds; a REST keyword the
  server knows too (`SELECT`, `CREATE`, `VIEW`) stays the server's.

Everything else is plain TextMate practice worth knowing when editing
the template: at one position the **earlier rule wins, not the longer
match** (so `<<` is listed before `<`), a `begin`/`end` rule's `end` is
tried before its inner patterns unless `applyEndPatternLast` is set
(the `''`, `""` and ``` `` ``` escapes need it), identifiers use
`(?<![\w$])`/`(?![\w$])` rather than `\b` because `$` is an identifier
character (`SELECT$x` is a name), and a number followed by an identifier
character is an identifier (`1e`, `123abc`), as is one after a
qualifying dot (`t.1`) but not after the range operator (`1..10`).
Identifiers get `entity.name.identifier.sql` (quoted:
`entity.name.identifier.quoted.sql`), which the 2026 default themes
colour orange and Dark+ / Dark Modern leave in the foreground colour.
They were `meta.identifier.sql`, unthemed, until the user asked for
colour (2026-10-12); `variable.other.sql` was tried and dropped, as the
2026 themes colour it barely apart from the foreground. User and system
variables are `variable.language.user.sql` / `variable.language.system.sql`,
blue in both theme families - the keywords' blue in Dark+ / Dark Modern,
apart from them in the 2026 themes. `variable.parameter` was tried and
dropped: the 2026 themes colour it orange, like the identifiers. Any character no rule knows gets
`invalid.illegal.character.sql`, which is what the server makes of it.
Colours themselves are the theme's: `keyword` is red in the 2026 themes
and blue in Dark Modern, and the grammar's only lever is the scope name.

## The word lists are generated; the token rules are not

`tools/grammar.mts` (run by Node 24 directly, which strips the types;
not part of the build or the lint) owns the lists:

```text
npm run grammar:sync -- --server ~/git/mariadb-server --shell ~/git/mariadb-shell
npm run grammar:build          # template + words.json -> the grammar
npm run grammar:check          # the same, failing if it differs: for CI
```

`sync` reads, from the server, `sql/lex.h` (`symbols[]`,
`sql_functions[]`), `sql/sql_yacc.yy` (the `keyword_*` and
`non_reserved_keyword_udt` rules say which tokens are non-reserved - the
Oracle-mode `%ifdef` blocks included, since one grammar highlights both
modes - and the `function_call_*` rules' `TOKEN '('` alternatives),
`sql/item_create.cc` (`func_array[]`) and `sql/item_geofunc.cc`
(`func_array_geom[]`); from the shell, `MRS_KEYWORD_LIST` and
`MRS_ANNOTATION_LIST` of `modules/mrs/core/mrs_lexer.h`. It writes
`syntaxes/words.json` with the lists and the commit of each checkout.
`build` fills the template's `${RESERVED}`, `${UNRESERVED}`,
`${FUNCTIONS}`, `${REST_KEYWORDS}`, `${REST_ANNOTATIONS}` with
alternations (longest first). A `${...}` anywhere else in the template,
a comment included, is an error.

So a server or shell release is a `sync`, a `build`, a look at the diff
of `words.json` and the snapshots, and a commit; the token rules are
touched only when `sql_lex.cc` gains a state, which it rarely does. The
template, `words.json` and `tools/` are in `.vscodeignore`: only the
built grammar ships. The checked-in grammar must be rebuilt after an
edit of the template; `words.test.ts` runs `grammar:check` to make sure.

Snapshot of the lists at the first sync (server `main` 2026-07-24, shell
26.10.1): 273 reserved, 418 unreserved, 479 functions, 133 REST keywords
(63 not server keywords), 19 annotations.

## Tests, three layers (`src/test/grammar/`)

They tokenize with `vscode-textmate` on `vscode-oniguruma` - what VS Code
itself uses - through `tokenize.ts`, so what a test sees is what the
editor colours, and no VS Code is needed; they are part of the
`extension` vitest project.

1. **`words.test.ts`, generated coverage.** Every word of `words.json`:
   reserved and non-reserved keywords get their scope
   (upper and lower case; `NULL`, `TRUE`, `FALSE`, `UNKNOWN` are
   constants instead), every function is a function before `(` and not
   without, every REST keyword and annotation is coloured, and a word
   that only starts with a keyword (`SELECTION`, `SELECT$1`) is a name.
   A sync that adds a word the grammar does not reach fails here.
2. **`tokens.test.ts`, token classes.** One group per lexer state:
   comments of every kind, `DELIMITER`, strings with introducers and
   escapes across lines, `"..."` as a string, quoted identifiers,
   numbers in every notation, constants, user and system variables,
   `user@host`, placeholders, operators and punctuation, REST paths and
   annotations. Each case is a line and the innermost scope of each of
   its tokens, whitespace left out (`scopesOf`), so a case reads like
   the line.
3. **`corpus.test.ts`, real SQL.** `corpus/sakila-schema.sql` and the
   shell's REST grammar test (`unittest/data/mrs/grammar_test.sql`,
   copied as `rest-grammar-test.sql`), tokenized whole against a
   `.tokens` file snapshot (`toMatchFileSnapshot`; one line of SQL, then
   its tokens), so a rule change shows as the tokens it moved, in
   review. Plus the invariant that no non-whitespace token is left
   without a scope. Refresh the corpora from their sources as they grow;
   `vitest -u` rewrites the snapshots after a deliberate change.

The UI test (`04-sql-results.test.ts`) checks what the unit tests
cannot: that a new SQL editor and a `.sql` file opened from disk are in
"MariaDB SQL" - the extension's claim on `.sql` wins over the built-in
`sql` language - and that the editor colours the tokens.

The ANSI_QUOTES highlight is tested apart from the grammar:
`src/test/sql/doubleQuotedSpans.test.ts` pins the spans (escapes,
strings, backticks, comments, versioned comments, `@"..."`), and
`src/test/editor/ansiQuotes.test.ts` the provider - registration,
tokens per line, the setting, one `sql_mode` read per connection,
refreshes - against the mock's `SemanticTokensBuilder`, whose
`decodeSemanticTokens` turns the relative encoding back into positions.
No UI test covers it yet: the UI tests' sandbox has no ANSI_QUOTES.

## Not done, on purpose or not yet

- Data types have no scope of their own (`storage.type`); they are
  keywords. The parser's `*_type` rules could supply the list.
- `CURRENT_TIMESTAMP()` and its kin take optional parentheses through
  `opt_time_precision`, not a `'('` alternative, so they stay keywords
  before `(`.
- No semantic tokens, completion or diagnostics: those would be an LSP's
  job, and the grammar is deliberately only a tokenizer.
