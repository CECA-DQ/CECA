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
    w, h = 1280, 720
    sx = int(w * _SAFE_FRAC)
    sy = int(h * _SAFE_FRAC)
    x, y = _anchor_box("rotulo_abajo_dcha", w, h, box_w=5000, box_h=80)
    assert x == sx
    assert y >= sy


def test_fit_lines_keeps_short_text():
    font = _load_font(18, bold=True)
    assert _fit_lines("Hola mundo", font, max_w=400, max_lines=2) == ["Hola mundo"]


def test_fit_lines_clamps_and_ellipsizes_overflow():
    # Text wraps to many lines; the last kept line is a single word wider than max_w,
    # so _ellipsize must trim it and append '…'.
    font = _load_font(18, bold=True)
    long = "corto " * 3 + "superlongworddddddddddddddddddd " * 8
    out = _fit_lines(long.strip(), font, max_w=200, max_lines=2)
    assert len(out) == 2
    assert out[-1].endswith("…")
    from src.routes.grafismo import _text_w
    assert _text_w(out[-1], font) <= 200


def test_fit_lines_ellipsizes_single_long_word():
    # A single word with no spaces, wider than max_w, must still be truncated + fit.
    font = _load_font(18, bold=True)
    out = _fit_lines("a" * 200, font, max_w=300, max_lines=2)
    assert len(out) == 1
    assert out[0].endswith("…")
    from src.routes.grafismo import _text_w
    assert _text_w(out[0], font) <= 300


def test_fit_lines_overflow_last_line_fits_width():
    font = _load_font(18, bold=True)
    from src.routes.grafismo import _text_w
    out = _fit_lines("palabra " * 60, font, max_w=300, max_lines=2)
    assert _text_w(out[-1], font) <= 300


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


from src.routes.grafismo import _render_lower_third, _hex_rgba, _AZUL_CARGO


def test_hex_rgba_parses_and_defaults():
    assert _hex_rgba("#1f6fb2") == (31, 111, 178, 255)
    assert _hex_rgba("") == _AZUL_CARGO          # default when empty
    assert _hex_rgba("garbage") == _AZUL_CARGO   # default on bad input
    assert _hex_rgba("1f6fb2") == (31, 111, 178, 255)  # without '#'
    assert _hex_rgba("#fff") == _AZUL_CARGO            # 3-digit → default


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


def test_rotulo_long_cargo_stays_in_safe_area():
    # Use a compact no-spaces string so [:60] still overflows box_w before the fix.
    el = GrafismoElemento(
        tipo="rotulo_persona", texto_principal="Nombre Apellido",
        texto_secundario="DirectorGeneralDePresupuestosYGastoPúblico" * 4,
        tiempo_inicio=0.0, duracion=6.0, ancla="rotulo_abajo_dcha",
    )
    img = _render_lower_third(el, 1280, 720)
    bbox = img.getbbox()
    sx = int(1280 * 0.05)
    assert bbox is not None
    assert bbox[2] <= 1280 - sx + 1   # right edge within the title-safe area


import re
from src.routes.grafismo import _render_element, _madrid_hhmm


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


def test_cintillo_empty_paragraph_draws_no_white_band():
    base = dict(tipo="titular", texto_principal="Titular de prueba", tiempo_inicio=0.0, duracion=5.0, etiqueta="X")
    def white_px(img):
        return sum(1 for p in img.getdata() if p == (255, 255, 255, 255))
    with_par = _render_cintillo(GrafismoElemento(**base, texto_secundario="Resumen de la noticia que ocupa la banda blanca"), 1280, 720)
    no_par   = _render_cintillo(GrafismoElemento(**base, texto_secundario=""), 1280, 720)
    # No paragraph → no solid white band → far fewer pure-white pixels (only glyph strokes)
    assert white_px(no_par) < white_px(with_par) / 5


from src.routes.grafismo import _build_overlay_filter, _FADE_S


def _vis(tipo, t0, dur, visible=True):
    return GrafismoElemento(tipo=tipo, texto_principal="x", tiempo_inicio=t0, duracion=dur, visible=visible)


def test_overlay_filter_skips_not_visible():
    els = [_vis("cintillo", 0, 5), _vis("contacto", 0, 5, visible=False), _vis("reloj", 0, 5)]
    rendered_idx, filt = _build_overlay_filter(els)
    assert rendered_idx == [0, 2]            # only the 2 visible elements
    assert filt.count("overlay=") == 2


def test_overlay_filter_fades_within_window():
    els = [_vis("cintillo", 2.0, 6.0)]       # t0=2.0, t1=8.0, fade=0.4
    _, filt = _build_overlay_filter(els)
    assert "fade=t=in:st=2.00:d=0.40:alpha=1" in filt
    assert "fade=t=out:st=7.60:d=0.40:alpha=1" in filt   # fade-out starts at t1 - fade
    assert "overlay=0:0[" in filt            # alpha gates visibility; no enable= needed


def test_simultaneo_rotulo_is_right_anchored_not_over_cintillo():
    # rotulo_persona_simultaneo must render like the normal (bottom-right) rótulo,
    # not the old left-anchored style that collided with the left cintillo.
    el = GrafismoElemento(
        tipo="rotulo_persona_simultaneo", texto_principal="Pedro Sánchez",
        texto_secundario="Presidente del Gobierno", tiempo_inicio=0.0, duracion=6.0,
        ancla="rotulo_abajo_dcha",
    )
    img = _render_element(el, 1280, 720)
    bbox = img.getbbox()
    assert bbox is not None
    assert bbox[0] > 1280 * 0.5   # painted pixels on the RIGHT half (no left collision)


def test_overlay_input_loops_past_fade_out():
    from pathlib import Path
    from src.routes.grafismo import _overlay_input_args
    el = _vis("cintillo", 2.0, 6.0)            # t1 = 8.0
    args = _overlay_input_args(el, Path("/tmp/x.png"))
    t = float(args[args.index("-t") + 1])
    assert t >= 8.0 + _FADE_S - 0.001          # input extends past t1 by the fade tail
