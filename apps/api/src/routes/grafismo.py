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
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

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

# Reference broadcaster palette (approx; sample exact hex from the reference image)
_ROJO_TAG      = (209,  46,  46, 255)   # #d12e2e  ÚLTIMA HORA tag
_NEGRO_BANDA   = ( 17,  17,  17, 255)   # #111111  title band bg
_TEXTO_PARRAFO = ( 26,  26,  26, 255)   # #1a1a1a  paragraph text
_AZUL_CARGO    = ( 31, 111, 178, 255)   # #1f6fb2  default role bar
_GAP           = 6                      # gap between cintillo blocks

# Broadcast safe margins
_LEFT_SAFE     = 80
_RIGHT_MARGIN  = 80
_BOTTOM_MARGIN = 60

# Vertical layout constants (relative to frame bottom)
_CINTILLO_BAND_H  = 90    # height of the full-width bottom strip
_ROTULO_BOX_H     = 68    # height of the name/role box
_ROTULO_GAP       = 10    # clear space between rotulo bottom and cintillo top
_COMBINED_ROTULO_EXTRA = 70  # extra vertical lift for name in simultaneous mode
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


def get_overlay_timing(segment_duration: float, t_offset: float = 0.0) -> dict:
    """Return relative inicio/fin values for name, quote, and cintillo overlays.

    t_offset: cumulative start of this segment in the final video.
    For in-segment (inicio_relativo) use, call with t_offset=0.
    """
    name_in  = t_offset + 1.5
    name_out = t_offset + 8.5

    if segment_duration >= 14:
        quote_in  = t_offset + 9.0
        quote_out = t_offset + min(segment_duration - 1.0, 14.0)
    else:
        # Short segment: name and quote shown simultaneously
        name_in   = t_offset + 1.5
        name_out  = t_offset + max(t_offset + 2.0, segment_duration - 0.5)
        quote_in  = name_in
        quote_out = name_out

    return {
        "name_in":        name_in,
        "name_out":       name_out,
        "quote_in":       quote_in,
        "quote_out":      quote_out,
        "name_enable":    f"between(t,{name_in:.2f},{name_out:.2f})",
        "role_enable":    f"between(t,{name_in:.2f},{name_out:.2f})",
        "quote_enable":   f"between(t,{quote_in:.2f},{quote_out:.2f})",
        "cintillo_enable": "1",
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GrafismoElemento(BaseModel):
    tipo: str                   # "cintillo" | "rotulo_persona" | "directo" | "contacto" | "reloj" | "mosca" | "canal" | "dato" | "frase_clave" | "pie_pagina"
    texto_principal: str
    texto_secundario: str = ""
    tiempo_inicio: float
    duracion: float
    posicion: str = "inferior"  # legacy, ignored (layout is anchor-driven)
    obligatorio: bool = False   # cintillo (title+paragraph) is True; the rest False
    visible: bool = True        # optional elements turned off in the editor are False (skipped at render)
    ancla: str = ""             # anchor preset; "" → default anchor for the tipo
    color_barra: str = ""       # rótulo role-bar colour override (hex), "" → default
    etiqueta: str = ""          # cintillo's optional ÚLTIMA HORA tag text, "" → no tag
    anim: str = "fade"          # entrance/exit animation for the burned render


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


_SAFE_FRAC = 0.05   # title-safe margin as a fraction of width/height


def _text_w(text: str, font: ImageFont.FreeTypeFont) -> int:
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 13


def _anchor_box(ancla: str, w: int, h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Top-left (x, y) for a box of (box_w, box_h) at a named anchor preset,
    clamped so the box never leaves the title-safe rectangle."""
    sx, sy = int(w * _SAFE_FRAC), int(h * _SAFE_FRAC)
    right, bottom = w - sx, h - sy
    presets = {
        "cintillo_abajo_izq":  (sx, bottom - box_h),
        "rotulo_abajo_dcha":   (right - box_w, bottom - box_h),
        "contacto_arriba_izq": (sx, sy),
        "directo_centro":      ((w - box_w) // 2, bottom - box_h),
        "mosca_esquina_dcha":  (right - box_w, bottom - box_h),
        "reloj_esquina_dcha":  (right - box_w, bottom - box_h),
        "canal_esquina_dcha":  (right - box_w, bottom - box_h),
    }
    x, y = presets.get(ancla, (sx, bottom - box_h))
    x = max(sx, min(x, right - box_w))
    y = max(sy, min(y, bottom - box_h))
    return x, y


def _ellipsize(s: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """Trim s (by words, then by characters) until s + '…' fits max_w."""
    if _text_w(s, font) <= max_w:
        return s
    while " " in s and _text_w(s + "…", font) > max_w:
        s = s.rsplit(" ", 1)[0]
    while s and _text_w(s + "…", font) > max_w:
        s = s[:-1]
    return s + "…"


def _fit_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 2) -> list[str]:
    """Wrap to <= max_lines; ellipsize any line that overflows max_w (including a
    single word with no spaces), so no line ever exceeds max_w."""
    lines = _wrap_text(text, font, max_w)
    if len(lines) <= max_lines:
        return [ln if _text_w(ln, font) <= max_w else _ellipsize(ln, font, max_w) for ln in lines]
    return lines[: max_lines - 1] + [_ellipsize(lines[max_lines - 1], font, max_w)]


# ---------------------------------------------------------------------------
# Element renderers
# ---------------------------------------------------------------------------

def _hex_rgba(value: str, default: tuple[int, int, int, int] = _AZUL_CARGO) -> tuple[int, int, int, int]:
    s = (value or "").lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
        except ValueError:
            return default
    return default


def _render_cintillo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Stepped cintillo: optional inline red tag → black title band (white text)
    → white paragraph band (black text). Anchored bottom-left, inside safe area."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    band_w = int(w * 0.55)                       # cintillo width
    f_tag   = _load_font(max(11, int(h * 0.018)), bold=True)
    f_title = _load_font(max(15, int(h * 0.026)), bold=True)
    f_par   = _load_font(max(12, int(h * 0.020)), bold=False)
    pad = 12

    title = el.texto_principal.strip()
    par   = el.texto_secundario.strip()
    tag   = el.etiqueta.strip().upper()

    title_lines = _fit_lines(title, f_title, band_w - 2 * pad, max_lines=2)
    par_lines   = _fit_lines(par,   f_par,   band_w - 2 * pad, max_lines=2)
    line_h_t = f_title.getbbox("Ag")[3] + 6
    line_h_p = f_par.getbbox("Ag")[3] + 6
    tag_h    = (f_tag.getbbox("Ag")[3] + 10) if tag else 0
    title_h  = len(title_lines) * line_h_t + 2 * pad
    par_h    = len(par_lines) * line_h_p + 2 * pad
    total_h  = tag_h + (_GAP if tag else 0) + title_h + _GAP + par_h

    x, y = _anchor_box("cintillo_abajo_izq", w, h, band_w, total_h)
    cur_y = y

    if tag:
        tag_w = _text_w(tag, f_tag) + 18
        draw.rectangle([x, cur_y, x + tag_w, cur_y + tag_h], fill=_ROJO_TAG)
        draw.text((x + 9, cur_y + 5), tag, font=f_tag, fill=_BLANCO)
        cur_y += tag_h + _GAP

    draw.rectangle([x, cur_y, x + band_w, cur_y + title_h], fill=_NEGRO_BANDA)
    for i, line in enumerate(title_lines):
        draw.text((x + pad, cur_y + pad + i * line_h_t), line, font=f_title, fill=_BLANCO)
    cur_y += title_h + _GAP

    draw.rectangle([x, cur_y, x + band_w, cur_y + par_h], fill=_BLANCO)
    for i, line in enumerate(par_lines):
        draw.text((x + pad, cur_y + pad + i * line_h_p), line, font=f_par, fill=_TEXTO_PARRAFO)

    return img


def _render_lower_third(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Person rótulo: white bold name (with shadow) + role on a colour bar."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    nombre = el.texto_principal.strip()
    cargo  = el.texto_secundario.strip()
    bar_color = _hex_rgba(el.color_barra)

    f_nombre = _load_font(max(16, int(h * 0.030)), bold=True)
    f_cargo  = _load_font(max(11, int(h * 0.018)), bold=True)

    box_w = int(w * 0.30)
    nombre_lines = _fit_lines(nombre, f_nombre, box_w, max_lines=2)
    line_h = f_nombre.getbbox("Ag")[3] + 4
    nombre_h = len(nombre_lines) * line_h
    cargo_h = (f_cargo.getbbox("Ag")[3] + 10) if cargo else 0
    total_h = nombre_h + (6 + cargo_h if cargo else 0)

    x, y = _anchor_box(el.ancla or "rotulo_abajo_dcha", w, h, box_w, total_h)

    for i, line in enumerate(nombre_lines):
        ly = y + i * line_h
        draw.text((x + 2, ly + 2), line, font=f_nombre, fill=(0, 0, 0, 150))  # shadow
        draw.text((x, ly), line, font=f_nombre, fill=_BLANCO)

    if cargo:
        cargo = _ellipsize(cargo[:60], f_cargo, box_w - 22)
        cargo_w = _text_w(cargo, f_cargo) + 22
        cy = y + nombre_h + 6
        draw.rectangle([x, cy, x + min(cargo_w, box_w), cy + cargo_h], fill=bar_color)
        draw.text((x + 11, cy + 5), cargo, font=f_cargo, fill=_BLANCO)

    return img


def _render_lower_third_simultaneo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Name+role raised _COMBINED_ROTULO_EXTRA pixels to make room for quote below.

    Used when both rotulo_persona and frase_clave are shown at the same time
    (short segments < 14s). Text-only with drop shadow (no navy box) so the
    combined block doesn't feel too heavy.
    """
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    nombre = el.texto_principal.strip()[:42]
    cargo  = el.texto_secundario.strip()[:58] if el.texto_secundario else ""

    f_nombre = _load_font(26, bold=True)
    f_cargo  = _load_font(16, bold=False)

    # Raise box above standard rotulo zone to clear the quote block
    base_y = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H - _COMBINED_ROTULO_EXTRA

    # Drop shadow + white name text
    draw.text((_LEFT_SAFE + 2, base_y + 2), nombre, font=f_nombre, fill=(0, 0, 0, 180))
    draw.text((_LEFT_SAFE,     base_y),     nombre, font=f_nombre, fill=_BLANCO)

    # Role in #CCCCCC below name
    if cargo:
        role_y = base_y + 32
        draw.text((_LEFT_SAFE + 1, role_y + 1), cargo, font=f_cargo, fill=(0, 0, 0, 140))
        draw.text((_LEFT_SAFE,     role_y),     cargo, font=f_cargo, fill=(204, 204, 204, 255))

    # Orange separator line (width = name text width)
    sep_y = base_y + 56
    try:
        name_w = f_nombre.getbbox(nombre)[2] - f_nombre.getbbox(nombre)[0]
    except Exception:
        name_w = len(nombre) * 14
    draw.rectangle([_LEFT_SAFE, sep_y, _LEFT_SAFE + name_w, sep_y + 2], fill=_NARANJA)

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
    """Quote card at the standard lower-third zone (h-168).

    Sequential mode: appears at the same h-168 position as the name, but at
    a later time (name shows 1.5-8.5s, quote shows 9.0s+).
    Simultaneous mode: appears at h-168 while the name is raised to h-238.
    Either way the quote never overlaps with the cintillo band.
    """
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    texto   = el.texto_principal.strip()
    f_quote = _load_font(19, bold=True)   # 19px semibold per spec

    bar_w  = 3    # left orange border (spec: 3px solid #FF6600)
    pad_h  = 14   # horizontal padding
    pad_v  = 5    # vertical padding
    line_h = 26

    max_w = min(int(w * 0.60), 860)
    lines = _wrap_text(texto, f_quote, max_w - bar_w - pad_h * 2)[:2]  # max 2 lines
    box_h = len(lines) * line_h + pad_v * 2

    # Align with the top of the standard rotulo zone
    box_x = _LEFT_SAFE
    box_y = h - _CINTILLO_BAND_H - _ROTULO_GAP - _ROTULO_BOX_H   # h - 168

    # Semi-transparent dark background only behind text (rgba 0,0,0,0.68 → alpha 173)
    draw.rectangle([box_x, box_y, box_x + max_w, box_y + box_h], fill=(0, 0, 0, 173))

    # Orange left border
    draw.rectangle([box_x, box_y, box_x + bar_w, box_y + box_h], fill=_NARANJA)

    # Quote text
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


def _madrid_hhmm() -> str:
    return datetime.now(ZoneInfo("Europe/Madrid")).strftime("%H:%M")


def _render_directo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    loc = el.texto_principal.strip()
    f = _load_font(max(11, int(h * 0.018)), bold=False)
    text = f"  Directo  |  {loc}" if loc else "  Directo"
    box_w = _text_w(text, f) + 26; box_h = f.getbbox("Ag")[3] + 12
    x, y = _anchor_box(el.ancla or "directo_centro", w, h, box_w, box_h)
    draw.rectangle([x, y, x + box_w, y + box_h], fill=(8, 16, 22, 160))
    draw.ellipse([x + 9, y + box_h // 2 - 4, x + 17, y + box_h // 2 + 4], fill=(255, 65, 54, 255))
    draw.text((x + 22, y + 6), text.strip(), font=f, fill=_BLANCO)
    return img


def _render_contacto(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    phone = el.texto_principal.strip()
    f = _load_font(max(12, int(h * 0.019)), bold=True)
    box_w = _text_w(phone, f) + 44; box_h = f.getbbox("Ag")[3] + 12
    x, y = _anchor_box(el.ancla or "contacto_arriba_izq", w, h, box_w, box_h)
    draw.rectangle([x, y, x + box_w, y + box_h], fill=(8, 16, 22, 210))
    cy = y + box_h // 2
    draw.ellipse([x + 8, cy - 8, x + 24, cy + 8], fill=(37, 211, 102, 255))
    draw.text((x + 32, y + 6), phone, font=f, fill=_BLANCO)
    return img


def _render_reloj(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    f = _load_font(max(13, int(h * 0.022)), bold=True)
    text = _madrid_hhmm()
    box_w = _text_w(text, f) + 18; box_h = f.getbbox("Ag")[3] + 10
    x, y = _anchor_box(el.ancla or "reloj_esquina_dcha", w, h, box_w, box_h)
    draw.rectangle([x, y, x + box_w, y + box_h], fill=(0, 0, 0, 255))
    draw.text((x + 9, y + 5), text, font=f, fill=_BLANCO)
    return img


def _render_mosca(w: int, h: int) -> Image.Image:
    """Program mark '360' with a filled 0."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    f = _load_font(max(18, int(h * 0.030)), bold=True)
    text = "36"
    tw = _text_w(text, f); th = f.getbbox("Ag")[3]
    disc = int(th * 0.82)
    box_w = tw + 4 + disc; box_h = th + 6
    x, y = _anchor_box("mosca_esquina_dcha", w, h, box_w, box_h)
    draw.text((x + 2, y + 2), text, font=f, fill=(0, 0, 0, 130))
    draw.text((x, y), text, font=f, fill=_BLANCO)
    dx = x + tw + 4; dy = y + (th - disc) // 2
    draw.ellipse([dx, dy, dx + disc, dy + disc], fill=_BLANCO)
    return img


def _render_canal(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Channel mark 'La 1' (rendered as a bold '1')."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    f = _load_font(max(24, int(h * 0.044)), bold=True)
    text = "1"
    box_w = _text_w(text, f) + 6; box_h = f.getbbox("Ag")[3] + 6
    x, y = _anchor_box(el.ancla or "canal_esquina_dcha", w, h, box_w, box_h)
    draw.text((x + 2, y + 2), text, font=f, fill=(0, 0, 0, 130))
    draw.text((x, y), text, font=f, fill=_BLANCO)
    return img


def _render_element(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    if el.tipo in ("cintillo", "titular"):
        return _render_cintillo(el, w, h)
    if el.tipo == "rotulo_persona":
        return _render_lower_third(el, w, h)
    if el.tipo == "rotulo_persona_simultaneo":
        return _render_lower_third_simultaneo(el, w, h)
    if el.tipo == "dato":
        return _render_dato(el, w, h)
    if el.tipo == "pie_pagina":
        return _render_crawl(el, w, h)
    if el.tipo == "frase_clave":
        return _render_frase_clave(el, w, h)
    if el.tipo == "directo":
        return _render_directo(el, w, h)
    if el.tipo == "contacto":
        return _render_contacto(el, w, h)
    if el.tipo == "reloj":
        return _render_reloj(el, w, h)
    if el.tipo == "mosca":
        return _render_mosca(w, h)
    if el.tipo == "canal":
        return _render_canal(el, w, h)
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


_FADE_S = 0.4   # entrance/exit fade duration (seconds)


def _build_overlay_filter(elementos: list[GrafismoElemento]) -> tuple[list[int], str]:
    """Build the filter_complex for the visible elements. Each element's PNG input
    is looped to span [0, t1]; its alpha fades in at t0 and out ending at t1, so the
    overlay needs NO enable= clause (alpha gates visibility, and the input's t-clock
    equals the output t-clock since both start at 0). Returns (visible_indices, filter).
    Input numbering: [0:v] is the base video; visible element k is input [k+1:v].
    """
    visible = [i for i, el in enumerate(elementos) if el.visible]
    parts: list[str] = []
    prev = "0:v"
    for k, idx in enumerate(visible):
        el = elementos[idx]
        t0 = el.tiempo_inicio
        t1 = t0 + el.duracion
        fade = min(_FADE_S, max(0.05, el.duracion / 2))
        parts.append(
            f"[{k + 1}:v]format=yuva420p,"
            f"fade=t=in:st={t0:.2f}:d={fade:.2f}:alpha=1,"
            f"fade=t=out:st={t1 - fade:.2f}:d={fade:.2f}:alpha=1[g{k}]"
        )
        out = f"v{k + 1}"
        parts.append(f"[{prev}][g{k}]overlay=0:0[{out}]")
        prev = out
    parts.append(f"[{prev}]copy[vout]")
    return visible, ";".join(parts)


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

    visible, filter_complex = _build_overlay_filter(elementos)

    # Render each VISIBLE element as a PNG (in input order)
    rendered: list[Path] = []
    for k, idx in enumerate(visible):
        png = tmpdir / f"el_{k:03d}.png"
        img = await loop.run_in_executor(None, _render_element, elementos[idx], w, h)
        img.save(str(png), "PNG")
        rendered.append(png)

    inputs: list[str] = ["-i", str(input_path)]
    for k, idx in enumerate(visible):
        el = elementos[idx]
        # Loop the static PNG from 0 to t1 so the alpha fade ramps at absolute
        # times; after t1 the input EOFs (overlay eof_action=repeat keeps the
        # final, fully-transparent frame → invisible).
        t1 = el.tiempo_inicio + el.duracion
        inputs += ["-loop", "1", "-t", f"{t1:.2f}", "-i", str(rendered[k])]

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
