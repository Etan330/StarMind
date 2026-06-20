from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.config import AGENT_TRACE_PATH


class AgentTracer:
    def log(self, event: str, payload: dict[str, Any]) -> None:
        AGENT_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        with AGENT_TRACE_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
