"""Speech-to-text engines. Each one turns validated audio into a Transcript.

* CloudTranscriber — any OpenAI-compatible /audio/transcriptions API (OpenAI, Groq, self-hosted whisper).
* LocalTranscriber — runs faster-whisper on this machine; no API key, audio never leaves the server.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.audio import Audio, AudioError
from app.catalog import ENGINES
from app.config import get_settings
from app.schemas import Segment, Transcript

log = logging.getLogger(__name__)


@dataclass
class Options:
    """Per-request choices made by the client."""

    model: str
    language: str | None = None
    prompt: str | None = None
    temperature: float = 0.0
    beam_size: int = 5
    api_key: str | None = None  # the client's own provider key; overrides the server's key


class Transcriber(Protocol):
    name: str

    def ready(self, api_key: str | None = None) -> bool: ...

    def transcribe(self, audio: Audio, opts: Options) -> Transcript: ...


class CloudTranscriber:
    def __init__(self, name: str, url: str, server_key: str, timeout: float):
        self.name, self.url, self.server_key, self.timeout = name, url, server_key, timeout

    def ready(self, api_key: str | None = None) -> bool:
        return bool(api_key or self.server_key)

    def transcribe(self, audio: Audio, opts: Options) -> Transcript:
        # Only whisper models return segment timestamps (verbose_json); gpt-4o-*-transcribe return plain json.
        verbose = opts.model.startswith("whisper")
        form = {"model": opts.model, "response_format": "verbose_json" if verbose else "json",
                "temperature": str(opts.temperature)}
        if opts.language:
            form["language"] = opts.language
        if opts.prompt:
            form["prompt"] = opts.prompt
        label = ENGINES[self.name]["label"]
        try:
            res = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {opts.api_key or self.server_key}"},
                data=form,
                files={"file": (f"audio.{audio.extension}", audio.data, audio.media_type)},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            log.warning("%s request failed: %s", label, exc)
            raise AudioError(502, "transcription_failed", f"Could not reach {label}. Please try again.") from exc
        if res.status_code == 401:
            raise AudioError(400, "invalid_api_key", f"{label} rejected the API key. Check it and try again.")
        if res.status_code == 429:
            raise AudioError(429, "provider_rate_limited", f"{label} rate limit or quota reached. Try again later.")
        try:
            res.raise_for_status()
            body = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("%s returned an error: %s", label, exc)
            raise AudioError(502, "transcription_failed", f"{label} could not transcribe this audio.") from exc
        return Transcript(
            text=(body.get("text") or "").strip(),
            language=body.get("language"),
            duration_seconds=body.get("duration"),
            segments=[Segment(start=seg["start"], end=seg["end"], text=seg["text"].strip())
                      for seg in body.get("segments") or []],
            engine=self.name,
            model=opts.model,
        )


@lru_cache(maxsize=2)
def _load_whisper(model: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # optional dependency: requirements-local.txt

    log.info("Loading faster-whisper model %r on %s (first use downloads it)", model, device)
    return WhisperModel(model, device=device, compute_type=compute_type)


class LocalTranscriber:
    name = "local"

    def ready(self, api_key: str | None = None) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, audio: Audio, opts: Options) -> Transcript:
        s = get_settings()
        try:
            model = _load_whisper(opts.model, s.local_device, s.local_compute_type)
        except Exception as exc:  # e.g. model download failed
            log.error("Could not load faster-whisper model %r: %s", opts.model, exc)
            raise AudioError(503, "model_unavailable", f"Speech model '{opts.model}' could not be loaded.") from exc
        try:
            segments, info = model.transcribe(io.BytesIO(audio.data), language=opts.language,
                                              initial_prompt=opts.prompt, temperature=opts.temperature,
                                              beam_size=opts.beam_size, vad_filter=True)
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
            model=opts.model,
        )


def build_transcriber(engine: str) -> Transcriber:
    s = get_settings()
    if engine == "local":
        return LocalTranscriber()
    if engine == "groq":
        return CloudTranscriber("groq", s.groq_api_url, s.groq_api_key, s.cloud_timeout_seconds)
    return CloudTranscriber("openai", s.openai_api_url, s.openai_api_key, s.cloud_timeout_seconds)


def get_engine_factory():
    """FastAPI dependency: returns the function that builds an engine by name (overridden in tests)."""
    return build_transcriber
