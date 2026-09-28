"""Smoke tests for the Streamlit UI, with the backend replaced by canned responses."""

import sys
from pathlib import Path

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parents[1]))
from utils import clock, to_srt  # noqa: E402

APP = str(Path(__file__).parents[1] / "app.py")
CATALOG = {"default_engine": "openai", "engines": [
    {"id": "openai", "label": "OpenAI", "models": ["whisper-1", "gpt-4o-transcribe"], "default_model": "whisper-1",
     "needs_api_key": True, "server_key_configured": False, "ready": True},
    {"id": "local", "label": "Local (offline)", "models": ["tiny", "base"], "default_model": "base",
     "needs_api_key": False, "server_key_configured": False, "ready": True},
]}


@pytest.fixture(autouse=True)
def _fresh_cache():
    st.cache_data.clear()


@pytest.fixture
def backend(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        return httpx.Response(200, json=CATALOG, request=httpx.Request("GET", url))

    def fake_post(url, **kwargs):
        calls.append(kwargs["data"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "text": "Hello world", "language": "english", "duration_seconds": 2.0, "processing_seconds": 0.4,
            "segments": [{"start": 0.0, "end": 2.0, "text": "Hello world"}], "engine": "local", "model": "tiny"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_sidebar_offers_models_languages_and_key(backend):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    provider, model, language = at.sidebar.selectbox
    assert provider.options == ["OpenAI", "Local (offline)"] and model.value == "whisper-1"
    assert "Hindi" in language.options
    assert at.sidebar.text_input(key="key_openai")  # API key field is shown for cloud providers
    assert at.button[0].disabled  # nothing recorded yet


def test_local_engine_hides_key_and_enables_beam_size(backend):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.selectbox[0].select("local").run()
    assert not any(t.key == "key_local" for t in at.sidebar.text_input)
    assert at.sidebar.selectbox[1].options == ["tiny", "base"]
    beam = next(s for s in at.sidebar.slider if s.label == "Beam size")
    assert beam.disabled is False


def test_offline_backend_shows_help(monkeypatch):
    def down(url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", down)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert "Can't connect to the backend" in at.error[0].value


def test_srt_formatting():
    assert clock(3725.5, srt=True) == "01:02:05,500" and clock(65) == "1:05"
    assert to_srt([{"start": 0, "end": 1.25, "text": "Hi"}]) == "1\n00:00:00,000 --> 00:00:01,250\nHi\n"
