# Running SQL

The run commands, how a file is bound to a connection, the statement
scanner and the splitting it feeds, what makes a result set editable, and
the gutter markers.

Part of [PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md). Where the results end
up is in [result-view.md](result-view.md).

A `.sql` editor gets two toolbar buttons — a connection picker and a run
button — plus a status bar entry showing the connection it will run on
(`Ctrl`/`Cmd`+`Enter` runs it). The selection runs if there is one, else
the whole file.

## The two run commands

| Command | Default | What runs |
| --- | --- | --- |
| `mariadb.runSqlFile` | `Cmd`/`Ctrl`+`Enter` | The whole file, or the selection. |
| `mariadb.runSqlStatement` | `Shift`+`Enter` | Just the statement the cursor is in, which is also selected so it is plain what went. |

A third toolbar button toggles **stop on error** for the file in front
of you. A file starts out doing whatever `mariadb.execute.stopOnError`
says and can be switched for the session without changing the setting;
closing the file forgets the switch.

That button is two commands, `mariadb.stopOnError.disable` and
`...enable`, shown one at a time on the `mariadb.stopOnError` context
key: VS Code cannot swap a command's icon, so a button with two states
has to be two commands with two icons and a `when` clause between them.

`statementAtOffset()` finds the statement at the cursor, over the same
scanner the gutter dots use. A caret just after a statement counts as
inside it; in the whitespace between two, the *following* statement is
taken, because that is the one being typed into.

**The shortcuts are settings-driven**, which `contributes.keybindings`
does not support directly - it is static. The way round it: contribute
one binding per (command, chord) pair, each gated on a `when` clause
naming a context key, and set those context keys from the settings
(`src/editor/keybindings.ts`). `mariadb.keybindings.runScript` and
`mariadb.keybindings.runStatementAtCursor` therefore move the commands
between a fixed set of chords. Anything outside that set is still the
Keyboard Shortcuts editor's job.

## Which connection a file runs on

In order: the connection explicitly picked for that document, else the
one its header names, else the default.

**New SQL Editor** on a connection in the tree opens an unsaved `.sql`
file bound to it. The binding is held in memory keyed by document URI,
*and* written into the file as a header:

```sql
-- MariaDB connection: dba@localhost:3310
```

The header exists because an untitled document's URI changes the moment
it is saved, which would silently drop an in-memory binding. Writing it
into the file keeps the association across a save, across reopening and
across sharing the file, and leaves it visible so it can be corrected by
editing the line. An explicit pick still wins over it, so the status bar
is always the truth.

Tab captions skip leading comments (`dropLeadingComments`), or the first
result of every generated file would be captioned with its header.

`ExecutionService.execute()` then:

1. splits the script client-side (`splitStatements.ts`) so each result can
   be paired with the statement that produced it — the server splits again
   on its own side, and the two have to line up,
2. runs it with `db.execute_sql_script`,
3. sends statements without a result set to the **Output** tab and each
   result set to a tab of its own,
4. works out whether a result set can be written back.

## The statement scanner

`src/sql/statementSpans.ts` is a port of the MySQL Shell's
`MySQLParsingServices.determineStatementRanges`. It scans character by
character and records offsets, never building substrings as it goes,
which is what lets it run over a large file: about 120 MB/s, and around
80 ms for a 9.6 MB, 100k statement script.

Three deliberate differences from the original:

- **It is a generator.** Spans are yielded as they are found, so the
  first one is available in microseconds and the gutter can show dots
  long before a large file has been scanned through.
- **No dollar-quoted strings.** That branch guards a MySQL 8.1 feature
  for JavaScript stored programs. MariaDB has no dollar-quoted strings,
  so the branch could only ever mis-scan a `$`.
- **One off-by-one is corrected.** In the original, a word starting with
  `d` that is not DELIMITER left `contentStart` on its *second*
  character, so `delete from t` reported `elete from t`. Every other
  branch marks content before advancing the tail; that one advanced
  first.

`hasContent(span)` tells a real statement from a run of comments:
`contentStart` is deliberately set below `span.start` when there is no
content. **Every** content-free span has to say so that way — the spans a
comment left open at the end of the input produces used to set
`contentStart` to `span.start` instead, which reads as content, so a
trailing `-- note` with no newline after it counted as a statement while
the same line with a newline did not. That fed `splitStatements()`,
`statementAtOffset()` and the gutter dots alike.

## Splitting has to agree with the server

The results come back as a flat list and are paired with the statements by
position, so a client split that disagrees with the server's shifts every
result after the disagreement, which silently gives a grid the wrong
statement and loses its editability.

One rule is not obvious and was read off the running server rather than
guessed: **a line comment that begins a statement is a statement of its
own.** `-- a note` followed by a query is two statements, not one. It
holds for `--` and `#`, for an indented comment, for a comment after a
semicolon and for each of several comment lines in a row. It does *not*
hold for block comments, which stay with the statement that follows, nor
for a line comment in the middle of a statement, which stays inside it.

**Being a statement is not the same as being run.** The MCP plugin does
not execute a comment — it carries no SQL, and the server would accept it
as a query and report a result for it — but it still *numbers* one, so the
indexes on either side of it are unchanged. `splitStatements()` therefore
keeps the comment as an entry (the pairing depends on it) and marks it
`executable: false`. `execute()` filters on that for the two things that
are about what is being run rather than what was split: the
`Running N statements` count, and the `stopped after N of M` on a failed
run. `describeRun()` follows the same rule, so the row a run goes up
with says what the row that replaces it will say. A run's own row (and
the row for a call that failed outright) points at the first
**executable** statement, not at a header comment standing in front of
it - unless the run failed, in which case it points at its first error.

`src/test/sql/splitStatements.test.ts` pins all of these, and
`splitStatements()` also drops DELIMITER commands, which the server
consumes without producing a result.

Agreement was checked against a running server across 21 scripts
(comments in every position, quoted and escaped delimiters, block
comments, DELIMITER blocks, empty statements); it is worth re-running
that comparison after any change here.

## What makes a result set editable

`findUpdatableTarget()` accepts only a plain `SELECT ... FROM <one table>`.
Joins, unions, `GROUP BY`, `HAVING`, `DISTINCT`, subqueries, derived tables
and aggregates in the select list are all rejected, because the result then
no longer maps row for row onto stored rows. Literals and comments are
stripped first so a keyword inside either cannot be mistaken for the real
thing.

For a statement that passes, the table's columns are fetched with
`db.get_object_details` — that is where the primary key, the data types and
the auto-increment flags come from. An unqualified table name is resolved
against `SELECT DATABASE()`, asked at most once per execution. Without a
primary key in the result the grid stays read only, since there would be no
way to address a row.

Edits are held in the grid until **Apply**. `QueryBuilder` then generates
`UPDATE`, `INSERT` and `DELETE` statements — modelled on the MySQL Shell's
`QueryBuilder` — addressing each row by the primary key it had *before* the
edit, leaving auto-increment and generated columns out of an `INSERT`, and
running deletes last so a row that was edited and then removed is not
updated after it is gone.

## Statement markers in the gutter

`src/editor/statementDecorations.ts` puts a blue dot on the first line of
every statement of a visible `.sql` editor.

The scan is **sliced**: it runs for a few milliseconds, pushes what it has
found to the editor, hands control back and carries on. On a large file
the first dots are therefore on screen almost at once instead of after
the whole file has been parsed, and typing stays responsive. A full
re-apply is throttled to at most every 150 ms, since `setDecorations`
takes the whole set each time and re-applying a growing array on every
slice would be quadratic.

A scan is abandoned when its document changes under it (the version is
re-checked at every statement), when a newer scan starts for the same
document, when the document is closed, or when the decorator is disposed.
Edits schedule a rescan 300 ms after they stop.

Comments get no dot: the dot goes on `contentStart`, not `span.start`.
DELIMITER commands get none either, so the dots line up with the
statements the result tabs come from.

The ranges come from the scanner **for now**. They are the same thing a
language server would report, so `StatementDecorator.decorate()` is the
seam to replace when one exists: everything else takes ranges and knows
nothing about where they came from.
