"""Indexing pipeline: discover -> download audio -> transcribe -> store.

Processes pending videos oldest-first (highest playlist position first). Each
video is handled in its own transaction so a single failure never aborts the
batch. Downloaded audio is removed after transcription.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from . import config, db, channel, transcribe

YOUTUBE_WATCH = "https://www.youtube.com/watch?v={id}"



def _media_env() -> dict:
    """Environment for yt-dlp subprocesses, ensuring the deno JS runtime
    (required by current yt-dlp for YouTube audio extraction) is discoverable
    at its standard install location even when not on the caller's PATH."""
    env = os.environ.copy()
    deno_bin = str(Path.home() / ".deno" / "bin")
    if Path(deno_bin).is_dir() and deno_bin not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = deno_bin + os.pathsep + env.get("PATH", "")
    return env

def refresh_channel(conn) -> int:
    """Discover current channel videos and record any we have not seen. Returns count."""
    videos = channel.discover_videos()
    for v in videos:
        db.upsert_discovered_video(conn, v["youtube_id"], v["title"], v["discovered_order"])
    return len(videos)


def _fetch_meta(youtube_id: str) -> dict:
    """Fetch upload date / duration / channel without downloading anything."""
    url = YOUTUBE_WATCH.format(id=youtube_id)
    proc = subprocess.run(
        ["yt-dlp", "--no-download", "--no-playlist", "--no-warnings",
         "--print", "%(upload_date)s|%(duration)s|%(channel)s", url],
        capture_output=True, text=True, check=False, env=_media_env(),
    )
    meta = {"upload_date": None, "duration": None, "channel": None}
    for line in proc.stdout.splitlines():
        if "|" in line:
            udate, dur, ch = (line.split("|") + ["", "", ""])[:3]
            meta = {
                "upload_date": _iso_date(udate),
                "duration": int(dur) if dur.isdigit() else None,
                "channel": ch or None,
            }
            break
    return meta


def download_audio(youtube_id: str) -> tuple[Path, dict]:
    """Download best audio as .m4a and return (path, metadata).

    Metadata is fetched in a separate yt-dlp pass: combining a multi-field
    ``--print`` template with audio extraction makes current yt-dlp skip writing
    the output file, so the two steps are kept apart.
    """
    outtmpl = str(config.AUDIO_DIR / "%(id)s.%(ext)s")
    proc = subprocess.run(
        [
            "yt-dlp",
            "-x",
            "--audio-format", "m4a",
            "--audio-quality", "0",
            "--no-playlist",
            "--no-progress",
            "--retries", "5",
            "-o", outtmpl,
            YOUTUBE_WATCH.format(id=youtube_id),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_media_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed:\n{proc.stderr.strip()}")

    meta = _fetch_meta(youtube_id)

    audio_path = config.AUDIO_DIR / f"{youtube_id}.m4a"
    if not audio_path.exists():
        # yt-dlp sometimes keeps the source extension; fall back to a glob.
        candidates = sorted(config.AUDIO_DIR.glob(f"{youtube_id}.*"))
        if not candidates:
            raise RuntimeError(f"audio file not found for {youtube_id}")
        audio_path = candidates[0]
    return audio_path, meta


def _iso_date(yyyymmdd: str) -> str | None:
    try:
        return datetime.strptime(yyyymmdd.strip(), "%Y%m%d").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def index_one(video) -> None:
    vid = video["id"]
    with db.transaction() as conn:
        db.set_video_processing(conn, vid)
    audio_path, meta = download_audio(video["youtube_id"])
    try:
        with db.transaction() as conn:
            db.update_video_meta(conn, vid, **meta)
        clean_text, segments = transcribe.transcribe(audio_path)
        with db.transaction() as conn:
            db.store_transcript(conn, vid, clean_text, segments, model=current_model_label())
        print(f"  done — {len(segments)} segments")
    finally:
        audio_path.unlink(missing_ok=True)


def current_model_label() -> str:
    """The transcriber identity stamped on every transcript this run produces
    (e.g. 'faster-whisper.large-v3'), so older transcriptions can be identified
    and incrementally re-done when a better model is configured."""
    return f"faster-whisper.{config.WHISPER_MODEL}"


def run_retranscribe(limit: int | None = None) -> int:
    """Re-transcribe done videos whose stored model differs from the current one
    (oldest-first), upgrading them to the configured model. New/pending videos
    are left to a normal `run`. Returns count upgraded."""
    db.init_db()
    with db.transaction() as conn:
        recovered = db.recover_stale(conn)
        if recovered:
            print(f"[indexer] recovered {recovered} video(s) left 'processing' by a prior crashed run")
    target = current_model_label()
    if limit is None:
        limit = config.MAX_VIDEOS_PER_RUN

    conn = db.get_conn()
    candidates = [dict(r) for r in db.retranscribe_candidates(conn, target, limit)]
    conn.close()

    n = len(candidates)
    if n == 0:
        print(f"[indexer] nothing to retranscribe — all done videos already at {target}")
        return 0
    print(f"[indexer] retranscribing {n} video(s) to {target} oldest-first")

    ok = 0
    for i, v in enumerate(candidates, 1):
        print(f"[{i}/{n}] {v['title']}  ({v['youtube_id']})  [was {v.get('model') or 'unknown'}]")
        try:
            index_one(v)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED: {e}")
            with db.transaction() as conn:
                db.mark_failed(conn, v["id"], str(e))
    print(f"[indexer] retranscribe finished: {ok}/{n} upgraded")
    db.checkpoint()
    return ok


def run(limit: int | None = None) -> int:
    """Refresh the channel, then transcribe up to `limit` pending videos. Returns count done."""
    db.init_db()
    with db.transaction() as conn:
        recovered = db.recover_stale(conn)
        if recovered:
            print(f"[indexer] recovered {recovered} video(s) left 'processing' by a prior crashed run")
        total = refresh_channel(conn)
    print(f"[indexer] channel has {total} videos")

    if limit is None:
        limit = config.MAX_VIDEOS_PER_RUN

    conn = db.get_conn()
    pending = [dict(r) for r in db.pending_videos(conn, limit)]
    conn.close()
    n = len(pending)

    if n == 0:
        print("[indexer] nothing pending — up to date")
        db.checkpoint()
        return 0
    print(f"[indexer] processing {n} pending video(s) oldest-first")

    ok = 0
    for i, v in enumerate(pending, 1):
        print(f"[{i}/{n}] {v['title']}  ({v['youtube_id']})")
        try:
            index_one(v)
            ok += 1
        except Exception as e:  # noqa: BLE001 - isolate failures per video
            print(f"  FAILED: {e}")
            with db.transaction() as conn:
                db.mark_failed(conn, v["id"], str(e))
    print(f"[indexer] finished: {ok}/{n} succeeded")
    db.checkpoint()
    return ok
