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


def _find_speaker_at_time(
    t: float,
    analisis_visual: list[dict] | None,
    src_idx: int,
) -> dict | None:
    """Return {nombre, cargo} of the person visible in the frame closest to time t.

    Used as a fallback when the LLM plan has a declaracion segment but no
    rotulo_persona (e.g. Mazón appears after Rufián but was not identified by
    the narrative LLM from text alone).
    """
    if not analisis_visual or src_idx >= len(analisis_visual):
        return None
    frames = analisis_visual[src_idx].get("fotogramas", [])
    if not frames:
        return None
    closest = min(frames, key=lambda f: abs(f.get("segundo", 0) - t))
    personas = closest.get("personas", [])
    for p in personas:
        name = p.get("nombre", "").strip()
        if name:
            return {"nombre": name, "cargo": p.get("cargo", "")}
    return None


def _assign_grafismo_timings(
    plan: dict,
    all_words: list[list[dict]] | None,
    analisis_visual: list[dict] | None = None,
) -> None:
    """Add inicio_relativo and duracion to every grafismo (mutates plan in-place).

    Rule A (declaracion segments):
      - Segment >= 14s (sequential): name shows 1.5→8.5s, quote shows 9.0→min(dur-1,14)s.
      - Segment < 14s (simultaneous): name+quote shown together 1.5→(dur-0.5)s.
        rotulo_persona renamed to rotulo_persona_simultaneo (grafismo.py renders it higher).
    Other types → deterministic lookup table.
    frase_clave → word-level search validates phrase exists; timing from Rule A, not phrase ts.
    """
    n_sources = len(all_words) if all_words else 0

    for seg in plan.get("segmentos", []):
        seg_tipo = seg.get("tipo", "broll")
        seg_start = float(seg.get("tiempo_inicio", 0.0))
        seg_end   = float(seg.get("tiempo_fin", seg_start + 8.0))
        seg_dur   = seg_end - seg_start
        src_idx   = int(seg.get("fuente_index", 0))
        words     = all_words[src_idx] if all_words and src_idx < n_sources else []

        is_short   = seg_dur < 14.0
        has_rotulo = any(g.get("tipo") == "rotulo_persona" for g in seg.get("grafismos", []))

        kept: list[dict] = []
        frase_pendiente: dict | None = None

        for g in seg.get("grafismos", []):
            g_tipo = g.get("tipo", "")

            if g_tipo == "frase_clave":
                phrase = g.get("texto_principal", "")
                ts = _find_phrase_timestamp(phrase, words, seg_start, seg_end)
                if ts is None:
                    logger.debug("frase_clave '%s' not found — dropping", phrase[:50])
                    continue
                frase_pendiente = g  # timing assigned after rotulo is processed

            elif g_tipo == "rotulo_persona":
                if is_short:
                    g["tipo"] = "rotulo_persona_simultaneo"
                    g["inicio_relativo"] = 1.5
                    g["duracion"] = max(0.5, seg_dur - 2.0)
                else:
                    g["inicio_relativo"] = 1.5
                    g["duracion"] = 7.0   # name: 1.5s → 8.5s
                kept.append(g)

            else:
                key = (seg_tipo, g_tipo)
                inicio, duracion = _TIMING_RULES.get(key, _DEFAULT_TIMING)
                g["inicio_relativo"] = inicio
                g["duracion"]        = duracion
                kept.append(g)

        if frase_pendiente is not None:
            if is_short:
                frase_pendiente["inicio_relativo"] = 1.5
                frase_pendiente["duracion"] = max(0.5, seg_dur - 2.0)
                kept.append(frase_pendiente)
            else:
                quote_out = min(seg_dur - 1.0, 14.0)
                if quote_out > 9.5:
                    frase_pendiente["inicio_relativo"] = 9.0
                    frase_pendiente["duracion"] = max(0.5, quote_out - 9.0)
                    kept.append(frase_pendiente)
                else:
                    logger.debug("Segment too short for sequential quote — dropping frase_clave")

        # Fallback: if this is a declaracion segment with no rotulo_persona,
        # try to inject one from visual analysis (catches speaker changes the
        # narrative LLM missed, e.g. Mazón replying after Rufián).
        if seg_tipo == "declaracion" and not any(g.get("tipo") == "rotulo_persona" for g in kept):
            speaker = _find_speaker_at_time(seg_start, analisis_visual, src_idx)
            if speaker:
                kept.insert(0, {
                    "tipo": "rotulo_persona",
                    "texto_principal": speaker["nombre"],
                    "texto_secundario": speaker["cargo"],
                    "inicio_relativo": 0.5,
                    "duracion": 6.0,
                })
                logger.info(
                    "Injected rotulo_persona '%s' for declaracion at %.1fs from visual analysis",
                    speaker["nombre"], seg_start,
                )

        seg["grafismos"] = kept

    logger.info(
        "Grafismo timings assigned: %d segments, %d grafismos total",
        len(plan.get("segmentos", [])),
        sum(len(s.get("grafismos", [])) for s in plan.get("segmentos", [])),
    )


# ---------------------------------------------------------------------------
# Highlight quote extraction (Fix 3) — separate LLM call with strict criteria
# ---------------------------------------------------------------------------

_SYSTEM_HIGHLIGHT = (
    "Eres un editor de titulares para un informativo de televisión española.\n"
    "Tu única tarea es elegir UNA frase del texto que se te da.\n\n"
    "CRITERIOS OBLIGATORIOS — rechaza cualquier frase que no cumpla TODOS:\n\n"
    "  1. COMPLETA GRAMATICALMENTE\n"
    "     La frase debe tener sujeto y verbo explícitos.\n"
    "     MAL: \"y eso es lo que no hicieron\"  ← ¿quién? ¿qué?\n"
    "     MAL: \"sabe lo que no hace\"  ← incompleta, sin contexto\n"
    "     BIEN: \"Mazón reconoció que no activó el protocolo de emergencias\"\n\n"
    "  2. AUTÓNOMA\n"
    "     Un espectador que no ha visto nada antes debe entenderla sola.\n"
    "     MAL: \"como le decía antes\"  ← referencia a algo no visto\n"
    "     MAL: \"eso que usted menciona\"  ← sin referente claro\n"
    "     BIEN: \"La Generalitat no envió alertas hasta las 20:11 del 29 de octubre\"\n\n"
    "  3. NOTICIOSA\n"
    "     Debe contener un hecho concreto, una cifra, un nombre, o una acción\n"
    "     que sea el núcleo informativo del fragmento.\n"
    "     MAL: \"esto es malo\"  ← vago\n"
    "     BIEN: \"113 llamadas al 112 quedaron sin respuesta esa noche\"\n\n"
    "  4. LONGITUD\n"
    "     Mínimo 6 palabras. Máximo 12 palabras.\n"
    "     Si no hay ninguna frase que cumpla 1-3 en el texto dado,\n"
    "     devuelve null en el campo \"frase\".\n\n"
    "  5. LITERAL\n"
    "     Copia la frase exactamente como aparece en el texto.\n"
    "     No la parafrasees ni la mejores.\n\n"
    "Responde SOLO con este JSON sin texto adicional ni markdown:\n"
    "{\n"
    "  \"frase\": \"string o null\",\n"
    "  \"segundo_inicio\": float,\n"
    "  \"cumple_criterios\": true,\n"
    "  \"razon\": \"string — en máx 10 palabras por qué esta frase es la mejor\"\n"
    "}"
)

_USER_HIGHLIGHT = (
    "Hablante: {nombre}, {cargo}.\n"
    "Duración del segmento: {duracion:.1f} segundos.\n\n"
    "Texto del segmento (con timestamps por palabra):\n"
    "{texto_con_timestamps}\n\n"
    "Elige la frase que cumpla todos los criterios del sistema.\n"
    "El campo segundo_inicio debe ser el timestamp de la PRIMERA PALABRA\n"
    "de la frase tal como aparece en el texto con timestamps."
)


def _format_words_with_timestamps(words: list[dict], seg_start: float, seg_end: float) -> str:
    """Format word-level transcript for the highlight LLM prompt."""
    scope = [w for w in words if w.get("start", 0) >= seg_start - 0.5 and w.get("start", 0) <= seg_end + 0.5]
    return " ".join(f"[{w['start']:.1f}]{w['word']}" for w in scope)


async def extract_highlight_quote(
    nombre: str,
    cargo: str,
    duracion: float,
    words: list[dict],
    seg_start: float,
    seg_end: float,
) -> dict | None:
    """Call LLM with strict criteria to select a citable quote from the segment.

    Returns {"frase": str, "segundo_inicio": float} or None if no valid quote found.
    Never raises — returns None on any failure.
    """
    texto_ts = _format_words_with_timestamps(words, seg_start, seg_end)
    if not texto_ts.strip():
        return None

    user_msg = _USER_HIGHLIGHT.format(
        nombre=nombre or "Declarante",
        cargo=cargo or "",
        duracion=duracion,
        texto_con_timestamps=texto_ts,
    )
    try:
        llm = get_llm_provider()
        response = await llm.generate(
            system=_SYSTEM_HIGHLIGHT,
            messages=[{"role": "user", "content": user_msg}],
            temperature=0.1,
            max_tokens=300,
        )
        raw = response.text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(raw[start:end])
        frase = data.get("frase")
        if not frase or not data.get("cumple_criterios"):
            return None
        words_count = len(frase.split())
        if words_count < 6:
            logger.debug("Highlight quote too short (%d words) — rejected", words_count)
            return None
        if words_count > 12:
            frase = " ".join(frase.split()[:12]) + "..."
        segundo = float(data.get("segundo_inicio", 0.0))
        if segundo == 0.0 and frase:
            logger.warning("highlight quote has segundo_inicio=0, using t=2.0")
            segundo = 2.0
        return {"frase": frase, "segundo_inicio": segundo}
    except Exception as exc:
        logger.warning("extract_highlight_quote failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Key moments extraction (Fix 4) — newsworthy headline detection
# ---------------------------------------------------------------------------

_SYSTEM_KEY_MOMENTS = (
    "Eres un editor jefe de informativos de televisión española.\n"
    "Tu tarea es identificar los 3-5 momentos más noticiosos del vídeo\n"
    "y convertir cada uno en un titular breve de TV.\n\n"
    "REGLAS:\n"
    "  1. Cada titular debe ser autónomo: se entiende sin ver el vídeo.\n"
    "  2. Estilo TVE/Antena 3: directo, sin adornos, verbo en presente o pasado simple.\n"
    "     MAL: \"El presidente habla sobre la polémica situación\"\n"
    "     BIEN: \"Mazón admite que no llamó a Emergencias hasta las 20:00\"\n"
    "  3. Máximo 10 palabras por titular.\n"
    "  4. Incluye el segundo aproximado del vídeo donde ocurre ese momento.\n"
    "  5. Clasifica cada momento: ADMISION | ACUSACION | CIFRA | COMPROMISO | CONTRADICCION\n"
    "  6. Ordénalos por importancia periodística (el más importante primero).\n\n"
    "Responde SOLO con este JSON sin markdown:\n"
    "{\n"
    "  \"titulares\": [\n"
    "    {\n"
    "      \"titular\": \"string — máx 10 palabras\",\n"
    "      \"tipo\": \"ADMISION | ACUSACION | CIFRA | COMPROMISO | CONTRADICCION\",\n"
    "      \"segundo_aproximado\": float,\n"
    "      \"cita_literal\": \"string — frase exacta del texto que sustenta el titular\"\n"
    "    }\n"
    "  ],\n"
    "  \"resumen_ejecutivo\": \"string — 2 frases que resumen el contenido completo\"\n"
    "}"
)


async def extract_key_moments(transcripcion_completa: str) -> dict:
    """Detect the 3-5 most newsworthy moments in the video and generate TV headlines.

    Called once per video with the full transcription.
    Returns {"titulares": [...], "resumen_ejecutivo": str}.
    Never raises — returns empty result on failure.
    """
    if not transcripcion_completa.strip():
        return {"titulares": [], "resumen_ejecutivo": ""}
    try:
        llm = get_llm_provider()
        response = await llm.generate(
            system=_SYSTEM_KEY_MOMENTS,
            messages=[{"role": "user", "content": transcripcion_completa}],
            temperature=0.2,
            max_tokens=1200,
        )
        raw = response.text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {"titulares": [], "resumen_ejecutivo": ""}
        return json.loads(raw[start:end])
    except Exception as exc:
        logger.warning("extract_key_moments failed: %s", exc)
        return {"titulares": [], "resumen_ejecutivo": ""}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def _scored_segments_to_plan(
    scored_segments: list[dict],
    cintillo_label: str,
    titular: str,
) -> dict:
    """Convert select_segments() output to the standard plan dict format.

    The LLM is NOT called when scored_segments are available — cuts come
    from visual scoring, not text-only reasoning.
    Grafismo text (speaker names from Gemini) is injected directly.
    """
    segs: list[dict] = []
    n = len(scored_segments)

    for i, s in enumerate(scored_segments):
        if i == 0:
            tipo = "intro"
        elif i == n - 1:
            tipo = "cierre"
        else:
            hablante = s.get("hablante", "plano_sala")
            tipo = "declaracion" if hablante not in ("desconocido", "plano_sala", "") else "broll"

        grafismos: list[dict] = []
        if tipo in ("intro", "cierre"):
            grafismos.append({
                "tipo": "titular",
                "texto_principal": f"{cintillo_label} — {titular[:50]}",
                "texto_secundario": "",
            })
        elif tipo == "declaracion":
            nombre = s.get("hablante", "")
            cargo  = s.get("cargo", "")
            if nombre and nombre not in ("desconocido", "plano_sala"):
                grafismos.append({
                    "tipo": "rotulo_persona",
                    "texto_principal": nombre,
                    "texto_secundario": cargo,
                })

        segs.append({
            "orden": i + 1,
            "fuente_index": s.get("fuente_index", 0),
            "tiempo_inicio": round(s["t_start"], 2),
            "tiempo_fin":    round(s["t_end"],   2),
            "tipo": tipo,
            "grafismos": grafismos,
        })

    return {
        "titulo_cintillo": f"{cintillo_label} — {titular[:55]}",
        "locucion": None,
        "segmentos": segs,
    }


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
    scored_segments: list[dict] | None = None,
) -> dict:
    """Build a full montage plan.

    When scored_segments are provided (from visual frame scoring), cuts are
    taken directly from them — the LLM only writes grafismo text and the
    voiceover script. This is the TV-editor workflow: watch first, then cut.

    Without scored_segments, the LLM selects cuts from text (legacy path).
    """
    # ── Path A: visual-score-driven cuts ────────────────────────────────────
    if scored_segments:
        plan = _scored_segments_to_plan(scored_segments, cintillo_label, titular)

        if requiere_locucion:
            # Still ask the LLM for the voiceover script, but not for cuts
            try:
                locucion_prompt = (
                    f"Escribe la locución (voz en off) para una pieza {tipo_pieza} de TV.\n"
                    f"Titular: {titular}\n"
                    f"Duración objetivo: {duracion_objetivo}s\n\n"
                    f"Transcripción del material:\n{transcripciones}"
                )
                llm = get_llm_provider()
                loc_resp = await llm.generate(
                    system=(
                        "Eres un redactor de informativos de televisión española. "
                        "Escribe el texto de la locución completa en español. "
                        "Solo el texto, sin JSON, sin titulares, sin aclaraciones."
                    ),
                    messages=[{"role": "user", "content": locucion_prompt}],
                    temperature=0.3,
                    max_tokens=1200,
                )
                plan["locucion"] = loc_resp.text.strip()
            except Exception as exc:
                logger.warning("Voiceover generation failed: %s", exc)

        _assign_grafismo_timings(plan, all_words)
        await _improve_highlight_quotes(plan, all_words)
        logger.info(
            "Timeline from visual scores: %d segments (no LLM cut selection)",
            len(plan.get("segmentos", [])),
        )
        return plan

    # ── Path B: LLM-driven cuts (fallback when no scored frames) ────────────
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

    # Improve frase_clave quotes in declaracion segments using strict criteria.
    # Run all calls in parallel to avoid serial latency.
    await _improve_highlight_quotes(plan, all_words)


    logger.info(
        "Narrative timeline generated: %d segments, locucion=%s",
        len(plan.get("segmentos", [])),
        plan.get("locucion") is not None,
    )
    return plan


async def _improve_highlight_quotes(plan: dict, all_words: list[list[dict]] | None) -> None:
    """Replace LLM-suggested frase_clave with validated quotes from extract_highlight_quote.

    Runs all per-segment calls in parallel. Mutates plan in-place.
    If a segment's frase_clave is rejected by the validator, it is dropped.
    """
    import asyncio as _asyncio

    n_sources = len(all_words) if all_words else 0

    async def _improve_segment(seg: dict) -> None:
        if seg.get("tipo") != "declaracion":
            return
        grafismos = seg.get("grafismos", [])
        frase_idx = next((i for i, g in enumerate(grafismos) if g.get("tipo") == "frase_clave"), None)
        if frase_idx is None:
            return

        src_idx = int(seg.get("fuente_index", 0))
        words = all_words[src_idx] if all_words and src_idx < n_sources else []
        seg_start = float(seg.get("tiempo_inicio", 0.0))
        seg_end   = float(seg.get("tiempo_fin", seg_start + 8.0))
        seg_dur   = seg_end - seg_start

        rotulo = next((g for g in grafismos if g.get("tipo") in ("rotulo_persona", "rotulo_persona_simultaneo")), {})
        nombre = rotulo.get("texto_principal", "Declarante")
        cargo  = rotulo.get("texto_secundario", "")

        result = await extract_highlight_quote(nombre, cargo, seg_dur, words, seg_start, seg_end)
        if result is None:
            grafismos.pop(frase_idx)
            logger.debug("extract_highlight_quote rejected quote for segment at %.1fs", seg_start)
        else:
            grafismos[frase_idx]["texto_principal"] = result["frase"]
            logger.debug("extract_highlight_quote approved quote: '%s'", result["frase"][:60])

    await _asyncio.gather(*[_improve_segment(s) for s in plan.get("segmentos", [])])


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
