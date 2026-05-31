"""Source resolution: a `fuente` may be a storage key or an http(s) URL.

URLs are downloaded on the fly (yt-dlp / ffmpeg); storage keys are read from
data/storage. These tests cover the routing and caching without touching the
network (the download helpers are monkeypatched).
"""

import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from src.routes import generar_pieza
from src.routes.generar_pieza import (
    _download_fuente,
    _is_url,
    _resolve_fuentes,
    _storage_key_for,
)


def test_is_url_true_for_http_and_https():
    assert _is_url("http://example.com/v.mp4")
    assert _is_url("https://www.youtube.com/watch?v=abc")


def test_is_url_false_for_storage_key():
    assert not _is_url("videos/clip.mp4")
    assert not _is_url("demo/raw/press.mp4")


def test_storage_key_for_downloaded_url_is_relative_key(tmp_path, monkeypatch):
    # A montage clip must route to the cached file's key, never the URL.
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    p = tmp_path / "videos" / "src_abc123.mp4"
    assert _storage_key_for(p) == "videos/src_abc123.mp4"


def test_storage_key_for_storage_key_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    p = (tmp_path / "demo/raw/press.mp4").resolve()
    assert _storage_key_for(p) == "demo/raw/press.mp4"


def test_resolve_fuentes_storage_key_found(tmp_path, monkeypatch):
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")
    out = asyncio.run(_resolve_fuentes(["clip.mp4"], ffmpeg="ffmpeg"))
    assert out == [(tmp_path / "clip.mp4").resolve()]


def test_resolve_fuentes_storage_key_missing_raises_404(tmp_path, monkeypatch):
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_resolve_fuentes(["nope.mp4"], ffmpeg="ffmpeg"))
    assert exc.value.status_code == 404
    assert "Fuente no encontrada" in exc.value.detail


def test_resolve_fuentes_mixes_url_and_storage_key(tmp_path, monkeypatch):
    # A URL is downloaded; a storage key is read — order preserved.
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")

    async def fake_download(url, ffmpeg):
        return tmp_path / "downloaded.mp4"

    monkeypatch.setattr(generar_pieza, "_download_fuente", fake_download)

    out = asyncio.run(
        _resolve_fuentes(["https://youtu.be/abc", "clip.mp4"], ffmpeg="ffmpeg")
    )
    assert out == [tmp_path / "downloaded.mp4", (tmp_path / "clip.mp4").resolve()]


def test_download_fuente_returns_cached_file_without_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    url = "https://youtu.be/abc"
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    videos = tmp_path / "videos"
    videos.mkdir()
    cached = videos / f"src_{digest}.mp4"
    cached.write_bytes(b"x")

    called = {"yt": False, "ff": False}

    async def fake_yt(u, o):
        called["yt"] = True

    async def fake_ff(u, o, f):
        called["ff"] = True

    monkeypatch.setattr(generar_pieza, "_ytdlp_download", fake_yt)
    monkeypatch.setattr(generar_pieza, "_ffmpeg_download", fake_ff)

    out = asyncio.run(_download_fuente(url, ffmpeg="ffmpeg"))
    assert out == cached
    assert not called["yt"] and not called["ff"]  # cache hit → no download


def test_download_fuente_routes_youtube_to_ytdlp(tmp_path, monkeypatch):
    monkeypatch.setattr(generar_pieza, "_STORAGE_BASE", tmp_path)
    monkeypatch.setattr(generar_pieza, "_is_direct_url", lambda u: False)
    monkeypatch.setattr(generar_pieza.shutil, "which", lambda name: "/usr/bin/yt-dlp")

    async def fake_yt(url, out):
        out.write_bytes(b"x")  # simulate a successful download

    monkeypatch.setattr(generar_pieza, "_ytdlp_download", fake_yt)

    out = asyncio.run(_download_fuente("https://youtu.be/abc", ffmpeg="ffmpeg"))
    assert out.exists()
    assert out.name.startswith("src_") and out.suffix == ".mp4"
