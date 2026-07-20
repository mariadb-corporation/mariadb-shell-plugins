---
description: Save a compressed summary of this session so a future session can resume. Usage: /checkpoint <path-to-project-subfolder>
---

# Checkpoint Command

Target directory for this checkpoint: $1

If $1 is empty, ask which project subfolder this checkpoint is for before doing anything else — do not guess, since this workspace contains multiple projects.

Otherwise:

1. Confirm that $1 exists as a directory. If it doesn't, stop and report that instead of creating it blindly.
2. Review this conversation and the current state of files under $1, then write (overwrite) `$1/.claude/PROJECT_CONTEXT.md` with a compressed summary structured as:

## Project

One-paragraph description of what this project is and its goal.

## Architecture / key decisions

Bullet list of non-obvious design choices and why.

## Current state

- What's implemented and working
- What's in progress
- What's broken/known issues

## Files that matter

Path -> one-line purpose, for files central to this work.

## Next steps

Concrete, ordered list of what to do next.

## Gotchas / things not to repeat

Dead ends already tried and why they failed.

Keep it dense — favor bullet points over prose, omit anything derivable by reading the code.

1. Run `git -C $1 status --short` and `git -C $1 branch --show-current`, append under a "Git state" heading.
2. Confirm back to the user the exact path you wrote to.
