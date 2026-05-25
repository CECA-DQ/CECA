"""Módulo 1: Indexador semántico de archivo de vídeo e imagen."""

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.llm.factory import get_llm_provider
from src.adapters.vector.factory import get_vector_adapter
from src.config import settings
from src.core.auth import get_tenant_id
from src.db.session import tenant_session
from src.models.segmento_archivo import SegmentoArchivo, TipoSegmento

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/archivo", tags=["archivo"])

_STORAGE_BASE = Path("data/storage")
_THUMBNAILS_DIR = Path("data/storage/thumbnails")
_CHUNK_SECONDS = 8  # segment length for bruto indexing

_PROMPT_DESCRIPCION_VISUAL = """Eres un documentalista de televisión.
Describe en máximo 30 palabras qué se ve en esta imagen de vídeo,
enfocándote en: quién aparece, qué está haciendo, dónde está,
y qué elementos visuales destacan. Sin adornos, descripción directa."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IndexarRequest(BaseModel):
    tipo: str          # "video_local" | "youtube" | "imagen"
    fuente: str        # storage key or YouTube URL
    nombre: str


class IngestUrlRequest(BaseModel):
    url: str
    nombre: str = ""                    # auto-detected from metadata if empty
    indexar_automaticamente: bool = True


class BuscarRequest(BaseModel):
    query: str
    tipo_filtro: str = "todos"  # "clip" | "segmento_bruto" | "imagen" | "todos"
    max_resultados: int = 12
    duracion_min: float = 0
    duracion_max: float = 9999


# ---------------------------------------------------------------------------
# Session dependency
# ---------------------------------------------------------------------------

async def _get_session(tenant_id: str = Depends(get_tenant_id)):
    async with tenant_session(tenant_id) as session:
        yield session


# ---------------------------------------------------------------------------
# Core indexing logic
# ---------------------------------------------------------------------------

def _resolve(key: str) -> Path:
    return (_STORAGE_BASE / key).resolve()


def _require_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH")
    return p


async def _extract_thumbnail(source: Path, time_s: float, out_path: Path, ffmpeg: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y",
        "-ss", str(time_s),
        "-i", str(source),
        "-vframes", "1",
        "-q:v", "3",
        str(out_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return proc.returncode == 0 and out_path.exists()


async def _transcribe_chunk(audio_bytes: bytes) -> str:
    """Transcribe audio bytes using Groq Whisper."""
    from src.adapters.stt.factory import get_stt_provider
    stt = get_stt_provider()
    try:
        result = await stt.transcribe(
            audio=audio_bytes,
            language=None,
            with_timestamps=False,
            with_diarization=False,
        )
        return " ".join(s.text for s in result.segments)
    except Exception as exc:
        logger.warning("Transcription failed for chunk: %s", exc)
        return ""


async def _describe_frame(image_bytes: bytes) -> str:
    """Ask the LLM to describe what's in a video frame."""
    llm = get_llm_provider()
    try:
        response = await llm.generate_with_vision(
            system=_PROMPT_DESCRIPCION_VISUAL,
            messages=[{"role": "user", "content": "Describe esta imagen."}],
            images=[image_bytes],
        )
        return response.text.strip()
    except Exception as exc:
        logger.warning("Visual description failed: %s", exc)
        return ""


async def _extract_audio_chunk(source: Path, t_start: float, duration: float, ffmpeg: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out = tmp.name
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y",
            "-ss", str(t_start), "-t", str(duration),
            "-i", str(source),
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            out,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        data = Path(out).read_bytes() if Path(out).exists() else b""
    finally:
        Path(out).unlink(missing_ok=True)
    return data


async def _get_video_duration(source: Path, ffprobe: str) -> float:
    import json as _json
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        data = _json.loads(stdout)
        return float(data.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


async def _index_video(
    source: Path,
    nombre: str,
    tipo: TipoSegmento,
    tenant_id: str,
    session: AsyncSession,
) -> int:
    ffmpeg = _require_ffmpeg()
    ffprobe = shutil.which("ffprobe") or ffmpeg.replace("ffmpeg", "ffprobe")
    _THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    vector = get_vector_adapter()

    duration = await _get_video_duration(source, ffprobe)
    if duration == 0:
        raise HTTPException(status_code=422, detail="Could not read video duration")

    segmentos_creados = 0
    t = 0.0

    while t < duration:
        t_end = min(t + _CHUNK_SECONDS, duration)
        chunk_duration = t_end - t
        seg_id = str(uuid4())

        # Thumbnail at midpoint of chunk
        thumb_key = f"thumbnails/{seg_id}.jpg"
        thumb_path = _THUMBNAILS_DIR / f"{seg_id}.jpg"
        thumb_ok = await _extract_thumbnail(source, t + chunk_duration / 2, thumb_path, ffmpeg)

        # Transcription
        audio_bytes = await _extract_audio_chunk(source, t, chunk_duration, ffmpeg)
        transcripcion = await _transcribe_chunk(audio_bytes) if audio_bytes else ""

        # Visual description from thumbnail
        descripcion = ""
        if thumb_ok:
            descripcion = await _describe_frame(thumb_path.read_bytes())

        # Tags: combine words from both texts
        texto_combinado = f"{transcripcion} {descripcion}".strip()
        etiquetas = list({w.lower() for w in texto_combinado.split() if len(w) > 4})[:10]

        # Store in DB
        segmento = SegmentoArchivo(
            id=uuid4(),
            tenant_id=tenant_id,
            tipo=tipo,
            nombre=f"{nombre} [{t:.0f}s-{t_end:.0f}s]",
            archivo_origen=str(source),
            tiempo_inicio=round(t, 2),
            tiempo_fin=round(t_end, 2),
            duracion=round(chunk_duration, 2),
            thumbnail_key=thumb_key if thumb_ok else None,
            transcripcion=transcripcion or None,
            descripcion_visual=descripcion or None,
            etiquetas=etiquetas or None,
        )
        session.add(segmento)

        # Index in vector store for semantic search
        if texto_combinado:
            await vector.index(
                id=str(segmento.id),
                text=texto_combinado,
                tenant_id=tenant_id,
                metadata={
                    "tipo": tipo.value,
                    "nombre": segmento.nombre,
                    "archivo_origen": str(source),
                    "tiempo_inicio": t,
                    "tiempo_fin": t_end,
                    "duracion": chunk_duration,
                    "thumbnail_key": thumb_key if thumb_ok else "",
                },
            )

        segmentos_creados += 1
        t = t_end

    await session.commit()
    return segmentos_creados


async def _index_imagen(
    source: Path,
    nombre: str,
    tenant_id: str,
    session: AsyncSession,
) -> int:
    _THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    vector = get_vector_adapter()
    seg_id = str(uuid4())

    # Use image itself as thumbnail (copy to thumbnails dir)
    thumb_key = f"thumbnails/{seg_id}.jpg"
    thumb_path = _THUMBNAILS_DIR / f"{seg_id}.jpg"

    import shutil as _shutil
    try:
        from PIL import Image
        img = Image.open(source).convert("RGB")
        img.thumbnail((640, 360))
        img.save(str(thumb_path), "JPEG", quality=85)
    except Exception:
        _shutil.copy2(str(source), str(thumb_path))

    descripcion = await _describe_frame(source.read_bytes())
    etiquetas = list({w.lower() for w in descripcion.split() if len(w) > 4})[:10]

    segmento = SegmentoArchivo(
        id=uuid4(),
        tenant_id=tenant_id,
        tipo=TipoSegmento.imagen,
        nombre=nombre,
        archivo_origen=str(source),
        tiempo_inicio=0.0,
        tiempo_fin=0.0,
        duracion=0.0,
        thumbnail_key=thumb_key,
        transcripcion=None,
        descripcion_visual=descripcion or None,
        etiquetas=etiquetas or None,
    )
    session.add(segmento)

    if descripcion:
        await vector.index(
            id=str(segmento.id),
            text=descripcion,
            tenant_id=tenant_id,
            metadata={
                "tipo": "imagen",
                "nombre": nombre,
                "archivo_origen": str(source),
                "tiempo_inicio": 0,
                "tiempo_fin": 0,
                "duracion": 0,
                "thumbnail_key": thumb_key,
            },
        )

    await session.commit()
    return 1


# ---------------------------------------------------------------------------
# URL ingest helpers
# ---------------------------------------------------------------------------

_DIRECT_MEDIA_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m3u8", ".ts", ".mpd"}


def _sanitize_filename(s: str, max_len: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "_", s).strip("_-")
    return s[:max_len] or "video"


def _is_direct_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _DIRECT_MEDIA_EXTS)


def _ytdlp_cmd() -> list[str]:
    """Base yt-dlp invocation using the project venv Python (always found).

    Adds --js-runtimes pointing to the system Node.js when available, and
    allows the EJS solver to be fetched from GitHub.  This combination
    handles YouTube's n-challenge and signature-challenge restrictions.
    """
    cmd = [sys.executable, "-m", "yt_dlp"]
    node = shutil.which("node")
    if node:
        cmd += ["--js-runtimes", f"node:{node}", "--remote-components", "ejs:github"]
    return cmd


def _ytdlp_cookies_args() -> list[str]:
    """Return the right cookies argument: file if it exists, browser otherwise."""
    if settings.ytdlp_cookies_path:
        return ["--cookies", str(settings.ytdlp_cookies_path)]
    return ["--cookies-from-browser", "brave"]


async def _ytdlp_get_info(url: str) -> dict:
    """Fetch video metadata from a URL without downloading (yt-dlp --dump-json)."""
    cmd = _ytdlp_cmd() + ["--dump-json", "--no-playlist"] + _ytdlp_cookies_args()
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-500:])
    return json.loads(stdout.decode())


async def _ytdlp_download(url: str, out_path: Path) -> None:
    """Download a URL to out_path as MP4 using yt-dlp."""
    cmd = _ytdlp_cmd() + [
        "--no-playlist",
        "--format",
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
        "/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "--output", str(out_path),
        "--no-progress",
    ] + _ytdlp_cookies_args()
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-500:])


async def _ffmpeg_download(url: str, out_path: Path, ffmpeg: str) -> None:
    """Download a direct media URL (HLS/DASH/MP4) with ffmpeg -c copy."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", url,
        "-c", "copy", "-movflags", "+faststart",
        str(out_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode()[-400:])


async def _get_resolution(path: Path, ffprobe: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    parts = stdout.decode().strip().split(",")
    return f"{parts[0]}x{parts[1]}" if len(parts) == 2 else "desconocida"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/indexar")
async def indexar(
    body: IndexarRequest,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    if body.tipo == "youtube":
        # Download via yt-dlp first
        from src.adapters.stt.audio import extract_audio_from_url  # reuse existing
        raise HTTPException(
            status_code=422,
            detail="YouTube indexing: download the video first and use tipo=video_local"
        )

    source = _resolve(body.fuente)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.fuente}")

    ext = source.suffix.lower()
    imagen_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    if ext in imagen_exts or body.tipo == "imagen":
        count = await _index_imagen(source, body.nombre, tenant_id, session)
        return {"ok": True, "segmentos_indexados": count, "nombre": body.nombre}

    tipo = TipoSegmento.clip if body.tipo == "video_local" else TipoSegmento.segmento_bruto
    count = await _index_video(source, body.nombre, tipo, tenant_id, session)

    return {
        "ok": True,
        "segmentos_indexados": count,
        "duracion_total": count * _CHUNK_SECONDS,
        "nombre": body.nombre,
    }


@router.post("/upload")
async def upload_video(
    file: UploadFile = File(...),
    nombre: str = Form(""),
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """Upload a video file, save to storage, and index it automatically."""
    allowed = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".mts", ".m4v"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=422, detail=f"Unsupported file type '{ext}'. Use: {', '.join(allowed)}")

    videos_dir = _STORAGE_BASE / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / (file.filename or f"upload_{uuid4()}{ext}")

    content = await file.read()
    dest.write_bytes(content)

    storage_key = f"videos/{dest.name}"
    display_name = nombre.strip() or dest.stem
    count = await _index_video(dest.resolve(), display_name, TipoSegmento.clip, tenant_id, session)

    return {
        "ok": True,
        "storage_key": storage_key,
        "nombre": display_name,
        "segmentos_indexados": count,
    }


@router.post("/ingest-url")
async def ingest_url(
    body: IngestUrlRequest,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """Download a video from any URL (YouTube, X, HLS, direct MP4…),
    save to storage, and optionally run semantic indexing."""
    ffmpeg = _require_ffmpeg()
    ffprobe = shutil.which("ffprobe") or ffmpeg.replace("ffmpeg", "ffprobe")
    videos_dir = _STORAGE_BASE / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    titulo_detectado: str = ""
    fuente: str = "direct"

    if _is_direct_url(body.url):
        # ── Direct media URL (HLS / DASH / plain MP4) ─────────────────────
        uid = uuid4().hex[:8]
        out_path = videos_dir / f"ingesta_{uid}.mp4"
        try:
            await _ffmpeg_download(body.url, out_path, ffmpeg)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=f"ffmpeg download failed: {exc}")
        titulo_detectado = body.nombre.strip() or out_path.stem

    else:
        # ── yt-dlp (YouTube, X, Vimeo, news sites, …) ─────────────────────
        if not shutil.which("yt-dlp"):
            raise HTTPException(
                status_code=500,
                detail="yt-dlp no está instalado. Ejecuta: pip install yt-dlp",
            )

        # Fetch metadata first (no download, fast)
        try:
            info = await _ytdlp_get_info(body.url)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"No se pudo obtener información del vídeo: {exc}",
            )

        titulo_detectado = info.get("title", "video")
        fuente = info.get("extractor_key", "yt-dlp").lower()
        safe_name = _sanitize_filename(titulo_detectado)
        out_path = videos_dir / f"{safe_name}.mp4"

        # Avoid collision with existing file
        if out_path.exists():
            out_path = videos_dir / f"{safe_name}_{uuid4().hex[:6]}.mp4"

        try:
            await _ytdlp_download(body.url, out_path)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Descarga fallida: {exc}",
            )

    if not out_path.exists():
        raise HTTPException(status_code=500, detail="La descarga finalizó pero no se encontró el fichero")

    storage_key = f"videos/{out_path.name}"
    nombre = body.nombre.strip() or titulo_detectado or out_path.stem
    duracion = await _get_video_duration(out_path, ffprobe)
    resolucion = await _get_resolution(out_path, ffprobe)

    segmentos_indexados = 0
    if body.indexar_automaticamente:
        try:
            segmentos_indexados = await _index_video(
                out_path, nombre, TipoSegmento.clip, tenant_id, session,
            )
        except Exception as exc:
            logger.warning("Auto-indexing failed for %s: %s", out_path.name, exc)

    logger.info(
        "ingest-url: %s → %s (%.1fs, %s, fuente=%s)",
        body.url[:80], storage_key, duracion, resolucion, fuente,
    )

    return {
        "ok": True,
        "storage_key": storage_key,
        "nombre": nombre,
        "titulo_detectado": titulo_detectado,
        "duracion": round(duracion, 1),
        "resolucion": resolucion,
        "segmentos_indexados": segmentos_indexados,
        "fuente": fuente,
    }


@router.post("/buscar")
async def buscar(
    body: BuscarRequest,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    vector = get_vector_adapter()
    resultados_vector = await vector.search(
        query=body.query,
        tenant_id=tenant_id,
        limit=body.max_resultados,
    )

    # Enrich results with DB data
    ids = [r.id for r in resultados_vector]
    if not ids:
        return {"resultados": [], "total": 0}

    rows = list(await session.scalars(
        select(SegmentoArchivo).where(
            SegmentoArchivo.id.in_(ids),
            SegmentoArchivo.tenant_id == tenant_id,
        )
    ))
    row_map = {str(r.id): r for r in rows}

    output = []
    for vr in resultados_vector:
        row = row_map.get(vr.id)
        if row is None:
            continue
        if body.tipo_filtro != "todos" and row.tipo.value != body.tipo_filtro:
            continue
        if row.duracion < body.duracion_min or row.duracion > body.duracion_max:
            continue
        output.append({
            "id": str(row.id),
            "score": round(vr.score, 4),
            "thumbnail_url": f"/api/archivo/thumbnail/{row.thumbnail_key}" if row.thumbnail_key else None,
            "descripcion": row.descripcion_visual or "",
            "transcripcion": row.transcripcion or "",
            "archivo_origen": row.archivo_origen,
            "nombre": row.nombre,
            "tiempo_inicio": row.tiempo_inicio,
            "tiempo_fin": row.tiempo_fin,
            "duracion": row.duracion,
            "tipo": row.tipo.value,
            "etiquetas": row.etiquetas or [],
        })

    return {"resultados": output, "total": len(output)}


@router.get("/thumbnail/{key:path}")
async def serve_thumbnail(key: str) -> Response:
    path = _resolve(key)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    suffix = path.suffix.lower()
    media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return Response(content=path.read_bytes(), media_type=media_type)


@router.get("/clip/{segment_id}")
async def extraer_clip(
    segment_id: str,
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> Response:
    from uuid import UUID as _UUID
    try:
        uid = _UUID(segment_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid segment ID")

    row = await session.scalar(
        select(SegmentoArchivo).where(
            SegmentoArchivo.id == uid,
            SegmentoArchivo.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Segment not found")

    ffmpeg = _require_ffmpeg()
    source = Path(row.archivo_origen)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    output_key = f"output/clips/{segment_id}.mp4"
    output_path = _resolve(output_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not output_path.exists():
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y",
            "-ss", str(row.tiempo_inicio),
            "-t", str(row.duracion),
            "-i", str(source),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg error: {stderr.decode()[-200:]}")

    return Response(
        content=output_path.read_bytes(),
        media_type="video/mp4",
        headers={"Content-Disposition": f"inline; filename=\"clip_{segment_id}.mp4\""},
    )


_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".mts", ".m4v"}


@router.get("/videos")
async def listar_videos(
    tenant_id: str = Depends(get_tenant_id),
    session: AsyncSession = Depends(_get_session),
) -> dict:
    """List all video files available in storage, with thumbnails from indexed segments."""
    videos_dir = _STORAGE_BASE / "videos"
    if not videos_dir.exists():
        return {"videos": []}

    files = [f for f in videos_dir.iterdir() if f.suffix.lower() in _VIDEO_EXTS]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    # One DB query to find the first indexed thumbnail per source file
    thumb_rows = await session.execute(
        select(SegmentoArchivo.archivo_origen, SegmentoArchivo.thumbnail_key)
        .where(
            SegmentoArchivo.tenant_id == tenant_id,
            SegmentoArchivo.thumbnail_key.isnot(None),
        )
        .order_by(SegmentoArchivo.created_at)
    )
    # Map filename → first thumbnail_key found
    thumb_map: dict[str, str] = {}
    for origen, thumb_key in thumb_rows:
        fname = Path(origen).name
        if fname not in thumb_map:
            thumb_map[fname] = thumb_key

    return {
        "videos": [
            {
                "storage_key": f"videos/{f.name}",
                "nombre": f.name,
                "size_mb": round(f.stat().st_size / 1_048_576, 1),
                "thumbnail_url": (
                    f"/api/archivo/thumbnail/{thumb_map[f.name]}"
                    if f.name in thumb_map else None
                ),
            }
            for f in files
        ]
    }
