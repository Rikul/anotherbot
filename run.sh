#!/bin/sh

set -e # Exit early if any commands fail

# Copied from .codecrafters/run.sh

SCRIPT_DIR="$(dirname "$0")"
PYTHONSAFEPATH=1 PYTHONPATH="$SCRIPT_DIR" exec uv run \
--project "$SCRIPT_DIR" \
-m app.main \
"$@"

