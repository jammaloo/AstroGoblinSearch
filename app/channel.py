"""Discover videos on the channel via yt-dlp (flat playlist, no downloads).

YouTube's Videos tab is ordered newest-first, so playlist position encodes
recency: position 0 is the newest upload. We store that position as
`discovered_order` and the indexer processes the *highest* numbers first — i.e.
the oldest video first, as required.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from . import config


def discover_videos(channel_url: str = config.CHANNEL_URL) -> list[dict[str, Any]]:
    """Return the channel's videos newest-first: [{youtube_id, title, discovered_order}]."""
    proc = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--no-warnings",
            "--dump-single-json",
            channel_url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed to list channel:\n{proc.stderr.strip()}")

    data = json.loads(proc.stdout)
    entries = data.get("entries") or []
    videos: list[dict[str, Any]] = []
    for idx, e in enumerate(entries):
        vid = e.get("id")
        title = e.get("title") or "(untitled)"
        if not vid:
            continue
        videos.append({"youtube_id": vid, "title": title, "discovered_order": idx})
    return videos
