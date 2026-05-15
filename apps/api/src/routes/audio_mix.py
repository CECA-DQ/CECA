"""Módulo 5: Mezcla de voiceover con vídeo usando ffmpeg."""

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/audio", tags=["audio"])

_STORAGE_BASE = Path("data/storage")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MezclarAudioRequest(BaseModel):
    video_key: str              # storage key del vídeo base
    audio_key: str              # storage key del MP3 de voiceover
    modo: str = "reemplazar"    # "reemplazar" | "mezclar"
    volumen_original: float = Field(default=0.1, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _resolve(key: str) -> Path:
    return (_STORAGE_BASE / key).resolve()


async def _mix(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    modo: str,
    vol_original: float,
    ffmpeg: str,
) -> None:
    if modo == "reemplazar":
        cmd = [
            ffmpeg, "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-map", "0:v",
            "-map", "1:a",
            "-shortest",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
    else:  # mezclar
        cmd = [
            ffmpeg, "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-filter_complex",
            f"[0:a]volume={vol_original}[a_orig];[1:a]volume=1.0[a_vo];[a_orig][a_vo]amix=inputs=2:duration=shortest[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-shortest",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio mix failed: {stderr.decode()[-400:]}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/mezclar-con-video")
async def mezclar_audio_con_video(body: MezclarAudioRequest) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    video_path = _resolve(body.video_key)
    audio_path = _resolve(body.audio_key)

    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {body.video_key}")
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail=f"Audio not found: {body.audio_key}")

    output_key = f"output/mezcla/{uuid4()}.mp4"
    output_path = _resolve(output_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    await _mix(video_path, audio_path, output_path, body.modo, body.volumen_original, ffmpeg)

    return {
        "ok": True,
        "video_key": output_key,
        "video_url": f"/api/audio/video/{output_key}",
        "modo": body.modo,
    }


@router.get("/video/{key:path}")
async def stream_mixed_video(key: str) -> Response:
    video_path = _resolve(key)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return Response(
        content=video_path.read_bytes(),
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename=\"{video_path.name}\""},
    )
