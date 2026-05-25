"""Tarea 3: Generador inteligente de piezas televisivas.

Transcribes one or more source videos, uses the LLM to select segments
according to the editorial criteria of each TV piece type, optionally
generates a TTS voiceover, and delegates final assembly to montaje.ensamblar().
"""

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.adapters.llm.factory import get_llm_provider
from src.core.auth import get_tenant_id
from src.routes.audio_mix import _mix
from src.routes.grafismo import GrafismoElemento as GrafismoEl, _apply_grafismos
from src.routes.montaje import SegmentoMontaje, _get_duration, _normalize_loudness, ensamblar
from src.services.narrative_timeline import generar_timeline_narrativo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/montaje", tags=["montaje"])

_STORAGE_BASE = Path("data/storage")

_PIECE_CONFIGS: dict[str, dict] = {
    "cola":       {"duracion_default": 45,  "criterio": "cortes cortos de 3-8 segundos de planos de recurso y b-roll sin declaraciones, distribuidos por todo el vídeo, para narrar encima", "con_locucion": False},
    "total":      {"duracion_default": 20,  "criterio": "solo la declaración más impactante del protagonista, en un único corte", "con_locucion": False},
    "vtr":        {"duracion_default": 120, "criterio": "mezcla de declaraciones clave (8-15 s cada una), b-roll de apoyo (3-6 s) y momentos de voz en off conectora, distribuidos por todo el vídeo", "con_locucion": True},
    "nota":       {"duracion_default": 75,  "criterio": "pieza informativa completa: declaración principal más contexto visual, cortes de 5-12 segundos distribuidos por el vídeo", "con_locucion": True},
    "off":        {"duracion_default": 60,  "criterio": "cortes cortos de 3-8 segundos de imágenes de recurso sin declaraciones, distribuidos por todo el vídeo, para narrar en off", "con_locucion": True},
    "broll":      {"duracion_default": 30,  "criterio": "cortes muy cortos de 3-6 segundos de imágenes de recurso variadas sin audio relevante, seleccionados de distintas partes del vídeo", "con_locucion": False},
    "highlights": {"duracion_default": 180, "criterio": "cortes cortos de 5-12 segundos de los momentos más destacados e impactantes, distribuidos cronológicamente por todo el vídeo", "con_locucion": False},
    "promo":      {"duracion_default": 30,  "criterio": "cortes muy cortos de 3-5 segundos de los momentos más llamativos para enganchar, tomados de distintas partes del vídeo", "con_locucion": False},
    "teaser":     {"duracion_default": 15,  "criterio": "uno o dos cortes de 5-8 segundos del instante más impactante para crear expectativa máxima", "con_locucion": False},
}

_PROMPT_GENERAR = """Eres un editor de televisión experto que trabaja con AVID Media Composer.
Selecciona segmentos de vídeo para montar una pieza de tipo "{tipo_pieza}".

PIEZA:
- Titular: {titular}
- Entradilla: {entradilla}
- Duración objetivo: {duracion_objetivo} segundos
- Criterio editorial: {criterio}

REGLAS DE MONTAJE — OBLIGATORIO CUMPLIR TODAS:
1. La suma total de (tiempo_fin - tiempo_inicio) de todos los segmentos debe ser
   entre {duracion_min} y {duracion_max} segundos. Esto es OBLIGATORIO.
2. Selecciona MÍNIMO {min_segmentos} segmentos. Más es mejor para dar ritmo.
3. Distribuye los segmentos a lo largo de TODO el material disponible.
   DIVIDE la duración total del material en {min_segmentos} partes iguales
   y selecciona al menos un segmento de cada parte.
4. Cortes cortos: entre 4 y 12 segundos por segmento, nunca más de 15.
5. Alterna tipos: no pongas dos "declaracion" seguidas, intercala "broll".
6. Estructura narrativa TV: intro (broll, 8s) → desarrollo → declaración → cierre (broll).

TRANSCRIPCIONES (fuente_index identifica el vídeo de origen):
{transcripciones}

{instrucciones_locucion}

Responde ÚNICAMENTE con JSON válido:
{{
  "segmentos": [
    {{
      "fuente_index": 0,
      "tiempo_inicio": 5.0,
      "tiempo_fin": 13.0,
      "tipo": "broll",
      "orden": 1,
      "motivo": "Intro: plano de recurso con acción relevante"
    }}
  ],
  "locucion": "Texto completo de voz en off si aplica, o null"
}}
Tipos válidos: declaracion, broll, recurso, intro, cierre."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GenerarPiezaRequest(BaseModel):
    tipo_pieza: str = "cola"
    titular: str
    entradilla: str = ""
    fuentes: list[str] = Field(min_length=1)
    duracion_objetivo: int | None = None
    generar_locucion: bool = False
    voz_id: str = "default"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _transcribe_source(source: Path, ffmpeg: str) -> list[dict]:
    from src.adapters.stt.factory import get_stt_provider

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        mp3_path = tmp.name
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", str(source),
            "-vn", "-acodec", "libmp3lame", "-q:a", "4", mp3_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        audio_bytes = Path(mp3_path).read_bytes()
        stt = get_stt_provider()
        result = await stt.transcribe(audio=audio_bytes, language=None, with_timestamps=True, with_diarization=False)
        return [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments]
    except Exception as exc:
        logger.warning("Transcription failed for %s: %s", source.name, exc)
        return []
    finally:
        Path(mp3_path).unlink(missing_ok=True)


def _sample_segments(segments: list[dict], max_chars: int) -> str:
    """Format segments sampling evenly to preserve the full timeline within the char budget."""
    lines = [
        f"[{s['start']:.1f}s-{s['end']:.1f}s] {s['text'].strip()}"
        for s in segments
    ]
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full
    avg_len = len(full) / max(len(lines), 1)
    max_lines = max(1, int(max_chars / avg_len))
    step = max(1, len(lines) // max_lines)
    return "\n".join(lines[i] for i in range(0, len(lines), step))


def _format_transcripts(sources: list[Path], all_segments: list[list[dict]]) -> str:
    """Combine transcripts from multiple sources, sampling each evenly."""
    # Budget per source: split 7000 chars across sources
    per_source_budget = max(500, 7000 // max(len(sources), 1))
    parts: list[str] = []
    for idx, (src, segs) in enumerate(zip(sources, all_segments)):
        header = f"--- FUENTE {idx} ({src.name}) ---"
        body = _sample_segments(segs, per_source_budget)
        parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def _parse_llm_json(raw: str) -> dict:
    """Extract and parse the first JSON object from an LLM response."""
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in LLM response: {exc}") from exc


async def _select_segments_llm(
    tipo_pieza: str,
    titular: str,
    entradilla: str,
    transcripciones: str,
    duracion_objetivo: int,
    criterio: str,
    quiere_locucion: bool,
) -> dict:
    instrucciones = (
        "Escribe también el texto de voz en off completo (campo 'locucion') que un locutor leerá sobre las imágenes."
        if quiere_locucion
        else "El campo 'locucion' debe ser null."
    )
    llm = get_llm_provider()
    min_segmentos = max(6, duracion_objetivo // 10)
    prompt = _PROMPT_GENERAR.format(
        tipo_pieza=tipo_pieza,
        titular=titular,
        entradilla=entradilla[:400],
        duracion_objetivo=duracion_objetivo,
        duracion_min=int(duracion_objetivo * 0.80),
        duracion_max=int(duracion_objetivo * 0.95),
        min_segmentos=min_segmentos,
        criterio=criterio,
        transcripciones=transcripciones,
        instrucciones_locucion=instrucciones,
    )
    response = await llm.generate(
        system="Eres un editor de televisión. Responde solo con JSON válido.",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000,
    )
    return _parse_llm_json(response.text.strip())


async def _synthesize_locucion(text: str, voz_id: str) -> str:
    from src.adapters.tts.factory import get_tts_provider
    tts = get_tts_provider()
    result = await tts.synthesize(text=text, voice_id=voz_id, language="es")
    audio_key = f"output/locucion/{uuid4()}.mp3"
    audio_path = (_STORAGE_BASE / audio_key).resolve()
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(result.audio)
    return audio_key


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/generar-pieza")
async def generar_pieza(
    body: GenerarPiezaRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    config = _PIECE_CONFIGS.get(body.tipo_pieza, _PIECE_CONFIGS["cola"])
    duracion = body.duracion_objetivo or config["duracion_default"]

    sources: list[Path] = []
    for key in body.fuentes:
        p = (_STORAGE_BASE / key).resolve()
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Source not found: {key}")
        sources.append(p)

    all_segments = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    if all(not segs for segs in all_segments):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, list(all_segments))
    quiere_locucion = body.generar_locucion and config["con_locucion"]

    try:
        selection = await _select_segments_llm(
            body.tipo_pieza, body.titular, body.entradilla,
            transcripciones_fmt, duracion, config["criterio"], quiere_locucion,
        )
    except ValueError as exc:
        logger.error("LLM segment selection failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"LLM selection failed: {exc}")

    raw_segs = selection.get("segmentos", [])
    if not raw_segs:
        raise HTTPException(status_code=422, detail="LLM returned no segments")

    audio_key: str | None = None
    locucion_text: str | None = selection.get("locucion")
    if quiere_locucion and locucion_text:
        try:
            audio_key = await _synthesize_locucion(locucion_text, body.voz_id)
        except Exception as exc:
            logger.warning("TTS synthesis failed, continuing without voiceover: %s", exc)

    segmentos: list[SegmentoMontaje] = []
    for seg in raw_segs:
        idx = int(seg.get("fuente_index", 0))
        if idx >= len(body.fuentes):
            logger.warning("fuente_index %d out of bounds (%d sources), falling back to 0", idx, len(body.fuentes))
            idx = 0
        segmentos.append(SegmentoMontaje(
            storage_key=body.fuentes[idx],
            tiempo_inicio=float(seg.get("tiempo_inicio", 0)),
            tiempo_fin=float(seg.get("tiempo_fin", 10)),
            tipo=seg.get("tipo", "broll"),
            orden=int(seg.get("orden", len(segmentos))),
        ))

    try:
        video_key, duration, material_en_loop = await ensamblar(
            segmentos,
            audio_voiceover_key=audio_key,
            duracion_objetivo=duracion,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "ok": True,
        "video_key": video_key,
        "video_url": f"/api/montaje/video/{video_key}",
        "duracion_real": round(duration, 1),
        "fps_salida": 25,
        "material_en_loop": material_en_loop,
        "tipo_pieza": body.tipo_pieza,
        "segmentos_montados": len(segmentos),
        "locucion_generada": audio_key is not None,
        "locucion_texto": locucion_text if quiere_locucion else None,
    }


# ---------------------------------------------------------------------------
# Schemas — pieza de emisión completa
# ---------------------------------------------------------------------------

class PiezaEmisionRequest(BaseModel):
    fuentes: list[str] = Field(min_length=1)
    titular: str
    entradilla: str = ""
    cuerpo: str = ""
    duracion_objetivo: int = Field(default=300, ge=300)
    tipo_pieza: str = "vtr"
    cintillo_label: str = "ÚLTIMA HORA"
    incluir_locucion: bool = True


# ---------------------------------------------------------------------------
# Route — pieza de emisión completa
# ---------------------------------------------------------------------------

_PROMPT_GRAFISMOS = """Eres un productor de televisión informativo español.
Genera grafismos de estilo Mañaneros 360/RTVE para esta noticia.
El cintillo inferior lleva [{cintillo}] — [titular breve].
Los datos clave van en la franja superior a mitad de pieza.
Duración del vídeo: {duracion} segundos.

Titular: {titular}
Entradilla: {entradilla}

Responde ÚNICAMENTE con JSON válido con esta estructura exacta:
{{
  "elementos": [
    {{"tipo": "titular", "texto_principal": "{cintillo} — titular breve", "tiempo_inicio": 2, "duracion": 13, "posicion": "inferior"}},
    {{"tipo": "pie_pagina", "texto_principal": "crawl con contexto de la noticia", "tiempo_inicio": 2, "duracion": {pie_duracion}, "posicion": "inferior"}},
    {{"tipo": "dato", "texto_principal": "dato clave 1 de la noticia", "tiempo_inicio": {dato1_t}, "duracion": 8, "posicion": "superior"}},
    {{"tipo": "dato", "texto_principal": "dato clave 2 de la noticia", "tiempo_inicio": {dato2_t}, "duracion": 8, "posicion": "superior"}},
    {{"tipo": "titular", "texto_principal": "{cintillo} — titular breve", "tiempo_inicio": {cierre_t}, "duracion": 20, "posicion": "inferior"}}
  ]
}}"""


def _plan_to_grafismos(
    plan_segmentos: list[dict],
    montaje_segs: list[SegmentoMontaje],
    video_duration: float,
) -> list[GrafismoEl]:
    """Convert per-segment relative graphic timings to absolute timeline positions."""
    elementos: list[GrafismoEl] = []
    current_t = 0.0

    for plan_seg, montaje_seg in zip(plan_segmentos, montaje_segs):
        seg_dur = (montaje_seg.tiempo_fin or montaje_seg.tiempo_inicio + 8) - montaje_seg.tiempo_inicio

        for g in plan_seg.get("grafismos", []):
            t0 = round(current_t + float(g.get("inicio_relativo", 0)), 1)
            dur = float(g.get("duracion", 5))
            if t0 >= video_duration:
                continue
            elementos.append(GrafismoEl(
                tipo=g.get("tipo", "titular"),
                texto_principal=str(g.get("texto_principal", "")),
                texto_secundario=str(g.get("texto_secundario", "")),
                tiempo_inicio=t0,
                duracion=min(dur, video_duration - t0),
            ))

        current_t += seg_dur

    return elementos


@router.post("/pieza-emision")
async def pieza_emision(
    body: PiezaEmisionRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")
    if not ffprobe:
        raise HTTPException(status_code=500, detail="ffprobe not found on PATH")

    pasos_completados: list[str] = []

    # ── PASO 1: Transcribir fuentes ────────────────────────────────────────────
    sources: list[Path] = []
    for key in body.fuentes:
        p = (_STORAGE_BASE / key).resolve()
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Source not found: {key}")
        sources.append(p)

    all_segs_trans = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    if all(not segs for segs in all_segs_trans):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, list(all_segs_trans))
    pasos_completados.append("transcripcion")

    # ── PASO 2: Generar timeline narrativo (segmentos + grafismos en una llamada)
    plan: dict = {}
    plan_segmentos: list[dict] = []
    locucion_text: str | None = None
    try:
        plan = await generar_timeline_narrativo(
            transcripciones=transcripciones_fmt,
            fuentes=body.fuentes,
            titular=body.titular,
            entradilla=body.entradilla,
            tipo_pieza=body.tipo_pieza,
            duracion_objetivo=body.duracion_objetivo,
            cintillo_label=body.cintillo_label,
            requiere_locucion=body.incluir_locucion,
        )
        plan_segmentos = plan.get("segmentos", [])
        locucion_text = plan.get("locucion")
        pasos_completados.append("timeline_narrativo")
    except Exception as exc:
        logger.error("Narrative timeline failed, falling back to basic selection: %s", exc)

    # Fallback: if timeline failed, use basic segment selection
    if not plan_segmentos:
        config = _PIECE_CONFIGS["vtr"]
        try:
            selection = await _select_segments_llm(
                "vtr", body.titular, body.entradilla,
                transcripciones_fmt, body.duracion_objetivo,
                config["criterio"], quiere_locucion=body.incluir_locucion,
            )
            plan_segmentos = selection.get("segmentos", [])
            locucion_text = selection.get("locucion")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Segment selection failed: {exc}")

    if not plan_segmentos:
        raise HTTPException(status_code=422, detail="LLM returned no segments")

    # ── PASO 3: Montar vídeo base ──────────────────────────────────────────────
    base_segs: list[SegmentoMontaje] = []
    for i, seg in enumerate(plan_segmentos):
        idx = int(seg.get("fuente_index", 0))
        if idx >= len(body.fuentes):
            idx = 0
        base_segs.append(SegmentoMontaje(
            storage_key=body.fuentes[idx],
            tiempo_inicio=float(seg.get("tiempo_inicio", 0)),
            tiempo_fin=float(seg.get("tiempo_fin", 10)),
            tipo=seg.get("tipo", "broll"),
            orden=int(seg.get("orden", i)),
        ))

    try:
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=body.duracion_objetivo,
            normalize_audio=False,   # normalization runs as the final paso, after grafismos+voiceover
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    pasos_completados.append("pieza_base")

    # ── PASO 4: Convertir grafismos relativos → absolutos y aplicar ───────────
    titulo_cintillo = plan.get("titulo_cintillo") or f"{body.cintillo_label} — {body.titular[:50]}"

    if plan_segmentos and plan.get("titulo_cintillo"):
        # Use content-aware graphics from the narrative plan
        elementos = _plan_to_grafismos(plan_segmentos, base_segs, duration)
    else:
        # Fallback structural graphics
        dur_int = max(30, int(duration))
        short_title = f"{body.cintillo_label} — {body.titular[:45]}"
        elementos = [
            GrafismoEl(tipo="titular", texto_principal=short_title, tiempo_inicio=2.0, duracion=13.0),
            GrafismoEl(tipo="titular", texto_principal=short_title,
                       tiempo_inicio=max(15.0, dur_int - 22.0), duracion=18.0),
        ]

    warning_grafismo: str | None = None
    if elementos:
        input_path = (_STORAGE_BASE / video_key).resolve()
        grafismo_key = f"output/grafismo/{uuid4()}.mp4"
        grafismo_output = (_STORAGE_BASE / grafismo_key).resolve()
        grafismo_output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                await _apply_grafismos(
                    input_path, elementos, grafismo_output,
                    ffmpeg, ffprobe, Path(tmpdir),
                )
            video_key = grafismo_key
            pasos_completados.append("grafismos")
        except RuntimeError as exc:
            warning_grafismo = str(exc)
            logger.error("Grafismo application failed: %s", exc)

    # ── PASO 4: Sintetizar locución y mezclar con el vídeo ────────────────────
    if body.incluir_locucion and locucion_text:
        audio_key_loc: str | None = None
        try:
            audio_key_loc = await _synthesize_locucion(locucion_text, "default")
            pasos_completados.append("locucion")
        except Exception as exc:
            logger.warning("TTS synthesis failed: %s", exc)

        if audio_key_loc:
            video_path_mix = (_STORAGE_BASE / video_key).resolve()
            audio_path_mix = (_STORAGE_BASE / audio_key_loc).resolve()
            mezcla_key = f"output/mezcla/{uuid4()}.mp4"
            mezcla_output = (_STORAGE_BASE / mezcla_key).resolve()
            mezcla_output.parent.mkdir(parents=True, exist_ok=True)
            try:
                await _mix(video_path_mix, audio_path_mix, mezcla_output, "mezclar", 0.15, ffmpeg)
                video_key = mezcla_key
                pasos_completados.append("mezcla")
            except RuntimeError as exc:
                logger.error("Audio mix failed, continuing without: %s", exc)

    # ── PASO 5: EBU R128 loudness normalization (-23 LUFS) ────────────────────
    final_path = (_STORAGE_BASE / video_key).resolve()
    lufs_normalizado = False
    normalized_path = final_path.with_stem(final_path.stem + "_r128")
    try:
        await _normalize_loudness(final_path, normalized_path)
        final_path.unlink()
        normalized_path.rename(final_path)
        lufs_normalizado = True
        pasos_completados.append("ebu_r128")
    except RuntimeError as exc:
        logger.warning("EBU R128 normalization skipped: %s", exc)
        normalized_path.unlink(missing_ok=True)

    final_duration = await _get_duration(final_path)

    return {
        "ok": True,
        "video_key": video_key,
        "video_url": f"/api/montaje/video/{video_key}",
        "duracion_real": round(final_duration, 1),
        "fps_salida": 25,
        "resolucion": "1280x720",
        "material_en_loop": material_en_loop,
        "titulo_generado": titulo_cintillo,
        "locucion_texto": locucion_text if body.incluir_locucion else None,
        "grafismos_aplicados": len(elementos),
        "segmentos_montados": len(base_segs),
        "pasos_completados": pasos_completados,
        "lufs_salida": -23 if lufs_normalizado else None,
        "warning_grafismo": warning_grafismo,
        "plan_narrativo": {
            "titulo_cintillo": titulo_cintillo,
            "segmentos": [
                {
                    "orden": s.get("orden", i),
                    "tipo": s.get("tipo"),
                    "duracion": round(
                        float(s.get("tiempo_fin", 0)) - float(s.get("tiempo_inicio", 0)), 1
                    ),
                    "grafismos": len(s.get("grafismos", [])),
                }
                for i, s in enumerate(plan_segmentos)
            ],
        },
    }


# ---------------------------------------------------------------------------
# Preview endpoint — returns the narrative plan without rendering
# ---------------------------------------------------------------------------

class PreviewTimelineRequest(BaseModel):
    fuentes: list[str] = Field(min_length=1)
    titular: str
    entradilla: str = ""
    duracion_objetivo: int = Field(default=300, ge=30)
    tipo_pieza: str = "vtr"
    cintillo_label: str = "ÚLTIMA HORA"
    incluir_locucion: bool = False


@router.post("/preview-timeline")
async def preview_timeline(
    body: PreviewTimelineRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """Generate the narrative montage plan without rendering any video.
    Use this to inspect what the AI would produce before committing to a full render.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    sources: list[Path] = []
    for key in body.fuentes:
        p = (_STORAGE_BASE / key).resolve()
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"Source not found: {key}")
        sources.append(p)

    all_segs = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    if all(not s for s in all_segs):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, list(all_segs))

    try:
        plan = await generar_timeline_narrativo(
            transcripciones=transcripciones_fmt,
            fuentes=body.fuentes,
            titular=body.titular,
            entradilla=body.entradilla,
            tipo_pieza=body.tipo_pieza,
            duracion_objetivo=body.duracion_objetivo,
            cintillo_label=body.cintillo_label,
            requiere_locucion=body.incluir_locucion,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    segmentos = plan.get("segmentos", [])
    total_dur = sum(
        float(s.get("tiempo_fin", 0)) - float(s.get("tiempo_inicio", 0))
        for s in segmentos
    )
    grafismos_total = sum(len(s.get("grafismos", [])) for s in segmentos)

    return {
        "ok": True,
        "titulo_cintillo": plan.get("titulo_cintillo"),
        "segmentos_count": len(segmentos),
        "duracion_estimada": round(total_dur, 1),
        "grafismos_count": grafismos_total,
        "tiene_locucion": plan.get("locucion") is not None,
        "plan": plan,
    }
