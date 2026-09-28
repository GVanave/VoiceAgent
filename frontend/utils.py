"""Formatting helpers for transcripts (no Streamlit imports, easy to test)."""


def clock(seconds: float, srt: bool = False) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}" if srt else (f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}")


def to_srt(segments: list[dict]) -> str:
    return "\n".join(f"{i}\n{clock(s['start'], True)} --> {clock(s['end'], True)}\n{s['text']}\n"
                     for i, s in enumerate(segments, 1))
