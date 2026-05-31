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
