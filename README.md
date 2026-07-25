# AstroGoblinSearch

Search the spoken content of every video on the
[Astrogoblin](https://www.youtube.com/@astrogoblinplays) YouTube channel. Each
match links straight to the moment in the video where the words were said.

A daily job discovers new uploads, downloads their audio, transcribes it with
OpenAI's Whisper (GPU-accelerated), and stores the timecoded transcript in
SQLite. A small Flask app provides full-text search over every transcript.

## How it works

```
yt-dlp (channel) ──► oldest-first queue ──► download audio ──► Whisper ──► SQLite
                                                                          │
        ┌─────────────────────────────────────────────────────────────────┘
        ▼
 Flask UI ── FTS5 search ──► matches with timestamps ──► YouTube links (?t=Ns)
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

- Python 3.10+
- An NVIDIA GPU is recommended (Whisper on CPU is ~10× slower) but not required
- `ffmpeg`
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — keep it current (`yt-dlp -U`)
- [`deno`](https://deno.land/) — current yt-dlp needs a JS runtime to extract
  YouTube audio. The indexer auto-discovers deno at `~/.deno/bin`.

## Setup

```bash
pip install -r requirements.txt          # openai-whisper, torch, flask
yt-dlp -U                                # ensure yt-dlp is up to date
curl -fsSL https://deno.land/install.sh | sh   # installs ~/.deno/bin/deno
```

Whisper downloads its model on first run (`~/.cache/whisper`, ~461 MB for `small`).

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

With 255 videos and the `small` model on a GTX 1660, expect roughly 15–60 s per
video. The daily cron keeps a bounded batch so each run finishes quickly; an
unlimited run (`-n 0`) will chew through the whole backfill if left running.

### 2. Upgrade transcripts to a better model (incremental retranscribe)

Every transcript records the model that produced it (e.g. `whisper.small`) in the
`videos.model` column. To re-transcribe only the videos done with an older model,
set the new model and run `--retranscribe` — it re-does done videos whose stored
model differs from the current one, oldest-first:

```bash
AGS_WHISPER_MODEL=medium python3 run_indexer.py --retranscribe      # next batch
AGS_WHISPER_MODEL=medium python3 run_indexer.py --retranscribe -n 0 # all of them
```

New/pending videos are untouched — they're handled by the normal run/cron above.

### 3. Run the search UI

```bash
python3 run_server.py
# open http://127.0.0.1:5000
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
| `AGS_WHISPER_MODEL` | `small` | Whisper model (`tiny`/`base`/`small`/`medium`/`large`) |
| `AGS_DEVICE` | `cuda` if available else `cpu` | Torch device |
| `AGS_FP16` | `0` | fp16 inference. Off by default — it yields NaN logits on some CUDA/GPU combos. Turn on only if verified. |
| `AGS_WHISPER_LANGUAGE` | `en` | Pinned language (faster, avoids misdetects) |
| `AGS_MAX_VIDEOS_PER_RUN` | `10` | Per-run cap; `0` in the CLI means unlimited |
| `AGS_WEB_PORT` | `5000` | Web server port |
| `AGS_DATA_DIR` / `AGS_AUDIO_DIR` | `./data`, `./audio` | Storage locations |

## Project layout

```
app/
  config.py      # all settings (env-overridable)
  db.py          # SQLite schema + FTS5 search
  channel.py     # discover channel videos (oldest-first queue)
  transcribe.py  # Whisper wrapper (model loaded once)
  indexer.py     # download -> transcribe -> store pipeline
  web.py         # Flask search app
templates/index.html
run_indexer.py   # CLI: index videos (cron target via cron_index.sh)
run_server.py    # CLI: run the web UI
cron_index.sh    # cron-friendly wrapper for run_indexer.py
```
