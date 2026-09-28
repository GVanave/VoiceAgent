"""Speech-to-text engines. Each one turns validated audio into a Transcript.

* OpenAITranscriber — calls any OpenAI-compatible /audio/transcriptions API (OpenAI, Groq, self-hosted whisper).
* LocalTranscriber  — runs faster-whisper on this machine; no API key, audio never leaves the server.
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from typing import Protocol

import httpx

from app.audio import Audio, AudioError
from app.config import Settings, get_settings
from app.schemas import Segment, Transcript

log = logging.getLogger(__name__)


class Transcriber(Protocol):
    name: str

    def ready(self) -> bool: ...

    def transcribe(self, audio: Audio, language: str | None = None, prompt: str | None = None) -> Transcript: ...


class OpenAITranscriber:
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings

    def ready(self) -> bool:
        return bool(self.settings.openai_api_key)

    def transcribe(self, audio: Audio, language: str | None = None, prompt: str | None = None) -> Transcript:
        s = self.settings
        form = {"model": s.openai_model, "response_format": "verbose_json"}
        if language:
            form["language"] = language
        if prompt:
            form["prompt"] = prompt
        try:
            res = httpx.post(
                s.openai_api_url,
                headers={"Authorization": f"Bearer {s.openai_api_key}"},
                data=form,
                files={"file": (f"audio.{audio.extension}", audio.data, audio.media_type)},
                timeout=s.openai_timeout_seconds,
            )
            res.raise_for_status()
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Transcription API request failed: %s", exc)
            raise AudioError(502, "transcription_failed", "The speech-to-text service failed.") from exc
        return Transcript(
            text=(body.get("text") or "").strip(),
            language=body.get("language"),
            duration_seconds=body.get("duration"),
            segments=[Segment(start=seg["start"], end=seg["end"], text=seg["text"].strip())
                      for seg in body.get("segments") or []],
            engine=self.name,
            model=s.openai_model,
        )


@lru_cache(maxsize=1)
def _load_whisper(model: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # optional dependency: requirements-local.txt

    log.info("Loading faster-whisper model %r on %s (first use downloads it)", model, device)
    return WhisperModel(model, device=device, compute_type=compute_type)


class LocalTranscriber:
    name = "local"

    def __init__(self, settings: Settings):
        self.settings = settings

    def ready(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, audio: Audio, language: str | None = None, prompt: str | None = None) -> Transcript:
        s = self.settings
        try:
            model = _load_whisper(s.local_model, s.local_device, s.local_compute_type)
        except Exception as exc:  # e.g. model download failed
            log.error("Could not load faster-whisper model %r: %s", s.local_model, exc)
            raise AudioError(503, "model_unavailable", f"Speech model '{s.local_model}' could not be loaded.") from exc
        try:
            segments, info = model.transcribe(io.BytesIO(audio.data), language=language, initial_prompt=prompt,
                                              vad_filter=True)
            segments = [Segment(start=round(seg.start, 2), end=round(seg.end, 2), text=seg.text.strip())
                        for seg in segments]
        except Exception as exc:  # corrupt or undecodable audio
            log.warning("Local transcription failed: %s", exc)
            raise AudioError(422, "transcription_failed", "Could not transcribe this audio file.") from exc
        return Transcript(
            text=" ".join(seg.text for seg in segments).strip(),
            language=info.language,
            duration_seconds=round(info.duration, 2),
            segments=segments,
            engine=self.name,
            model=s.local_model,
        )


def get_transcriber() -> Transcriber:
    settings = get_settings()
    if settings.transcriber == "local":
        return LocalTranscriber(settings)
    return OpenAITranscriber(settings)
