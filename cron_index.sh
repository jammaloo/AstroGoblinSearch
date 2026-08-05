#!/usr/bin/env bash
# Daily indexer for AstroGoblinSearch — meant to be run by cron.
#
# Discovers any new videos on the channel and transcribes up to
# AGS_MAX_VIDEOS_PER_RUN (default 10) of the oldest not-yet-indexed ones.
# Oldest-first means the backfill completes in chronological order, and the
# daily run only ever has a few brand-new videos to process once caught up.
#
# Crontab (runs every day at 03:00):
#   0 3 * * * /home/jammaloo/Development/AstroGoblinSearch/cron_index.sh >> /home/jammaloo/Development/AstroGoblinSearch/data/cron.log 2>&1
set -euo pipefail

# Resolve this script's directory (symlink-safe) and run from the project root.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do SOURCE="$(readlink "$SOURCE")"; done
cd "$(dirname "$SOURCE")"

# cron runs with a minimal environment — make sure every tool is on PATH:
#   ~/.local/bin  -> yt-dlp, whisper
#   ~/.deno/bin   -> deno (required by current yt-dlp for YouTube extraction)
export PATH="$HOME/.local/bin:$HOME/.deno/bin:$PATH"
# yt-dlp reads YouTube cookies from this browser to fetch age-restricted videos.
# Override by exporting AGS_COOKIES_FROM_BROWSER before invoking this script.
export AGS_COOKIES_FROM_BROWSER="${AGS_COOKIES_FROM_BROWSER:-firefox}"

echo "[$(date -Iseconds)] indexer run start"
exec python3 run_indexer.py "$@"
exec ./pushIt.sh
