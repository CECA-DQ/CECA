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

### STT — Speech to Text (`adapters/stt/`) *(pending)*

Transcribes audio with timestamps and speaker diarization.

**Planned implementations:** Whisper API, Whisper local.

---

### TTS — Text to Speech (`adapters/tts/`) *(pending)*

Converts the voiceover script to audio.

**Planned implementations:** ElevenLabs, OpenAI TTS.

---

### Storage (`adapters/storage/`) *(pending)*

Stores and retrieves video files and generated assets.

**Planned implementations:** Local filesystem (dev), Cloudflare R2 (production).

---

### Vector (`adapters/vector/`) *(pending)*

Stores and queries content embeddings for semantic search.

**Planned implementations:** pgvector (PostgreSQL extension).

## Adding a new provider

1. Create `adapters/<family>/<provider>.py` inheriting from the base class.
2. Implement all abstract methods.
3. Add it to the `match` in `factory.py`.
4. Add the new value to the env var documentation here and in `.env.example`.
5. Write an integration test under `tests/integration/adapters/`.
