from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    stt_provider: str = "whisper_api"     # whisper_api | whisper_local
    tts_provider: str = "elevenlabs"      # elevenlabs | openai_tts
    vector_provider: str = "mock"         # mock | pgvector

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""

    # Observability
    cost_alert_threshold_usd: float = 10.0


settings = Settings()
