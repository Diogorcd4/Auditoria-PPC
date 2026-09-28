from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .base import LLMClient, LLMResponse

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

TASK_MARKER_RE = re.compile(r"\[TASK:([a-z0-9_]+)\]")


class MockLLMClient(LLMClient):
    """Deterministic client that replays JSON fixtures instead of calling a real model.

    Used by the whole test suite and by the demo mode, so nothing ever depends on network
    access or a paid API key. The task is read from a `[TASK:<name>]` marker that callers
    put at the start of the system prompt; `fixtures` maps that name to a file under
    `tests/fixtures/` (or an explicit Path). A task with no mapping falls back to
    `default_fixture`.
    """

    def __init__(
        self,
        fixtures: Optional[dict[str, str]] = None,
        fixtures_dir: Path | str | None = None,
        default_fixture: str = "default_response.json",
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else FIXTURES_DIR
        self.fixtures = fixtures or {}
        self.default_fixture = default_fixture
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        model: str = "",
        json_schema: Optional[dict[str, Any]] = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        task = self._extract_task(system)
        filename = self.fixtures.get(task, self.default_fixture)
        content = self._load_fixture(filename)
        self.calls.append({"task": task, "system": system, "prompt": prompt, "fixture": filename})
        return LLMResponse(content=content, model=model or "mock-model", backend="mock", duration_seconds=0.0)

    async def list_models(self) -> list[str]:
        return ["mock-model"]

    async def health_check(self) -> bool:
        return True

    def _extract_task(self, system: str) -> str:
        match = TASK_MARKER_RE.search(system)
        return match.group(1) if match else ""

    def _load_fixture(self, filename: str) -> str:
        path = self.fixtures_dir / filename
        if not path.exists():
            return "{}"
        return path.read_text(encoding="utf-8")
