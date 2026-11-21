---
name: planning
description: Plan before acting on non-trivial or ambiguous work - new features, refactors, migrations, multi-file changes, projects with several steps. Explore first, ask only blocking questions, write a short plan file, get the user's OK, then execute against it with the todo tools.
---
# Planning

Use this when a wrong first move would waste real effort: the task touches several files or systems, the request is ambiguous, or there are several reasonable approaches. Skip it for one-step jobs.

## 1. Explore (read-only)
Before proposing anything, learn how things work now:
- `list_dir`, `glob` and `grep` to find the relevant code, config and docs; read_file the key parts.
- `git_log` / `git_status` to see recent changes and the current state.
- Look for existing helpers, patterns and conventions to reuse instead of inventing new ones.
- Note constraints: tests, lint rules, deployment, data that must not be lost.
Make no changes in this phase.

## 2. Ask only what blocks you
List what you don't know. For each item: can you find it in the code, decide it with a sensible default, or is it truly the user's call? Ask the user only the last kind, all together, each with your recommended answer, e.g. "Store it in SQLite (recommended, the app already uses it) or a JSON file?"

## 3. Write the plan
Put it in a file the user can read and you can come back to: `<workspace>/<project>/PLAN.md` (or the repo's own docs folder if the user prefers). Keep it short enough to scan:
```markdown
# <Goal in one line>

## Context
Why this is being done and what "done" looks like.

## Approach
The chosen design in a few bullets, and why (one line on the main alternative you rejected).

## Changes
- path/to/file.py — what changes
- new: path/to/new.py — what it holds

## Steps
1. … (each step small enough to verify on its own)

## Verification
Commands/tests/manual checks that prove it works.

## Risks / open questions
```

## 4. Get approval
Summarise the plan in the reply (goal, approach, steps, anything the user must decide) and ask the user to approve or change it. Don't start the changes until they agree, unless they already told you to go ahead without asking.

## 5. Execute against the plan
- Mirror the steps into `todo_add`; mark each `in_progress` when you start it and `done` when it's verified.
- Verify each step before moving on (run the tests, check output).
- When reality differs from the plan (an assumption was wrong, a step is bigger), update PLAN.md and tell the user if it changes scope or design. Don't silently drift.
- Finish with: what was done, how it was verified, anything left over.
