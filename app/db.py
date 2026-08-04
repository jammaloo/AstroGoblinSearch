"""SQLite persistence + FTS5 full-text search over transcript segments.

Design (per requirements):
  * `videos.clean_text`  — the full clean transcript for a video (display + archive).
  * `segments`           — the *timecoded* transcript: one row per Whisper segment
                           with its start/end timestamp and the segment text.
  * `segments_fts`       — FTS5 index over each segment's normalized text. Searching
                           hits the timecoded segments directly, so every match carries
                           its own timestamp — giving multiple, independently timestamped
                           matches within a single video.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id      TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    channel         TEXT,
    upload_date     TEXT,          -- ISO YYYY-MM-DD
    duration        INTEGER,        -- seconds
    discovered_order INTEGER NOT NULL DEFAULT 0,  -- playlist position, 0 = newest
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|done|failed
    clean_text      TEXT,          -- full clean transcript
    model           TEXT,          -- transcriber used, e.g. "whisper.small"
    indexed_at      TEXT,          -- ISO timestamp when fully indexed
    error           TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    seg_idx     INTEGER NOT NULL,
    start       REAL NOT NULL,     -- seconds
    end         REAL NOT NULL,
    text        TEXT NOT NULL,     -- raw segment text
    clean_text  TEXT NOT NULL      -- normalized text (what FTS indexes)
);
CREATE INDEX IF NOT EXISTS ix_segments_video ON segments(video_id);

-- Full-text index over segment clean text. Contentful table: rowid == segments.id.
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    clean_text,
    tokenize = "porter unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction() -> sqlite3.Connection:
    """Context manager yielding a connection that commits/rollbacks as a block."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    with transaction() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def checkpoint() -> None:
    """Fold the WAL back into the main database file. Called at the end of an
    indexer run so a read-only web reader (e.g. the PHP UI, possibly running as
    a different user without WAL file access) sees the latest transcripts."""
    with get_conn() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before a feature shipped."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    if "model" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN model TEXT")
        conn.executescript(SCHEMA)


# --- normalisation ----------------------------------------------------------
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace -> clean searchable text."""
    if not text:
        return ""
    text = _NONWORD.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


# --- writes -----------------------------------------------------------------
def upsert_discovered_video(
    conn: sqlite3.Connection,
    youtube_id: str,
    title: str,
    discovered_order: int,
) -> None:
    """Record a video discovered on the channel if we have not seen it before."""
    conn.execute(
        """INSERT INTO videos (youtube_id, title, discovered_order)
           VALUES (?, ?, ?)
           ON CONFLICT(youtube_id) DO UPDATE SET
             title = excluded.title,
             discovered_order = excluded.discovered_order
           WHERE videos.status = 'pending'""",
        (youtube_id, title, discovered_order),
    )


def set_video_processing(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute("UPDATE videos SET status = 'processing' WHERE id = ?", (video_id,))


def mark_failed(conn: sqlite3.Connection, video_id: int, error: str) -> None:
    conn.execute(
        "UPDATE videos SET status = 'failed', error = ? WHERE id = ?",
        (error[:500], video_id),
    )


def recover_stale(conn: sqlite3.Connection) -> int:
    """Reset videos left 'processing' by a crashed/interrupted run back to
    'pending' so they're retried. Returns the count recovered."""
    cur = conn.execute(
        "UPDATE videos SET status = 'pending' WHERE status = 'processing'"
    )
    return cur.rowcount


def store_transcript(
    conn: sqlite3.Connection,
    video_id: int,
    clean_text: str,
    segments: Iterable[dict[str, Any]],
    model: str | None = None,
) -> None:
    """Persist the full clean transcript and every timecoded segment (+ FTS rows)."""
    # Replace any prior transcript for this video (idempotent re-index).
    old_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM segments WHERE video_id = ?", (video_id,))]
    if old_ids:
        placeholders = ",".join("?" * len(old_ids))
        conn.execute(f"DELETE FROM segments WHERE id IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM segments_fts WHERE rowid IN ({placeholders})", old_ids)

    conn.execute("UPDATE videos SET clean_text = ? WHERE id = ?", (clean_text, video_id))

    rows = []
    fts_rows = []
    for seg in segments:
        seg_text = seg["text"].strip()
        clean = normalize(seg_text)
        if not clean:
            continue
        cur = conn.execute(
            """INSERT INTO segments (video_id, seg_idx, start, end, text, clean_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (video_id, seg["seg_idx"], seg["start"], seg["end"], seg_text, clean),
        )
        rows.append(cur.lastrowid)
        fts_rows.append((cur.lastrowid, clean))
    conn.executemany("INSERT INTO segments_fts (rowid, clean_text) VALUES (?, ?)", fts_rows)

    conn.execute(
        """UPDATE videos
           SET status = 'done', indexed_at = datetime('now'), error = NULL, model = ?
           WHERE id = ?""",
        (model, video_id),
    )


def update_video_meta(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    upload_date: str | None,
    duration: int | None,
    channel: str | None,
) -> None:
    conn.execute(
        """UPDATE videos SET upload_date = ?, duration = ?, channel = ?
           WHERE id = ?""",
        (upload_date, duration, channel, video_id),
    )


# --- reads ------------------------------------------------------------------
def pending_videos(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Videos to (re)process, oldest-first (highest playlist position = oldest).
    Includes 'failed' so transient errors (e.g. YouTube 403 throttling) are
    retried on the next run instead of being permanently skipped."""
    sql = (
        "SELECT * FROM videos WHERE status IN ('pending', 'failed') "
        "ORDER BY discovered_order DESC, id ASC"
    )
    params: tuple[Any, ...] = ()
    if limit and limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def retranscribe_candidates(conn: sqlite3.Connection, current_model: str, limit: int) -> list[sqlite3.Row]:
    """Done videos not already transcribed with `current_model` (NULL counts as
    stale), oldest-first — the set an incremental upgrade run would redo."""
    sql = (
        "SELECT * FROM videos WHERE status = 'done' "
        "AND (model IS NULL OR model != ?) "
        "ORDER BY discovered_order DESC, id ASC"
    )
    params: tuple[Any, ...] = (current_model,)
    if limit and limit > 0:
        sql += " LIMIT ?"
        params = (current_model, limit)
    return conn.execute(sql, params).fetchall()

def get_video(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()


def get_video_by_youtube_id(conn: sqlite3.Connection, youtube_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM videos WHERE youtube_id = ?", (youtube_id,)).fetchone()
