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

# --- Whisper ----------------------------------------------------------------
# small = ~2 GB VRAM, fast + accurate on a GTX 1660. Bump to "medium" / "large"
# for higher accuracy at the cost of speed and memory.
WHISPER_MODEL = os.environ.get("AGS_WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("AGS_DEVICE", "")  # "" => auto-detect at transcribe time


def resolve_device() -> str:
    """Resolve the torch device. torch is imported lazily so the web app and DB
    layer — which never use torch — are not forced to depend on it."""
    if WHISPER_DEVICE:
        return WHISPER_DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"

WHISPER_LANGUAGE = os.environ.get("AGS_WHISPER_LANGUAGE", "en")
# Run Whisper in float32. fp16 produces NaN logits on some CUDA/torch/GPU combos
# (incl. this GTX 1660 + torch 2.13). Set AGS_FP16=1 only if you've verified it.
WHISPER_FP16 = os.environ.get("AGS_FP16", "0") == "1"

# --- Indexing ---------------------------------------------------------------
# How many pending videos a single indexer run transcribes before stopping.
# Keeps a daily cron bounded; set to 0 (or a huge number) for an unlimited
# backfill run. Oldest pending video is always processed first.
MAX_VIDEOS_PER_RUN = int(os.environ.get("AGS_MAX_VIDEOS_PER_RUN", "10"))

# --- Web --------------------------------------------------------------------
WEB_HOST = os.environ.get("AGS_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("AGS_WEB_PORT", "5000"))
