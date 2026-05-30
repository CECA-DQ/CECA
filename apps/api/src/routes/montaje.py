"""Multi-source video assembly for TV pieces.

Normalises each clip to a common format (1280x720, h264, aac) before
concatenating so that dimension or codec mismatches never break the join.
"""

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/montaje", tags=["montaje"])

_STORAGE_BASE = Path("data/storage")
_OUTPUT_DIR = _STORAGE_BASE / "output" / "montajes"
_W, _H = 1280, 720


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SegmentoMontaje(BaseModel):
    storage_key: str
    tiempo_inicio: float = 0.0
    tiempo_fin: float | None = None   # None = until end of clip
    tipo: str = "broll"               # declaracion | broll | recurso | intro | cierre
    orden: int = 0


class EnsamblarRequest(BaseModel):
    tipo_pieza: str = "cola"
    titular: str = ""
    segmentos: list[SegmentoMontaje]
    audio_voiceover_key: str | None = None
    duracion_objetivo: int | None = None


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")
    return p


def _ffprobe() -> str:
    p = shutil.which("ffprobe")
    if not p:
        raise HTTPException(status_code=500, detail="ffprobe not found on PATH")
    return p


async def _get_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        _ffprobe(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except Exception:
        return 0.0


def _normalize_cmd(
    ffmpeg: str, source: Path, t_start: float, t_end: float | None,
    out: Path, mute: bool = False,
) -> list[str]:
    """Build the ffmpeg cut/re-encode command. When mute, drop the audio stream."""
    vf = (
        f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
        f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )
    # Use -t (duration) not -to (end time): more reliable with fast seek because
    # keyframe alignment can leave a short audio tail with -to.
    cmd = [ffmpeg, "-y", "-ss", str(t_start)]
    if t_end is not None:
        cmd += ["-t", str(round(t_end - t_start, 3))]
    cmd += [
        "-i", str(source),
        "-vf", vf,
        "-r", "25", "-vsync", "cfr", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


async def _normalizar_clip(
    source: Path, t_start: float, t_end: float | None, out: Path, mute: bool = False,
) -> None:
    """Cut and re-encode to 1280x720 h264/aac so all clips are concat-compatible."""
    cmd = _normalize_cmd(_ffmpeg(), source, t_start, t_end, out, mute=mute)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Clip normalisation failed ({source.name}): {stderr.decode()[-300:]}")


async def _concat(clip_paths: list[Path], out: Path) -> None:
    """Concatenate normalised clips (same codec → stream copy, no re-encode)."""
    list_file = out.parent / f"{out.stem}_concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg(), "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Concat failed: {stderr.decode()[-300:]}")


async def _loop_to_duration(source: Path, target: float, out: Path) -> None:
    """Re-encode looping the source until it reaches target duration."""
    cmd = [
        _ffmpeg(), "-y",
        "-stream_loop", "-1",
        "-i", str(source),
        "-t", str(target),
        "-r", "25", "-vsync", "cfr", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Loop to duration failed: {stderr.decode()[-300:]}")


async def _mix_voiceover(video: Path, audio: Path, out: Path) -> None:
    """Mix voiceover at full volume over original audio ducked to 15%."""
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg(), "-y",
        "-i", str(video),
        "-i", str(audio),
        "-filter_complex",
        "[0:a]volume=0.15[orig];[1:a]volume=1.0[vo];[orig][vo]amix=inputs=2:duration=first[out]",
        "-map", "0:v", "-map", "[out]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Voiceover mix failed: {stderr.decode()[-300:]}")


async def _normalize_loudness(source: Path, out: Path) -> None:
    """EBU R128 two-pass loudness normalization: -23 LUFS / LRA 11 / TP -1.5 dBTP."""
    ffmpeg = _ffmpeg()

    # Pass 1 — measure integrated loudness
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(source),
        "-af", "loudnorm=I=-23:LRA=11:TP=-1.5:print_format=json",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr1 = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Loudnorm pass 1 failed: {stderr1.decode()[-300:]}")

    # Extract JSON measurement block from stderr
    match = re.search(r"\{[^{}]*\}", stderr1.decode(), re.DOTALL)
    if not match:
        raise RuntimeError("Loudnorm pass 1 returned no measurement JSON")
    try:
        m = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse loudnorm JSON: {exc}") from exc

    af = (
        f"loudnorm=I=-23:LRA=11:TP=-1.5"
        f":measured_I={m['input_i']}"
        f":measured_LRA={m['input_lra']}"
        f":measured_TP={m['input_tp']}"
        f":measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}"
        f":linear=true:print_format=none"
    )

    # Pass 2 — apply normalization (video stream copied, audio re-encoded)
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", str(source),
        "-af", af,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        str(out),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr2 = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Loudnorm pass 2 failed: {stderr2.decode()[-300:]}")


# ---------------------------------------------------------------------------
# Core assembly logic (reusable by generar-pieza in Task 3)
# ---------------------------------------------------------------------------

async def ensamblar(
    segmentos: list[SegmentoMontaje],
    audio_voiceover_key: str | None = None,
    output_key: str | None = None,
    duracion_objetivo: int | None = None,
    normalize_audio: bool = True,
    mute_clips: bool = False,
) -> tuple[str, float, bool]:
    """Assemble clips and return (video_key, duration_seconds, material_en_loop)."""
    if not segmentos:
        raise HTTPException(status_code=422, detail="At least one segment is required")

    ordered = sorted(segmentos, key=lambda s: s.orden)

    # Validate all source files exist
    sources: list[Path] = []
    for seg in ordered:
        p = (_STORAGE_BASE / seg.storage_key).resolve()
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {seg.storage_key}")
        sources.append(p)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    uid = str(uuid4())
    work_dir = _OUTPUT_DIR / uid
    work_dir.mkdir()

    try:
        # 1. Normalise each clip
        clip_paths: list[Path] = []
        for i, (seg, src) in enumerate(zip(ordered, sources)):
            clip = work_dir / f"clip_{i:03d}.mp4"
            await _normalizar_clip(src, seg.tiempo_inicio, seg.tiempo_fin, clip, mute=mute_clips)
            clip_paths.append(clip)

        # 2. Concatenate
        out_key = output_key or f"output/montajes/{uid}.mp4"
        out_path = (_STORAGE_BASE / out_key).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if len(clip_paths) == 1:
            clip_paths[0].rename(out_path)
        else:
            await _concat(clip_paths, out_path)

        # 3. Loop material if shorter than target
        material_en_loop = False
        duration = await _get_duration(out_path)
        # Only loop when material is genuinely too short (< 85% of target).
        # A small shortfall (editorial trim, budget enforcement) must NOT trigger
        # a loop — that would repeat the opening clip at the end of the piece.
        if duracion_objetivo and duration < duracion_objetivo * 0.40:
            looped = out_path.with_stem(out_path.stem + "_looped")
            await _loop_to_duration(out_path, float(duracion_objetivo), looped)
            out_path.unlink()
            looped.rename(out_path)
            material_en_loop = True
            duration = await _get_duration(out_path)

        # 4. Mix voiceover if provided
        if audio_voiceover_key:
            audio_path = (_STORAGE_BASE / audio_voiceover_key).resolve()
            if audio_path.exists():
                mixed = out_path.with_stem(out_path.stem + "_mixed")
                await _mix_voiceover(out_path, audio_path, mixed)
                out_path.unlink()
                mixed.rename(out_path)

        # 5. EBU R128 loudness normalization (-23 LUFS / LRA 11 / TP -1.5)
        if normalize_audio:
            normalized = out_path.with_stem(out_path.stem + "_norm")
            try:
                await _normalize_loudness(out_path, normalized)
                out_path.unlink()
                normalized.rename(out_path)
            except RuntimeError as exc:
                logger.warning("Loudness normalization skipped (no audio track?): %s", exc)
                normalized.unlink(missing_ok=True)

        duration = await _get_duration(out_path)
        return out_key, duration, material_en_loop

    finally:
        # Clean up normalised intermediate clips
        for clip in work_dir.iterdir():
            clip.unlink(missing_ok=True)
        work_dir.rmdir()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/ensamblar")
async def ensamblar_endpoint(body: EnsamblarRequest) -> dict:
    try:
        video_key, duration, material_en_loop = await ensamblar(
            segmentos=body.segmentos,
            audio_voiceover_key=body.audio_voiceover_key,
            duracion_objetivo=body.duracion_objetivo,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "ok": True,
        "video_key": video_key,
        "video_url": f"/api/montaje/video/{video_key}",
        "duracion_real": round(duration, 1),
        "fps_salida": 25,
        "resolucion": f"{_W}x{_H}",
        "material_en_loop": material_en_loop,
        "segmentos_montados": len(body.segmentos),
        "tipo_pieza": body.tipo_pieza,
        "lufs_salida": -23,
    }


@router.get("/video/{key:path}")
async def stream_montaje_video(key: str) -> FileResponse:
    path = (_STORAGE_BASE / key).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)
