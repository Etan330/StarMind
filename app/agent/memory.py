from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config import AGENT_MEMORY_PATH, DEFAULT_AGENT_MEMORY, read_json, write_json


class AgentMemory:
    def read(self) -> dict[str, Any]:
        payload = read_json(AGENT_MEMORY_PATH, DEFAULT_AGENT_MEMORY)
        payload.setdefault("runs", [])
        payload.setdefault("notes", [])
        return payload

    def remember_run(self, run_id: str, question: str, answer: str) -> None:
        payload = self.read()
        payload["runs"].insert(
            0,
            {
                "run_id": run_id,
                "question": question[:500],
                "answer": answer[:1000],
                "created_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        payload["runs"] = payload["runs"][:50]
        write_json(AGENT_MEMORY_PATH, payload)
