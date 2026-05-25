"""Narrative timeline generator for TV news pieces.

Takes raw transcription (and optional visual frame analysis) and produces
a structured montage plan: segments in narrative order with content-aware
graphic cues. The LLM acts as a senior Mañaneros 360 producer.

Architecture principle:
  The LLM decides WHAT to show (which grafismo type, what text).
  The code decides WHEN to show it (inicio_relativo, duracion) using
  deterministic rules and word-level timestamp search.
"""

import json
import logging
import re as _re

from src.adapters.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt — LLM only decides WHAT, never WHEN
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
   - Segmento 1 → tipo "intro": broll de 6-10s.
   - Segmentos 2-N → alterna "broll" y "declaracion". Empieza con broll.
   - Segmento final → tipo "cierre": broll de 6-10s.

2. DETECCIÓN DE DECLARACIONES:
   Lee la transcripción. Cuando detectes que alguien está hablando, crea un segmento
   tipo "declaracion". Solo añade rotulo_persona si puedes identificar CLARAMENTE el
   nombre real de la persona — usando el ANÁLISIS VISUAL si está disponible.
   Si no aparece el nombre explícitamente, NO añadas el grafismo.

3. GRAFISMOS POR SEGMENTO (campo "grafismos", puede ser []):
   IMPORTANTE: NO incluyas inicio_relativo ni duracion — el sistema los calcula solo.
   Solo indica tipo, texto_principal y texto_secundario.

   - Segmento "intro": añade un grafismo tipo "titular".
     texto_principal = "{cintillo_label} — [titular breve, max 50 chars]"
   - Segmento "declaracion" con nombre identificado: añade "rotulo_persona".
     texto_principal = nombre completo, texto_secundario = cargo o filiación.
   - Segmento "declaracion" sin nombre identificado: grafismos=[].
   - Segmento "broll" con dato numérico relevante en la transcripción: añade "dato".
     texto_principal = el dato concreto (ej: "12.000 fallecidos").
   - Segmento "cierre": añade "titular" (mismo texto que intro).
   - Segmentos "broll" sin dato relevante: grafismos=[].

3b. FRASE CLAVE — cita el momento más impactante de cada declaración:
   En cada segmento "declaracion" con una frase realmente citrable, añade
   un grafismo tipo "frase_clave". REGLAS ESTRICTAS:
   - texto_principal: copia las palabras EXACTAS de la transcripción, máximo 7 palabras.
     El sistema buscará esas palabras literalmente en el audio para sincronizarlas.
     Si necesitas truncar, añade "..." solo al final.
     USA MINÚSCULAS — el sistema hace el matching sin distinción de mayúsculas.
   - texto_secundario: siempre vacío "".
   - Máximo 1 frase_clave por segmento declaracion.
   - NO añadas frase_clave en broll, intro ni cierre.
   - Si la declaración no tiene ninguna frase impactante, no la añadas.

4. DURACIÓN — REGLA ABSOLUTA, NO NEGOCIABLE:
   - La suma total de (tiempo_fin - tiempo_inicio) de TODOS los segmentos DEBE estar
     entre {duracion_min} y {duracion_max} segundos. NI UN SEGUNDO MÁS.
   - Si el material tiene 30 minutos y se piden 60 segundos, selecciona SOLO los mejores
     60 segundos — no los 30 minutos completos.
   - Mínimo {min_segmentos} segmentos, máximo {max_segmentos}.
   - Cada segmento individual: entre 5 y 12 segundos. NUNCA más de 12s por segmento.
   - Distribuye los segmentos a lo largo del material (no cojas todo del principio).
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
          "texto_principal": "Nombre del declarante",
          "texto_secundario": "Cargo o filiación"
        }},
        {{
          "tipo": "frase_clave",
          "texto_principal": "es usted un psicópata",
          "texto_secundario": ""
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
# Grafismo timing — deterministic rules + word-level phrase search
# ---------------------------------------------------------------------------

# (segment_tipo, grafismo_tipo) → (inicio_relativo, duracion)
_TIMING_RULES: dict[tuple[str, str], tuple[float, float]] = {
    ("intro",       "titular"):        (2.0, 13.0),
    ("cierre",      "titular"):        (1.0, 15.0),
    ("declaracion", "rotulo_persona"): (0.5,  6.0),
    ("broll",       "dato"):           (1.0,  6.0),
    ("declaracion", "dato"):           (1.0,  6.0),
    ("intro",       "dato"):           (1.0,  6.0),
}
_FRASE_CLAVE_DURACION = 4.0
_DEFAULT_TIMING = (1.0, 5.0)


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip trailing ellipsis, remove punctuation → token list."""
    text = _re.sub(r"\.\.\.$", "", text.strip().lower())
    return [t for t in _re.sub(r"[^\w\sáéíóúüñ]", "", text).split() if t]


def _find_phrase_timestamp(
    phrase: str,
    words: list[dict],
    seg_start: float,
    seg_end: float,
) -> float | None:
    """Return absolute start time where `phrase` occurs in the word timeline.

    Searches a sliding window in [seg_start-0.5s, seg_end+0.5s].
    Requires ≥55% token overlap to avoid false positives on short phrases.
    Returns None when not found — caller drops the frase_clave rather than guessing.
    """
    phrase_tokens = _normalize_tokens(phrase)
    n = len(phrase_tokens)
    if n == 0 or not words:
        return None

    scope = [
        w for w in words
        if w["start"] >= seg_start - 0.5 and w["start"] <= seg_end + 0.5
    ]
    if len(scope) < n:
        return None

    best_ratio = 0.0
    best_time: float | None = None

    for i in range(len(scope) - n + 1):
        window = scope[i : i + n]
        window_tokens = _normalize_tokens(" ".join(w["word"] for w in window))
        matches = sum(1 for pt, wt in zip(phrase_tokens, window_tokens) if pt == wt)
        ratio = matches / n
        if ratio > best_ratio:
            best_ratio = ratio
            best_time = window[0]["start"]

    return best_time if best_ratio >= 0.55 else None


def _assign_grafismo_timings(
    plan: dict,
    all_words: list[list[dict]] | None,
) -> None:
    """Add inicio_relativo and duracion to every grafismo (mutates plan in-place).

    cintillo / rotulo / dato → deterministic table lookup.
    frase_clave              → word-level transcript search; dropped if not found.
    """
    n_sources = len(all_words) if all_words else 0

    for seg in plan.get("segmentos", []):
        seg_tipo = seg.get("tipo", "broll")
        seg_start = float(seg.get("tiempo_inicio", 0.0))
        seg_end = float(seg.get("tiempo_fin", seg_start + 8.0))
        src_idx = int(seg.get("fuente_index", 0))
        words = all_words[src_idx] if all_words and src_idx < n_sources else []

        kept: list[dict] = []
        for g in seg.get("grafismos", []):
            g_tipo = g.get("tipo", "")

            if g_tipo == "frase_clave":
                phrase = g.get("texto_principal", "")
                ts = _find_phrase_timestamp(phrase, words, seg_start, seg_end)
                if ts is None:
                    logger.debug(
                        "frase_clave '%s' not found in word transcript — dropping", phrase[:50]
                    )
                    continue  # never guess — drop it
                g["inicio_relativo"] = round(max(0.0, ts - seg_start), 2)
                g["duracion"] = _FRASE_CLAVE_DURACION
            else:
                key = (seg_tipo, g_tipo)
                inicio, duracion = _TIMING_RULES.get(key, _DEFAULT_TIMING)
                g["inicio_relativo"] = inicio
                g["duracion"] = duracion

            kept.append(g)

        seg["grafismos"] = kept

    logger.info(
        "Grafismo timings assigned: %d segments, %d grafismos total",
        len(plan.get("segmentos", [])),
        sum(len(s.get("grafismos", [])) for s in plan.get("segmentos", [])),
    )


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
    all_words: list[list[dict]] | None = None,
) -> dict:
    """Call the LLM once and get back a full montage plan.

    The LLM returns segment selections and grafismo text only.
    Grafismo timings are then assigned deterministically by _assign_grafismo_timings,
    using word-level transcript data for frase_clave placement.
    """
    duracion_mat = duracion_material or duracion_objetivo
    min_segs = max(3, duracion_objetivo // 15)
    max_segs = max(min_segs + 2, duracion_objetivo // 8)

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
        max_segmentos=max_segs,
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
    _validate_plan(plan, len(fuentes), duracion_objetivo)
    _assign_grafismo_timings(plan, all_words)
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


def _validate_plan(plan: dict, n_fuentes: int, duracion_objetivo: int | None = None) -> None:
    """Validate structure and hard-enforce the duration budget.

    Does NOT set grafismo timings — that is handled by _assign_grafismo_timings.
    """
    _PLACEHOLDER_NAMES = {"declarante", "desconocido", "unknown", "speaker", "persona", ""}

    segs = plan.get("segmentos", [])
    for seg in segs:
        # Clamp fuente_index
        idx = int(seg.get("fuente_index", 0))
        seg["fuente_index"] = min(idx, n_fuentes - 1)

        # Ensure times are floats and tiempo_fin > tiempo_inicio
        seg["tiempo_inicio"] = float(seg.get("tiempo_inicio", 0))
        seg["tiempo_fin"] = float(seg.get("tiempo_fin", seg["tiempo_inicio"] + 8))
        if seg["tiempo_fin"] <= seg["tiempo_inicio"]:
            seg["tiempo_fin"] = seg["tiempo_inicio"] + 8

        # Clamp individual segment to 12 s max
        if seg["tiempo_fin"] - seg["tiempo_inicio"] > 12.0:
            seg["tiempo_fin"] = seg["tiempo_inicio"] + 12.0

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

    if duracion_objetivo and segs:
        _enforce_duration_budget(plan, duracion_objetivo)


def _enforce_duration_budget(plan: dict, duracion_objetivo: int) -> None:
    """Trim segments from the middle until total duration fits the budget.

    Always keeps the first segment (intro) and last segment (cierre).
    Segments that partially exceed the budget are truncated rather than dropped.
    """
    budget = duracion_objetivo * 1.05  # 5 % tolerance

    def seg_dur(s: dict) -> float:
        return s["tiempo_fin"] - s["tiempo_inicio"]

    segs = plan["segmentos"]
    segs.sort(key=lambda s: s.get("orden", 0))

    total = sum(seg_dur(s) for s in segs)
    if total <= budget:
        return

    logger.warning(
        "LLM plan exceeded duration budget: %.1fs vs %.1fs budget — trimming",
        total, budget,
    )

    first = segs[:1]
    last = segs[-1:] if len(segs) > 1 else []
    middle = segs[1:-1] if len(segs) > 2 else []

    fixed = sum(seg_dur(s) for s in first + last)
    remaining_budget = budget - fixed

    kept: list[dict] = []
    used = 0.0
    for seg in middle:
        d = seg_dur(seg)
        if used + d <= remaining_budget:
            kept.append(seg)
            used += d
        else:
            leftover = remaining_budget - used
            if leftover >= 4.0:
                seg["tiempo_fin"] = seg["tiempo_inicio"] + leftover
                kept.append(seg)
            break

    plan["segmentos"] = first + kept + last
    for i, s in enumerate(plan["segmentos"], 1):
        s["orden"] = i

    new_total = sum(seg_dur(s) for s in plan["segmentos"])
    logger.info("Duration after trim: %.1fs (budget: %.1fs)", new_total, budget)
