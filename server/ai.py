from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

GEMINI_INTERACTIONS = "https://generativelanguage.googleapis.com/v1beta/interactions"
OPENAI_RESPONSES = "https://api.openai.com/v1/responses"
OPENAI_IMAGES = "https://api.openai.com/v1/images/generations"

DEFAULT_TTS_STYLE = """A lively Korean male narrator in his late 20s to early 30s.
Speak in natural, conversational Korean optimized for YouTube Shorts.
Bright, witty, playful, slightly comedic and energetic.
Use a moderately fast pace and punchy rhythm with subtle comedic timing.
Naturally emphasize punchlines, surprising facts, rhetorical questions, contrasts and key words.
Sound like a funny charismatic friend explaining something interesting.
Do not sound like a news anchor, formal announcer, commercial narrator, documentary narrator, or exaggerated cartoon character.
Avoid excessive shouting, overacting, robotic cadence, and unnatural pauses.
Keep Korean pronunciation clear at a slightly fast pace."""

DEFAULT_SCRIPT_PROMPT = """You are a Korean short-form script editor.
Rewrite the SOURCE into an entertaining Korean explanatory Shorts script.
Rules:
- Korean banmal / conversational speech.
- Witty, punchy, lightly comedic; do not force jokes every line.
- Keep factual meaning. Do not invent medical or scientific claims.
- Each output line MUST be 12 Korean characters or fewer including spaces and punctuation whenever possible.
- One subtitle unit per line.
- Natural flow between lines.
- No numbering, bullets, markdown, emojis, headings, or explanations.
- Output ONLY the final script lines.

SOURCE:
{source}
"""

DEFAULT_IMAGE_PROMPT = """Create one standalone visual asset for a Korean explanatory comedy short-form video.
The visual must match this narration cue: {line}
Nearby context: {context}

Requirements:
- funny, instantly understandable visual gag
- one main visual idea only
- character does NOT need to stay consistent with other images
- may use a person, animal, object, food, symbol, or absurd metaphor
- 2D or 3D style is fine; vary styles naturally
- NO text, NO captions, NO letters, NO numbers, NO logos, NO speech bubbles
- transparent background
- strong readable silhouette and clean composition
- suitable to place over a vertical 9:16 video background
"""

DEFAULT_CHARACTER_PROMPT = """Use a funny visual character or object when helpful.
The character does NOT need to stay consistent across all images unless explicitly requested.
A person, object, animal, food, mascot, or absurd metaphor is allowed."""


class AIError(RuntimeError):
    pass


def _post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code >= 400:
        body = r.text[:1200]
        raise AIError(f"HTTP {r.status_code}: {body}")
    try:
        return r.json()
    except Exception as e:
        raise AIError(f"Invalid JSON response: {r.text[:500]}") from e


def _find_first(obj: Any, predicate) -> Any | None:
    if predicate(obj):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_first(value, predicate)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_first(value, predicate)
            if found is not None:
                return found
    return None


def generate_script(source: str, *, instructions: str = DEFAULT_SCRIPT_PROMPT, openai_key_override: str | None = None, gemini_key_override: str | None = None) -> tuple[str, str]:
    template = instructions.strip() or DEFAULT_SCRIPT_PROMPT
    prompt = template.replace("{source}", source) if "{source}" in template else f"{template}\n\nSOURCE:\n{source}"
    openai_key = (openai_key_override or os.getenv("OPENAI_API_KEY", "")).strip()
    if openai_key:
        payload = {
            "model": os.getenv("OPENAI_TEXT_MODEL", "gpt-5.6-luna"),
            "input": prompt,
        }
        data = _post_json(
            OPENAI_RESPONSES,
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            payload=payload,
        )
        # Responses API usually exposes message -> content -> output_text.
        item = _find_first(data, lambda x: isinstance(x, dict) and x.get("type") == "output_text" and isinstance(x.get("text"), str))
        if item:
            return item["text"].strip(), "openai"
        if isinstance(data.get("output_text"), str):
            return data["output_text"].strip(), "openai"
        raise AIError("OpenAI response did not contain output text")

    gemini_key = (gemini_key_override or os.getenv("GEMINI_API_KEY", "")).strip()
    if not gemini_key:
        raise AIError("Set OPENAI_API_KEY or GEMINI_API_KEY to rewrite the script")
    payload = {
        "model": os.getenv("GEMINI_TEXT_MODEL", "gemini-3.7-flash"),
        "input": prompt,
    }
    data = _post_json(
        GEMINI_INTERACTIONS,
        headers={"x-goog-api-key": gemini_key, "Content-Type": "application/json"},
        payload=payload,
    )
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip(), "gemini"
    node = _find_first(data, lambda x: isinstance(x, dict) and isinstance(x.get("text"), str))
    if node:
        return node["text"].strip(), "gemini"
    raise AIError("Gemini response did not contain output text")


def synthesize_gemini_tts(text: str, out_wav: Path, voice: str = "Puck", style: str = DEFAULT_TTS_STYLE, retries: int = 3, gemini_key_override: str | None = None) -> None:
    key = (gemini_key_override or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise AIError("GEMINI_API_KEY is required for TTS")

    prompt = f"""SYNTHESIZE SPEECH ONLY.
Follow the STYLE directions, but do not read the directions aloud.
Read only the exact text under TRANSCRIPT.
Language: Korean.

STYLE:
{style}

TRANSCRIPT:
{text}
"""
    payload = {
        "model": os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"),
        "input": prompt,
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            data = _post_json(
                GEMINI_INTERACTIONS,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                payload=payload,
                timeout=180,
            )
            audio = data.get("output_audio")
            b64 = audio.get("data") if isinstance(audio, dict) else None
            if not b64:
                node = _find_first(
                    data,
                    lambda x: isinstance(x, dict)
                    and isinstance(x.get("data"), str)
                    and (x.get("type") == "audio" or "mime_type" in x or "mimeType" in x),
                )
                b64 = node.get("data") if node else None
            if not b64:
                raise AIError("Gemini TTS response did not contain audio data")
            pcm = base64.b64decode(b64)
            # Current Gemini TTS sample output is raw 24kHz PCM16 mono.
            from audio_utils import write_pcm16_wav

            write_pcm16_wav(out_wav, pcm, sample_rate=24000)
            return
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.2 * attempt)
    raise AIError(f"Gemini TTS failed after {retries} attempts: {last_error}")


def _image_prompt(line: str, context: str, image_instructions: str, character_instructions: str) -> str:
    base = image_instructions.strip() or DEFAULT_IMAGE_PROMPT
    if "{line}" in base or "{context}" in base:
        prompt = base.replace("{line}", line).replace("{context}", context)
    else:
        prompt = f"{base}\n\nCurrent narration: {line}\nNearby context: {context}"
    character = character_instructions.strip() or DEFAULT_CHARACTER_PROMPT
    return f"{prompt}\n\nCHARACTER / OBJECT DIRECTION:\n{character}"


def generate_image_gemini(line: str, context: str, out_path: Path, *, image_instructions: str = DEFAULT_IMAGE_PROMPT, character_instructions: str = DEFAULT_CHARACTER_PROMPT, gemini_key_override: str | None = None) -> None:
    key = (gemini_key_override or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise AIError("GEMINI_API_KEY is required for Gemini image generation")

    prompt = _image_prompt(line, context, image_instructions, character_instructions)
    payload = {
        "model": os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image"),
        "input": prompt,
        "response_format": {
            "type": "image",
            "mime_type": "image/png",
            "aspect_ratio": os.getenv("GEMINI_IMAGE_ASPECT_RATIO", "1:1"),
            "image_size": "1K",
        },
    }
    data = _post_json(
        GEMINI_INTERACTIONS,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        payload=payload,
        timeout=300,
    )
    # Interactions API exposes output_image; tolerate nested representations too.
    image = data.get("output_image")
    b64 = image.get("data") if isinstance(image, dict) else None
    if not b64:
        node = _find_first(
            data,
            lambda x: isinstance(x, dict)
            and isinstance(x.get("data"), str)
            and x.get("type") == "image",
        )
        b64 = node.get("data") if node else None
    if not b64:
        raise AIError("Gemini image response did not contain image data")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(b64))


def generate_image_openai(line: str, context: str, out_path: Path, *, image_instructions: str = DEFAULT_IMAGE_PROMPT, character_instructions: str = DEFAULT_CHARACTER_PROMPT, openai_key_override: str | None = None) -> None:
    key = (openai_key_override or os.getenv("OPENAI_API_KEY", "")).strip()
    if not key:
        raise AIError("OPENAI_API_KEY is required for automatic image generation")

    prompt = _image_prompt(line, context, image_instructions, character_instructions)
    payload = {
        "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "prompt": prompt,
        "size": "1024x1536",
        "quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
        "background": "transparent",
        "output_format": "png",
    }
    data = _post_json(
        OPENAI_IMAGES,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload=payload,
        timeout=300,
    )
    node = _find_first(data, lambda x: isinstance(x, dict) and isinstance(x.get("b64_json"), str))
    b64 = node.get("b64_json") if node else None
    if not b64:
        raise AIError("OpenAI response did not contain generated image data")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(b64))
