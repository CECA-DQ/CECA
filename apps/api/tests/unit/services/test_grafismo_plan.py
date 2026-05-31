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
    segs = [_seg(0, 5), _seg(10, 18, hablante="Enrique Santiago"), _seg(20, 25)]
    plan = _scored_segments_to_plan(
        segs, cintillo_label="POLÍTICA", titular="T", tipo_pieza="nota",
    )
    rotulos = [g for s in plan["segmentos"] for g in s["grafismos"] if g["tipo"] == "rotulo_persona"]
    assert rotulos and rotulos[0]["ancla"] == "rotulo_abajo_dcha"
    assert rotulos[0]["obligatorio"] is False


def test_total_gets_mandatory_cintillo_via_guarantee_block():
    # 'total' routes every segment to "declaracion" → no cintillo is added in the
    # loop, so the guarantee block must inject the mandatory cintillo on segment 0.
    segs = [_seg(0, 6, hablante="Pedro Sánchez")]
    plan = _scored_segments_to_plan(
        segs, cintillo_label="ÚLTIMA HORA", titular="Comparece el presidente",
        tipo_pieza="total",
    )
    cintillos = [g for s in plan["segmentos"] for g in s["grafismos"] if g["tipo"] == "titular"]
    assert len(cintillos) == 1
    assert cintillos[0]["obligatorio"] is True
    assert cintillos[0]["ancla"] == "cintillo_abajo_izq"
