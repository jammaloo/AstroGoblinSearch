"""Whisper transcription. The model is loaded once and reused across videos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import whisper

from . import config

_model: whisper.Whisper | None = None


def get_model() -> whisper.Whisper:
    global _model
    if _model is None:
        print(f"[whisper] loading model '{config.WHISPER_MODEL}' on {config.WHISPER_DEVICE}…")
        _model = whisper.load_model(config.WHISPER_MODEL, device=config.WHISPER_DEVICE)
    return _model


def transcribe(audio_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Transcribe an audio file.

    Returns (clean_text, segments) where clean_text is Whisper's full denoised
    transcript and segments is a list of {seg_idx, start, end, text} — the
    timecoded version used to resolve match timestamps.
    """
    model = get_model()
    result = model.transcribe(
        str(audio_path),
        language=config.WHISPER_LANGUAGE,
        fp16=config.WHISPER_FP16,
        verbose=False,
    )
    clean_text = (result.get("text") or "").strip()
    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            {"seg_idx": i, "start": float(seg["start"]), "end": float(seg["end"]), "text": text}
        )
    return clean_text, segments
