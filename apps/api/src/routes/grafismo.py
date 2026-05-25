"""Overlay de títulos y grafismos sobre vídeo al estilo Mañaneros 360 / RTVE.

Pillow renders each element as a full-frame RGBA PNG; FFmpeg composites them
in a single filter_complex pass using the overlay filter (no drawtext needed,
so this works even when FFmpeg lacks --enable-libfreetype).
Output is always 25fps yuv420p H.264 CRF-20.
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

# M360 brand colours (RGBA)
_NARANJA      = (249, 115,  22, 255)   # #F97316
_NEGRO_BANDA  = (  0,   0,   0, 200)   # full-width background strip
_NEGRO_BOX    = (  0,   0,   0, 215)   # element containers
_BLANCO       = (255, 255, 255, 255)
_BLANCO_SEC   = (203, 213, 225, 255)   # #CBD5E1 secondary text
_GRIS_CRAWL   = (148, 163, 184, 255)   # #94A3B8

# Broadcast safe areas
_LEFT_SAFE    = 80
_RIGHT_MARGIN = 80
_BOTTOM_MARGIN = 60

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
    posicion: str = "inferior"  # kept for API compatibility


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


# ---------------------------------------------------------------------------
# Element renderers — each returns a full-frame RGBA Image (transparent base)
# ---------------------------------------------------------------------------

def _render_cintillo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Cintillo inferior M360: dark band + orange label box + headline text."""
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
    titulo = titulo.strip()[:65]

    f_label  = _load_font(20, bold=True)
    f_titulo = _load_font(24, bold=True)
    f_sub    = _load_font(17, bold=False)

    # Full-width dark background band (bottom 112px)
    band_y = h - 112
    draw.rectangle([0, band_y, w, h], fill=_NEGRO_BANDA)

    # Orange label box
    lbbox  = f_label.getbbox(label)
    lbl_w  = lbbox[2] - lbbox[0] + 24
    box_x  = _LEFT_SAFE
    box_y  = h - 98
    box_h  = 40
    draw.rectangle([box_x, box_y, box_x + lbl_w, box_y + box_h], fill=_NARANJA)
    draw.text((box_x + 12, box_y + 10), label, font=f_label, fill=_BLANCO)

    # White vertical divider
    div_x = box_x + lbl_w + 8
    draw.rectangle([div_x, box_y, div_x + 2, box_y + box_h], fill=(255, 255, 255, 210))

    # Headline text
    draw.text((div_x + 14, box_y + 8), titulo, font=f_titulo, fill=_BLANCO)

    # Optional subtitle / secondary text
    if el.texto_secundario:
        sub = el.texto_secundario.strip()[:80]
        draw.text((box_x, h - 52), sub, font=f_sub, fill=_BLANCO_SEC)

    return img


def _render_lower_third(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Lower third M360: dark box + orange accent line + name + role."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    nombre = el.texto_principal.strip()[:40]
    cargo  = el.texto_secundario.strip()[:55] if el.texto_secundario else ""

    f_nombre = _load_font(26, bold=True)
    f_cargo  = _load_font(18, bold=False)

    box_x, box_y = _LEFT_SAFE, h - 118
    box_w, box_h = 520, 72

    # Dark background
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NEGRO_BOX)

    # Orange accent line above the box
    draw.rectangle([box_x, box_y - 3, box_x + box_w, box_y], fill=_NARANJA)

    # Name
    draw.text((box_x + 16, box_y + 8), nombre, font=f_nombre, fill=_BLANCO)

    # Role in orange
    if cargo:
        draw.text((box_x + 16, box_y + 40), cargo, font=f_cargo, fill=_NARANJA)

    return img


def _render_pantallon(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Pantallón centrado: dark box + large orange figure + description."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    dato = el.texto_principal.strip()
    desc = el.texto_secundario.strip()[:55] if el.texto_secundario else ""

    f_dato = _load_font(52, bold=True)
    f_desc = _load_font(22, bold=False)

    box_w, box_h = 620, 165
    box_x = (w - box_w) // 2
    box_y = (h - box_h) // 2

    # Dark container
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NEGRO_BOX)

    # Orange top accent line
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + 4], fill=_NARANJA)

    # Main figure — centred, naranja
    dbbox = f_dato.getbbox(dato)
    dato_w = dbbox[2] - dbbox[0]
    draw.text((box_x + (box_w - dato_w) // 2, box_y + 18), dato, font=f_dato, fill=_NARANJA)

    # Description — centred, white
    if desc:
        sbbox = f_desc.getbbox(desc)
        desc_w = sbbox[2] - sbbox[0]
        draw.text((box_x + (box_w - desc_w) // 2, box_y + 118), desc, font=f_desc, fill=_BLANCO)

    return img


def _render_crawl(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Static ticker banner — Pillow can't animate, renders as a fixed strip."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    texto = el.texto_principal.strip()
    f = _load_font(14, bold=False)

    strip_y = h - 26
    draw.rectangle([0, strip_y, w, h], fill=(0, 0, 0, 232))
    draw.text((12, strip_y + 4), texto, font=f, fill=_GRIS_CRAWL)

    return img


def _render_frase_clave(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Right-side highlight card: dark box + orange accent bar + large quote text.

    Appears in the upper-right quadrant so it never clashes with the lower-third
    person nameplate or the bottom cintillo band.
    """
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    texto = el.texto_principal.strip()

    # Word-wrap: max ~22 chars per line so text stays readable at broadcast size
    words = texto.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        if len(test) > 22 and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    f_quote = _load_font(32, bold=True)

    line_h = 42
    padding_v = 20
    padding_h = 20
    bar_w = 6
    box_w = int(w * 0.37)
    box_h = len(lines) * line_h + padding_v * 2
    box_x = w - box_w - _RIGHT_MARGIN
    box_y = int(h * 0.10)  # upper area, clear of both top safe zone and lower thirds

    # Semi-transparent dark background
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=_NEGRO_BOX)

    # Orange vertical accent bar on the left edge
    draw.rectangle([box_x, box_y, box_x + bar_w, box_y + box_h], fill=_NARANJA)

    # Quote text lines
    for i, line in enumerate(lines):
        draw.text(
            (box_x + bar_w + padding_h, box_y + padding_v + i * line_h),
            line,
            font=f_quote,
            fill=_BLANCO,
        )

    return img


def _render_mosca(w: int, h: int) -> Image.Image:
    """Program logo watermark — always present, top of the graphic stack."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    f = _load_font(30, bold=True)
    text = "360*"
    tbbox = f.getbbox(text)
    text_w = tbbox[2] - tbbox[0]
    text_h = tbbox[3] - tbbox[1]

    x = w - text_w - _RIGHT_MARGIN
    y = h - text_h - _BOTTOM_MARGIN

    # Drop shadow
    draw.text((x + 2, y + 2), text, font=f, fill=(0, 0, 0, 140))
    draw.text((x, y), text, font=f, fill=_BLANCO)

    return img


def _render_element(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    if el.tipo == "titular":
        return _render_cintillo(el, w, h)
    if el.tipo == "rotulo_persona":
        return _render_lower_third(el, w, h)
    if el.tipo == "dato":
        return _render_pantallon(el, w, h)
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
# Core apply function — called from this module and from generar_pieza.py
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

    # 1. Render each element as RGBA PNG
    rendered: list[tuple[GrafismoElemento, Path]] = []
    for i, el in enumerate(elementos):
        png = tmpdir / f"el_{i:03d}.png"
        img = await loop.run_in_executor(None, _render_element, el, w, h)
        img.save(str(png), "PNG")
        rendered.append((el, png))

    # 2. Render mosca (always visible, always on top)
    mosca_png = tmpdir / "mosca.png"
    mosca_img = await loop.run_in_executor(None, _render_mosca, w, h)
    mosca_img.save(str(mosca_png), "PNG")

    # 3. Build filter_complex — chain overlays, mosca last
    # Input indices: 0=video, 1..N=elements, N+1=mosca
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

    # 4. Assemble FFmpeg command
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
