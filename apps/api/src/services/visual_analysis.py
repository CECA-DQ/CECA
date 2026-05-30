"""Visual frame scoring for TV news production.

Extracts one JPEG frame every 5 seconds and sends ALL frames + transcript
pairs to Gemini Vision in a single call. Each frame receives a journalistic
score (0-10) so the segment selection algorithm can pick the best cuts
without relying on a text-only model guessing what is on screen.

Architecture:
  analyze_video_visually() → list of scored frames (puntuacion 0-10)
  The downstream select_segments() in segment_selection.py uses these scores.
"""

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

from src.adapters.llm.factory import get_visual_analysis_provider
from src.config import settings

logger = logging.getLogger(__name__)

_SYSTEM = """Eres un editor de televisión española analizando material de vídeo \
para seleccionar los mejores momentos periodísticos.

Para CADA fotograma que recibes debes:

1. IDENTIFICAR quién aparece en pantalla y si está hablando:
   - Si reconoces a una persona pública: indica su nombre y cargo
   - Si hay alguien hablando sin identificar: marca como "desconocido"
   - Si es un plano general sin hablante claro: marca como "plano_sala"
   Indicios de que alguien está hablando: boca abierta, postura activa, gesticulando.

2. PUNTUAR el valor periodístico de ese momento (0-10):
   10 = momento único, muy citable, alta carga informativa o emocional.
        Ejemplos: muestra documentos, dice una cifra clave, hay tensión visible,
        confrontación directa, gesto muy expresivo.
   7-9 = declaración relevante, hablante activo y visible, frase completa.
   4-6 = hablante habla pero sin momento especial destacable.
   1-3 = plano de escucha, plano general, persona mirando papeles, sin habla activa.
   0   = nadie habla, plano de sala vacía, momento muerto.

3. DETECTAR si hay texto visible en pantalla (documentos, carteles, pantallas).

Responde SOLO con este JSON, sin texto adicional ni markdown:
{
  "frames": [
    {
      "frame_id": 0,
      "timestamp_s": 0.0,
      "hablante": "nombre o 'desconocido' o 'plano_sala'",
      "hablante_confianza": "alta | media | baja",
      "cargo_inferido": "string o null",
      "puntuacion": 0,
      "razon_puntuacion": "string — máx 10 palabras",
      "texto_visible_en_pantalla": "string o null",
      "es_momento_visual_especial": false
    }
  ]
}"""


async def _extract_frames(
    video_path: Path,
    ffmpeg: str,
    tmpdir: Path,
    interval: float,
    n_frames: int,
) -> list[tuple[float, bytes]]:
    out_pattern = str(tmpdir / "f_%04d.jpg")
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval:.2f},scale=320:180",   # smaller = fewer tokens
        "-frames:v", str(n_frames),
        "-q:v", "5",
        out_pattern,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()

    frames: list[tuple[float, bytes]] = []
    for i, p in enumerate(sorted(tmpdir.glob("f_*.jpg"))):
        frames.append((round(i * interval, 1), p.read_bytes()))
    return frames


async def _extract_frames_at(
    video_path: Path,
    ffmpeg: str,
    tmpdir: Path,
    timestamps: list[float],
    scale: str = "512:288",
) -> list[tuple[float, bytes]]:
    """Extract one JPEG per requested timestamp (content-driven sampling).

    Higher default resolution than the grid path so documents / expressions /
    on-screen text are legible to the scorer. Extractions run concurrently; a
    failed or empty extraction is skipped, not fatal. Output files are prefixed
    c_NNNN to avoid collision with the f_NNNN grid frames. Results are returned
    in input-timestamp order.
    """
    async def _extract_one(i: int, ts: float) -> Path:
        out = tmpdir / f"c_{i:04d}.jpg"
        cmd = [
            ffmpeg, "-y", "-ss", f"{max(0.0, ts):.2f}", "-i", str(video_path),
            "-frames:v", "1", "-vf", f"scale={scale}", "-q:v", "4", str(out),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        return out

    outs = await asyncio.gather(*[_extract_one(i, ts) for i, ts in enumerate(timestamps)])
    frames: list[tuple[float, bytes]] = []
    for ts, out in zip(timestamps, outs):
        if out.exists() and out.stat().st_size > 0:
            frames.append((round(ts, 1), out.read_bytes()))
    return frames


def _get_transcript_window(words: list[dict], t: float, half: float = 3.0) -> str:
    """Return the transcript text in the ±half second window around t."""
    window = [w["word"] for w in words if abs(w.get("start", 0) - t) <= half]
    return " ".join(window) if window else ""


async def analyze_video_visually(
    video_path: Path,
    ffmpeg: str,
    duration: float,
    words: list[dict] | None = None,
    tipo_contenido: str = "informativo",
    tema: str = "",
    max_frames: int | None = None,
    candidate_moments: list[dict] | None = None,
) -> dict:
    """Extract frames and score them for journalistic value using Gemini Vision.

    Returns {"frames": [scored_frame, ...], "personas_principales": [...]}.
    Legacy callers that only use fotogramas/personas_principales still work
    because we populate a fotogramas alias field.
    Never raises — returns empty result on any failure.
    """
    if duration <= 0:
        return {"frames": [], "fotogramas": [], "personas_principales": []}

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — visual analysis unavailable")
        return {"frames": [], "fotogramas": [], "personas_principales": []}

    with tempfile.TemporaryDirectory() as tmpdir:
        if candidate_moments:
            timestamps = [c["timestamp"] for c in candidate_moments]
            raw_frames = await _extract_frames_at(
                video_path, ffmpeg, Path(tmpdir), timestamps
            )
            cand_text = {round(c["timestamp"], 1): c.get("text", "") for c in candidate_moments}
        else:
            if max_frames is None:
                # Cap at 12: Gemini 2.5 Flash uses internal reasoning tokens that eat into
                # the output budget, so 12 frames × ~300 tokens/frame + overhead fits in 8000.
                max_frames = min(max(4, int(duration / 5.0)), 12)
            # Spread frames evenly across the full video duration.
            # For short videos (≤60s) this keeps the natural 5s cadence.
            # For long videos (e.g. 24min) this samples one frame every ~120s
            # instead of only covering the opening 60 seconds.
            interval = max(5.0, duration / max_frames)
            raw_frames = await _extract_frames(
                video_path, ffmpeg, Path(tmpdir), interval, max_frames
            )
            cand_text = {}

    if not raw_frames:
        logger.warning("No frames extracted from %s", video_path.name)
        return {"frames": [], "fotogramas": [], "personas_principales": []}

    # Build interleaved content: text header + (text label + image) per frame
    content: list[dict] = []
    content.append({
        "type": "text",
        "text": (
            f"Vídeo de {duration:.0f}s. "
            f"Contexto: {tipo_contenido} — {tema or 'sin tema especificado'}.\n"
            "Analiza cada fotograma y puntúa su valor periodístico.\n"
        ),
    })

    for i, (ts, img_bytes) in enumerate(raw_frames):
        transcript_text = (cand_text.get(ts) or _get_transcript_window(words or [], ts)) if cand_text else _get_transcript_window(words or [], ts)
        content.append({
            "type": "text",
            "text": (
                f"\n--- FOTOGRAMA {i} — t={ts}s ---\n"
                f'Transcripción en este momento: "{transcript_text}"\n'
            ),
        })
        content.append({"type": "image", "data": img_bytes, "mime_type": "image/jpeg"})

    try:
        provider = get_visual_analysis_provider()

        # Retry once on 429 — parse the retry delay from the error message
        for attempt in range(2):
            try:
                response = await provider.generate_with_interleaved_content(
                    system=_SYSTEM,
                    content=content,
                    temperature=0.1,
                    max_tokens=8000,  # Gemini 2.5 Flash uses reasoning tokens internally;
                                      # 8000 gives headroom for 12 frames + thinking overhead
                )
                break
            except Exception as exc:
                msg = str(exc)
                if "429" in msg and attempt == 0:
                    wait = 60.0
                    m = re.search(r"retryDelay.*?(\d+)s", msg)
                    if m:
                        wait = min(float(m.group(1)) + 5, 120)
                    logger.warning("Gemini 429 — waiting %.0fs before retry", wait)
                    await asyncio.sleep(wait)
                else:
                    raise
        raw = response.text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning("No JSON in Gemini vision response. First 300 chars: %s", raw[:300])
            raise ValueError("No JSON in Gemini vision response")
        result = json.loads(raw[start:end])

        scored: list[dict] = result.get("frames", [])

        # Map each scored frame back to the timestamp of the frame we actually
        # sent, by frame_id — robust to the model dropping/reordering frames.
        # The model legitimately returns fewer frames than sent (it skips
        # low-value ones), so a smaller count is expected, not an error.
        ts_by_id = {i: raw_frames[i][0] for i in range(len(raw_frames))}
        aligned: list[dict] = []
        seen_ids: set[int] = set()
        for frame in scored:
            fid = frame.get("frame_id")
            if isinstance(fid, int) and fid in ts_by_id and fid not in seen_ids:
                frame["timestamp_s"] = ts_by_id[fid]
                aligned.append(frame)
                seen_ids.add(fid)
            else:
                logger.debug("Skipping frame with unmappable/duplicate frame_id=%r", fid)
        scored = aligned
        if len(scored) != len(raw_frames):
            logger.debug("Scored %d of %d sent frames", len(scored), len(raw_frames))

        # Build personas_principales from high-confidence identifications
        known: dict[str, dict] = {}
        for f in scored:
            name = f.get("hablante", "")
            if name in ("desconocido", "plano_sala", ""):
                continue
            conf = f.get("hablante_confianza", "baja")
            if conf not in ("alta", "media"):
                continue
            if name not in known:
                known[name] = {
                    "nombre": name,
                    "cargo": f.get("cargo_inferido") or "",
                    "segundos_aparicion": [],
                }
            known[name]["segundos_aparicion"].append(f["timestamp_s"])

        personas = list(known.values())

        # fotogramas alias for backward compatibility with callers that check
        # v.get("fotogramas") — maps to scored frames with legacy field names
        fotogramas = [
            {
                "segundo": f["timestamp_s"],
                "tipo_plano": "declarante" if f.get("puntuacion", 0) >= 5 else "broll",
                "personas": (
                    [{"nombre": f["hablante"], "cargo": f.get("cargo_inferido", "")}]
                    if f.get("hablante") not in ("desconocido", "plano_sala", "")
                    else []
                ),
                "texto_visible": f.get("texto_visible_en_pantalla") or "",
                "descripcion": f.get("razon_puntuacion", ""),
                "puntuacion": f.get("puntuacion", 0),
            }
            for f in scored
        ]

        logger.info(
            "Visual scoring: %d frames, top score=%s, %d speakers identified — %s",
            len(scored),
            max((f.get("puntuacion", 0) for f in scored), default=0),
            len(personas),
            video_path.name,
        )
        return {"frames": scored, "fotogramas": fotogramas, "personas_principales": personas}

    except Exception as exc:
        logger.warning("Visual analysis failed for %s: %s", video_path.name, exc)
        return {"frames": [], "fotogramas": [], "personas_principales": []}
