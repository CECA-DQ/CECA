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
