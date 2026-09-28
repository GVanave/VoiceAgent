"""Voice-to-text agent: upload an audio file, get back the transcript.

Run:  uvicorn app.main:app --reload     Docs: http://localhost:8000/docs
"""

import logging
import secrets

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.audio import AudioError, validate_audio
from app.config import get_settings
from app.schemas import Health, Transcript
from app.transcribers import Transcriber, get_transcriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Voice-to-Text Agent", version="1.0.0")


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
        raise AudioError(401, "unauthorized", "Missing or invalid API key.")


@app.get("/health", response_model=Health)
def health(transcriber: Transcriber = Depends(get_transcriber)):
    return Health(status="ok", engine=transcriber.name, ready=transcriber.ready())


@app.post("/v1/transcribe", response_model=Transcript, dependencies=[Depends(require_api_key)])
async def transcribe(
    file: UploadFile = File(..., description="Audio file: WAV, MP3, M4A, WebM, OGG or FLAC"),
    language: str | None = Form(default=None, pattern="^[a-z]{2}$", description="ISO-639-1 code, e.g. 'en'. "
                                "Omit to auto-detect."),
    prompt: str | None = Form(default=None, max_length=500, description="Optional vocabulary/context hint"),
    transcriber: Transcriber = Depends(get_transcriber),
):
    settings = get_settings()
    if not transcriber.ready():
        raise AudioError(503, "engine_not_configured",
                         f"The '{transcriber.name}' engine is not configured. See .env.example.")
    data = await file.read(settings.max_audio_mb * 1024 * 1024 + 1)  # never read more than the limit
    audio = validate_audio(data, settings.max_audio_mb)
    # Transcription is blocking (HTTP call or CPU work); run it off the event loop.
    result = await run_in_threadpool(transcriber.transcribe, audio, language, prompt)
    if not result.text:
        raise AudioError(422, "no_speech", "No speech was detected in the audio.")
    return result
