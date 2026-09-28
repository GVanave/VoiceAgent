import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from app import transcribers
from app.audio import Audio, AudioError
from app.config import get_settings
from app.main import app
from app.schemas import Segment, Transcript
from app.transcribers import OpenAITranscriber, get_transcriber


def wav_bytes(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


class FakeTranscriber:
    name = "fake"

    def __init__(self, text="Hello world.", ready=True, error=None):
        self.text, self._ready, self.error, self.calls = text, ready, error, []

    def ready(self):
        return self._ready

    def transcribe(self, audio, language=None, prompt=None):
        self.calls.append({"audio": audio, "language": language, "prompt": prompt})
        if self.error:
            raise self.error
        return Transcript(text=self.text, language=language or "en", duration_seconds=0.5,
                          segments=[Segment(start=0, end=0.5, text=self.text)], engine="fake", model="fake-1")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("API_KEY", "OPENAI_API_KEY", "TRANSCRIBER", "MAX_AUDIO_MB"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    app.dependency_overrides.clear()


@pytest.fixture
def fake():
    engine = FakeTranscriber()
    app.dependency_overrides[get_transcriber] = lambda: engine
    return engine


@pytest.fixture
def client():
    return TestClient(app)


def post(client, data, **form):
    return client.post("/v1/transcribe", files={"file": ("clip.wav", data, "audio/wav")}, data=form)


# ---------------------------------------------------------------- API ---
def test_transcribe_returns_text_and_segments(client, fake):
    res = post(client, wav_bytes(), language="de", prompt="names: Ganesh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["text"] == "Hello world." and body["language"] == "de"
    assert body["segments"][0]["end"] == 0.5
    call = fake.calls[0]
    assert call["audio"].media_type == "audio/wav" and call["prompt"] == "names: Ganesh"


def test_health_reports_engine(client, fake):
    assert client.get("/health").json() == {"status": "ok", "engine": "fake", "ready": True}


def test_engine_not_configured(client):
    # Default engine is "openai" and no OPENAI_API_KEY is set.
    assert client.get("/health").json()["ready"] is False
    res = post(client, wav_bytes())
    assert res.status_code == 503 and res.json()["error"]["code"] == "engine_not_configured"


def test_non_audio_is_rejected(client, fake):
    res = post(client, b"<html>" * 500)
    assert res.status_code == 400 and res.json()["error"]["code"] == "unsupported_format"
    assert not fake.calls


def test_too_large_is_rejected(client, fake, monkeypatch):
    monkeypatch.setenv("MAX_AUDIO_MB", "1")
    get_settings.cache_clear()
    res = post(client, wav_bytes(seconds=40))
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"


def test_silence_returns_422(client, fake):
    fake.text = ""
    res = post(client, wav_bytes())
    assert res.status_code == 422 and res.json()["error"]["code"] == "no_speech"


def test_bad_language_code_is_rejected(client, fake):
    assert post(client, wav_bytes(), language="english").status_code == 422


def test_api_key_is_enforced_when_set(client, fake, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    assert post(client, wav_bytes()).status_code == 401
    ok = client.post("/v1/transcribe", files={"file": ("a.wav", wav_bytes(), "audio/wav")},
                     headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_engine_errors_are_returned_cleanly(client, fake):
    fake.error = AudioError(502, "transcription_failed", "The speech-to-text service failed.")
    res = post(client, wav_bytes())
    assert res.status_code == 502 and res.json()["error"]["code"] == "transcription_failed"


# ------------------------------------------------------ OpenAI engine ---
def openai_engine(monkeypatch, handler):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr(transcribers.httpx, "post", handler)
    return OpenAITranscriber(get_settings())


def test_openai_engine_parses_verbose_json(monkeypatch):
    sent = {}

    def handler(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "text": " Hi there. ", "language": "english", "duration": 1.5,
            "segments": [{"start": 0.0, "end": 1.5, "text": " Hi there."}]})

    engine = openai_engine(monkeypatch, handler)
    result = engine.transcribe(Audio(wav_bytes(), "audio/wav", "wav"), language="en")
    assert result.text == "Hi there." and result.segments[0].text == "Hi there." and result.engine == "openai"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["data"] == {"model": "whisper-1", "response_format": "verbose_json", "language": "en"}


def test_openai_engine_failure_becomes_502(monkeypatch):
    def handler(url, **kwargs):
        return httpx.Response(500, request=httpx.Request("POST", url), text="boom")

    engine = openai_engine(monkeypatch, handler)
    with pytest.raises(AudioError) as exc:
        engine.transcribe(Audio(wav_bytes(), "audio/wav", "wav"))
    assert exc.value.status_code == 502
