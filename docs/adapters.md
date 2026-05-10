# Adapters

## What and why

An adapter is a class that wraps an external service behind a fixed interface.
The rest of the application only knows the interface — it never imports the SDK directly.

This means:
- Swapping providers is a one-line change in the `.env` file.
- Tests use a fake (mock) implementation — no real API calls, no costs.
- When AVID integration arrives, only the adapter file changes. Nothing else.

## How each adapter is structured

Every adapter family has the same four files:

| File | Purpose |
|---|---|
| `base.py` | Abstract class that defines the interface (what methods exist) |
| `<provider>.py` | Concrete implementation for one provider |
| `<provider>.py` (stub) | Skeleton for a future provider — raises `NotImplementedError` |
| `factory.py` | Reads the env var and returns the right implementation |

## Current adapters

### MAM — Media Asset Manager (`adapters/mam/`)

Provides access to the video catalogue.

**Interface (`MAMAdapter`):**
- `search(query, tenant_id, limit)` → list of `MediaAsset`
- `get_asset(asset_id, tenant_id)` → `MediaAsset`
- `download(asset_id, tenant_id)` → raw bytes

**Implementations:**
- `MockMAMAdapter` — in-memory catalogue with 3 demo assets. Used for development and all unit tests.
- `AvidMAMAdapter` — pending. Will call AVID MediaCentral REST API once the first client is signed.

**Env var:** `MAM_PROVIDER=mock` or `MAM_PROVIDER=avid`

---

### LLM — Language Model (`adapters/llm/`)

Generates text, analyses images, produces editorial content.

**Interface (`LLMProvider`):**
- `generate(system, messages, model, temperature, max_tokens)` → `LLMResponse`
- `generate_with_vision(system, messages, images)` → `LLMResponse`
- `stream(system, messages)` → async iterator of strings
- `estimate_cost(input_tokens, output_tokens)` → float (USD)

**Implementations:**
- `ClaudeProvider` — Anthropic Claude. Primary provider.
- `OpenAIProvider` — stub, not yet implemented.

**Env var:** `LLM_PROVIDER=claude` or `LLM_PROVIDER=openai`

---

### STT — Speech to Text (`adapters/stt/`)

Transcribes audio bytes into text with per-segment timestamps and optional speaker diarization.

**Interface (`STTProvider`):**
- `transcribe(audio, language, with_timestamps, with_diarization)` → `Transcript`

**Returns:**
- `Transcript` — contains `language`, `full_text`, and a list of `TranscriptSegment` (each with `start`, `end`, `text`, `speaker`)

**Implementations:**
- `MockSTTProvider` — returns a fixed 4-segment transcript in Spanish. Used in all unit tests.
- `WhisperAPIProvider` — calls OpenAI Whisper API (`whisper-1`) with `verbose_json` format to get per-segment timestamps. Diarization not supported natively by Whisper — wire `pyannote-audio` separately when needed.

**Env var:** `STT_PROVIDER=mock` or `STT_PROVIDER=whisper_api`

**Note:** The mock simulates two speakers (`speaker_0`, `speaker_1`). The Whisper implementation raises `NotImplementedError` if `with_diarization=True`.

---

### TTS — Text to Speech (`adapters/tts/`)

Converts the voiceover script into mp3 audio bytes, ready to be mixed into the final video.

**Interface (`TTSProvider`):**
- `synthesize(text, voice_id, language)` → `SynthesisResult`
- `estimate_cost(character_count)` → float (USD)

**Returns:**
- `SynthesisResult` — contains `audio` (raw mp3 bytes), `duration_seconds`, and `voice_id`

**Implementations:**
- `MockTTSProvider` — returns empty bytes and estimates duration from word count (150 wpm). Cost is always 0. Used in all unit tests.
- `ElevenLabsProvider` — calls ElevenLabs API with model `eleven_multilingual_v2`, output format `mp3_44100_128`. Streams audio chunks and joins them. Cost estimated at ~$0.30 per 1000 characters (Creator plan).

**Env var:** `TTS_PROVIDER=mock` or `TTS_PROVIDER=elevenlabs`

**Note:** ElevenLabs does not return audio duration in the API response — it is estimated from word count.

---

### Storage (`adapters/storage/`)

Stores and retrieves binary files: raw video, extracted audio, generated voiceover, and the final MP4.

**Interface (`StorageAdapter`):**
- `upload(key, data, content_type)` → key
- `download(key)` → bytes
- `delete(key)` → None
- `exists(key)` → bool

**Key convention:** `{tenant_id}/{project_id}/{filename}` — tenant isolation is enforced at the key level.

**Implementations:**
- `LocalStorageAdapter` — writes to `data/storage/` on disk. Includes path traversal protection. For development only.
- `R2StorageAdapter` — Cloudflare R2 via the S3-compatible API (`aioboto3`). Used in production.

**Env var:** `STORAGE_PROVIDER=local` or `STORAGE_PROVIDER=r2`

**Note:** `data/storage/` is in `.gitignore`. Never commit stored files.

---

### Vector (`adapters/vector/`)

Stores content embeddings and retrieves the most semantically similar entries for a given query. Used to build the content queue and suggest related pieces.

**Interface (`VectorAdapter`):**
- `index(id, text, tenant_id, metadata)` → None
- `search(query, tenant_id, limit)` → list of `SearchResult`
- `delete(id, tenant_id)` → None

**Returns:**
- `SearchResult` — contains `id`, `score` (cosine similarity, 0.0–1.0), and `metadata`

**Implementations:**
- `MockVectorAdapter` — in-memory store with substring matching as a proxy for similarity. Score is 1.0 for exact match, 0.5 for partial. Used in all unit tests.
- `PgVectorAdapter` — stores embeddings in PostgreSQL using the `pgvector` extension. Embeddings are generated via Voyage AI or Cohere (to be wired when implementing). Pending implementation.

**Env var:** `VECTOR_PROVIDER=mock` or `VECTOR_PROVIDER=pgvector`

**Note:** The mock does not generate real embeddings. The pgvector implementation will require a `content_embeddings` table migration and an embeddings API key.

## Adding a new provider

1. Create `adapters/<family>/<provider>.py` inheriting from the base class.
2. Implement all abstract methods.
3. Add it to the `match` in `factory.py`.
4. Add the new value to the env var documentation here and in `.env.example`.
5. Write an integration test under `tests/integration/adapters/`.
