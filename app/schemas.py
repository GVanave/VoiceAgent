from pydantic import BaseModel


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    text: str
    language: str | None
    duration_seconds: float | None
    segments: list[Segment]
    engine: str
    model: str
    processing_seconds: float | None = None


class Health(BaseModel):
    status: str
    engine: str
    ready: bool


class EngineInfo(BaseModel):
    id: str
    label: str
    models: list[str]
    default_model: str
    needs_api_key: bool
    server_key_configured: bool
    ready: bool


class Catalog(BaseModel):
    default_engine: str
    engines: list[EngineInfo]
