"""Central configuration. Every knob is overridable via environment variables."""
from __future__ import annotations

import os
from pathlib import Path

# --- Paths ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AGS_DATA_DIR", BASE_DIR / "data"))
AUDIO_DIR = Path(os.environ.get("AGS_AUDIO_DIR", BASE_DIR / "audio"))
DB_PATH = DATA_DIR / "transcripts.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# --- Channel ----------------------------------------------------------------
CHANNEL_URL = os.environ.get(
    "AGS_CHANNEL_URL", "https://www.youtube.com/@astrogoblinplays/videos"
)
CHANNEL_NAME = os.environ.get("AGS_CHANNEL_NAME", "Astrogoblin")

# --- Transcription (faster-whisper / CTranslate2) ---------------------------
# Whisper model id passed to faster-whisper. large-v3 = best accuracy.
WHISPER_MODEL = os.environ.get("AGS_WHISPER_MODEL", "large-v3")
# CTranslate2 compute type. int8 = quantized weights, fast + low VRAM on GPU.
# Alternatives: int8_float16 (int8 weights, fp16 compute), float16, float32.
COMPUTE_TYPE = os.environ.get("AGS_COMPUTE_TYPE", "int8")
WHISPER_DEVICE = os.environ.get("AGS_DEVICE", "")  # "" => auto-detect at transcribe time
WHISPER_LANGUAGE = os.environ.get("AGS_WHISPER_LANGUAGE", "en")


def resolve_device() -> str:
    """Resolve the CTranslate2 device ('cuda'/'cpu'). ctranslate2 is imported
    lazily so the web app and DB layer — which never transcribe — are not forced
    to depend on it."""
    if WHISPER_DEVICE:
        return WHISPER_DEVICE
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"

# --- Indexing ---------------------------------------------------------------
# How many pending videos a single indexer run transcribes before stopping.
# Keeps a daily cron bounded; set to 0 (or a huge number) for an unlimited
# backfill run. Oldest pending video is always processed first.
MAX_VIDEOS_PER_RUN = int(os.environ.get("AGS_MAX_VIDEOS_PER_RUN", "10"))

# Browser whose cookies yt-dlp reads to authenticate age-restricted downloads
# (e.g. "firefox", "chrome", "brave"). The browser must be logged into YouTube.
# Defaults to "firefox"; set AGS_COOKIES_FROM_BROWSER="" to disable, or to
# another browser if that's where you're signed in.
COOKIES_FROM_BROWSER = os.environ.get("AGS_COOKIES_FROM_BROWSER", "firefox")

# --- Web --------------------------------------------------------------------
WEB_HOST = os.environ.get("AGS_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("AGS_WEB_PORT", "5000"))
