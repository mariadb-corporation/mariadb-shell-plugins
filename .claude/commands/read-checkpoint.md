---
description: Reads a compressed summary of the last session so this session can resume. Usage: /read-checkpoint <path-to-project-subfolder>
---

# Read Checkpoint Command

Source directory for reading the checkpoint: $1

If $1 is empty, each project subfolder that contains a `.claude/PROJECT_CONTEXT.md` needs to be processed. This is important since this workspace contains multiple projects.

When a context file `$1/.claude/PROJECT_CONTEXT.md` already exists, read this file to be able to resume the last session.

## A. `$1/.claude/context/` exists — split layout

`$1/.claude/PROJECT_CONTEXT.md` is the index: the description, the top-level layout table, a table of the context files with what is in each, and whatever cross-cutting sections it carries.

1. Read the index first. It tells you which context file covers which area.
2. Read only the context files this session's work touched — that is the point of the split, so do not read the whole set to write a checkpoint.

## B. `$1/.claude/PROJECT_CONTEXT.md` exists, with no `context/` — single file

Read it, it has the full session information.

## C. Neither exists — new project

If $1 is not empty but the folder does not exist, error out stating that `$1/.claude/PROJECT_CONTEXT.md` does not exist and the user needs to run the `/checkpoint $1` command first.
