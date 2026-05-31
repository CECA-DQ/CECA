"""Tarea 3: Generador inteligente de piezas televisivas.

Transcribes one or more source videos, uses the LLM to select segments
according to the editorial criteria of each TV piece type, optionally
generates a TTS voiceover, and delegates final assembly to montaje.ensamblar().
"""

import asyncio
import hashlib
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
from src.routes.archivo import (
    _ffmpeg_download,
    _is_direct_url,
    _sanitize_filename,
    _ytdlp_download,
    _ytdlp_get_info,
)
from src.routes.audio_mix import _mix
from src.routes.grafismo import GrafismoElemento as GrafismoEl, _apply_grafismos
from src.routes.montaje import SegmentoMontaje, _get_duration, _normalize_loudness, ensamblar
from src.services.candidate_moments import find_candidate_moments
from src.services.narrative_timeline import _format_visual_context, generar_timeline_narrativo
from src.services.segment_selection import select_segments
from src.services.visual_analysis import analyze_video_visually

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/montaje", tags=["montaje"])


def _build_cut_points(words: list[dict], segs: list[dict]) -> list[float]:
    """Build a list of valid cut times from word-level silence gaps (preferred) or segment edges.

    A silence gap is defined as ≥250 ms between consecutive words. The cut point is
    placed at the midpoint of the gap so it sits in the quietest part of the audio.
    """
    if words:
        points: list[float] = []
        if words:
            points.append(words[0]["start"])
        for i in range(len(words) - 1):
            gap = words[i + 1]["start"] - words[i]["end"]
            if gap >= 0.25:
                points.append((words[i]["end"] + words[i + 1]["start"]) / 2.0)
        if words:
            points.append(words[-1]["end"])
        return sorted(set(round(p, 3) for p in points))

    # Fallback: use segment start/end boundaries
    return sorted({s["start"] for s in segs} | {s["end"] for s in segs})


def _nearest_cut(cut_points: list[float], t: float, tolerance: float) -> float:
    if not cut_points:
        return t
    closest = min(cut_points, key=lambda x: abs(x - t))
    return closest if abs(closest - t) <= tolerance else t


def _deduplicate_segments(plan_segs: list[dict]) -> list[dict]:
    """Remove segments with identical (fuente_index, tiempo_inicio, tiempo_fin).

    The LLM occasionally repeats the same clip twice — once as intro and once
    at the end — which makes the final video loop back visually.
    """
    seen: set[tuple] = set()
    clean: list[dict] = []
    for seg in plan_segs:
        key = (
            int(seg.get("fuente_index", 0)),
            round(float(seg.get("tiempo_inicio", 0)), 1),
            round(float(seg.get("tiempo_fin", 0)), 1),
        )
        if key not in seen:
            seen.add(key)
            clean.append(seg)
        else:
            logger.warning(
                "Duplicate segment removed: fuente=%d t=[%.1f, %.1f]", *key
            )
    return clean


def _snap_segment_boundaries(
    plan_segs: list[dict],
    all_segs_trans: list[list[dict]],
    all_words: list[list[dict]] | None = None,
    tolerance: float = 2.5,
) -> list[dict]:
    """Align LLM-chosen timestamps to the nearest silence gap in the audio.

    When word-level timestamps are available (all_words), cuts are placed at
    natural speech pauses (≥250 ms silence between words), giving frame-accurate
    audio cuts.  Falls back to segment boundaries when words are absent.
    """
    if not any(all_segs_trans):
        return plan_segs

    n = len(all_segs_trans)
    source_cuts: list[list[float]] = [
        _build_cut_points(
            (all_words[i] if all_words and i < len(all_words) else []),
            all_segs_trans[i],
        )
        for i in range(n)
    ]

    for seg in plan_segs:
        src_idx = min(int(seg.get("fuente_index", 0)), n - 1)
        cuts = source_cuts[src_idx]

        raw_start = float(seg.get("tiempo_inicio", 0.0))
        raw_end = float(seg.get("tiempo_fin", raw_start + 8.0))

        snapped_start = _nearest_cut(cuts, raw_start, tolerance)
        snapped_end = _nearest_cut(cuts, raw_end, tolerance)

        # Never let snapping shrink a segment below 3 s
        if snapped_end - snapped_start < 3.0:
            snapped_end = snapped_start + max(3.0, raw_end - raw_start)

        seg["tiempo_inicio"] = round(snapped_start, 2)
        seg["tiempo_fin"] = round(snapped_end, 2)

    return plan_segs

_STORAGE_BASE = Path("data/storage")


def _is_url(s: str) -> bool:
    """A fuente entry that must be downloaded rather than read from storage."""
    return s.startswith(("http://", "https://"))


def _storage_key_for(path: Path) -> str:
    """Storage key (relative to _STORAGE_BASE) for a resolved source path, so a
    montage clip can be routed back to its file. A downloaded URL maps to the
    cached file's key (e.g. ``videos/src_<hash>.mp4``), never the original URL."""
    return str(path.resolve().relative_to(_STORAGE_BASE.resolve()))


async def _download_fuente(url: str, ffmpeg: str) -> Path:
    """Download a URL fuente into data/storage and return its path.

    Content-addressed by a hash of the URL so the same video is not
    re-downloaded across endpoints (analizar-material → pieza-emision) or across
    retries. Direct media URLs use ffmpeg; everything else (YouTube, etc.) uses
    yt-dlp — the same download path used for the single `url` field.
    """
    videos_dir = _STORAGE_BASE / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    out_path = videos_dir / f"src_{hashlib.sha1(url.encode()).hexdigest()[:12]}.mp4"
    if out_path.exists():
        return out_path  # already downloaded

    if _is_direct_url(url):
        await _ffmpeg_download(url, out_path, ffmpeg)
    else:
        if not shutil.which("yt-dlp"):
            raise HTTPException(status_code=500, detail="yt-dlp no está instalado")
        await _ytdlp_download(url, out_path)

    if not out_path.exists():
        raise HTTPException(
            status_code=500,
            detail="La descarga finalizó pero no se encontró el fichero",
        )
    return out_path


async def _resolve_fuentes(fuentes: list[str], ffmpeg: str) -> list[Path]:
    """Resolve each fuente to a local path. http(s) URLs are downloaded
    (yt-dlp / ffmpeg); every other entry is a storage key under data/storage.
    Raises 404 for a missing storage key, 422 for a failed download.
    """
    sources: list[Path] = []
    for key in fuentes:
        if _is_url(key):
            try:
                sources.append(await _download_fuente(key, ffmpeg))
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=f"Descarga fallida ({key}): {exc}")
        else:
            p = (_STORAGE_BASE / key).resolve()
            if not p.exists():
                raise HTTPException(status_code=404, detail=f"Fuente no encontrada: {key}")
            sources.append(p)
    return sources

# ---------------------------------------------------------------------------
# Editorial analysis prompts
# ---------------------------------------------------------------------------

_SYSTEM_ANALIZAR = (
    "Eres un redactor jefe de informativos de televisión española. "
    "Analizas transcripciones de material bruto y generas los metadatos editoriales "
    "para producir piezas TV. Respondes SOLO con JSON válido, sin markdown."
)

_PROMPT_ANALIZAR = """Analiza esta transcripción de material de vídeo bruto y genera los metadatos editoriales para producir una pieza televisiva.

TRANSCRIPCIÓN:
{transcripciones}
{contexto_visual}
Responde ÚNICAMENTE con este JSON:
{{
  "titular": "Titular directo, máximo 80 caracteres, estilo informativo TV",
  "entradilla": "Dos o tres frases que resumen la noticia, unas 80 palabras, estilo periodístico",
  "cuerpo": "Desarrollo informativo con contexto y datos relevantes, 3-5 frases",
  "tipo_pieza_sugerido": "cola|vtr|nota|total|off|broll|highlights",
  "duracion_sugerida": 120,
  "cintillo_label": "ÚLTIMA HORA",
  "personas_detectadas": [{{"nombre": "Nombre", "cargo": "Cargo"}}],
  "temas": ["tema1", "tema2", "tema3"],
  "tono": "informativo|urgente|analítico|positivo",
  "requiere_locucion": true
}}

REGLAS:
- titular: ¿qué pasó? ¿quién? Directo, sin adornos, máximo 80 caracteres
- tipo_pieza: cola si es recurso visual sin declaraciones; total si hay un único declarante; vtr si hay historia completa; nota si es informativo corto con declaraciones y contexto
- duracion_sugerida en segundos: cola→15-45, total→15-25, vtr→60-180, nota→30-75
- Solo incluye personas cuyo nombre aparezca explícitamente en la transcripción o en el análisis visual
- cintillo_label: ÚLTIMA HORA / ECONOMÍA / POLÍTICA / INTERNACIONAL / SOCIEDAD / DEPORTES / CULTURA"""


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

# Piece types rendered with NO audio (silent recurso the presenter narrates over
# live). Deliberately NOT the same as segment_selection._RECURSO_TYPES: `off` is
# also recurso but keeps a mixed voiceover, so it must NOT be muted.
_MUTED_TYPES = {"cola", "broll"}

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

async def _transcribe_source(source: Path, ffmpeg: str) -> tuple[list[dict], list[dict]]:
    """Transcribe a video source, returning (segments, words).

    segments: [{"start", "end", "text"}]
    words:    [{"start", "end", "word"}] — empty list if provider doesn't support word timestamps
    """
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
        segs = [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments]
        words = [{"start": w.start, "end": w.end, "word": w.word} for w in result.words]
        return segs, words
    except Exception as exc:
        logger.warning("Transcription failed for %s: %s", source.name, exc)
        return [], []
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
    from src.config import settings as _settings
    tts = get_tts_provider()
    resolved_voice = voz_id if voz_id and voz_id != "default" else _settings.tts_default_voice_id
    result = await tts.synthesize(text=text, voice_id=resolved_voice, language="es")
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

    all_trans = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    all_segments = [t[0] for t in all_trans]
    if all(not segs for segs in all_segments):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, all_segments)
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
    titular: str = ""           # auto-generated from transcription if empty
    entradilla: str = ""
    cuerpo: str = ""
    duracion_objetivo: int = Field(default=300, ge=30)
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

    # Apply piece-type defaults: duration and locución from _PIECE_CONFIGS
    piece_cfg = _PIECE_CONFIGS.get(body.tipo_pieza, _PIECE_CONFIGS["vtr"])
    # If frontend sends the schema default (300), replace with the type's editorial default.
    # This applies to ALL piece types including vtr — the schema default of 300 is just a
    # placeholder sentinel, not an editorial choice.
    duracion_solicitada = (
        piece_cfg["duracion_default"]
        if body.duracion_objetivo == 300
        else body.duracion_objetivo
    )
    # Respect locución flag from frontend but warn when it conflicts with piece type
    quiere_locucion = body.incluir_locucion and piece_cfg["con_locucion"]

    # ── PASO 1: Transcribir fuentes + análisis visual en paralelo ─────────────
    # fuentes may be storage keys or http(s) URLs (downloaded on the fly).
    sources = await _resolve_fuentes(body.fuentes, ffmpeg)
    # Storage key per source, used to route each montage clip back to its file.
    # For a downloaded URL this is the cached file's key, not the original URL.
    fuente_keys = [_storage_key_for(p) for p in sources]

    # Get durations for visual analysis frame budgeting
    source_durations = await asyncio.gather(*[_get_duration(src) for src in sources])

    # Transcribe first so we can pass word timestamps to visual scoring
    all_trans_raw = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    all_segs_trans  = [t[0] for t in all_trans_raw]
    all_words_trans = [t[1] for t in all_trans_raw]

    # Content-driven sampling: pick the moments worth scoring from the transcript
    # Topic keywords for candidate scoring; fall back to entradilla/cuerpo when
    # the titular is auto-generated (and thus empty at this point).
    tema_str = (body.titular.strip() or body.entradilla.strip() or body.cuerpo.strip())[:200]
    all_candidates = [
        find_candidate_moments(
            all_words_trans[i], all_segs_trans[i], dur,
            tema=tema_str, budget=25,
        )
        for i, (src, dur) in enumerate(zip(sources, source_durations))
    ]

    # Visual scoring: Gemini sees frames + transcript words together
    all_visual = await asyncio.gather(*[
        analyze_video_visually(
            src, ffmpeg, dur,
            words=all_words_trans[i],
            tipo_contenido=body.tipo_pieza,
            tema=tema_str,
            candidate_moments=all_candidates[i] or None,
        )
        for i, (src, dur) in enumerate(zip(sources, source_durations))
    ])

    if all(not segs for segs in all_segs_trans):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, all_segs_trans)
    pasos_completados.append("transcripcion")
    if any(v.get("fotogramas") for v in all_visual):
        pasos_completados.append("analisis_visual")

    # Effective duration: don't request more than what the source material can provide.
    max_available = max(
        (s["end"] for segs in all_segs_trans for s in segs),
        default=float(duracion_solicitada),
    )
    duracion_efectiva = min(duracion_solicitada, int(max_available * 0.9))

    # ── Auto-generar titular si no se proporcionó ─────────────────────────────
    titular = body.titular.strip()
    entradilla = body.entradilla.strip()
    if not titular:
        try:
            source_names_auto = [f.split("/")[-1] for f in body.fuentes]
            cv = _format_visual_context(list(all_visual), source_names_auto)
            auto_resp = await get_llm_provider().generate(
                system=_SYSTEM_ANALIZAR,
                messages=[{"role": "user", "content": _PROMPT_ANALIZAR.format(
                    transcripciones=transcripciones_fmt,
                    contexto_visual=f"\n{cv}\n" if cv else "",
                )}],
                temperature=0.3,
                max_tokens=800,
            )
            auto = _parse_llm_json(auto_resp.text.strip())
            titular = auto.get("titular", "") or "Sin título"
            if not entradilla:
                entradilla = auto.get("entradilla", "")
            pasos_completados.append("auto_analisis")
        except Exception as exc:
            logger.warning("Auto-analysis for titular failed: %s", exc)
            titular = titular or "Sin título"

    # ── PASO 2a: Selección de cortes por scoring visual (determinístico) ─────────
    # If visual scoring returned high-quality frames, use the deterministic
    # select_segments() algorithm. This mirrors how a TV editor works: watch
    # first, then cut — no text-only LLM guessing what is on screen.
    all_scored_frames = [v.get("frames", []) for v in all_visual]
    # Pass all frames — no pre-filter here. select_segments() applies its own
    # threshold internally and has a fallback for broll/cola pieces with no
    # high-scoring speaker frames.
    scored_frames_flat = [
        {**f, "fuente_index": i}
        for i, frames in enumerate(all_scored_frames)
        for f in frames
    ]

    score_selected: list[dict] = []
    if scored_frames_flat:
        # Flatten words across sources for silence snapping
        words_flat = [w for ws in all_words_trans for w in ws]
        score_selected = select_segments(
            scored_frames_flat,
            target_duration=float(duracion_efectiva),
            words=words_flat,
            tipo_pieza=body.tipo_pieza,
            n_fuentes=len(body.fuentes),
        )
        if score_selected:
            pasos_completados.append("seleccion_visual")
            logger.info(
                "Visual scoring selected %d segments (%.1fs)",
                len(score_selected),
                sum(s["t_end"] - s["t_start"] for s in score_selected),
            )

    # ── PASO 2b: Generar timeline narrativo (grafismos + locucion) ────────────
    # The LLM no longer decides cuts — it only assigns grafismo text and writes
    # the voiceover script. If visual scoring produced segments, we inject them
    # into the plan; otherwise the LLM selects cuts as a fallback.
    plan: dict = {}
    plan_segmentos: list[dict] = []
    locucion_text: str | None = None
    try:
        plan = await generar_timeline_narrativo(
            transcripciones=transcripciones_fmt,
            fuentes=body.fuentes,
            titular=titular,
            entradilla=entradilla,
            tipo_pieza=body.tipo_pieza,
            duracion_objetivo=duracion_efectiva,
            cintillo_label=body.cintillo_label,
            requiere_locucion=quiere_locucion,
            duracion_material=int(max_available),
            analisis_visual=list(all_visual),
            all_words=all_words_trans,
            scored_segments=score_selected or None,
        )
        plan_segmentos = plan.get("segmentos", [])
        locucion_text = plan.get("locucion")
        pasos_completados.append("timeline_narrativo")
    except Exception as exc:
        logger.error("Narrative timeline failed, falling back to basic selection: %s", exc)

    # Remove any duplicate segments before snapping and assembly
    if plan_segmentos:
        plan_segmentos = _deduplicate_segments(plan_segmentos)

    # Snap LLM timestamps to silence gaps in the audio for clean cuts
    if plan_segmentos:
        plan_segmentos = _snap_segment_boundaries(
            plan_segmentos, all_segs_trans, all_words_trans
        )

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
        if idx >= len(fuente_keys):
            idx = 0
        base_segs.append(SegmentoMontaje(
            storage_key=fuente_keys[idx],
            tiempo_inicio=float(seg.get("tiempo_inicio", 0)),
            tiempo_fin=float(seg.get("tiempo_fin", 10)),
            tipo=seg.get("tipo", "broll"),
            orden=int(seg.get("orden", i)),
        ))

    try:
        video_key, duration, material_en_loop = await ensamblar(
            base_segs,
            duracion_objetivo=duracion_efectiva,
            normalize_audio=False,   # normalization runs as the final paso, after grafismos+voiceover
            mute_clips=body.tipo_pieza in _MUTED_TYPES,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    pasos_completados.append("pieza_base")

    # ── PASO 4: Convertir grafismos relativos → absolutos y aplicar ───────────
    titulo_cintillo = plan.get("titulo_cintillo") or f"{body.cintillo_label} — {body.titular[:50]}"

    # Use plan-based grafismos whenever the timeline was generated (regardless of
    # whether titulo_cintillo came back empty — the fallback title is already set above)
    if plan_segmentos:
        elementos = _plan_to_grafismos(plan_segmentos, base_segs, duration)
        # Ensure at least one cintillo element exists (LLM sometimes omits intro grafismo)
        has_cintillo = any(e.tipo == "titular" for e in elementos)
        if not has_cintillo:
            elementos.insert(0, GrafismoEl(
                tipo="titular",
                texto_principal=titulo_cintillo,
                tiempo_inicio=2.0,
                duracion=13.0,
            ))
    else:
        dur_int = max(30, int(duration))
        elementos = [
            GrafismoEl(tipo="titular", texto_principal=titulo_cintillo, tiempo_inicio=2.0, duracion=13.0),
            GrafismoEl(tipo="titular", texto_principal=titulo_cintillo,
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
        except Exception as exc:
            import traceback as _tb
            warning_grafismo = str(exc)
            logger.error("Grafismo application failed: %s\n%s", exc, _tb.format_exc())

    # ── PASO 4: Sintetizar locución y mezclar con el vídeo ────────────────────
    if quiere_locucion and locucion_text:
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

    all_trans_prev = await asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources])
    all_segs = [t[0] for t in all_trans_prev]
    all_words_prev = [t[1] for t in all_trans_prev]
    if all(not s for s in all_segs):
        raise HTTPException(status_code=422, detail="Could not transcribe any source video")

    transcripciones_fmt = _format_transcripts(sources, all_segs)
    max_available_prev = max(
        (s["end"] for segs in all_segs for s in segs),
        default=float(body.duracion_objetivo),
    )
    duracion_efectiva_prev = min(body.duracion_objetivo, int(max_available_prev * 0.9))

    try:
        plan = await generar_timeline_narrativo(
            transcripciones=transcripciones_fmt,
            fuentes=body.fuentes,
            titular=body.titular,
            entradilla=body.entradilla,
            tipo_pieza=body.tipo_pieza,
            duracion_objetivo=duracion_efectiva_prev,
            cintillo_label=body.cintillo_label,
            requiere_locucion=body.incluir_locucion,
            duracion_material=int(max_available_prev),
            all_words=all_words_prev,
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


# ---------------------------------------------------------------------------
# Schemas — analizar material
# ---------------------------------------------------------------------------

class AnalizarMaterialRequest(BaseModel):
    fuentes: list[str] = []   # storage keys already in the system
    url: str = ""             # optional: URL to download + analyze in one call


# ---------------------------------------------------------------------------
# Route — analizar material sin título ni entradilla
# ---------------------------------------------------------------------------

@router.post("/analizar-material")
async def analizar_material(
    body: AnalizarMaterialRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """Transcribe and visually analyze raw video, then return editorial metadata.

    Accepts storage keys, a URL (downloaded on the fly), or both.
    Returns: titular, entradilla, cuerpo, tipo_pieza_sugerido, personas, etc.
    The frontend can use these fields to pre-fill the generation form or pass them
    directly to /pieza-emision without any user input.
    """
    if not body.fuentes and not body.url:
        raise HTTPException(status_code=422, detail="Proporciona 'fuentes' o 'url'")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    fuentes = list(body.fuentes)
    url_storage_key: str | None = None

    # ── Descargar URL si se proporcionó ──────────────────────────────────────
    if body.url:
        if not _is_direct_url(body.url) and not shutil.which("yt-dlp"):
            raise HTTPException(status_code=500, detail="yt-dlp no está instalado")

        videos_dir = _STORAGE_BASE / "videos"
        videos_dir.mkdir(parents=True, exist_ok=True)

        if _is_direct_url(body.url):
            out_path = videos_dir / f"analisis_{uuid4().hex[:8]}.mp4"
            try:
                await _ffmpeg_download(body.url, out_path, ffmpeg)
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=f"Descarga fallida: {exc}")
        else:
            try:
                info = await _ytdlp_get_info(body.url)
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=f"No se pudo leer la URL: {exc}")

            safe = _sanitize_filename(info.get("title", "video"))
            out_path = videos_dir / f"{safe}.mp4"
            if out_path.exists():
                out_path = videos_dir / f"{safe}_{uuid4().hex[:6]}.mp4"
            try:
                await _ytdlp_download(body.url, out_path)
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=f"Descarga fallida: {exc}")

        if not out_path.exists():
            raise HTTPException(status_code=500, detail="La descarga finalizó pero no se encontró el fichero")

        url_storage_key = f"videos/{out_path.name}"
        fuentes.append(url_storage_key)

    # ── Resolver paths (storage keys + http(s) URLs) ──────────────────────────
    sources = await _resolve_fuentes(fuentes, ffmpeg)

    # ── Transcripción + análisis visual en paralelo ───────────────────────────
    source_durations = await asyncio.gather(*[_get_duration(src) for src in sources])

    all_trans_anal, all_visual = await asyncio.gather(
        asyncio.gather(*[_transcribe_source(src, ffmpeg) for src in sources]),
        asyncio.gather(*[
            analyze_video_visually(src, ffmpeg, dur)
            for src, dur in zip(sources, source_durations)
        ]),
    )
    all_segs = [t[0] for t in all_trans_anal]

    if all(not segs for segs in all_segs):
        raise HTTPException(status_code=422, detail="No se pudo transcribir ninguna fuente")

    transcripciones_fmt = _format_transcripts(sources, all_segs)
    max_available = max(
        (s["end"] for segs in all_segs for s in segs),
        default=0.0,
    )

    # ── Formatear contexto visual ─────────────────────────────────────────────
    source_names = [f.split("/")[-1] for f in fuentes]
    contexto_visual = _format_visual_context(list(all_visual), source_names)

    # ── LLM: generar metadatos editoriales ────────────────────────────────────
    prompt = _PROMPT_ANALIZAR.format(
        transcripciones=transcripciones_fmt,
        contexto_visual=f"\n{contexto_visual}\n" if contexto_visual else "",
    )
    llm = get_llm_provider()
    try:
        response = await llm.generate(
            system=_SYSTEM_ANALIZAR,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500,
        )
        analisis = _parse_llm_json(response.text.strip())
    except Exception as exc:
        logger.error("LLM material analysis failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Análisis LLM fallido: {exc}")

    return {
        "ok": True,
        "titular": analisis.get("titular", ""),
        "entradilla": analisis.get("entradilla", ""),
        "cuerpo": analisis.get("cuerpo", ""),
        "tipo_pieza_sugerido": analisis.get("tipo_pieza_sugerido", "cola"),
        "duracion_sugerida": int(analisis.get("duracion_sugerida", 60)),
        "cintillo_label": analisis.get("cintillo_label", "ÚLTIMA HORA"),
        "personas_detectadas": analisis.get("personas_detectadas", []),
        "temas": analisis.get("temas", []),
        "tono": analisis.get("tono", "informativo"),
        "requiere_locucion": bool(analisis.get("requiere_locucion", False)),
        "fuentes": fuentes,
        "url_storage_key": url_storage_key,
        "duracion_material": round(max_available, 1),
    }
