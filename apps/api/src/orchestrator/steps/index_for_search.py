import asyncio

from src.orchestrator.state import PipelineState
from src.orchestrator.steps.base import PipelineStep


class IndexForSearchStep(PipelineStep):
    """Embed the editorial content into the vector store for semantic search."""

    name = "index_for_search"
    description = "Index content in vector store for semantic search"

    async def execute(self, state: PipelineState) -> PipelineState:
        await asyncio.sleep(0.5)

        result = {
            "indexed_chunks": 12,
            "collection": "editorial_pieces",
            "provider": "mock",
        }
        state.step_results[self.name] = result
        return state
