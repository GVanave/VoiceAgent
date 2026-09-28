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
from app.transcribers import CloudTranscriber, Options, get_engine_factory


def wav_bytes(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


class FakeTranscriber:
    def __init__(self, name, needs_key):
        self.name, self.needs_key = name, needs_key
        self.server_key, self.text, self.error, self.calls = "", "Hello world.", None, []

    def ready(self, api_key=None):
        return bool(api_key or self.server_key) if self.needs_key else True

    def transcribe(self, audio, opts):
        self.calls.append({"audio": audio, "opts": opts})
        if self.error:
            raise self.error
        return Transcript(text=self.text, language=opts.language or "en", duration_seconds=0.5,
                          segments=[Segment(start=0, end=0.5, text=self.text)], engine=self.name, model=opts.model)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "TRANSCRIBER", "MAX_AUDIO_MB"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    app.dependency_overrides.clear()


@pytest.fixture
def engines():
    fakes = {"openai": FakeTranscriber("openai", True), "groq": FakeTranscriber("groq", True),
             "local": FakeTranscriber("local", False)}
    app.dependency_overrides[get_engine_factory] = lambda: fakes.__getitem__
    return fakes


@pytest.fixture
def client():
    return TestClient(app)


def post(client, data=None, **form):
    return client.post("/v1/transcribe", files={"file": ("clip.wav", data or wav_bytes(), "audio/wav")}, data=form)


# ---------------------------------------------------------------- API ---
def test_transcribe_passes_all_options(client, engines):
    res = post(client, engine="groq", model="whisper-large-v3", language="hi", prompt="names: Ganesh",
               temperature="0.4", beam_size="3", provider_api_key=" gsk-user ")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["text"] == "Hello world." and body["engine"] == "groq" and body["model"] == "whisper-large-v3"
    assert body["processing_seconds"] >= 0
    opts = engines["groq"].calls[0]["opts"]
    assert opts == Options(model="whisper-large-v3", language="hi", prompt="names: Ganesh", temperature=0.4,
                           beam_size=3, api_key="gsk-user")


def test_defaults_come_from_server_settings(client, engines):
    engines["openai"].server_key = "sk-server"
    res = post(client)
    assert res.status_code == 200, res.text
    assert res.json()["engine"] == "openai" and res.json()["model"] == "whisper-1"
    assert engines["openai"].calls[0]["opts"].api_key is None  # the server's key is used


def test_cloud_engine_without_any_key(client, engines):
    res = post(client, engine="openai")
    assert res.status_code == 400 and res.json()["error"]["code"] == "api_key_required"


def test_local_engine_needs_no_key(client, engines):
    res = post(client, engine="local", model="small")
    assert res.status_code == 200 and res.json()["model"] == "small"


def test_unknown_model_is_rejected(client, engines):
    res = post(client, engine="local", model="../../etc/passwd")
    assert res.status_code == 400 and res.json()["error"]["code"] == "unknown_model"
    assert post(client, engine="nope").status_code == 422


@pytest.mark.parametrize("field,value", [("temperature", "1.5"), ("beam_size", "0"), ("language", "english")])
def test_out_of_range_options_are_rejected(client, engines, field, value):
    assert post(client, engine="local", **{field: value}).status_code == 422


def test_models_catalog(client, engines):
    engines["groq"].server_key = "gsk-server"
    body = client.get("/v1/models").json()
    assert body["default_engine"] == "openai"
    by_id = {e["id"]: e for e in body["engines"]}
    assert by_id["openai"]["models"][0] == "whisper-1" and by_id["openai"]["server_key_configured"] is False
    assert by_id["groq"]["server_key_configured"] is True
    assert by_id["local"]["needs_api_key"] is False and "large-v3" in by_id["local"]["models"]


def test_health_reports_engine(client, engines):
    assert client.get("/health").json() == {"status": "ok", "engine": "openai", "ready": False}


def test_non_audio_is_rejected(client, engines):
    res = post(client, b"<html>" * 500, engine="local")
    assert res.status_code == 400 and res.json()["error"]["code"] == "unsupported_format"
    assert not engines["local"].calls


def test_too_large_is_rejected(client, engines, monkeypatch):
    monkeypatch.setenv("MAX_AUDIO_MB", "1")
    get_settings.cache_clear()
    res = post(client, wav_bytes(seconds=40), engine="local")
    assert res.status_code == 413 and res.json()["error"]["code"] == "file_too_large"


def test_silence_returns_422(client, engines):
    engines["local"].text = ""
    res = post(client, engine="local")
    assert res.status_code == 422 and res.json()["error"]["code"] == "no_speech"


def test_access_key_is_enforced_when_set(client, engines, monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    assert post(client, engine="local").status_code == 401
    assert client.get("/v1/models").status_code == 401
    ok = client.post("/v1/transcribe", files={"file": ("a.wav", wav_bytes(), "audio/wav")}, data={"engine": "local"},
                     headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_engine_errors_are_returned_cleanly(client, engines):
    engines["local"].error = AudioError(503, "model_unavailable", "Speech model could not be loaded.")
    res = post(client, engine="local")
    assert res.status_code == 503 and res.json()["error"]["code"] == "model_unavailable"


# ------------------------------------------------------- cloud engine ---
def cloud(monkeypatch, handler, server_key="sk-server"):
    monkeypatch.setattr(transcribers.httpx, "post", handler)
    return CloudTranscriber("openai", "https://api.example/v1/audio/transcriptions", server_key, 5)


def audio():
    return Audio(wav_bytes(), "audio/wav", "wav")


def test_cloud_engine_parses_verbose_json(monkeypatch):
    sent = {}

    def handler(url, **kwargs):
        sent.update(url=url, **kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "text": " Hi there. ", "language": "english", "duration": 1.5,
            "segments": [{"start": 0.0, "end": 1.5, "text": " Hi there."}]})

    result = cloud(monkeypatch, handler).transcribe(audio(), Options(model="whisper-1", language="en", temperature=0.2))
    assert result.text == "Hi there." and result.segments[0].text == "Hi there." and result.model == "whisper-1"
    assert sent["headers"]["Authorization"] == "Bearer sk-server"
    assert sent["data"] == {"model": "whisper-1", "response_format": "verbose_json", "temperature": "0.2",
                            "language": "en"}


def test_cloud_engine_user_key_and_plain_json_for_gpt4o(monkeypatch):
    sent = {}

    def handler(url, **kwargs):
        sent.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"text": "Hi"})

    result = cloud(monkeypatch, handler).transcribe(audio(), Options(model="gpt-4o-transcribe", api_key="sk-user"))
    assert result.text == "Hi" and result.segments == []
    assert sent["headers"]["Authorization"] == "Bearer sk-user" and sent["data"]["response_format"] == "json"


@pytest.mark.parametrize("status,code", [(401, "invalid_api_key"), (429, "provider_rate_limited"),
                                         (500, "transcription_failed")])
def test_cloud_engine_errors(monkeypatch, status, code):
    def handler(url, **kwargs):
        return httpx.Response(status, request=httpx.Request("POST", url), text="error")

    with pytest.raises(AudioError) as exc:
        cloud(monkeypatch, handler).transcribe(audio(), Options(model="whisper-1"))
    assert exc.value.code == code
