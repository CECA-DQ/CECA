"""Módulo 2: Generador de highlights — extrae momentos clave de un vídeo largo."""

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.adapters.llm.factory import get_llm_provider
from src.core.auth import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/highlights", tags=["highlights"])

_STORAGE_BASE = Path("data/storage")

_PROMPT_HIGHLIGHTS = """Eres un editor de vídeo deportivo y periodístico experto.
Se te proporciona una muestra representativa de la transcripción de un vídeo con timestamps.
El contenido es: {tipo_contenido}

Selecciona los segmentos más importantes para un resumen de {duracion_objetivo} segundos.
Criterio de selección: {criterio}

Para deportes prioriza: goles/puntos, momentos de tensión, celebraciones, jugadas clave.
Para ruedas de prensa prioriza: declaraciones con datos concretos, momentos de tensión, anuncios.

REGLAS DE MONTAJE OBLIGATORIAS:
- Cada corte debe durar entre 5 y 15 segundos. No selecciones fragmentos más largos.
- Distribuye los segmentos a lo largo de TODO el vídeo. No agrupes selecciones al principio.
- Elige momentos de distintas partes del vídeo para dar variedad y ritmo.

TRANSCRIPCIÓN CON TIMESTAMPS (muestra representativa de todo el vídeo):
{transcripcion}

Responde ÚNICAMENTE con JSON válido, sin markdown ni explicaciones:
{{
  "segmentos_seleccionados": [
    {{
      "tiempo_inicio": 45.2,
      "tiempo_fin": 58.7,
      "motivo": "Punto de break decisivo",
      "importancia": 10
    }}
  ],
  "titulo_resumen": "...",
  "descripcion_breve": "..."
}}
Ordena por importancia descendente. No superes {duracion_objetivo} segundos en total."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class HighlightsRequest(BaseModel):
    fuente: str                          # storage key del vídeo
    tipo_contenido: str = "evento"       # "partido_futbol" | "tenis" | "rueda_prensa" | "evento"
    duracion_objetivo: int = Field(default=180, ge=30, le=600)
    criterio: str = "momentos_clave"     # "momentos_clave" | "declaraciones" | "ambos"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(key: str) -> Path:
    return (_STORAGE_BASE / key).resolve()


async def _transcribe_full(source: Path, ffmpeg: str) -> list[dict]:
    from src.adapters.stt.factory import get_stt_provider

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        mp3_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", str(source),
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            mp3_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()

        audio_bytes = Path(mp3_path).read_bytes()
        stt = get_stt_provider()
        result = await stt.transcribe(
            audio=audio_bytes,
            language=None,
            with_timestamps=True,
            with_diarization=False,
        )
        return [{"start": s.start, "end": s.end, "text": s.text} for s in result.segments]
    except Exception as exc:
        logger.warning("Transcription failed for %s: %s", source.name, exc)
        return []
    finally:
        Path(mp3_path).unlink(missing_ok=True)


def _format_transcript(segments: list[dict], max_chars: int = 8000) -> str:
    """Format transcript sampling evenly so the LLM sees the full video timeline."""
    lines = [
        f"[{s.get('start', 0):.1f}s-{s.get('end', 0):.1f}s] {s.get('text', '').strip()}"
        for s in segments
    ]
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full
    # Sample every Nth segment to cover the whole timeline within the char budget
    avg_len = len(full) / max(len(lines), 1)
    max_lines = max(1, int(max_chars / avg_len))
    step = max(1, len(lines) // max_lines)
    return "\n".join(lines[i] for i in range(0, len(lines), step))


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


async def _select_highlights(
    transcripcion_fmt: str,
    tipo_contenido: str,
    duracion_objetivo: int,
    criterio: str,
) -> dict:
    llm = get_llm_provider()
    prompt = _PROMPT_HIGHLIGHTS.format(
        tipo_contenido=tipo_contenido,
        duracion_objetivo=duracion_objetivo,
        criterio=criterio,
        transcripcion=transcripcion_fmt,
    )
    response = await llm.generate(
        system="Eres un editor de vídeo experto. Responde solo con JSON válido.",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    return _parse_llm_json(response.text.strip())


async def _compose_highlights(
    source: Path,
    segmentos: list[dict],
    ffmpeg: str,
) -> tuple[Path, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        clips = []

        for i, seg in enumerate(segmentos):
            t0 = seg["tiempo_inicio"]
            t1 = seg["tiempo_fin"]
            clip_path = tmp / f"clip_{i:03d}.mp4"

            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-y",
                "-ss", str(t0), "-to", str(t1),
                "-i", str(source),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-avoid_negative_ts", "make_zero",
                str(clip_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if clip_path.exists():
                clips.append(clip_path)

        if not clips:
            raise RuntimeError("No clips could be extracted")

        concat_txt = tmp / "concat.txt"
        concat_txt.write_text("\n".join(f"file '{c}'" for c in clips))

        output_path = tmp / "highlights.mp4"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[-300:]}")

        out_key = f"output/highlights/{uuid4()}.mp4"
        out_final = (_STORAGE_BASE / out_key).resolve()
        out_final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(output_path), str(out_final))
        return out_final, out_key


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/generar")
async def generar_highlights(
    body: HighlightsRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    source = _resolve(body.fuente)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {body.fuente}")

    logger.info("Generating highlights for %s", source.name)

    segments = await _transcribe_full(source, ffmpeg)
    if not segments:
        raise HTTPException(status_code=422, detail="Could not transcribe video")

    transcript_fmt = _format_transcript(segments)

    try:
        selection = await _select_highlights(
            transcript_fmt, body.tipo_contenido, body.duracion_objetivo, body.criterio
        )
    except ValueError as exc:
        logger.error("LLM selection failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"LLM selection failed: {exc}")

    segs_sel = selection.get("segmentos_seleccionados", [])
    if not segs_sel:
        raise HTTPException(status_code=422, detail="LLM returned no segments")

    try:
        out_path, out_key = await _compose_highlights(source, segs_sel, ffmpeg)
    except RuntimeError as exc:
        logger.error("Highlights composition failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    total_dur = sum(s["tiempo_fin"] - s["tiempo_inicio"] for s in segs_sel)

    return {
        "ok": True,
        "highlights_id": out_key.split("/")[-1].replace(".mp4", ""),
        "video_url": f"/api/highlights/video/{out_key}",
        "duracion_total": round(total_dur, 1),
        "titulo": selection.get("titulo_resumen", ""),
        "descripcion": selection.get("descripcion_breve", ""),
        "momentos": segs_sel,
    }


@router.get("/video/{key:path}")
async def stream_highlights_video(key: str) -> FileResponse:
    path = (_STORAGE_BASE / key).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)
