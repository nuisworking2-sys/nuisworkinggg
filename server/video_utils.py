from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    background_mode: str = "color"  # color | image
    background_color: str = "#111111"
    background_image: Path | None = None
    image_x: int = 0
    image_y: int = -120
    image_scale: float = 0.72
    subtitle_align: str = "bottom"
    subtitle_y: int = 1480
    subtitle_font: str = "Pretendard"
    subtitle_font_size: int = 62
    subtitle_outline: int = 4


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _srt_time(ms: int) -> str:
    cs = int(round(ms / 10.0))
    h = cs // 360000
    m = (cs % 360000) // 6000
    s = (cs % 6000) // 100
    c = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def make_ass(cues: Iterable[dict], settings: RenderSettings, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    x = settings.width // 2
    align_map = {"top": 8, "middle": 5, "bottom": 2}
    an = align_map.get(settings.subtitle_align, 2)
    y_default = {"top": 220, "middle": settings.height // 2, "bottom": settings.height - 360}[settings.subtitle_align if settings.subtitle_align in {"top", "middle", "bottom"} else "bottom"]
    y = settings.subtitle_y if settings.subtitle_y is not None else y_default
    style = f"Style: Default,{settings.subtitle_font},{settings.subtitle_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,{settings.subtitle_outline},0,2,40,40,50,1"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {settings.width}",
        f"PlayResY: {settings.height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        style,
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for cue in cues:
        text = _ass_escape(str(cue["text"]))
        tag = f"{{\\an{an}\\pos({x},{y})}}"
        lines.append(f"Dialogue: 0,{_srt_time(int(cue['start_ms']))},{_srt_time(int(cue['end_ms']))},Default,,0,0,0,,{tag}{text}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _ffmpeg_escape_filter_path(path: Path) -> str:
    s = str(path.resolve())
    s = s.replace("\\", r"\\")
    s = s.replace(":", r"\:")
    s = s.replace(",", r"\,")
    s = s.replace("'", r"\'")
    return s


def render_video_mp4(
    output_path: Path,
    audio_path: Path,
    cues: list[dict],
    image_dir: Path | None,
    settings: RenderSettings,
) -> None:
    ffmpeg = "ffmpeg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = output_path.with_suffix(".ass")
    make_ass(cues, settings, ass_path)

    total_sec = max(1.0, max((int(c["end_ms"]) for c in cues), default=1000) / 1000.0 + 0.25)

    cmd = [ffmpeg, "-y"]
    if settings.background_mode == "image" and settings.background_image:
        cmd += ["-loop", "1", "-t", f"{total_sec:.3f}", "-i", str(settings.background_image)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{total_sec:.3f}", "-i", f"color=c={settings.background_color}:s={settings.width}x{settings.height}:r={settings.fps}"]
    cmd += ["-i", str(audio_path)]

    image_inputs: list[tuple[int, dict, Path]] = []
    next_input_idx = 2
    if image_dir and image_dir.exists():
        for cue in cues:
            img_rel = cue.get("image")
            if not img_rel:
                continue
            img_path = image_dir / Path(str(img_rel)).name
            if not img_path.exists():
                continue
            cmd += ["-loop", "1", "-t", f"{total_sec:.3f}", "-i", str(img_path)]
            image_inputs.append((next_input_idx, cue, img_path))
            next_input_idx += 1

    bg_filter = f"[0:v]scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,crop={settings.width}:{settings.height},setsar=1[bg0]"
    filters = [bg_filter]
    current = "bg0"
    for n, cue, _img_path in image_inputs:
        start = int(cue["start_ms"]) / 1000.0
        end = int(cue["end_ms"]) / 1000.0
        overlay_name = f"v{n}"
        x = f"(W-w)/2+{settings.image_x}"
        y = f"(H-h)/2+{settings.image_y}"
        scale_expr = max(settings.image_scale, 0.05)
        filters.append(f"[{n}:v]scale=iw*{scale_expr}:ih*{scale_expr}[img{n}]")
        filters.append(
            f"[{current}][img{n}]overlay=x={x}:y={y}:enable='between(t,{start:.3f},{end:.3f})':format=auto[{overlay_name}]"
        )
        current = overlay_name

    ass_filter = _ffmpeg_escape_filter_path(ass_path)
    filters.append(f"[{current}]subtitles='{ass_filter}'[vout]")

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(settings.fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "ffmpeg 렌더링 실패\n"
            f"명령어: {' '.join(shlex.quote(c) for c in cmd)}\n\n"
            f"stderr:\n{e.stderr.decode('utf-8', errors='ignore')[-4000:]}"
        )


def render_transparent_mov(
    output_path: Path,
    cues: list[dict],
    image_dir: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    image_x: int = 0,
    image_y: int = 0,
    image_scale: float = 0.72,
) -> None:
    """Render silent ProRes 4444 MOV while preserving PNG alpha."""
    total_sec = max(0.1, max(int(cue["end_ms"]) for cue in cues) / 1000.0)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-t", f"{total_sec:.3f}",
        "-i", f"color=c=black@0.0:s={width}x{height}:r={fps},format=rgba",
    ]
    inputs: list[tuple[int, dict]] = []
    for cue in cues:
        image = image_dir / Path(str(cue["image"])).name
        if not image.exists():
            continue
        input_index = len(inputs) + 1
        cmd += ["-loop", "1", "-t", f"{total_sec:.3f}", "-i", str(image)]
        inputs.append((input_index, cue))
    filters = ["[0:v]format=rgba[base]"]
    current = "base"
    for input_index, cue in inputs:
        name = f"v{input_index}"
        start, end = int(cue["start_ms"]) / 1000, int(cue["end_ms"]) / 1000
        filters.append(f"[{input_index}:v]format=rgba,scale=iw*{max(.05, image_scale)}:ih*{max(.05, image_scale)}[img{input_index}]")
        filters.append(
            f"[{current}][img{input_index}]overlay=(W-w)/2+{image_x}:(H-h)/2+{image_y}:"
            f"enable='between(t,{start:.3f},{end:.3f})':format=auto[{name}]"
        )
        current = name
    filters.append(f"[{current}]format=yuva444p10le[vout]")
    cmd += [
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-an",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
        "-alpha_bits", "16", "-r", str(fps), str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "투명 MOV 렌더링 실패\n" + exc.stderr.decode("utf-8", errors="ignore")[-4000:]
        ) from exc
