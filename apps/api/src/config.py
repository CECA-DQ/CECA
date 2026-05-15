from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to apps/api/
_API_ROOT = Path(__file__).parent.parent

# Always resolve .env relative to apps/api/, regardless of where the command is run from.
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    # Database / cache
    database_url: str = "postgresql+asyncpg://ceca:ceca@localhost:5432/ceca"
    redis_url: str = "redis://localhost:6379/0"

    # AI providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    elevenlabs_api_key: str = ""
    voyage_api_key: str = ""

    # Adapter selection
    llm_provider: str = "groq"            # groq | claude | openai
    llm_default_model: str = "llama-3.3-70b-versatile"
    mam_provider: str = "mock"            # mock | avid
    storage_provider: str = "local"       # local | r2
    stt_provider: str = "groq_whisper"    # groq_whisper | whisper_api | mock
    tts_provider: str = "elevenlabs"      # elevenlabs | openai_tts
    tts_default_voice_id: str = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs Adam (multilingual)
    vector_provider: str = "mock"         # mock | pgvector | pgfts
    embedding_model: str = "local/intfloat/multilingual-e5-small"  # local/<model> | text-embedding-3-small | voyage-3
    embedding_dimensions: int = 384       # must match vector column in migration

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # yt-dlp cookies file path (relative to apps/api or absolute)
    ytdlp_cookies_file: str = "data/yt_cookies.txt"

    @property
    def ytdlp_cookies_path(self) -> Optional[Path]:
        """Absolute path to the yt-dlp cookies file, or None if not found."""
        p = Path(self.ytdlp_cookies_file)
        if not p.is_absolute():
            p = _API_ROOT / p
        return p if p.exists() else None

    # Observability
    cost_alert_threshold_usd: float = 10.0


settings = Settings()
