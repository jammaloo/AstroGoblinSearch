"""Transcription via faster-whisper (CTranslate2) running Whisper large-v3 in
int8 on the GPU. The model is loaded once and reused across videos.

Exposes the same contract the rest of the app depends on:
    transcribe(path) -> (clean_text, segments)
where segments is the timecoded transcript used to resolve match timestamps.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from . import config

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        device = config.resolve_device()
        print(
            f"[faster-whisper] loading '{config.WHISPER_MODEL}' on {device} "
            f"({config.COMPUTE_TYPE})…"
        )
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=device,
            compute_type=config.COMPUTE_TYPE,
        )
    return _model


def transcribe(audio_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Transcribe an audio file.

    Returns (clean_text, segments) where clean_text is the full transcript and
    segments is a list of {seg_idx, start, end, text} — the timecoded version
    used to resolve match timestamps.
    """
    model = get_model()
    seg_iter, _info = model.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,
        language=config.WHISPER_LANGUAGE,
    )
    # faster-whisper yields segments lazily; materialise them now (the audio
    # file is deleted immediately after this returns).
    segments: list[dict[str, Any]] = []
    parts: list[str] = []
    for i, s in enumerate(seg_iter):
        text = (s.text or "").strip()
        if not text:
            continue
        segments.append(
            {"seg_idx": i, "start": float(s.start), "end": float(s.end), "text": text}
        )
        parts.append(text)
    clean_text = " ".join(parts)
    return clean_text, segments
