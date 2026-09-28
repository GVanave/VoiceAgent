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


class Health(BaseModel):
    status: str
    engine: str
    ready: bool
