"""Módulo 3: Cola desde brutos — monta una cola directamente de material sin editar."""

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

router = APIRouter(prefix="/api/cola", tags=["cola"])

_STORAGE_BASE = Path("data/storage")

_PROMPT_SELECCION_PLANOS = """Eres un editor de televisión experto en selección de planos para una cola informativa.

Noticia a cubrir:
TITULAR: {titular}
ENTRADILLA: {entradilla}
CUERPO: {cuerpo}

A continuación tienes una muestra representativa de la transcripción del vídeo bruto con timestamps.
Selecciona los mejores segmentos visuales para montar una cola de {duracion_objetivo} segundos.

Prioriza: planos con acción relevante, declaraciones clave, planos generales para contextualizar.
Evita: silencios largos, planos técnicos (cámara en negro, pruebas de sonido).

REGLAS DE MONTAJE OBLIGATORIAS:
- Cada corte debe durar entre 3 y 10 segundos. No selecciones fragmentos más largos.
- Distribuye los segmentos a lo largo de TODO el vídeo. No agrupes selecciones al principio.
- Varía los tipos de plano para dar ritmo visual.

TRANSCRIPCIÓN (muestra representativa de todo el vídeo):
{transcripcion}

Responde ÚNICAMENTE con JSON válido:
{{
  "segmentos": [
    {{
      "tiempo_inicio": 5.0,
      "tiempo_fin": 12.0,
      "motivo": "Declaración principal del protagonista",
      "tipo_plano": "declaracion"
    }}
  ],
  "instrucciones_montaje": "..."
}}
No superes {duracion_objetivo} segundos en total."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ColaDesdebrutoRequest(BaseModel):
    titular: str
    entradilla: str
    cuerpo: str
    fuente_bruto: str               # storage key del vídeo bruto
    duracion_objetivo: int = Field(default=30, ge=10, le=120)
    incluir_archivo: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(key: str) -> Path:
    return (_STORAGE_BASE / key).resolve()


async def _transcribe_for_cola(source: Path, ffmpeg: str) -> list[dict]:
    """Transcribe the bruto using the configured STT adapter (Groq Whisper)."""
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
        return [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in result.segments
        ]
    except Exception as exc:
        logger.warning("Transcription failed: %s", exc)
        return []
    finally:
        Path(mp3_path).unlink(missing_ok=True)


def _sample_transcript(segments: list[dict], max_chars: int = 6000) -> str:
    """Format transcript sampling evenly so the LLM sees the full video timeline."""
    lines = [
        f"[{s.get('start', 0):.1f}s-{s.get('end', 0):.1f}s] {s.get('text', '').strip()}"
        for s in segments
    ]
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full
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


async def _select_planos(
    titular: str,
    entradilla: str,
    cuerpo: str,
    segments: list[dict],
    duracion_objetivo: int,
) -> dict:
    transcript_fmt = _sample_transcript(segments)
    llm = get_llm_provider()
    prompt = _PROMPT_SELECCION_PLANOS.format(
        titular=titular,
        entradilla=entradilla,
        cuerpo=cuerpo[:500],
        duracion_objetivo=duracion_objetivo,
        transcripcion=transcript_fmt,
    )
    response = await llm.generate(
        system="Eres un editor de televisión. Responde solo con JSON válido.",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1500,
    )
    return _parse_llm_json(response.text.strip())


async def _montar_cola(source: Path, segmentos: list[dict], ffmpeg: str) -> tuple[Path, str]:
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
            raise RuntimeError("No clips extracted")

        concat_txt = tmp / "concat.txt"
        concat_txt.write_text("\n".join(f"file '{c}'" for c in clips))

        output_path = tmp / "cola.mp4"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c", "copy", "-movflags", "+faststart", str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat: {stderr.decode()[-300:]}")

        out_key = f"output/colas/{uuid4()}.mp4"
        out_final = (_STORAGE_BASE / out_key).resolve()
        out_final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(output_path), str(out_final))
        return out_final, out_key


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/desde-bruto")
async def cola_desde_bruto(
    body: ColaDesdebrutoRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")

    source = _resolve(body.fuente_bruto)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Bruto not found: {body.fuente_bruto}")

    logger.info("Generating cola from bruto: %s", source.name)

    segments = await _transcribe_for_cola(source, ffmpeg)
    if not segments:
        raise HTTPException(status_code=422, detail="Could not transcribe bruto")

    try:
        selection = await _select_planos(
            body.titular, body.entradilla, body.cuerpo,
            segments, body.duracion_objetivo,
        )
    except ValueError as exc:
        logger.error("LLM selection failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"LLM selection failed: {exc}")

    segs = selection.get("segmentos", [])
    if not segs:
        raise HTTPException(status_code=422, detail="LLM returned no segments")

    try:
        _, out_key = await _montar_cola(source, segs, ffmpeg)
    except RuntimeError as exc:
        logger.error("Cola composition failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    total = sum(s["tiempo_fin"] - s["tiempo_inicio"] for s in segs)

    return {
        "ok": True,
        "video_key": out_key,
        "video_url": f"/api/cola/video/{out_key}",
        "duracion_total": round(total, 1),
        "segmentos_usados": len(segs),
        "instrucciones_montaje": selection.get("instrucciones_montaje", ""),
    }


@router.get("/video/{key:path}")
async def stream_cola_video(key: str) -> FileResponse:
    path = (_STORAGE_BASE / key).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)
