#!/bin/bash
# Publish the transcripts database: commit, push, and redeploy — but only when
# something actually changed. The indexer avoids writing to the DB on no-op runs
# (see db.upsert_discovered_video); this guard is the backstop so a run with
# nothing new never produces an empty commit, push, and server redeploy.
set -euo pipefail

git add .

if git diff --cached --quiet; then
    echo "[push] nothing to publish — skipping commit/push/deploy"
    exit 0
fi

git commit -m "New Transcriptions"
git push
ssh search.astrogoblin.jammaloo.com 'cd /var/www/jammaloo/subdomains/search.astrogoblin.jammaloo.com/private && ./update.sh'
