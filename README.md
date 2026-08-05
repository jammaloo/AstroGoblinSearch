# AstroGoblinSearch

Search the spoken content of every video on the
[Astrogoblin](https://www.youtube.com/@astrogoblinplays) YouTube channel. Each
match links straight to the moment in the video where the words were said.

A daily job discovers new uploads, downloads their audio, transcribes it with
Whisper large-v3 via faster-whisper (CTranslate2, int8, GPU-accelerated), and
stores the timecoded transcript in SQLite. A single PHP page provides full-text
search over every transcript — no Python runtime needed on the web host.

## How it works

yt-dlp (channel) ──► oldest-first queue ──► download audio ──► faster-whisper ──► SQLite
                                                                          │
        ┌─────────────────────────────────────────────────────────────────┘
        ▼
 PHP UI ── FTS5 search ──► matches with timestamps ──► YouTube links (?t=Ns)
```

- **`videos.clean_text`** — the full clean transcript of a video (the searchable text).
- **`segments`** — the *timecoded* transcript: one row per Whisper segment with its
  `start`/`end` timestamp.
- **`segments_fts`** — an FTS5 index over each segment's text. Searching hits
  segments directly, so every match carries its own timestamp — giving multiple,
  independently timestamped matches inside a single video.

Videos are queued **oldest-first** (YouTube's Videos tab is newest-first; the
indexer reverses that order), so an interrupted backfill always resumes with the
oldest unprocessed video.

## Requirements

**Indexing host (Python, run via cron):**

- Python 3.10+
- An NVIDIA GPU is recommended (Whisper on CPU is ~10× slower) but not required
- `ffmpeg`
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — keep it current (`yt-dlp -U`)
- [`deno`](https://deno.land/) — current yt-dlp needs a JS runtime to extract
  YouTube audio. The indexer auto-discovers deno at `~/.deno/bin`.

**Web host (PHP only):**

- PHP 8.0+ with the `PDO_SQLITE` extension (standard on most hosts)
- Read access to `data/transcripts.db`

## Setup

```bash
pip install -r requirements.txt          # faster-whisper (CTranslate2) — indexer only
yt-dlp -U                                # ensure yt-dlp is up to date
curl -fsSL https://deno.land/install.sh | sh   # installs ~/.deno/bin/deno
```

faster-whisper downloads the model from HuggingFace on first run
(`~/.cache/huggingface`, ~3 GB for `large-v3`).

## Running

### 1. Index videos

```bash
# Transcribe up to AGS_MAX_VIDEOS_PER_RUN (default 10) oldest pending videos:
python3 run_indexer.py

# Unlimited backfill run (process every pending video in one go):
python3 run_indexer.py -n 0

# Exactly 5 videos this run:
python3 run_indexer.py -n 5
```

With 255 videos and `large-v3` int8 on a GTX 1660, expect roughly realtime-to-2×
per video (a 15 min video transcribes in a few minutes). The daily cron keeps a
bounded batch so each run finishes quickly; an unlimited run (`-n 0`) will chew
through the whole backfill if left running.

### 2. Upgrade transcripts to a better model (incremental retranscribe)

Every transcript records the model that produced it (e.g. `whisper.small` or
`faster-whisper.large-v3`) in the `videos.model` column. To re-transcribe only
the videos done with an older model, set the new model and run `--retranscribe`
— it re-does done videos whose stored model differs from the current one,
oldest-first. For example, re-doing the legacy `whisper.small` transcripts with
the current `large-v3`:

```bash
python3 run_indexer.py --retranscribe      # next batch (AGS_MAX_VIDEOS_PER_RUN)
python3 run_indexer.py --retranscribe -n 0 # all of them
```

New/pending videos are untouched — they're handled by the normal run/cron above.

### 3. Serve the search UI (PHP)

Point your web server's document root at `web/` (Apache mod_php or nginx + php-fpm).
`web/index.php` reads `data/transcripts.db` (path overridable via `AGS_DB_PATH`).
The web host needs only PHP with `PDO_SQLITE` and read access to the database —
no Python.

To try it locally without a web server:

```bash
php -S 127.0.0.1:8000 -t web     # open http://127.0.0.1:8000
```

### 4. Daily cron

```bash
crontab -e
```

Add (runs daily at 03:00, logs to `data/cron.log`):

```
0 3 * * * /home/jammaloo/Development/AstroGoblinSearch/cron_index.sh >> /home/jammaloo/Development/AstroGoblinSearch/data/cron.log 2>&1
```

The wrapper sets up `PATH` for cron's minimal environment, then runs the indexer.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `AGS_CHANNEL_URL` | `…/@astrogoblinplays/videos` | Channel to index |
| `AGS_WHISPER_MODEL` | `large-v3` | Whisper model id passed to faster-whisper (e.g. `large-v3`, `medium`) |
| `AGS_COMPUTE_TYPE` | `int8` | CTranslate2 compute type (`int8`, `int8_float16`, `float16`, `float32`) |
| `AGS_DEVICE` | `cuda` if available else `cpu` | CTranslate2 device |
| `AGS_WHISPER_LANGUAGE` | `en` | Pinned language (faster, avoids misdetects) |
| `AGS_MAX_VIDEOS_PER_RUN` | `10` | Per-run cap; `0` in the CLI means unlimited |
| `AGS_DB_PATH` | `./data/transcripts.db` | Path to the DB (PHP UI); override when `web/` is deployed elsewhere |
| `AGS_DATA_DIR` / `AGS_AUDIO_DIR` | `./data`, `./audio` | Storage locations |
| `AGS_COOKIES_FROM_BROWSER` | `firefox` | Browser yt-dlp reads YouTube cookies from for age-restricted videos (`firefox`/`chrome`/`brave`); `""` disables |

## Project layout

```
app/
  config.py      # all settings (env-overridable)
  db.py          # SQLite schema + writes (the indexer's persistence layer)
  channel.py     # discover channel videos (oldest-first queue)
  transcribe.py  # faster-whisper wrapper (model loaded once)
  indexer.py     # download -> transcribe -> store pipeline
web/index.php    # PHP search UI (reads the SQLite DB; no Python needed)
run_indexer.py   # CLI: index videos (cron target via cron_index.sh)
cron_index.sh    # cron-friendly wrapper for run_indexer.py
