"""Voice-to-text agent: upload an audio file, get back the transcript.

Run:  uvicorn app.main:app --reload     Docs: http://localhost:8000/docs
"""

import logging
import secrets
import time
from collections.abc import Callable
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.audio import AudioError, validate_audio
from app.catalog import ENGINES, default_model, models_for
from app.config import get_settings
from app.schemas import Catalog, EngineInfo, Health, Transcript
from app.transcribers import Options, Transcriber, get_engine_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Voice-to-Text Agent", version="1.1.0")

EngineFactory = Callable[[str], Transcriber]


@app.exception_handler(AudioError)
async def audio_error_handler(_: Request, exc: AudioError):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    """If API_KEY is set, clients must send `Authorization: Bearer <API_KEY>`."""
    expected = get_settings().api_key
    if not expected:
        return
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token.encode(), expected.encode()):
        raise AudioError(401, "unauthorized", "Missing or invalid access key.")


@app.get("/health", response_model=Health)
def health(factory: EngineFactory = Depends(get_engine_factory)):
    engine = get_settings().transcriber
    return Health(status="ok", engine=engine, ready=factory(engine).ready())


@app.get("/v1/models", response_model=Catalog, dependencies=[Depends(require_api_key)])
def list_models(factory: EngineFactory = Depends(get_engine_factory)):
    """Engines and models the client can choose from."""
    s = get_settings()
    engines = []
    for engine_id, info in ENGINES.items():
        transcriber = factory(engine_id)
        engines.append(EngineInfo(
            id=engine_id, label=info["label"],
            models=models_for(engine_id, s), default_model=default_model(engine_id, s),
            needs_api_key=info["needs_api_key"],
            server_key_configured=info["needs_api_key"] and transcriber.ready(),
            # Cloud engines are always usable: the client can bring its own key.
            ready=transcriber.ready() if not info["needs_api_key"] else True,
        ))
    return Catalog(default_engine=s.transcriber, engines=engines)


@app.post("/v1/transcribe", response_model=Transcript, dependencies=[Depends(require_api_key)])
async def transcribe(
    file: UploadFile = File(..., description="Audio file: WAV, MP3, M4A, WebM, OGG or FLAC"),
    engine: Literal["openai", "groq", "local"] | None = Form(default=None, description="Defaults to TRANSCRIBER"),
    model: str | None = Form(default=None, description="One of the engine's models (see /v1/models)"),
    language: str | None = Form(default=None, pattern="^[a-z]{2}$", description="ISO-639-1 code, e.g. 'en'. "
                                "Omit to auto-detect."),
    prompt: str | None = Form(default=None, max_length=500, description="Optional vocabulary/context hint"),
    temperature: float = Form(default=0.0, ge=0.0, le=1.0, description="0 = most deterministic"),
    beam_size: int = Form(default=5, ge=1, le=10, description="Local engine only: higher = slower, more accurate"),
    provider_api_key: str | None = Form(default=None, max_length=300,
                                        description="Your own OpenAI/Groq key. Used for this request only."),
    factory: EngineFactory = Depends(get_engine_factory),
):
    settings = get_settings()
    engine = engine or settings.transcriber
    model = model or default_model(engine, settings)
    if model not in models_for(engine, settings):
        raise AudioError(400, "unknown_model", f"Model '{model}' is not available for the {engine} engine.")

    transcriber = factory(engine)
    provider_api_key = (provider_api_key or "").strip() or None
    if not transcriber.ready(provider_api_key):
        label = ENGINES[engine]["label"]
        if ENGINES[engine]["needs_api_key"]:
            raise AudioError(400, "api_key_required", f"Enter your {label} API key to use this engine.")
        raise AudioError(503, "engine_not_configured", "The local engine is not installed on this server.")

    data = await file.read(settings.max_audio_mb * 1024 * 1024 + 1)  # never read more than the limit
    audio = validate_audio(data, settings.max_audio_mb)
    opts = Options(model=model, language=language, prompt=prompt or None, temperature=temperature,
                   beam_size=beam_size, api_key=provider_api_key)
    started = time.perf_counter()
    # Transcription is blocking (HTTP call or CPU work); run it off the event loop.
    result = await run_in_threadpool(transcriber.transcribe, audio, opts)
    if not result.text:
        raise AudioError(422, "no_speech", "No speech was detected in the audio.")
    result.processing_seconds = round(time.perf_counter() - started, 2)
    return result
