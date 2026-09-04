from __future__ import annotations
import base64
import shlex
import subprocess
from pathlib import Path


def render_transparent_mov(output_path: Path, cues: list[dict], image_dir: Path, width: int = 1080, height: int = 1920, fps: int = 30, image_x: int = 0, image_y: int = 0, image_scale: float = 0.72) -> None:
    """Create a silent ProRes 4444 MOV with alpha using a low-memory sequential timeline."""
    if not cues:
        raise RuntimeError("SRT 타이밍이 비어 있습니다.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_sec = max(0.1, max(int(c["end_ms"]) for c in cues) / 1000.0)
    blank = output_path.with_name(".transparent_blank.png")
    timeline = output_path.with_name(".transparent_timeline.ffconcat")
    blank.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAADElEQVR42mNk+A8EAAn7A/0xRkyWAAAAAElFTkSuQmCC"))
    entries = []
    cursor = 0.0
    for cue in sorted(cues, key=lambda c: int(c["start_ms"])):
        start = max(cursor, int(cue["start_ms"]) / 1000.0)
        end = max(start, int(cue["end_ms"]) / 1000.0)
        if start > cursor:
            entries.append((blank, start - cursor))
        image = image_dir / Path(str(cue.get("image", ""))).name
        entries.append((image if image.exists() else blank, max(0.001, end - start)))
        cursor = end
    if cursor < total_sec:
        entries.append((blank, total_sec - cursor))
    def escaped(path: Path) -> str:
        return str(path.resolve()).replace("'", "'\\''")
    lines = ["ffconcat version 1.0"]
    for path, duration in entries:
        lines += [f"file '{escaped(path)}'", f"duration {duration:.6f}"]
    lines.append(f"file '{escaped(entries[-1][0])}'")
    timeline.write_text("\n".join(lines) + "\n", encoding="utf-8")
    scale = max(0.05, image_scale)
    filters = (f"[0:v]format=rgba,scale=iw*{scale}:ih*{scale}[img];" f"[1:v]format=rgba[base];" f"[base][img]overlay=(W-w)/2+{image_x}:(H-h)/2+{image_y}:shortest=1:format=auto,format=yuva444p10le[vout]")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(timeline), "-f", "lavfi", "-t", f"{total_sec:.3f}", "-i", f"color=c=black@0.0:s={width}x{height}:r={fps},format=rgba", "-filter_complex", filters, "-map", "[vout]", "-an", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-r", str(fps), str(output_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("투명 MOV 렌더링 실패\n명령어: " + " ".join(shlex.quote(x) for x in cmd) + "\n" + exc.stderr.decode("utf-8", errors="ignore")[-6000:]) from exc
    finally:
        blank.unlink(missing_ok=True)
        timeline.unlink(missing_ok=True)
