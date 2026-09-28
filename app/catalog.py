"""The engines and models this agent offers. Only models listed here can be requested."""

from app.config import Settings

ENGINES: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "models": ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
        "needs_api_key": True,
    },
    "groq": {
        "label": "Groq",
        "models": ["whisper-large-v3-turbo", "whisper-large-v3"],
        "needs_api_key": True,
    },
    "local": {
        "label": "Local (offline)",
        "models": ["tiny", "base", "small", "medium", "large-v3"],
        "needs_api_key": False,
    },
}


def default_model(engine: str, settings: Settings) -> str:
    return {"openai": settings.openai_model, "groq": settings.groq_model, "local": settings.local_model}[engine]


def models_for(engine: str, settings: Settings) -> list[str]:
    models = list(ENGINES[engine]["models"])
    configured = default_model(engine, settings)
    if configured not in models:  # a model set in the server's .env is always allowed
        models.insert(0, configured)
    return models
