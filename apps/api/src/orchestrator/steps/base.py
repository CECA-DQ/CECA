from abc import ABC, abstractmethod

from src.orchestrator.state import PipelineState


class PipelineStep(ABC):
    """Base class for every pipeline step.

    Each step receives the current state, does one thing, and returns
    the updated state. Steps never call each other — the Pipeline
    orchestrates the sequence.
    """

    name: str
    description: str

    @abstractmethod
    async def execute(self, state: PipelineState) -> PipelineState: ...
