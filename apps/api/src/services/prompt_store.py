import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, StrictUndefined, TemplateError


@dataclass(frozen=True)
class RenderedPrompt:
    key: str
    version: int
    system: str
    user: str


class _PromptTemplate:
    """Parsed representation of a single .md prompt file."""

    def __init__(self, key: str, version: int, system_src: str, user_src: str) -> None:
        self.key = key
        self.version = version
        self._system_src = system_src
        self._user_src = user_src
        self._env = Environment(undefined=StrictUndefined, autoescape=False)

    def render(self, **variables) -> RenderedPrompt:
        try:
            system = self._env.from_string(self._system_src).render(**variables)
            user = self._env.from_string(self._user_src).render(**variables)
        except TemplateError as exc:
            raise ValueError(f"Failed to render prompt '{self.key}': {exc}") from exc
        return RenderedPrompt(key=self.key, version=self.version, system=system, user=user)


class PromptStore:
    """Loads and renders versioned prompt templates from markdown files.

    Prompt files live under src/prompts/, organized by domain.
    Each file has YAML frontmatter and ## system / ## user sections.
    Templates are rendered with Jinja2.

    Usage:
        store = PromptStore(base_path)
        prompt = store.render("pipeline/write_script", transcript=..., segments=...)
        response = await llm.generate(system=prompt.system, messages=[{"role": "user", "content": prompt.user}])
    """

    def __init__(self, base_path: Path) -> None:
        self._templates: dict[str, _PromptTemplate] = {}
        self._load_all(base_path)

    def render(self, key: str, **variables) -> RenderedPrompt:
        if key not in self._templates:
            raise KeyError(f"Prompt not found: '{key}'. Available: {sorted(self._templates)}")
        return self._templates[key].render(**variables)

    def keys(self) -> list[str]:
        return sorted(self._templates)

    # ------------------------------------------------------------------
    # Private loading helpers
    # ------------------------------------------------------------------

    def _load_all(self, base_path: Path) -> None:
        for path in base_path.rglob("*.md"):
            key = str(path.relative_to(base_path).with_suffix(""))
            self._templates[key] = self._parse(key, path.read_text(encoding="utf-8"))

    def _parse(self, key: str, content: str) -> _PromptTemplate:
        frontmatter, body = self._split_frontmatter(content)
        version = int(frontmatter.get("version", 1))
        system = self._extract_section(body, "system")
        user = self._extract_section(body, "user")
        return _PromptTemplate(key, version, system, user)

    def _split_frontmatter(self, content: str) -> tuple[dict[str, str], str]:
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return {}, content
        raw = match.group(1)
        meta = {}
        for line in raw.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        return meta, content[match.end():]

    def _extract_section(self, body: str, section: str) -> str:
        pattern = rf"##\s+{section}\s*\n(.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""


# Singleton — instantiated once at first use, reused for the lifetime of the process.
_store: PromptStore | None = None


def get_prompt_store() -> PromptStore:
    global _store
    if _store is None:
        _store = PromptStore(Path(__file__).parent.parent / "prompts")
    return _store
