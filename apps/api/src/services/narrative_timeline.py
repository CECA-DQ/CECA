"""Narrative timeline generator for TV news pieces.

Takes raw transcription (and optional visual frame analysis) and produces
a structured montage plan: segments in narrative order with content-aware
graphic cues. The LLM acts as a senior Mañaneros 360 producer.
"""

import json
import logging

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
{contexto_visual}
━━━ INSTRUCCIONES ━━━

Construye el plan de montaje siguiendo estas reglas de producción TV:

1. ESTRUCTURA NARRATIVA OBLIGATORIA:
   - Segmento 1 → tipo "intro": broll de 6-10s. El cintillo entra aquí en segundo 2.
   - Segmentos 2-N → alterna "broll" y "declaracion". Empieza con broll.
   - Segmento final → tipo "cierre": broll de 6-10s. El cintillo reaparece.

2. DETECCIÓN DE DECLARACIONES:
   Lee la transcripción. Cuando detectes que alguien está hablando, crea un segmento
   tipo "declaracion". Solo añade rotulo_persona si puedes identificar CLARAMENTE el
   nombre real de la persona — usando el ANÁLISIS VISUAL si está disponible.
   Si no aparece el nombre explícitamente, NO añadas el grafismo.

3. GRAFISMOS POR SEGMENTO (campo "grafismos", lista puede ser vacía []):
   - Segmento "intro": añade cintillo. inicio_relativo=2, duracion=13.
   - Segmento "declaracion" con nombre identificado: añade rotulo_persona. inicio_relativo=0.5, duracion=7.
   - Segmento "declaracion" sin nombre identificado: grafismos=[].
   - Segmento "broll" con dato numérico importante en transcripción: añade dato. inicio_relativo=1, duracion=6.
   - Segmento "cierre": añade cintillo. inicio_relativo=1, duracion=15.
   - NO añadas grafismos a segmentos de broll sin contenido relevante.
   NOTA: los grafismos pueden extenderse más allá del segmento — durations son en la línea de tiempo final.

4. DURACIÓN:
   - Suma de (tiempo_fin - tiempo_inicio) debe estar entre {duracion_min} y {duracion_max} segundos.
   - Mínimo {min_segmentos} segmentos, máximo 40.
   - Cortes de 5-12 segundos por segmento, nunca más de 15.
   - Distribuye los segmentos a lo largo de TODO el material disponible.
   - El material disponible tiene una duración máxima de {duracion_material} segundos.
     NO selecciones segmentos con tiempo_fin mayor que eso.

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
# Visual context formatter
# ---------------------------------------------------------------------------

def _format_visual_context(analisis_list: list[dict], source_names: list[str]) -> str:
    """Format visual analysis from one or more sources for the LLM prompt."""
    lines: list[str] = []
    has_content = False

    for idx, (analisis, name) in enumerate(zip(analisis_list, source_names)):
        persons = analisis.get("personas_principales", [])
        frames = analisis.get("fotogramas", [])
        if not persons and not frames:
            continue

        has_content = True
        lines.append(f"FUENTE {idx} ({name}):")

        if persons:
            for p in persons:
                t_str = ", ".join(f"{t}s" for t in p.get("segundos_aparicion", [])[:6])
                lines.append(f"  Identificado: {p['nombre']} ({p.get('cargo', '')}) — aparece en {t_str}")

        declarants = [f for f in frames if f.get("tipo_plano") == "declarante" and not any(
            p.get("nombre") for p in f.get("personas", [])
        )]
        if declarants:
            lines.append(
                f"  Declarante no identificado en: {', '.join(str(f['segundo']) for f in declarants[:6])}s"
            )

        for f in frames:
            if f.get("texto_visible"):
                lines.append(f"  Texto en pantalla ({f['segundo']}s): \"{f['texto_visible']}\"")

    if not has_content:
        return ""

    header = "\nANÁLISIS VISUAL DEL MATERIAL (detectado con IA — úsalo para rotulos precisos):\n"
    return header + "\n".join(lines) + "\n"


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
    duracion_material: int | None = None,
    analisis_visual: list[dict] | None = None,
) -> dict:
    """Call the LLM once and get back a full montage plan.

    Returns the raw plan dict. Callers are responsible for converting
    relative graphic timings to absolute positions after assembly.
    """
    duracion_mat = duracion_material or duracion_objetivo
    min_segs = max(6, duracion_objetivo // 10)

    source_names = [f.split("/")[-1] for f in fuentes]
    contexto_visual = (
        _format_visual_context(analisis_visual, source_names)
        if analisis_visual
        else ""
    )

    prompt = _USER_TEMPLATE.format(
        tipo_pieza=tipo_pieza,
        titular=titular,
        cintillo_label=cintillo_label,
        duracion_objetivo=duracion_objetivo,
        requiere_locucion=str(requiere_locucion).lower(),
        transcripciones=transcripciones,
        contexto_visual=contexto_visual,
        duracion_min=int(duracion_objetivo * 0.80),
        duracion_max=int(duracion_objetivo * 0.95),
        min_segmentos=min_segs,
        duracion_material=duracion_mat,
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
    """Clamp out-of-range index/time values so downstream never crashes."""
    _PLACEHOLDER_NAMES = {"declarante", "desconocido", "unknown", "speaker", "persona", ""}

    for seg in plan.get("segmentos", []):
        # Clamp fuente_index
        idx = int(seg.get("fuente_index", 0))
        seg["fuente_index"] = min(idx, n_fuentes - 1)

        # Ensure times are floats and tiempo_fin > tiempo_inicio
        seg["tiempo_inicio"] = float(seg.get("tiempo_inicio", 0))
        seg["tiempo_fin"] = float(seg.get("tiempo_fin", seg["tiempo_inicio"] + 8))
        if seg["tiempo_fin"] <= seg["tiempo_inicio"]:
            seg["tiempo_fin"] = seg["tiempo_inicio"] + 8

        # Ensure grafismos is a list
        if not isinstance(seg.get("grafismos"), list):
            seg["grafismos"] = []

        # Drop rotulo_persona with placeholder/unknown names
        seg["grafismos"] = [
            g for g in seg["grafismos"]
            if not (
                g.get("tipo") == "rotulo_persona"
                and g.get("texto_principal", "").strip().lower() in _PLACEHOLDER_NAMES
            )
        ]

        # Validate grafismo timings — NOTE: duration is NOT clamped to the segment length.
        # Grafismos intentionally span across cuts (e.g. a 13-second cintillo over a 8-second intro
        # continues visually into the next clip — that is standard TV production practice).
        for g in seg["grafismos"]:
            g["inicio_relativo"] = max(0.0, float(g.get("inicio_relativo", 0)))
            g["duracion"] = max(1.0, float(g.get("duracion", 5)))
