from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path


def write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def _rms(samples: array) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples))


def trim_wav_silence(
    src: Path,
    dst: Path,
    threshold_dbfs: float = -43.0,
    chunk_ms: int = 20,
    padding_ms: int = 40,
) -> int:
    """Trim leading/trailing silence from PCM16 mono/stereo WAV. Returns duration ms."""
    with wave.open(str(src), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError("Only 16-bit PCM WAV is supported")

    samples = array("h")
    samples.frombytes(frames)
    frame_count = len(samples) // channels
    if frame_count <= 0:
        dst.write_bytes(src.read_bytes())
        return 0

    chunk_frames = max(1, int(rate * chunk_ms / 1000))
    max_amp = 32767.0
    threshold = max_amp * (10 ** (threshold_dbfs / 20.0))

    active: list[int] = []
    for start_frame in range(0, frame_count, chunk_frames):
        end_frame = min(frame_count, start_frame + chunk_frames)
        start_i = start_frame * channels
        end_i = end_frame * channels
        if _rms(samples[start_i:end_i]) >= threshold:
            active.append(start_frame)

    if not active:
        start_frame, end_frame = 0, frame_count
    else:
        pad = int(rate * padding_ms / 1000)
        start_frame = max(0, active[0] - pad)
        end_frame = min(frame_count, active[-1] + chunk_frames + pad)

    start_i = start_frame * channels
    end_i = end_frame * channels
    trimmed = samples[start_i:end_i]

    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(trimmed.tobytes())

    return int(round((end_frame - start_frame) * 1000 / rate))


def concat_wavs(paths: list[Path], dst: Path, gap_ms: int = 80) -> tuple[list[tuple[int, int]], int]:
    """Concatenate compatible WAVs; return cue (start,end) timings and total duration in ms."""
    if not paths:
        raise ValueError("No WAV files to concatenate")

    params = None
    all_frames: list[bytes] = []
    timings: list[tuple[int, int]] = []
    cursor_ms = 0

    for idx, path in enumerate(paths):
        with wave.open(str(path), "rb") as wf:
            p = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise ValueError(f"WAV format mismatch: {path}")
            frame_count = wf.getnframes()
            audio = wf.readframes(frame_count)
            duration_ms = int(round(frame_count * 1000 / wf.getframerate()))
            all_frames.append(audio)
            timings.append((cursor_ms, cursor_ms + duration_ms))
            cursor_ms += duration_ms

            if idx != len(paths) - 1 and gap_ms > 0:
                channels, width, rate = params
                gap_frames = int(rate * gap_ms / 1000)
                all_frames.append(b"\x00" * gap_frames * channels * width)
                cursor_ms += gap_ms

    channels, width, rate = params  # type: ignore[misc]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        for chunk in all_frames:
            out.writeframes(chunk)

    return timings, cursor_ms
