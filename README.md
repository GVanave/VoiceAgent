# Voice-to-Text Agent

A small, standalone FastAPI service: send it an audio file, get back the transcript with timestamps.
It is independent of the GuessIngs app in this repository and can be moved to its own repository as-is.

```
client ──POST /v1/transcribe (audio)──►  FastAPI
                                          ├─ API key check (optional)
                                          ├─ validate audio (real format + size)
                                          ├─ engine: "openai" ──► OpenAI / Groq / self-hosted whisper API
                                          │      or  "local"  ──► faster-whisper on this machine (offline)
                                          └─◄ { text, language, duration, segments[] }
```

## Engines

| `TRANSCRIBER` | What it uses | Needs |
|---|---|---|
| `openai` (default) | Any OpenAI-compatible `/audio/transcriptions` API: OpenAI `whisper-1`, Groq `whisper-large-v3`, a self-hosted whisper server | `OPENAI_API_KEY` (+ `OPENAI_API_URL`, `OPENAI_MODEL` for non-OpenAI providers) |
| `local` | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on this machine; audio never leaves the server, no per-minute cost | `pip install -r requirements-local.txt`; the model (`LOCAL_MODEL`, default `base`) is downloaded from Hugging Face on first use |

All settings are in [`.env.example`](.env.example).

## Run

```bash
cd voice-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # or requirements-local.txt for the offline engine
cp .env.example .env                       # set OPENAI_API_KEY, or TRANSCRIBER=local
uvicorn app.main:app --reload              # API docs: http://localhost:8000/docs
```

Docker:

```bash
docker build -t voice-agent .                                  # API engine only (small image)
docker build -t voice-agent --build-arg INSTALL_LOCAL=true .   # include faster-whisper
docker run -p 8000:8000 --env-file .env voice-agent
```

## API

### `POST /v1/transcribe`

Multipart form fields:

| Field | Required | Description |
|---|---|---|
| `file` | yes | WAV, MP3, M4A, WebM, OGG or FLAC; max `MAX_AUDIO_MB` (default 25) |
| `language` | no | ISO-639-1 code (`en`, `de`, `hi`, …). Omit to auto-detect |
| `prompt` | no | Context or vocabulary hint (names, jargon) to improve accuracy |

```bash
curl -X POST http://localhost:8000/v1/transcribe \
  -H "Authorization: Bearer $API_KEY" \
  -F file=@meeting.m4a -F language=en
```

```json
{
  "text": "And so my fellow Americans, ask not what your country can do for you...",
  "language": "en",
  "duration_seconds": 11.0,
  "segments": [{ "start": 0.0, "end": 11.0, "text": "And so my fellow Americans, ..." }],
  "engine": "local",
  "model": "base"
}
```

The `Authorization` header is only required when `API_KEY` is set.

Errors are returned as `{"error": {"code": "...", "message": "..."}}`:

| Status | Code | When |
|---|---|---|
| 400 | `unsupported_format`, `audio_too_short` | Not an audio file / empty file |
| 401 | `unauthorized` | `API_KEY` set and header missing or wrong |
| 413 | `file_too_large` | Over `MAX_AUDIO_MB` |
| 422 | `no_speech`, `transcription_failed` | Silence / audio could not be decoded |
| 502 | `transcription_failed` | The external speech-to-text API failed |
| 503 | `engine_not_configured`, `model_unavailable` | Missing API key or faster-whisper / model could not be loaded |

### `GET /health`

`{"status": "ok", "engine": "openai", "ready": true}` — `ready` is false when the engine is not configured.

## Project layout

```
app/
  main.py          routes, API-key check, error handler
  config.py        settings from environment / .env
  audio.py         upload validation (format detected from file bytes, size limit)
  transcribers.py  OpenAITranscriber and LocalTranscriber behind one interface
  schemas.py       response models
tests/test_api.py  API and engine tests (no network or API key needed)
```

Adding another engine (e.g. Deepgram, Azure) means writing one class with `name`, `ready()` and `transcribe()`
and returning it from `get_transcriber()`.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```
