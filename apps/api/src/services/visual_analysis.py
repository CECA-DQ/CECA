"""Visual frame analysis for TV news production.

Extracts key frames and uses a vision LLM to detect speakers, scene types,
and on-screen text. The result feeds the narrative timeline so it can
generate content-accurate rotulos and grafismos.
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from src.adapters.llm.claude import ClaudeProvider
from src.config import settings

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Eres un asistente de producción de informativos de televisión. "
    "Analizas fotogramas de vídeo para identificar quién aparece en pantalla, "
    "el tipo de plano y cualquier texto visible. "
    "Respondes SOLO con JSON válido, sin markdown."
)

_USER_TEMPLATE = """Analiza estos {n_frames} fotogramas extraídos del vídeo, uno cada {intervalo} segundos aproximadamente.

Para cada fotograma indica:
- segundo: timestamp aproximado en el vídeo
- tipo_plano: "declarante" (persona hablando a cámara), "broll" (imágenes de recurso), "presentador", "grafico"
- personas: solo si reconoces CLARAMENTE a alguien (personalidad pública conocida). Si no, lista vacía.
- texto_visible: cualquier rótulo, cartel o texto legible en la imagen (vacío si no hay nada)
- descripcion: una frase breve de lo que se ve

REGLA FUNDAMENTAL: Nunca inventes nombres. Solo añade nombre y cargo si estás absolutamente seguro de quién es la persona.

Responde ÚNICAMENTE con este JSON:
{{
  "fotogramas": [
    {{
      "segundo": 0,
      "tipo_plano": "declarante",
      "personas": [{{"nombre": "Nombre Real si lo conoces", "cargo": "Cargo o institución"}}],
      "texto_visible": "",
      "descripcion": "descripción breve de la escena"
    }}
  ],
  "personas_principales": [
    {{
      "nombre": "Nombre Real",
      "cargo": "Cargo oficial",
      "segundos_aparicion": [0, 30, 60]
    }}
  ]
}}"""


async def _extract_frames(
    video_path: Path,
    ffmpeg: str,
    tmpdir: Path,
    n_frames: int,
    duration: float,
) -> list[tuple[float, bytes]]:
    interval = max(2.0, duration / n_frames)
    out_pattern = str(tmpdir / "f_%04d.jpg")
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval:.2f},scale=640:360",
        "-frames:v", str(n_frames),
        "-q:v", "4",
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


async def analyze_video_visually(
    video_path: Path,
    ffmpeg: str,
    duration: float,
    max_frames: int = 8,
) -> dict:
    """Extract frames and analyze with vision LLM.

    Returns a dict with 'fotogramas' and 'personas_principales'.
    Never raises — returns empty result on any failure.
    """
    if duration <= 0:
        return {"fotogramas": [], "personas_principales": []}

    with tempfile.TemporaryDirectory() as tmpdir:
        frames = await _extract_frames(
            video_path, ffmpeg, Path(tmpdir), max_frames, duration
        )

    if not frames:
        logger.warning("No frames extracted from %s", video_path.name)
        return {"fotogramas": [], "personas_principales": []}

    interval = max(2.0, duration / max_frames)
    images = [img for _, img in frames]

    prompt = _USER_TEMPLATE.format(n_frames=len(frames), intervalo=int(interval))

    try:
        if not settings.anthropic_api_key:
            logger.warning("ANTHROPIC_API_KEY not set — visual analysis unavailable")
            return {"fotogramas": [], "personas_principales": []}
        llm = ClaudeProvider(
            api_key=settings.anthropic_api_key,
            default_model="claude-sonnet-4-6",
        )
        response = await llm.generate_with_vision(
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            images=images,
        )
        raw = response.text.strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON in vision response")
        result = json.loads(raw[start:end])

        # Stamp actual timestamps onto returned frames
        for i, frame in enumerate(result.get("fotogramas", [])):
            if i < len(frames):
                frame["segundo"] = frames[i][0]

        logger.info(
            "Visual analysis: %d frames, %d people detected in %s",
            len(result.get("fotogramas", [])),
            len(result.get("personas_principales", [])),
            video_path.name,
        )
        return result

    except NotImplementedError:
        logger.warning("Vision not supported by current LLM provider — skipping visual analysis")
        return {"fotogramas": [], "personas_principales": []}
    except Exception as exc:
        logger.warning("Visual analysis failed for %s: %s", video_path.name, exc)
        return {"fotogramas": [], "personas_principales": []}
