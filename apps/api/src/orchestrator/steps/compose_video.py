"""Compose video step.

Downloads the source video, cuts the selected segments, concatenates them,
and saves the final MP4 to local storage.

Pipeline:
  1. yt-dlp downloads the source video at 720p (fast, reasonable quality for demo)
  2. FFmpeg cuts each selected segment (-ss / -to with re-encode for clean cuts)
  3. FFmpeg concat demuxer joins clips into a single MP4
  4. Result is uploaded to local storage under output/{project_id}/final.mp4

Falls back to a mock key if FFmpeg is missing or the download fails.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from src.adapters.storage.factory import get_storage_adapter
from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep

logger = logging.getLogger(__name__)


class ComposeVideoStep(PipelineStep):
    """Cut selected segments, concatenate, and save the final MP4."""

    name = "compose_video"
    description = "Assemble final MP4 from selected clips via FFmpeg"

    async def execute(self, state: PipelineState) -> PipelineState:
        video_url = state.video_key
        segments = state.selected_segments

        if not video_url or not segments:
            logger.warning("No video URL or segments — using mock video key")
            return self._mock(state)

        if not video_url.startswith(("http://", "https://")):
            logger.warning("Local file composition not implemented — using mock video key")
            return self._mock(state)

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            logger.warning("ffmpeg not found — using mock video key")
            return self._mock(state)

        try:
            output_key = await _compose(state.project_id, video_url, segments, ffmpeg)
            duration = sum(s["end"] - s["start"] for s in segments)
            result = {
                "video_key": output_key,
                "duration_seconds": round(duration, 1),
                "clips": len(segments),
                "provider": "ffmpeg",
            }
            state.composed_video_key = output_key
            state.step_results[self.name] = result
            logger.info("Video composed: %s (%.1fs, %d clips)", output_key, duration, len(segments))
        except Exception as exc:
            logger.warning("compose_video failed (%s: %s) — using mock key", type(exc).__name__, exc)
            return self._mock(state)

        return state

    def _mock(self, state: PipelineState) -> PipelineState:
        key = f"output/{state.project_id}/final.mp4"
        state.composed_video_key = key
        state.step_results[self.name] = {
            "video_key": key,
            "duration_seconds": 93.5,
            "clips": len(state.selected_segments),
            "provider": "mock",
        }
        return state


async def _compose(
    project_id: UUID,
    video_url: str,
    segments: list[dict],
    ffmpeg: str,
) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # 1. Download source video at 720p (faster than 1080p for demo)
        logger.info("Downloading source video for composition: %s", video_url)
        video_path = await _download_video(video_url, tmp)
        logger.info("Source video downloaded: %.1f MB", video_path.stat().st_size / 1_048_576)

        # 2. Cut each selected segment
        clip_paths = await _cut_clips(video_path, segments, tmp, ffmpeg)
        logger.info("Cut %d clips", len(clip_paths))

        # 3. Concatenate all clips into a single MP4
        output_path = tmp / "final.mp4"
        await _concat_clips(clip_paths, output_path, ffmpeg)
        logger.info("Concatenated to %.1f MB", output_path.stat().st_size / 1_048_576)

        # 4. Upload to local storage
        output_key = f"output/{project_id}/final.mp4"
        storage = get_storage_adapter()
        await storage.upload(output_key, output_path.read_bytes(), "video/mp4")

        return output_key


async def _download_video(url: str, tmpdir: Path) -> Path:
    """Download video at 720p using yt-dlp."""
    out_template = str(tmpdir / "source.%(ext)s")

    def _run() -> None:
        import yt_dlp  # type: ignore[import]
        ydl_opts = {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run)

    mp4_files = list(tmpdir.glob("source.*"))
    if not mp4_files:
        raise RuntimeError("yt-dlp did not download a video file")
    return mp4_files[0]


async def _cut_clips(
    source: Path,
    segments: list[dict],
    tmpdir: Path,
    ffmpeg: str,
) -> list[Path]:
    """Cut each segment from the source video. Returns list of clip paths."""
    clips = []
    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        clip_path = tmpdir / f"clip_{i:03d}.mp4"

        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", str(source),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
            str(clip_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg clip {i} failed: {stderr.decode()[-300:]}")
        clips.append(clip_path)

    return clips


async def _concat_clips(clips: list[Path], output: Path, ffmpeg: str) -> None:
    """Concatenate clips using FFmpeg concat demuxer."""
    concat_list = output.parent / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{clip}'" for clip in clips),
        encoding="utf-8",
    )

    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {stderr.decode()[-300:]}")
