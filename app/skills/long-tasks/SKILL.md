---
name: long-tasks
description: Carry out long or open-ended work spanning many steps or turns - big refactors, data processing, research, long builds or jobs. Keep a progress file, checkpoint often, run slow jobs in the background, delegate side work, and resume cleanly after interruptions.
---
# Long-running tasks

A long task outlives your attention: turns end, the iteration limit hits, the bot restarts, the user comes back tomorrow. Work so that any future you can pick it up from files alone.

## 1. Set up
- Make a project folder: `<workspace>/<project>/`.
- Write `PROGRESS.md` there before doing real work:
```markdown
# <Goal>
Done when: <concrete finish condition>

## Status
<one line: what's happening now>

## Done
- [x] …

## Next
- [ ] …

## Decisions & notes
- <choice made and why; paths, commands, IDs you'll need again>

## Problems
- <what failed and what you tried>
```
- If a plan is needed first, use the planning skill, then copy its steps into "Next".
- Add the current few steps to the todo list (`todo_add`); the todo list is per-session memory, PROGRESS.md is the durable record.

## 2. Work in milestones
- Break the job into pieces you can finish and verify in a handful of tool calls each.
- After each milestone: verify it (tests, counts, spot checks), then update PROGRESS.md (move items to Done, record decisions). Do this before starting the next piece, not at the end.
- Keep outputs on disk (intermediate data, logs, generated files) rather than only in the conversation.
- Prefer idempotent steps (safe to re-run): check whether the output already exists before redoing work.

## 3. Slow commands
bash calls time out (300 s at most). For anything longer, run it in the background and poll:
```bash
cd <workspace>/<project> && nohup <command> > job.log 2>&1 & echo $! > job.pid
```
Then check with short calls: `tail -n 20 job.log`, `kill -0 $(cat job.pid) && echo running || echo finished`. Poll with sensible gaps (e.g. `sleep 30` inside a bash call with a 60 s timeout), not in a tight loop. Record the PID and log path in PROGRESS.md.

## 4. Delegate and schedule
- Side research or independent sub-tasks: `helper_agent` with a self-contained prompt (it has no conversation history), asking for a concise result. Use it to keep this conversation's context small.
- Work that should continue while the user is away, or recur (check a job every hour, re-run a scrape nightly): a scheduled task whose prompt says to read PROGRESS.md first, do the next step, update the file and report.
- Durable facts the user will want next time (e.g. where the project lives): `save_memory`.

## 5. Running out of room
If you are near the iteration limit or the turn has gone on very long: stop at a clean point, update PROGRESS.md, and tell the user what is done, what is next and that sending "continue" resumes it.

## 6. Resuming
When the user says "continue" or comes back to a project: read PROGRESS.md (and `job.log` / `git_status` if relevant) first, check that the "Status" line is still true (did the background job finish?), then carry on from "Next". Don't redo finished work.

## 7. Finishing
Verify the finish condition, mark PROGRESS.md complete, clean up temporary files you created (ask before deleting anything else), and give the user a summary: results, where the outputs are, anything not done and why. Be honest about partial results.
