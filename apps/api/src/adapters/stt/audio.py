"""Audio extraction utilities for the STT pipeline.

Supports three source types:
- YouTube URLs  → yt-dlp (downloads best audio, outputs mp3)
- Direct video URLs (mp4, mov, etc.) → httpx download + ffmpeg audio extract
- Local storage keys → ffmpeg audio extract from file on disk

All functions return raw mp3 bytes ready to be sent to the Whisper API.
FFmpeg is required for direct URLs and local files. If ffmpeg is not found, a
RuntimeError is raised with a clear message so the caller can fall back to mock.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DIRECT_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts")
_MAX_DIRECT_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB safety limit


def _is_direct_video_url(url: str) -> bool:
    """Return True only if the URL is a direct video file (by extension)."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in _DIRECT_VIDEO_EXTENSIONS)


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install it with: brew install ffmpeg"
        )
    return path


async def extract_audio_from_url(url: str) -> bytes:
    """Download video from URL and return mp3 audio bytes.

    Uses httpx + ffmpeg for direct video file URLs (ending in .mp4, .mov, etc.).
    Uses yt-dlp for everything else (YouTube, RTVE, and any site yt-dlp supports).
    """
    if _is_direct_video_url(url):
        return await _extract_audio_direct_url(url)
    return await _extract_audio_ytdlp(url)


async def extract_audio_from_file(file_path: str) -> bytes:
    """Extract mp3 audio from a local video file using ffmpeg."""
    ffmpeg = _require_ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        out_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", file_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            out_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-500:]}")
        return Path(out_path).read_bytes()
    finally:
        Path(out_path).unlink(missing_ok=True)


async def _extract_audio_ytdlp(url: str) -> bytes:
    """Use yt-dlp to download best audio from any supported URL (YouTube, RTVE, etc.)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = str(Path(tmpdir) / "audio.%(ext)s")

        # yt-dlp is a blocking CLI — run in a thread to keep the event loop free
        def _run() -> None:
            from src.config import settings
            import yt_dlp  # type: ignore[import]
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": out_template,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "96",
                }],
                "quiet": True,
                "no_warnings": True,
            }
            cookies = settings.ytdlp_cookies_path
            if cookies:
                ydl_opts["cookiefile"] = str(cookies)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run)

        # yt-dlp writes <name>.mp3 after the FFmpegExtractAudio postprocessor
        mp3_files = list(Path(tmpdir).glob("*.mp3"))
        if not mp3_files:
            raise RuntimeError("yt-dlp did not produce an mp3 file")

        logger.info("yt-dlp extracted audio: %s (%d bytes)", mp3_files[0].name, mp3_files[0].stat().st_size)
        return mp3_files[0].read_bytes()


async def _extract_audio_direct_url(url: str) -> bytes:
    """Download a direct video URL with httpx, then extract audio with ffmpeg."""
    import httpx

    ffmpeg = _require_ffmpeg()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_video = Path(tmpdir) / "video.mp4"
        tmp_audio = Path(tmpdir) / "audio.mp3"

        logger.info("Downloading video from %s", url)
        async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                downloaded = 0
                with tmp_video.open("wb") as fh:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > _MAX_DIRECT_DOWNLOAD_BYTES:
                            raise RuntimeError("Video exceeds 500 MB limit for direct download")

        logger.info("Downloaded %d bytes, extracting audio", downloaded)

        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", str(tmp_video),
            "-vn", "-acodec", "libmp3lame", "-q:a", "4",
            str(tmp_audio),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extract failed: {stderr.decode()[-500:]}")

        return tmp_audio.read_bytes()


async def get_video_metadata_from_local_file(file_path: str) -> dict:
    """Return technical metadata for a local video file using ffprobe."""
    return await _metadata_ffprobe(file_path)


async def get_video_metadata_from_url(url: str) -> dict:
    """Return technical metadata dict for a video URL.

    Uses ffprobe for direct video file URLs.
    Uses yt-dlp for everything else (YouTube, RTVE, and any site yt-dlp supports).
    """
    if _is_direct_video_url(url):
        return await _metadata_ffprobe(url)
    return await _metadata_ytdlp(url)


async def _metadata_ytdlp(url: str) -> dict:
    """Extract metadata via yt-dlp without downloading (YouTube, RTVE, etc.)."""
    def _run() -> dict:
        from src.config import settings
        import yt_dlp  # type: ignore[import]
        ydl_opts = {"quiet": True, "no_warnings": True}
        cookies = settings.ytdlp_cookies_path
        if cookies:
            ydl_opts["cookiefile"] = str(cookies)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "duration_seconds": info.get("duration") or 0.0,
            "resolution": f"{info.get('width', 0)}x{info.get('height', 0)}" if info.get("width") else "unknown",
            "fps": float(info.get("fps") or 0.0),
            "codec": info.get("vcodec") or "unknown",
            "audio_codec": info.get("acodec") or "unknown",
            "title": info.get("title") or "",
            "file_size_bytes": info.get("filesize") or info.get("filesize_approx") or 0,
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run)


async def _metadata_ffprobe(url: str) -> dict:
    """Use ffprobe to get metadata from a direct video URL."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe not found on PATH")

    import json as _json
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    data = _json.loads(stdout)

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})

    width = video_stream.get("width", 0)
    height = video_stream.get("height", 0)
    fps_raw = video_stream.get("r_frame_rate", "0/1")
    num, den = (fps_raw.split("/") + ["1"])[:2]
    fps = round(int(num) / max(int(den), 1), 2)

    return {
        "duration_seconds": float(fmt.get("duration") or 0),
        "resolution": f"{width}x{height}" if width else "unknown",
        "fps": fps,
        "codec": video_stream.get("codec_name") or "unknown",
        "audio_codec": audio_stream.get("codec_name") or "unknown",
        "file_size_bytes": int(fmt.get("size") or 0),
    }
