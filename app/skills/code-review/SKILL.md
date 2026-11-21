---
name: code-review
description: Review code changes for real bugs - the local working tree, a branch against its base, a commit, or a GitHub pull request URL. Finds correctness, security and error-handling problems, verifies each one, and reports them ranked by severity with file:line and a concrete failure scenario.
---
# Code review

The goal is to find the bugs a careful senior engineer would catch, and to report **only findings you have checked**. A short list of real problems beats a long list of guesses.

## 1. Work out the target
| User says | Get the diff with |
|---|---|
| nothing / "my changes" / a repo path | `git_status`, then `git_diff(stat=true)` for the overview, `git_diff` (unstaged) and `git_diff(staged=true)` |
| a branch | `git_log` to find the base, then `git_diff(ref="main...HEAD")` (use the real base branch) |
| a commit | `git_log(ref="<sha>", max_count=1, patch=true)` |
| a GitHub PR URL | `web_fetch` `<pr-url>.diff` for the diff and the PR page for its description; if the repo is cloned locally, `git fetch origin pull/<n>/head:pr-<n>` with bash and review locally |

If there is nothing to review, say so and stop. For large diffs (> ~1500 changed lines), say which parts you prioritised.

## 2. Understand the intent
Read the PR description / commit messages and any linked issue. State to yourself in one sentence what the change is meant to do; a bug is behaviour that contradicts that intent or breaks something that used to work.

## 3. Read the code, not just the diff
- Read each changed file in full around the change (read_file with offsets), not only the hunk.
- `grep` for callers of every changed function, class, config key or message format: changed signatures and changed return values break callers outside the diff.
- Check the tests that cover the code (`glob` for `test_*`), and project rules (CLAUDE.md, CONTRIBUTING, lint config).

## 4. Hunt for these
- **Correctness**: wrong conditions, off-by-one, inverted logic, unhandled `None`/empty/zero, wrong units, mutated shared defaults, wrong variable, copy-paste slips, stale state after an early return.
- **Error paths**: exceptions swallowed or too broad, resources not closed, partial writes, retries without limits, errors reported as success.
- **Concurrency/async**: races on shared state, missing `await`, blocking calls on the event loop, locks held across awaits, cancellation leaving things half-done.
- **Security**: injection (SQL, shell, path traversal, HTML), secrets in code/logs, missing auth/permission checks, unsafe deserialisation, trusting client input.
- **API/contract changes**: callers or stored data that still expect the old shape; migrations; backwards compatibility.
- **Tests**: does a test fail without the fix? Are edge cases and error paths covered? Are assertions meaningful?
Style and naming are not bugs. Mention them only if they will clearly cause one, and at most a few as "nits".

## 5. Verify every finding
For each candidate:
- Re-read the exact lines and trace the values: can the bad input actually reach this code? Is it already handled elsewhere?
- Where it is cheap, prove it: run the existing tests (bash, e.g. `uv run pytest path -q`, `npm test`), or write a tiny reproduction script in the workspace. Never modify the user's files during a review unless asked.
- Drop anything you can't back up. If something is plausible but unproven, keep it only if the impact is high, and label it "unverified".
For big reviews you may hand separate files to `helper_agent` with the diff, the checklist above and "report only verified bugs with file:line"; then verify what they report yourself.

## 6. Report
Order by severity. For each finding:
```
[severity] path/to/file.py:123 — one-line summary
What happens: concrete input/state → wrong result/crash.
Fix: the smallest change that fixes it.
```
Severities: **critical** (data loss, security hole, crash on a common path), **major** (wrong behaviour in a realistic case), **minor** (edge case, misleading error, missing test for risky code), **nit** (optional, few).

End with a 1-3 sentence verdict (ready / ready after fixes / needs rework) and what you did not check (e.g. "didn't run the integration tests").

### PR-style full report (when the user asks for a full PR review)
Add before the findings:
1. **Change description**: what the PR does, behaviour before, behaviour after.
2. **File by file**: for each file, what changed and why it's needed for the PR's goal.
3. **Testing**: tests that exist (✅) and that are missing (❌): happy path, edge cases, errors.

## Posting
Only post comments to GitHub or elsewhere when the user asks, and show them the text first.
