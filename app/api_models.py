from __future__ import annotations

from pydantic import BaseModel, Field


class AgentAnswer(BaseModel):
    run_id: str
    answer: str
    sources: list[dict] = Field(default_factory=list)
    model: str
    provider: str
    profile: str | None = None
