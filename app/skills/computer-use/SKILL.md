# Computer Use Skill (desktop in a container)

## Overview
Lets you get things done in a graphical Linux desktop (terminal, Firefox, any GUI app) by handing the task to Claude's
computer toolset (`computer_toolset_20260801`). It follows the
[claude-quickstarts computer-toolset example](https://github.com/anthropics/claude-quickstarts/blob/main/computer-toolset/README.md),
except the VNC desktop runs in a **throwaway Docker container** instead of a local Xvnc.

How it fits together:
1. `desktop.sh start` runs a container with TigerVNC's `Xvnc` (no password), fluxbox, xterm and Firefox ESR, and
   publishes VNC on the host's loopback only (`127.0.0.1:5900`).
2. `run_task.py "<task>"` connects the quickstart's example VNC driver to it and runs a Claude tool-runner loop until
   the task is done. Claude sees screenshots and clicks/types; you get a transcript and a `FINAL ANSWER:` block.
3. `desktop.sh stop` throws the desktop away.

You do not click anything yourself: you describe the task, run the script, and read the answer.

## When to Use This Skill
- The task needs a real GUI: a desktop app, a site that defeats headless browsers, a visual check of how something
  looks, a workflow that is only reachable by clicking.
- For plain web scraping, screenshots of a URL or PDFs, prefer the Puppeteer skill — it is faster and cheaper.

## Prerequisites
- `ANTHROPIC_API_KEY` in the environment (the computer toolset runs on Claude, separately from your own model).
  If it is missing, stop and tell the user it needs to be set.
- Docker on the machine running the bot, **or** the Compose `desktop` sidecar (see "Running in Docker Compose").
- `python3` with `venv`, and network access to GitHub and PyPI for the one-time setup.

All paths below are relative to this skill's directory (`<skills_dir>/computer-use`, from `get_skills_dir`).

## Workflow

### 1. One-time setup
```bash
cd <skills_dir>/computer-use
PY=$(./setup.sh)      # venv + Anthropic SDK + the quickstart's VNC driver; prints the venv python path
```
It installs into `$ANOTHERBOT_HOME/computer-use` (override with `COMPUTER_USE_HOME`) and is safe to re-run.

### 2. Start the desktop
```bash
./desktop.sh start    # builds the image the first time (a few minutes), then "VNC ready at 127.0.0.1:5900"
./desktop.sh status   # check later
```
Options (env vars): `VNC_PORT` (host port, default 5900), `GEOMETRY` (default `1280x800`, max `1920x1200`),
`DESKTOP_NAME` (container name, default `anotherbot-desktop`).

### 3. Run the task
```bash
"$PY" run_task.py "Open Firefox, go to https://example.com and tell me the main heading" --max-steps 40
```
- Write the task as a complete, self-contained instruction, including what to report back.
- Each model message is printed as `[n] ...`, each action as `action: left_click {...}`, then `FINAL ANSWER:`.
- Exit codes: `0` done, `1` setup/API/VNC error (message on stderr), `3` hit `--max-steps`.
- Env: `VNC_HOST` / `VNC_PORT` (default `127.0.0.1:5900`), `MODEL` (default `claude-sonnet-5-5`).
- Runs take a while (each step is a screenshot round trip). Run long tasks in the background and redirect output to
  a log file in the workspace, then read it.
- The desktop keeps its state between runs, so follow-up tasks can build on earlier ones until you stop it.

### 4. Clean up
```bash
./desktop.sh stop     # removes the container and everything in it
```
Stop it when the user's task is finished, unless they asked to keep it around.

## Watching the desktop
The user can watch or take over with any VNC viewer at `127.0.0.1:5900` on the bot's machine (e.g.
`vncviewer 127.0.0.1:5900`, or an SSH tunnel `ssh -L 5900:127.0.0.1:5900 <host>` from elsewhere). Tell them this if
they want to see what is happening.

## Running in Docker Compose
When anotherbot itself runs from `compose.yaml` there is no Docker inside its container. Start the desktop as a sidecar
from the host instead:
```bash
docker compose --profile desktop up -d --build desktop
```
Then, inside the bot, skip `desktop.sh start` and run tasks with `VNC_HOST=desktop`:
```bash
VNC_HOST=desktop ./desktop.sh status
VNC_HOST=desktop "$PY" run_task.py "..."
```
Stopping and resetting the sidecar is done from the host (`docker compose rm -sf desktop`).

## Safety
- What is on the screen steers the model, and a focused terminal runs whatever it types. That is why the desktop is a
  disposable container running as an unprivileged user — never point `run_task.py` at a real desktop (e.g. `x11vnc` on
  the user's `:0`) unless the user explicitly asks for it and understands the risk.
- Never publish the VNC port on anything but `127.0.0.1`: it has no password.
- Don't put secrets (passwords, API keys) into a task or onto the desktop unless the user explicitly asks you to.
- Every action is auto-approved (the bot can't answer the quickstart's y/N prompt); the container is the boundary. If a
  task might touch accounts, money or anything outside the container, confirm with the user first.

## Limitations (from the example driver)
- Implemented actions: `screenshot`, `left_click`, `right_click`, `double_click`, `triple_click`, `mouse_move`,
  `scroll`, `type`, `key`, `wait`. Not available: `zoom`, dragging, `middle_click`, `hold_key`, `cursor_position`.
- `shift+<letter>` types the lowercase letter; screen size can't change while connected; screens above 1920x1200 are
  refused; screenshots use the raw encoding (~4 MB each at 1280x800).
- If the VNC server stops answering, the run fails — check `./desktop.sh status`, restart the desktop, and rerun.

## Troubleshooting
- `error: Docker is not available here.` → use the Compose sidecar, or ask the user to install/start Docker.
- `VNC did not answer` → `./desktop.sh logs`; something else may hold port 5900 (`VNC_PORT=5901 ./desktop.sh start`
  and pass the same `VNC_PORT` to `run_task.py`).
- `Connection refused` from `run_task.py` → the desktop isn't running or `VNC_HOST`/`VNC_PORT` don't match.
- Authentication error → `ANTHROPIC_API_KEY` is missing or wrong.
- Test the desktop without a model or API key: `cd $ANOTHERBOT_HOME/computer-use/driver && "$PY" exercise.py`.
