# Voice-to-Text Agent

A small, standalone speech-to-text app: a FastAPI backend plus a Streamlit web UI. Record or upload audio, pick a
provider, model and language, and get the transcript with timestamps. It is independent of the GuessIngs app in this
repository and can be moved to its own repository as-is.

```
 Streamlit UI (frontend/)                  FastAPI backend (app/)
 ┌──────────────────────────┐             ┌────────────────────────────────────────────┐
 │ provider · model · key   │ GET /v1/models  access key check (optional)              │
 │ language · temperature   │────────────►│ validate audio (real format + size)        │
 │ prompt · beam size       │ POST /v1/transcribe                                      │
 │ 🎤 record / 📁 upload     │────────────►│ engine ─ openai ─► OpenAI API              │
 │ transcript · .txt .srt   │◄────────────│        ├ groq ───► Groq API                │
 └──────────────────────────┘             │        └ local ──► faster-whisper (offline)│
                                          └────────────────────────────────────────────┘
```

## Engines and models

| Engine | Models | Needs |
|---|---|---|
| `openai` | `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` | An OpenAI API key (typed in the UI or `OPENAI_API_KEY`) |
| `groq` | `whisper-large-v3-turbo`, `whisper-large-v3` | A Groq API key (typed in the UI or `GROQ_API_KEY`) |
| `local` | `tiny`, `base`, `small`, `medium`, `large-v3` | `pip install -r requirements-local.txt`; the model downloads from Hugging Face on first use |

Only models on this list can be requested (see `app/catalog.py`). `gpt-4o-*-transcribe` models don't return
timestamps, so the subtitle download is disabled for them. All server settings are in [`.env.example`](.env.example).

## Run

```bash
cd voice-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r frontend/requirements.txt   # add requirements-local.txt for offline mode
cp .env.example .env                                            # provider keys are optional here

uvicorn app.main:app --reload                 # backend  → http://localhost:8000/docs
streamlit run frontend/app.py                 # web UI   → http://localhost:8501
```

Or with Docker (both services): `docker compose up --build`, then open http://localhost:8501.
Set `INSTALL_LOCAL=true` in your shell to include the offline engine in the backend image.

## Using the UI

- **Sidebar → Model:** choose the provider and model, and paste your API key. The key is sent with each request only
  and never stored. Leave it empty if the server has its own key.
- **Sidebar → Language:** pick the spoken language or leave *Auto-detect*.
- **Sidebar → Advanced:** *temperature* (0 = most consistent), *beam size* (local engine: higher = more accurate,
  slower) and a *context hint* with names or jargon to spell correctly.
- **Sidebar → Connection:** backend URL and the access key (only if the backend sets `API_KEY`).
- **Main area:** record with the microphone or upload a file, click **Transcribe**, then edit the text and download it
  as `.txt`, `.srt` subtitles or `.json`.

## API

### `GET /v1/models`

The engines and models the client can choose from, with whether the server has its own key for each provider.

### `POST /v1/transcribe`

Multipart form fields (only `file` is required):

| Field | Description |
|---|---|
| `file` | WAV, MP3, M4A, WebM, OGG or FLAC; max `MAX_AUDIO_MB` (default 25) |
| `engine` | `openai`, `groq` or `local` (default: `TRANSCRIBER`) |
| `model` | One of the engine's models (default: the engine's default) |
| `language` | ISO-639-1 code (`en`, `hi`, `de`, …); omit to auto-detect |
| `temperature` | 0–1, default 0 |
| `beam_size` | 1–10, default 5 (local engine only) |
| `prompt` | Context or vocabulary hint, max 500 characters |
| `provider_api_key` | Your own OpenAI/Groq key for this request (overrides the server's key) |

```bash
curl -X POST http://localhost:8000/v1/transcribe \
  -F file=@meeting.m4a -F engine=groq -F model=whisper-large-v3-turbo -F language=en \
  -F provider_api_key=$GROQ_API_KEY
```

```json
{
  "text": "And so my fellow Americans, ask not what your country can do for you...",
  "language": "en",
  "duration_seconds": 11.0,
  "segments": [{ "start": 0.0, "end": 11.0, "text": "And so my fellow Americans, ..." }],
  "engine": "groq",
  "model": "whisper-large-v3-turbo",
  "processing_seconds": 0.8
}
```

Add `-H "Authorization: Bearer $API_KEY"` when the backend sets `API_KEY`.

Errors are returned as `{"error": {"code": "...", "message": "..."}}`:

| Status | Code | When |
|---|---|---|
| 400 | `unsupported_format`, `audio_too_short` | Not an audio file / empty file |
| 400 | `unknown_model`, `api_key_required`, `invalid_api_key` | Model not offered / no provider key / key rejected |
| 401 | `unauthorized` | Access key (`API_KEY`) missing or wrong |
| 413 | `file_too_large` | Over `MAX_AUDIO_MB` |
| 422 | `no_speech`, `transcription_failed` | Silence / audio could not be decoded |
| 429 | `provider_rate_limited` | The provider's rate limit or quota was reached |
| 502 | `transcription_failed` | The provider could not be reached or failed |
| 503 | `engine_not_configured`, `model_unavailable` | Local engine not installed / model could not be loaded |

### `GET /health`

`{"status": "ok", "engine": "openai", "ready": true}` for the default engine.

## Project layout

```
app/                  backend
  main.py             routes, access-key check, error handler
  catalog.py          engines and the models each one offers
  transcribers.py     CloudTranscriber (OpenAI, Groq) and LocalTranscriber behind one interface
  audio.py            upload validation (format detected from file bytes, size limit)
  config.py           settings from environment / .env
  schemas.py          response models
frontend/             Streamlit UI
  app.py              the page: sidebar settings, record/upload, results
  utils.py            timestamp and .srt formatting
tests/                backend tests       frontend/tests/   UI tests (no network or API key needed)
```

Adding another provider means adding it to `catalog.py` and returning an engine from `build_transcriber()`.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```
