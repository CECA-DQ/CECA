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
from src.routes.montaje import SegmentoMontaje, ensamblar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/montaje", tags=["montaje"])

_STORAGE_BASE = Path("data/storage")

_PIECE_CONFIGS: dict[str, dict] = {
    "cola":       {"duracion_default": 45,  "criterio": "planos de recurso y b-roll sin declaraciones, para narrar encima", "con_locucion": False},
    "total":      {"duracion_default": 20,  "criterio": "solo la declaración más impactante del protagonista", "con_locucion": False},
    "vtr":        {"duracion_default": 120, "criterio": "mezcla de declaraciones clave, b-roll de apoyo y voz en off conectora", "con_locucion": True},
    "nota":       {"duracion_default": 75,  "criterio": "pieza informativa completa, declaración principal más contexto visual", "con_locucion": True},
    "off":        {"duracion_default": 60,  "criterio": "solo imágenes de recurso sin declaraciones, para narrar en off", "con_locucion": True},
    "broll":      {"duracion_default": 30,  "criterio": "imágenes de recurso variadas sin audio relevante", "con_locucion": False},
    "highlights": {"duracion_default": 180, "criterio": "momentos más destacados e impactantes en orden cronológico", "con_locucion": False},
    "promo":      {"duracion_default": 30,  "criterio": "momentos más llamativos para enganchar antes del reportaje", "con_locucion": False},
    "teaser":     {"duracion_default": 15,  "criterio": "el instante más impactante para crear expectativa máxima", "con_locucion": False},
}

_PROMPT_GENERAR = """Eres un editor de televisión experto.
Selecciona segmentos de vídeo para montar una pieza de tipo "{tipo_pieza}".

PIEZA:
- Titular: {titular}
- Entradilla: {entradilla}
- Duración objetivo: {duracion_objetivo} segundos
- Criterio editorial: {criterio}

TRANSCRIPCIONES (fuente_index identifica el vídeo de origen):
{transcripciones}

{instrucciones_locucion}

Responde ÚNICAMENTE con JSON válido:
{{
  "segmentos": [
    {{
      "fuente_index": 0,
      "tiempo_inicio": 5.0,
      "tiempo_fin": 12.0,
      "tipo": "broll",
      "orden": 1,
      "motivo": "Plano de recurso con acción relevante"
    }}
  ],
  "locucion": "Texto completo de voz en off si aplica, o null"
}}
Tipos válidos de segmento: declaracion, broll, recurso, intro, cierre.
No superes {duracion_objetivo} segundos en total."""


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


def _format_transcripts(sources: list[Path], all_segments: list[list[dict]]) -> str:
    parts: list[str] = []
    for idx, (src, segs) in enumerate(zip(sources, all_segments)):
        lines = [f"--- FUENTE {idx} ({src.name}) ---"]
        for s in segs:
            lines.append(f"[{s['start']:.1f}s-{s['end']:.1f}s] {s['text'].strip()}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


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
    prompt = _PROMPT_GENERAR.format(
        tipo_pieza=tipo_pieza,
        titular=titular,
        entradilla=entradilla[:400],
        duracion_objetivo=duracion_objetivo,
        criterio=criterio,
        transcripciones=transcripciones[:7000],
        instrucciones_locucion=instrucciones,
    )
    response = await llm.generate(
        system="Eres un editor de televisión. Responde solo con JSON válido.",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


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
    selection = await _select_segments_llm(
        body.tipo_pieza, body.titular, body.entradilla,
        transcripciones_fmt, duracion, config["criterio"], quiere_locucion,
    )

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
            idx = 0
        segmentos.append(SegmentoMontaje(
            storage_key=body.fuentes[idx],
            tiempo_inicio=float(seg.get("tiempo_inicio", 0)),
            tiempo_fin=float(seg.get("tiempo_fin", 10)),
            tipo=seg.get("tipo", "broll"),
            orden=int(seg.get("orden", len(segmentos))),
        ))

    try:
        video_key, duration = await ensamblar(segmentos, audio_voiceover_key=audio_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "ok": True,
        "video_key": video_key,
        "video_url": f"/api/montaje/video/{video_key}",
        "duracion_total": round(duration, 1),
        "tipo_pieza": body.tipo_pieza,
        "segmentos_montados": len(segmentos),
        "locucion_generada": audio_key is not None,
        "locucion_texto": locucion_text if quiere_locucion else None,
    }
