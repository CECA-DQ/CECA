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

    interval = 5.0   # one frame every 5s — catches speaker changes within a shot
    if max_frames is None:
        max_frames = min(max(4, int(duration / interval)), 16)  # cap at 16 frames

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_frames = await _extract_frames(
            video_path, ffmpeg, Path(tmpdir), interval, max_frames
        )

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
        transcript_text = _get_transcript_window(words or [], ts)
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
                    max_tokens=2000,
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
            raise ValueError("No JSON in Gemini vision response")
        result = json.loads(raw[start:end])

        scored: list[dict] = result.get("frames", [])

        # Stamp actual timestamps (model may have slightly different values)
        for i, frame in enumerate(scored):
            if i < len(raw_frames):
                frame["timestamp_s"] = raw_frames[i][0]

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
