"""Narrative timeline generator for TV news pieces.

Takes raw transcription and produces a structured montage plan:
segments in narrative order with content-aware graphic cues.
The LLM acts as a senior Mañaneros 360 producer, reading the transcript
and deciding what to cut, in what order, and what graphics each moment needs.
"""

import json
import logging
from pathlib import Path

from src.adapters.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = (
    "Eres un productor senior de informativos del programa Mañaneros 360 (RTVE). "
    "Tu trabajo es construir el plan de montaje narrativo de una pieza de TV a partir "
    "de la transcripción del material bruto. Respondes SOLO con JSON válido, sin markdown."
)

_USER_TEMPLATE = """DATOS DE LA PIEZA:
- Tipo: {tipo_pieza}
- Titular: {titular}
- Etiqueta cintillo: {cintillo_label}
- Duración objetivo: {duracion_objetivo} segundos
- Locucion requerida: {requiere_locucion}

TRANSCRIPCIONES DEL MATERIAL BRUTO:
{transcripciones}

━━━ INSTRUCCIONES ━━━

Construye el plan de montaje siguiendo estas reglas de producción TV:

1. ESTRUCTURA NARRATIVA OBLIGATORIA:
   - Segmento 1 → tipo "intro": broll de 6-10s. El cintillo entra aquí en segundo 2.
   - Segmentos 2-N → alterna "broll" y "declaracion". Empieza con broll.
   - Segmento final → tipo "cierre": broll de 6-10s. El cintillo reaparece.

2. DETECCIÓN DE DECLARACIONES:
   Lee la transcripción. Cuando detectes que alguien está hablando, crea un segmento
   tipo "declaracion". Solo añade rotulo_persona si puedes identificar CLARAMENTE el
   nombre real de la persona en el texto. Si no aparece el nombre explícitamente,
   NO añadas el grafismo — es mejor no tener rótulo que inventar uno.

3. GRAFISMOS POR SEGMENTO (campo "grafismos", lista puede ser vacía []):
   - Segmento "intro": añade cintillo. inicio_relativo=2, duracion=13.
   - Segmento "declaracion" con nombre identificado: añade rotulo_persona. inicio_relativo=0.5, duracion=min(7, dur_segmento-1).
   - Segmento "declaracion" sin nombre identificado: grafismos=[].
   - Segmento "broll" con dato numérico importante en transcripción: añade dato. inicio_relativo=1, duracion=6.
   - Segmento "cierre": añade cintillo. inicio_relativo=1, duracion=min(15, dur_segmento-1).
   - NO añadas grafismos a segmentos de broll sin contenido relevante.

4. DURACIÓN:
   - Suma de (tiempo_fin - tiempo_inicio) debe estar entre {duracion_min} y {duracion_max} segundos.
   - Mínimo {min_segmentos} segmentos, máximo 40.
   - Cortes de 5-12 segundos por segmento, nunca más de 15.
   - Distribuye los segmentos a lo largo de TODO el material disponible.

5. LOCUCIÓN (si requiere_locucion=true):
   Escribe el texto completo de voz en off en el campo "locucion".
   Debe narrar la pieza completa, adaptado a los segmentos seleccionados.
   Si requiere_locucion=false, pon null.

RESPONDE ÚNICAMENTE CON ESTE JSON (sin comentarios, sin markdown):
{{
  "titulo_cintillo": "texto para el cintillo, máximo 55 caracteres",
  "locucion": "texto de la locución completa o null",
  "segmentos": [
    {{
      "orden": 1,
      "fuente_index": 0,
      "tiempo_inicio": 0.0,
      "tiempo_fin": 8.0,
      "tipo": "intro",
      "grafismos": [
        {{
          "tipo": "titular",
          "inicio_relativo": 2.0,
          "duracion": 13.0,
          "texto_principal": "{cintillo_label} — titulo breve",
          "texto_secundario": ""
        }}
      ]
    }},
    {{
      "orden": 2,
      "fuente_index": 0,
      "tiempo_inicio": 45.0,
      "tiempo_fin": 57.0,
      "tipo": "declaracion",
      "grafismos": [
        {{
          "tipo": "rotulo_persona",
          "inicio_relativo": 0.5,
          "duracion": 7.0,
          "texto_principal": "Nombre del declarante",
          "texto_secundario": "Cargo o filiación"
        }}
      ]
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

async def generar_timeline_narrativo(
    transcripciones: str,
    fuentes: list[str],
    titular: str,
    entradilla: str,
    tipo_pieza: str,
    duracion_objetivo: int,
    cintillo_label: str,
    requiere_locucion: bool = False,
) -> dict:
    """
    Call the LLM once and get back a full montage plan:
    ordered segments with relative-time graphic cues attached to each one.

    Returns the raw plan dict. Callers are responsible for converting
    relative graphic timings to absolute positions after assembly.
    """
    min_segs = max(6, duracion_objetivo // 10)
    prompt = _USER_TEMPLATE.format(
        tipo_pieza=tipo_pieza,
        titular=titular,
        cintillo_label=cintillo_label,
        duracion_objetivo=duracion_objetivo,
        requiere_locucion=str(requiere_locucion).lower(),
        transcripciones=transcripciones,
        duracion_min=int(duracion_objetivo * 0.80),
        duracion_max=int(duracion_objetivo * 0.95),
        min_segmentos=min_segs,
    )

    llm = get_llm_provider()
    response = await llm.generate(
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25,
        max_tokens=6000,
    )

    plan = _parse_plan(response.text.strip())
    _validate_plan(plan, len(fuentes))
    logger.info(
        "Narrative timeline generated: %d segments, locucion=%s",
        len(plan.get("segmentos", [])),
        plan.get("locucion") is not None,
    )
    return plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_plan(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in LLM response: {raw[:200]}")
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from LLM: {exc}") from exc


def _validate_plan(plan: dict, n_fuentes: int) -> None:
    """Clamp out-of-range values so downstream never crashes."""
    for seg in plan.get("segmentos", []):
        # Clamp fuente_index
        idx = int(seg.get("fuente_index", 0))
        seg["fuente_index"] = min(idx, n_fuentes - 1)

        # Ensure times are floats
        seg["tiempo_inicio"] = float(seg.get("tiempo_inicio", 0))
        seg["tiempo_fin"] = float(seg.get("tiempo_fin", seg["tiempo_inicio"] + 8))

        # Ensure grafismos is a list
        if not isinstance(seg.get("grafismos"), list):
            seg["grafismos"] = []

        # Drop rotulo_persona with no real name (placeholder values look unprofessional)
        _PLACEHOLDER_NAMES = {"declarante", "desconocido", "unknown", "speaker", "persona", ""}
        seg["grafismos"] = [
            g for g in seg["grafismos"]
            if not (
                g.get("tipo") == "rotulo_persona"
                and g.get("texto_principal", "").strip().lower() in _PLACEHOLDER_NAMES
            )
        ]

        # Clamp relative timings inside each grafismo
        seg_dur = seg["tiempo_fin"] - seg["tiempo_inicio"]
        for g in seg["grafismos"]:
            g["inicio_relativo"] = float(g.get("inicio_relativo", 0))
            g["duracion"] = min(float(g.get("duracion", 5)), max(1.0, seg_dur - g["inicio_relativo"]))
