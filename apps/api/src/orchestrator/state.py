from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class PipelineState:
    """Shared context that flows through every pipeline step.

    Each step reads what it needs and writes its output back here.
    step_results is persisted to PipelineStepRun.result (JSONB) for traceability.
    """

    project_id: UUID
    tenant_id: str
    pipeline_run_id: UUID
    video_key: str = ""

    # Typed results populated as steps run
    transcript: dict = field(default_factory=dict)
    scenes: list[dict] = field(default_factory=list)
    visual_analysis: list[dict] = field(default_factory=list)
    selected_segments: list[dict] = field(default_factory=list)
    voiceover_script: str = ""
    voiceover_audio_key: str = ""
    composed_video_key: str = ""
    editorial_package: dict = field(default_factory=dict)
    tv_pieces: dict = field(default_factory=dict)

    # Per-step raw results stored in DB for traceability
    step_results: dict[str, dict] = field(default_factory=dict)
