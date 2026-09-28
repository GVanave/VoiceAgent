"""Validate uploaded audio before it is transcribed."""

from dataclasses import dataclass

MIN_AUDIO_BYTES = 1024


class AudioError(ValueError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class Audio:
    data: bytes
    media_type: str
    extension: str


def detect_format(data: bytes) -> tuple[str, str] | None:
    """Identify the audio container from its magic bytes (the client's filename/content type is not trusted)."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm", "webm"  # also what browsers' MediaRecorder produces
    if data[:4] == b"OggS":
        return "audio/ogg", "ogg"
    if data[:4] == b"fLaC":
        return "audio/flac", "flac"
    if data[4:8] == b"ftyp":
        return "audio/mp4", "m4a"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg", "mp3"
    return None


def validate_audio(data: bytes, max_mb: int) -> Audio:
    if len(data) > max_mb * 1024 * 1024:
        raise AudioError(413, "file_too_large", f"Audio file is too large (maximum {max_mb} MB).")
    if len(data) < MIN_AUDIO_BYTES:
        raise AudioError(400, "audio_too_short", "Audio file is empty or too short.")
    fmt = detect_format(data)
    if fmt is None:
        raise AudioError(400, "unsupported_format", "Unsupported audio format. Use WAV, MP3, M4A, WebM, OGG or FLAC.")
    return Audio(data=data, media_type=fmt[0], extension=fmt[1])
