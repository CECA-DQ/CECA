"""Módulo 4: Overlay de títulos y grafismos sobre vídeo.

Uses Pillow to render graphic elements as RGBA PNGs, then ffmpeg overlay
filter to composite them onto the video at the specified time ranges.
No libfreetype required in the ffmpeg build.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/grafismo", tags=["grafismo"])

_OUTPUT_DIR = Path("data/storage/output/grafismo")
_STORAGE_BASE = Path("data/storage")

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GrafismoElemento(BaseModel):
    tipo: str                  # "titular" | "rotulo_persona" | "dato" | "pie_pagina"
    texto_principal: str
    texto_secundario: str = ""
    tiempo_inicio: float
    duracion: float
    posicion: str = "inferior"  # "superior" | "inferior" | "centro"


class GrafismoRequest(BaseModel):
    video_input_key: str
    elementos: list[GrafismoElemento]


# ---------------------------------------------------------------------------
# Pillow rendering helpers
# ---------------------------------------------------------------------------

def _find_font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_elemento_png(
    el: GrafismoElemento,
    width: int,
    height: int,
    out_path: Path,
) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_main = _find_font(32)
    font_sec = _find_font(22)

    pad = 10

    if el.posicion == "superior":
        y_main, y_sec = 40, 82
    elif el.posicion == "centro":
        y_main = height // 2 - 22
        y_sec  = height // 2 + 18
    else:
        y_main = height - 95
        y_sec  = height - 58

    def _draw_box_text(text: str, x: int, y: int, font, fg, bg=(0, 0, 0, 175)):
        bbox = draw.textbbox((x, y), text, font=font)
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad // 2,
             bbox[2] + pad, bbox[3] + pad // 2],
            fill=bg,
        )
        draw.text((x, y), text, font=font, fill=fg)

    if el.tipo == "titular":
        text = el.texto_principal.upper()
        bbox = draw.textbbox((0, 0), text, font=font_main)
        x = (width - (bbox[2] - bbox[0])) // 2
        _draw_box_text(text, x, y_main, font_main, (255, 255, 255, 255))
        if el.texto_secundario:
            sec = el.texto_secundario
            sbbox = draw.textbbox((0, 0), sec, font=font_sec)
            sx = (width - (sbbox[2] - sbbox[0])) // 2
            _draw_box_text(sec, sx, y_sec, font_sec, (220, 220, 220, 255), (0, 0, 0, 140))

    elif el.tipo == "rotulo_persona":
        _draw_box_text(el.texto_principal, 50, y_main, font_main, (255, 255, 255, 255))
        if el.texto_secundario:
            _draw_box_text(el.texto_secundario, 50, y_sec, font_sec, (255, 215, 0, 255), (0, 0, 0, 150))

    else:  # dato / pie_pagina
        _draw_box_text(el.texto_principal, 50, y_main, font_main, (255, 255, 255, 255))

    img.save(str(out_path), "PNG")


async def _get_video_dimensions(ffprobe: str, video_path: Path) -> tuple[int, int]:
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        w, h = stdout.decode().strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1280, 720


async def _apply_grafismos(
    input_path: Path,
    elementos: list[GrafismoElemento],
    output_path: Path,
    ffmpeg: str,
    ffprobe: str,
    tmpdir: Path,
) -> None:
    width, height = await _get_video_dimensions(ffprobe, input_path)

    # Render one PNG per element
    png_paths: list[Path] = []
    for i, el in enumerate(elementos):
        png = tmpdir / f"overlay_{i}.png"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _render_elemento_png, el, width, height, png)
        png_paths.append(png)

    # Build ffmpeg filter_complex: chain overlay filters with time-based enable
    inputs = ["-i", str(input_path)]
    for p in png_paths:
        inputs += ["-i", str(p)]

    filter_parts: list[str] = []
    prev = "0:v"
    for i, el in enumerate(elementos):
        t0 = el.tiempo_inicio
        t1 = el.tiempo_inicio + el.duracion
        out_label = f"v{i}"
        filter_parts.append(
            f"[{prev}][{i + 1}:v]overlay=0:0:enable='between(t,{t0},{t1})'[{out_label}]"
        )
        prev = out_label

    filter_complex = ";".join(filter_parts)

    cmd = (
        [ffmpeg, "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", f"[{prev}]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "copy",
            str(output_path),
        ]
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg grafismo failed: {stderr.decode()[-600:]}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/aplicar")
async def aplicar_grafismos(body: GrafismoRequest) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")
    if not ffprobe:
        raise HTTPException(status_code=500, detail="ffprobe not found on PATH")

    input_path = (_STORAGE_BASE / body.video_input_key).resolve()
    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {body.video_input_key}")

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_key = f"output/grafismo/{uuid4()}.mp4"
    output_path = (_STORAGE_BASE / output_key).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        await _apply_grafismos(
            input_path, body.elementos, output_path,
            ffmpeg, ffprobe, Path(tmpdir),
        )

    return {
        "ok": True,
        "video_key": output_key,
        "video_url": f"/api/grafismo/video/{output_key}",
        "grafismos_aplicados": len(body.elementos),
    }


@router.get("/video/{key:path}")
async def stream_grafismo_video(key: str) -> Response:
    video_path = (_STORAGE_BASE / key).resolve()
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return Response(
        content=video_path.read_bytes(),
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename=\"{video_path.name}\""},
    )
