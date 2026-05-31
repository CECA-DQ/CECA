# Editable/animated grafismos (backend) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle grafismos to the broadcaster reference look (stepped cintillo, person rótulo, optional directo/contact/360/clock/La 1), driven by an editable plan, with anchor-preset positioning, title-safe clamping, text-fit, and a fade in/out burned into the exported MP4.

**Architecture:** Keep the existing Pillow-PNG + FFmpeg-overlay pipeline in `src/routes/grafismo.py`. Add fields to `GrafismoElemento` (`obligatorio`/`visible`/`ancla`/`color_barra`/`etiqueta`/`anim`), a resolution-relative **anchor** + **text-fit** helper layer, restyled/new `_render_*` functions, and a fade pass in `_apply_grafismos`. The plan builders (`narrative_timeline._scored_segments_to_plan`, `generar_pieza._plan_to_grafismos`) emit/propagate the new fields. The front editor consumes the same plan via `/preview-timeline` and exports through `/api/grafismo/aplicar`.

**Tech Stack:** Python 3.12, Pillow, FFmpeg, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-05-31-grafismos-editables-design.md`

**Setup for every test run:** `cd apps/api` first; venv is **Python 3.12**. Pure unit tests — no rendering/network. Pillow is already a dependency (`PIL` imported in `grafismo.py`).

**File map:**
- Modify `apps/api/src/routes/grafismo.py` — schema, palette, anchor/text-fit helpers, renderers, `_apply_grafismos`, `_render_element`.
- Modify `apps/api/src/services/narrative_timeline.py` — `_scored_segments_to_plan` (composed cintillo + obligatorio/ancla/optionals).
- Modify `apps/api/src/routes/generar_pieza.py` — `_plan_to_grafismos` (propagate new fields) + pieza-emision (paragraph text).
- Create `apps/api/tests/unit/routes/test_grafismo_render.py` and `apps/api/tests/unit/services/test_grafismo_plan.py`.

---

## Task 1: Extend `GrafismoElemento` with the editable fields

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (the `GrafismoElemento` model, ~lines 103-109)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (create)

- [ ] **Step 1: Write the failing test** — create `apps/api/tests/unit/routes/test_grafismo_render.py`:

```python
"""Grafismo data model, anchor/text-fit helpers, renderers, and fade command."""

from src.routes.grafismo import GrafismoElemento


def test_grafismo_elemento_new_fields_defaults():
    el = GrafismoElemento(tipo="cintillo", texto_principal="T", tiempo_inicio=0.0, duracion=5.0)
    assert el.obligatorio is False
    assert el.visible is True
    assert el.ancla == ""
    assert el.color_barra == ""
    assert el.etiqueta == ""
    assert el.anim == "fade"
    # legacy field still present, ignored
    assert el.posicion == "inferior"


def test_grafismo_elemento_accepts_overrides():
    el = GrafismoElemento(
        tipo="cintillo", texto_principal="Título", texto_secundario="Párrafo",
        tiempo_inicio=0.0, duracion=5.0,
        obligatorio=True, visible=False, ancla="cintillo_abajo_izq",
        color_barra="#1f6fb2", etiqueta="ÚLTIMA HORA", anim="fade",
    )
    assert el.obligatorio and not el.visible
    assert el.ancla == "cintillo_abajo_izq"
    assert el.etiqueta == "ÚLTIMA HORA"
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -v`
  Expected: FAIL — `GrafismoElemento` rejects the unknown keyword fields / attributes missing.

- [ ] **Step 3: Extend the model** — in `grafismo.py`, replace the `GrafismoElemento` class:

```python
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
```

- [ ] **Step 4: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -v`
  Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): editable fields on GrafismoElemento (obligatorio/visible/ancla/color_barra/etiqueta/anim)"
```

---

## Task 2: Anchor presets, safe-area clamp, and text-fit helpers (pure)

These pure helpers are the foundation: they guarantee nothing leaves the title-safe area and long text never overflows.

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (add helpers after `_wrap_text`, ~line 152)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (append)

- [ ] **Step 1: Write the failing tests** — append:

```python
from src.routes.grafismo import _anchor_box, _fit_lines, _load_font, _SAFE_FRAC


def test_anchor_box_stays_in_safe_area_for_every_preset():
    w, h = 1280, 720
    sx, sy = int(w * _SAFE_FRAC), int(h * _SAFE_FRAC)
    for ancla in [
        "cintillo_abajo_izq", "rotulo_abajo_dcha", "contacto_arriba_izq",
        "directo_centro", "mosca_esquina_dcha", "reloj_esquina_dcha", "canal_esquina_dcha",
    ]:
        x, y = _anchor_box(ancla, w, h, box_w=300, box_h=80)
        assert x >= sx and y >= sy
        assert x + 300 <= w - sx
        assert y + 80 <= h - sy


def test_anchor_box_clamps_oversized_box_into_safe_area():
    # A box wider than the safe area is clamped to the left-safe edge, never negative.
    w, h = 1280, 720
    sx = int(w * _SAFE_FRAC)
    x, y = _anchor_box("rotulo_abajo_dcha", w, h, box_w=5000, box_h=80)
    assert x == sx


def test_fit_lines_keeps_short_text():
    font = _load_font(18, bold=True)
    assert _fit_lines("Hola mundo", font, max_w=400, max_lines=2) == ["Hola mundo"]


def test_fit_lines_clamps_and_ellipsizes_overflow():
    font = _load_font(18, bold=True)
    long = "palabra " * 60
    out = _fit_lines(long.strip(), font, max_w=300, max_lines=2)
    assert len(out) == 2
    assert out[-1].endswith("…")
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -v`
  Expected: FAIL — `_anchor_box`/`_fit_lines`/`_SAFE_FRAC` not importable.

- [ ] **Step 3: Implement the helpers** — in `grafismo.py`, after `_wrap_text` (~line 152) add:

```python
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


def _fit_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines: int = 2) -> list[str]:
    """Wrap to <= max_lines; if it overflows, truncate the last kept line with '…'."""
    lines = _wrap_text(text, font, max_w)
    if len(lines) <= max_lines:
        return lines
    last = lines[max_lines - 1]
    while last and _text_w(last + "…", font) > max_w and " " in last:
        last = last.rsplit(" ", 1)[0]
    return lines[: max_lines - 1] + [last + "…"]
```

- [ ] **Step 4: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -v`
  Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): anchor presets + safe-area clamp + text-fit helpers"
```

---

## Task 3: Restyle the cintillo as a composed stepped element

Red inline `etiqueta` tag (optional) → black title band (white text) → white paragraph band (black text), with a small gap, anchored bottom-left, all inside the safe area.

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (add palette constants near line 38; rewrite `_render_cintillo`, ~158-207)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (append)

- [ ] **Step 1: Write the failing test** — append:

```python
from src.routes.grafismo import _render_cintillo


def test_cintillo_renders_full_frame_rgba():
    el = GrafismoElemento(
        tipo="cintillo", texto_principal="El PP carga contra el Gobierno",
        texto_secundario="Arremeten también contra Yolanda Díaz por el rescate a Plus Ultra",
        tiempo_inicio=0.0, duracion=6.0, etiqueta="ÚLTIMA HORA", obligatorio=True,
    )
    img = _render_cintillo(el, 1280, 720)
    assert img.size == (1280, 720)
    assert img.mode == "RGBA"


def test_cintillo_extreme_text_keeps_painted_pixels_in_safe_area():
    el = GrafismoElemento(
        tipo="cintillo",
        texto_principal="Titular larguísimo " * 12,
        texto_secundario="Subtítulo larguísimo que se repite muchas veces " * 6,
        tiempo_inicio=0.0, duracion=6.0, etiqueta="ÚLTIMA HORA",
    )
    img = _render_cintillo(el, 1280, 720)
    bbox = img.getbbox()  # bounding box of non-transparent pixels
    assert bbox is not None
    sx, sy = int(1280 * 0.05), int(720 * 0.05)
    left, top, right, bottom = bbox
    assert left >= sx - 1 and right <= 1280 - sx + 1
    assert bottom <= 720 - sy + 1
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k cintillo -v`
  Expected: FAIL — the current `_render_cintillo` paints a full-width navy band to `h` (bottom=720, outside safe area), so the safe-area assertion fails; and there is no `etiqueta` handling.

- [ ] **Step 3: Add palette constants** — in `grafismo.py` after the existing palette (~line 38) add:

```python
# Reference broadcaster palette (approx; sample exact hex from the reference image)
_ROJO_TAG     = (209,  46,  46, 255)   # #d12e2e  ÚLTIMA HORA tag
_NEGRO_BANDA  = ( 17,  17,  17, 255)   # #111111  title band bg
_TEXTO_PARRAFO = (26,  26,  26, 255)   # #1a1a1a  paragraph text
_AZUL_CARGO   = ( 31, 111, 178, 255)   # #1f6fb2  default role bar
_GAP          = 6                      # gap between cintillo blocks
```

- [ ] **Step 4: Rewrite `_render_cintillo`** — replace the whole function:

```python
def _render_cintillo(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    """Stepped cintillo: optional inline red tag → black title band (white text)
    → white paragraph band (black text). Anchored bottom-left, inside safe area."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    sx = int(w * _SAFE_FRAC)
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
```

- [ ] **Step 5: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k cintillo -v`
  Expected: PASS (2 passed). Then run the whole file: `uv run pytest tests/unit/routes/test_grafismo_render.py -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): stepped cintillo (red tag + black title + white paragraph), safe-area clamped"
```

---

## Task 4: Restyle the person rótulo (white name + colored role bar)

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (rewrite `_render_lower_third`, ~210-236; add a hex parser)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (append)

- [ ] **Step 1: Write the failing test** — append:

```python
from src.routes.grafismo import _render_lower_third, _hex_rgba, _AZUL_CARGO


def test_hex_rgba_parses_and_defaults():
    assert _hex_rgba("#1f6fb2") == (31, 111, 178, 255)
    assert _hex_rgba("") == _AZUL_CARGO          # default when empty
    assert _hex_rgba("garbage") == _AZUL_CARGO   # default on bad input


def test_rotulo_renders_in_safe_area():
    el = GrafismoElemento(
        tipo="rotulo_persona", texto_principal="Enrique Santiago",
        texto_secundario="Portavoz Parlamentario IU", tiempo_inicio=0.0, duracion=6.0,
        ancla="rotulo_abajo_dcha",
    )
    img = _render_lower_third(el, 1280, 720)
    assert img.size == (1280, 720) and img.mode == "RGBA"
    bbox = img.getbbox()
    sx, sy = int(1280 * 0.05), int(720 * 0.05)
    assert bbox and bbox[0] >= sx - 1 and bbox[2] <= 1280 - sx + 1 and bbox[3] <= 720 - sy + 1
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k "hex_rgba or rotulo" -v`
  Expected: FAIL — `_hex_rgba` missing; the current rótulo anchors bottom-left with a navy box (not safe-clamped right).

- [ ] **Step 3: Add `_hex_rgba` + rewrite `_render_lower_third`** — in `grafismo.py`:

```python
def _hex_rgba(value: str, default: tuple = _AZUL_CARGO) -> tuple:
    s = (value or "").lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
        except ValueError:
            return default
    return default
```

```python
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
        cargo = cargo[:60]
        cargo_w = _text_w(cargo, f_cargo) + 22
        cy = y + nombre_h + 6
        draw.rectangle([x, cy, x + min(cargo_w, box_w), cy + cargo_h], fill=bar_color)
        draw.text((x + 11, cy + 5), cargo, font=f_cargo, fill=_BLANCO)

    return img
```

- [ ] **Step 4: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k "hex_rgba or rotulo" -v`
  Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): restyle person rótulo (white name + configurable role bar)"
```

---

## Task 5: New renderers — directo, contacto, reloj (Madrid), mosca 360, canal La 1

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (add renderers; update `_render_element` dispatch, ~392-405)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (append)

- [ ] **Step 1: Write the failing tests** — append:

```python
import re
from src.routes.grafismo import (
    _render_element, _madrid_hhmm,
)


def test_madrid_hhmm_format():
    assert re.fullmatch(r"\d{2}:\d{2}", _madrid_hhmm())


def test_new_renderers_dispatch_and_stay_in_safe_area():
    cases = [
        ("directo", "Sede del PSOE, Madrid", "directo_centro"),
        ("contacto", "610 793 793", "contacto_arriba_izq"),
        ("reloj", "", "reloj_esquina_dcha"),
        ("mosca", "", "mosca_esquina_dcha"),
        ("canal", "", "canal_esquina_dcha"),
    ]
    sx, sy = int(1280 * 0.05), int(720 * 0.05)
    for tipo, txt, ancla in cases:
        el = GrafismoElemento(tipo=tipo, texto_principal=txt, tiempo_inicio=0.0, duracion=5.0, ancla=ancla)
        img = _render_element(el, 1280, 720)
        assert img.size == (1280, 720) and img.mode == "RGBA"
        bbox = img.getbbox()
        assert bbox is not None, f"{tipo} rendered nothing"
        assert bbox[0] >= sx - 1 and bbox[2] <= 1280 - sx + 1
        assert bbox[1] >= sy - 1 and bbox[3] <= 720 - sy + 1
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k "madrid or new_renderers" -v`
  Expected: FAIL — `_madrid_hhmm` missing; `_render_element` returns a blank image for these tipos (`getbbox()` is None).

- [ ] **Step 3: Add the renderers + clock helper** — in `grafismo.py` (add `from datetime import datetime` and `from zoneinfo import ZoneInfo` at the top with the other imports):

```python
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
    draw.ellipse([x + 8, cy - 8, x + 24, cy + 8], fill=(37, 211, 102, 255))  # whatsapp green
    draw.text((x + 32, y + 6), phone, font=f, fill=_BLANCO)
    return img


def _render_reloj(el: GrafismoElemento, w: int, h: int) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); draw = ImageDraw.Draw(img)
    f = _load_font(max(13, int(h * 0.022)), bold=True)
    text = _madrid_hhmm()
    box_w = _text_w(text, f) + 18; box_h = f.getbbox("Ag")[3] + 10
    x, y = _anchor_box(el.ancla or "reloj_esquina_dcha", w, h, box_w, box_h)
    draw.rectangle([x, y, x + box_w, y + box_h], fill=(0, 0, 0, 255))  # black bg
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
    draw.ellipse([dx, dy, dx + disc, dy + disc], fill=_BLANCO)  # filled 0
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
```

- [ ] **Step 4: Update `_render_element` dispatch** — replace the function so `titular` aliases to the new cintillo and the new tipos route correctly:

```python
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
```

(Note: `_render_mosca` takes `(w, h)` — keep that signature; the dispatch calls it without `el`.)

- [ ] **Step 5: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -v`
  Expected: PASS (all, incl. the 2 new).

- [ ] **Step 6: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): directo/contacto/reloj(Madrid)/mosca360/canal renderers + dispatch"
```

---

## Task 6: `_apply_grafismos` — skip not-visible + fade in/out

**Files:**
- Modify: `apps/api/src/routes/grafismo.py` (`_apply_grafismos`, ~434-501; extract the filter builder for testability)
- Test: `apps/api/tests/unit/routes/test_grafismo_render.py` (append)

- [ ] **Step 1: Write the failing tests** — append:

```python
from src.routes.grafismo import _build_overlay_filter, _FADE_S


def _vis(tipo, t0, dur, visible=True):
    return GrafismoElemento(tipo=tipo, texto_principal="x", tiempo_inicio=t0, duracion=dur, visible=visible)


def test_overlay_filter_skips_not_visible():
    els = [_vis("cintillo", 0, 5), _vis("contacto", 0, 5, visible=False), _vis("reloj", 0, 5)]
    rendered_idx, filt = _build_overlay_filter(els)
    # Only the 2 visible elements are composited (indices into the rendered list)
    assert rendered_idx == [0, 2]
    assert filt.count("overlay=") == 2


def test_overlay_filter_fades_within_window():
    els = [_vis("cintillo", 2.0, 6.0)]  # t0=2.0, t1=8.0, fade=0.4
    _, filt = _build_overlay_filter(els)
    assert "fade=t=in:st=2.00:d=0.40:alpha=1" in filt
    assert "fade=t=out:st=7.60:d=0.40:alpha=1" in filt  # fade-out starts at t1 - fade
    assert "overlay=0:0[" in filt  # alpha gates visibility; no enable= needed
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k overlay_filter -v`
  Expected: FAIL — `_build_overlay_filter`/`_FADE_S` not defined.

- [ ] **Step 3: Extract `_build_overlay_filter` + add fade** — in `grafismo.py`, add a constant near the palette and the builder above `_apply_grafismos`:

```python
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
```

Then rewrite the body of `_apply_grafismos` to render only visible elements and loop each PNG to its own `t1`. Replace the section from `# 1. Render each element` through the `cmd = (...)` block (including the always-on mosca block — mosca/clock/canal now arrive through `elementos`) with:

```python
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
```

- [ ] **Step 4: Run to verify it passes** — `cd apps/api && uv run pytest tests/unit/routes/test_grafismo_render.py -k overlay_filter -v`
  Expected: PASS (2 passed). Then full file → PASS.

- [ ] **Step 5: Verify the module still imports** — `cd apps/api && uv run python -c "import src.routes.grafismo"` → exit 0.

- [ ] **Step 6: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/grafismo.py apps/api/tests/unit/routes/test_grafismo_render.py
git commit -m "feat(grafismo): skip not-visible elements + fade in/out in the burned render"
```

---

## Task 7: Plan generation — composed cintillo + obligatorio/ancla + propagate fields

**Files:**
- Modify: `apps/api/src/services/narrative_timeline.py` (`_scored_segments_to_plan`, ~542-617)
- Modify: `apps/api/src/routes/generar_pieza.py` (`_plan_to_grafismos`, ~564-591)
- Test: `apps/api/tests/unit/services/test_grafismo_plan.py` (create)

- [ ] **Step 1: Write the failing tests** — create `apps/api/tests/unit/services/test_grafismo_plan.py`:

```python
"""Plan generation: mandatory cintillo + new grafismo fields propagate."""

from src.services.narrative_timeline import _scored_segments_to_plan


def _seg(t_start, t_end, hablante="plano_sala"):
    return {"t_start": t_start, "t_end": t_end, "max_score": 6,
            "hablante": hablante, "cargo": "", "razon": "", "fuente_index": 0}


def test_cola_first_segment_has_mandatory_cintillo_with_title_and_paragraph():
    segs = [_seg(0, 5), _seg(10, 15)]
    plan = _scored_segments_to_plan(
        segs, cintillo_label="ÚLTIMA HORA", titular="El PP carga contra el Gobierno",
        tipo_pieza="cola",
    )
    grafismos = plan["segmentos"][0]["grafismos"]
    cintillo = next(g for g in grafismos if g["tipo"] == "titular")  # rendered as stepped cintillo
    assert cintillo["obligatorio"] is True
    assert cintillo["texto_principal"]            # title present
    assert cintillo["ancla"] == "cintillo_abajo_izq"


def test_rotulo_carries_ancla_when_speaker_identified():
    # nota assigns: i==0 intro, i==n-1 cierre, middle declaracion → needs 3 segments
    # so the middle one is a declaracion that can carry a rótulo.
    segs = [_seg(0, 5), _seg(10, 18, hablante="Enrique Santiago"), _seg(20, 25)]
    plan = _scored_segments_to_plan(
        segs, cintillo_label="POLÍTICA", titular="T", tipo_pieza="nota",
    )
    rotulos = [g for s in plan["segmentos"] for g in s["grafismos"] if g["tipo"] == "rotulo_persona"]
    assert rotulos and rotulos[0]["ancla"] == "rotulo_abajo_dcha"
    assert rotulos[0]["obligatorio"] is False
```

- [ ] **Step 2: Run to verify it fails** — `cd apps/api && uv run pytest tests/unit/services/test_grafismo_plan.py -v`
  Expected: FAIL — current plan emits `tipo:"titular"` (not `cintillo`), without `obligatorio`/`ancla`, and the cintillo isn't guaranteed on the first cola segment.

- [ ] **Step 3: Update `_scored_segments_to_plan`** — make the cintillo a composed mandatory element with `etiqueta` from the label, set anchors and the new fields. Replace the grafismo-building section (the `cintillo_grafismo` dict and the per-tipo `grafismos.append(...)` calls) with:

```python
    cintillo_grafismo = {
        "tipo": "titular",                           # tipo stays "titular" so _TIMING_RULES
                                                     # ("intro"/"cierre","titular") still match;
                                                     # renders as the stepped cintillo (dispatch alias)
        "texto_principal": titular[:120],            # title band
        "texto_secundario": "",                      # paragraph filled by caller (entradilla)
        "etiqueta": cintillo_label,                  # optional red tag
        "obligatorio": True,
        "ancla": "cintillo_abajo_izq",
    }
```

and where grafismos are appended per segment, set anchors/flags on the optional ones, e.g. the rótulo:

```python
        elif tipo == "declaracion":
            nombre = s.get("hablante", "")
            cargo  = s.get("cargo", "")
            if nombre and nombre not in ("desconocido", "plano_sala"):
                grafismos.append({
                    "tipo": "rotulo_persona",
                    "texto_principal": nombre,
                    "texto_secundario": cargo,
                    "obligatorio": False,
                    "ancla": "rotulo_abajo_dcha",
                })
```

Keep the existing rule that intro/cierre and the first broll get the cintillo. Then, just before the `return`, **guarantee the mandatory cintillo is present** on the first segment (so title/paragraph always show, for every piece type):

```python
    # Mandatory cintillo: ensure the first segment carries one (title+paragraph always shown).
    has_cintillo = any(
        g.get("tipo") == "titular" for s in segs for g in s.get("grafismos", [])
    )
    if segs and not has_cintillo:
        segs[0].setdefault("grafismos", []).insert(0, dict(cintillo_grafismo))
```

- [ ] **Step 4: Run plan tests** — `cd apps/api && uv run pytest tests/unit/services/test_grafismo_plan.py -v` → PASS (2 passed).

- [ ] **Step 5: Propagate new fields in `_plan_to_grafismos`** — in `generar_pieza.py`, add the new fields to the `GrafismoEl(...)` construction (it currently passes only tipo/textos/timing):

```python
            elementos.append(GrafismoEl(
                tipo=g.get("tipo", "cintillo"),
                texto_principal=str(g.get("texto_principal", "")),
                texto_secundario=str(g.get("texto_secundario", "")),
                tiempo_inicio=t0,
                duracion=min(dur, video_duration - t0),
                obligatorio=bool(g.get("obligatorio", False)),
                visible=bool(g.get("visible", True)),
                ancla=str(g.get("ancla", "")),
                color_barra=str(g.get("color_barra", "")),
                etiqueta=str(g.get("etiqueta", "")),
            ))
```

- [ ] **Step 6: Run full unit suite** — `cd apps/api && uv run pytest tests/unit -q`
  Expected: PASS — all prior tests + the new grafismo tests. (If any existing grafismo/timeline test asserted `tipo:"titular"`, update it to `cintillo` — the alias keeps rendering working, but plan-level tests may need the new tipo. Do not weaken; align the expectation.)

- [ ] **Step 7: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/services/narrative_timeline.py apps/api/src/routes/generar_pieza.py apps/api/tests/unit/services/test_grafismo_plan.py
git commit -m "feat(grafismo): composed mandatory cintillo in plan + propagate editable fields"
```

---

## Task 8: Wire paragraph text + verify end-to-end (no rendering)

**Files:**
- Modify: `apps/api/src/routes/generar_pieza.py` (pieza-emision PASO 2b/4 — fill the cintillo paragraph from `entradilla`)
- Test: `apps/api/tests/unit/services/test_grafismo_plan.py` (append)

- [ ] **Step 1: Write the failing test** — append:

```python
def test_caller_can_fill_cintillo_paragraph():
    # The plan leaves the paragraph empty; the route fills it from the entradilla.
    segs = [_seg(0, 5)]
    plan = _scored_segments_to_plan(segs, cintillo_label="ÚLTIMA HORA", titular="T", tipo_pieza="cola")
    cintillo = next(g for s in plan["segmentos"] for g in s["grafismos"] if g["tipo"] == "titular")
    assert cintillo["texto_secundario"] == ""   # empty until the route fills it
    # Simulate the route filling it:
    entradilla = "Resumen de la noticia en dos líneas."
    cintillo["texto_secundario"] = entradilla
    assert cintillo["texto_secundario"] == entradilla
```

- [ ] **Step 2: Run to verify it passes-as-written** — `cd apps/api && uv run pytest tests/unit/services/test_grafismo_plan.py -k paragraph -v`
  Expected: PASS (this documents the contract: plan leaves paragraph empty, route fills it).

- [ ] **Step 3: Fill the paragraph in pieza-emision** — in `generar_pieza.py`, after `generar_timeline_narrativo(...)` returns `plan` and before `_plan_to_grafismos`/`_apply_grafismos` (PASO 4), set the cintillo paragraph from the editorial `entradilla`:

```python
    # The mandatory cintillo paragraph is the editorial summary (entradilla).
    for seg in plan_segmentos:
        for g in seg.get("grafismos", []):
            if g.get("tipo") == "cintillo" and not g.get("texto_secundario"):
                g["texto_secundario"] = entradilla.strip()[:200]
```

- [ ] **Step 4: Verify route modules import** — `cd apps/api && uv run python -c "import src.routes.generar_pieza; import src.routes.grafismo"` → exit 0.

- [ ] **Step 5: Run the full unit suite — no regressions** — `cd apps/api && uv run pytest tests/unit -q`
  Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
cd /Users/luisbravo/Music/CECA
git add apps/api/src/routes/generar_pieza.py apps/api/tests/unit/services/test_grafismo_plan.py
git commit -m "feat(grafismo): fill mandatory cintillo paragraph from entradilla"
```

---

## After all tasks

- `/api/grafismo/aplicar` already accepts `list[GrafismoElemento]` and calls `_apply_grafismos` → it now honours the new fields and renders the new style with fades, so the front's "export" works without a new endpoint. (If the front prefers a per-segment plan endpoint, add a thin wrapper that flattens via `_plan_to_grafismos` — out of scope here.)
- **Live validation (manual, not unit-testable):** run a real `cola` and a real `nota` and check the burned MP4 shows the stepped cintillo + fades + correct optional elements, nothing off-screen. Use `127.0.0.1`, restart the API (no `--reload`) so it serves the new code.
- Update `HANDOFF.md` (grafismos backend done; what's validated) in the final commit or a follow-up.
- Use `superpowers:finishing-a-development-branch`. Branch `feat/grafismos-editables`, base `develop` (stacked on `feat/multi-source-colas`). PRs via GitHub URL; commit email `luiscbravo94@gmail.com`.
- **What the default auto-plan ships:** the restyled **mandatory cintillo** (title+paragraph+optional tag) + **person rótulos**. The renderers and data model for the optional furniture (mosca `360`, clock, `La 1`, directo, contacto) all exist (Tasks 1/5), so the **front editor can add them**; `/aplicar` renders whatever the front sends.
- **Follow-up (not in this plan):** auto-including the corner furniture in the default plan with the precise cluster layout (`360` above the clock, `La 1` beside). The current corner anchors (`mosca_/reloj_/canal_esquina_dcha`) resolve to the same bottom-right point, so a composed corner-cluster renderer or distinct sub-anchors is needed first — tune this during live validation. Likewise the `dato`/`frase_clave`/`pie_pagina` renderers keep their current style (not in the reference catalog).
- Deferred (per spec): free x/y positioning, full slide+fade in MP4, ticking clock, the front editor itself.
