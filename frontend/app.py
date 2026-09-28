"""Streamlit frontend for the Voice-to-Text Agent.

Run:  streamlit run app.py      (the backend must be running, see ../README.md)
"""

import json
import os

import httpx
import streamlit as st
from utils import clock, to_srt

DEFAULT_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")

LANGUAGES = {
    "Auto-detect": None, "English": "en", "Hindi": "hi", "Marathi": "mr", "Gujarati": "gu", "Tamil": "ta",
    "Telugu": "te", "Kannada": "kn", "Bengali": "bn", "Urdu": "ur", "German": "de", "French": "fr",
    "Spanish": "es", "Italian": "it", "Portuguese": "pt", "Dutch": "nl", "Russian": "ru", "Turkish": "tr",
    "Arabic": "ar", "Chinese": "zh", "Japanese": "ja", "Korean": "ko",
}
MODEL_NOTES = {
    "whisper-1": "Classic Whisper · timestamps",
    "gpt-4o-transcribe": "Most accurate · no timestamps",
    "gpt-4o-mini-transcribe": "Fast & cheap · no timestamps",
    "whisper-large-v3-turbo": "Very fast · timestamps",
    "whisper-large-v3": "Most accurate Groq model",
    "tiny": "~75 MB · fastest, least accurate",
    "base": "~140 MB · good for quick tests",
    "small": "~460 MB · balanced",
    "medium": "~1.5 GB · accurate, slower",
    "large-v3": "~3 GB · best quality, slowest",
}

st.set_page_config(page_title="Voice to Text", page_icon="🎙️", layout="centered")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2.5rem; max-width: 780px;}
      .hero h1 {font-size: 2.1rem; margin-bottom: 0;}
      .hero p {color: #6B7280; margin-top: .25rem;}
      .pill {display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.8rem; font-weight:600;}
      .pill.ok {background:#ECFDF5; color:#047857;} .pill.bad {background:#FEF2F2; color:#B91C1C;}
      [data-testid="stMetricValue"] {font-size: 1.35rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------- backend ---
def _headers(access_key: str) -> dict:
    return {"Authorization": f"Bearer {access_key}"} if access_key else {}


def _error_message(res: httpx.Response) -> str:
    try:
        return res.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"The server answered with status {res.status_code}."


@st.cache_data(ttl=60, show_spinner=False)
def load_catalog(backend: str, access_key: str) -> dict:
    res = httpx.get(f"{backend}/v1/models", headers=_headers(access_key), timeout=10)
    if res.status_code != 200:
        raise RuntimeError(_error_message(res))
    return res.json()


def transcribe(backend: str, access_key: str, audio: bytes, filename: str, params: dict) -> dict:
    form = {k: str(v) for k, v in params.items() if v not in (None, "")}
    res = httpx.post(f"{backend}/v1/transcribe", headers=_headers(access_key), data=form,
                     files={"file": (filename, audio)}, timeout=600)
    if res.status_code != 200:
        raise RuntimeError(_error_message(res))
    return res.json()


# ------------------------------------------------------------- sidebar ---
with st.sidebar:
    st.header("⚙️ Settings")

    with st.expander("🔌 Connection", expanded=False):
        backend = st.text_input("Backend URL", DEFAULT_BACKEND).rstrip("/")
        access_key = st.text_input("Access key", type="password", help="Only if the backend sets API_KEY.")

    try:
        catalog = load_catalog(backend, access_key)
    except (httpx.HTTPError, RuntimeError) as exc:
        st.markdown('<span class="pill bad">● Backend offline</span>', unsafe_allow_html=True)
        st.caption(str(exc) if isinstance(exc, RuntimeError) else f"Cannot reach {backend}")
        catalog = None
    else:
        st.markdown('<span class="pill ok">● Backend connected</span>', unsafe_allow_html=True)

    if catalog:
        engines = {e["id"]: e for e in catalog["engines"]}
        st.subheader("Model")
        engine_id = st.selectbox("Provider", list(engines), format_func=lambda e: engines[e]["label"],
                                 index=list(engines).index(catalog["default_engine"]))
        engine = engines[engine_id]
        model = st.selectbox("Model", engine["models"], index=engine["models"].index(engine["default_model"]))
        if model in MODEL_NOTES:
            st.caption(MODEL_NOTES[model])

        provider_key = ""
        if engine["needs_api_key"]:
            provider_key = st.text_input(
                f"{engine['label']} API key", type="password", key=f"key_{engine_id}",
                placeholder="Using the server's key" if engine["server_key_configured"] else "Paste your key",
                help="Sent with each request only; never stored.")
            if not provider_key and not engine["server_key_configured"]:
                st.caption("🔑 An API key is required for this provider.")
        elif not engine["ready"]:
            st.warning("The local engine is not installed on the backend (requirements-local.txt).")

        st.subheader("Language")
        language = LANGUAGES[st.selectbox("Spoken language", list(LANGUAGES),
                                          help="Choosing the language improves accuracy and speed.")]

        with st.expander("🎛️ Advanced", expanded=False):
            temperature = st.slider("Temperature", 0.0, 1.0, 0.0, 0.1,
                                    help="0 = most consistent. Raise it only if the output repeats itself.")
            beam_size = st.slider("Beam size", 1, 10, 5, disabled=engine_id != "local",
                                  help="Local engine only. Higher = more accurate but slower.")
            prompt = st.text_area("Context / vocabulary hint", max_chars=500, height=90,
                                  placeholder="e.g. Names and terms: Ganesh, Ausbildung, FastAPI",
                                  help="Helps the model spell names and jargon correctly.")

# ---------------------------------------------------------------- main ---
st.markdown('<div class="hero"><h1>🎙️ Voice to Text</h1>'
            '<p>Record or upload audio and get an accurate transcript in seconds.</p></div>',
            unsafe_allow_html=True)

if not catalog:
    st.error(f"Can't connect to the backend at **{backend}**. Start it with `uvicorn app.main:app` "
             "or change the URL under **Settings → Connection**.")
    st.stop()

source = st.segmented_control("Input", ["🎤 Record", "📁 Upload"], default="🎤 Record",
                              label_visibility="collapsed")
audio_bytes, filename = None, "recording.wav"
if source == "📁 Upload":
    uploaded = st.file_uploader("Audio file", type=["wav", "mp3", "m4a", "webm", "ogg", "flac"],
                                help="Max 25 MB")
    if uploaded:
        audio_bytes, filename = uploaded.getvalue(), uploaded.name
        st.audio(audio_bytes)
else:
    recorded = st.audio_input("Click the microphone and start speaking")
    if recorded:
        audio_bytes = recorded.getvalue()

lang_label = next(name for name, code in LANGUAGES.items() if code == language)
st.caption(f"**{engine['label']}** · {model} · {lang_label} · temperature {temperature:g}"
           "  —  change these in the sidebar ⚙️")

needs_key = engine["needs_api_key"] and not provider_key and not engine["server_key_configured"]
clicked = st.button("✨ Transcribe", type="primary", width="stretch",
                    disabled=audio_bytes is None or needs_key)

if clicked:
    params = {"engine": engine_id, "model": model, "language": language, "temperature": temperature,
              "beam_size": beam_size, "prompt": prompt.strip(), "provider_api_key": provider_key.strip()}
    with st.spinner(f"Transcribing with {engine['label']} · {model}…"):
        try:
            st.session_state.result = transcribe(backend, access_key, audio_bytes, filename, params)
            st.session_state.error = None
        except httpx.HTTPError:
            st.session_state.result, st.session_state.error = None, f"Lost connection to {backend}."
        except RuntimeError as exc:
            st.session_state.result, st.session_state.error = None, str(exc)

if st.session_state.get("error"):
    st.error(st.session_state.error, icon="⚠️")

result = st.session_state.get("result")
if result:
    st.divider()
    st.subheader("Transcript")
    words = len(result["text"].split())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Language", (result.get("language") or "—").title())
    duration, took = result.get("duration_seconds"), result.get("processing_seconds")
    c2.metric("Duration", clock(duration) if duration is not None else "—")
    c3.metric("Processed in", f"{took:.1f}s" if took is not None else "—")
    c4.metric("Words", words)

    text = st.text_area("Transcript", result["text"], height=220, label_visibility="collapsed")
    st.caption(f"{result['engine']} · {result['model']}  —  you can edit the text before downloading.")

    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Text (.txt)", text, "transcript.txt", "text/plain", width="stretch")
    d2.download_button("⬇️ Subtitles (.srt)", to_srt(result["segments"]), "transcript.srt", "text/plain",
                       width="stretch", disabled=not result["segments"],
                       help=None if result["segments"] else "This model does not return timestamps.")
    d3.download_button("⬇️ JSON", json.dumps(result, indent=2, ensure_ascii=False), "transcript.json",
                       "application/json", width="stretch")

    if result["segments"]:
        with st.expander(f"🕒 Timestamps ({len(result['segments'])} segments)"):
            st.dataframe([{"Start": clock(s["start"]), "End": clock(s["end"]), "Text": s["text"]}
                          for s in result["segments"]], hide_index=True, width="stretch")
