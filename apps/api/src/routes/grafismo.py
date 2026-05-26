"""Overlay de títulos y grafismos sobre vídeo al estilo Mañaneros 360 / RTVE.

Pillow renders each element as a full-frame RGBA PNG; FFmpeg composites them
in a single filter_complex pass using the overlay filter.
Output is always 25fps yuv420p H.264 CRF-20.

Layout (from bottom of frame):
  h - 90  →  h       : cintillo band (label + headline, up to 2 lines)
  h - 170 →  h - 90  : rotulo_persona / dato box (clear of cintillo)
  h * 0.52 → ...     : frase_clave (quote card, above the lower thirds)
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

router = APIRouter(prefix="/api/grafismo", tags=["grafismo"])

_OUTPUT_DIR = Path("data/storage/output/grafismo")
_STORAGE_BASE = Path("data/storage")

# ---------------------------------------------------------------------------
# Brand palette — M360 / RTVE style
# ---------------------------------------------------------------------------

_NARANJA      = (249, 115,  22, 255)   # #F97316
_NAVY_BANDA   = ( 10,  22,  40, 235)   # #0A1628 deep navy — full-width strip
_NAVY_BOX     = ( 10,  22,  40, 220)   # #0A1628 deep navy — element containers
_BLANCO       = (255, 255, 255, 255)
_BLANCO_SEC   = (203, 213, 225, 255)   # #CBD5E1 secondary / cargo text
_GRIS_CRAWL   = (148, 163, 184, 255)   # #94A3B8 ticker text

# Broadcast safe margins
_LEFT_SAFE     = 80
_RIGHT_MARGIN  = 80
_BOTTOM_MARGIN = 60

# Vertical layout constants (relative to frame bottom)
_CINTILLO_BAND_H  = 90    # height of the full-width bottom strip
_ROTULO_BOX_H     = 68    # height of the name/role box
_ROTULO_GAP       = 10    # clear space between rotulo bottom and cintillo top
# rotulo top = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H
# = h - 90 - 10 - 68 = h - 168

_FONT_BOLD_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_REGULAR_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GrafismoElemento(BaseModel):
    tipo: str                   # "titular" | "rotulo_persona" | "dato" | "pie_pagina" | "frase_clave"
    texto_principal: str
    texto_secundario: str = ""
    tiempo_inicio: float
    duracion: float
    posicion: str = "inferior"  # kept for API compatibility — layout is now type-driven


class GrafismoRequest(BaseModel):
    video_input_key: str
    elementos: list[GrafismoElemento]


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_REGULAR_CANDIDATES
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Split text into lines that fit within max_width pixels using font metrics."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        try:
            bbox = font.getbbox(test)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(test) * 13
        if text_w > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


# ---------------------------------------------------------------------------
# Element renderers
# ---------------------------------------------------------------------------

def _render_cintillo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Cintillo inferior M360: navy band + orange label box + headline (up to 2 lines)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    raw = el.texto_principal
    if " — " in raw:
        label, titulo = raw.split(" — ", 1)
    elif " - " in raw:
        label, titulo = raw.split(" - ", 1)
    else:
        label, titulo = "INFO", raw

    label  = label.strip().upper()[:20]
    titulo = titulo.strip()

    f_label  = _load_font(20, bold=True)
    f_titulo = _load_font(21, bold=True)
    f_sub    = _load_font(16, bold=False)

    # Full-width navy background band
    band_y = h - _CINTILLO_BAND_H
    draw.rectangle([0, band_y, w, h], fill=_NAVY_BANDA)

    # Orange label box
    lbbox = f_label.getbbox(label)
    lbl_w = lbbox[2] - lbbox[0] + 24
    box_x = _LEFT_SAFE
    box_y = h - 78
    box_h = 40
    draw.rectangle([box_x, box_y, box_x + lbl_w, box_y + box_h], fill=_NARANJA)
    draw.text((box_x + 12, box_y + 10), label, font=f_label, fill=_BLANCO)

    # White vertical divider
    div_x = box_x + lbl_w + 8
    draw.rectangle([div_x, box_y, div_x + 2, box_y + box_h], fill=(255, 255, 255, 160))

    # Headline — wrap into up to 2 lines
    text_x = div_x + 14
    max_text_w = w - text_x - _RIGHT_MARGIN
    lines = _wrap_text(titulo, f_titulo, max_text_w)
    line_spacing = 22
    for i, line in enumerate(lines[:2]):
        draw.text((text_x, box_y + 5 + i * line_spacing), line, font=f_titulo, fill=_BLANCO)

    # Optional subtitle
    if el.texto_secundario:
        draw.text((box_x, h - 20), el.texto_secundario.strip()[:90], font=f_sub, fill=_BLANCO_SEC)

    return img


def _render_lower_third(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Lower third M360: navy box above the cintillo zone + orange top accent + name + role."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    nombre = el.texto_principal.strip()[:42]
    cargo  = el.texto_secundario.strip()[:58] if el.texto_secundario else ""

    f_nombre = _load_font(26, bold=True)
    f_cargo  = _load_font(17, bold=False)

    box_x = _LEFT_SAFE
    box_y = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H  # h - 168
    box_w = 540
    box_h = _ROTULO_BOX_H  # 68px

    # Navy background
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NAVY_BOX)

    # Orange accent line across the TOP of the box
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + 3], fill=_NARANJA)

    # Name
    draw.text((box_x + 16, box_y + 8), nombre, font=f_nombre, fill=_BLANCO)

    # Role / cargo in orange
    if cargo:
        draw.text((box_x + 16, box_y + 40), cargo, font=f_cargo, fill=_NARANJA)

    return img


def _render_dato(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Data lower-third: navy box above cintillo zone, figure in orange + description."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    dato = el.texto_principal.strip()[:30]
    desc = el.texto_secundario.strip()[:50] if el.texto_secundario else ""

    f_dato = _load_font(34, bold=True)
    f_desc = _load_font(17, bold=False)

    box_x = _LEFT_SAFE
    box_y = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H  # same row as rotulo
    box_w = 500
    box_h = _ROTULO_BOX_H

    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NAVY_BOX)
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + 3], fill=_NARANJA)

    # Figure in orange
    draw.text((box_x + 16, box_y + 10), dato, font=f_dato, fill=_NARANJA)

    # Description to the right of the figure
    if desc:
        try:
            dato_w = f_dato.getbbox(dato)[2] - f_dato.getbbox(dato)[0]
        except Exception:
            dato_w = len(dato) * 20
        draw.text((box_x + 16 + dato_w + 14, box_y + 24), desc, font=f_desc, fill=_BLANCO)

    return img


def _render_frase_clave(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Quote card: navy box in the center-lower zone, above the lower thirds.

    Positioned at ~52% of frame height so it never clashes with cintillo
    (bottom 90px) or rotulo_persona (h-168 to h-100).
    """
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    texto = el.texto_principal.strip()
    f_quote = _load_font(30, bold=True)

    box_w = int(w * 0.60)
    lines = _wrap_text(texto, f_quote, box_w - 50)  # 50px for bar + padding

    line_h    = 38
    pad_v     = 16
    pad_h     = 18
    bar_w     = 5
    box_h     = len(lines) * line_h + pad_v * 2

    box_x = _LEFT_SAFE
    # Anchor bottom of quote just above the rotulo_persona top, with 8px gap.
    # rotulo top = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H = h - 168
    frase_bottom = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H - 8
    box_y = frase_bottom - box_h

    # Navy background
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NAVY_BOX)

    # Orange left accent bar
    draw.rectangle([box_x, box_y, box_x + bar_w, box_y + box_h], fill=_NARANJA)

    # Quote lines
    for i, line in enumerate(lines):
        draw.text(
            (box_x + bar_w + pad_h, box_y + pad_v + i * line_h),
            line,
            font=f_quote,
            fill=_BLANCO,
        )

    return img


def _render_crawl(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Static ticker — rendered as a fixed strip just above the cintillo band."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    texto = el.texto_principal.strip()
    f = _load_font(14, bold=False)

    strip_y = h - _CINTILLO_BAND_H - 22
    draw.rectangle([0, strip_y, w, strip_y + 22], fill=(0, 0, 0, 210))
    draw.text((12, strip_y + 4), texto, font=f, fill=_GRIS_CRAWL)

    return img


def _render_mosca(w: int, h: int) -> Image.Image:
    """Program logo watermark — top-right corner, always on top."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    f = _load_font(28, bold=True)
    text = "360*"
    tbbox = f.getbbox(text)
    text_w = tbbox[2] - tbbox[0]
    text_h = tbbox[3] - tbbox[1]

    x = w - text_w - _RIGHT_MARGIN
    y = 40  # top-right, inside safe zone

    draw.text((x + 2, y + 2), text, font=f, fill=(0, 0, 0, 130))
    draw.text((x, y), text, font=f, fill=_BLANCO)

    return img


def _render_element(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    if el.tipo == "titular":
        return _render_cintillo(el, w, h)
    if el.tipo == "rotulo_persona":
        return _render_lower_third(el, w, h)
    if el.tipo == "dato":
        return _render_dato(el, w, h)
    if el.tipo == "pie_pagina":
        return _render_crawl(el, w, h)
    if el.tipo == "frase_clave":
        return _render_frase_clave(el, w, h)
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core apply function
# ---------------------------------------------------------------------------

async def _apply_grafismos(
    input_path: Path,
    elementos: list[GrafismoElemento],
    output_path: Path,
    ffmpeg: str,
    ffprobe: str,
    tmpdir: Path,
) -> None:
    w, h = await _get_video_dimensions(ffprobe, input_path)
    loop = asyncio.get_running_loop()

    # 1. Render each element as RGBA PNG at video resolution
    rendered: list[tuple[GrafismoElemento, Path]] = []
    for i, el in enumerate(elementos):
        png = tmpdir / f"el_{i:03d}.png"
        img = await loop.run_in_executor(None, _render_element, el, w, h)
        img.save(str(png), "PNG")
        rendered.append((el, png))

    # 2. Mosca always on top
    mosca_png = tmpdir / "mosca.png"
    mosca_img = await loop.run_in_executor(None, _render_mosca, w, h)
    mosca_img.save(str(mosca_png), "PNG")

    # 3. Build filter_complex
    filter_parts: list[str] = []
    prev = "0:v"
    for i, (el, _) in enumerate(rendered):
        t0 = el.tiempo_inicio
        t1 = t0 + el.duracion
        out = f"v{i + 1}"
        filter_parts.append(
            f"[{prev}][{i + 1}:v]overlay=0:0:enable='between(t,{t0},{t1})'[{out}]"
        )
        prev = out

    mosca_idx = len(rendered) + 1
    filter_parts.append(f"[{prev}][{mosca_idx}:v]overlay=0:0[vout]")
    filter_complex = ";".join(filter_parts)

    # 4. FFmpeg command
    inputs: list[str] = ["-i", str(input_path)]
    for _, png in rendered:
        inputs += ["-i", str(png)]
    inputs += ["-i", str(mosca_png)]

    cmd = (
        [ffmpeg, "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "0:a?",
            "-r", "25", "-fps_mode", "cfr", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
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
        raise RuntimeError(f"ffmpeg grafismo failed: {stderr.decode()[-800:]}")


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
        "fps_salida": 25,
        "motor": "pillow+overlay",
    }


@router.get("/video/{key:path}")
async def stream_grafismo_video(key: str) -> FileResponse:
    video_path = (_STORAGE_BASE / key).resolve()
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(video_path), media_type="video/mp4", filename=video_path.name)
