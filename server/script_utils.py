from __future__ import annotations

import re
from typing import Iterable


def clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        line = re.sub(r"^[-*•]+\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line:
            lines.append(line)
    return lines


def split_line_smart(line: str, max_chars: int = 12) -> list[str]:
    """Split Korean short-form lines while preferring whitespace boundaries."""
    line = re.sub(r"\s+", " ", line.strip())
    if not line:
        return []
    if len(line) <= max_chars:
        return [line]

    words = line.split(" ")
    out: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        if len(word) <= max_chars:
            current = word
        else:
            # Long token: prefer punctuation boundaries, then hard cut.
            parts = [p for p in re.split(r"(?<=[,!?…~])", word) if p]
            for part in parts:
                while len(part) > max_chars:
                    out.append(part[:max_chars])
                    part = part[max_chars:]
                if part:
                    if current and len(current + part) <= max_chars:
                        current += part
                    elif current:
                        out.append(current)
                        current = part
                    else:
                        current = part
    if current:
        out.append(current)
    return out


def enforce_short_lines(text: str, max_chars: int = 12, max_lines: int = 80) -> list[str]:
    out: list[str] = []
    for line in clean_lines(text):
        out.extend(split_line_smart(line, max_chars=max_chars))
        if len(out) >= max_lines:
            break
    return out[:max_lines]


def srt_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def make_srt(cues: Iterable[dict]) -> str:
    blocks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        blocks.append(
            f"{idx}\n{srt_timestamp(int(cue['start_ms']))} --> {srt_timestamp(int(cue['end_ms']))}\n{cue['text']}"
        )
    return "\n\n".join(blocks) + "\n"
